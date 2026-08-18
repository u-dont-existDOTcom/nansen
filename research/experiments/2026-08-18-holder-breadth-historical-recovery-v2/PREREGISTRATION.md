# Preregistration — 2026-08-18-holder-breadth-historical-recovery-v2

Status: **preregistered recovery; no successor provider access has run**.

This is an outcome-unseen, source-bound recovery of the terminal v1 discovery,
not the untouched original preregistration. It adopts the exact paid screener
bytes from source manifest `af8f77f9d0a5a9d5043401e8a676a173326b008ff0802fb6ecdca3efac936b23` and never calls the
historical screener again.

- Recovery-only policies learned from the source are frozen: the missing quoted
  cost is accepted only because pinned cost, expected cost, observed use, and
  the 75-to-70 balance transition all equal five; response chain `bsc` is mapped
  to requested chain `bnb` only in the adopted screener.
- Raw provider rows and normalized rows have separate hashes. The exact
  normalized body, top-20 cohort, and 4 OHLCV
  payloads are sealed before any successor provider request.
- Dates, eligibility, feature thresholds, arms, twelve-day outcome, 100/250-bps
  cost sensitivities, missingness handling, and advancement gates are unchanged
  from `holder-breadth-daily-v1`.
- New holdings and OHLCV responses must use contract-native `bnb`; no alias is
  applied to newly collected evidence.
- Successor ceiling: `7` authenticated request attempts and
  `6` additional credits: account preflight 0, at most two holdings
  pages at 1 each, and exactly four OHLCV batches at 1 each. Retries are disabled.
- Cumulative study ceiling, including the sealed source: at most
  `9` authenticated attempts and `11`
  credits. Reports must state incremental and cumulative accounting.

This discovery can only decide whether the daily holder-breadth analogue merits
prospective plumbing. It cannot establish profitability or authorize capital.
