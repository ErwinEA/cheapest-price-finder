from urllib.parse import urlparse

from .base import VendorAdapter


class BigBasketAdapter(VendorAdapter):
    """BigBasket renders results client-side (SiteConfig.render_js=True), and
    price/stock depend on the delivery pincode: set it in SiteConfig.cookies."""

    vendor_id = "bigbasket"

    def clean_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
