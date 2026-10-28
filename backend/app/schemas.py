import base64
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

CATEGORIES = {"groceries", "electronics", "household", "fashion", "beauty"}
# Only fixed, known browser push providers can receive outbound requests.
PUSH_HOSTS = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class SearchInput(StrictModel):
    query: str = Field(min_length=2, max_length=200)
    category: str
    vendors: list[str] | None = Field(default=None, min_length=1, max_length=3)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value):
        value = " ".join(value.split())
        if len(value) < 2 or not any(c.isalnum() for c in value):
            raise ValueError("Query must contain at least two characters and a letter or number")
        return value

    @field_validator("category")
    @classmethod
    def known_category(cls, value):
        if value not in CATEGORIES:
            raise ValueError(f"Category must be one of {sorted(CATEGORIES)}")
        return value


class AlertInput(StrictModel):
    product_id: str
    target_price: Decimal = Field(gt=0, le=999999999999, max_digits=14, decimal_places=2)
    channel: str = "email"
    vendors: list[str] | None = Field(default=None, min_length=1, max_length=3)

    @field_validator("channel")
    @classmethod
    def known_channel(cls, value):
        if value not in {"email", "push"}:
            raise ValueError("Channel must be email or push")
        return value


class AlertPatch(StrictModel):
    target_price: Decimal | None = Field(default=None, gt=0, le=999999999999, max_digits=14, decimal_places=2)
    active: bool | None = None


class PushInput(StrictModel):
    endpoint: str = Field(max_length=2000)
    keys: dict[str, str]

    @field_validator("endpoint")
    @classmethod
    def safe_endpoint(cls, value):
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or host not in PUSH_HOSTS or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("Unsupported browser push endpoint")
        return value

    @field_validator("keys")
    @classmethod
    def valid_keys(cls, value):
        if set(value) != {"p256dh", "auth"}:
            raise ValueError("Subscription requires p256dh and auth keys")
        try:
            decoded = {k: base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)) for k, v in value.items()}
            if len(decoded["p256dh"]) != 65 or decoded["p256dh"][0] != 4 or len(decoded["auth"]) != 16:
                raise ValueError()
        except Exception:
            raise ValueError("Invalid browser subscription keys") from None
        return value


class ConfigPatch(StrictModel):
    enabled: bool | None = None
    selectors: dict[str, str] | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)
    timeout_s: float | None = Field(default=None, ge=1, le=60)
    min_request_interval_s: float | None = Field(default=None, ge=0.1, le=300)
    retry_base_delay_s: float | None = Field(default=None, ge=0.1, le=30)
    max_results: int | None = Field(default=None, ge=1, le=100)
    delivery_fee: Decimal | None = Field(default=None, ge=0, le=100000)
    free_delivery_threshold: Decimal | None = Field(default=None, ge=0, le=1000000)
    handling_fee: Decimal | None = Field(default=None, ge=0, le=100000)

    @field_validator("selectors")
    @classmethod
    def valid_selectors(cls, value):
        from soupsieve import compile as compile_selector
        if value is not None:
            allowed = {"item", "name", "price", "mrp", "image", "link", "out_of_stock"}
            if not set(value) <= allowed or not {"item", "name", "price", "link"} <= set(value):
                raise ValueError("Supply item, name, price, link and optional mrp/image/out_of_stock selectors")
            for selector in value.values():
                if not selector or len(selector) > 1000:
                    raise ValueError("Invalid selector length")
                try:
                    compile_selector(selector)
                except Exception:
                    raise ValueError("Invalid CSS selector") from None
        return value
