"""The engine is imported directly; vendor adapters remain unchanged."""
import hashlib
import sys
import threading
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import PriceListing, Product, ScrapingJob, SearchHistory, SiteConfig, Vendor, now
from .settings import ROOT

sys.path.insert(0, str(ROOT / "engine"))
from engine import Engine
from engine.config import DEFAULT_SITE_CONFIGS, SiteConfig as EngineConfig, SiteConfigStore
from engine.fetchers import FixtureFetcher
from engine.jobs import ScrapeScheduler

CAPABILITIES = {
    "amazon": ["groceries", "electronics", "household", "fashion", "beauty"],
    "flipkart": ["groceries", "electronics", "household", "fashion", "beauty"],
    "bigbasket": ["groceries", "household", "beauty"],
}


def seed(sessions):
    # Startup is normally after a single migration job. Savepoints also make
    # simultaneous application-worker starts safe against duplicate inserts.
    with sessions.begin() as db:
        for key, cfg in DEFAULT_SITE_CONFIGS.items():
            for model, identity, values in (
                (Vendor, key, {"id": key, "name": cfg.display_name, "categories": CAPABILITIES[key]}),
                (SiteConfig, key, {"vendor_id": key, "config": cfg.to_dict()}),
            ):
                if not db.get(model, identity):
                    try:
                        with db.begin_nested():
                            db.add(model(**values))
                            db.flush()
                    except IntegrityError:
                        pass


class DatabaseConfigStore(SiteConfigStore):
    def __init__(self, sessions):
        self.sessions = sessions

    def get(self, vendor_id):
        with self.sessions() as db:
            row = db.get(SiteConfig, vendor_id)
            if not row:
                raise KeyError(vendor_id)
            return EngineConfig.from_dict(row.config)

    def all(self):
        with self.sessions() as db:
            return {row.vendor_id: EngineConfig.from_dict(row.config) for row in db.scalars(select(SiteConfig))}

    def update(self, vendor_id, **changes):
        with self.sessions.begin() as db:
            row = db.get(SiteConfig, vendor_id, with_for_update=True)
            merged = row.config | changes
            config = EngineConfig.from_dict(merged)
            row.config = config.to_dict()
            return config


class DurableScheduler(ScrapeScheduler):
    def __init__(self, *args, sessions, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions = sessions

    def save(self, job):
        with self.sessions.begin() as db:
            db.merge(ScrapingJob(id=job.id, vendor_id=job.vendor, query=job.query,
                                status=job.status.value, detail=job.to_dict(True), created_at=job.created_at))

    def run_job(self, job):
        from engine.jobs import JobStatus
        job.status, job.started_at = JobStatus.RUNNING, now()
        self.save(job)
        try:
            return super().run_job(job)
        finally:
            self.save(job)


class SearchService:
    def __init__(self, sessions, settings):
        self.sessions = sessions
        self.settings = settings
        self.store = DatabaseConfigStore(sessions)
        fetcher = FixtureFetcher.from_dir(ROOT / "engine/tests/fixtures") if settings.fixture_mode else None
        self.engine = Engine(config_store=self.store, fetcher=fetcher)
        self.engine.scheduler = DurableScheduler(self.engine.adapters, self.store, sessions=sessions)
        self.lock = threading.Lock()

    def vendors_for(self, category, requested=None):
        with self.sessions() as db:
            capable = {v.id for v in db.scalars(select(Vendor)) if category in v.categories}
        enabled = {key for key, cfg in self.store.all().items() if cfg.enabled}
        if requested is not None and not set(requested) <= capable & enabled:
            raise HTTPException(422, "Selected vendors must be enabled and support the selected category")
        chosen = sorted(set(requested) if requested is not None else capable & enabled)
        if not chosen:
            raise HTTPException(422, "No enabled vendors support this category")
        return chosen

    def execute(self, query, category, vendors=None, user_id=None):
        chosen = self.vendors_for(category, vendors)
        history_id = None
        if user_id:
            with self.sessions.begin() as db:
                history = SearchHistory(user_id=user_id, query=query, category=category, vendors=chosen)
                db.add(history)
                db.flush()
                history_id = history.id
        # One engine run per process keeps its shared registry bounded and its
        # rate limiter consistent. Each run still scrapes vendors concurrently.
        with self.lock:
            try:
                result = self.engine.search(query, chosen)
                for job in result.jobs:
                    self.engine.scheduler.save(job)
                payload = result.to_dict()
                payload.update({"searchId": history_id, "category": category, "currency": "INR",
                                "demo": self.settings.fixture_mode})
                failed = len(result.failed_vendors)
                payload["status"] = "failed" if failed == len(chosen) else "partial" if failed else "succeeded"
                self.persist(result, payload, category, query, history_id)
                return result, payload
            except Exception:
                if history_id:
                    with self.sessions.begin() as db:
                        row = db.get(SearchHistory, history_id)
                        row.status = "failed"
                raise
            finally:
                self.engine.scheduler._jobs.clear()

    def persist(self, result, payload, category, query, history_id):
        with self.sessions.begin() as db:
            for comparison, item in zip(result.comparisons, payload["results"]):
                # Stable within a category and exact normalized representative
                # name. Alert matching separately rechecks the original anchor.
                key = hashlib.sha256(f"{category}:{comparison.name.casefold()}".encode()).hexdigest()
                if not db.get(Product, key):
                    try:
                        with db.begin_nested():
                            db.add(Product(id=key, name=comparison.name, category=category, query=query,
                                           anchor=comparison.offers[0].to_contract()))
                            db.flush()
                    except IntegrityError:
                        pass
                item["productId"] = key
                for offer in comparison.offers:
                    db.add(PriceListing(product_id=key, vendor_id=offer.vendor, search_id=history_id,
                                        price=offer.price, effective_price=(offer.effective_price if offer.effective_price is not None else offer.price),
                                        stock_status=offer.stock_status.value, url=offer.url,
                                        snapshot=offer.to_contract(), observed_at=offer.timestamp))
            if history_id:
                row = db.get(SearchHistory, history_id)
                row.status = payload["status"]
                row.result = payload
