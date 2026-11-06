"""
src/firestore_service.py
========================
Handles persistence of trade records to Google Cloud Firestore in the 'nifty' collection.

Supports:
  - Recording open positions with strategy name, indicators at entry, and spot/strike info.
  - Updating closed positions with exit time, exit price, reason, and estimated/realized P&L.
  - Querying today's trades.
  - Safe error handling so database operations never disrupt the core trading loop.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)


class FirestoreService:
    """
    Manages trade storage in Firestore collection 'nifty'.

    Parameters
    ----------
    credentials_path : str
        Path to serviceAccountKey.json.
    collection_name : str
        Target Firestore collection name (default 'nifty').
    """

    def __init__(
        self,
        credentials_path: str = "config/serviceAccountKey.json",
        collection_name: str = "nifty",
    ) -> None:
        self.collection_name = collection_name
        self.credentials_path = credentials_path
        self._db: firestore.firestore.Client | None = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Firebase app instance and Firestore client."""
        if not os.path.exists(self.credentials_path):
            logger.warning(
                "Firestore service account key not found at '%s'. "
                "Firestore syncing will be disabled.",
                self.credentials_path,
            )
            return

        try:
            # Check if firebase app is already initialized
            try:
                app = firebase_admin.get_app()
            except ValueError:
                cred = credentials.Certificate(self.credentials_path)
                app = firebase_admin.initialize_app(cred)

            self._db = firestore.client(app)
            logger.info("Connected to Firestore. Collection: '%s'", self.collection_name)
        except Exception as exc:
            logger.error("Failed to initialize Firestore client: %s", exc)
            self._db = None

    @property
    def is_connected(self) -> bool:
        return self._db is not None

    def record_entry(
        self,
        trade_id: str,
        strategy_name: str,
        symbol: str,
        option_type: str,
        strike: int,
        spot_price: float,
        lot_size: int,
        entry_time: datetime,
        entry_price: float | None,
        indicators: dict[str, Any],
        live_trade: bool,
    ) -> bool:
        """
        Record a newly opened trade in the 'nifty' collection.
        """
        if not self.is_connected or self._db is None:
            logger.debug("Firestore not connected. Skipping record_entry.")
            return False

        try:
            entry_iso = entry_time.astimezone(IST).isoformat()
            now_iso = datetime.now(tz=IST).isoformat()

            data = {
                "trade_id": trade_id,
                "strategy_name": strategy_name,
                "symbol": symbol,
                "option_type": option_type,
                "strike": strike,
                "spot_price": spot_price,
                "lot_size": lot_size,
                "entry_time": entry_iso,
                "entry_price": entry_price,
                "indicators_at_entry": {
                    "rsi": indicators.get("rsi"),
                    "bull_trend": indicators.get("bull_trend"),
                    "bear_trend": indicators.get("bear_trend"),
                    "r1": indicators.get("r1"),
                    "s1": indicators.get("s1"),
                    "upper_bb": indicators.get("upper_bb"),
                    "lower_bb": indicators.get("lower_bb"),
                },
                "status": "OPEN",
                "live_trade": live_trade,
                "exit_time": None,
                "exit_price": None,
                "exit_reason": None,
                "pnl": None,
                "created_at": now_iso,
                "updated_at": now_iso,
            }

            self._db.collection(self.collection_name).document(trade_id).set(data)
            logger.info("Trade entry recorded in Firestore: %s (Strategy: %s)", trade_id, strategy_name)
            return True
        except Exception as exc:
            logger.error("Failed to record trade entry in Firestore: %s", exc)
            return False

    def record_exit(
        self,
        trade_id: str,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        pnl: float | None = None,
    ) -> bool:
        """
        Update an open trade record with exit details and P&L.
        """
        if not self.is_connected or self._db is None:
            logger.debug("Firestore not connected. Skipping record_exit.")
            return False

        try:
            exit_iso = exit_time.astimezone(IST).isoformat()
            now_iso = datetime.now(tz=IST).isoformat()

            update_data: dict[str, Any] = {
                "status": "CLOSED",
                "exit_time": exit_iso,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl": pnl,
                "updated_at": now_iso,
            }

            doc_ref = self._db.collection(self.collection_name).document(trade_id)
            doc_ref.update(update_data)
            logger.info("Trade exit recorded in Firestore: %s | PnL=%s", trade_id, pnl)
            return True
        except Exception as exc:
            logger.error("Failed to record trade exit in Firestore for %s: %s", trade_id, exc)
            return False

