# Historical holder-breadth source recovery v2 result review

Date: 2026-08-18
Bundle: `research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/`
Implementation commit: `4b23bd8`
Preregistration commit: `32742a5`
Terminal manifest SHA-256: `9b478afb4bbf1baec1b894526aaf52b37a77193016996b1811d35df67d1a64ef`

## Verdict

The exact `holder-breadth-daily-v1` comparison **does not advance**. This is a
valid negative result for that frozen daily analogue, not proof that every
holder-breadth strategy or community profitability claim is false.

Positive holder breadth looked strong under token-equal averaging but failed
the preregistered typical-event check. Its base-cost token-equal mean was
`+8.7630%`, versus `-13.2175%` for non-positive breadth, a `+21.9805` percentage
point spread. Its event median was nevertheless `-6.1055%`, versus `-3.1959%`
for the reference, a `-2.9096` point spread. That negative event-median spread
was the only failed advancement gate.

The conflict is economically important: this rule found a few very large
winners, not a reliable ordinary trade. The positive arm had 9 positive and 17
negative events. Its largest event was SLX at `+215.3483%` after base costs.
An explicitly post-result concentration sensitivity that removes only the
highest positive-arm token changes the token-equal mean from `+8.7630%` to
`-5.0093%` and the stress mean to `-7.8707%`. The token-equal arm spread remains
positive at approximately `+3.42` points because SLX also had a `-70.7173%`
reference-arm event. This sensitivity was not an advancement gate; it explains
why the frozen median gate correctly blocked promotion.

## Frozen result

- Positive arm: 26 non-overlapping events, 16 tokens, 100% outcome coverage,
  `+8.7630%` token-equal base mean, `-6.1055%` event median, and `+5.4922%`
  token-equal stress mean.
- Non-positive arm: 15 events, 13 tokens, 100% outcome coverage,
  `-13.2175%` token-equal base mean, `-3.1959%` event median, and `-15.8273%`
  token-equal stress mean.
- Block spreads: `+32.9197`, `+35.0043`, `-2.6843`, and `+31.2712`
  percentage points. Three of four blocks favored positive breadth.
- Every other frozen gate passed: event and token counts, complete outcome
  coverage, positive token-equal spread, positive positive-arm stress mean,
  and at least three positive block spreads.
- The selection status is `does_not_advance` solely because the event-median
  spread was non-positive.

The dataset contains all 1,120 daily feature rows for 20 selected tokens over
56 signal dates and 41 non-overlapping eligible events. The 29 token-arm
memberships correspond to 17 physical tokens, with 12 appearing in both arms;
events are therefore clustered rather than independent. No missing eligible
outcome was silently dropped. Historical-holdings input coverage was
1,326/1,480 requested token-days (`89.59%`) and complete for 15 of 20 selected
tokens; the other five had 5 to 57 absent daily position rows. The frozen rule
emitted signals only from complete five-day feature windows. That handling is
reproducible and passed the event/token gates, but absence from a positions
surface can be informative, so it further limits generalization beyond this
exact comparison.

## Provenance and accounting

The successor preserved the terminal v1 source and never called the screener.
It adopted the exact paid screener response, kept separate raw and normalized
row hashes, applied the preregistered source-only `bsc -> bnb` alias, and froze
the 20-token selection plus four OHLCV payloads before live access.

The live successor made exactly seven authenticated attempt-1 requests with no
retry: account, two historical-holdings pages, and one OHLCV batch for each of
Base, BNB, Ethereum, and Solana. It used six additional credits and moved the
proved current balance from 70 to 64. Including v1, the complete study used
nine authenticated attempts and eleven credits, exactly its cumulative ceiling.

Every new paid response reported cost 1, use 1, and the expected remaining
balance. The completed budget has no halted reason. No GPT call, order, wallet
action, settlement, or capital movement occurred.

## Integrity and verification

- Offline `historical-recovery-check` reconstructed the frozen selection,
  request plan, 1,120 feature rows, 41 events, summary, report, budget replay,
  and completed seal from archived evidence.
- Report SHA-256:
  `e6b5451783a8ed0e527e4f318c1b0d359d630a13a6c6c2c199ca9aee3063f945`.
- Summary SHA-256:
  `6608c07d75c7d0c5bb53a21c61e749c6678ca64f49c72521f8623b63c657d119`.
- Completed seal SHA-256:
  `272a0963a71ba7d0cda23af7943ac53521e96f32d9e8d63b61b2889343507ef0`.
- An independent offline result audit confirmed the exact request topology,
  1,326 holdings rows, all 1,360 requested OHLCV candles, 1,120 features, 41
  events, every aggregate, every gate, and the terminal hashes.
- Before collection, the repository suite passed 541 current tests in bounded
  groups, bytecode compilation passed, `git diff --check` was clean, and an
  independent audit found no paid-run blocker.

## Disposition

Do not rerun, retune, or relabel this cohort. Stop the unchanged daily
holder-breadth rule as an advancement candidate. It may remain only as a fixed
benchmark in future untouched evidence.

The next profitability-capable experiment is not another small historical or
GPT rerun. It is a multi-cycle prospective cohort that records counterfactual
outcomes for every selected token, complete buyer/seller breadth and labelled
exchange flow, point-in-time execution evidence, costs, and token/week block
statistics. The current proved balance of 64 credits can fund one bounded
five-token plumbing cycle, but not the preregistered evidence scale needed to
make a profitability claim. Spending nearly all remaining credits on one cycle
would validate plumbing, not a strategy.
