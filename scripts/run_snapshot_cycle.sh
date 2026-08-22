#!/usr/bin/env bash
set -euo pipefail

REPO="${NANSEN_REPO:-$HOME/Téléchargements/nansen-signal-lab}"
cd "$REPO"

# Keep the dedicated worker checkout aligned with canonical main without rewriting history.
git fetch origin main
git merge --ff-only origin/main

# Scheduled cycles must be genuinely current, so deliberately bypass request cache.
# Market-wide spot screen: 4 requests. Tracked NEAR perp: 2 requests (24h + 7d).
PYTHONPATH=src .venv/bin/python scripts/publish_smart_money_snapshot.py --refresh --push
PYTHONPATH=src .venv/bin/python scripts/publish_perp_signal.py NEAR --refresh --push
