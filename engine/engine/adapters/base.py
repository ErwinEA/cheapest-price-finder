"""VendorAdapter: the base class every vendor adapter extends.

    scrape(query) -> html            fetch the search page (raises ScrapeError)
    parse(html)   -> [RawListing]    extract cards using SiteConfig selectors

Selectors are read from SiteConfig on every call, so admin edits apply without
a redeploy. Subclasses only override small hooks (e.g. clean_url).
"""
from __future__ import annotations

from abc import ABC
from typing import Optional
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..config import SiteConfig, SiteConfigStore
from ..errors import BlockedError, ConfigError, NetworkError
from ..fetchers import DefaultFetcher, Fetcher
from ..models import RawListing


class VendorAdapter(ABC):
    vendor_id: str = ""

    def __init__(self, config_store: SiteConfigStore, fetcher: Optional[Fetcher] = None):
        if not self.vendor_id:
            raise TypeError(f"{type(self).__name__} must set vendor_id")
        self._store = config_store
        self._fetcher = fetcher or DefaultFetcher()

    @property
    def config(self) -> SiteConfig:
        return self._store.get(self.vendor_id)

    # ---- scrape -----------------------------------------------------------
    def build_search_url(self, query: str) -> str:
        return self.config.search_url_template.format(query=quote_plus(query.strip()))

    def scrape(self, query: str) -> str:
        cfg = self.config
        if not cfg.enabled:
            raise ConfigError(f"{self.vendor_id} is disabled")
        if not query.strip():
            raise ConfigError("empty query")

        resp = self._fetcher.get(
            self.build_search_url(query),
            headers=cfg.headers,
            cookies=cfg.cookies,
            render_js=cfg.render_js,
            timeout=cfg.timeout_s,
        )
        if resp.status in (403, 429, 503) or self.looks_blocked(resp.text, cfg):
            raise BlockedError(f"{self.vendor_id} blocked the request (HTTP {resp.status})")
        if resp.status >= 400:
            raise NetworkError(f"{self.vendor_id} returned HTTP {resp.status}")
        return resp.text

    @staticmethod
    def looks_blocked(html: str, cfg: SiteConfig) -> bool:
        lowered = html.lower()
        return any(marker in lowered for marker in cfg.block_markers)

    # ---- parse ------------------------------------------------------------
    def parse(self, html: str) -> list[RawListing]:
        cfg = self.config
        item_selector = cfg.selectors.get("item")
        if not item_selector:
            raise ConfigError(f"{self.vendor_id}: selectors['item'] is not set")

        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        seen_urls: set[str] = set()
        for card in soup.select(item_selector):
            raw = self.parse_card(card, cfg)
            if raw and raw.url not in seen_urls:  # nested or repeated cards
                seen_urls.add(raw.url)
                listings.append(raw)
            if len(listings) >= cfg.max_results:
                break
        return listings

    def parse_card(self, card: Tag, cfg: SiteConfig) -> Optional[RawListing]:
        sel = cfg.selectors
        name = self._text(card, sel.get("name"))
        price = self._text(card, sel.get("price"))
        href = self._attr(card, sel.get("link"), ("href",))
        if not (name and price and href):
            return None  # not a product card, or no price to compare

        url = self.clean_url(urljoin(cfg.base_url, href))
        image = self._attr(card, sel.get("image"), cfg.image_attrs, skip_prefix="data:")
        return RawListing(
            vendor=self.vendor_id,
            product_name=name,
            price_text=price,
            url=url,
            image_url=urljoin(cfg.base_url, image) if image else None,
            mrp_text=self._text(card, sel.get("mrp")),
            stock_text=self.find_stock_text(card, cfg),
        )

    def clean_url(self, url: str) -> str:
        """Hook: strip tracking parameters. Default keeps the URL as is."""
        return url

    def find_stock_text(self, card: Tag, cfg: SiteConfig) -> Optional[str]:
        selector = cfg.selectors.get("out_of_stock")
        if selector:
            el = card.select_one(selector)
            if el is not None:
                return el.get_text(" ", strip=True) or "out of stock"
        text = card.get_text(" ", strip=True).lower()
        for marker in cfg.out_of_stock_texts:
            if marker in text:
                return marker
        return None

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _text(card: Tag, selector: Optional[str]) -> Optional[str]:
        if not selector:
            return None
        el = card.select_one(selector)
        if el is None:
            return None
        text = el.get_text(" ", strip=True)
        return text or None

    @staticmethod
    def _attr(card: Tag, selector: Optional[str], attrs, skip_prefix: str = "") -> Optional[str]:
        els = [card] if not selector else card.select(selector)
        for el in els:
            for attr in attrs:
                value = el.get(attr)
                if isinstance(value, list):
                    value = value[0] if value else None
                if value and not (skip_prefix and value.startswith(skip_prefix)):
                    return value
        return None
