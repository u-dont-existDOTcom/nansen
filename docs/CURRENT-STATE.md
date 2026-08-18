# Current state

Updated: 2026-08-18

## Goal

Find a strategy that survives prospective, execution-aware profitability gates.
The owner approved proceeding with the recommended Nansen cohort and an
approximately 1,800-credit evidence budget.

## Verified baseline

- Main branch head: offline cohort implementation `22f5d2a` plus reviewed
  hardening checkpoint `a6367fb`.
- Historical result baseline: `c91609b` plus terminal replay guard `cbd165c`.
- Historical holder-breadth recovery v2 is immutable `completed` and
  `does_not_advance`; do not rerun or tune it.
- Terminal manifest:
  `9b478afb4bbf1baec1b894526aaf52b37a77193016996b1811d35df67d1a64ef`.
- Current proved Nansen balance after the terminal recovery: 64 credits.
- Exact holder-breadth finding: positive token-equal aggregates were driven by
  a heavy-tail winner while the frozen event-median spread was negative.
- Result review:
  `docs/audits/2026-08-18-holder-breadth-historical-recovery-v2-result-review.md`.
- Prospective implementation review:
  `docs/audits/2026-08-18-prospective-multi-cycle-cohort-v1-review.md`.
- The pre-existing untracked `handoff.md` is not part of either checkpoint and
  remains untouched.

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

This provides 160 candidate opportunities. It does not guarantee 100 strategy
signals. Implementation must therefore report `insufficient_strategy_fills`
rather than treating counterfactual fills as strategy fills. Increasing the
program to 64 cycles would approximately double the maximum spend and requires
new owner approval before preregistration.

## Verification

- Full offline suite: 635 passed in 95.01 seconds.
- Complete cohort lifecycle suite: 26 passed.
- `compileall` and `git diff --check`: passed.
- Exact cohort design SHA-256:
  `891fb3ba5307723f79d0413fc8d45957b1d11f966e5f920fa40ee732de7a230a`.
- Exact contract SHA-256:
  `d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`.
- Frozen strategy SHA-256:
  `5d5859be0c03bd1f786436ad199aac48de9c6688883392836796c0f8e3ccf6d5`.
- No live Nansen or OpenAI call was made while building or verifying it.

## Next safe action

Funding is the current external blocker. The last proved balance is only 64
credits, so do not schedule or initialize the live cohort yet. Once the account
is funded for the full 1,792-credit ceiling, choose a future `HH:05` UTC start,
initialize the frozen cohort offline, and let cycle one's zero-credit account
preflight prove the entire remaining program ceiling before its first billable
request. No live cohort has been initialized, and no provider query, credit
spend, push, or publication is authorized by this checkpoint.
