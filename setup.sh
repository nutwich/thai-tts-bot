#!/usr/bin/env bash
# Linux (Ubuntu/Debian) setup for the Thai TTS bot, e.g. an Oracle Cloud Always Free VM.
#   First time : bash setup.sh   -> asks you to put the token in .env, then run it again
#   Update     : git pull && bash setup.sh
# Installs packages, then runs the bot as a systemd service that starts on boot
# and restarts itself if it crashes. Safe to run as many times as you like.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
DIR="$(pwd)"
SERVICE=thai-tts-bot
RUN_USER="$(id -un)"

echo "==> Installing system packages (Python, Opus voice library)..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-venv python3-pip libopus0 >/dev/null

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Python 3.10+ is needed (found $(python3 --version)). Use Ubuntu 24.04."
    exit 1
fi

echo "==> Installing bot packages..."
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo
    echo "============================================================"
    echo " Put your bot token in .env:"
    echo "   nano .env"
    echo " Paste it after DISCORD_TOKEN= (right-click pastes in most"
    echo " terminals), save with Ctrl+O then Enter, exit with Ctrl+X,"
    echo " then run:  bash setup.sh"
    echo "============================================================"
    exit 0
fi
chmod 600 .env
if ! grep -qE '^DISCORD_TOKEN=[^[:space:]]' .env; then
    echo "DISCORD_TOKEN in .env is empty. Edit it with: nano .env"
    exit 1
fi

echo "==> Installing the service (starts on boot, restarts on crash)..."
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=Thai TTS Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
ExecStart="$DIR/.venv/bin/python" "$DIR/bot.py"
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE >/dev/null 2>&1
sudo systemctl restart $SERVICE

sleep 5
echo
sudo journalctl -u $SERVICE -n 10 --no-pager -o cat || true
echo
echo "Bot is running. Useful commands:"
echo "  live log : sudo journalctl -u $SERVICE -f      (Ctrl+C to exit)"
echo "  restart  : sudo systemctl restart $SERVICE"
echo "  stop     : sudo systemctl stop $SERVICE"
echo "  update   : git pull && bash setup.sh"
