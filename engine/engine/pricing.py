from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .config import SiteConfig
from .models import Listing

_PAISE = Decimal("0.01")


def calculateEffectivePrice(listing: Listing, config: SiteConfig) -> Decimal:
    """What the buyer actually pays for one item from this vendor:

        price + delivery_fee (if price < free_delivery_threshold) + handling_fee

    All three values come from SiteConfig, so they can be tuned per vendor.
    """
    total = listing.price
    if listing.price < config.free_delivery_threshold:
        total += config.delivery_fee
    total += config.handling_fee
    return total.quantize(_PAISE, rounding=ROUND_HALF_UP)
