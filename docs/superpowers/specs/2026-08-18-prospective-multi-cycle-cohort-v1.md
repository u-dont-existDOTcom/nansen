# Prospective multi-cycle cohort v1

Status: offline implementation contract. No provider access is authorized by
this document. A live program may be initialized only after this design and
its implementation are committed and a fresh zero-credit account preflight
proves the full remaining program ceiling is funded.

## Objective and estimand

Build an execution-aware prospective holdout for previously frozen flow rules
and one newly frozen buyer-breadth/exchange-inventory co-movement rule. The
program estimates four-hour paper returns conditional on a rule emitting
`LONG`; it does not claim executable routing, causal transfer attribution, or
profitability before all advancement gates pass.

All 160 selected opportunities are holdout observations. No parameter may be
trained, tuned, or selected from their outcomes. A theory learned from these
outcomes requires a new prospective cohort.

The existing H0-H4 comparator definitions bind exact manifest bytes with
SHA-256 `5d5859be0c03bd1f786436ad199aac48de9c6688883392836796c0f8e3ccf6d5`.

## Program and schedule

- 32 cycles, five tokens per cycle, 160 opportunities.
- The first cycle is an explicit RFC 3339 UTC timestamp aligned to minute 05,
  second 00. Subsequent cycles are exactly 44 hours apart. The 32 observations
  span 56 days 20 hours and therefore satisfy the eight-week time gate without
  adding a 33rd cycle or exceeding the approved budget.
- Each scheduled timestamp is the evidence-collection start. Starting a missed
  cycle more than 15 minutes late makes it unscorable; it is not rescheduled or
  replaced. The flow-feature cutoff is the top of the UTC hour immediately
  preceding that scheduled `HH:05` start. The common decision `t0` is the first
  five-minute boundary after all five feature sets and decisions are computed.
  The immutable decision seal must precede `t0` and occur no later than 45
  minutes after the scheduled start. Provider access stops once that deadline
  passes. A slow or stale crash-resume therefore terminalizes instead of
  turning old features or a past execution window into a backtest.
- A cycle progresses append-only through `planned`, `universe_sealed`,
  `features_sealed`, `decisions_sealed`, and `outcome_sealed`, or terminates
  `unscorable`. Earlier seals are never rewritten.
- Performance aggregation remains locked until all 32 cycles are terminal.
  Routine progress exposes only stage, completeness, and budget totals.

## Pinned contract and budget

The program binds the exact local Nansen OpenAPI bytes with SHA-256
`d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`.
All six billable endpoints below cost one credit on the pinned Free and Pro
plans.

Maximum per cycle:

```text
1 token-screener
5 * (
  1 smart-money flow + 1 exchange flow
  + 2 BUY breadth pages + 2 SELL breadth pages
  + 2 entry DEX pages + 2 exit DEX pages
  + 1 OHLCV
)
= 56 credits
```

The hard program ceiling is 1,792 billable credits. One zero-credit account
preflight per cycle makes the authenticated-attempt ceilings 57 per cycle and
1,824 for the program. There are no automatic retries, repair calls, second
screener pages, replacement candidates, or rerolls. A cycle account preflight
must prove at least `56 * remaining_cycles` credits before that cycle can make
a billable call. Provider-caused pricing drift is preserved and terminalized;
it is never hidden by rewriting the authorized ceiling.

## Universe and deterministic panel

The exact screener payload is:

```json
{"chains":["solana","ethereum","base","bnb","arbitrum"],"timeframe":"24h","pagination":{"page":1,"per_page":1000},"filters":{"trader_type":"sm","include_stablecoins":false,"token_age_days":{"min":3},"market_cap_usd":{"min":1000000},"liquidity":{"min":250000}},"order_by":[{"field":"netflow","direction":"DESC"}]}
```

Page one must declare `is_last_page=true`; otherwise the cycle seals
`insufficient_universe` after the screener. Eligible rows require an approved
chain, unique normalized identity, non-empty symbol, finite positive price,
volume, liquidity and market cap, age at least three days, and finite signed
netflow and price change. The raw `price_change` is frozen as a decimal return;
its percent representation is raw times 100. A raw magnitude above 20 is
treated as a provider-semantics failure.

Selection is without replacement within a cycle. For every stratum, prefer
the lowest prior selection count, then the stratum score, lowercase chain, and
normalized address (lowercase only for EVM-style addresses):

1. `early_accumulation`: netflow > 0 and price_change <= 0.05; maximize
   netflow / market_cap.
2. `middle_accumulation`: netflow > 0 and 0.05 < price_change <= 0.15;
   maximize the ratio.
3. `momentum_accumulation`: netflow > 0 and price_change > 0.15; maximize the
   ratio.
4. `neutral_control`: minimize absolute ratio among unused rows.
5. `distribution_control`: netflow < 0; minimize the signed ratio.

Selection computation reserves the distribution control immediately after the
three accumulation strata, then chooses neutral from the still-unused rows;
the sealed panel is nevertheless emitted in the numbered order above. This
precedence prevents a lone negative-flow candidate from being consumed as the
neutral control and is part of the frozen rule.

Prior counts use selections only, never missingness, fills, decisions, or
returns. An empty stratum seals `insufficient_strata` before token calls; no
fallback changes the estimand. Each token's virtual notional is
`min(1000 USD, 0.001 * screener liquidity USD)` and is sealed with the panel.

## Predecision evidence

For every selected token, collect and archive:

- `tgm/flows` for `smart_money` and `exchange`, from the completed-flow cutoff
  minus 26 hours to that cutoff minus one microsecond, page one/per-page
  1000/date ascending. Admit
  only the exact trailing 25 contiguous completed hourly observations ending
  at cutoff. The extra requested hour is a completeness buffer.
- `tgm/who-bought-sold` for BUY and SELL from the scheduled collection start
  minus 24 hours to that start minus one microsecond. Freeze labels `Fund`,
  `Smart Trader`, and
  `30D Smart Trader`, minimum trade volume USD zero, volume-descending order,
  and at most two complete pages of 1000. A non-final page two makes breadth
  unavailable and the token decision unavailable; it is never interpreted as
  an abstention or zero breadth.

Flow validation requires exact identity, complete pagination, positive price,
nonnegative holdings/value and counts, valid holder count, strict hourly
ordering, and `bucket_end <= cutoff`. Exchange DEX/CEX component fields are
preserved. Breadth validation requires exact pagination, unique normalized
addresses, and finite nonnegative directional volumes.

The following decisions are sealed before the entry window:

- Existing frozen H0-H4 rules are evaluated without changed thresholds.
- H5 v1 emits `LONG` only when all required evidence is available and:
  four-hour Smart-Money holdings change is positive; distinct BUY addresses
  exceed distinct SELL addresses; total BUY USD exceeds total SELL USD; and
  four-hour exchange inventory change is negative. This is named
  `buyer-breadth-exchange-comovement-v1`. It does not assert that exchange
  inventory transferred to the observed Smart-Money wallets.
- The frozen distribution veto can only turn a separately qualified `LONG`
  into `ABSTAIN`; it cannot create an entry.

## Counterfactual outcomes

Outcome evidence is collected for every selected token, independent of its
decision:

- BUY DEX trades in `[decision t0 + 5m, decision t0 + 15m)`.
- SELL DEX trades in `[decision t0 + 4h + 5m, decision t0 + 4h + 15m)`.
- Maximum two complete pages per side, 1000 rows per page. The request upper
  bound is represented as the half-open end minus one microsecond.
- One five-minute OHLCV request spanning the exact closed grid from
  `decision t0` through the exit-window end. `truncated=false`, exact token
  identity and timeframe, contiguous closed candles, positive OHLC, and
  nonnegative volume are required. The pinned object-valued market-cap candle
  is validated when present.

The entry fill consumes chronological BUY liquidity up to the frozen notional;
the exit consumes chronological SELL liquidity for the acquired token amount.
Duplicate trade-row ambiguity or incomplete pagination makes the cycle
unscorable; these are evidence-integrity failures, not fills. Insufficient
liquidity produces an unfilled outcome with the observed partial amount and
fill ratio, never a fabricated price. The dataset records observed VWAP gross
return, 100-bps base and 250-bps stress per-side returns, fill status, fill
ratio, MFE, MAE, and token/week identifiers. It is paper execution evidence,
not a route/fee/gas guarantee.

## Aggregation and advancement

Counts remain distinct: selected opportunities, rule signals, attempted
counterfactual fills, filled rule signals, and scored outcomes. A filled
ABSTAIN counterfactual is never counted as a strategy fill.

The sole confirmatory, advance-eligible rule is
`buyer-breadth-exchange-comovement-v1+distribution-veto`. Base H5 and all
legacy H0-H4 base/paired variants are reported descriptively but cannot
advance. This single-primary rule avoids selecting a false winner from an
unadjusted family of correlated strategy tests.

A rule can advance only if all are true:

- all 32 cycles are terminal and the observation window spans at least eight
  weeks;
- at least 100 filled rule signals across at least 20 unique tokens;
- at least 70% of that rule's signals fill and score;
- positive token-equal mean base return, positive event median base return,
  and positive token-equal mean stress return;
- no token contributes more than 20% of scored signals and no UTC week more
  than 25%;
- the lower bound of a deterministic token/week block-bootstrap 95% interval
  for token-equal base mean is positive; and
- all availability, budget, archive, and leakage checks pass.

The bootstrap seed is SHA-256 of the program id, interpreted as an unsigned
integer; it uses 10,000 replicates. Each replicate resamples UTC-week blocks,
pools each physical token's events across those sampled weeks, then resamples
the available physical-token blocks. The replicate statistic and point
statistic are both the equal mean of token-level event means. If fewer than
eight represented weeks or 20 tokens remain, the interval is unavailable and
the rule does not advance.

With 160 opportunities, this program may end `insufficient_strategy_fills`.
Counterfactual fills cannot satisfy the 100 strategy-fill gate. Expanding to a
64-cycle program would change the approved budget and requires a new design
and owner approval.

## Integrity and failure rules

All requests, raw responses, metadata, normalized features, decisions,
counterfactual outcomes, budget transitions, and seals are exact-byte archived
under one append-only program. Logical request ids are cycle- and token-
namespaced. Each response must match the expected endpoint, request identity,
credit price, chain/token identity, requested time bounds, and pagination.
Symlinks, path traversal, hash mismatch, ambiguous transmission, unexpected
extra artifacts, late starts, contract drift, pricing drift, or crash recovery
ambiguity fail closed. Recovery adopts only exact already-written bytes and
never retransmits an ambiguous request.

No GPT/OpenAI call participates in selection or decisions. The bottleneck is
point-in-time data and execution evidence, not model deliberation.
