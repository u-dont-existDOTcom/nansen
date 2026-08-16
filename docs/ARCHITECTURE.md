# Architecture

## Objective
Test whether Smart-Money accumulation predicts forward returns, especially whether accumulation that precedes price movement outperforms accumulation after large price moves.

## Durable workflow
1. **Authenticate cheaply** with one Token Screener call.
2. **Cache raw API responses** by endpoint + canonical request hash. Never repay for an identical request unless `--refresh` is explicit.
3. **Discover candidates** with Token Screener using `only_smart_money=true`.
4. **Inspect historical Smart-Money flows** for selected tokens using `tgm/flows` + `label=smart_money`.
5. Before a serious backtest, validate field units and timestamp semantics from live responses.
6. Build historical event cohorts without look-ahead/survivorship bias.
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
