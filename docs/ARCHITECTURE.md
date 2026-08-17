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
