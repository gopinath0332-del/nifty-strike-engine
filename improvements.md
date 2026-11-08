Based on a comprehensive review of the entire codebase ([`src/`](file:///d:/Workspace/nifty-strike-engine/src), [`scripts/`](file:///d:/Workspace/nifty-strike-engine/scripts), [`config/`](file:///d:/Workspace/nifty-strike-engine/config), and deployment setup), here is a prioritized roadmap of improvements categorized by impact:

---

### 1. High Priority: Trading Execution & Reliability

#### A. Pre-Seed Historical Candles on Morning Startup

- **Current Issue**: The engine initializes with an empty `_candle_buffer` and requires `MIN_CANDLES = 25` closed candles before indicators become valid. On a 5-minute timeframe, **25 candles = 125 minutes (2 hours 5 minutes)**. The bot would be blind until ~11:20 AM, missing prime morning breakout entries (like the 09:50 AM entry on Nov 6).
- **Improvement**: At 09:14 AM startup, call `kite.historical_data()` for the NIFTY 50 token (`256265`) to fetch the previous 30 closed 5-minute candles from the preceding trading day.
- **Benefit**: Indicators (Supertrend, RSI, BB, Pivots) are fully warmed up from 09:15 AM, enabling the bot to evaluate signals on the very first 09:20 AM candle.

#### B. Position State Persistence & Crash Recovery

- **Current Issue**: `OrderManager` and `SignalEngine` store `_open_symbol`, `_entry_price`, and `_trade_today` only in RAM. If the Raspberry Pi experiences a power blip, Wi-Fi reconnection, or systemd restart while a position is open:
  1. It forgets it has an open position (leaving an unmanaged naked option on Zerodha with no trailing stop or 15:00 EOD exit).
  2. It resets `_trade_today = 0`, potentially triggering a second trade on the same day.
- **Improvement**:
  - Save active state to a local JSON file (`data/active_position.json`) on entry and remove it on exit.
  - On startup, reconcile with `kite.positions()["net"]` to re-adopt any open NFO intraday position.

#### C. Account Margin Pre-Check

- **Current Issue**: Buying 1 lot of NIFTY options requires ~₹15,000–₹25,000 in cash margin. If funds are insufficient, Kite rejects the order with an API error.
- **Improvement**: Before placing an order, query `kite.margins("equity")` to verify `available.live_balance >= required_margin`. If insufficient, send an immediate Discord alert (`❌ Order aborted: Insufficient margin`).

---

### 2. High Priority: Market Infrastructure & Calendar

#### A. NSE Official Trading Holidays Calendar

- **Current Issue**: The bot bypasses weekends (Saturday/Sunday), but Indian exchanges also close on ~15 national and festival holidays per year (e.g., Diwali, Republic Day, Christmas, Eid, Mahashivratri). On those days, the morning timer would still attempt to trade on a closed exchange.
- **Improvement**: Add an NSE holiday list (either hardcoded in `config/settings.yml` for the current calendar year or dynamically fetched). If today is an exchange holiday, enter standby mode just like on weekends.

#### B. Asynchronous Discord Notifications

- **Current Issue**: [`DiscordNotifier`](file:///d:/Workspace/nifty-strike-engine/src/discord_notifier.py) makes synchronous HTTP calls: `requests.post(..., timeout=8)`. If Discord's API has latency or network packet loss, this stalls the calling thread for up to 8 seconds.
- **Improvement**: Offload webhook delivery to a background queue (`queue.Queue` with a daemon worker thread) so order fills and tick aggregation are never delayed by external HTTP calls.

---

### 3. Medium Priority: Observability & Monitoring

#### A. Daily Morning "System Armed" Briefing (09:14 IST)

- Send a morning status embed to Discord right before market open:
  - Kite token validity & authenticated user ID.
  - Available account cash balance & margin.
  - Previous day close and calculated Pivot levels (R1, S1, Pivot).
  - Confirmation that 5-minute candle builder is armed.

#### B. Daily EOD Summary (15:30 IST)

- On quiet days when no trade conditions are triggered, send a clean EOD summary:
  ```text
  📊 Nifty Strike Engine — EOD Session Summary
  • Date: 2026-11-09
  • Market Status: Closed (15:30 IST)
  • Signals Evaluated: 75 candles
  • Trades Taken: 0 (No breakout met all 4 filter criteria)
  • NIFTY Day Range: 24,520 - 24,780
  • Bot Status: Entering standby until tomorrow 08:50 AM IST
  ```
  This gives you peace of mind that the bot was running properly all day without having to check server logs.

#### C. MicroSD Card Log Rotation on the Pi

- **Current Issue**: `logs/app.log` and `logs/signals.log` grow indefinitely over months.
- **Improvement**: Ensure [`src/logger.py`](file:///d:/Workspace/nifty-strike-engine/src/logger.py) uses `logging.handlers.RotatingFileHandler` with `maxBytes=10*1024*1024` (10 MB) and `backupCount=5` to prevent filling up the Pi's storage.

---

### 4. Strategy & Analytics Enhancements

#### A. Option Greek & Volatility Logging (IV & Delta)

- At entry time, record the contract's Implied Volatility (IV) and Delta. Over time, this data helps analyze whether trades taken in high-IV environments suffer from theta crush compared to low-IV expansions.

#### B. Slippage & Order Type Control

- Currently, orders use `ORDER_TYPE_MARKET`. While market orders guarantee fill speed, sudden illiquid order books can cause slippage. You could introduce a configurable `ORDER_TYPE_LIMIT` with marketable buffer (e.g., `LTP + 0.5%`) or place an exchange-level Stop Loss Market (`SL-M`) order for hard risk capping.

#### C. Automated Daily HTML Report Sync

- Automate `scripts/generate_html_report.py` to run daily at 15:35 IST via systemd, outputting to a web directory or syncing to a private dashboard so you can view your equity curve and win-rate analytics from any mobile device.

---

### Suggested Priority Order

| Priority | Improvement                                     | Effort  | Impact                                                    |
| :------- | :---------------------------------------------- | :------ | :-------------------------------------------------------- |
| **P0**   | **Historical Candle Pre-Seeding on Startup**    | 30 mins | Prevents missing the first 2 hours of trades each morning |
| **P0**   | **Position State Persistence (Crash Recovery)** | 45 mins | Protects against power/network drop leaving open trades   |
| **P1**   | **NSE Holiday Calendar Check**                  | 20 mins | Prevents unnecessary runs on exchange holidays            |
| **P1**   | **Morning & EOD Discord Heartbeat Embeds**      | 25 mins | Complete peace-of-mind monitoring                         |
| **P2**   | **Account Margin Pre-Check**                    | 15 mins | Graceful warning if funds are low                         |
| **P2**   | **Async Discord Webhook Queue**                 | 20 mins | Zero-latency execution path                               |

Let me know which of these you would like to implement first!
