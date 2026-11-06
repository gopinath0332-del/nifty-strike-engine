"""
config/settings.py
==================
Loads and validates all environment variables using pydantic-settings.
Raises a clear error on startup if any required key is missing.
"""

from __future__ import annotations

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Zerodha Kite Connect --------------------------------
    kite_api_key: str
    kite_api_secret: str
    kite_access_token: str

    # ---- Discord ---------------------------------------------
    discord_webhook_url: str

    # ---- Live Trading Flag -----------------------------------
    live_order_placement: bool = False

    # ---- Trade Settings --------------------------------------
    lot_size: int = 65
    max_trades_per_day: int = 1

    # ---- Indicator Parameters --------------------------------
    st_atr_period: int = 10
    st_factor: float = 3.0
    rsi_length: int = 14
    bb_length: int = 20
    bb_mult: float = 2.0

    # ---- Strategy & Logging ----------------------------------
    strategy_name: str = "NIFTY Options Buying Strategy v9"
    firestore_credentials_path: str = "config/serviceAccountKey.json"
    firestore_collection: str = "nifty"
    log_level: str = "INFO"

    # ---- Session Timing (IST) --------------------------------
    candle_interval_minutes: int = 3
    entry_cutoff_time: str = "14:45"
    eod_exit_time: str = "15:00"

    @field_validator("kite_api_key", "kite_api_secret", "kite_access_token", "discord_webhook_url")
    @classmethod
    def must_not_be_placeholder(cls, v: str, info) -> str:
        placeholders = {"your_api_key_here", "your_api_secret_here", "your_access_token_here"}
        if v.strip() in placeholders or v.strip() == "":
            raise ValueError(
                f"Field '{info.field_name}' has a placeholder/empty value. "
                "Please fill in your .env file."
            )
        return v.strip()

    @model_validator(mode="after")
    def validate_time_formats(self) -> "Settings":
        import re
        pattern = re.compile(r"^\d{2}:\d{2}$")
        for field_name in ("entry_cutoff_time", "eod_exit_time"):
            val = getattr(self, field_name)
            if not pattern.match(val):
                raise ValueError(f"{field_name} must be in HH:MM format, got '{val}'")
        return self

    @property
    def entry_cutoff_hhmm(self) -> tuple[int, int]:
        h, m = self.entry_cutoff_time.split(":")
        return int(h), int(m)

    @property
    def eod_exit_hhmm(self) -> tuple[int, int]:
        h, m = self.eod_exit_time.split(":")
        return int(h), int(m)


# Singleton getter — imported or instantiated when needed
def get_settings() -> Settings:
    return Settings()


