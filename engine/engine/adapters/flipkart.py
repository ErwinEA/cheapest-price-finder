from urllib.parse import parse_qs, urlparse

from .base import VendorAdapter


class FlipkartAdapter(VendorAdapter):
    vendor_id = "flipkart"

    def clean_url(self, url: str) -> str:
        """Keep only the product id (?pid=...) and drop search/tracking params."""
        parsed = urlparse(url)
        pid = parse_qs(parsed.query).get("pid")
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return f"{base}?pid={pid[0]}" if pid else base
