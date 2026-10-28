import re

from .base import VendorAdapter

_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")


class AmazonAdapter(VendorAdapter):
    vendor_id = "amazon"

    def clean_url(self, url: str) -> str:
        """Reduce long tracking URLs to https://www.amazon.in/dp/<ASIN>."""
        match = _ASIN.search(url)
        if match:
            return f"{self.config.base_url}/dp/{match.group(1)}"
        return url.split("?")[0]
