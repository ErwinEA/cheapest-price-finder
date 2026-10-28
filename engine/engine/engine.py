"""Engine: the one entry point the backend calls.

    engine = Engine()
    result = engine.search("amul butter 500 g")
    result.to_dict()   # JSON-ready, camelCase contract
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .adapters import build_default_adapters
from .adapters.base import VendorAdapter
from .config import InMemorySiteConfigStore, SiteConfigStore
from .fetchers import Fetcher
from .jobs import JobStatus, ScrapeJob, ScrapeScheduler
from .matching import DEFAULT_THRESHOLD, compare_and_rank
from .models import ProductComparison


@dataclass
class SearchResult:
    query: str
    comparisons: list[ProductComparison]
    jobs: list[ScrapeJob] = field(default_factory=list)

    @property
    def failed_vendors(self) -> list[str]:
        return [j.vendor for j in self.jobs if j.status == JobStatus.FAILED]

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "results": [c.to_dict() for c in self.comparisons],
            "vendors": [
                {"vendor": j.vendor, "status": j.status.value, "listingCount": len(j.listings),
                 "attempts": j.attempts, "error": j.error}
                for j in self.jobs
            ],
        }


class Engine:
    def __init__(
        self,
        config_store: Optional[SiteConfigStore] = None,
        fetcher: Optional[Fetcher] = None,
        adapters: Optional[dict[str, VendorAdapter]] = None,
        *,
        max_workers: int = 3,
        match_threshold: float = DEFAULT_THRESHOLD,
        **scheduler_kwargs,
    ):
        self.config_store = config_store or InMemorySiteConfigStore()
        self.adapters = adapters or build_default_adapters(self.config_store, fetcher)
        self.scheduler = ScrapeScheduler(
            self.adapters, self.config_store, max_workers=max_workers, **scheduler_kwargs
        )
        self.match_threshold = match_threshold

    def search(self, query: str, vendors: Optional[list[str]] = None) -> SearchResult:
        """Scrape every vendor, then match, compare and rank. A vendor that fails is
        reported in `jobs`/`failed_vendors`; the others' results are still returned."""
        jobs = self.scheduler.dispatch(query, vendors)
        listings = [l for job in jobs for l in job.listings]
        comparisons = compare_and_rank(listings, query=query, threshold=self.match_threshold)
        return SearchResult(query, comparisons, jobs)
