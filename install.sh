#!/usr/bin/env bash
# ── install.sh ─────────────────────────────────────────────────────────────
# One-shot setup for learn-portal.
#
#   1. Installs Python dependencies (via uv or pip)
#   2. Creates a systemd user service on port 6666
#   3. Enables and starts the service
#
# Usage:
#   chmod +x install.sh && ./install.sh
#
# After running, open http://YOUR_SERVER_IP:6666 in your browser.
# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Find uv or fall back to pip ────────────────────────────────────────────
UV=""
if command -v uv &>/dev/null; then
    UV="uv"
elif [ -x "$HOME/.hermes/bin/uv" ]; then
    UV="$HOME/.hermes/bin/uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
fi

info()  { echo -e "\\033[1;34m•\\033[0m $*"; }
ok()    { echo -e "\\033[1;32m✓\\033[0m $*"; }
err()   { echo -e "\\033[1;31m✗\\033[0m $*" >&2; }

# ── Step 1: Install Python dependencies ────────────────────────────────────
info "Installing Python dependencies…"

if [ -n "$UV" ]; then
    info "Using \`$UV\` (recommended)"
    "$UV" pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
else
    info "Using \`pip\` (consider installing uv for faster installs)"
    pip3 install --quiet --user -r "$SCRIPT_DIR/requirements.txt"
fi
ok "Dependencies installed"

# ── Step 2: Create systemd user service ────────────────────────────────────
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/learn-portal.service"

# Resolve absolute path for the uv/python binary to bake into the unit file.
if [ -n "$UV" ]; then
    EXEC_CMD="$UV run uvicorn app:app --host 0.0.0.0 --port 6666"
else
    EXEC_CMD="uvicorn app:app --host 0.0.0.0 --port 6666"
fi

info "Creating systemd user service…"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Learn Portal — Teaching Workspace Viewer
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${EXEC_CMD}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

ok "Service written to ${SERVICE_FILE}"

# ── Step 3: Enable & start ─────────────────────────────────────────────────
info "Enabling and starting service…"
systemctl --user daemon-reload
systemctl --user enable --now learn-portal.service

# Small wait to catch early failure
sleep 2

if systemctl --user is-active --quiet learn-portal.service; then
    ok "learn-portal.service is running"
else
    err "Service failed to start — check logs:"
    err "  journalctl --user -u learn-portal.service --no-pager -n 30"
    exit 1
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "\\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m"
echo -e "\\033[1;32m  ✅  Learn Portal is live!\\033[0m"
echo -e "\\033[1;32m  📍  http://localhost:6666\\033[0m"
echo -e "\\033[1;32m  📍  http://$(hostname -I 2>/dev/null | awk '{print $1}'):6666\\033[0m"
echo -e "\\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m"
echo ""
echo "Manage:  systemctl --user [start|stop|restart|status] learn-portal.service"
echo "Logs:    journalctl --user -u learn-portal.service -f"
echo ""
