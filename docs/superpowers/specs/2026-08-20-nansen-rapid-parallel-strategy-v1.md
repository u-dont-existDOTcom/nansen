# Nansen rapid parallel-strategy prospective program v1

Status: frozen exact protocol approved for offline preregistration. This
document alone authorizes no public or authenticated provider request. The
byte-exact implementation, fixtures, runtime closure, crash/replay lifecycle,
accounting chain, automation design, and independent blocker/high review have
passed their pre-initialization gates. The generated preregistration and its
exact source must still be committed, replayed, and activated before the first
scheduled request.

## Objective, supersession, and independence boundary

This is a separately named rapid prospective program. It evaluates all exact
eleven pre-live strategy candidates concurrently and offline from one shared,
outcome-independent evidence panel, selects at most one challenger to the
already authorized a-priori `c01` anchor, tests the resulting family of one or
two rules once on a later time- and identity-disjoint validation partition, and
advances at most one rule. It estimates execution-aware paper returns. It makes
no causal, live-route, gas-inclusive, guaranteed-profitability, capital-
deployment, or replication claim.

Program ID:
`2026-08-20-rapid-prospective-parallel-strategy-v1`.

Portfolio ID:
`2026-08-20-rapid-independent-prospective-strategy-portfolio-v1`.

This rapid program supersedes only the future live authority and October
calendar of the frozen preregistration
`2026-08-19-prospective-parallel-strategy-v1`. The October preregistration
remains immutable: this program never edits, deletes, resumes, or executes it,
and never reads or uses any of its token panels, features, decisions, outcomes,
calibration rows, scores, rankings, or shortlists. No October outcome may add,
remove, rank, threshold, validate, or otherwise tune a rapid-program candidate.

Program A and A2 are terminal and immutable. This program must not read or use
their token panels, features, outcomes, calibration rows, scores, rankings, or
shortlists. Its candidate grammar is copied from the exact Program-A candidate
contract at pre-live commit `610f31c` that existed before Program-A provider
access, SHA-256
`aa4d1085a0b3594a8a255584e0aec7a0bdab0a6438bcc63b7af0076a9f5d056a`.
That contract is definition provenance only; no A/A2 result can add, remove,
rank, threshold, or otherwise tune a candidate. This program is not A3 or B2.

The owner aborted the former 32-cycle cohort v1 after its exact first cycle:
two authenticated attempts and one billable credit. That owner-aborted state is
a hard activation fact, not a terminal-v1 claim. The rapid program must bind an
exact operational replay of the cycle-one account/screener budget evidence and
proof that the former cohort has no remaining live authority before its first
public or authenticated request. It must never describe the abort as successful
completion, a terminal cohort result, or a terminal-v1 prerequisite. The former
cohort is never resumed, killed, edited, raced, or used for scientific input.

## Schedule, purge, identity partitions, and result timing

The frozen paid schedule is:

```text
first discovery cycle  2026-08-22T12:05:00Z
discovery cycles        1..42, every 8h
last discovery cycle   2026-09-05T04:05:00Z
phase transition       32h start-to-start; three empty 8h grid positions
first validation cycle 2026-09-06T12:05:00Z
validation cycles       43..85, every 8h
last validation cycle  2026-09-20T12:05:00Z
```

The 32-hour phase gap exceeds the conservative 31h10 dependency span from the
earliest admitted feature bound to the latest possible outcome end. Empty purge
positions make no public or authenticated request and are not observations or
missing cycles. All paid observations remain on the eight-hour grid.

The last discovery cycle cannot terminalize later than its absolute hard stop,
`2026-09-05T12:04:40Z`, which is 13d23h59m40s after the first discovery start.
Only then may the program expose a preliminary discovery lead and freeze the
family `{c01}` or `{c01, top_challenger}`. That approximately 14-day lead is
selection-stage, descriptive, and not a validated result. It must carry its
discovery support and economic metrics and the explicit label `preliminary`.

The last validation cycle cannot terminalize later than
`2026-09-20T20:04:40Z`, 29d7h59m40s after the first discovery start. Only after
all validation cycles terminal and exact offline replay succeeds may the
program produce a validated paper result. A result at approximately 29 days
may still be `no_rule`, `unscorable`, or `fatal`; even a validated paper winner
does not guarantee profitability, live execution, independent confirmation, or
temporal replication.

Every physical token is permanently assigned before the first request:

```text
salt bytes:
2026-08-20-rapid-independent-prospective-strategy-portfolio-v1|identity-partition-v1
salt SHA-256:
7b1785558e04520c918d612a22f5e47841ff1e5dd56bb36294a5fa286621b2ba
```

Normalize response `bsc` to request-native `bnb`; lowercase Ethereum, Base,
and BNB addresses regardless of prefix; preserve Solana case. Compute
`SHA256(salt || NUL || chain || NUL || address)`. The top two digest bits map
`00/01/10/11` to discovery, validation, later primary confirmation, and later
temporal replication. Borrowing across partitions is forbidden.

## Deterministic 13-token sampling

Each cycle requests exactly page one of the production screener using Smart
Money, non-stablecoin, age >=3d, market-cap >=1m, liquidity >=250k, and the
Ethereum/Solana/Base/BNB domain. The page must be final, contain no more than
1,000 rows, and contain unique normalized identities. Every non-null numeric
field must be a finite non-boolean number; raw net flow must be non-increasing
with nulls only as a suffix; and `abs(price_change) <= 20`. A numeric provider
semantics violation is program-fatal. A legitimately null required field makes
only its row ineligible and never becomes zero.

After phase partitioning, require at least 13 eligible identities. Sort by
`netflow/market_cap` descending, then normalized chain/address ascending. For
band `i in [0,12]`, use rows
`[floor(i*N/13), floor((i+1)*N/13))`. Select one row per band by ascending:

1. prior sealed selection count for that identity in the phase;
2. prior sealed selection count for its chain in the phase;
3. `SHA256(program_id || NUL || phase || NUL || cycle || NUL || identity)`;
4. normalized chain/address.

Counts are snapshotted before the cycle and do not change between its bands.
Archive source rank, partition rank, band bounds, hash, and prior counts. Fewer
than 13 phase identities or an empty band makes the cycle unscorable before
token calls. There is no page two, replacement, reroll, relaxed filter, or
cross-partition borrowing.

## Candidate family and concurrent offline semantics

All eleven non-cash candidates are fixed before any request and evaluated
concurrently offline for every selected opportunity from the same shared
evidence panel:

1. `c01-buyer-breadth-exchange`: positive screener flow, BUY addresses > SELL
   addresses, negative exchange net flow;
2. `c02-buyer-volume-exchange`: positive screener flow, BUY USD > SELL USD,
   negative exchange net flow;
3. `c03-early-breadth-divergence`: positive screener flow, buyer breadth, price
   change <=0;
4. `c04-early-exchange-divergence`: positive screener flow, negative exchange
   net flow, price change <=0;
5. `c05-breadth-continuation`: positive screener flow, buyer breadth, price
   change in `(0,0.15]`;
6. `c06-top-pnl-confirmation`: positive screener flow, negative exchange net
   flow, positive top-PnL flow;
7. `c07-smart-trader-confirmation`: positive screener flow, negative exchange
   net flow, positive Smart-Trader flow;
8. `c08-three-segment-consensus`: negative exchange net flow, positive
   Smart-Trader flow, positive whale flow;
9. `c09-fresh-wallet-confirmation`: positive screener flow, negative exchange
   net flow, positive latest-as-of fresh-wallet flow;
10. `c10-buyer-breadth-benchmark`: positive screener flow and buyer breadth;
11. `c11-screener-accumulation-benchmark`: positive screener flow.

Candidate predicates, sources, operators, tri-state availability, and exact
prospective crosswalk are machine-readable. `c01` is the a-priori candidate.
All eleven decisions are computed and sealed for every selected opportunity
before outcome availability. This is parallel offline evaluation of one shared
evidence panel, never parallel network transmission.

Use strong-Kleene conjunction: false dominates unavailable. Evaluate candidate
predicates first; a false candidate abstains without consulting vetoes. A true
candidate then applies two risk vetoes:

- selling pressure: seller addresses > buyer addresses, SELL USD > BUY USD,
  and price change <=0;
- prospective distribution: exact source market phase is `markdown` or
  `distribution_divergence`, distribution persistence >=0.75, four-hour
  holdings acceleration <0, and four-hour holder-count change <0.

A true veto abstains and can never create a LONG. A needed unavailable value
makes the decision unavailable. Only a true candidate with both vetoes
decisively false is LONG. The exact recognized market phases are
`accumulation_divergence`, `markup`, `distribution_divergence`, `markdown`, and
`flat`; an unknown phase is invalid evidence, not false.

## Point-in-time evidence and request ceiling

Each selected token has an eleven-credit maximum:

- one completed Smart-Money `tgm/flows` request;
- one `tgm/flow-intelligence` request;
- WBS BUY and SELL, at most two pages each;
- DEX BUY and SELL, at most two pages each; and
- one exact five-minute OHLCV request.

Do not also request exchange-labelled `tgm/flows`. Conditional page two is
requested only after sealed page one says nonfinal. Page two must be final;
page three is forbidden. Every response is bound to the scheduled event,
identity, endpoint, request bytes/hash, side where applicable, response
bytes/hash, retrieval time, and budget entry before it can influence a decision
or outcome.

At scheduled time `S`, Smart-Money flow requests the 26-hour buffer ending one
microsecond before the completed-hour boundary and admits the exact trailing 25
completed rows. WBS uses `[S-24h,S)`. Flow-intelligence uses `timeframe=1d`,
must be freshly retrieved during the decision window with `cache_hit=false` and
no warnings, and must contain finite-or-null signed segments. Its pinned body
does not prove provider snapshot age or necessarily echo identity; identity is
therefore bound by the sealed request and receipt-time semantics are reported
as a limitation. A candidate-required null makes its primitive unavailable.

Each cycle has two account/budget epochs:

```text
predecision: account + screener + 13*(SM flow + FI + max4 WBS)
             80 authenticated attempts, 79 credits
settlement:  account + 13*(max4 DEX + OHLCV)
             66 authenticated attempts, 65 credits
cycle:       146 authenticated attempts, 144 credits
program:     12,410 authenticated attempts, 12,240 credits
```

Every reservation and zero-cost account transmission consumes an attempt.
Retries, retransmission of ambiguous attempts, replacement, and repair calls
are forbidden. Contract/pricing drift, malformed pricing, balance
discontinuity, a ceiling breach, non-object/malformed charged response, or
possible transmission without a complete response terminalizes the program
before another request.

The first account baseline must match the exact finite balance set reconstructed
from terminal A/A2 operational ledgers and the owner-aborted cohort-v1
cycle-one operational proof, and must be at least 42,945 credits: 12,240 for
this program, 13,786 for a later 113-cycle primary holdout, 16,592 for a later
136-cycle temporal replication, and a permanent 327-credit safety margin. The
observed alternative is then frozen. Every later epoch must equal that baseline
minus exact conservative settled/reserved rapid-program spend. A surplus never
expands authority. No other Nansen job may retain live authority after the
owner-abort activation prerequisite except this program's serialized actions
under the shared provider lock. Verified page-depth underspend may roll only
into additional fixed-cadence temporal-replication cycles through a ledger-only
formula frozen before primary/replication performance is unlocked; it never
returns to rule search.

The reconstruction opens at the provider-proved 50,063-credit snapshot taken
after cohort-v1 cycle one's one-credit screener and immediately before Program
A. It never subtracts that cycle-one credit or any pre-snapshot work again. It
binds the owner-aborted cohort-v1 operational replay at exactly two attempts and
one credit and makes no terminal-v1 claim. It binds Program A's terminal seal
SHA-256
`3132f1bfaa5e99d535bd6ded819f9751a44bede2f6dbf0bf60689cd0c9c49230`
and conservative 537 credits/135 attempts. A2's first 49,531-credit account
proof resolves A's terminal five-credit ambiguity as uncharged, so the
post-snapshot reconstruction subtracts A's 532 confirmed credits and zero
reserve. It binds Program A2's terminal seal SHA-256
`5f58ce65563be7f4ab909b3b00fa2bbac5eba590d9b534f8216b14043181d230`
and 4,829 conservative credits/1,219 attempts. A2 proves 4,828 settled credits;
its terminal HTTP 500 retains the exact unresolved set `{0,1}`. With no
post-snapshot cohort-v1 spend, the exact first-baseline set is therefore
`{44,702, 44,703}`. Its lower branch exceeds the 42,945-credit frozen future-
authority floor by 1,757 credits. These predecessor inputs are operational
accounting only; no predecessor or superseded-October panel, feature, decision,
outcome, rank, or return enters this program.

## Timing and counterfactual outcomes

A cycle starts only in `[S,S+15m]`, seals decisions by `S+45m`, and admits no
new predecision transport at or after `S+43m30s`. `t0` is the first five-minute
boundary strictly after decision computation and must be sealed in
`[S+5m,S+50m]` before outcome availability.

For every selected opportunity regardless of every candidate decision:

- BUY DEX window `[t0+5m,t0+15m)`;
- SELL DEX window `[t0+4h5m,t0+4h15m)`;
- OHLCV closed five-minute grid `t0..t0+4h15m`;
- settlement admission no earlier than `t0+4h21m`;
- no settlement transport at or after `S+7h58m30s`;
- absolute action hard stop `S+7h59m40s`.

Virtual notional is `min($1,000,0.001*sealed_liquidity)`. Chronological BUY
liquidity fills USD notional and chronological SELL liquidity exits the token
amount actually acquired, including partial entries. Preserve requested and
observed token/USD amounts, ratios, VWAP, trade counts, partials, and provenance.
Unfilled, partial, and unavailable are never zero returns. Strategy scoring
requires complete entry and exit. Base/stress returns are
`(exitUSD/notional)*(1-cost)^2-1`, with 1% and 2.5% cost per side. OHLCV is a
common diagnostic and never substitutes for DEX fills.

## Discovery support and shortlist

Discovery has six fixed seven-cycle blocks. All 42 cycles must be terminal;
at least 38 cycles must be complete; at least 492/546 planned slots must be
selected; and at least 95% of selected opportunities must have common OHLCV.

A candidate survives discovery only with:

- decision availability >=90%;
- >=30 fully scored LONGs across >=20 physical tokens and >=2 UTC weeks;
- complete-entry/exit score rate among LONGs >=70%;
- all six blocks represented and >=4 positive block medians;
- positive token-equal base mean, event base median, token-equal stress mean,
  and leave-one-token-out minimum base mean;
- maximum scored shares: token <=20%, block <=30%, chain <=60%; and
- no missing return among events classified as fully filled/scored.

The deterministic discovery bootstrap uses 10,000 Python `random.Random`
MT19937 replicates. Seed with the big-endian first eight bytes of
`SHA256(program_id|discovery|candidate_id|block-token-bootstrap-v1)`. Each
replicate samples six block labels with replacement, carries all events for
each block occurrence, samples with replacement the same number of distinct
physical tokens in the carried population, computes each sampled token's mean,
and averages those token means. Sort results and use zero-based index 249.
Every block must contain a scored event.

Rank passing challengers `c02..c11` by numeric stress mean, base mean, event
median, bootstrap lower bound, positive block count, lower token/block/chain
shares, scored count, then candidate ID ascending. Freeze at most the top
challenger. The a-priori `c01` anchor enters validation regardless of its
discovery performance provided its machine crosswalk is supported; discovery
metrics remain descriptive and cannot tune it. The validation family is
therefore exactly `{c01}` or `{c01, top_challenger}`. Seal it atomically after
all 42 discovery cycles terminal; no validation request may occur before that
seal. No qualifying challenger yields a valid `c01`-only validation family,
not an adaptive substitute or a no-survivor stop. There is no fallback,
second-best promotion, or replacement challenger after the family is frozen.

## Untouched validation and multiplicity

Validation has six blocks of 7/7/7/7/7/8 cycles. All 43 cycles must be
terminal; at least 39 complete; at least 504/559 slots selected; and common
OHLCV coverage >=95%. Each of the one or two frozen-family candidates requires
decision availability >=95%, >=70 scored LONGs across >=25 tokens and >=2 UTC
weeks, score rate >=70%, all six blocks represented with >=4 positive block
medians, token share <=15%, week share <=60%, chain share <=60%, and positive
base, median, stress, and leave-one-token-out metrics.

Each candidate uses the same 10,000-replicate algorithm with phase
`validation`. The maximum formal family has two members; freeze one-sided
alpha=.025 per member (Bonferroni family-wise alpha<=.05) before discovery.
Use the sorted replicate at zero-based index 249 for each candidate, including
when no challenger qualifies. Passing requires that bound >0 in addition to
every economic/support gate.

If both candidates pass, choose exactly one by validation stress mean, base
mean, bootstrap lower bound, event median, lower token/week/chain shares,
scored count, then candidate ID ascending. No fallback, second-best promotion,
rule change, additional candidate, or outcome-dependent extension is permitted.
A supported phase with no passing candidate yields `no_rule`; insufficient
phase support yields `unscorable`; provider/contract/budget failure yields
`fatal`. Only a validated winner may seed separately frozen later confirmation
and temporal-replication programs.

Routine status reveals only schedule/stage/completeness/attempts/credits and
failure codes. Discovery aggregation and shortlist sealing are atomic after all
42 discovery cycles terminal. Validation aggregation refuses before all 43
validation cycles terminal. Blinding is application-level, not cryptographic;
fixed schedules and no optional stopping prevent peeking from changing calls.

## Automation, replay, and freeze gates

Implementation uses new-only paths outside every frozen v1/A/A2 and superseded
October source/runtime set. It archives and hashes this spec, the exact candidate
contract, exact OpenAPI, imported source/dependency closure, scripts, units,
tests, schedule, partition vectors, request plan, runtime versions, the exact
owner-abort/cycle-one operational proof, and the A/A2 operational reconciliation.
Every live action and offline replay refuses drift. All derived stage/final
claims are recomputed from raw, hash-bound evidence and budget journals;
existence of a seal is never sufficient adoption proof.

One persistent minute timer uses absolute paths, synchronized-time and network
gates only for live actions, bounded overdue offline transitions, a state-
progress guard, the shared provider mutex, stable runtime-directory lifetime,
no randomized delay, no automatic retries, and user lingering. Missed windows
terminalize deterministically and never backfill market evidence. It must also
prove that the owner-aborted cohort and superseded October program retain no
live timer, service, process, or provider authority before activation and on
every rapid-program live action.

Before initialization, tests must cover: exact rapid calendar/purge; partition
and selection golden vectors; all eleven exact crosswalks; exhaustive tri-state
truth tables; point-in-time payloads; BSC/BNB response normalization; malformed
numerics; conditional pagination; partial fills; audited outcome provenance;
79/65/144/12,240 credit and 12,410-attempt arithmetic; timing boundaries;
10,000-replicate nonconstant bootstrap golden vectors; fixed two-member
Bonferroni behavior; anchor/challenger/tie/no-challenger branches; exact phase
denominators; crash injection after reservation/request/response/failure/seal;
tamper/global-fatal replay; exact balance continuity; owner-aborted cohort-v1
cycle-one admission without a terminal-v1 claim; superseded-October isolation;
runtime/HEAD/source freeze; timer catch-up; lock/systemd/linger smoke; and
unchanged replay of A and A2 operational inputs. Independent blocker/high review
must pass before preregistration or any provider request.
