"""Normalize scraped strings into one comparable format."""
from __future__ import annotations

import html
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Optional

from .models import Listing, Quantity, RawListing, StockStatus

_PRICE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_OOS_RE = re.compile(r"out of stock|currently unavailable|sold out|notify me|unavailable", re.I)

_UNITS = {
    "kg": ("g", 1000), "kgs": ("g", 1000),
    "g": ("g", 1), "gm": ("g", 1), "gms": ("g", 1), "gram": ("g", 1), "grams": ("g", 1),
    "l": ("ml", 1000), "ltr": ("ml", 1000), "ltrs": ("ml", 1000),
    "litre": ("ml", 1000), "litres": ("ml", 1000), "liter": ("ml", 1000), "liters": ("ml", 1000),
    "ml": ("ml", 1),
    "pc": ("unit", 1), "pcs": ("unit", 1), "piece": ("unit", 1), "pieces": ("unit", 1),
    "unit": ("unit", 1), "units": ("unit", 1),
}
_QTY_RE = re.compile(
    r"(?<![\w.])(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*"
    r"(kgs|kg|gms|gm|grams|gram|g|ltrs|ltr|litres|litre|liters|liter|ml|l|pcs|pc|pieces|piece|units|unit)\b",
    re.I,
)


def parse_price(text: Optional[str]) -> Optional[Decimal]:
    """'₹1,29,999.00' -> Decimal('129999.00'). For ranges ('₹499 - ₹799') takes the
    first (lowest) number. Returns None for 'Free', empty text, or zero."""
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    try:
        value = Decimal(match.group().replace(",", ""))
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def clean_name(name: str) -> str:
    name = html.unescape(name).replace("\xa0", " ")
    return re.sub(r"\s+", " ", name).strip(" -–|,")


def extract_quantity(name: str) -> Optional[Quantity]:
    """First pack size in the name, converted to g / ml / unit ('2 x 500 g' -> 1000 g)."""
    for match in _QTY_RE.finditer(name):
        multiplier, amount, unit_raw = match.groups()
        # "5G" (network) is not 5 grams: a bare capital G on a tiny number is not a weight.
        if unit_raw == "G" and Decimal(amount) < 10:
            continue
        unit, factor = _UNITS[unit_raw.lower()]
        total = Decimal(amount) * factor * (Decimal(multiplier) if multiplier else 1)
        return Quantity(total.normalize(), unit)
    return None


def strip_quantity(name: str) -> str:
    return _QTY_RE.sub(" ", name)


def parse_stock(stock_text: Optional[str]) -> StockStatus:
    if stock_text and _OOS_RE.search(stock_text):
        return StockStatus.OUT_OF_STOCK
    return StockStatus.IN_STOCK


def normalize(raw: RawListing) -> Optional[Listing]:
    """RawListing -> Listing. Returns None when the price cannot be read."""
    price = parse_price(raw.price_text)
    if price is None:
        return None
    mrp = parse_price(raw.mrp_text)
    if mrp is not None and mrp < price:
        mrp = None  # a "MRP" below the price is a mis-scrape, not information
    name = clean_name(raw.product_name)
    if not name:
        return None
    return Listing(
        vendor=raw.vendor,
        name=name,
        price=price,
        url=raw.url,
        stock_status=parse_stock(raw.stock_text),
        image_url=raw.image_url,
        mrp=mrp,
        quantity=extract_quantity(name),
    )


def normalize_all(raws: Iterable[RawListing]) -> list[Listing]:
    return [l for l in (normalize(r) for r in raws) if l is not None]
