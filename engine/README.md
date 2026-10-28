# Price Aggregator: Engine (Person C)

Gets and cleans vendor data, matches products across vendors, and ranks offers by what the buyer actually pays.

```
adapter.scrape(query) -> html -> adapter.parse(html) -> [RawListing]
   -> normalize() -> [Listing] -> calculateEffectivePrice() -> matchListing() -> compare_and_rank()
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium     # only BigBasket needs a real browser
python -m pytest                # 64 tests, no network needed
python -m engine search "amul butter" --fixtures tests/fixtures   # offline demo
```

## Using it from the backend

```python
from engine import Engine

engine = Engine()                           # or Engine(config_store=YourDbBackedStore())
result = engine.search("amul butter 500 g") # scrapes all vendors concurrently
result.to_dict()                            # JSON-ready (camelCase contract)
result.failed_vendors                       # e.g. ["amazon"]; other vendors' results are still returned
```

The listing contract: `{vendor, productName, price, stockStatus, url, imageUrl, timestamp, mrp, effectivePrice, quantity}`.
I could only see part of the shared contract in the spec screenshot (it cuts off after `timestamp`), so check this against what the backend expects.

## What's where (maps to the spec)

| Spec item | Where |
|---|---|
| `VendorAdapter` base + Amazon/Flipkart/BigBasket, `scrape` and `parse` | `engine/adapters/` |
| Normalize and clean prices | `engine/normalize.py` (`parse_price`, quantities, stock) |
| `calculateEffectivePrice` | `engine/pricing.py` (price + delivery fee below free-delivery threshold + handling fee) |
| `matchListing`, compare and rank | `engine/matching.py` (rapidfuzz) |
| Job lifecycle: dispatch, retry, `markFailed`, rate limiting | `engine/jobs.py` (`ScrapeScheduler`, `RateLimiter`) |
| Selectors read from `SiteConfig` | `engine/config.py`; re-read on every scrape and every retry |

For the admin endpoints: `engine.scheduler.list_jobs()`, `get_job(id)`, `job.to_dict()` (includes a per-job log), and `SiteConfigStore` (implement it on your DB; `InMemorySiteConfigStore` is the default).

## Before you rely on it: verify the selectors and fees

I had no network access to the vendor sites, so **the default selectors and delivery fees in `config.py` are untested against live pages** (the tests run against synthetic fixtures I wrote). Flipkart's class names in particular are obfuscated and rotate. To check:

```bash
python -m engine diagnose amazon "iphone 15"            # fetches live, shows which selectors match
python -m engine diagnose flipkart --html saved.html     # or use a page saved from your browser
```

Fix any broken selector by editing the `SiteConfig` (no code change). The `delivery_fee` / `free_delivery_threshold` values are placeholders: confirm them. BigBasket prices and stock depend on the delivery pincode (set it in `SiteConfig.cookies`).

Amazon and Flipkart actively block scrapers; expect `BlockedError` retries/failures in practice. The job runner reports them per vendor rather than crashing.

## Behaviour worth knowing

- **Failures are classified.** Network errors and bot blocks are retried with exponential backoff; a page that loads but yields 0 items (stale selectors) fails immediately with `SelectorError` so an admin sees it in the job log. A genuine "no results" page counts as success.
- **Matching is conservative.** Different pack sizes (500 g vs 100 g), variants (iPhone 15 vs 15 Pro) and storage sizes never match. Tune `match_threshold` (default 80) if real titles match too rarely. Very short vs very long titles for the same item can fall below it.
- **Ranking.** In-stock offers beat out-of-stock ones even if cheaper; groups are ordered by best effective price; results unrelated to the query are filtered out. Known limit: an accessory whose name contains the query (e.g. "iPhone 15 case") can still pass the relevance filter.
