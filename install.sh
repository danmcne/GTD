#!/bin/bash
# GTD App — installer for Ubuntu 24
# Installs to ~/gtd, sets up a systemd user service, opens in browser.

set -e
DEST="$HOME/gtd"
PORT=5000

echo "=== GTD App Installer ==="

# 1. Copy app files
echo "→ Installing to $DEST …"
mkdir -p "$DEST/static" "$DEST/data/journal" "$DEST/data/notes"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/app.py"           "$DEST/app.py"
cp "$SCRIPT_DIR/static/index.html" "$DEST/static/index.html"

# 2. Install Python deps
echo "→ Installing Python dependencies…"
pip install flask --break-system-packages -q

# 3. Create systemd user service
echo "→ Setting up systemd service…"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/gtd.service" << EOF
[Unit]
Description=GTD Personal Productivity App
After=network.target

[Service]
Type=simple
WorkingDirectory=$DEST
ExecStart=$(which python3) $DEST/app.py
Restart=on-failure
Environment=HOME=$HOME

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable gtd.service
systemctl --user start gtd.service

sleep 1

# 4. Open in browser
echo "→ Opening in browser…"
xdg-open "http://localhost:$PORT" 2>/dev/null || \
  firefox "http://localhost:$PORT" 2>/dev/null || \
  echo "Open http://localhost:$PORT in your browser."

echo ""
echo "✓ GTD App is running at http://localhost:$PORT"
echo ""
echo "Useful commands:"
echo "  systemctl --user status gtd   # check if running"
echo "  systemctl --user stop gtd     # stop"
echo "  systemctl --user restart gtd  # restart after update"
echo "  journalctl --user -u gtd -f   # view logs"
echo ""
echo "All data lives in $DEST/data/ — back it up freely."
