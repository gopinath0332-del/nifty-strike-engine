"""
src/discord_notifier.py
=======================
Sends rich embed messages to a Discord webhook for all trade events.

Embed colours
-------------
  CE Entry  : 0x00C851  (green)
  PE Entry  : 0xFF4444  (red)
  ST Exit   : 0xFFBB33  (amber)
  EOD Exit  : 0x33B5E5  (blue)
  Profit    : 0x10B981  (emerald green)
  Loss      : 0xF43F5E  (rose red)
  Error     : 0x8B0000  (dark red)
  Info      : 0x888888  (grey)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import requests

logger = logging.getLogger(__name__)

# Embed colour constants
_COLOR = {
    "ce_entry":  0x00C851,
    "pe_entry":  0xFF4444,
    "st_exit":   0xFFBB33,
    "eod_exit":  0x33B5E5,
    "profit":    0x10B981,
    "loss":      0xF43F5E,
    "error":     0x8B0000,
    "info":      0x888888,
}


class DiscordNotifier:
    """
    Formats and delivers structured Discord webhook embeds.

    Parameters
    ----------
    webhook_url : str
        Full Discord webhook URL.
    live_order_placement : bool
        Included in every message footer so it is always visible.
    """

    def __init__(self, webhook_url: str, live_order_placement: bool = False) -> None:
        self._url   = webhook_url
        self._live  = live_order_placement

    # ------------------------------------------------------------------
    # Public notification methods
    # ------------------------------------------------------------------

    def ce_entry(
        self,
        symbol: str,
        strike: int,
        spot: float,
        qty: int,
        candle_time: datetime,
        rsi: float,
        upper_bb: float,
        r1: float,
        entry_price: float | None = None,
    ) -> None:
        fields = [
            ("Contract", symbol, True),
            ("NIFTY Spot", f"₹{spot:,.2f}", True),
            ("Lot Size", f"{qty} units (1 lot)", True),
        ]
        if entry_price is not None:
            margin = entry_price * qty
            fields.extend([
                ("Entry Premium", f"₹{entry_price:,.2f}", True),
                ("Margin Used", f"₹{margin:,.2f}", True),
            ])
        fields.extend([
            ("RSI (14)", f"{rsi:.1f}", True),
            ("Upper BB", f"₹{upper_bb:,.2f}", True),
            ("Pivot R1", f"₹{r1:,.2f}", True),
            ("Trigger Time", _fmt_time(candle_time), True),
        ])

        self._send(
            color=_COLOR["ce_entry"],
            title="🚀 Long CE Position Opened (Call Buy)",
            fields=fields,
        )

    def pe_entry(
        self,
        symbol: str,
        strike: int,
        spot: float,
        qty: int,
        candle_time: datetime,
        rsi: float,
        lower_bb: float,
        s1: float,
        entry_price: float | None = None,
    ) -> None:
        fields = [
            ("Contract", symbol, True),
            ("NIFTY Spot", f"₹{spot:,.2f}", True),
            ("Lot Size", f"{qty} units (1 lot)", True),
        ]
        if entry_price is not None:
            margin = entry_price * qty
            fields.extend([
                ("Entry Premium", f"₹{entry_price:,.2f}", True),
                ("Margin Used", f"₹{margin:,.2f}", True),
            ])
        fields.extend([
            ("RSI (14)", f"{rsi:.1f}", True),
            ("Lower BB", f"₹{lower_bb:,.2f}", True),
            ("Pivot S1", f"₹{s1:,.2f}", True),
            ("Trigger Time", _fmt_time(candle_time), True),
        ])

        self._send(
            color=_COLOR["pe_entry"],
            title="📉 Long PE Position Opened (Put Buy)",
            fields=fields,
        )

    def exit_signal(
        self,
        symbol: str,
        reason: str,
        entry_price: float | None,
        exit_price: float,
        candle_time: datetime,
        qty: int = 65,
    ) -> None:
        pnl = None
        pnl_str = "N/A"
        roi_str = "N/A"
        pts_str = "N/A"
        color = _COLOR["eod_exit"] if "EOD" in reason else _COLOR["st_exit"]

        if entry_price is not None and entry_price > 0:
            pts = exit_price - entry_price
            pnl = round(pts * qty, 2)
            pts_str = f"{pts:+.2f} pts"
            roi = (pnl / (entry_price * qty)) * 100
            pnl_str = f"₹{pnl:+,.2f}"
            roi_str = f"{roi:+.1f}%"
            color = _COLOR["profit"] if pnl >= 0 else _COLOR["loss"]

        title = "🚪 Position Closed"
        if pnl is not None:
            if pnl > 0:
                title = f"🟢 Trade Closed: PROFIT ₹{pnl:+,.2f} ({roi_str})"
            else:
                title = f"🔴 Trade Closed: LOSS ₹{pnl:+,.2f} ({roi_str})"

        fields = [
            ("Contract", symbol, True),
            ("Exit Reason", reason, True),
            ("Net Trade P&L", f"**{pnl_str}**", True),
            ("Entry Premium", f"₹{entry_price:,.2f}" if entry_price else "N/A", True),
            ("Exit Premium", f"₹{exit_price:,.2f}", True),
            ("Points Captured", pts_str, True),
            ("ROI on Margin", roi_str, True),
            ("Quantity", f"{qty} units", True),
            ("Exit Time", _fmt_time(candle_time), True),
        ]

        self._send(
            color=color,
            title=title,
            fields=fields,
        )

    def margin_insufficient(
        self,
        symbol: str,
        option_type: str,
        required_margin: float,
        available_balance: float,
        qty: int,
    ) -> None:
        shortfall = max(0.0, required_margin - available_balance)
        self._send(
            color=_COLOR["error"],
            title="❌ Order Aborted: Insufficient Margin",
            fields=[
                ("Contract", symbol, True),
                ("Option Type", option_type, True),
                ("Quantity", f"{qty} units", True),
                ("Required Margin", f"₹{required_margin:,.2f}", True),
                ("Available Cash", f"₹{available_balance:,.2f}", True),
                ("Shortfall", f"₹{shortfall:,.2f}", True),
                ("Action", "Deposit funds or adjust lot size in settings.yml", False),
            ],
        )

    def error(self, message: str) -> None:
        self._send(
            color=_COLOR["error"],
            title="🚨 Error Alert",
            fields=[("Detail", message, False)],
        )

    def info(self, message: str) -> None:
        self._send(
            color=_COLOR["info"],
            title="ℹ️ System Notification",
            fields=[("Message", message, False)],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, color: int, title: str, fields: list[tuple[str, str, bool]]) -> None:
        """Post a Discord embed payload; log warning on failure."""
        if not self._url:
            logger.warning("Discord webhook URL not configured; message dropped.")
            return

        payload = {
            "embeds": [
                {
                    "title":  title,
                    "color":  color,
                    "fields": [
                        {"name": name, "value": str(value), "inline": inline}
                        for name, value, inline in fields
                    ],
                    "footer": {
                        "text": f"Nifty Strike Engine • Mode: {'LIVE' if self._live else 'SIMULATION'}",
                    },
                    "timestamp": datetime.now(tz=IST).isoformat(),
                }
            ]
        }

        try:
            resp = requests.post(self._url, json=payload, timeout=8)
            if resp.status_code not in (200, 204):
                logger.warning(
                    "Discord webhook returned %d: %s",
                    resp.status_code, resp.text,
                )
        except Exception as exc:
            logger.warning("Failed to deliver Discord notification: %s", exc)


def _fmt_time(dt: datetime) -> str:
    """Format datetime as 'HH:MM IST'."""
    try:
        ist_dt = dt.astimezone(IST)
    except Exception:
        ist_dt = dt
    return ist_dt.strftime("%H:%M IST")
