from .amazon import AmazonAdapter
from .base import VendorAdapter
from .bigbasket import BigBasketAdapter
from .flipkart import FlipkartAdapter

ADAPTER_CLASSES = (AmazonAdapter, FlipkartAdapter, BigBasketAdapter)


def build_default_adapters(config_store, fetcher=None) -> dict[str, VendorAdapter]:
    return {cls.vendor_id: cls(config_store, fetcher) for cls in ADAPTER_CLASSES}


__all__ = [
    "VendorAdapter",
    "AmazonAdapter",
    "FlipkartAdapter",
    "BigBasketAdapter",
    "ADAPTER_CLASSES",
    "build_default_adapters",
]
