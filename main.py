"""
main.py
=======
Entry point for the Nifty Strike Engine.

Usage
-----
    python main.py

Ensure .env is populated with valid credentials before running.
In simulation mode (LIVE_ORDER_PLACEMENT=false, the default), no real
orders will be placed but all Discord notifications will fire.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

from src.logger import setup_logger

logger = logging.getLogger("nifty_strike_engine")


def main() -> None:
    # Validate settings eagerly (raises on bad/missing .env values)
    try:
        from config.settings import get_settings
        settings = get_settings()
    except Exception as exc:
        # Fallback console log if settings fails to load
        setup_logger("INFO")
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    # Initialize full logging infrastructure with level from settings
    setup_logger(settings.log_level)

    from src.session_manager import SessionManager

    manager = SessionManager(settings)

    # Graceful shutdown on SIGINT / SIGTERM (Ctrl-C or systemd stop)
    def _shutdown(signum, frame):  # noqa: ANN001
        logger.info("Shutdown signal received (%s). Stopping…", signum)
        manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("=" * 60)
    logger.info("  Nifty Strike Engine")
    logger.info("  Mode : %s", "LIVE" if settings.live_order_placement else "SIMULATION")
    logger.info("  Lot  : %d units", settings.lot_size)
    logger.info("  TF   : %dm", settings.candle_interval_minutes)
    logger.info("=" * 60)

    manager.run()


if __name__ == "__main__":
    main()

