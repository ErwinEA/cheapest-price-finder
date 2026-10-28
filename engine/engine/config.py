"""SiteConfig: everything vendor-specific that can change without a code change.

The backend's admin endpoints edit these (selectors, fees, rate limits, enable
flag). The engine re-reads the config on every scrape, so edits apply live.

VERIFY BEFORE RELYING ON THESE: the selectors and fee values below are
best-effort defaults. Vendor markup changes often (Flipkart's class names are
obfuscated and rotate). Use `python -m engine diagnose <vendor> "<query>"` to
check which selectors still match a live page.
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from typing import Optional

from .errors import ConfigError

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass
class SiteConfig:
    vendor_id: str
    display_name: str
    base_url: str
    search_url_template: str  # must contain {query}
    # Selector keys: item, name, price, mrp, image, link, out_of_stock (optional).
    # Comma-separated selectors act as fallbacks.
    selectors: dict = field(default_factory=dict)
    image_attrs: tuple = ("src", "data-src")
    out_of_stock_texts: tuple = (
        "currently unavailable",
        "out of stock",
        "sold out",
        "notify me",
    )
    block_markers: tuple = ()  # text that means "bot wall / captcha"
    no_results_markers: tuple = ()  # text that means "genuinely zero results"
    render_js: bool = False  # True -> fetch through Playwright
    enabled: bool = True
    headers: dict = field(default_factory=lambda: dict(DEFAULT_HEADERS))
    cookies: dict = field(default_factory=dict)  # e.g. delivery pincode
    timeout_s: float = 20.0
    min_request_interval_s: float = 2.0  # rate limit between requests
    max_retries: int = 2  # retries after the first attempt
    retry_base_delay_s: float = 2.0  # exponential backoff base
    max_results: int = 20
    # Used by calculateEffectivePrice. PLACEHOLDER values: confirm per vendor.
    delivery_fee: Decimal = Decimal("0")
    free_delivery_threshold: Decimal = Decimal("0")  # fee applies below this
    handling_fee: Decimal = Decimal("0")

    @classmethod
    def from_dict(cls, data: dict) -> "SiteConfig":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        for key in ("delivery_fee", "free_delivery_threshold", "handling_fee"):
            if key in clean:
                clean[key] = Decimal(str(clean[key]))
        for key in ("image_attrs", "out_of_stock_texts", "block_markers", "no_results_markers"):
            if key in clean:
                clean[key] = tuple(clean[key])
        return cls(**clean)

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("delivery_fee", "free_delivery_threshold", "handling_fee"):
            data[key] = str(data[key])
        return data


class SiteConfigStore(ABC):
    """Implement this on top of the backend's database for admin-editable config."""

    @abstractmethod
    def get(self, vendor_id: str) -> SiteConfig: ...

    @abstractmethod
    def all(self) -> dict[str, SiteConfig]: ...

    @abstractmethod
    def update(self, vendor_id: str, **changes) -> SiteConfig: ...


class InMemorySiteConfigStore(SiteConfigStore):
    def __init__(self, configs: Optional[dict[str, SiteConfig]] = None):
        source = configs if configs is not None else DEFAULT_SITE_CONFIGS
        self._configs = {k: copy.deepcopy(v) for k, v in source.items()}

    def get(self, vendor_id: str) -> SiteConfig:
        try:
            return self._configs[vendor_id]
        except KeyError:
            raise ConfigError(f"No SiteConfig for vendor '{vendor_id}'") from None

    def all(self) -> dict[str, SiteConfig]:
        return dict(self._configs)

    def update(self, vendor_id: str, **changes) -> SiteConfig:
        current = self.get(vendor_id)
        merged = current.to_dict()
        merged.update(changes)
        self._configs[vendor_id] = SiteConfig.from_dict(merged)
        return self._configs[vendor_id]


DEFAULT_SITE_CONFIGS: dict[str, SiteConfig] = {
    "amazon": SiteConfig(
        vendor_id="amazon",
        display_name="Amazon",
        base_url="https://www.amazon.in",
        search_url_template="https://www.amazon.in/s?k={query}",
        selectors={
            "item": 'div[data-component-type="s-search-result"]',
            "name": "h2 span",
            "price": "span.a-price:not(.a-text-price) span.a-offscreen",
            "mrp": "span.a-price.a-text-price span.a-offscreen",
            "image": "img.s-image",
            "link": 'a[href*="/dp/"]',
        },
        block_markers=(
            "enter the characters you see below",
            "to discuss automated access to amazon data",
            "api-services-support@amazon.com",
        ),
        no_results_markers=("no results for", "did not match any products"),
        delivery_fee=Decimal("40"),
        free_delivery_threshold=Decimal("499"),
    ),
    "flipkart": SiteConfig(
        vendor_id="flipkart",
        display_name="Flipkart",
        base_url="https://www.flipkart.com",
        search_url_template="https://www.flipkart.com/search?q={query}",
        selectors={
            "item": "div[data-id]",
            "name": "div.KzDlHZ, a.wjcEIp, a.WKTcLC, a.IRpwTa",
            "price": "div.Nx9bqj, div._30jeq3",
            "mrp": "div.yRaY8j, div._3I9_wc",
            "image": "img",
            "link": 'a[href*="/p/"]',
        },
        block_markers=("are you a human", "access denied"),
        no_results_markers=("sorry, no results found", "no results found"),
        delivery_fee=Decimal("40"),
        free_delivery_threshold=Decimal("500"),
    ),
    "bigbasket": SiteConfig(
        vendor_id="bigbasket",
        display_name="BigBasket",
        base_url="https://www.bigbasket.com",
        search_url_template="https://www.bigbasket.com/ps/?q={query}&nc=as",
        selectors={
            "item": '[qa="product"], li[class*="PaginateItems"]',
            "name": '[qa="product_name"], h3',
            "price": '[qa="price"], span[class*="Pricing___StyledLabel"]',
            "mrp": '[qa="mrp"], span[class*="Pricing___StyledLabel"][class*="strike"]',
            "image": "img",
            "link": 'a[href*="/pd/"]',
        },
        render_js=True,  # BigBasket renders its listing client-side
        cookies={},  # set the delivery pincode cookie here; prices/stock depend on it
        block_markers=("access denied", "request blocked"),
        no_results_markers=("no results found", "couldn't find"),
        delivery_fee=Decimal("30"),
        free_delivery_threshold=Decimal("500"),
    ),
}
