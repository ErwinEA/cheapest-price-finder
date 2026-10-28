"""Product matching across vendors, then compare and rank.

matchListing(a, b) decides if two listings are the same product. It is
deliberately conservative: a wrong match shows the user a fake "saving".
  1. pack sizes must agree (500 g never matches 100 g)
  2. variant words must agree (iPhone 15 vs iPhone 15 Pro)
  3. numeric tokens (model numbers, storage) must not contradict
  4. fuzzy name similarity (rapidfuzz) must clear the threshold
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .models import Listing, ProductComparison
from .normalize import strip_quantity

try:  # the spec calls for rapidfuzz; difflib keeps things working without it
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    class fuzz:  # type: ignore[no-redef]
        @staticmethod
        def token_sort_ratio(a: str, b: str) -> float:
            sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
            return 100 * SequenceMatcher(None, sa, sb).ratio()

        @staticmethod
        def token_set_ratio(a: str, b: str) -> float:
            ta, tb = set(a.split()), set(b.split())
            common = " ".join(sorted(ta & tb))
            return max(
                100 * SequenceMatcher(None, common, " ".join(sorted(ta))).ratio(),
                100 * SequenceMatcher(None, common, " ".join(sorted(tb))).ratio(),
            )


DEFAULT_THRESHOLD = 80.0
_NOISE = {"with", "for", "and", "the", "of", "new", "combo", "pack", "by", "in", "buy",
          "online", "best", "price", "india", "only", "from", "a"}
_VARIANTS = {"pro", "max", "plus", "ultra", "mini", "lite", "se", "fe"}
_JOIN_UNITS = re.compile(r"(\d)\s+(gb|tb|mb|mah|hz|w|inch)\b")


@dataclass
class MatchResult:
    is_match: bool
    score: float
    reason: str


def _tokens(name: str) -> list[str]:
    text = strip_quantity(name).lower()
    text = _JOIN_UNITS.sub(r"\1\2", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [t for t in text.split() if t not in _NOISE]


def _compatible(a: set, b: set) -> bool:
    """Same, or one side simply says less (Amazon lists RAM, Flipkart doesn't)."""
    return a <= b or b <= a


def matchListing(a: Listing, b: Listing, threshold: float = DEFAULT_THRESHOLD) -> MatchResult:
    if a.quantity and b.quantity and a.quantity != b.quantity:
        return MatchResult(False, 0.0, f"quantity mismatch ({a.quantity} vs {b.quantity})")

    ta, tb = _tokens(a.name), _tokens(b.name)
    if not ta or not tb:
        return MatchResult(False, 0.0, "empty name")

    if {t for t in ta if t in _VARIANTS} != {t for t in tb if t in _VARIANTS}:
        return MatchResult(False, 0.0, "variant mismatch")

    na = {t for t in ta if any(c.isdigit() for c in t)}
    nb = {t for t in tb if any(c.isdigit() for c in t)}
    if not _compatible(na, nb):
        return MatchResult(False, 0.0, "model/spec mismatch")

    sa, sb = " ".join(ta), " ".join(tb)
    score = 0.4 * fuzz.token_set_ratio(sa, sb) + 0.6 * fuzz.token_sort_ratio(sa, sb)
    ok = score >= threshold
    return MatchResult(ok, round(score, 1), "match" if ok else "name similarity below threshold")


def relevance(query: str, name: str) -> float:
    return float(fuzz.token_set_ratio(" ".join(_tokens(query)), " ".join(_tokens(name))))


def group_listings(listings: list[Listing], threshold: float = DEFAULT_THRESHOLD) -> list[list[Listing]]:
    """Greedy clustering: each listing joins the first group whose anchor it matches.
    Comparing to the anchor only (not every member) stops names drifting via chains."""
    groups: list[list[Listing]] = []
    for listing in listings:
        for group in groups:
            if matchListing(group[0], listing, threshold).is_match:
                group.append(listing)
                break
        else:
            groups.append([listing])
    return groups


def _offer_key(l: Listing):
    effective = l.effective_price if l.effective_price is not None else l.price
    return (not l.in_stock, effective, l.price)


def compare_and_rank(
    listings: list[Listing],
    query: Optional[str] = None,
    threshold: float = DEFAULT_THRESHOLD,
    min_relevance: float = 60.0,
) -> list[ProductComparison]:
    """Group matching listings, order each group's offers cheapest-first (in-stock
    before out-of-stock, by effective price), then rank groups: products with
    something in stock first, then by best effective price. Groups unrelated to
    the query (relevance < min_relevance) are dropped."""
    comparisons: list[ProductComparison] = []
    for group in group_listings(listings, threshold):
        offers = sorted(group, key=_offer_key)
        name = min((o.name for o in offers), key=len)  # shortest = least marketing noise
        rel = relevance(query, name) if query else 100.0
        if query and rel < min_relevance:
            continue
        in_stock = [o for o in offers if o.in_stock]
        best = in_stock[0] if in_stock else None
        savings = Decimal("0")
        if len(in_stock) > 1:
            savings = _eff(in_stock[-1]) - _eff(in_stock[0])
        comparisons.append(ProductComparison(name, offers, best, savings, rel))

    comparisons.sort(key=lambda c: (c.best_offer is None, _eff(c.best_offer or c.offers[0])))
    return comparisons


def _eff(l: Listing) -> Decimal:
    return l.effective_price if l.effective_price is not None else l.price
