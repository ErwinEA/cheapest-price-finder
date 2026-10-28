import json
from decimal import Decimal

import pytest

from engine import Engine, JobStatus, RateLimiter, ScrapeJob
from engine.fetchers import FixtureFetcher

from conftest import FakeTime, SequenceFetcher, make_engine


# ---- rate limiter ----------------------------------------------------------
def test_rate_limiter_spaces_requests_per_vendor():
    ft = FakeTime()
    rl = RateLimiter(clock=ft.now, sleep=ft.sleep)
    assert rl.wait("amazon", 2.0) == 0.0
    assert rl.wait("amazon", 2.0) == 2.0   # must wait out the interval
    assert rl.wait("flipkart", 2.0) == 0.0  # other vendors are independent
    assert ft.sleeps == [2.0]


# ---- retry / markFailed ----------------------------------------------------
def test_retries_with_exponential_backoff_then_succeeds(pages):
    ft = FakeTime()
    fetcher = SequenceFetcher([(503, ""), (503, ""), (200, pages["amazon"])])
    engine = make_engine(fetcher, fake_time=ft)
    (job,) = engine.scheduler.dispatch("amul butter", ["amazon"])
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 3
    assert ft.sleeps == [2.0, 4.0]  # base 2s, doubling, jitter pinned to 0
    assert len(job.listings) == 3


def test_gives_up_and_marks_failed_after_max_attempts(pages):
    fetcher = SequenceFetcher([(429, "")])
    engine = make_engine(fetcher)
    (job,) = engine.scheduler.dispatch("x", ["flipkart"])
    assert job.status == JobStatus.FAILED
    assert job.attempts == 3  # first try + max_retries (2)
    assert job.error.startswith("BlockedError")
    assert any("FAILED" in line for line in job.log)


def test_selector_drift_fails_fast_without_retry(pages):
    fetcher = SequenceFetcher([(200, "<html><body><p>totally different markup</p></body></html>")])
    engine = make_engine(fetcher)
    (job,) = engine.scheduler.dispatch("x", ["amazon"])
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert job.error.startswith("SelectorError")


def test_genuine_no_results_is_success_not_failure():
    page = "<html><body>No results for xyzzy</body></html>"
    engine = make_engine(SequenceFetcher([(200, page)]))
    (job,) = engine.scheduler.dispatch("xyzzy", ["amazon"])
    assert job.status == JobStatus.SUCCEEDED and job.listings == []


def test_prices_unreadable_is_a_failure():
    page = '<div data-component-type="s-search-result"><h2><a href="/x/dp/B000000009"><span>Thing</span></a></h2>' \
           '<span class="a-price"><span class="a-offscreen">Free</span></span></div>'
    engine = make_engine(SequenceFetcher([(200, page)]))
    (job,) = engine.scheduler.dispatch("thing", ["amazon"])
    assert job.status == JobStatus.FAILED and "price" in job.error


def test_disabled_vendor_is_skipped_and_unknown_vendor_fails(pages, store):
    store.update("flipkart", enabled=False)
    engine = make_engine(FixtureFetcher(pages), store=store)
    jobs = engine.scheduler.dispatch("amul butter", ["flipkart", "nope"])
    assert [j.status for j in jobs] == [JobStatus.SKIPPED, JobStatus.FAILED]


def test_parser_crash_is_contained(pages):
    engine = make_engine(FixtureFetcher(pages))

    def boom(html):
        raise RuntimeError("parser bug")

    engine.adapters["amazon"].parse = boom
    jobs = engine.scheduler.dispatch("amul butter")
    by_vendor = {j.vendor: j for j in jobs}
    assert by_vendor["amazon"].status == JobStatus.FAILED
    assert by_vendor["flipkart"].status == JobStatus.SUCCEEDED  # others unaffected


def test_job_registry_for_admin_endpoints(pages):
    engine = make_engine(FixtureFetcher(pages))
    jobs = engine.scheduler.dispatch("amul butter")
    assert engine.scheduler.get_job(jobs[0].id) is jobs[0]
    assert len(engine.scheduler.list_jobs()) == 3
    assert engine.scheduler.list_jobs(JobStatus.FAILED) == []
    json.dumps(jobs[0].to_dict(include_listings=True))  # serialisable


def test_config_edit_applies_between_retries(pages):
    """An admin fixing selectors mid-retry takes effect on the next attempt."""
    from engine import InMemorySiteConfigStore

    store = InMemorySiteConfigStore()
    ft = FakeTime()
    fetcher = SequenceFetcher([(503, ""), (200, pages["amazon"])])
    engine = make_engine(fetcher, store=store, fake_time=ft)
    original_sleep = engine.scheduler._sleep

    def sleep_and_edit(s):
        original_sleep(s)
        store.update("amazon", max_results=1)

    engine.scheduler._sleep = sleep_and_edit
    (job,) = engine.scheduler.dispatch("amul butter", ["amazon"])
    assert job.status == JobStatus.SUCCEEDED and len(job.listings) == 1


# ---- full pipeline ---------------------------------------------------------
def test_end_to_end_ranks_cheapest_effective_price(pages):
    engine = make_engine(FixtureFetcher(pages))
    result = engine.search("amul butter")

    assert {j.vendor: j.status for j in result.jobs} == {
        "amazon": JobStatus.SUCCEEDED, "flipkart": JobStatus.SUCCEEDED, "bigbasket": JobStatus.SUCCEEDED,
    }
    names = [c.name for c in result.comparisons]
    assert not any("Cheese" in n for n in names)  # filtered as irrelevant

    by_qty = {("500" in c.name): c for c in result.comparisons}
    big = by_qty[True]
    assert [(o.vendor, o.effective_price) for o in big.offers] == [
        ("bigbasket", Decimal("305.00")),   # 275 + 30 delivery
        ("flipkart", Decimal("319.00")),    # 279 + 40
        ("amazon", Decimal("325.00")),      # 285 + 40
    ]
    assert big.best_offer.vendor == "bigbasket" and big.savings == Decimal("20.00")

    small = by_qty[False]
    assert small.best_offer.vendor == "flipkart"      # BigBasket is cheaper but out of stock
    assert small.offers[-1].vendor == "bigbasket"
    assert not small.offers[-1].in_stock

    # cheapest-first overall: the 100 g pack (96) before the 500 g pack (305)
    assert result.comparisons[0] is small


def test_partial_failure_still_returns_other_vendors(pages):
    broken = dict(pages, amazon=(503, ""))
    engine = make_engine(FixtureFetcher(broken))
    result = engine.search("amul butter")
    assert result.failed_vendors == ["amazon"]
    assert all(o.vendor != "amazon" for c in result.comparisons for o in c.offers)
    assert result.comparisons  # flipkart + bigbasket still compared


def test_result_is_json_ready_and_uses_contract_keys(pages):
    engine = make_engine(FixtureFetcher(pages))
    data = json.loads(json.dumps(engine.search("amul butter").to_dict()))
    offer = data["results"][0]["offers"][0]
    for key in ("vendor", "productName", "price", "stockStatus", "url", "imageUrl", "timestamp"):
        assert key in offer
