# Historical holder-breadth source recovery v2

## Objective

Recover the outcome-unseen historical holder-breadth discovery from the exact
paid screener evidence sealed by v1, without reopening that terminal bundle or
paying for the screener again. This is a new immutable experiment whose only
post-source policy changes are explicit evidence-plumbing rules.

## Source boundary

The successor binds the v1 terminal manifest, seal, design, preregistration,
OpenAPI, account evidence, screener request, exact response bytes, and response
metadata before any provider access. It copies the required evidence into its
own bundle without changing the source.

The source ended before holdings or outcomes. Its account balance was 75. The
screener response was HTTP 200, reported five credits used and 70 remaining,
and omitted only the quoted-cost header. The recovery accepts five credits for
that already-observed source request because all independent available values
agree: the pinned OpenAPI cost is five, the requested expected cost was five,
the used header is five, and the balance transition is 75 to 70. This exception
applies only to adoption of the source response; every new paid response still
requires strict complete pricing evidence.

The source request used chain `bnb`, while the response used provider alias
`bsc`. The recovery maps `bsc` to `bnb` only in the already-observed screener.
It records separate hashes for every raw and normalized selected row, plus the
raw body and normalized body. New holdings and OHLCV evidence must use `bnb` as
specified by the contract and receives no alias transformation.

## Frozen preprocessing and analysis

Initialization is offline. It freezes the exact normalized 153-row source body,
the deterministic top-20 cohort, and exactly four same-chain OHLCV request
payloads before any successor provider access. The selected cohort contains
three Base, eight BNB, four Ethereum, and five Solana tokens.

All dates, eligibility rules, accumulation features, positive and non-positive
holder-breadth arms, twelve-day outcome, non-overlap rule, missing-outcome
denominator, 100/250-basis-point per-side sensitivities, four blocks, and
advancement gates remain exactly `holder-breadth-daily-v1`. The source recovery
does not tune a strategy threshold or inspect any outcome before freezing these
inputs.

## Collection and budget

The successor performs a fresh zero-credit account preflight, collects at most
two complete historical-holdings pages, and always collects the four frozen
OHLCV batches. Complete holdings schema and identity are validated before the
first OHLCV request, and each OHLCV response is fully validated against its
frozen payload before the next request. Automatic retries are disabled. Its
hard incremental ceiling is seven authenticated request attempts and six
authorized credits. Including the sealed source's two attempts and five
credits, the cumulative authorized study ceiling is nine attempts and eleven
credits. A provider-reported overcharge is preserved in an `unscorable` seal as
an actual budget breach rather than making the evidence unsealable or treating
the extra charge as authorized. Terminal reports and seals state both
incremental and cumulative actual accounting.

Every request is archived before transmission. Exact response bytes, headers,
timings, pricing evidence, budget transitions, frozen inputs, derived outputs,
and terminal state are hash-bound. A transmitted request without complete
evidence is never retransmitted automatically. A contract mismatch, source
binding change, pagination failure, schema drift, pricing ambiguity, or ceiling
violation terminalizes the successor as `unscorable`.

## Interpretation boundary

This recovery remains historical beta discovery and explicitly differs from
the untouched original preregistration. It can prioritize or drop the daily
holder-breadth analogue for prospective plumbing. It cannot establish a
profitable or executable strategy, satisfy the repository's prospective gates,
authorize capital, or erase the limitations of daily close proxies,
selection-date-only liquidity, cost sensitivities, and separately undocumented
wallet-label effective dates.
