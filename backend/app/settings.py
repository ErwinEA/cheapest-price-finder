from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "sqlite:///./pricefinder.db"
    jwt_secret: str = Field(min_length=32)
    token_minutes: int = Field(default=30, ge=1, le=1440)
    cors_origins: list[str] = ["http://localhost:3000"]
    fixture_mode: bool = False
    alert_interval_seconds: int = Field(default=900, ge=60)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    email_from: str = "alerts@example.com"
    vapid_private_key: str | None = None
    vapid_public_key: str | None = None
    vapid_subject: str = "mailto:admin@example.com"

    @model_validator(mode="after")
    def safe_secret(self):
        if self.jwt_secret.lower() in {"change-me" * 4, "x" * 32}:
            raise ValueError("Generate a random JWT_SECRET; do not use a placeholder")
        return self
