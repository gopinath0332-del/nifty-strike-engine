"""
config/settings.py
==================
Application Settings Loader.

Architecture:
  - Secrets & Credentials (API keys, secrets, tokens, webhook) -> .env
  - All Strategy, Trade, Timing, and Indicator settings -> config/settings.yml
    (SINGLE SOURCE OF TRUTH)
"""

from __future__ import annotations

import os
import re
import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SETTINGS_YML_PATH = os.path.join(os.path.dirname(__file__), "settings.yml")
SETTINGS_YAML_PATH = os.path.join(os.path.dirname(__file__), "settings.yaml")


def _resolve_yaml_path(custom_path: str | None = None) -> str:
    """Resolves path to settings.yml (primary single source of truth)."""
    if custom_path and os.path.exists(custom_path):
        return custom_path
    if os.path.exists(SETTINGS_YML_PATH):
        return SETTINGS_YML_PATH
    if os.path.exists(SETTINGS_YAML_PATH):
        return SETTINGS_YAML_PATH
    return SETTINGS_YML_PATH


def _load_yaml_settings(yaml_path: str | None = None) -> dict:
    """
    Parses settings.yml into a dictionary.
    settings.yml is the single source of truth for all strategy parameters.
    """
    target_path = _resolve_yaml_path(yaml_path)
    if not os.path.exists(target_path):
        raise FileNotFoundError(
            f"Configuration file not found: {target_path}. "
            "config/settings.yml is required as the single source of truth."
        )

    with open(target_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}

    parsed = {}

    # Trade settings
    trade = doc.get("trade", {})
    if "lot_size" in trade:
        parsed["lot_size"] = int(trade["lot_size"])
    if "max_trades_per_day" in trade:
        parsed["max_trades_per_day"] = int(trade["max_trades_per_day"])

    # Strategy & Database
    strat = doc.get("strategy", {})
    if "name" in strat:
        parsed["strategy_name"] = str(strat["name"]).strip()
    if "firestore_credentials_path" in strat:
        parsed["firestore_credentials_path"] = str(strat["firestore_credentials_path"]).strip()
    if "firestore_collection" in strat:
        parsed["firestore_collection"] = str(strat["firestore_collection"]).strip()

    # Logging
    logging_elem = doc.get("logging", {})
    if "level" in logging_elem:
        parsed["log_level"] = str(logging_elem["level"]).strip().upper()

    # Indicators
    ind = doc.get("indicators", {})
    st = ind.get("supertrend", {})
    if "atr_period" in st:
        parsed["st_atr_period"] = int(st["atr_period"])
    if "factor" in st:
        parsed["st_factor"] = float(st["factor"])
    if "buffer_points" in st:
        parsed["st_buffer_points"] = float(st["buffer_points"])

    rsi_elem = ind.get("rsi", {})
    if "length" in rsi_elem:
        parsed["rsi_length"] = int(rsi_elem["length"])

    bb = ind.get("bollinger_bands", {})
    if "length" in bb:
        parsed["bb_length"] = int(bb["length"])
    if "multiplier" in bb:
        parsed["bb_mult"] = float(bb["multiplier"])

    # Timing
    timing = doc.get("timing", {})
    if "candle_interval_minutes" in timing:
        parsed["candle_interval_minutes"] = int(timing["candle_interval_minutes"])
    if "entry_cutoff_time" in timing:
        parsed["entry_cutoff_time"] = str(timing["entry_cutoff_time"]).strip()
    if "eod_exit_time" in timing:
        parsed["eod_exit_time"] = str(timing["eod_exit_time"]).strip()

    return parsed


class Settings(BaseSettings):
    """
    Application Settings:
      - Secrets & credentials validated from .env
      - Strategy, indicators, timing, and trading parameters loaded from config/settings.yml
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Secrets & Credentials (Loaded from .env) -------------
    kite_api_key: str
    kite_api_secret: str
    kite_access_token: str
    discord_webhook_url: str

    # ---- Live Trading Gate (from .env) -----------------------
    live_order_placement: bool = False

    # ---- Strategy & Application Parameters (from settings.yml) -
    strategy_name: str
    firestore_credentials_path: str
    firestore_collection: str

    # Trade settings
    lot_size: int
    max_trades_per_day: int

    # Indicators
    st_atr_period: int
    st_factor: float
    st_buffer_points: float
    rsi_length: int
    bb_length: int
    bb_mult: float

    # Logging
    log_level: str

    # Timing
    candle_interval_minutes: int
    entry_cutoff_time: str
    eod_exit_time: str

    @classmethod
    def load(cls, yaml_path: str = SETTINGS_YML_PATH) -> "Settings":
        """
        Loads application settings merging .env credentials with config/settings.yml
        as the authoritative single source of truth.
        """
        yaml_settings = _load_yaml_settings(yaml_path)
        return cls(**yaml_settings)

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
def get_settings(yaml_path: str = SETTINGS_YML_PATH) -> Settings:
    return Settings.load(yaml_path=yaml_path)
