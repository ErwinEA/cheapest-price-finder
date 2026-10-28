"""Scraping job lifecycle: dispatch -> run (rate limited) -> retry -> markFailed.

One ScrapeJob per (vendor, query). `ScrapeScheduler.dispatch()` runs one job per
vendor concurrently, so one slow or blocked vendor never delays the others.
The jobs registry (`get_job`, `list_jobs`) is what the backend's admin
endpoints for jobs and logs can read from.
"""
from __future__ import annotations

import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from .adapters.base import VendorAdapter
from .config import SiteConfig, SiteConfigStore
from .errors import ConfigError, ScrapeError, SelectorError
from .models import Listing, utcnow
from .normalize import normalize_all
from .pricing import calculateEffectivePrice


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # vendor disabled


@dataclass
class ScrapeJob:
    vendor: str
    query: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    max_attempts: int = 1
    created_at: datetime = field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    listings: list[Listing] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.log.append(f"{utcnow().isoformat(timespec='seconds')} {message}")

    def to_dict(self, include_listings: bool = False) -> dict:
        data = {
            "id": self.id,
            "vendor": self.vendor,
            "query": self.query,
            "status": self.status.value,
            "attempts": self.attempts,
            "maxAttempts": self.max_attempts,
            "createdAt": self.created_at.isoformat(),
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "listingCount": len(self.listings),
            "log": list(self.log),
        }
        if include_listings:
            data["listings"] = [l.to_contract() for l in self.listings]
        return data


class RateLimiter:
    """Per-vendor minimum gap between requests. Thread-safe: each caller reserves
    the next free slot under a lock, then sleeps outside it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._clock, self._sleep = clock, sleep
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, min_interval: float) -> float:
        with self._lock:
            now = self._clock()
            start = max(now, self._next_slot.get(key, 0.0))
            self._next_slot[key] = start + min_interval
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)


class ScrapeScheduler:
    def __init__(
        self,
        adapters: dict[str, VendorAdapter],
        config_store: SiteConfigStore,
        *,
        max_workers: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ):
        self.adapters = adapters
        self.config_store = config_store
        self.max_workers = max_workers
        self._sleep = sleep
        self._jitter = jitter
        self.limiter = RateLimiter(clock=clock, sleep=sleep)
        self._jobs: dict[str, ScrapeJob] = {}
        self._lock = threading.Lock()

    # ---- registry (for admin endpoints) ----------------------------------
    def get_job(self, job_id: str) -> Optional[ScrapeJob]:
        return self._jobs.get(job_id)

    def list_jobs(self, status: Optional[JobStatus] = None) -> list[ScrapeJob]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j for j in jobs if status is None or j.status == status]

    # ---- dispatch ---------------------------------------------------------
    def dispatch(self, query: str, vendors: Optional[list[str]] = None) -> list[ScrapeJob]:
        """Create one job per vendor, run them concurrently, return them all."""
        jobs: list[ScrapeJob] = []
        runnable: list[ScrapeJob] = []
        for vendor in vendors or list(self.adapters):
            job = ScrapeJob(vendor=vendor, query=query)
            with self._lock:
                self._jobs[job.id] = job
            jobs.append(job)
            if vendor not in self.adapters:
                self.markFailed(job, ConfigError(f"unknown vendor '{vendor}'"))
            elif not self.config_store.get(vendor).enabled:
                job.status = JobStatus.SKIPPED
                job.finished_at = utcnow()
                job.note("vendor disabled in SiteConfig; skipped")
            else:
                runnable.append(job)

        if runnable:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(runnable))) as pool:
                list(pool.map(self.run_job, runnable))
        return jobs

    # ---- single job lifecycle --------------------------------------------
    def run_job(self, job: ScrapeJob) -> ScrapeJob:
        adapter = self.adapters[job.vendor]
        job.status = JobStatus.RUNNING
        job.started_at = utcnow()

        while True:
            cfg = self.config_store.get(job.vendor)  # re-read: admin edits apply on retry
            job.max_attempts = cfg.max_retries + 1
            job.attempts += 1
            try:
                waited = self.limiter.wait(job.vendor, cfg.min_request_interval_s)
                if waited:
                    job.note(f"rate limit: waited {waited:.1f}s")
                job.note(f"attempt {job.attempts}/{job.max_attempts}: scraping '{job.query}'")
                html = adapter.scrape(job.query)
                raws = adapter.parse(html)
                self._check_parsed(raws, html, cfg)

                listings = normalize_all(raws)
                if raws and not listings:
                    raise SelectorError("items found but no price could be read: price selector is likely stale")
                for listing in listings:
                    listing.effective_price = calculateEffectivePrice(listing, cfg)

                job.listings = listings
                job.status = JobStatus.SUCCEEDED
                job.finished_at = utcnow()
                job.note(f"succeeded: {len(listings)} listings")
                return job

            except ScrapeError as exc:
                if exc.retryable and job.attempts < job.max_attempts:
                    delay = cfg.retry_base_delay_s * 2 ** (job.attempts - 1) * (1 + 0.25 * self._jitter())
                    job.status = JobStatus.RETRYING
                    job.note(f"{type(exc).__name__}: {exc}; retrying in {delay:.1f}s")
                    self._sleep(delay)
                    job.status = JobStatus.RUNNING
                    continue
                return self.markFailed(job, exc)
            except Exception as exc:  # a parser bug must not take down the other vendors
                return self.markFailed(job, exc)

    @staticmethod
    def _check_parsed(raws: list, html: str, cfg: SiteConfig) -> None:
        if raws:
            return
        lowered = html.lower()
        if any(marker in lowered for marker in cfg.no_results_markers):
            return  # the vendor really has no results for this query
        raise SelectorError("page loaded but 0 items matched: selectors are likely stale")

    def markFailed(self, job: ScrapeJob, error: Exception) -> ScrapeJob:
        job.status = JobStatus.FAILED
        job.error = f"{type(error).__name__}: {error}"
        job.finished_at = utcnow()
        job.note(f"FAILED after {job.attempts} attempt(s): {job.error}")
        return job
