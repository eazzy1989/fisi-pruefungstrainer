#!/usr/bin/env bash
# Installiert den AP1-Trainer als systemd-Service für den aktuellen Benutzer.
# Aufruf im Bot-Ordner:  bash install.sh
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
SERVICE=ap1bot

if [ ! -f .env ]; then
  read -rsp "BOT_TOKEN vom @BotFather einfügen (Eingabe bleibt unsichtbar): " TOKEN
  echo
  echo "BOT_TOKEN=$TOKEN" > .env
  chmod 600 .env
fi

python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

sudo tee /etc/systemd/system/$SERVICE.service > /dev/null <<UNIT
[Unit]
Description=AP1 Telegram Trainer Bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/venv/bin/python $DIR/telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE.service
sleep 3
systemctl status $SERVICE.service --no-pager | head -n 8
echo
echo "Fertig. Logs: journalctl -u $SERVICE -f"
