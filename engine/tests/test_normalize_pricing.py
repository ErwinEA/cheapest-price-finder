from decimal import Decimal

import pytest

from engine import Listing, SiteConfig, calculateEffectivePrice, normalize, parse_price
from engine.models import RawListing, StockStatus
from engine.normalize import extract_quantity


@pytest.mark.parametrize("text,expected", [
    ("₹285", "285.00"),
    ("₹1,29,999.00", "129999.00"),
    ("Rs. 499", "499.00"),
    ("Rs.499/-", "499.00"),
    ("₹499 - ₹799", "499.00"),
    ("M.R.P.: ₹799", "799.00"),
    ("1,299.5", "1299.50"),
])
def test_parse_price(text, expected):
    assert parse_price(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "Free", "Currently unavailable", "₹0"])
def test_parse_price_unreadable(text):
    assert parse_price(text) is None


@pytest.mark.parametrize("name,amount,unit", [
    ("Amul Butter 500 g", "500", "g"),
    ("Aashirvaad Atta 5 kg", "5000", "g"),
    ("Tata Salt 2 x 500 g", "1000", "g"),
    ("Fortune Oil 1.5 L", "1500", "ml"),
    ("Real Juice 200ml", "200", "ml"),
    ("Eggs 6 pcs", "6", "unit"),
])
def test_extract_quantity(name, amount, unit):
    q = extract_quantity(name)
    assert (q.amount, q.unit) == (Decimal(amount), unit)


@pytest.mark.parametrize("name", [
    "Samsung Galaxy M14 5G 128GB",   # 5G is a network, 128GB is storage
    "Apple iPhone 15 (128 GB) - Black",
    "Boat Airdopes 141",
])
def test_extract_quantity_ignores_specs(name):
    assert extract_quantity(name) is None


def test_normalize_builds_listing():
    raw = RawListing("amazon", "  Amul  Butter &amp; Co 500 g ", "₹1,285.00", "https://x/dp/1",
                     mrp_text="₹1,305", stock_text=None)
    listing = normalize(raw)
    assert listing.name == "Amul Butter & Co 500 g"
    assert listing.price == Decimal("1285.00")
    assert listing.mrp == Decimal("1305.00")
    assert listing.stock_status == StockStatus.IN_STOCK
    assert str(listing.quantity) == "500 g"


def test_normalize_drops_bad_price_and_bad_mrp():
    assert normalize(RawListing("amazon", "X", "Free", "u")) is None
    listing = normalize(RawListing("amazon", "X", "₹100", "u", mrp_text="₹50"))
    assert listing.mrp is None


def test_normalize_out_of_stock():
    listing = normalize(RawListing("bigbasket", "X", "₹10", "u", stock_text="notify me"))
    assert listing.stock_status == StockStatus.OUT_OF_STOCK


def _cfg(**kw):
    base = dict(vendor_id="v", display_name="V", base_url="https://v", search_url_template="https://v/{query}")
    return SiteConfig(**{**base, **kw})


def test_effective_price_adds_delivery_below_threshold():
    cfg = _cfg(delivery_fee=Decimal("40"), free_delivery_threshold=Decimal("499"))
    assert calculateEffectivePrice(Listing("v", "x", Decimal("285"), "u"), cfg) == Decimal("325.00")


def test_effective_price_free_delivery_at_threshold():
    cfg = _cfg(delivery_fee=Decimal("40"), free_delivery_threshold=Decimal("499"))
    assert calculateEffectivePrice(Listing("v", "x", Decimal("499"), "u"), cfg) == Decimal("499.00")


def test_effective_price_handling_fee_always_applies():
    cfg = _cfg(handling_fee=Decimal("5"))
    assert calculateEffectivePrice(Listing("v", "x", Decimal("1000"), "u"), cfg) == Decimal("1005.00")
