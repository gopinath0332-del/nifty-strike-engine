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
import signal
import sys

# Configure logging before any other imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("nifty_strike_engine.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


def main() -> None:
    # Validate settings eagerly (raises on bad/missing .env values)
    try:
        from config.settings import settings
    except Exception as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

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

