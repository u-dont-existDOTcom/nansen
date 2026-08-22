# Smart-Money Exit Timing (1–20 weeks)

Status: architecture checkpoint. Do not treat this document as a validated trading rule until the exit study has passed walk-forward/holdout testing.

## Objective

Extend the existing entry-signal research so that a position opened from a Smart-Money accumulation signal can be exited within a 1–20 week horizon to maximize **expected realized return**, while explicitly measuring drawdown, liquidity/slippage, and premature-exit risk.

The week number is a horizon constraint, not the primary signal. Exit timing should respond to evidence of Smart-Money distribution and weakening conviction.

## Existing-work decision

**Adapt rather than invent from scratch.**

The repository's existing hypothesis—Smart-Money accumulation before a price move may be more informative than accumulation after a large move—has a close analogue in recent market-microstructure research. Anastasopoulos et al., *Journal of Financial Markets* 79 (2026), DOI 10.1016/j.finmar.2026.101047, find that cryptocurrency order flow predicts future returns at daily and weekly horizons, with stronger predictive content at the weekly horizon, while lagged returns contain a short-term reversal component.

Nansen already exposes the core observables needed for an exit study:

- Smart Money netflow over 1h / 24h / 7d / 30d;
- current Smart Money holdings;
- historical Smart Money holdings;
- real-time Smart Money DEX trades;
- Smart Money perpetual activity on supported venues;
- Token Screener and per-token flow history already used by this repo.

Therefore the novel remainder is not a new theory of flow. It is the **exit-state classifier and its validation for the repo's specific entry cohorts and 1–20 week horizon**.

## Required live state per position

At each evaluation timestamp collect, where available:

1. price return: 24h, 7d, 30d, and since entry;
2. Smart Money netflow: 1h, 24h, 7d, 30d;
3. Smart Money holdings level and recent balance change;
4. historical-holdings slope / change over at least 7d and 30d;
5. Smart Money DEX buy vs sell value/count over the latest 24h;
6. CEX-transfer contribution when exposed by the flow endpoint;
7. Smart Money perp positioning/trades for the token or relevant market, when supported;
8. liquidity, volume, market cap, and estimated exit slippage;
9. broad-market regime variables (at minimum BTC/ETH benchmark returns and volatility).

Preserve raw point-in-time responses in the cache. Never reconstruct historical signals from today's wallet labels or today's token universe when that would introduce look-ahead or survivorship bias.

## Candidate exit states

These are hypotheses to test, not hand-tuned production rules.

### Conviction intact

Typical evidence:

- 7d and 30d Smart Money netflow remain positive;
- aggregate Smart Money holdings are stable or rising;
- recent DEX activity is not persistently sell-skewed;
- price has not become severely detached from flow/holdings.

Default action in the experiment: **continue holding**, subject to the 20-week maximum horizon and liquidity/risk controls.

### Early distribution / trim state

Typical evidence:

- price is still rising but 24h flow turns negative or decelerates sharply;
- 7d flow remains positive but is weakening;
- holdings flatten or begin to fall;
- DEX trades shift toward selling.

This is the main divergence state to test for partial exits. It should be compared with doing nothing, not assumed superior.

### Confirmed distribution / exit state

Typical evidence:

- 24h and 7d netflow are negative together, especially if 30d flow also rolls over;
- Smart Money holdings are falling materially;
- DEX activity remains sell-skewed;
- Smart Money perp activity confirms reduced long exposure / increased short exposure where available;
- deterioration persists across more than one snapshot rather than one noisy print.

This is the main candidate full-exit state.

### Hard horizon

At 20 weeks, close the experimental position for horizon-comparable evaluation even if conviction remains intact. A separate long-horizon study can test what happens after week 20.

## Baselines

Every proposed exit model must beat strong simple baselines after costs:

1. fixed exits at 1, 2, 4, 8, 12, 16, and 20 weeks;
2. buy-and-hold through week 20;
3. the prior simple rule baseline: 5% stop, sell 50% at +15%, remainder at +30%;
4. simple price-only trailing-stop / trend exits;
5. simple flow-only exits (for example, first persistent 7d Smart Money netflow reversal).

Do not promote a more complex classifier unless it improves out-of-sample return/drawdown tradeoffs versus these baselines.

## Evaluation design

Reuse the repo's early / middle / momentum entry cohorts. For each entry event, simulate daily or higher-frequency decision snapshots until exit or week 20.

Record:

- realized net return after estimated costs;
- MFE and MAE before exit;
- maximum drawdown while held;
- time to exit;
- percentage of eventual MFE captured;
- premature-exit regret (post-exit upside over a defined window);
- tail loss / expected shortfall;
- turnover and estimated slippage;
- performance by entry bucket, chain, liquidity tier, market-cap tier, and market regime.

Use chronological discovery / validation / untouched holdout periods. If data volume permits, use walk-forward evaluation. All thresholds or learned parameters must be selected without seeing the final holdout.

## Model ladder

Test in this order:

1. **Rule baseline:** transparent persistence/divergence rules.
2. **Regularized logistic / survival model:** probability that the best remaining exit is near, using only contemporaneously available features.
3. **Tree/boosted model:** only if it produces materially better holdout economics and stable feature behavior.

Do not start with a bespoke weighted score. The recent order-flow literature supports nonlinear forecasting as a candidate, but the simpler model must remain the benchmark.

## Live decision output

For an actual position, report:

- current state: conviction intact / early distribution / confirmed distribution / ambiguous;
- evidence by 24h / 7d / 30d horizon;
- whether Smart Money holdings confirm or contradict flow;
- DEX/perp confirmation where available;
- weeks since entry and weeks remaining to the 20-week cap;
- recommended action as a probability-aware range (hold / trim fraction / exit), not a false-precision sell date;
- explicit data freshness and any missing endpoint/authentication caveat.

## Current implementation gap

As of 2026-08-22 the durable repo implements candidate discovery and per-token Smart-Money flow retrieval, but not the multi-endpoint exit-state collector or validated 1–20 week exit model. The live Nansen connector used during this review also returned an authentication error because a `NANSEN-API-KEY` header was not configured, so no current Smart-Money values should be represented as freshly observed until authentication is restored.
