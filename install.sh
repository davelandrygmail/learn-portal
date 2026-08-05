#!/usr/bin/env bash
# ── install.sh ─────────────────────────────────────────────────────────────
# One-shot setup for learn-portal.
#
#   1. Installs Python dependencies (via uv or pip)
#   2. Creates a systemd user service on port 7777
#   3. Enables and starts the service
#
# Usage:
#   chmod +x install.sh && ./install.sh
#
# After running, open http://YOUR_SERVER_IP:7777 in your browser.
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

# ── Step 1: Create a venv and install Python dependencies ─────────────────
# A dedicated .venv is created up front so the systemd unit (which execs
# .venv/bin/uvicorn) is guaranteed a real binary to point at, regardless of
# what env `pip`/`uv` would otherwise install into.
info "Creating virtual environment (.venv)…"

VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    if [ -n "$UV" ]; then
        "$UV" venv --quiet "$VENV_DIR"
    elif command -v python3 &>/dev/null; then
        python3 -m venv "$VENV_DIR"
    else
        err "No uv or python3 available to create a virtual environment."
        exit 1
    fi
else
    info "Existing .venv detected (reusing)."
fi
ok "Virtual environment ready"

info "Installing Python dependencies…"

if [ -n "$UV" ]; then
    # uv venv creates a pip-less env; install into it via uv pip directly.
    "$UV" pip install --quiet --python "$VENV_DIR/bin/python" \
        -r "$SCRIPT_DIR/requirements.txt"
elif [ -x "$VENV_DIR/bin/python" ]; then
    # python3 -m venv bundles pip; install into the venv explicitly.
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
else
    err "No venv python binary found; aborting."
    exit 1
fi
ok "Dependencies installed"

# ── Step 2: Create systemd user service ────────────────────────────────────
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/learn-portal.service"

# Resolve absolute path for the venv uvicorn binary to bake into the unit file.
# Pointing straight at the venv binary avoids `uv run` project-discovery
# fragility when the portal has no pyproject.toml (requirements.txt only).
EXEC_CMD="$SCRIPT_DIR/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 7777"

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
echo -e "\\033[1;32m  📍  http://localhost:7777\\033[0m"
echo -e "\\033[1;32m  📍  http://$(hostname -I 2>/dev/null | awk '{print $1}'):7777\\033[0m"
echo -e "\\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m"
echo ""
echo "Manage:  systemctl --user [start|stop|restart|status] learn-portal.service"
echo "Logs:    journalctl --user -u learn-portal.service -f"
echo ""
