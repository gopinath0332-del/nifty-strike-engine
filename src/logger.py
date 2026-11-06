"""
src/logger.py
=============
Production logging configuration for Nifty Strike Engine.

Features:
  - Console handler with clean formatted output.
  - Daily rotating log files in logs/ directory (TimedRotatingFileHandler).
  - Dedicated signals log (logs/signals.log) specifically recording candle evaluations,
    indicator values, entry/exit triggers for quick auditing.
  - Configurable log levels via environment variable or settings.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(log_level: str = "INFO") -> logging.Logger:
    """
    Configure application-wide root logger and specialized handlers.

    Parameters
    ----------
    log_level : str
        Level name: DEBUG, INFO, WARNING, ERROR, CRITICAL
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logger is invoked more than once
    if root.handlers:
        root.handlers.clear()

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DATE_FORMAT)

    # 1. Console Stream Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # 2. Main Daily Rotating File Handler (retains 30 days of logs)
    app_log_path = os.path.join(LOG_DIR, "engine.log")
    file_handler = TimedRotatingFileHandler(
        app_log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 3. Dedicated Signals Audit Log (separate logger for fast review of trade signals & indicators)
    signal_logger = logging.getLogger("signals")
    signal_logger.setLevel(logging.INFO)
    signal_log_path = os.path.join(LOG_DIR, "signals.log")
    signal_handler = TimedRotatingFileHandler(
        signal_log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    signal_fmt = logging.Formatter("%(asctime)s %(message)s", datefmt=DATE_FORMAT)
    signal_handler.setFormatter(signal_fmt)
    signal_logger.addHandler(signal_handler)
    signal_logger.propagate = True  # Also show in main engine log

    logger = logging.getLogger("nifty_strike_engine")
    logger.info("Logging initialized. App log: %s | Level: %s", app_log_path, log_level.upper())
    return logger

