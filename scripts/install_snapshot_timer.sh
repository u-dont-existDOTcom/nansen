#!/usr/bin/env bash
set -euo pipefail

REPO="${NANSEN_REPO:-$HOME/Téléchargements/nansen-signal-lab}"
UNIT_DIR="$HOME/.config/systemd/user"
SERVICE="$UNIT_DIR/nansen-smart-money-snapshot.service"
TIMER="$UNIT_DIR/nansen-smart-money-snapshot.timer"

if [ ! -d "$REPO/.git" ]; then
  echo "ERROR: Nansen repo not found at $REPO" >&2
  exit 1
fi
if [ ! -f "$REPO/.env" ]; then
  echo "ERROR: $REPO/.env is missing; configure NANSEN_API_KEY first." >&2
  exit 1
fi
if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "ERROR: repo virtualenv is missing at $REPO/.venv" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"

cat > "$SERVICE" <<'EOF'
[Unit]
Description=Refresh sanitized Nansen Smart Money snapshot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'cd "$HOME/Téléchargements/nansen-signal-lab" && exec /bin/bash scripts/run_snapshot_cycle.sh'
EOF

cat > "$TIMER" <<'EOF'
[Unit]
Description=Refresh Nansen Smart Money snapshot every 6 hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:05:00
Persistent=true
RandomizedDelaySec=5m
Unit=nansen-smart-money-snapshot.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now nansen-smart-money-snapshot.timer

echo "Installed and enabled: nansen-smart-money-snapshot.timer"
systemctl --user list-timers nansen-smart-money-snapshot.timer --no-pager
