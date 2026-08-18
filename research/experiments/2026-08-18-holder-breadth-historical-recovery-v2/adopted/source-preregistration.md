# Preregistration — 2026-08-18-holder-breadth-historical-discovery-v1

Status: **preregistered; no paid collection has run**.

This immutable discovery experiment tests a daily-granularity analogue of the
previously advanced holder-breadth comparison. It does not modify or rerun any
sealed GPT pilot.

- Selection: historical Smart-Money screener page one at `2026-06-01`; top
  `20` eligible tokens by netflow with deterministic chain/address
  tie-breaks, market cap at least $1m, liquidity at least $250k, age at least
  three days, and stablecoins excluded.
- Evidence: historical Smart-Money daily holdings from `2026-05-28` through
  `2026-08-09`, at most two complete pages, plus independent daily OHLCV in
  at most five chain-batched calls. The provider broadly describes the
  historical holdings surface as no-lookahead; wallet-label effective-date
  semantics remain a discovery limitation.
- Signal dates: `2026-06-02` through `2026-07-27` (eight weeks).
- Positive arm: positive four-day balance change, at least 50% positive daily
  deltas, at least 80% accumulation retention, and positive four-day holder
  breadth. Reference arm has the same accumulation predicates and non-positive
  holder breadth.
- Availability: signal at day `t`, entry at independent OHLCV close on `t+1`,
  and exit twelve days later. A non-overlapping eligible signal remains in the
  denominator when OHLCV is missing; advancement requires 100% contiguous
  outcome coverage.
- Costs: 100 bps/side base and 250 bps/side stress. These are sensitivities, not
  timestamped executable quotes.
- Descriptive advancement requires at least 10 non-overlapping events and five
  tokens in each arm; positive aggregate token-equal and event-median spreads;
  positive stress token-equal mean in the positive arm; and positive block
  spread in at least three of four fixed 14-day blocks.
- Hard provider ceiling: `9` authenticated request attempts and
  `12` credits (account 0, historical screener 5, holdings pages and
  OHLCV batches 1 each). Automatic retries are disabled.

No result from this historical beta discovery can satisfy the repository's
prospective profitability gates. Any surviving rule still requires an untouched
prospective, execution-aware holdout.
