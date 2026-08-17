# Community-inspired trailing signal shadow

Status: **discovery** — descriptive signal shadow, not a new backtest.

Experiment: [`2026-08-16-community-signal-shadow`](manifest.json) | source: [`2026-08-16-seven-token-pilot`](../2026-08-16-seven-token-pilot/manifest.json), SHA-256 `2662998bdf21d2d3b80d11003b8e62db59e1ebee68ad33858f2bf0008c8a93d0`.

[Powered by Nansen API](https://nansen.ai/). The source pilot's permitted public evidence follows Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide); this shadow publishes only derived aggregates, never raw Smart-Money holdings.

## Scope and availability

This schema-v2 bundle deterministically transforms the frozen schema-v1 pilot into `signal-features.csv`; it does not resample tokens, alter the source raw data, introduce a score, fit weights, or create independent observations. Its seven purposively selected token series cannot determine feature weights or predictive accuracy. The exact joins to the pilot's forward labels below are descriptive context for dependent event rows, not validation or a holdout result.

Every feature is first available at the current completed flow bucket's `bucket_end`, never its source bucket start. The manifest declares `point_in_time_guarantee=unknown`, so this bundle is discovery-only and is ineligible for holdout use. Candidate-selection market cap, liquidity, netflow, and role metadata are not signal inputs because their recorded availability is not point-in-time-safe.

For a contiguous trailing horizon `h`, with start/end holdings `H0`/`H1`, prices `P0`/`P1`, and hourly holdings deltas `d`:

- `holdings_change_h_pct = 100 * (H1 / H0 - 1)` and `price_return_h_pct = 100 * (P1 / P0 - 1)`.
- Accumulation/distribution persistence is the fraction of the `h` deltas that are respectively strictly positive/negative. Velocity is holdings change divided by `h`; acceleration is that velocity minus the immediately preceding, disjoint `h`-hour velocity.
- `holder_count_change_h` is Smart-Money **holder breadth**, not buyer breadth. Retention is `max(H1 - H0, 0) / sum(max(d, 0))`, unavailable when gross positive deltas are zero.
- Divergence is `holdings_change_h_pct - price_return_h_pct`. Phase is `accumulation_divergence` for positive holdings and non-positive price, `markup` for both positive, `distribution_divergence` for negative holdings and non-negative price, `markdown` for both negative, otherwise `flat`; incomplete history is `unavailable`.

Any missing or gapped hour makes the affected horizon unavailable rather than shortening or filling the window. No forward return, MFE, MAE, selection field, fixed community weight, or composite score enters this table.

## Descriptive audit

The reproducible audit in the task record reports phase counts, maximum persistence, and the latest acceleration/divergence for every token and horizon. At the final common timestamp, its 24-hour observations are:

- AI-HEDGE-FUND: 22 accumulation-divergence and 25 distribution-divergence rows; latest 24-hour acceleration `0.0403294078955` and divergence `27.7124175215` percent.
- CHEAT.SH: 45 markup rows; latest 24-hour acceleration `0.0759783671211` and divergence `-38.5147138394` percent.
- MONGO: 71 accumulation-divergence rows; latest 24-hour acceleration `-0.00710775987958` and divergence `20.6821884262` percent.
- PRISMA: 35 markup rows; latest 24-hour acceleration `0.0636752270114` and divergence `-168.063078826` percent.
- CDXR: 21 accumulation-divergence and 47 markup rows; latest 24-hour acceleration `1.83977325215` and divergence `45.5145469041` percent.
- CATE: 22 accumulation-divergence and 46 markup rows; latest 24-hour acceleration `-0.0453304122204` and divergence `-23.5204295035` percent.
- TOAD: 42 accumulation-divergence and 19 markup rows; latest 24-hour acceleration `-0.508788054182` and divergence `12.3303005961` percent.

All 259 pilot event rows join exactly on chain, token address, and timestamp. Label availability declines from 257/259 at 1 hour to 196/259 at 24 hours; those overlapping, right-censored rows are not independent samples. The audit's full per-token/per-horizon output is the source for each number above and for the descriptive label context.

## Missing evidence and collection order

Buyer breadth, exchange withdrawal, label-specific rotation, historical liquidity, and execution costs are absent. `holders_count` cannot substitute for buyer breadth, and a paired Smart-Money balance change plus exchange balance change cannot establish transfer attribution.

Collect evidence in this order:

1. Point-in-time state with explicit provider-as-of, labels-as-of, price-as-of, availability, and guarantee.
2. Wallet-level buyer/seller breadth for a fixed interval, with complete pagination and exact request provenance.
3. Exchange-labelled flows for the same interval.
4. Transfer-level counterparty attribution with explicit exchange/entity labels and supply context.

The first gate is supported by Nansen's [historical holdings documentation](https://docs.nansen.ai/api/smart-money/historical-positions) and [data methodology](https://docs.nansen.ai/guides/data-methodology-and-technical-reference), but it has not been collected for this bundle. Execution research also requires historical liquidity and realistic fees, slippage, and fill rules before any ROI claim.

## Community provenance and licensing

The public [Nansen CLI Builds catalog](https://release.nansen.ai/en/help/articles/6399546-nansen-cli-builds) is inspiration only: its project descriptions and reported outcomes are community claims, not results replicated here. Nansen's [CLI research guide](https://academy.nansen.ai/en/articles/5207576-research-and-trade-with-nansen-cli) is cited for the surrounding research interface, not as validation of this signal.

Nansen Divergence contributed only the general lead that holdings/flow and price regimes may be examined together; no community performance claim or fixed weighting is adopted. Smart Money Rotation Radar supplied high-level leads about rotation, freshness, liquidity, breadth, and staleness, but its repository has no declared license: no code, prose, or expression was copied. Supply Control Scanner's reported exchange-withdrawal behavior and Superior Trade's reported four-hour cadence/performance are likewise unreplicated leads, never expected returns or validated predictions.

## Reproduction

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
```

The exact read-only audit command and its complete output are retained in the local Task 4 record; the report's stated values are reproduced from the committed CSVs by that audit.
