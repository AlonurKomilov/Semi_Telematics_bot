#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
#  Install 4truck Bot as a systemd service
#  (auto-start on boot + auto-restart on crash)
# ─────────────────────────────────────────────────────
set -euo pipefail

SERVICE_NAME="4truck-bot"
SERVICE_FILE="${SERVICE_NAME}.service"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -f "${PROJECT_DIR}/${SERVICE_FILE}" ]]; then
    echo "❌ ${SERVICE_FILE} not found in ${PROJECT_DIR}"
    exit 1
fi

echo "📋 Installing ${SERVICE_NAME} systemd service..."

# Stop the old nohup-based process if running
if [[ -f "${PROJECT_DIR}/.bot.pid" ]]; then
    OLD_PID=$(cat "${PROJECT_DIR}/.bot.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "🛑 Stopping old bot process (PID ${OLD_PID})..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
    rm -f "${PROJECT_DIR}/.bot.pid"
fi

# Copy service file and enable
sudo cp "${PROJECT_DIR}/${SERVICE_FILE}" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl start "${SERVICE_NAME}"

echo ""
echo "✅ Service installed and started!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ${SERVICE_NAME}   — check status"
echo "  sudo systemctl restart ${SERVICE_NAME}  — restart bot"
echo "  sudo systemctl stop ${SERVICE_NAME}     — stop bot"
echo "  sudo journalctl -u ${SERVICE_NAME} -f   — follow logs"
echo "  make status                              — quick status check"
echo "  make logs                                — follow logs"
