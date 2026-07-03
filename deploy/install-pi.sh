#!/usr/bin/env bash
# HouseSpotter native install for Raspberry Pi OS (Bookworm, arm64).
# - No Docker. No changes to Tailscale or any other service.
# - Installs to /opt/housespotter, runs as its own user on port 8410.
# Run from the repo root: sudo bash deploy/install-pi.sh
set -euo pipefail

APP_DIR=/opt/housespotter
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Checking system packages (only installing what's missing)"
MISSING=""
dpkg -s python3-venv &>/dev/null || MISSING="$MISSING python3-venv python3-pip"
command -v sqlite3 &>/dev/null || MISSING="$MISSING sqlite3"
# Node is only needed to build the frontend; skip if node exists or dist is pre-built
if [ ! -f "$REPO_DIR/frontend/dist/index.html" ] && ! command -v node &>/dev/null; then
  MISSING="$MISSING nodejs npm"
fi
if [ -n "$MISSING" ]; then
  echo "    installing:$MISSING"
  apt-get update -qq
  apt-get install -y -qq $MISSING
else
  echo "    all present — no apt changes"
fi

echo "==> Creating service user + directories"
id -u housespotter &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin housespotter
mkdir -p "$APP_DIR"

echo "==> Copying application"
rsync -a --delete \
  --exclude '.venv' --exclude 'node_modules' --exclude 'data' --exclude '.git' \
  "$REPO_DIR/" "$APP_DIR/"
mkdir -p "$APP_DIR/data" "$APP_DIR/backups"

echo "==> Python venv + dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/backend/requirements.txt"

if [ -f "$APP_DIR/frontend/dist/index.html" ]; then
  echo "==> Frontend already built — skipping npm build"
else
  echo "==> Building frontend"
  cd "$APP_DIR/frontend"
  npm ci --silent 2>/dev/null || npm install --silent
  npm run build
fi

echo "==> Environment file"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  SECRET=$("$APP_DIR/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(32))")
  sed -i "s|^HS_SESSION_SECRET=.*|HS_SESSION_SECRET=$SECRET|" "$APP_DIR/.env"
  echo ""
  echo "  !! Edit $APP_DIR/.env before starting:"
  echo "     - HS_PASSWORD (login password)"
  echo "     - HS_ANTHROPIC_API_KEY (AI features)"
  echo "     - HS_TELEGRAM_BOT_TOKEN / HS_TELEGRAM_CHAT_ID (alerts)"
  echo ""
fi

chown -R housespotter:housespotter "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> systemd service"
cp "$APP_DIR/deploy/housespotter.service" /etc/systemd/system/housespotter.service
systemctl daemon-reload
systemctl enable housespotter

echo "==> Nightly backup (cron)"
chmod +x "$APP_DIR/deploy/backup.sh"
cat > /etc/cron.d/housespotter-backup <<EOF
30 2 * * * housespotter /opt/housespotter/deploy/backup.sh >> /opt/housespotter/backups/backup.log 2>&1
EOF

echo ""
echo "Done. Next steps:"
echo "  1. sudo nano $APP_DIR/.env       # set password + API keys"
echo "  2. sudo systemctl start housespotter"
echo "  3. Open http://<pi-tailscale-name>:8410 from any device on your tailnet"
echo ""
echo "Nothing in this install touched Tailscale or any other running service."
