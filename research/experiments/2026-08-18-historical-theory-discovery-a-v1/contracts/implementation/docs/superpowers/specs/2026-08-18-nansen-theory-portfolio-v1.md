# Nansen theory portfolio v1

Status: design draft. This document authorizes no provider request until the
corresponding implementation, fixtures, tests, runtime manifest, and exact
request plan are independently reviewed and committed. Each live phase must
then pass its own zero-credit account preflight before its first billable
request.

## Objective and separation

Use the owner's newly authorized Nansen balance to generate simple market
theories, select exactly one rule, and test it twice on untouched prospective
data. The portfolio is a funnel, not a sequence of retries until a favorable
result appears:

1. historical point-in-time discovery;
2. prospective discovery and validation;
3. one primary prospective holdout; and
4. one later, non-overlapping temporal replication.

The active `2026-08-18-prospective-multi-cycle-cohort-v1` program remains
byte-frozen. No artifact from it, including its cycle-one screener response,
may enter this portfolio's features, outcomes, thresholds, candidate ranking,
or reports. Its `insufficient_strata` result supplies only the operational
lesson that rare absolute panel strata are brittle.

The portfolio estimates execution-aware paper returns. It does not claim
causal transfer attribution, route availability, gas-inclusive profitability,
or permission to deploy capital.

## Portfolio budget firewall

The proved balance after active-cohort cycle one is 50,063 credits. The active
cohort retains a hard worst-case reserve of 1,736 credits (`31 * 56`). New
research receives a cumulative maximum of 48,000 credits, leaving a further
327-credit safety margin:

```text
50,063 - 1,736 - 48,000 = 327
```

| Program | Initial allocation credits |
|---|---:|
| A. Historical PIT discovery | 8,000 |
| B. Prospective discovery/validation | 12,240 |
| C. Primary prospective holdout | 13,786 |
| D. Temporal replication and fixed-rule extensions | 13,974 |
| **Total new research** | **48,000** |

One immutable initial allocation document binds these maxima. Actual use is
derived only from hash-bound terminal program-ledger snapshots; the portfolio
does not dual-write request truth into a second mutable ledger. A later
append-only transfer journal may debit verified terminal underspend from A-C
and credit exactly the same amount to D, with prefix recovery and a permanent
`sum(spend) <= 48,000` invariant. Every program has a separate append-only
request ledger inside its allocation. Before the
first billable request of a program, a zero-credit account response must prove
that provider remaining credits are at least:

```text
active-cohort remaining worst-case reserve
+ every unspent portfolio allocation
+ the 327-credit safety margin
```

Every priced response must preserve the exact remaining-balance chain and must
leave the active-cohort reserve plus safety margin intact. Pricing drift,
missing or malformed use/remaining evidence, an ambiguous transmitted request,
or a balance discontinuity stops the portfolio before another request. For the
historical beta family only, a missing quoted-cost header is acceptable when
all of the following are true and durably derived without changing the raw
metadata: the pinned OpenAPI contract says five, the reservation says five,
`credit_used` says five, and provider remaining decreases by exactly five.

The active cohort always has scheduling priority. Before Program A starts, the
operator must stop its user timer and prove both the timer and oneshot service
inactive. A crash-safe outer supervisor must restart the cohort timer after
Program A completes, terminalizes, or is stopped. Program A must itself stop
initiating public or authenticated requests at `2026-08-20T10:42:00Z`; its
supervisor sends a hard termination at `10:43:30Z`, and no process may remain
after `10:45:00Z`, twenty minutes before cycle two's scheduled start. The
90-second request-drain interval covers the pinned 60-second HTTP timeout plus
recovery margin even if the outer supervisor fails. Later portfolio timers use a shared
provider lock and yield throughout every active-cohort action window. No two
independent request ledgers may observe an interleaved provider-balance chain.

There are no replacement observations, rerolls, retries of ambiguous requests,
or outcome-dependent extensions. An explicit zero-use rate-limit response may
be retried once only if a separately frozen program permits it; Program A does
not. Verified page-depth underspend can roll only forward into additional
fixed-cadence observations of the already frozen replication rule. It can never
return to discovery or change a tested threshold.

## Pinned provider contract and runtime

Every program binds the exact full OpenAPI bytes archived by the active cohort,
SHA-256 `d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`.
Historical screener, flow-summary, who-bought-sold, DEX-trade, and beta OHLCV
requests cost five credits on the pinned plan. The production flow, flow
intelligence, who-bought-sold, DEX-trade, and OHLCV surfaces cost one credit.

Portfolio code lives outside `src/nansen_signal_lab`, `requirements.txt`, and
`nansen-lab` while the active cohort is running. It archives and hashes every
imported protocol-bearing source, this design, the OpenAPI document, the Python
version, and the installed versions of `httpx`, `httpcore`, `anyio`, `certifi`,
`idna`, `h11`, `typing_extensions`, `python-dotenv`, `pandas`, `numpy`,
`python-dateutil`, `pytz`, `tzdata`, and `six`. These are the direct runtime
packages plus the active Python 3.12 default HTTP transport and dataframe
dependency closures; optional extras are not imported. Live commands and replay
refuse any runtime drift before provider access. Before the first pending authenticated
transmission of every anchor invocation or process resume, the runner fetches
the public OpenAPI bytes without credentials, archives a new observation, and
requires the same SHA-256. A stale observation never authorizes a resumed
request. Contract drift terminalizes the whole program without spending and
forbids later anchors.

Request artifacts are installed before transmission. Exact raw bodies and
allowed headers are installed before a request ledger is settled. Logical IDs
are stable. Crash recovery adopts only exact complete response artifacts and
never retransmits a request whose transmission may already have begun. All
derived files, phase transitions, candidate sets, primary-rule selections, and
terminal reports are content-addressed and sealed append-only.

## Program A: historical point-in-time discovery

### Scope, dates, and budget

Program A uses 65 fixed Sunday anchors from `2025-05-18` through `2026-08-09`,
all after the provider's documented `2025-03-11` temporal exchange-label
coverage boundary and before the active cohort began. These dates are split
into fixed chronological discovery blocks for stability analysis; none is a
confirmatory holdout.

Maximum billable credits:

```text
65 anchors * 1 historical screener page * 5                  =   325
400 token-events * (1 flow + 1 BUY + 1 SELL page) * 5        = 6,000
65 preselected execution events * (1 entry + 1 exit) * 5     =   650
400 production OHLCV requests * 1                            =   400
                                                                  -----
                                                                  7,375
```

The phase allocation is 8,000, so 625 credits are unusable safety inside the
phase. Each anchor is a separate budget epoch with its own zero-credit account
baseline: 32 authenticated attempts/127 credits for each of the first ten
seven-event anchors and 28 attempts/111 credits for each remaining six-event
anchor. The program maximum is therefore 1,860 authenticated attempts and
7,375 credits. Per-anchor rebaselining prevents an external balance change
from being charged before detection; Program A still forbids any interleaved
provider job. Every reservation consumes an attempt slot, including the 65
confirmed-zero account preflights and a crash reservation whose transmission
status is not yet settled; zero pricing never reopens capacity. The historical
beta list endpoints expose no contract
tie-breaker after their single effective sort field. Program A therefore never
requests page two. A non-final who-bought-sold or DEX page makes that evidence
unavailable, never truncated evidence or a reason to reroll. Verified phase
underspend may later roll only to Program D.

### Historical universe

For each anchor date, request exactly page one of the historical screener with
this exact payload:

```json
{"to_date":"ANCHOR","timeframe_days":1,"chains":["ethereum","solana","base","bnb"],"trader_type":"sm","filters":{"market_cap_usd":{"min":1000000},"liquidity_usd":{"min":250000},"token_age_days":{"min":3}},"pagination":{"page":1,"per_page":1000},"order_by":[{"field":"netflow","direction":"DESC"}],"apply_blacklist_filter":false}
```

The page is explicitly the frozen, potentially capped, raw-netflow-descending
1,000-row prefix universe; `is_last_page=false` does not misrepresent it as
globally complete. All rank-stratum names below are prefix-relative, not
global per-chain extrema or representative controls.
It must identify page one/per-page 1,000, contain no more than 1,000 rows, and
have no duplicate normalized token identity.

Eligible rows require one of the four requested chains, a nonempty symbol,
finite positive price, market cap, liquidity and volume, token age at least
three days, finite signed netflow and price change, and absolute raw
`price_change <= 20`. A larger magnitude is provider-semantics failure for the
anchor, not an ordinary ineligible row.

The same pinned historical screener has previously returned contract-requested
`bnb` rows under the response spelling `bsc`. Program A freezes the source-only
normalization `bsc -> bnb`, preserves both spellings and the normalization rule
in every selected row, and uses contract-native `bnb` in all downstream beta
requests. No other chain alias is admitted. Response rows must be
non-increasing by finite raw netflow, with null-netflow rows only at the end,
or the anchor is a provider-semantics failure.

For each eligible chain, sort locally by signed `netflow / market_cap`, then
normalized token address. Four rotating rank strata are fixed:

- `upper_tail`: maximum score;
- `upper_middle`: row at floor 75% of the zero-based sorted range;
- `near_zero`: minimum absolute score, then address;
- `lower_tail`: minimum score.

The schedule contains 400 slots: seven slots on each of the first ten anchors
and six on each remaining anchor. Global slot `i` uses `chains[i mod 4]`, giving
exactly 100 slots per chain. Multiple same-chain slots on one anchor rotate
through distinct strata with `(anchor_index + chain_index + within-chain-slot)
mod 4`. If two scheduled strata resolve to the same row because a chain has too
few eligible rows, the later slot is unavailable; it is never replaced.
Repeated physical tokens across weeks remain distinct events and are handled
by token/week concentration statistics.

The 65 execution-calibration events are the planned event with the smallest
SHA-256 of the pre-token identity (`anchor|chain|stratum`) for each anchor.
This choice is sealed from the schedule before any screener response exists.

### Point-in-time features

For every selected token, the decision cutoff is the end of the anchor UTC
day. Request historical token-flow summary over that exact day. Request
historical who-bought-sold BUY and SELL over the same range, exactly page one
per side, `per_page=1000`, minimum directional trade volume zero, and all ten
contract-listed historical Smart-Money labels. BUY sorts by bought USD
descending and SELL by sold USD descending.

Only page one is ever requested. A non-final page makes the corresponding
breadth/volume primitives unavailable; truncated counts are never interpreted
as zero. Valid complete
responses yield distinct normalized buyer and seller addresses, buyer/seller
USD, token volume, and address-level concentration. Flow-summary fields retain
null as unavailable, distinct from zero, and expose signed smart-trader,
top-PnL, whale, public-figure, exchange, and fresh-wallet net flows plus wallet
counts. The contract defines positive exchange net flow as deposit-to-exchange;
therefore a negative value is an exchange-outflow feature, not proof of a
transfer to observed Smart-Money wallets.

Fresh-wallet flow means the latest provider snapshot on or before `date_to`;
the response exposes no snapshot timestamp. The primitive is therefore named
`fresh_latest_asof_positive`, never “anchor-day fresh flow,” and its unknown
staleness is a fixed discovery limitation.

Feature requests use the anchor-day cutoff and screener row; the explicitly
named fresh-wallet field retains the provider's latest-as-of semantics above.
No outcome or later label state may enter them.

### Outcomes and execution calibration

Common `t0` is `ANCHOR + 1 day at 00:00:00Z`. Every selected token receives one
production `tgm/token-ohlcv` request for the exact five-minute grid from `t0`
through `t0 + 4h15m`. Contract-native chain/address, `timeframe=5m`,
`truncated=false`, exact identity, contiguous timestamps,
positive OHLC, and nonnegative volume are required. Entry proxy is the close of
the `t0+10m` candle and exit proxy is the close of the `t0+4h10m` candle. This
production endpoint exposes no blacklist-as-of parameter, so the outcome is
explicitly discovery-biased calibration and cannot become PIT confirmation.

The 65 preselected calibration events additionally request historical DEX BUY
trades in `[t0+5m,t0+15m)` and SELL trades in `[t0+4h5m,t0+4h15m)`, ordered by
timestamp ascending, exactly page one of at most 1,000 per side. A non-final
page makes execution unavailable. After a complete page, equal timestamps are
ordered locally by transaction hash before fill simulation. Virtual notional is
`min(1000 USD, 0.001 * screener_liquidity_usd)`. Chronological BUY liquidity
fills that notional; chronological SELL liquidity exits the acquired token
amount. Every accepted DEX row must satisfy token amount times estimated price
equals estimated USD value within max(1%, USD 0.01); both entry tokens and exit
proceeds use proportional observed amount/value rather than recomputed
liquidity. Partial fills preserve observed amounts and fill ratios.

Report gross, 100-bps-per-side base, and 250-bps-per-side stress returns. DEX
execution results form a separately stratified proxy-bias/feasibility study
with chain, prefix stratum, liquidity, availability, and fill coverage. Sparse
calibration never enters candidate ranking. Program A does not claim executable
profitability.

### Frozen candidate library and ranking

Program A evaluates a finite library of simple monotone rules. Each entry uses
at most three positive predicates and a distinct, historically computable
selling-pressure veto named `historical-selling-pressure-v1`. It triggers only
when Smart-Money seller addresses exceed buyer addresses, SELL USD exceeds BUY
USD, and price change is nonpositive. This is not the frozen four-hour H1
distribution veto, which needs market phase, persistence, holdings acceleration,
and holder-count evidence that Program A does not collect. Primitive predicates
are:

- positive screener Smart-Money netflow/market-cap;
- Smart-Money buyer addresses exceed seller addresses;
- Smart-Money BUY USD exceeds SELL USD;
- exchange net flow is negative;
- smart-trader net flow is positive;
- top-PnL net flow is positive;
- whale net flow is positive;
- latest-as-of fresh-wallet net flow is positive;
- price change is nonpositive; or
- price change is positive and at most 15%.

The twelve fixed candidates are: buyer-breadth/exchange, buyer-volume/exchange,
early breadth divergence, early exchange divergence, breadth continuation,
top-PnL confirmation, smart-trader confirmation, three-segment consensus,
fresh-wallet confirmation, buyer-breadth benchmark, screener-accumulation
benchmark, and cash/no-signal benchmark. Exact Boolean definitions and every
availability dependency live in the sealed machine-readable candidate contract
and must pass a response-plan satisfiability test before provider access. The
historical selling-pressure veto may only turn a qualified LONG into ABSTAIN;
it never creates a trade.

Every predicate conjunction uses strong Kleene three-state logic: any `false`
operand makes the conjunction false even if another operand is unavailable;
otherwise any unavailable operand makes it unavailable; only all-true is true.
The candidate conjunction is evaluated first. A false candidate is ABSTAIN and
an unavailable candidate is UNAVAILABLE without consulting the veto. Only a
true candidate evaluates the veto: false veto is LONG, unavailable veto is
UNAVAILABLE, and true veto is ABSTAIN. Flow-summary predicates additionally
require `warnings_present` to be the literal boolean `false`; missing or
non-boolean warning state is unavailable.

Candidate statistics report all 400 planned slots and actual selected
opportunities separately. Selection coverage uses planned slots; candidate
decision availability and common-outcome coverage use selected opportunities.
UNAVAILABLE remains distinct from ABSTAIN. Reports include signals and availability,
common-outcome availability, physical tokens, chains, weeks, concentration,
and missing-signal tipping points. Rankings use token-equal base/stress means,
event medians, fixed calendar-block stability, and token/week/chain
concentration. No candidate can pass discovery support with fewer than 20
scored signals, ten physical tokens, eight weeks, 80% decision availability,
90% common OHLCV coverage, and 100% outcome availability for its emitted
signals. Up to five candidates are retained deterministically: the a-priori
buyer-breadth + positive Smart-Money flow + negative exchange-flow analogue
occupies one of the five slots, and at most four others enter by sealed
lexicographic ranking. Descending ranking order is: positive stress-mean flag,
positive event-median flag, positive calendar-block count, numeric token-equal
stress mean, numeric token-equal base mean, event median, lower maximum token
share, lower maximum week share, lower maximum chain share, and scored-signal
count; remaining ties use candidate ID ascending. No Program A result is
confirmatory.

Program A is scientifically complete only if at least 59 of 65 anchors are
complete, at least 320 of 400 planned slots select opportunities, and at least
90% of selected opportunities have common proxy outcomes. Otherwise it seals
the ranking and calibration descriptively but terminalizes as
`unscorable/insufficient_program_a_support`; an all-missing run can never be
reported as completed discovery. Any contract drift, account failure, pricing
or balance discontinuity, ambiguous transmission, or request-ledger ceiling
failure instead creates a program-fatal unscorable seal immediately. It never
continues to the next anchor. Any archived non-2xx provider response is likewise
a permanent program-global failure, including after a crash that occurred just
after its charged response was committed to the request ledger.

## Program B: prospective discovery and validation

Program B is separately implemented and committed only after Program A seals.
It runs 85 cycles exactly eight hours apart over 28 days, with 13 tokens per
cycle and a maximum of 144 credits per cycle (`1 + 13 * 11`), totaling 12,240.
Its first 42 cycles are candidate discovery and its final 43 are validation.

Broad page-one eligible sampling replaces rare absolute strata. Before B's
first cycle, one permanent protocol-salted hash maps every normalized
chain/address identity to exactly one of B-discovery, B-validation, C, or D;
the partitions can never be chosen after observing B. Rank/hash balancing is
also frozen. A purge interval at least as long as the maximum feature lookback
plus outcome window separates B-discovery from B-validation, B from C, and C
from D. Feature evidence uses Smart-Money completed flows, flow intelligence,
complete BUY/SELL breadth, and a separately frozen prospective distribution
veto. Counterfactual entry/exit DEX and
OHLCV evidence is collected for every selected opportunity independent of its
decision. Exact request windows, pagination, execution, costs, missingness,
source pinning, and crash rules are at least as strict as the active cohort.

Only Program A's sealed candidates are eligible. Before B's first request, its
implementation freezes numeric support, decision/outcome availability, fill,
token-equal base/stress, event-median, stability, concentration, multiplicity,
bootstrap, tie, and no-survivor rules. The candidate subset is sealed before
B-validation begins. Validation selects at most one passing rule. If none
passes, the portfolio may separately label the a-priori analogue as a failed-B
fallback for a newly frozen C/D test, but it cannot claim B validation. There
is no next-best fallback after observing later outcomes.

## Programs C and D: primary holdout and replication

Before Program C starts, freeze the exact selected rule, thresholds, feature
code, costs, identity partitions, cycle schedules, missingness handling,
bootstrap seed, and advancement gates. Program C runs 113 cycles exactly 12
hours apart over 56 days, 11 tokens per cycle, maximum 122 credits per cycle,
and maximum 13,786 credits.

Program D uses the same rule and code on its different permanent identity
partition and the immediately following non-overlapping 56-day block. Its base
113 cycles use at most 13,786 credits. Its initial allocation has 188 credits
above that base. Verified underspend rolled only forward from A through C may
be debited from its source allocation and credited to D, increasing D's dynamic
cap while the portfolio cap remains 48,000. The transferred amount may add
fixed 12-hour replication cycles. The extension count is computed and sealed
before Program C performance is unlocked; it cannot depend on returns.

Program C performance remains blinded until D is terminal. Each holdout must
independently satisfy all of:

- all scheduled cycles terminal and at least eight represented UTC weeks;
- at least 95% scorable selected opportunities;
- at least 95% primary-rule decision availability across selected opportunities,
  with unavailable decisions retained in the denominator;
- at least 150 filled primary-rule signals across at least 40 physical tokens;
- at least 70% of primary-rule signals fill and score;
- positive token-equal base mean, event median base return, and token-equal
  stress mean;
- no token above 10%, no UTC week above 20%, and no chain above 60% of scored
  signals;
- positive token-equal base mean after deleting the single best contributing
  physical token; and
- positive lower bound of the frozen deterministic token/week block-bootstrap
  95% interval for token-equal base mean.

Advancement requires both C and D to pass independently. Failure, insufficient
support, or unavailability ends the theory's confirmatory claim. It never
authorizes another threshold, replacement sample, or result-driven extension.

## Automation and visibility

Safe automated actions are account preflights, allocation checks, schedule
collision avoidance, request/response archiving, fixed pagination, feature and
decision seals, delayed settlements, offline candidate ranking, terminal-only
aggregation, runtime validation, and cost-only roll-forward to Program D.

During prospective programs, routine status exposes schedules, stages,
completeness, request counts, credit use, and failure codes only. Candidate
performance is unavailable until Program B's applicable freeze. Program C and
D performance remains unavailable until both are terminal. The active cohort's
performance remains unavailable until its own frozen terminal condition.

Each program requires an offline fixture lifecycle, independent blocker/high
review, a clean committed implementation, and an exact runtime manifest before
its first paid request. Those are engineering integrity gates, not owner-choice
pauses. The runner stops for owner advice only if completing the portfolio
would require changing its estimand, spending cap, active-cohort reserve,
capital boundary, or another substantive policy frozen here.
