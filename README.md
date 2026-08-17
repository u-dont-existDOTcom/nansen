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
./nansen-lab flows --chain solana --token TOKEN_ADDRESS --label exchange --days 7
./nansen-lab who-bought-sold --chain solana --token TOKEN_ADDRESS --side BUY \
  --from 2026-08-16T00:00:00Z --to 2026-08-17T00:00:00Z
```

Identical requests are cached under `data/cache/`. Use `--refresh` only when you deliberately want another paid/current API call.
Flow responses written to the default ignored `results/` scratch path retain the historical overwrite behavior. An explicit `flows --output PATH` is treated as durable evidence and refuses to replace either the response or its `.request.json` sidecar unless `--force-output` is also supplied. New sidecars distinguish cache hits, original response retrieval time, artifact write time, and the exact response SHA-256. Legacy raw-only cache entries use their file modification time as the retrieval-time fallback instead of claiming a fresh network response.

`who-bought-sold` records wallet-level buyer or seller evidence for an explicit interval, labels, threshold, and page. It is the required source for buyer breadth, subject to complete pagination; `holders_count` is only holder breadth. `flows --label exchange` archives exchange-labelled flow evidence separately from Smart-Money flows. Neither command runs automatically: live/current requests can consume credits only when you explicitly invoke one. A paired exchange flow and Smart-Money flow does not establish transfer attribution.

## Research architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The initial experiment compares early Smart-Money accumulation with momentum accumulation and eventually an eligible-token control cohort, while explicitly avoiding look-ahead and survivorship bias.

This repository is the canonical durable checkpoint for the workflow.

## Committed research evidence

The first public evidence bundle is the [`2026-08-16 seven-token pilot`](research/experiments/2026-08-16-seven-token-pilot/REPORT.md). Reproduce its deterministic analysis with:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
```

The [`community-signal shadow`](research/experiments/2026-08-16-community-signal-shadow/REPORT.md) is a schema-v2, discovery-only companion derived from the immutable schema-v1 pilot. It uses `bucket_end` as feature availability, contains no fitted score, and cannot be used as a holdout while its point-in-time guarantee is unknown.

The [`paper-strategy feasibility evaluation`](research/experiments/2026-08-17-paper-strategy-feasibility/REPORT.md) is a schema-v3 offline comparison of preregistered theories. It selected no entry strategy, so it emitted no paper shortlist; its holder-breadth comparison advanced only as a descriptive follow-up. The next evidence upgrade is a separately versioned historical/beta collection with complete point-in-time provenance, pagination, liquidity, quotes, and execution costs.

The [`prospective GPT pilot`](research/experiments/2026-08-17-gpt-prospective-pilot/PREREGISTRATION.md) is a schema-v4, one-token paper observation. Its preregistration fixes an identity-blinded `gpt-5.6-sol` two-pass protocol, all six frozen comparator records, common observed fills, strict-greater-than win semantics, and a ten-call/ten-credit Nansen ceiling. `pilot-init`, `pilot-replay`, and `pilot-check` are offline; only explicit `pilot-start` and `pilot-settle` invocations can access providers. An unavailable or incomplete comparison is `unscorable`, never zero, and the workflow cannot create an order, wallet action, venue submission, or capital movement.

```bash
./nansen-lab pilot-replay --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
```

`results/` is ignored scratch space for exploratory outputs. Evidence becomes durable only after it is copied byte-for-byte into `research/experiments/`, checksummed in a manifest, documented, and committed. The append-only [`research ledger`](docs/RESEARCH-LEDGER.md) and [`evidence graph`](docs/RESEARCH-GRAPH.md) preserve the experiment's claims, limitations, and follow-up state.

[Powered by Nansen API](https://nansen.ai/). Public Token Screener and `tgm/flows` evidence is included under Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide).
