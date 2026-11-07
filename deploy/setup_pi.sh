#!/bin/bash
# ==============================================================================
# deploy/setup_pi.sh
# Production Provisioning & Deployment Script for Raspberry Pi 5
# Aligned with Pi standards: User pi, /home/pi/nifty-strike-engine
# ==============================================================================

set -e

PROJECT_DIR="/home/pi/nifty-strike-engine"
SERVICE_NAME="nifty-strike-engine"
VENV_DIR="${PROJECT_DIR}/venv"

echo "=========================================================="
echo "🚀 Initializing Nifty Strike Engine Production Deployment"
echo "=========================================================="

# 1. Update and install base OS dependencies
echo "📦 Step 1: Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git curl \
    libasound2 libatk-bridge2.0-0 libdrm2 libgbm1 libxkbcommon0 libnss3 libnspr4

# 2. Setup project directory
if [ ! -d "$PROJECT_DIR" ]; then
    echo "📁 Step 2: Creating directory $PROJECT_DIR..."
    sudo mkdir -p "$PROJECT_DIR"
    sudo chown -R pi:pi "$PROJECT_DIR"
fi

# 3. Setup Python virtual environment
echo "🐍 Step 3: Setting up Python virtual environment in ${VENV_DIR}..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

# Upgrade pip
"$VENV_DIR/bin/pip" install --upgrade pip

# 4. Copy project files if running from an outside staging directory
if [ "$(pwd)" != "$PROJECT_DIR" ]; then
    echo "📂 Step 4: Syncing application files into $PROJECT_DIR..."
    cp -R config deploy pine scripts src tests main.py requirements.txt "$PROJECT_DIR/" 2>/dev/null || true
    if [ -f .env ]; then
        cp .env "$PROJECT_DIR/.env"
    fi
else
    echo "📂 Step 4: Already inside target directory ($PROJECT_DIR)."
fi

# 5. Install Python dependencies
echo "📦 Step 5: Installing Python requirements..."
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# 6. Install Playwright browser binaries and OS browser dependencies
echo "🌐 Step 6: Installing Playwright Headless Chromium..."
"$VENV_DIR/bin/playwright" install chromium
sudo "$VENV_DIR/bin/playwright" install-deps chromium 2>/dev/null || true

# 7. Ensure runtime directories exist
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data" "$PROJECT_DIR/config"
sudo chown -R pi:pi "$PROJECT_DIR"

# 8. Check configuration files
echo "⚙️ Step 8: Checking configuration files..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "⚠️ Warning: $PROJECT_DIR/.env not found!"
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo "ℹ️ Created template $PROJECT_DIR/.env from .env.example. Please populate your API keys & TOTP secret."
    fi
fi

if [ ! -f "$PROJECT_DIR/config/serviceAccountKey.json" ]; then
    echo "ℹ️ Note: config/serviceAccountKey.json is not present. If you want Firestore trade journaling, please place your Firebase admin key there."
fi

# 9. Configure and reload Systemd services & timer
echo "🔧 Step 9: Configuring systemd services..."
sudo cp "$PROJECT_DIR/deploy/nifty-strike-engine.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/deploy/nifty-auto-login.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/deploy/nifty-auto-login.timer" /etc/systemd/system/

sudo systemctl daemon-reload

# 10. Enable timer and service
echo "⏱️ Step 10: Enabling auto-login timer (08:50 AM IST weekdays) and main engine..."
sudo systemctl enable --now nifty-auto-login.timer
sudo systemctl enable nifty-strike-engine.service

echo "=========================================================="
echo "✅ Nifty Strike Engine Deployment Complete!"
echo "=========================================================="
echo "Next steps & Useful commands:"
echo "  1. Test Auto-Login Now:    $VENV_DIR/bin/python $PROJECT_DIR/scripts/auto_login.py"
echo "  2. Check Timer Status:     sudo systemctl list-timers nifty-auto-login.timer"
echo "  3. Start Engine Manually:  sudo systemctl start nifty-strike-engine"
echo "  4. Live Engine Logs:       journalctl -u nifty-strike-engine -f"
echo "  5. Engine Service Status:  sudo systemctl status nifty-strike-engine"
echo "=========================================================="
