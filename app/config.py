"""Central configuration, loaded once from environment variables / .env.

Teaching note: every tunable lives here. No module reads os.environ directly,
so tests can build a Settings object by hand and never touch your real .env.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Telegram ---------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Kept as a plain string on purpose: pydantic-settings would try to JSON-decode
    # a list[int] field and choke on "123,456". We parse it ourselves below.
    allowed_telegram_user_ids: str = ""

    # --- Modes ------------------------------------------------------------
    test_mode: bool = True
    dry_run: bool = True

    # --- Storage / logging ------------------------------------------------
    database_path: Path = Path("data/monitor.db")
    log_level: str = "INFO"
    log_file: Path = Path("data/logs/monitor.log")

    # --- Polling ----------------------------------------------------------
    default_poll_interval_seconds: int = Field(default=60, ge=5)
    min_poll_interval_seconds: int = Field(default=30, ge=5)
    fetch_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=1, le=10)
    backoff_base_seconds: float = Field(default=5.0, gt=0)
    backoff_max_seconds: float = Field(default=300.0, gt=0)
    max_concurrent_checks: int = Field(default=4, ge=1, le=32)
    error_notify_threshold: int = Field(default=3, ge=1)
    renotify_cooldown_seconds: int = Field(default=3600, ge=0)

    # --- Real BookMyShow adapter -----------------------------------------
    enable_bookmyshow_source: bool = False
    bookmyshow_min_interval_seconds: int = Field(default=60, ge=5)
    bookmyshow_headless: bool = True
    respect_robots_txt: bool = True

    # --- Dashboard --------------------------------------------------------
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765

    # --- Display ----------------------------------------------------------
    display_timezone: str = "Asia/Kolkata"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------ #
    @property
    def allowed_user_ids(self) -> frozenset[int]:
        """Parse "111,222" -> {111, 222}. Empty => nobody is authorised."""
        ids: set[int] = set()
        for chunk in self.allowed_telegram_user_ids.split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    ids.add(int(chunk))
                except ValueError:
                    # A typo must not silently widen access, so we ignore it loudly
                    # (logging isn't configured yet at import time, hence print).
                    print(f"[config] ignoring non-numeric Telegram user id: {chunk!r}")
        return frozenset(ids)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the whole process shares one Settings instance."""
    return Settings()
