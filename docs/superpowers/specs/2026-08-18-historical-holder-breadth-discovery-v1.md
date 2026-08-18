# Historical holder-breadth discovery v1

## Objective

Test the only preregistered community-inspired component that advanced
descriptively—positive Smart-Money holder breadth—on a larger, temporally
anchored daily dataset before spending most of the remaining credits on a
prospective execution cohort.

This is a new immutable experiment family. It never reopens or settles the
sealed GPT prospective bundles.

## Fixed dataset

- Historical screener date: 2026-06-01, one-day window.
- Chains: Ethereum, Solana, Base, and BNB.
- Eligibility: market cap >= $1m, liquidity >= $250k, token age >= 3 days,
  stablecoins excluded, Smart-Money trader cohort. The request deliberately
  disables the provider blacklist because its historical effective-date
  semantics are not documented; fixed numeric and sector rules remain.
- Cohort: the top 20 eligible rows on screener page one by netflow; chain and
  normalized address are the deterministic tie-breaks. Page one need not be the
  final page because the server order and page-local selection are part of the
  frozen rule.
- Holdings evidence: stable `smart-money/historical-holdings`, 2026-05-28
  through 2026-08-09 inclusive, filtered to the frozen token addresses and at
  most two complete 1,000-row pages.
- Outcome evidence: stable `tgm/token-ohlcv`, daily candles from 2026-06-03
  through 2026-08-09, grouped deterministically into at most five same-chain
  batches of at most ten addresses.
- Signal window: 2026-06-02 through 2026-07-27 inclusive (eight weeks).

The historical beta screener costs five credits. Each stable historical
holdings page and each stable OHLCV batch costs one. A zero-credit account
preflight is mandatory. The hard ceiling is nine authenticated request attempts
and twelve credits. Automatic retries are disabled, and the terminal seal
independently counts the archived transmissible request attempts. The exact
live OpenAPI SHA-256 is pinned before preregistration.

## Fixed daily analogue

The signal is available after end-of-day `t`. It uses the five contiguous daily
snapshots from `t-4` through `t`:

- balance change is positive;
- at least two of four balance deltas are positive;
- net retained accumulation divided by gross positive accumulation is >= 0.8;
- the positive arm requires holder-count change > 0;
- the non-positive reference arm requires holder-count change <= 0.

Entry uses the independent OHLCV daily close on `t+1`; exit uses the daily close
twelve days later. Every calendar-day candle from entry through exit must be
present. A signal with complete holdings features remains an eligible,
non-overlapping event even when its outcome path is incomplete; it receives a
missing-outcome status rather than disappearing. Episodes do not overlap within
token and arm. Base cost is 100 bps per side; stress cost is 250 bps per side.

The signal window is split into four fixed 14-day blocks. Descriptive
advancement requires at least ten events and five tokens per arm, positive
token-equal and event-median base-cost spreads, positive positive-arm
token-equal stress expectancy, and positive token-equal block spread in at
least three blocks. Both arms must also have 100% outcome coverage.

## Interpretation boundary

This experiment can reject or advance the daily analogue for prospective
plumbing. It cannot establish profitability because the screener is beta,
liquidity is known only at cohort selection, prices are daily close proxies, and
costs are sensitivities rather than timestamped fills. The provider broadly
documents the historical holdings surface as no-lookahead, but does not
separately document Smart-Money wallet-label effective dates. Advancement still
requires an untouched prospective cohort with the repository's existing
eight-week, 100-fill, 20-token, fill-rate, bootstrap, stress, and concentration
gates.

## Evidence and recovery

Every request, exact response byte string, retained response header, retrieval
time, credit observation, contract byte string, derived file, budget snapshot,
and terminal seal is append-only and hashed. A transmitted request without a
complete archived response is never retransmitted automatically. Contract
drift, incomplete pagination, insufficient eligible tokens, malformed evidence,
an exceeded request-attempt ceiling, or a provider/budget ambiguity terminalizes
the bundle as `unscorable`. Terminal snapshot and seal timestamps are adopted
from an already written transaction artifact so finalization is crash-resumable
without changing bytes or repeating provider calls.
