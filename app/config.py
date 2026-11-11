"""Application settings loaded from environment variables (and an optional .env file).

Only pydantic (no pydantic-settings) is used so the core stays dependency-light.
Secrets are never logged: use ``Settings.safe_dict()`` for diagnostics.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class PaymentsMode(str, Enum):
    DISABLED = "disabled"  # no invoices; admins can grant subscriptions manually
    TEST = "test"  # Telegram test environment (bot token created in the test DC)
    LIVE = "live"  # real Telegram Stars; requires PAYMENTS_LIVE_CONFIRMED=yes


SECRET_FIELDS = {"bot_token", "database_url", "redis_url", "webhook_secret", "crypto_webhook_secret", "ai_api_key"}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _split_csv(value: object) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [item.strip() for item in str(value).split(",") if item.strip()]


class Settings(BaseModel):
    bot_token: str = ""
    admin_ids: list[int] = Field(default_factory=list)
    database_url: str = "postgresql+psycopg://market:market@localhost:5432/market"
    redis_url: str = "redis://localhost:6379/0"

    environment: str = "development"
    log_level: str = "INFO"
    default_language: str = "pl"
    default_timezone: str = "Europe/Warsaw"

    # Sources. Real sources stay disabled until legal access is confirmed (docs/SOURCES.md).
    enabled_sources: list[str] = Field(default_factory=lambda: ["mock"])
    # Personal mode: the bot serves ONLY the admins (ADMIN_IDS), sells nothing, and may read
    # OLX public search results at a low rate (docs/PERSONAL_MODE.md). Commercial mode when false.
    personal_mode: bool = False
    # python -m app.dev_run keeps data in: "file" (data/dev_state.pickle) or "postgres" (DATABASE_URL)
    storage: str = "file"
    source_min_request_interval_seconds: float = 10.0
    source_timeout_seconds: float = 15.0

    # Scheduler / worker.
    scheduler_tick_seconds: int = 30
    max_checks_per_user_per_tick: int = 3
    max_checks_per_tick: int = 200
    notification_batch_size: int = 50
    notification_max_attempts: int = 5
    notify_on_first_run: bool = False

    # Payments.
    payments_mode: PaymentsMode = PaymentsMode.DISABLED
    payments_live_confirmed: bool = False
    crypto_enabled: bool = False
    crypto_webhook_secret: str = ""

    # HTTP API.
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    webhook_secret: str = ""

    # Rate limiting of bot commands.
    command_rate_per_minute: int = 30

    # Data retention (days).
    retention_listings_days: int = 30
    retention_runs_days: int = 14
    retention_notifications_days: int = 90

    support_contact: str = "@support"

    # AI purchase advisor (app/services/ai.py): disabled | ollama | anthropic | openai_compat
    ai_provider: str = "disabled"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_base_url: str = ""
    ai_daily_limit: int = 30
    ai_max_tokens: int = 1500
    ai_vision: bool = False  # send the listing photo to the model (only for vision models, e.g. qwen2.5vl)
    # Hot-deal mode: the AI reads the description of every deal candidate before it is sent (local model = free).
    ai_screen_deals: bool = True
    # Evening summary for the owner in personal mode (local hour, 0-23; -1 = off)
    digest_hour: int = 21

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _admins(cls, value: object) -> list[int]:
        return [int(v) for v in _split_csv(value)]

    @field_validator("enabled_sources", mode="before")
    @classmethod
    def _sources(cls, value: object) -> list[str]:
        return [str(v).lower() for v in _split_csv(value)]

    @field_validator("payments_live_confirmed", "crypto_enabled", "notify_on_first_run", "personal_mode",
                     "ai_vision", "ai_screen_deals", mode="before")
    @classmethod
    def _bool(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @property
    def live_payments_allowed(self) -> bool:
        return self.payments_mode is PaymentsMode.LIVE and self.payments_live_confirmed

    @property
    def payments_available(self) -> bool:
        if self.personal_mode:  # personal use: nothing is sold
            return False
        return self.payments_mode is PaymentsMode.TEST or self.live_payments_allowed

    @property
    def ai_ready(self) -> bool:
        if self.ai_provider == "anthropic":
            return bool(self.ai_api_key)
        if self.ai_provider == "openai_compat":
            return bool(self.ai_base_url and self.ai_model)
        if self.ai_provider == "ollama":
            return bool(self.ai_model)
        return False

    @property
    def real_sources_allowed(self) -> set[str]:
        """Real marketplaces that may be fetched in the current mode."""
        return {"olx"} if self.personal_mode else set()

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids

    def safe_dict(self) -> dict:
        data = self.model_dump(mode="json")
        for key in SECRET_FIELDS:
            if data.get(key):
                data[key] = "***"
        return data


def load_settings(env: dict[str, str] | None = None, env_file: str | Path | None = ".env") -> Settings:
    """Build settings: .env file values are overridden by real environment variables."""
    merged: dict[str, str] = {}
    if env_file:
        merged.update(_parse_env_file(Path(env_file)))
    merged.update(os.environ if env is None else env)
    fields = Settings.model_fields
    data = {name: merged[name.upper()] for name in fields if name.upper() in merged}
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
