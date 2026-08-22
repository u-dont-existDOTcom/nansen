#!/usr/bin/env bash
set -euo pipefail

REPO="${NANSEN_REPO:-$HOME/Téléchargements/nansen-signal-lab}"
cd "$REPO"

# Keep the dedicated worker checkout aligned with canonical main without rewriting history.
git fetch origin main
git merge --ff-only origin/main

# A scheduled cycle must be genuinely current, so deliberately bypass request cache.
PYTHONPATH=src .venv/bin/python scripts/publish_smart_money_snapshot.py --refresh --push
