# Paper Strategy Feasibility Evaluation Design

Date: 2026-08-17
Status: approved under the repository owner's standing instruction to choose safe in-scope trade-offs autonomously

## Objective

Turn the frozen seven-token discovery evidence into a deterministic, leakage-resistant feasibility comparison of several preregistered strategy theories, then select at most one entry theory and one risk-off veto for prospective paper trading.

The output is a paper-research shortlist, not a backtest winner, expected-return claim, live signal, order, or authorization to trade capital. The source evidence remains discovery-only because it has seven purposively selected tokens, dependent hourly observations, no eligible-universe controls, no historical execution quotes, and `point_in_time_guarantee=unknown`.

## Approach decision

Three approaches were considered:

1. **Deterministic feasibility evaluator over the frozen bundle — selected.** This is offline, costs no API credits, preserves the existing evidence, and can freeze hypotheses before any future paper outcomes. Its limitation is that selection means “most useful to test in paper,” never “validated alpha.”
2. **Immediate retrospective reconstruction from Nansen beta backtesting endpoints.** The current API exposes historical screener, historical Smart-Money balances, historical buyer/seller, historical flow summary, OHLCV, and DEX-trade surfaces with stronger temporal semantics. This can provide a broader follow-up, but it is a separate paid, beta-schema collection project and still needs complete pagination and an execution model.
3. **Wait for a prospective hourly holdout before selecting anything.** This provides the strongest evidence but delays paper testing for weeks. Paper trading is itself the safe prospective validation stage, so waiting is unnecessary provided the shortlist is explicitly unvalidated.

The implementation uses approach 1 now and records approach 2 as the next evidence upgrade. No live or paid endpoint is called by the evaluator.

## Scope and boundaries

The implementation adds a schema-v3 evaluation bundle and a focused pure module. It must not alter any schema-v1 or schema-v2 manifest, raw response, derived CSV, checksum, feature definition, or loader behavior.

The evaluator:

- loads a schema-v3 manifest;
- binds it by SHA-256 to the existing schema-v2 signal manifest;
- follows the already validated schema-v2-to-schema-v1 lineage;
- rebuilds signal and source hourly rows in memory;
- evaluates only whitelisted trailing signal fields;
- calculates executable proxy outcomes from the first complete hourly price after signal availability;
- applies non-overlap and fixed cost scenarios;
- emits deterministic event, summary, and paper-strategy outputs;
- never constructs `NansenClient` or accesses credentials.

The evaluator does not fit weights, optimize thresholds, randomly split rows, infer exchange-to-Smart-Money transfers, treat holder count as buyer breadth, or use `selection_*`, `cohort_role`, `forward_*`, MFE, or MAE fields as predictors.

## Fixed hypotheses

All thresholds below are frozen as version 1. Changing any predicate creates a new stable theory ID and requires fresh prospective evidence.

### H0 — Flow-only benchmark

`flow-only-benchmark-v1` fires when `holdings_change_1h_pct > 0`. It is a reference, never selectable for paper trading. It tests whether added conditions improve on the behavior already shown to be insufficient by the pilot.

### H1 — Distribution risk-off veto

`distribution-risk-off-veto-v1` fires when all are true:

- `market_phase_4h` is `markdown` or `distribution_divergence`;
- `distribution_persistence_4h >= 0.75`;
- `holdings_acceleration_4h_pct_per_hour < 0`;
- `holder_count_change_4h < 0`.

It predicts a negative next-executable four-hour return. Its objective is avoided loss for an already open long after exit and later re-entry costs. It can be selected only as a veto paired with an entry theory.

### H2 — Breadth-confirmed acceleration inflection

`breadth-acceleration-inflection-v1` fires when all are true:

- `market_phase_4h` is `markup` or `accumulation_divergence`;
- `accumulation_persistence_4h >= 0.50`;
- `holder_count_change_4h > 0`;
- current `holdings_acceleration_4h_pct_per_hour > 0`;
- the exact prior-hour `holdings_acceleration_4h_pct_per_hour <= 0`.

It predicts a positive next-executable four-hour return. It tests whether a sign change in acceleration is a useful entry timing event.

### H3 — Holder-breadth incremental comparison

Both arms require:

- `holdings_change_4h_pct > 0`;
- `accumulation_persistence_4h >= 0.50`;
- `accumulation_retention_4h >= 0.80`.

`holder-breadth-positive-arm-v1` additionally requires `holder_count_change_4h > 0`; `holder-breadth-nonpositive-arm-v1` requires `holder_count_change_4h <= 0`. The outcome is the next-executable 12-hour return. This is a paired descriptive comparison and neither arm is directly selectable. The hypothesis advances only if both the token-equal mean spread and event-median spread are positive.

### H4 — Sustained markup confirmation

`sustained-markup-confirmation-v1` fires when all are true:

- `market_phase_12h == markup`;
- `accumulation_persistence_12h >= 0.50`;
- `holder_count_change_12h > 0`;
- `accumulation_retention_12h >= 0.80`;
- `holdings_acceleration_12h_pct_per_hour > 0`.

It predicts a positive next-executable four-hour return. This separates momentum participation from the early-divergence theory that the pilot could not validate.

### H5 — Buyer-breadth and exchange-outflow confirmation

`buyer-breadth-exchange-confirmation-v1` is recorded as blocked, not evaluated. It requires complete historical or prospective BUY-address pages and point-in-time exchange-flow evidence that are absent from the frozen bundle. `holders_count` cannot substitute for buyer breadth, and exchange co-movement cannot establish transfer attribution.

## Schema-v3 manifest

The manifest contains:

- `schema_version: 3`;
- stable `experiment_id`, title, status, created time, and hypothesis text;
- `source_signal_manifest` and exact SHA-256;
- `evaluation_window` with explicit inclusive start and exclusive end;
- explicit time blocks used only for stability reporting;
- cost scenarios with stable IDs and per-side basis points;
- execution proxy version, one-hour entry lag, and non-overlap policy;
- declarative theory records with stable ID, role, holding period, objective, and `all` predicates;
- paper-feasibility gates and prospective validation gates.

Predicate records use `{feature, operator, value, lag_hours}`. Operators are restricted to `eq`, `in`, `gt`, `gte`, `lt`, and `lte`. `lag_hours` may be zero or a positive integer and must resolve to an exact same-token timestamp. Predicate features must be trailing metric fields returned by `signal_fieldnames()`; identity, selection, label, outcome, MFE, and MAE fields are rejected.

The schema-v3 loader uses lexical normalization before symlink resolution, requires the source to be a direct sibling experiment, verifies the source hash, and invokes the schema-v2 loader. A schema-v3 manifest over an `unknown` source must be `discovery`; it cannot assert holdout or validated status.

## Evaluation semantics

For a signal available at hourly `bucket_end = t` and holding period `h`:

1. predicates use only signal rows at or before `t`;
2. the executable proxy entry is the source price at exactly `t + 1 hour`;
3. the exit is the source price at exactly `t + 1 hour + h`;
4. missing or non-contiguous entry/exit history makes the episode unavailable;
5. a token cannot open another episode before the previous fixed exit;
6. time blocks are chronological and never random;
7. late censored events are omitted, never filled with zero.

This one-hour lag is deliberately conservative for the frozen diagnostic. Prospective paper trading will use the first timestamped executable quote at least five minutes after feature availability, not this hourly proxy.

Gross entry return is `exit_price / entry_price - 1`. For per-side cost `c`, net entry return is `(exit_price / entry_price) * (1 - c) * (1 - c) - 1`. Veto avoided-loss benefit is `-gross_return - 2*c`, because the paper policy models one exit and one later re-entry. The base scenario is 100 basis points per side and the stress scenario is 250 basis points per side. Both remain sensitivities because the frozen bundle has no route quotes, gas, or historical point-in-time liquidity.

## Aggregation and selection

Every theory summary reports event count, token count, chronological-block incidence, event mean/median/win rate, token-equal mean, maximum token P&L contribution, and gross/base/stress objectives. Equal-token aggregation prevents a high-frequency token from dominating the headline metric.

Paper selection is deliberately a low bar for choosing what to test, not a statistical validation gate:

- an entry theory needs at least five mature non-overlapping events across three tokens, positive base-cost token-equal mean and event median, and positive base-cost token-equal mean in at least two nonempty time blocks;
- eligible entry theories rank by base-cost token-equal mean, then stable theory ID;
- at most one entry theory is selected;
- the veto needs at least three mature events across three tokens plus positive base-cost token-equal mean and median avoided-loss benefit;
- H0 is benchmark-only, H3 is comparison-only, and H5 is blocked;
- an `unknown` discovery source can produce `selected_for_paper_discovery` but never `validated`, `holdout_winner`, or a capital-trading recommendation.

If no theory clears the paper-feasibility gate, the output selects none and records the failed criteria. The code must not force a winner.

Advancement beyond paper requires at least eight calendar weeks, 100 simulated fills across at least 20 tokens, timestamped quotes, point-in-time liquidity, positive mean and median after actual simulated costs, a positive lower one-sided 95% token/week block-bootstrap bound, non-negative stress expectancy, at least 70% fill rate, and no token contributing more than 20% of total P&L.

## Outputs

The bundle contains deterministic:

- `derived/theory-events.csv`: every mature, non-overlapping episode and its gross/base/stress objective;
- `derived/theory-summary.csv`: per-theory and per-time-block metrics plus gate results;
- `derived/paper-strategies.json`: `mode: paper_only`, source guarantee, selected entry/veto IDs, unselected and blocked IDs, reason codes, execution assumptions, and prospective advancement gates.

The JSON contains strategy definitions only. It contains no current token order, wallet, account, venue, secret, position submission, or claim that a historical token remains actionable.

`./nansen-lab evaluate --manifest PATH` writes the outputs atomically. `--check` rebuilds them and requires byte identity. The command is offline by construction.

## Paper policy emitted for selected theories

The paper-only policy uses:

- signal time `max(bucket_end, provider_available_at)`;
- first independently captured executable quote at least five minutes later;
- quote age at most 60 seconds;
- fixed four-hour exit;
- one open episode per token;
- virtual notional `min($1,000, 0.001 * point_in_time_liquidity_usd)`;
- unfilled/cash status for missing routes or one-way quoted cost above 2.5%;
- explicit fee, gas, spread, and slippage recording;
- no real order submission.

## Testing

Development follows strict red-green-refactor cycles. Tests cover:

- strict schema-v3 keys, types, source containment, source hash, and discovery/holdout guarantee matrix;
- predicate field/operator/lag whitelists and proof that label-side fields cannot influence selection;
- exact-hour lag resolution, conservative entry/exit pricing, maturity, gaps, and same-token cooldown;
- literal hand-calculated entry and veto cost formulas;
- token-equal aggregation, block metrics, deterministic tie-breaking, comparison-arm spread, and no-forced-winner behavior;
- paper-only vocabulary and absence of order/account fields;
- deterministic write/check behavior and offline CLI wiring;
- full regression suite and unchanged frozen schema-v1/schema-v2 hashes.

## Follow-up evidence path

After the paper-only shortlist is frozen, a separate collection project should use Nansen's beta backtesting family where its endpoint contract explicitly supplies historical label vintages and point-in-time universe state. Preferred evidence is historical Token Screener, historical Smart-Money balances, historical who-bought/sold, historical token-flow summary, OHLCV, and historical DEX trades. Every page, request, retrieval time, schema version, completeness marker, and hash must be archived. Legacy history receives `provider_pit` only where the official endpoint contract explicitly promises temporal labels; otherwise it remains discovery evidence.
