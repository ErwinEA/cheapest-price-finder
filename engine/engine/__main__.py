"""Command line:

    python -m engine search "amul butter 500 g" [--vendors amazon,flipkart] [--json]
    python -m engine search "amul butter" --fixtures tests/fixtures      # offline demo
    python -m engine diagnose amazon "iphone 15" [--html saved_page.html]

`diagnose` shows how many elements each configured selector matches, so you can
tell which one broke when a vendor changes its markup.
"""
from __future__ import annotations

import argparse
import json
import sys

from bs4 import BeautifulSoup

from .engine import Engine
from .fetchers import FixtureFetcher


def _search(args) -> int:
    fetcher = FixtureFetcher.from_dir(args.fixtures) if args.fixtures else None
    engine = Engine(fetcher=fetcher, sleep=(lambda s: None) if args.fixtures else __import__("time").sleep)
    vendors = args.vendors.split(",") if args.vendors else None
    result = engine.search(args.query, vendors)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0

    for job in result.jobs:
        extra = f" ({job.error})" if job.error else ""
        print(f"[{job.vendor}] {job.status.value}: {len(job.listings)} listings{extra}")
    print()
    for comp in result.comparisons:
        print(f"{comp.name}")
        for offer in comp.offers:
            mark = "*" if offer is comp.best_offer else " "
            stock = "" if offer.in_stock else "  (out of stock)"
            print(f"  {mark} {offer.vendor:<10} Rs {offer.price:>8}  effective Rs {offer.effective_price:>8}{stock}")
        if comp.savings:
            print(f"    save Rs {comp.savings} by picking the best offer")
        print()
    return 0


def _diagnose(args) -> int:
    engine = Engine()
    adapter = engine.adapters[args.vendor]
    cfg = adapter.config
    html = open(args.html, encoding="utf-8").read() if args.html else adapter.scrape(args.query)
    soup = BeautifulSoup(html, "html.parser")

    items = soup.select(cfg.selectors["item"])
    print(f"{args.vendor}: {len(html):,} bytes of html, {len(items)} item(s) match selectors['item']")
    for key, selector in cfg.selectors.items():
        if key == "item":
            continue
        hits = sum(1 for card in items if card.select_one(selector))
        print(f"  {key:<13} matched in {hits}/{len(items)} cards   <- {selector}")
    parsed = adapter.parse(html)
    print(f"\nparse() produced {len(parsed)} listing(s). First:")
    if parsed:
        print(json.dumps(parsed[0].__dict__, indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="scrape all vendors and compare prices")
    s.add_argument("query")
    s.add_argument("--vendors", help="comma-separated, e.g. amazon,flipkart")
    s.add_argument("--json", action="store_true")
    s.add_argument("--fixtures", help="directory of <vendor>.html files; runs offline")
    s.set_defaults(func=_search)

    d = sub.add_parser("diagnose", help="check which selectors still match")
    d.add_argument("vendor", choices=["amazon", "flipkart", "bigbasket"])
    d.add_argument("query", nargs="?", default="")
    d.add_argument("--html", help="use a saved page instead of fetching")
    d.set_defaults(func=_diagnose)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
