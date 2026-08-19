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

## Preregistered parallel-strategy program

- Program:
  `research/experiments/2026-08-19-prospective-parallel-strategy-v1/program.json`.
- Preregistered manifest SHA-256:
  `17d7cf6be19e48d095fc27ef080223b293706048bb5d4c07855bfed589811bb3`.
- Source checkpoints: implementation `870eaf6`, direct-entrypoint hardening
  `dfb81e3`, and inert preregistration bundle `6143d73`.
- Stage is `preregistered`; terminal-v1 activation evidence is intentionally
  absent. Initialization made no public or authenticated provider request and
  authorizes none.
- The program evaluates all eleven pre-live fixed non-cash rules from candidate
  contract SHA-256
  `aa4d1085a0b3594a8a255584e0aec7a0bdab0a6438bcc63b7af0076a9f5d056a`
  on one common evidence panel. Provider requests are serialized; rule
  evaluation is parallel and offline. `c01` is the a-priori anchor, at most one
  discovery challenger joins it in validation, and no fallback is allowed.
- Discovery is 42 eight-hour cycles from `2026-10-15T12:05:00Z`; validation is
  43 identity-disjoint cycles after a frozen 32-hour purge, ending
  `2026-11-13T12:05:00Z`. Maximum authority is 12,410 authenticated attempts and
  12,240 billable credits. Current use is zero attempts and zero credits.
- A replay-valid terminal cohort v1 is mandatory before activation. Calendar
  passage is insufficient; a nonterminal v1 causes an offline missed-window
  transition, never a race or backfill.
- `nansen-signal-lab-parallel-strategy.timer` is installed as a copy under
  `/home/joel/.config/systemd/user/`, enabled, and active. The no-action service
  smoke returned `Result=success` and `ExecMainStatus=0`.
- Installed service SHA-256:
  `221d51596246515807e2c7d7ae6dbf55a7394589bd902fea1dbb909869239b81`.
  Installed timer SHA-256:
  `0dad373b4c243f969defffdfa5551643bed77fd6b8441051ae87ddfa627cf123`.
- The committed cohort runtime-preservation drop-in is installed separately at
  `/home/joel/.config/systemd/user/nansen-signal-lab-cohort.service.d/` with
  SHA-256
  `6d4d6fb1a8b1916eaac6d7eece3cea5ba442c7e730258369553d9da66a045efb`.
  This preserves the shared provider-lock inode without changing the
  byte-frozen repository cohort unit. Both installed service fragments live on
  `/home`; login lingering remains enabled.

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
- New parallel-strategy suite: 118 passed in 620.42 seconds, including the
  nonconstant 10,000-replicate bootstrap, exact denominators, full fake-provider
  lifecycle, crash injection, tamper replay, balance continuity, and systemd
  boundaries.
- Full terminal A2 replay suite: 27 passed in 336.27 seconds; terminal Program A
  replay remains byte-exact after isolating all new deployment templates.
- Independent blocker/high review: ready. `compileall`, `git diff --check`,
  preregistration HEAD equality, runtime replay, and `systemd-analyze verify`
  passed. No new-program provider request was made.
- Final whole-repository regression after preregistration and deployment:
  859 passed in 974.35 seconds.

## Next safe action

No manual cycle command is currently due. The enabled timer will start cycle two
at `2026-08-20T11:05:00Z`; the allowed start window ends at `11:20:00Z`. If
decisions seal, it will read and honor the exact `earliest_settlement` timestamp
from that sealed artifact rather than assuming a four-hour boundary. Verify and
commit newly created evidence at the next durable checkpoint; the timer itself
does not stage, commit, push, or publish.

The enabled parallel-strategy timer remains inert until its frozen activation
lead time. It will then require and replay terminal v1 before creating any
provider client. Do not manually activate it early, inspect A/A2 market results
to tune it, add strategies, or spend its authority outside the preregistered
request plan. Before any manual cohort intervention, stop the cohort timer and
confirm its oneshot service is inactive so it cannot race the operator command.
