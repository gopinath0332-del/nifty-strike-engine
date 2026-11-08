#!/bin/bash
# ==============================================================================
# stop.sh
# Stop script for Nifty Strike Engine on Raspberry Pi / Linux
# ==============================================================================

SERVICE_NAME="nifty-strike-engine"
TIMER_NAME="nifty-auto-login.timer"

echo "=========================================================="
echo "🛑 Stopping Nifty Strike Engine Services..."
echo "=========================================================="

# 1. Stop main trading engine service
if systemctl list-unit-files | grep -q "^$SERVICE_NAME.service"; then
    echo "Stopping $SERVICE_NAME..."
    sudo systemctl stop $SERVICE_NAME
    echo "✅ $SERVICE_NAME stopped."
else
    echo "WARNING: $SERVICE_NAME service not found in systemd."
fi

# 2. Check current status
sleep 1
echo ""
echo "Current Service Status:"
sudo systemctl status $SERVICE_NAME --no-pager || true

# 3. Show morning timer status
echo ""
echo "Auto-Login Timer Status (Runs 08:50 AM IST weekdays):"
sudo systemctl list-timers $TIMER_NAME --no-pager || true

echo "=========================================================="
echo "To restart: ./restart.sh or sudo systemctl start $SERVICE_NAME"
echo "=========================================================="

