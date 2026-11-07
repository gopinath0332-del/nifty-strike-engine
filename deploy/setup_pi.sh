#!/usr/bin/env bash
# ==============================================================================
# deploy/setup_pi.sh
# Production Provisioning & Deployment Script for Raspberry Pi 5 (running as root)
# ==============================================================================

set -euo pipefail

echo "=========================================================="
echo "🚀 Initializing Nifty Strike Engine Production Deployment"
echo "=========================================================="

# Check root execution
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root. Please run: sudo ./deploy/setup_pi.sh" 
   exit 1
fi

APP_DIR="/root/nifty-strike-engine"
VENV_DIR="/root/venv"

echo "📦 Step 1: Updating system packages and installing OS dependencies..."
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git curl libasound2 libatk-bridge2.0-0 libdrm2 libgbm1

echo "🐍 Step 2: Setting up Python virtual environment in ${VENV_DIR}..."
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "🌐 Step 3: Installing Playwright Headless Chromium & Browser Drivers..."
"${VENV_DIR}/bin/playwright" install chromium
"${VENV_DIR}/bin/playwright" install-deps chromium || true

echo "📁 Step 4: Ensuring required directories exist..."
mkdir -p "${APP_DIR}/logs" "${APP_DIR}/data" "${APP_DIR}/config"

echo "⚙️ Step 5: Checking configuration files..."
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "⚠️ Warning: ${APP_DIR}/.env not found!"
    if [ -f "${APP_DIR}/.env.example" ]; then
        cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
        echo "ℹ️ Created template ${APP_DIR}/.env from .env.example. Please populate your API keys & TOTP secret."
    fi
fi

if [ ! -f "${APP_DIR}/config/serviceAccountKey.json" ]; then
    echo "ℹ️ Note: config/serviceAccountKey.json is not present. If you want Firestore trade journaling, please place your Firebase admin key there."
fi

echo "🔧 Step 6: Installing systemd services & timer..."
cp "${APP_DIR}/deploy/nifty-strike-engine.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/nifty-auto-login.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/nifty-auto-login.timer" /etc/systemd/system/

systemctl daemon-reload

echo "⏱️ Step 7: Enabling morning auto-login timer (08:50 AM IST weekdays)..."
systemctl enable --now nifty-auto-login.timer
systemctl enable nifty-strike-engine.service

echo "=========================================================="
echo "✅ Nifty Strike Engine Provisioning Complete!"
echo "=========================================================="
echo "Useful Commands:"
echo "  • Test Auto-Login Now:    /root/venv/bin/python ${APP_DIR}/scripts/auto_login.py"
echo "  • Check Timer Status:     systemctl list-timers nifty-auto-login.timer"
echo "  • Start Engine Manually:  systemctl start nifty-strike-engine"
echo "  • Live Engine Logs:       journalctl -u nifty-strike-engine -f"
echo "  • Engine Service Status:  systemctl status nifty-strike-engine"
echo "=========================================================="
