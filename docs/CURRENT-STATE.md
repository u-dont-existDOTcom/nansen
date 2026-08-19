# Current state

Updated: 2026-08-19

## Goal

Find a strategy that survives prospective, execution-aware profitability gates.
The owner approved proceeding with the recommended Nansen cohort and an
approximately 1,800-credit evidence budget. The funded 32-cycle holdout is now
active. On 2026-08-18 the owner separately authorized continued theory
generation and evidence collection against the rest of the funded Nansen
balance; that authorization does not alter this holdout's frozen rules or cap.
On 2026-08-19 the owner explicitly authorized a new independent prospective
program that evaluates all promising preregistered strategies concurrently.
Concurrency means common, outcome-independent evidence collection followed by
parallel offline rule evaluation; provider transmissions remain serialized and
append-only. The new program must quarantine Program A/A2 market outcomes and
must not masquerade as their forbidden A3/B2 continuation.

## Verified baseline

- Offline cohort implementation: `22f5d2a` plus reviewed hardening checkpoint
  `a6367fb` and recovery checkpoint `ca714e3`.
- Frozen live preregistration: `248dd16`; cycle-one evidence: `dda2a9c`.
- External cohort automation: `e086a42`.
- Historical result baseline: `c91609b` plus terminal replay guard `cbd165c`.
- Historical holder-breadth recovery v2 is immutable `completed` and
  `does_not_advance`; do not rerun or tune it.
- Terminal manifest:
  `9b478afb4bbf1baec1b894526aaf52b37a77193016996b1811d35df67d1a64ef`.
- Cycle one's account preflight proved 50,064 credits before billable work;
  the provider reported 50,063 after its one-credit screener.
- Exact holder-breadth finding: positive token-equal aggregates were driven by
  a heavy-tail winner while the frozen event-median spread was negative.
- Result review:
  `docs/audits/2026-08-18-holder-breadth-historical-recovery-v2-result-review.md`.
- Prospective implementation review:
  `docs/audits/2026-08-18-prospective-multi-cycle-cohort-v1-review.md`.
- The pre-existing untracked `handoff.md` is not part of either checkpoint and
  remains untouched.

## Active cohort

- Program:
  `research/experiments/2026-08-18-prospective-multi-cycle-cohort-v1/program.json`.
- Cycle one was scheduled for `2026-08-18T15:05:00Z` and terminalized
  `unscorable` with the frozen reason `insufficient_strata`. No reroll or
  settlement is allowed or required.
- Because the frozen advancement gate requires all 32 cycles to seal outcomes,
  v1 can no longer advance after cycle one's unscorable result. The owner
  explicitly chose to complete it unchanged as a descriptive/training dataset.
- Verified cumulative use: 2 authenticated attempts, 1 billable credit, and no
  ceiling breach. The full funding gate passed before the screener.
- Cycle two is scheduled for `2026-08-20T11:05:00Z`.
- Operational constraint: invoke every cohort command with the absolute program
  path. The frozen CLI accepts a relative path, but once a cycle exists its
  offline tree verifier can loop while comparing absolute descendants to a
  relative root. Absolute-path replay/check passed. Do not patch the frozen
  implementation during the holdout.

## Active automation

- `nansen-signal-lab-cohort.timer` is installed under
  `/home/joel/.config/systemd/user/`, enabled, and active. It polls every UTC
  minute, but makes no provider call before a frozen action is due.
- Login lingering is enabled (`Linger=yes`), so the user manager survives logout
  and starts at boot. The unit copy is on `/home`, not the optional `nofail`
  `/mnt/hdd` mount; if the repository mount is temporarily absent, later timer
  polls retry after it appears.
- The first automatic poll at `2026-08-18T17:58:00Z` exited successfully with no
  due action. A separate host smoke proved the service sandbox, private runtime
  lock, NTP query, and NetworkManager readiness query.
- Installed service SHA-256:
  `b4fe3833d8767207faab023093cc557d21265c10e49330c8131f4b8bac42832f`.
  Installed timer SHA-256:
  `cde29e3e7c1ee832f576c76b029bfb8c837c7947bd58e05a956e08c694f7038b`.
- The supervisor drains overdue non-network terminalizations under one lock,
  then reaches any still-live cycle in the same invocation. It requires
  synchronized time, network readiness for paid actions, state progress after
  every command, and a successful absolute-path integrity check afterward.
- The machine must remain powered and awake around observation windows. The
  timer cannot reconstruct market evidence missed during power-off or suspend.

## Terminal theory-discovery programs

- Historical Program A is terminal after a provider-semantics 422 on the
  selected Solana USDC flow request. Its frozen successor A2 did not retry that
  event and started from original anchor seven.
- A2 terminalized `unscorable` after a charged/provider HTTP 500 at
  `a52-s05-ethereum-upper_tail/ohlcv`. Offline replay verifies 45 terminal
  anchors, 1,219 authenticated attempts, and 4,829 conservative billable
  credits. Terminal evidence is committed at `e176277`.
- A2's frozen machine B2 eligibility is false/empty, so the drafted B2 funnel
  cannot initialize, substitute a candidate, retry A2, or create A3. Its
  untracked code is an offline design reference only and authorizes no request.
- The new owner-authorized program is a separately named prospective protocol.
  It may reuse pre-existing candidate definitions and audited implementation
  primitives, but it may not inspect or use A/A2 token panels, features,
  outcomes, scores, rankings, or descriptive shortlists to choose its rules.

## Completed offline implementation

The append-only multi-cycle prospective cohort family now:

- remain separate from sealed schema-v4 GPT pilots and historical bundles;
- use fixed UTC cycles and deterministic five-token panels;
- collect complete Smart-Money and exchange flows plus BUY/SELL address breadth;
- collect counterfactual entry/exit DEX evidence and exact five-minute OHLCV for
  every candidate, independent of the strategy decision;
- freeze decisions before outcomes and aggregate only offline;
- enforce per-cycle and cumulative request/credit ceilings with no rerolls;
- keep opportunity count, strategy-signal count, fill count, and outcome count
  distinct;
- enforce the existing eight-week, 100-fill, 20-token, 70%-fill, stress,
  concentration, and token/week bootstrap gates without weakening them;
- treat buyer-breadth/exchange co-movement paired with the distribution veto as
  the sole confirmatory rule; all other frozen variants are descriptive;
- archive and validate exact protocol source, dependency versions, and
  self-contained comparator definitions before every live command and replay;
- recover provably untransmitted reservations without inventing request
  attempts, preserve partial-entry exit evidence, and retain sealed opportunity
  and decision counts when a later outcome stage becomes unscorable;
- enforce the pinned provider's signed-flow semantics, exact response bounds,
  page ceilings, pagination types, and frozen terminal reason codes.

## Budget boundary

The approved approximately 1,800-credit design is 32 cycles x 5 tokens, with a
maximum of 56 billable credits per cycle (1 screener plus at most 11 per token)
and 1,792 total. A zero-credit authenticated account preflight makes the hard
attempt ceiling 57 per cycle and 1,824 total attempts.

After cycle one's one-credit screener, v1 retains a maximum frozen reserve of
1,736 credits and the provider reported 50,063 remaining. The owner's broader
authorization reserves the approximately 48,327-credit headroom for separately
named, source-bound experiments. It must not be spent by enlarging, tuning, or
rerolling v1; each later protocol still needs its own fixed estimand, request
plan, ceiling, future sample, offline tests, and preregistration before paid
calls. "Use the credits" means evidence-bearing requests under those protocols,
not purposeless balance exhaustion.

This provides 160 candidate opportunities. It does not guarantee 100 strategy
signals. Implementation must therefore report `insufficient_strategy_fills`
rather than treating counterfactual fills as strategy fills. Increasing the
active program to 64 cycles is forbidden. A larger successor must be separately
designed, reviewed, and preregistered; the broader owner spend authorization
covers that later protocol without changing v1.

## Verification

- Full offline suite: 643 passed in 101.51 seconds.
- Complete cohort lifecycle suite: 26 passed.
- Cohort supervisor suite: 8 passed.
- `compileall` and `git diff --check`: passed.
- Exact cohort design SHA-256:
  `891fb3ba5307723f79d0413fc8d45957b1d11f966e5f920fa40ee732de7a230a`.
- Exact contract SHA-256:
  `d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`.
- Frozen strategy SHA-256:
  `5d5859be0c03bd1f786436ad199aac48de9c6688883392836796c0f8e3ccf6d5`.
- Offline implementation verification made no live calls; active cohort usage
  is recorded separately above.

## Next safe action

No manual cycle command is currently due. The enabled timer will start cycle two
at `2026-08-20T11:05:00Z`; the allowed start window ends at `11:20:00Z`. If
decisions seal, it will read and honor the exact `earliest_settlement` timestamp
from that sealed artifact rather than assuming a four-hour boundary. Verify and
commit newly created evidence at the next durable checkpoint; the timer itself
does not stage, commit, push, or publish.

In parallel, freeze and independently review the newly authorized post-v1
multi-strategy prospective protocol. Use common counterfactual evidence to
evaluate the full predeclared candidate family concurrently, but serialize
every provider transmission and select at most one rule through a purged,
identity-disjoint discovery/validation design. Require replay-valid terminal v1
before that program's first request, so its balance epochs cannot race v1.
Before any manual intervention, stop the timer and confirm its oneshot service
is inactive so it cannot race an operator command.
