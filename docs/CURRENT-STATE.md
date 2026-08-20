# Current state

Updated: 2026-08-20

## Goal

Find a strategy that survives prospective, execution-aware profitability gates
without waiting months for a first useful result. On 2026-08-20 the owner
authorized the rapid independent prospective program and retired the former
cohort/October automation. The rapid program evaluates all eleven pre-live
rules concurrently from one common, outcome-independent evidence panel;
provider transmissions remain serialized and append-only so concurrency does
not duplicate credits or race the exact balance ledger. It targets a
preliminary discovery lead after approximately 14 days and a validated paper
result after approximately 29 days. Program A/A2 market outcomes and the
superseded October program remain quarantined.

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

## Owner-aborted cohort

- Program:
  `research/experiments/2026-08-18-prospective-multi-cycle-cohort-v1/program.json`.
- Cycle one was scheduled for `2026-08-18T15:05:00Z` and terminalized
  `unscorable` with the frozen reason `insufficient_strata`. No reroll or
  settlement is allowed or required.
- Because the frozen advancement gate requires all 32 cycles to seal outcomes,
  v1 could no longer advance after cycle one's unscorable result. On 2026-08-20
  the owner superseded its future live authority with the rapid independent
  program. This is an operational owner abort, not successful completion or a
  terminal scientific result.
- Verified cumulative use: 2 authenticated attempts, 1 billable credit, and no
  ceiling breach. The full funding gate passed before the screener.
- Cycles 2..32 remain uninitialized and are forbidden from provider access.
- `nansen-signal-lab-cohort.timer` is installed but inactive and disabled.
- Operational constraint: invoke every cohort command with the absolute program
  path. The frozen CLI accepts a relative path, but once a cycle exists its
  offline tree verifier can loop while comparing absolute descendants to a
  relative root. Absolute-path replay/check passed. Do not patch the frozen
  implementation or resume the program.

## Active automation

- `nansen-signal-lab-rapid-research.timer` is installed as a copy under
  `/home/joel/.config/systemd/user/`, enabled, and active. It polls every UTC
  minute but makes no provider call before a frozen action is due.
- Login lingering is enabled (`Linger=yes`), so the user manager survives logout
  and starts at boot. The unit copy is on `/home`, not the optional `nofail`
  `/mnt/hdd` mount; if the repository mount is temporarily absent, later timer
  polls retry after it appears.
- A direct no-due service smoke exited with `Result=success` and
  `ExecMainStatus=0`. The post-activation integrity check reports all 85 cycles
  planned, zero authenticated attempts, zero billable credits, and no budget
  halt.
- Installed service SHA-256:
  `dcbce67661ed8dd8aa4a746a9f6816e9b5e03dc206d39e941445849195c86756`.
  Installed timer SHA-256:
  `80767d69bfc36a9e49b24df0ec8fc52b7aa475506d720455e0c3469ea74c4ac8`.
- The former cohort and October parallel-strategy timers are both inactive and
  disabled. Their service/timer files remain immutable and installed only for
  replay/provenance.
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

## Superseded October preregistration

- Program:
  `research/experiments/2026-08-19-prospective-parallel-strategy-v1/program.json`.
- Preregistered manifest SHA-256:
  `17d7cf6be19e48d095fc27ef080223b293706048bb5d4c07855bfed589811bb3`.
- Source checkpoints: implementation `870eaf6`, direct-entrypoint hardening
  `dfb81e3`, and inert preregistration bundle `6143d73`.
- Stage is `preregistered`; activation evidence is intentionally absent.
  Initialization made no public or authenticated provider request. The rapid
  program supersedes only its future live authority and October calendar; its
  bytes and scientific boundary remain immutable.
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
- It must never be activated, resumed, merged with rapid evidence, or used to
  add/remove/rank a rapid candidate.
- `nansen-signal-lab-parallel-strategy.timer` is installed as a copy under
  `/home/joel/.config/systemd/user/`, but it is inactive and disabled.
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

## Active rapid parallel-strategy program

- Program:
  `research/experiments/2026-08-20-rapid-prospective-parallel-strategy-v1/program.json`.
- Source freeze: `785826a`; host timer fix: `930c11d`; current byte-exact
  preregistration: `23f0b4e`.
- Preregistered manifest SHA-256 before activation:
  `ac2d6430cd9b2121085125afb6a7745a5074de117cfc83f43d71dab7d5139e28`.
- Stage is `activated`. The immutable activation transaction binds the exact
  owner-aborted cohort cycle one, stopped legacy units, Program A/A2 ledgers,
  and first-account balance set `{44,702, 44,703}`. Activation and the no-due
  smoke made zero provider calls.
- Discovery is 42 eight-hour cycles from `2026-08-22T12:05:00Z` through
  `2026-09-05T04:05:00Z`. After a 32-hour purge, validation is 43 cycles from
  `2026-09-06T12:05:00Z` through `2026-09-20T12:05:00Z`.
- Each shared 13-token panel evaluates all eleven frozen strategies offline.
  `c01` is the a-priori anchor; at most one discovery challenger joins it in
  formal validation; there is no fallback or next-best substitution.
- Maximum authority is 12,410 authenticated attempts and 12,240 billable
  credits. Current use is zero attempts and zero credits. The protected future
  research floor is 42,945 credits.

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

The rapid program has exactly 85 cycles x 13 opportunities. Each cycle admits
at most 146 authenticated attempts and 144 billable credits across separate
predecision and settlement account epochs. The program ceiling is 12,410
attempts and 12,240 credits; no retry, reroll, replacement, adaptive endpoint,
or duplicated per-strategy request is allowed.

The operational opening balance is exactly one of `{44,702, 44,703}`, depending
only on A2's sealed one-credit ambiguity. The program enforces exact later
balance continuity and preserves at least 42,945 credits: 13,786 for a later
primary confirmation, 16,592 for longer replication, and 327 safety margin in
addition to the rapid authority. A balance discontinuity, provider contract or
pricing drift, or ambiguous transmission is globally fatal and authorizes no
later request.

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
- Offline implementation verification made no provider calls; owner-aborted
  cohort use is recorded separately above.
- Rapid parallel-strategy verification: 123 focused tests passed, including the
  nonconstant 10,000-replicate bootstrap, exact denominators, full fake-provider
  lifecycle, crash injection, tamper replay, balance continuity, and systemd
  boundaries.
- Full terminal A2 replay suite: 27 passed in 336.27 seconds; terminal Program A
  replay remains byte-exact after isolating all new deployment templates.
- Independent blocker/high review: READY after exact unscorable-reason and
  global-fatal replay hardening.
- Source/preregistration HEAD gate, activated replay, post-activation program
  check, `compileall`, `git diff --check`, and `systemd-analyze verify` passed.
- Installed unit bytes equal the committed repository units. The direct service
  smoke succeeded; current program use remains zero attempts/zero credits.

## Next safe action

No manual cycle command is currently due. The enabled rapid timer will admit
cycle one at `2026-08-22T12:05:00Z`; its start grace ends at `12:20:00Z`. It
will poll once per minute, prove the old authorities remain retired, verify NTP
and network readiness only for live actions, serialize provider calls under the
shared lock, and run an exact offline check after each action.

Do not re-enable the cohort or October timers, manually duplicate a rapid call,
inspect A/A2 outcomes to tune candidates, or add a strategy. Commit newly
created evidence at the next durable checkpoint; the timer never stages,
commits, pushes, or publishes.
