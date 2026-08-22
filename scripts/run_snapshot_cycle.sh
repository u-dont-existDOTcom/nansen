#!/usr/bin/env bash
set -euo pipefail

REPO="${NANSEN_REPO:-$HOME/Téléchargements/nansen-signal-lab}"
cd "$REPO"

# Generated signal files may be modified by an interrupted prior cycle. Preserve
# only those outputs so they cannot block code updates; never stash source files.
git stash push -m "auto-signal-pre-sync-$(date -u +%Y%m%dT%H%M%SZ)" -- signals/ >/dev/null 2>&1 || true

# Keep the worker aligned with canonical main. Rebase is intentional here: if a
# prior signal commit was created locally but its push lost a race with a remote
# code commit, preserve that signal commit while replaying it above current main.
git fetch origin main
git rebase origin/main

# A scheduled cycle must be genuinely current, so deliberately bypass request cache.
PYTHONPATH=src .venv/bin/python scripts/publish_smart_money_snapshot.py --refresh --push

# Track NEAR specifically via Hyperliquid Smart Money perp positioning.
PYTHONPATH=src .venv/bin/python scripts/publish_perp_signal.py NEAR --refresh --push
