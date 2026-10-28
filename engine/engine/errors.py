"""Error types used by the scraping job lifecycle.

`retryable` tells the job runner whether another attempt can help.
"""


class ScrapeError(Exception):
    retryable = True


class NetworkError(ScrapeError):
    """Timeout, connection reset, 5xx, etc. Usually transient."""


class BlockedError(ScrapeError):
    """Captcha / 403 / 429 / bot wall. Retry later with backoff."""


class SelectorError(ScrapeError):
    """Page loaded but nothing could be parsed: selectors are probably stale.

    Retrying will not help; an admin has to update the SiteConfig selectors.
    """

    retryable = False


class ConfigError(ScrapeError):
    """Vendor disabled, unknown, or a required dependency is missing."""

    retryable = False
