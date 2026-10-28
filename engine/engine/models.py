"""Shared data contract for the engine.

Pipeline: adapter.parse() -> RawListing -> normalize() -> Listing
          -> calculateEffectivePrice() -> matchListing() -> ProductComparison

Listing.to_contract() emits the camelCase shape agreed with the backend:
{vendor, productName, price, stockStatus, url, imageUrl, timestamp, ...}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _f(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"


@dataclass(frozen=True)
class Quantity:
    """Pack size in canonical units: grams, millilitres, or item count."""

    amount: Decimal
    unit: str  # "g" | "ml" | "unit"

    def __str__(self) -> str:
        return f"{self.amount.normalize():f} {self.unit}"


@dataclass
class RawListing:
    """Exactly what the adapter scraped. Strings are untouched."""

    vendor: str
    product_name: str
    price_text: str
    url: str
    image_url: Optional[str] = None
    mrp_text: Optional[str] = None
    stock_text: Optional[str] = None  # text suggesting unavailability, else None


@dataclass
class Listing:
    """A cleaned, comparable offer from one vendor."""

    vendor: str
    name: str
    price: Decimal
    url: str
    stock_status: StockStatus = StockStatus.IN_STOCK
    image_url: Optional[str] = None
    mrp: Optional[Decimal] = None
    quantity: Optional[Quantity] = None
    timestamp: datetime = field(default_factory=utcnow)
    effective_price: Optional[Decimal] = None

    @property
    def in_stock(self) -> bool:
        return self.stock_status == StockStatus.IN_STOCK

    def to_contract(self) -> dict:
        return {
            "vendor": self.vendor,
            "productName": self.name,
            "price": _f(self.price),
            "stockStatus": self.stock_status.value,
            "url": self.url,
            "imageUrl": self.image_url,
            "timestamp": self.timestamp.isoformat(),
            "mrp": _f(self.mrp),
            "effectivePrice": _f(self.effective_price),
            "quantity": str(self.quantity) if self.quantity else None,
        }


@dataclass
class ProductComparison:
    """One product, with every vendor's offer for it, cheapest first."""

    name: str
    offers: list[Listing]
    best_offer: Optional[Listing]
    savings: Decimal  # most expensive in-stock offer minus the best one
    relevance: float = 100.0

    def to_dict(self) -> dict:
        return {
            "productName": self.name,
            "relevance": round(self.relevance, 1),
            "bestOffer": self.best_offer.to_contract() if self.best_offer else None,
            "savings": _f(self.savings),
            "offers": [o.to_contract() for o in self.offers],
        }
