# Architecture

## Objective
Test whether Smart-Money accumulation predicts forward returns, especially whether accumulation that precedes price movement outperforms accumulation after large price moves.

## Durable workflow
1. **Authenticate cheaply** with one Token Screener call.
2. **Cache raw API responses** by endpoint + canonical request hash. Never repay for an identical request unless `--refresh` is explicit. Cache provenance preserves the original network retrieval time; legacy raw-only entries fall back explicitly to file mtime.
3. **Discover candidates** with Token Screener using `only_smart_money=true`.
4. **Inspect historical Smart-Money flows** for selected tokens using `tgm/flows` + `label=smart_money`.
5. Before a serious backtest, validate field units and timestamp semantics from live responses.
6. Build historical event cohorts without look-ahead/survivorship bias. A completed hourly bucket becomes available at its timezone-aware `bucket_end`, never at its source `date` start boundary.
7. Measure forward returns at 1h, 4h, 12h, 24h, 3d, 7d plus MFE/MAE and costs.
8. Split discovery vs holdout periods before optimizing thresholds.

## Initial hypothesis buckets
- Early accumulation: strong Smart-Money buying; trailing price change <= +5%.
- Momentum accumulation: strong Smart-Money buying; trailing price change > +15%.
- Middle: between those thresholds.
- Control: comparable eligible tokens without strong Smart-Money inflow.

These are hypotheses, not trading rules. Thresholds must be tuned only on a training sample and evaluated on untouched holdout data.

## Important API caveat
Nansen Token Screener currently documents short retention at fine granularity and up to roughly two months for daily data. The serious backtest may therefore need `smart-money/historical-holdings`, per-token `tgm/flows`, external OHLCV, or locally accumulated snapshots rather than pretending Token Screener is an unlimited historical database.

## Versioned community-signal shadow

The `2026-08-16-seven-token-pilot` is frozen schema v1: its raw evidence, manifest, and derived CSV bytes remain unchanged. `2026-08-16-community-signal-shadow` is an opt-in schema-v2 companion that references the v1 manifest hash and writes only `derived/signal-features.csv`. Its trailing features use contiguous completed source buckets and become available at `bucket_end`; it is discovery-only because `point_in_time_guarantee=unknown`.

`holders_count` measures Smart-Money **holder breadth**. It is not buyer breadth. Buyer breadth requires wallet-level buyer evidence from an explicit, complete `who-bought-sold` request. Exchange-labelled flows provide separately labelled co-movement evidence; they do not attribute a transfer between an exchange and Smart Money.

```bash
# User-invoked collection only; each call can use paid/current API access.
./nansen-lab who-bought-sold --chain base --token TOKEN_ADDRESS --side BUY \
  --from 2026-08-16T00:00:00Z --to 2026-08-17T00:00:00Z \
  --labels Fund "Smart Trader" "30D Smart Trader" --min-volume-usd 1000 --limit 100
./nansen-lab flows --chain base --token TOKEN_ADDRESS --label exchange --days 7
```

There are no automatic credit-spending calls: tests, analysis, and documentation do not invoke Nansen. Callers deliberately choose live collection; durable explicit outputs retain exact request provenance and reject accidental overwrite. Run both deterministic checks after a committed-bundle change:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
```

## Offline schema-v3 paper feasibility

`2026-08-17-paper-strategy-feasibility` binds by SHA-256 to the schema-v2 shadow and evaluates only manifest-declared trailing predicates. It rebuilds the validated source lineage in memory, enters at the exact next-hour source price, requires a mature fixed exit, prevents overlapping same-theory episodes per token, and applies frozen 100/250 basis-point per-side cost sensitivities. Chronological blocks are descriptive stability checks; there is no random split or fitted threshold.

```bash
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
```

The command is offline: it neither loads `.env` nor constructs `NansenClient`. Its schema-v3 outputs are deterministic event and summary CSVs plus a paper-only JSON decision. The current bundle selected no entry strategy. A distribution veto was individually eligible but cannot be selected without an entry, while the holder-breadth comparison advanced only for further discovery.

The upgrade path is a new evidence collection, not threshold revision. Historical/beta endpoint use must archive every page, request, retrieval time, schema version, completeness marker, and hash, and may claim point-in-time status only where the provider contract explicitly supplies it. Historical Token Screener, Smart-Money balances, buyer/seller breadth, token-flow summaries, OHLCV, DEX trades, point-in-time liquidity, and executable quotes are the required inputs before a broader backtest or prospective paper advancement.

## Bounded historical holder-breadth discovery

The `holder-breadth-daily-v1` workflow is a separate immutable discovery family.
It freezes a historical screener date and deterministic 20-token universe, then
collects at most two complete point-in-time daily historical-holdings pages and
at most five independent chain-batched daily OHLCV responses. A four-day
accumulation condition is partitioned into positive and non-positive
holder-breadth arms and evaluated at a fixed twelve-day horizon across four
chronological blocks. Every eligible signal remains in the denominator, and
advancement requires complete outcome coverage. Thresholds, dates, costs, and
advancement gates are bound before collection.

The live command first matches the exact public OpenAPI bytes and proves the
account balance, then enforces nine authenticated request attempts and twelve
credits with no automatic retries. It
uses the same durable request-before-transmission, exact-response archive, and
hash-linked budget journal as the prospective runner. Init and check never load
credentials.

```bash
./nansen-lab historical-init --experiment-dir research/experiments/EXPERIMENT_ID
./nansen-lab historical-start --manifest research/experiments/EXPERIMENT_ID/manifest.json
./nansen-lab historical-check --manifest research/experiments/EXPERIMENT_ID/manifest.json
```

The result can prioritize or drop the daily holder-breadth analogue. It cannot
advance to capital: screener beta risk, selection-date-only liquidity, daily
close proxies, and assumed 100/250-bps costs keep it below prospective and
execution-aware profitability gates.

### Source-bound recovery

If a terminal discovery has complete, outcome-unseen screener bytes but fails
only provider accounting/schema evidence, it is never reopened. The separately
versioned recovery binds the source manifest and evidence, records raw and
normalized row hashes, freezes its cohort and outcome requests before new
access, and collects only holdings plus independent OHLCV. The v2 recovery
admits exactly one observed-cost derivation and one source-only `bsc -> bnb`
alias. New responses retain strict pricing and contract-native chain checks.
Its seven-attempt/six-credit ceiling is incremental; the seal also reports the
nine-attempt/eleven-credit source-plus-successor ceiling.

The source recovery is one-off and bound to the terminal experiment ID. Its
completed bundle may be verified offline, but cannot initialize a duplicate:

```bash
./nansen-lab historical-recovery-check \
  --manifest research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/manifest.json
```

## Prospective schema-v4 GPT pilot

`2026-08-17-gpt-prospective-pilot` is a one-token, paper-only observation bound by hash to the frozen schema-v3 strategy records, the prospective design, and the pinned Nansen OpenAPI contract extract. Its lifecycle is append-only: `preregistered -> snapshot_collected -> decision_sealed -> entry_observed -> settled`, with `unscorable` as a terminal failure state. Every stage uses a hash-linked seal and an immutable snapshot of the Nansen budget journal; the mutable budget head is only a recoverable cache.

Before any paid call, the runner verifies the public OpenAPI bytes, archives a model-access preflight, and requires an explicit zero-cost Nansen account response. Every provider request is installed before transmission and every received response is archived as exact bytes with headers and timing metadata. A transmitted request without a complete response is ambiguous and is never rerolled. Only an explicit zero-use 429 may receive one persisted retry. The hard ceiling is ten Nansen calls and ten credits.

The selected candidate's address and symbol are sealed separately in `derived/selection.json`; GPT receives only `normalized/snapshot.json`, whose identity is `candidate-1`. Pass 1 chooses `LONG` or `ABSTAIN` from that snapshot. Pass 2 receives the exact same snapshot, the exact Pass 1 response hash, and all six frozen theory records without their historical outcomes. Neither pass has tools or prior-response chaining.

All GPT and comparator actions share the same observed DEX entry/exit evidence and closed five-minute OHLCV grid. Incomplete pages, open/gapped candles, unavailable predicates, unfilled orders, and cash are distinct states. Pass 2 wins only when its scored net return is strictly greater than every applicable scorable comparator; ties are not wins. The workflow is descriptive and paper-only: no code path submits an order, touches a wallet, estimates gas, moves capital, or claims an executable route.

```bash
# Offline, credential-free commands
./nansen-lab pilot-init --experiment-dir research/experiments/2026-08-17-gpt-prospective-pilot
./nansen-lab pilot-replay --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
```
