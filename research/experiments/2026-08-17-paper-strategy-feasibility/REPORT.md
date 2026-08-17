# Preregistered paper strategy feasibility evaluation

Status: **discovery** — no paper strategy selected.

Experiment: [`2026-08-17-paper-strategy-feasibility`](manifest.json) | source: [`2026-08-16-community-signal-shadow`](../2026-08-16-community-signal-shadow/manifest.json), SHA-256 `881c544e2591f76c727bc30e2663d77df408e72acadd7866ee3c240f5c06b1b8`.

[Powered by Nansen API](https://nansen.ai/). This evaluation is an offline transform of the already archived source evidence and made no live or paid API call.

## Decision

The preregistered gates selected no entry theory, so the paper shortlist is empty. The distribution risk-off veto cleared its own low feasibility gate but cannot be selected without an eligible entry theory. Thresholds were not changed after observing the result.

The holder-breadth comparison did advance for further paper discovery: its positive-breadth arm exceeded the non-positive arm by `8.414517919376026` percentage points on token-equal mean base-cost objective and by `0.6657275563326959` points on event-median base-cost objective. This is a descriptive comparison on the same seven selected token series, not an independently validated trading strategy.

## Fixed evaluation contract

The schema-v3 manifest freezes five hypotheses as six evaluable records because the holder-breadth hypothesis has two comparison arms. Every predicate reads only trailing schema-v2 signal fields available at `bucket_end`. A signal at hour `t` enters at the exact source price at `t + 1 hour` and exits after the theory's fixed four- or twelve-hour horizon. Missing entry or exit hours are omitted, and a theory cannot open overlapping episodes for the same token.

The base sensitivity charges 100 basis points per side; stress charges 250 basis points per side. Entry objectives apply both costs multiplicatively. The risk-off veto reports avoided-loss benefit after an exit and later re-entry cost. The three chronological blocks partition `[2026-08-13T11:00:00Z, 2026-08-16T10:00:00Z)` without random splitting.

The evaluator produced 95 mature, non-overlapping theory episodes. These rows are dependent within token and across overlapping theories; they are not 95 independent trades.

## Overall results

| Theory | Role | Episodes | Tokens | Token-equal base objective | Event-median base objective | Token-equal stress objective | Gate result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Breadth/acceleration inflection | entry | 3 | 2 | -1.1514% | +10.4565% | -4.1241% | Rejected: too few episodes and tokens; non-positive token-equal base mean |
| Distribution risk-off | veto | 3 | 3 | +0.4855% | +1.4191% | -2.5145% | Eligible alone; not selected because no entry qualified |
| Flow-only benchmark | reference | 56 | 7 | +1.5197% | -1.3178% | -1.5334% | Benchmark only |
| Holder breadth non-positive | comparison | 12 | 5 | -3.2445% | +0.4658% | -6.1543% | Comparison only |
| Holder breadth positive | comparison | 14 | 6 | +5.1700% | +1.1315% | +2.0072% | Comparison only; H3 advances descriptively |
| Sustained markup | entry | 7 | 3 | +13.2308% | -3.9763% | +9.8256% | Rejected: non-positive event median after base costs |

The flow-only benchmark illustrates why a mean alone is insufficient: its token-equal base mean was positive while its event median and stress token-equal mean were negative. The sustained-markup theory also had a positive token-equal mean but a negative event median, so the frozen gate correctly refused to force a winner.

## Chronological stability and cost sensitivity

- Breadth/acceleration appeared once in each block. Base objectives were `-18.4794%`, `+21.8966%`, and `+10.4565%`; only two tokens contributed overall.
- Every distribution-veto episode occurred in the first block. Its positive base result became negative under the stress cost, and the later two blocks supplied no evidence.
- Flow-only token-equal base objectives moved from `-1.4056%` to `+3.1997%` and `+3.5140%`; its overall stress objective was negative.
- Holder-breadth-positive token-equal base objectives were `-8.9565%`, `+14.8404%`, and `+1.0246%`. The non-positive arm reported `-3.6646%`, `-5.0570%`, and `+9.1718%`. The favorable overall H3 spread therefore does not imply uniform block dominance.
- Sustained-markup token-equal base objectives were `-7.1745%`, `+16.5624%`, and `+12.5375%`. Despite two positive blocks and seven episodes across three tokens, the overall base-cost event median remained `-3.9763%`.

The base/stress results are sensitivities, not simulated fill costs: the source contains no timestamped route quotes, point-in-time liquidity, gas, or slippage.

## Selection, rejection, and blocked evidence

`paper-strategies.json` records:

- `selection_status: no_paper_strategy_selected`;
- no selected entry and no selected veto;
- breadth/acceleration rejected for insufficient events, insufficient tokens, and a non-positive token-equal base mean;
- sustained markup rejected for a non-positive base-cost event median;
- distribution risk-off withheld because a veto requires a selected entry;
- flow-only retained only as the benchmark;
- both holder-breadth arms retained only for the H3 comparison;
- buyer-breadth plus exchange-outflow confirmation blocked because complete point-in-time wallet-buyer and exchange-labelled flow history is absent.

`holders_count` remains holder breadth, not buyer breadth. Exchange-labelled co-movement would not by itself prove that Smart Money received withdrawn exchange supply.

## Evidence strength and next test

The source declares `point_in_time_guarantee=unknown`, uses seven purposively selected tokens, and contains dependent hourly observations. This experiment can freeze a prospective research question, but it cannot establish expected return, accuracy, causal attribution, or a capital-trading recommendation.

The next evidence upgrade is a separately versioned collection using Nansen's historical/beta surfaces only where their contracts provide point-in-time semantics. Archive every request, page, retrieval time, schema version, completeness marker, and hash for historical Token Screener, Smart-Money balances, buyer/seller breadth, token-flow summaries, OHLCV, and DEX trades. Do not promote a strategy beyond paper discovery until the emitted advancement gates are satisfied: at least eight weeks, 100 simulated fills across 20 tokens, timestamped quotes, point-in-time liquidity, positive actual-cost mean and median, a positive lower one-sided 95% token/week block-bootstrap bound, non-negative stress expectancy, at least 70% fill rate, and no token above 20% of total P&L.

## Reproduction

```bash
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
```

This command is offline and requires exact byte identity for [`theory-events.csv`](derived/theory-events.csv), [`theory-summary.csv`](derived/theory-summary.csv), and [`paper-strategies.json`](derived/paper-strategies.json).

## Disclaimer

This is a reproducible paper-research feasibility study, not trading, investment, legal, or financial advice. It does not recommend buying, selling, or holding any token.
