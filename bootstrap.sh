#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

need_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! need_cmd python3 || ! need_cmd git; then
  echo "Installing required system packages (python3, venv, git)..."
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip git
fi

if [[ ! -d .venv ]]; then
  if ! python3 -m venv .venv 2>/dev/null; then
    echo "python3-venv is missing; installing it..."
    sudo apt-get update
    sudo apt-get install -y python3-venv
    python3 -m venv .venv
  fi
fi

.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt >/dev/null

if [[ ! -f .env ]] || ! grep -q '^NANSEN_API_KEY=.' .env; then
  echo
  echo "Nansen API key required. Create/copy it from your Nansen account API page."
  read -r -s -p "Paste Nansen API key (input hidden): " NANSEN_API_KEY
  echo
  if [[ -z "${NANSEN_API_KEY}" ]]; then
    echo "No key entered; setup stopped before any API call." >&2
    exit 2
  fi
  umask 077
  printf 'NANSEN_API_KEY=%s\n' "$NANSEN_API_KEY" > .env
  chmod 600 .env
fi

# GitHub's Contents API does not preserve executable bits, so make local launchers executable.
chmod +x bootstrap.sh nansen-lab

.venv/bin/pytest -q

echo
echo "Running one cached/cheap Nansen Token Screener smoke call..."
./nansen-lab smoke

echo
echo "SETUP COMPLETE"
echo "Next: ./nansen-lab plan --tokens 10"
echo "Then: ./nansen-lab candidates"
