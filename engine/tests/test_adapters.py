import pytest

from engine import BlockedError, ConfigError, NetworkError
from engine.adapters import AmazonAdapter, BigBasketAdapter, FlipkartAdapter
from engine.fetchers import FixtureFetcher


def adapter(cls, store, pages):
    return cls(store, FixtureFetcher(pages))


def test_amazon_parse(store, pages):
    a = adapter(AmazonAdapter, store, pages)
    raws = a.parse(a.scrape("amul butter"))
    # widget (no price) and the unavailable ghee (no price) are dropped
    assert [r.product_name for r in raws] == [
        "Amul Pasteurised Butter 500 g (Carton)",
        "Amul Pasteurised Butter 100 g",
        "Amul Cheese Slices 200 g",
    ]
    first = raws[0]
    assert first.vendor == "amazon"
    assert first.price_text == "₹285" and first.mrp_text == "₹305"
    assert first.url == "https://www.amazon.in/dp/B000000001"  # tracking stripped
    assert first.image_url == "https://m.media-amazon.com/images/I/amul500.jpg"


def test_flipkart_parse(store, pages):
    a = adapter(FlipkartAdapter, store, pages)
    raws = a.parse(a.scrape("amul butter"))
    assert [r.price_text for r in raws] == ["₹279", "₹56"]  # banner card dropped
    assert raws[0].url == "https://www.flipkart.com/amul-pasteurised-butter/p/itm111?pid=BTRX1"
    assert raws[0].mrp_text == "₹305"


def test_bigbasket_parse(store, pages):
    a = adapter(BigBasketAdapter, store, pages)
    raws = a.parse(a.scrape("amul butter"))
    assert len(raws) == 2
    assert raws[0].url == "https://www.bigbasket.com/pd/10000001/amul-butter-pasteurised-500-g/"
    assert raws[0].image_url == "https://www.bbassets.com/media/amul500.jpg"  # skipped the data: placeholder
    assert raws[0].mrp_text == "₹290"
    assert raws[0].stock_text is None
    assert raws[1].stock_text is not None  # Out of Stock / Notify Me


def test_search_url_is_built_from_config(store, pages):
    a = adapter(AmazonAdapter, store, pages)
    assert a.build_search_url(" amul butter ") == "https://www.amazon.in/s?k=amul+butter"


def test_captcha_page_is_blocked(store):
    a = adapter(AmazonAdapter, store, {"amazon": "<html>Enter the characters you see below</html>"})
    with pytest.raises(BlockedError):
        a.scrape("x")


@pytest.mark.parametrize("status", [403, 429, 503])
def test_block_statuses(store, status):
    a = adapter(FlipkartAdapter, store, {"flipkart": (status, "")})
    with pytest.raises(BlockedError):
        a.scrape("x")


def test_server_error_is_network_error(store):
    a = adapter(FlipkartAdapter, store, {"flipkart": (500, "")})
    with pytest.raises(NetworkError):
        a.scrape("x")


def test_disabled_vendor_refuses(store, pages):
    store.update("amazon", enabled=False)
    with pytest.raises(ConfigError):
        adapter(AmazonAdapter, store, pages).scrape("x")


def test_selectors_come_from_config_live(store, pages):
    a = adapter(AmazonAdapter, store, pages)
    html = a.scrape("amul butter")
    assert len(a.parse(html)) == 3
    broken = dict(store.get("amazon").selectors, item="div.no-such-class")
    store.update("amazon", selectors=broken)  # no new adapter, no redeploy
    assert a.parse(html) == []


def test_max_results_caps_output(store, pages):
    store.update("amazon", max_results=1)
    a = adapter(AmazonAdapter, store, pages)
    assert len(a.parse(a.scrape("x"))) == 1
