# nansen

Cache-first research harness for testing whether Nansen Smart-Money accumulation predicts forward returns, especially whether accumulation **before** price movement outperforms buying after a large move is already underway.

## Install from GitHub on Zorin

Use the repository rather than a ZIP:

```bash
cd ~/Téléchargements
DEST="$HOME/Téléchargements/nansen-signal-lab"
if [ -e "$DEST" ]; then
  mv "$DEST" "${DEST}.failed-$(date +%Y%m%d-%H%M%S)"
fi
git clone https://github.com/u-dont-existDOTcom/nansen.git "$DEST"
cd "$DEST"
bash bootstrap.sh
```

The bootstrap:

1. creates a local Python virtual environment;
2. installs pinned-compatible dependencies;
3. asks for the Nansen API key with hidden terminal input;
4. stores the key only in `.env` with restrictive permissions;
5. runs the test suite;
6. performs one cached Token Screener smoke call.

`.env`, API caches, result files, virtual environments and Python bytecode are excluded from Git.

## Commands

```bash
./nansen-lab smoke
./nansen-lab plan --tokens 10
./nansen-lab candidates
./nansen-lab flows --chain solana --token TOKEN_ADDRESS --days 7
```

Identical requests are cached under `data/cache/`. Use `--refresh` only when you deliberately want another paid/current API call.

## Research architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The initial experiment compares early Smart-Money accumulation with momentum accumulation and eventually an eligible-token control cohort, while explicitly avoiding look-ahead and survivorship bias.

This repository is the canonical durable checkpoint for the workflow.

## Committed research evidence

The first public evidence bundle is the [`2026-08-16 seven-token pilot`](research/experiments/2026-08-16-seven-token-pilot/REPORT.md). Reproduce its deterministic analysis with:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
```

`results/` is ignored scratch space for exploratory outputs. Evidence becomes durable only after it is copied byte-for-byte into `research/experiments/`, checksummed in a manifest, documented, and committed. The append-only [`research ledger`](docs/RESEARCH-LEDGER.md) and [`evidence graph`](docs/RESEARCH-GRAPH.md) preserve the experiment's claims, limitations, and follow-up state.

[Powered by Nansen API](https://nansen.ai/). Public Token Screener and `tgm/flows` evidence is included under Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide).
