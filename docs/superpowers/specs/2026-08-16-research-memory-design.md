# Research Memory and Evidence Bundles

Date: 2026-08-16

Status: Approved for implementation

## Purpose

Make every research lesson durable, reviewable, reproducible, and useful to later prediction work. The repository must preserve the evidence behind a conclusion, not only the conclusion copied from a chat.

The first bundle will capture the 2026-08-16 seven-token Smart-Money flows pilot. Later runs will use the same structure without overwriting earlier evidence.

## Goals

- Commit the raw API responses, cohort-selection snapshots, request provenance, derived features, conclusions, caveats, and next tests needed to reproduce each experiment.
- Maintain an append-only research ledger and a GitHub-rendered Mermaid evidence graph.
- Produce point-in-time features and forward-return labels without look-ahead.
- Make missing forward horizons explicit, especially CDXR's not-yet-mature 24-hour outcome.
- Detect evidence drift or accidental mutation through checksums and bundle validation.
- Keep analysis deterministic and covered by focused tests.
- Attribute redistributed Nansen data in every public bundle.

## Non-goals

- This phase will not fit or advertise a profitable trading model.
- It will not optimize thresholds on the seven-token discovery sample.
- It will not treat a balance increase as proof of an exchange purchase; transfers and label changes may also change aggregate holdings.
- It will not commit API credentials, `.env`, virtual environments, or redundant request caches.
- The Mermaid graph will not be the numerical source of truth.

## Selected approach

Use versioned, immutable experiment bundles plus a curated Markdown ledger and Mermaid evidence graph.

A prose-only ledger was rejected because it cannot prove which rows and transformations support a claim. A database-first or graph-database design was rejected for now because it creates opaque Git diffs and operational overhead before the dataset warrants it.

## Repository layout

```text
research/
  experiments/
    2026-08-16-seven-token-pilot/
      manifest.json
      raw/
        candidates-20260816T094029Z.csv
        flows-*.json
      derived/
        hourly-features.csv
        event-windows.csv
        token-summary.csv
      REPORT.md
docs/
  RESEARCH-LEDGER.md
  RESEARCH-GRAPH.md
src/nansen_signal_lab/
  experiment.py
tests/
  test_experiment.py
```

The existing ignored `results/` and `data/cache/` directories remain scratch space. Evidence becomes durable only after it is copied into a named experiment bundle, checksummed, validated, documented, and committed.

## Bundle manifest

`manifest.json` is the machine-readable index and uses `schema_version: 1`. It contains:

- experiment ID, title, status (`discovery` or `holdout`), and creation time;
- hypothesis frozen before evaluation;
- source attribution and endpoint documentation URL;
- cohort records containing chain, token symbol, token address, and selection-time metadata;
- one evidence record per raw file containing its relative path, SHA-256 checksum, endpoint, request parameters, retrieval time, observed time range, row count, and completeness count;
- derivation configuration, including trailing and forward horizons;
- known provenance gaps.

The legacy CLI used relative `--days 4` windows and did not persist the exact request payload. The first manifest says so explicitly, records the invocation, file modification time, and exact observed interval, and does not invent unavailable timestamps. Current flow collection persists the exact request payload alongside each response, distinguishes cache status, original response retrieval time, and artifact write time, and records the response SHA-256. Legacy raw-only cache entries use file mtime as an explicit retrieval-time fallback.

Manifest paths are relative to the bundle. Validation fails on a missing file, checksum mismatch, unsupported schema version, invalid kind/endpoint pair, inconsistent raw row/count/completeness/range provenance, mismatched request/cohort identity, invalid evidence references, duplicate token identity, or inconsistent observed range. EVM-style `0x` addresses are case-insensitive; Solana addresses remain case-sensitive.

## Raw evidence policy

Raw files are copied byte-for-byte and never edited after commit. A correction or later follow-up creates a new evidence file or bundle and links back to the earlier one.

The first bundle includes the later candidate CSV snapshot and all seven current four-day `tgm/flows` responses. The earlier candidate CSV is byte-identical (SHA-256 `4ed384e23a9156017e358c524ae8c837f40c251ec993d819e88fe801bb8fba43`) and is recorded as a deduplicated scratch artifact rather than committed twice. The shorter pre-pilot CATE response is superseded by the validated four-day response and lacks sufficient request provenance, so it is excluded explicitly in the report rather than presented as equivalent evidence.

The repository is public. Nansen's redistribution guide currently marks Token Screener and `tgm/flows` data as redistributable with attribution. Each report and top-level research index will display "Powered by Nansen API" with a link to <https://nansen.ai/> and the redistribution guide at <https://docs.nansen.ai/guides/redistribution-guide>.

## Deterministic analysis

`src/nansen_signal_lab/experiment.py` contains pure analysis functions. The CLI gains:

```text
./nansen-lab analyze --manifest research/experiments/EXPERIMENT/manifest.json
./nansen-lab analyze --manifest research/experiments/EXPERIMENT/manifest.json --check
```

Normal mode validates the bundle and regenerates all derived CSV files deterministically. `--check` regenerates in memory and fails if committed derived files differ; it does not modify files.

Both `date` and `bucket_end` are parsed as timezone-aware ISO-8601 timestamps, and `bucket_end` must be later than `date`. A completed bucket becomes available at `bucket_end`; this availability instant is the feature/event `timestamp` used by every trailing and forward horizon lookup. The original boundaries remain in `source_bucket_start` and `source_bucket_end`. Rows are ordered by availability timestamp and duplicates are rejected. The final incomplete bucket is excluded and counted. Rows with prices that are not finite and positive, or holdings that are not finite and non-negative, are excluded and counted. Hourly gaps are reported and block horizon calculations that would cross them.

## Prediction-ready outputs

`hourly-features.csv` contains one row per valid token-hour with:

- experiment ID, cohort role, chain, token symbol, address, bucket-end availability timestamp, source bucket start, and source bucket end;
- price, aggregate Smart-Money token holdings, USD value, and holder count;
- one-hour holdings delta in tokens, percent, and approximate USD notional;
- trailing price returns at 1h, 4h, 12h, and 24h;
- trailing holdings changes at the same horizons;
- selection-time market cap, liquidity, age, netflow, and flow-to-market-cap ratio where available;
- completeness and gap flags.

`event-windows.csv` contains every non-zero holdings-change event rather than only events passing an optimized threshold. It adds forward returns at 1h, 4h, 12h, and 24h plus maximum favorable excursion and maximum adverse excursion for each mature horizon. Missing horizons remain blank and carry an explicit availability flag; they are never zero-filled.

`token-summary.csv` reproduces the seven-line endpoint summary and adds gross accumulation, gross distribution, net holdings change, count of accumulation events, size-weighted trailing returns, and size-weighted forward returns over mature windows.

No feature may read a row after its bucket-end availability timestamp. Forward returns, MFE, and MAE are labels only and must never be included as model inputs.

## Research report and graph

Each `REPORT.md` records:

- the frozen question and cohort-selection rationale;
- exact observation window and validation counts;
- results linked to derived rows and raw evidence;
- supported conclusions, rejected interpretations, limitations, and next test;
- whether each forward horizon is mature;
- Nansen attribution and a non-advisory research disclaimer.

`docs/RESEARCH-LEDGER.md` is append-only. Each entry links the experiment bundle, concise lessons, confidence level, and follow-up state.

`docs/RESEARCH-GRAPH.md` uses stable node IDs and Mermaid edges:

```text
hypothesis --> experiment --> observation --> interpretation --> next_test
                                      \--> caveat
```

The first graph distinguishes these observations:

- CDXR: large holdings accumulation with flat price, but the decisive 24-hour label is not mature;
- AI-HEDGE-FUND and MONGO: accumulation into weakness followed by further weakness;
- CHEAT.SH and PRISMA: momentum participation, with PRISMA ending in slight net distribution;
- TOAD: aggressive accumulation without a reversal in the observed window;
- CATE: mixed, modest momentum participation.

Graph nodes link to the experiment report; numeric claims live in committed CSV files and raw evidence.

## Follow-up collection

The first next test is a fixed-window CDXR follow-up after 2026-08-16 22:00 UTC, when the accumulation bucket starting at 2026-08-15 21:00 UTC and available at its 22:00 UTC end has a complete 24-hour label. The follow-up uses explicit `--from` and `--to` timestamps and creates new immutable evidence rather than overwriting the pilot response.

Later work expands horizons to 3d and 7d, adds eligible-token controls, uses an external OHLCV source for independent price validation, and freezes discovery/holdout splits before threshold fitting.

## Error handling

Analysis stops with a clear error when evidence is missing, mutated, malformed, duplicated, or temporally inconsistent. It does not silently impute prices, holdings, timestamps, or forward labels. Warnings that do not invalidate the bundle, such as an unavailable future horizon, are emitted in the report and availability columns.

## Verification

Focused tests cover:

- manifest validation and checksum mismatch detection;
- kind-specific provenance, raw-count/range, and cohort/evidence reference validation;
- exclusion of incomplete buckets;
- timezone-aware bucket-end availability, duplicate and gap handling;
- malformed/non-finite price and holdings exclusion;
- trailing and forward return calculations;
- absence of forward labels before a horizon matures;
- MFE and MAE calculations;
- deterministic output ordering and regeneration;
- preservation of the current seven-token summary values;
- cache-hit/network retrieval provenance and legacy-cache fallback;
- explicit-output overwrite refusal, forced replacement, response hashing, and temporary-file cleanup.

Completion requires the full test suite, a successful bundle validation, deterministic `analyze --check`, a secret scan of staged paths, and a clean Git diff containing only intended research-memory changes.

## Publication workflow

Implementation occurs on `codex/research-memory`. After verification, the intended files are committed, the branch is pushed, and a draft pull request is opened against `main`. No API key, `.env`, cache file, or unrelated scratch output may be staged.
