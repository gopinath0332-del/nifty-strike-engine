#!/bin/bash
# ==============================================================================
# restart.sh
# Restart script for Nifty Strike Engine on Raspberry Pi / Linux
# ==============================================================================

SERVICE_NAME="nifty-strike-engine"
TIMER_NAME="nifty-auto-login.timer"

echo "=========================================================="
echo "🔄 Restarting Nifty Strike Engine..."
echo "=========================================================="

# 1. Restart main trading engine
if systemctl list-unit-files | grep -q "^$SERVICE_NAME.service"; then
    echo "Restarting $SERVICE_NAME..."
    sudo systemctl restart $SERVICE_NAME
else
    echo "WARNING: $SERVICE_NAME service not found in systemd, skipping."
fi

# 2. Ensure auto-login timer is active
if systemctl list-unit-files | grep -q "^$TIMER_NAME"; then
    echo "Ensuring $TIMER_NAME is enabled and running..."
    sudo systemctl enable --now $TIMER_NAME 2>/dev/null || true
fi

# 3. Wait a moment for process initialization
sleep 2

# 4. Check service status
echo ""
echo "Service Status:"
sudo systemctl status $SERVICE_NAME --no-pager || true

# 5. Show recent logs
echo ""
echo "Recent Logs:"
sudo journalctl -u $SERVICE_NAME -n 15 --no-pager

echo "=========================================================="

