# Current state

Updated: 2026-08-18

## Goal

Find a strategy that survives prospective, execution-aware profitability gates.
The owner approved proceeding with the recommended Nansen cohort and an
approximately 1,800-credit evidence budget.

## Verified baseline

- Main branch result head: `c91609b` plus terminal replay guard `cbd165c`.
- Historical holder-breadth recovery v2 is immutable `completed` and
  `does_not_advance`; do not rerun or tune it.
- Terminal manifest:
  `9b478afb4bbf1baec1b894526aaf52b37a77193016996b1811d35df67d1a64ef`.
- Current proved Nansen balance after the terminal recovery: 64 credits.
- Exact holder-breadth finding: positive token-equal aggregates were driven by
  a heavy-tail winner while the frozen event-median spread was negative.
- Result review:
  `docs/audits/2026-08-18-holder-breadth-historical-recovery-v2-result-review.md`.

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
  concentration, and token/week bootstrap gates without weakening them.
- treat buyer-breadth/exchange co-movement paired with the distribution veto as
  the sole confirmatory rule; all other frozen variants are descriptive.

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

- Full offline suite: 616 passed.
- Exact contract SHA-256:
  `d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`.
- Frozen strategy SHA-256:
  `5d5859be0c03bd1f786436ad199aac48de9c6688883392836796c0f8e3ccf6d5`.
- No live Nansen or OpenAI call was made while building or verifying it.

## Next safe action

Commit the reviewed offline implementation. Do not initialize a live cohort,
query the provider, spend credits, push, or publish until a fresh account
preflight can prove all 1,792 remaining program credits. The last proved
balance is only 64 credits, so funding is the current external blocker.
