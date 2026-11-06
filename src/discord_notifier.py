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
    "error":     0x8B0000,
    "info":      0x888888,
}


class DiscordNotifier:
    """
    Thin wrapper around the Discord Incoming Webhook API.

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
    ) -> None:
        self._send(
            color=_COLOR["ce_entry"],
            title="📈 CE Entry Signal",
            fields=[
                ("Symbol",   symbol,            True),
                ("Strike",   str(strike),        True),
                ("Spot",     f"₹{spot:,.2f}",   True),
                ("Qty",      str(qty),           True),
                ("RSI",      f"{rsi:.2f}",       True),
                ("Upper BB", f"₹{upper_bb:,.2f}", True),
                ("R1",       f"₹{r1:,.2f}",      True),
                ("Time",     _fmt_time(candle_time), True),
            ],
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
    ) -> None:
        self._send(
            color=_COLOR["pe_entry"],
            title="📉 PE Entry Signal",
            fields=[
                ("Symbol",   symbol,             True),
                ("Strike",   str(strike),         True),
                ("Spot",     f"₹{spot:,.2f}",    True),
                ("Qty",      str(qty),            True),
                ("RSI",      f"{rsi:.2f}",        True),
                ("Lower BB", f"₹{lower_bb:,.2f}", True),
                ("S1",       f"₹{s1:,.2f}",       True),
                ("Time",     _fmt_time(candle_time), True),
            ],
        )

    def exit_signal(
        self,
        symbol: str,
        reason: str,
        entry_price: float | None,
        exit_price: float,
        candle_time: datetime,
    ) -> None:
        pnl_str = "N/A"
        if entry_price is not None:
            pnl = (exit_price - entry_price) * 65
            pnl_str = f"₹{pnl:+,.2f} (est.)"

        color = _COLOR["eod_exit"] if "EOD" in reason else _COLOR["st_exit"]
        self._send(
            color=color,
            title=f"🚪 Exit — {reason}",
            fields=[
                ("Symbol",     symbol,                  True),
                ("Exit Price", f"₹{exit_price:,.2f}",  True),
                ("Est. P&L",   pnl_str,                 True),
                ("Reason",     reason,                   True),
                ("Time",       _fmt_time(candle_time),  True),
            ],
        )

    def error(self, message: str) -> None:
        self._send(
            color=_COLOR["error"],
            title="🚨 Error",
            fields=[("Detail", message, False)],
        )

    def info(self, message: str) -> None:
        self._send(
            color=_COLOR["info"],
            title="ℹ️ Info",
            fields=[("Message", message, False)],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, color: int, title: str, fields: list[tuple[str, str, bool]]) -> None:
        mode   = "🟢 LIVE" if self._live else "🔵 SIMULATION"
        now    = datetime.now(tz=IST).strftime("%d %b %Y %H:%M:%S IST")
        embed: dict[str, Any] = {
            "title":  title,
            "color":  color,
            "fields": [{"name": n, "value": v, "inline": inl} for n, v, inl in fields],
            "footer": {"text": f"Nifty Strike Engine • {mode} • {now}"},
        }
        payload = {"embeds": [embed]}
        try:
            resp = requests.post(self._url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.debug("Discord notification sent: %s", title)
        except requests.RequestException as exc:
            logger.error("Discord notification failed: %s", exc)


def _fmt_time(ts: datetime) -> str:
    return ts.astimezone(IST).strftime("%H:%M:%S IST")

