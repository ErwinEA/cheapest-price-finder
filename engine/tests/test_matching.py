from decimal import Decimal

from engine import Listing, compare_and_rank, group_listings, matchListing
from engine.models import StockStatus
from engine.normalize import extract_quantity


def L(vendor, name, price, eff=None, stock=StockStatus.IN_STOCK):
    return Listing(vendor, name, Decimal(price), f"https://{vendor}/{name}", stock,
                   quantity=extract_quantity(name),
                   effective_price=Decimal(eff) if eff else None)


def test_same_product_different_titles_match():
    a = L("amazon", "Amul Pasteurised Butter 500 g (Carton)", "285")
    b = L("bigbasket", "Amul Butter - Pasteurised 500 g", "275")
    assert matchListing(a, b).is_match


def test_quantity_mismatch_never_matches():
    r = matchListing(L("a", "Amul Butter 500 g", "1"), L("b", "Amul Butter 100 g", "1"))
    assert not r.is_match and "quantity" in r.reason


def test_same_quantity_different_units_match():
    assert matchListing(L("a", "Amul Butter 1 kg", "1"), L("b", "Amul Butter 1000 g", "1")).is_match


def test_variant_mismatch():
    r = matchListing(L("a", "Apple iPhone 15 (128 GB) - Black", "1"),
                     L("b", "Apple iPhone 15 Pro (128 GB) - Black", "1"))
    assert not r.is_match and "variant" in r.reason


def test_storage_mismatch():
    r = matchListing(L("a", "Apple iPhone 15 (128 GB) - Black", "1"),
                     L("b", "Apple iPhone 15 (256 GB) - Black", "1"))
    assert not r.is_match and "spec" in r.reason


def test_one_side_omitting_ram_still_matches():
    a = L("amazon", "Samsung Galaxy M14 5G (6GB RAM, 128GB Storage, Blue)", "1")
    b = L("flipkart", "Samsung Galaxy M14 5G (Blue, 128 GB)", "1")
    assert matchListing(a, b).is_match


def test_accessory_does_not_match_phone():
    a = L("amazon", "Apple iPhone 15 128 GB", "1")
    b = L("flipkart", "iPhone 15 Back Case Cover", "1")
    assert not matchListing(a, b).is_match


def test_group_listings_groups_by_product():
    groups = group_listings([
        L("amazon", "Amul Butter 500 g", "285"),
        L("amazon", "Amul Butter 100 g", "58"),
        L("flipkart", "Amul Butter 500 g", "279"),
    ])
    assert sorted(len(g) for g in groups) == [1, 2]


def test_compare_and_rank_orders_offers_and_computes_savings():
    comps = compare_and_rank([
        L("amazon", "Amul Butter 500 g", "285", "325"),
        L("flipkart", "Amul Butter 500 g", "279", "319"),
        L("bigbasket", "Amul Butter 500 g", "275", "305"),
    ], query="amul butter")
    assert len(comps) == 1
    c = comps[0]
    assert [o.vendor for o in c.offers] == ["bigbasket", "flipkart", "amazon"]
    assert c.best_offer.vendor == "bigbasket"
    assert c.savings == Decimal("20")


def test_out_of_stock_never_wins_even_if_cheapest():
    comps = compare_and_rank([
        L("amazon", "Amul Butter 100 g", "58", "98"),
        L("bigbasket", "Amul Butter 100 g", "40", "70", StockStatus.OUT_OF_STOCK),
    ], query="amul butter")
    c = comps[0]
    assert c.best_offer.vendor == "amazon"
    assert c.offers[-1].vendor == "bigbasket"


def test_all_out_of_stock_has_no_best_offer_and_ranks_last():
    comps = compare_and_rank([
        L("a", "Amul Butter 100 g", "10", "10", StockStatus.OUT_OF_STOCK),
        L("a", "Amul Butter 500 g", "99", "99"),
    ], query="amul butter")
    assert comps[0].best_offer is not None
    assert comps[-1].best_offer is None


def test_irrelevant_results_are_filtered_by_query():
    comps = compare_and_rank([
        L("amazon", "Amul Butter 500 g", "285"),
        L("amazon", "Amul Cheese Slices 200 g", "130"),
    ], query="amul butter")
    assert [c.name for c in comps] == ["Amul Butter 500 g"]
