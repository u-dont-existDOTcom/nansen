# Seven-token Smart-Money accumulation pilot

Status: **discovery**
Experiment: [`2026-08-16-seven-token-pilot`](manifest.json)

[Powered by Nansen API](https://nansen.ai/). The archived Token Screener and `tgm/flows` evidence is published under Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide).

## Question and design

The frozen question was whether Smart-Money accumulation before a large price move might show different forward-return behavior from accumulation after momentum was already visible. The screen selected four `early` tokens and three `momentum` tokens from the 2026-08-16 candidate snapshot. This is a small, purposively selected discovery cohort, not a control-matched or holdout evaluation.

The bundle contains the exact candidate CSV and seven byte-for-byte `tgm/flows` responses indexed by [`manifest.json`](manifest.json). Every flow response has 96 hourly rows spanning `2026-08-12T10:00:00Z` through `2026-08-16T09:00:00Z`: 95 complete rows and one final incomplete row. Analysis excludes the incomplete row, leaving 665 valid token-hours with no invalid metric rows or hourly gaps. Exact request `from`/`to` boundaries are unavailable because the legacy relative-window CLI did not persist them; the manifest records the original `--days 4 --limit 100` invocations, retrieval times, observed intervals, and checksums without reconstructing unavailable boundaries.

Derived evidence:

- [`token-summary.csv`](derived/token-summary.csv) freezes endpoint and within-token event-weighted results.
- [`event-windows.csv`](derived/event-windows.csv) contains all 259 non-zero hourly holdings-change events and forward-label availability.
- [`hourly-features.csv`](derived/hourly-features.csv) contains point-in-time features for all 665 valid rows.

## Seven-line endpoint summary

Returns and holdings changes are percentages from the derived summary. The 24-hour columns compare the last complete row with the complete row exactly 24 hours earlier; all-window columns compare the first and last valid complete rows.

| Token | Role | Price 24h | Holdings 24h | Price all | Holdings all |
| --- | --- | ---: | ---: | ---: | ---: |
| CDXR | early | +0.04% | +45.55% | +0.72% | +52.50% |
| AI-HEDGE-FUND | early | -27.01% | +0.70% | -20.14% | +0.14% |
| CHEAT.SH | momentum | +39.80% | +1.28% | +171.94% | +1.14% |
| MONGO | early | -20.48% | +0.20% | -64.11% | +1.31% |
| PRISMA | momentum | +168.92% | +0.86% | +264.03% | -0.15% |
| TOAD | early | -6.98% | +5.35% | -46.95% | +32.58% |
| CATE | momentum | +27.23% | +3.71% | +9.48% | +11.28% |

These endpoints describe co-movement during one observed window. They do not establish that holdings changes caused price moves.

## Event-weighted timing analysis

For each token, accumulation returns are weighted by that token's positive hourly holdings delta. Weighting is strictly within-token because token units are not comparable across assets. An unavailable forward horizon is omitted rather than filled with zero. `Mature 24h` therefore shows how many accumulation events contribute to the 24-hour weighted return.

| Token | Accumulation events | Mature 24h | Weighted forward 1h | Weighted forward 4h | Weighted forward 12h | Weighted trailing 24h | Weighted forward 24h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CDXR | 7 | 4 | +0.03% | -0.07% | +0.35% | +1.45% | +0.47% |
| AI-HEDGE-FUND | 20 | 16 | -0.36% | -3.21% | -15.97% | -20.34% | -12.85% |
| CHEAT.SH | 28 | 21 | +0.22% | +6.14% | +13.23% | +35.57% | +11.41% |
| MONGO | 26 | 20 | -2.06% | -5.91% | -7.91% | -27.11% | -25.24% |
| PRISMA | 26 | 18 | +4.02% | +16.00% | +44.55% | +19.93% | +12.85% |
| TOAD | 33 | 26 | -0.23% | -1.13% | +0.11% | -11.81% | -4.79% |
| CATE | 45 | 31 | +0.77% | +0.55% | -0.30% | +9.95% | +4.14% |

The observed behavior is heterogeneous. AI-HEDGE-FUND and MONGO accumulated during weakness and their size-weighted mature forward returns remained negative at every reported horizon. CHEAT.SH and PRISMA had positive weighted forward returns at every horizon, consistent with momentum participation in this window; PRISMA nevertheless ended with a slight all-window net holdings decline. TOAD accumulated aggressively but did not reverse its all-window price decline. CATE's weighted forward path was modest and mixed. Four tokens had positive within-token weighted 24-hour forward returns and three had negative returns, but that sign count is neither a success rate nor predictive evidence: events within a token are dependent, the cohort was selected rather than sampled, and no control group was evaluated.

### CDXR label maturity

CDXR is the clearest unresolved observation: Smart-Money holdings rose 52.50% while price rose only 0.72% over the complete window. The largest accumulation bucket is timestamped `2026-08-15T21:00:00Z` in the source, ends at `22:00:00Z`, and adds 13,020,450.496 tokens. Its 12-hour and 24-hour forward labels are unavailable in this bundle; the last complete observation is `2026-08-16T08:00:00Z`. Consequently, CDXR's 24-hour weighted result uses only four earlier mature events and must not be read as the decisive label for the late accumulation.

The fixed-window next test collects new immutable CDXR evidence after `2026-08-16T22:00:00Z`, using explicit `--from` and `--to` timestamps, so the late bucket receives a complete 24-hour label without overwriting this pilot.

## What the evidence supports

- The seven tokens exhibited five descriptively different behaviors: flat-price CDXR accumulation; continued weakness for AI-HEDGE-FUND and MONGO; momentum participation for CHEAT.SH and PRISMA; TOAD accumulation without an observed reversal; and mixed, modest CATE participation.
- Holdings accumulation did not uniformly precede positive returns in this selected four-day window.
- Point-in-time features and forward labels can be reproduced from the committed evidence, and unavailable horizons remain explicit.

The pilot does **not** support a tradable threshold, a causal Smart-Money effect, an early-versus-momentum performance comparison, or an out-of-sample prediction claim.

## Limitations

- **Selection bias:** the same-day screen selected high-netflow candidates and assigned `early`/`momentum` roles using normalized price change. There is no eligible-token control cohort, random sampling, preregistered threshold, or untouched holdout.
- **Endpoint scope:** `tgm/flows` provides hourly aggregate holdings for Nansen's `smart_money` label and an endpoint price; it does not identify a causal transaction sequence. Prices were not independently checked against OHLCV data.
- **Short, dependent sample:** seven tokens over 95 complete hours cannot estimate generalization. Hourly events for the same token are correlated, and token-level weighted values are not cross-token portfolio returns.
- **Legacy provenance gap:** exact original request boundaries and sidecars were not persisted. The manifest records this absence rather than inventing timestamps.
- **Right censoring:** late events lack some 12-hour and 24-hour labels, most materially CDXR's late accumulation. Availability columns expose this censoring.

## Excluded scratch artifacts

The earlier candidate CSV was excluded because it is byte-identical to `candidates-20260816T094029Z.csv` (SHA-256 `4ed384e23a9156017e358c524ae8c837f40c251ec993d819e88fe801bb8fba43`) and would duplicate evidence. `flows-solana-Ai66LHZG9MCz.pre-pilot.json` was excluded because it is a shorter, superseded CATE response without sufficient request provenance. Neither artifact is part of the committed bundle.

## Disclaimer

This report documents a reproducible research pilot. It is not trading, investment, legal, or financial advice, and it does not recommend buying or selling any token.
