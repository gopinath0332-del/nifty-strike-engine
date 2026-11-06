# Nifty Strike Engine

Algorithmic trading engine for **NIFTY 50 Options Buying** on a **3-minute timeframe**, converted from the TradingView Pine Script strategy *NIFTY Options Buying Strategy v9*.

Built with **Python**, **Zerodha Kite Connect**, and designed to run headlessly on a **Raspberry Pi 5** production server.

---

## Strategy Summary

| Parameter | Value |
|---|---|
| **Instrument** | NIFTY 50 Index Options (NFO) |
| **Strategy Type** | Options Buying only (CE / PE) |
| **Timeframe** | 3-minute candles |
| **Trade Frequency** | **Maximum 1 trade per day** (CE **or** PE, never both) |
| **Contract** | Current-week expiry (rolls to next week if today is expiry day) |
| **Lot Size** | Fixed **65 units** (1 contract) |
| **Strike Selection** | ATM ± 1% of NIFTY spot price, rounded to nearest 50 |
| **Trailing Stop-Loss** | Supertrend flip (bear flip exits CE, bull flip exits PE) |
| **Hard EOD Exit** | **15:00 IST sharp** (all open positions force-closed) |
| **Entry Cutoff** | **14:45 IST** (no new entries after this time) |
| **Alerts** | Rich Discord webhook embeds for every event |

### Entry Conditions

```
# CE (Call) Entry
bull_trend AND rsi > 70 AND close > R1 AND close > upper_bb
AND no_open_position AND trades_today == 0 AND time < 14:45 IST

# PE (Put) Entry
bear_trend AND rsi < 30 AND close < S1 AND close < lower_bb
AND no_open_position AND trades_today == 0 AND time < 14:45 IST
```

---

## Project Structure

```
nifty-strike-engine/
├── .env.example                  # Environment template
├── .gitignore
├── main.py                       # Application entry point
├── requirements.txt              # Python dependencies
├── README.md
├── REQUIREMENT.MD                # Original user requirements
├── config/
│   ├── __init__.py
│   └── settings.py               # Pydantic settings & env validation
├── src/
│   ├── __init__.py
│   ├── candle_builder.py         # 3-min tick aggregator
│   ├── indicators.py             # Supertrend, RSI, BB, Traditional Pivots
│   ├── strike_selector.py        # 1% spot offset + expiry roll logic
│   ├── signal_engine.py          # State-machine signal evaluator
│   ├── order_manager.py          # Kite REST orders + LIVE gate
│   ├── discord_notifier.py       # Rich embed Discord notifications
│   ├── session_manager.py        # Top-level orchestrator & Kite WebSocket
│   └── kite_auth.py              # Token generation helpers
├── scripts/
│   ├── refresh_token.py          # Morning token refresh CLI
│   └── backfill_candles.py       # Historical candle downloader
├── tests/
│   ├── __init__.py
│   ├── test_strike_selector.py   # Strike rounding & roll tests
│   ├── test_signal_engine.py     # Signal logic & 1-trade/day tests
│   └── test_indicators.py        # Indicator correctness tests
├── deploy/
│   └── nifty-strike-engine.service # systemd unit for Raspberry Pi 5
└── pine/
    └── nifty-options-buying-strategy.pine  # Original reference script
```

---

## Quick Start (Local / Development)

### 1. Prerequisites
- Python 3.10+
- Active Zerodha Kite Connect developer account

### 2. Install dependencies
```bash
python -m venv venv
# Linux / macOS / Raspberry Pi:
source venv/bin/activate
# Windows:
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Configure `.env`
```bash
cp .env.example .env
```
Fill in your credentials:
```dotenv
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret
KITE_ACCESS_TOKEN=your_access_token   # see Token Refresh below
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
LIVE_ORDER_PLACEMENT=false            # start in SIMULATION mode
```

### 4. Run tests
```bash
python -m pytest tests/ -v
```

### 5. Morning Token Refresh Options

You have two choices for refreshing the daily token:

#### Option A: Zero-Touch Automated Login (Recommended)
Add your Zerodha credentials & TOTP secret to `.env`:
```dotenv
ZERODHA_USER_ID=your_client_id
ZERODHA_PASSWORD=your_password
ZERODHA_TOTP_SECRET=your_totp_secret_key
```
Then run:
```bash
python scripts/auto_login.py
```
This runs 100% headlessly, computes the 2FA code via `pyotp`, fetches the new token, writes it to `.env`, and sends a Discord alert.

#### Option B: Interactive Browser Refresh
```bash
python scripts/refresh_token.py
```
Follow the interactive prompt: open the URL, log in, paste the redirect URL.

### 6. Start the engine
```bash
python main.py
```

---

## Raspberry Pi 5 Production Deployment

The engine is designed to run headlessly as **`root`** via systemd.

### Step 1 — Setup on Pi
```bash
# Update packages
sudo apt update && sudo apt install -y python3-venv git

# Clone / copy project to /root/nifty-strike-engine
sudo git clone <your-repo> /root/nifty-strike-engine
cd /root/nifty-strike-engine

# Create virtualenv
sudo python3 -m venv /root/venv
sudo /root/venv/bin/pip install --upgrade pip
sudo /root/venv/bin/pip install -r requirements.txt

# Install Playwright Headless Chromium & system dependencies for Zero-Touch Morning Login
sudo /root/venv/bin/playwright install chromium
sudo /root/venv/bin/playwright install-deps chromium

# Configure .env
sudo cp .env.example .env
sudo nano .env   # fill in your credentials
```

### Step 2 — Install systemd service
```bash
sudo cp deploy/nifty-strike-engine.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nifty-strike-engine
```

### Step 3 — Service commands
```bash
# Start the bot
sudo systemctl start nifty-strike-engine

# Check status
sudo systemctl status nifty-strike-engine

# Live log stream
sudo journalctl -u nifty-strike-engine -f

# Stop the bot
sudo systemctl stop nifty-strike-engine
```

### Step 4 — Set Up Morning 08:50 AM Automated Login
Enable the systemd timer so the Raspberry Pi wakes up every weekday morning at 08:50 AM IST, logs in using `pyotp`, refreshes the token, restarts the engine service, and pings your Discord:

```bash
# Copy timer and service
sudo cp deploy/nifty-auto-login.service /etc/systemd/system/
sudo cp deploy/nifty-auto-login.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start the timer
sudo systemctl enable --now nifty-auto-login.timer

# Verify timer is scheduled
sudo systemctl list-timers nifty-auto-login.timer
```

Now you don't have to touch anything while playing badminton—the bot logs in and starts itself before market open!

---

## Safety Controls

| Control | Description |
|---|---|
| `LIVE_ORDER_PLACEMENT=false` | Default mode. All indicators, signals, and Discord alerts run in real-time, but **zero orders are sent to the exchange**. |
| `MAX_TRADES_PER_DAY=1` | Hard stop after **1 filled trade** (CE or PE). No re-entry allowed that day, even if conditions match again. |
| `ENTRY_CUTOFF_TIME=14:45` | No new trades entered after 14:45 IST to avoid late-day chop. |
| `EOD_EXIT_TIME=15:00` | Mandatory hard exit — all open positions force-closed at 15:00 IST sharp. |
| Expiry Auto-Roll | If today is Thursday (weekly expiry), automatically trades **next week's contract** to avoid last-hour expiry zeroing risk. |
