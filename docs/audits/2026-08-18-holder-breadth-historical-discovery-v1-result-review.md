# Historical holder-breadth discovery v1 result review

Date: 2026-08-18
Bundle: `research/experiments/2026-08-18-holder-breadth-historical-discovery-v1/`
Implementation commit: `5fe5472`
Preregistration commit: `9a83439`
Terminal manifest SHA-256: `af8f77f9d0a5a9d5043401e8a676a173326b008ff0802fb6ecdca3efac936b23`

## Verdict

The first historical holder-breadth discovery is terminally `unscorable`. It
does not validate, reject, or measure the holder-breadth strategy. The public
OpenAPI preflight and zero-credit account baseline passed, and the historical
screener returned HTTP 200 on attempt 1. The screener response reported five
credits used and 70 remaining, but omitted `X-Nansen-Credits-Cost`. The
preregistered fail-closed pricing rule therefore stopped before holdings or
OHLCV collection.

The bundle is immutable and must not be rerun or rewritten. Its paid screener
response remains usable only as hash-bound source evidence for a separately
designed and preregistered recovery successor.

## Provider evidence and accounting

- OpenAPI SHA-256 matched the preregistration:
  `d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`.
- Account attempt 1 returned HTTP 200, plan `free`, body balance 75, explicit
  cost 0, and no use/remaining headers. The preregistered account-baseline-v2
  derivation admitted this as a zero-credit baseline.
- Historical screener attempt 1 returned HTTP 200, `credit_used=5`, and
  `credit_remaining=70`, but `credit_cost=null` because the cost header was
  absent. The response was ledgered as ambiguous pricing evidence after the
  observed five-credit deduction.
- Terminal replay records two authenticated request attempts, one counted paid
  call, five credits used, provider baseline 75, and halted reason
  `pricing evidence is incomplete or malformed`.
- No retry or retransmission occurred. No holdings or OHLCV request ran.

The screener body contains 153 rows, declares page 1 as the final page, and is
otherwise parseable. It also exposes a provider schema mismatch: the request
uses chain `bnb`, while returned BNB rows use `bsc`. The frozen v1 validator
correctly rejects an unrequested literal chain. Any successor that reuses these
bytes must preregister an explicit `bsc -> bnb` response alias rather than
silently changing the terminal source.

## Strategy interpretation

There is no derived cohort, feature table, event table, return, arm comparison,
or advancement decision in this bundle. The terminal event concerns provider
pricing and response-schema evidence only. It is not a negative strategy result
and does not support the conclusion that community-reported strategies are
unprofitable.

A recovery successor may preserve statistical independence if it changes only
evidence plumbing: bind this terminal manifest and exact screener bytes, derive
the already observed five-credit charge from the pinned five-credit OpenAPI
contract plus `used=5` and the 75-to-70 balance transition, preregister the
literal chain alias, and retain every original date, threshold, cost, outcome,
and advancement gate. It must not call the historical screener again.

## Integrity and verification

- Offline `historical-check` verified the terminal manifest, preregistration,
  report, six budget transitions, budget snapshot, account derivation, exact
  OpenAPI bytes, both request/response pairs, and unscorable seal.
- Terminal report SHA-256:
  `9c02fdc7d337cfdf941e4818a2eed37d8e4c15b85b1a73fc6d47f2252d824037`.
- Terminal seal SHA-256:
  `171950eb403fed73f3f23762c8779febd64da0e118db81f2d5c61f21a1a183b5`.
- Screener raw-response SHA-256:
  `136fb8db5d7b505e9b7fcf1cb88bddb4dea76a1734cd4f7dbbce3ae0a0c70556`.
- Screener response-metadata SHA-256:
  `45c68aacf9a75ced06fb80a1c548bfe2070222d612e448fb33d48582409949ee`.
- The implementation/design tree passed 522 repository tests before the live
  run; the final crash-recovery and provider-accounting surfaces also passed an
  independent 100-test offline review. `compileall` and `git diff --check`
  passed.

## Completion

V1 is complete and immutable at `unscorable`. Do not run `historical-start`
again for this manifest. Further collection requires a new source-bound design,
new committed preregistration, and its own hard attempt/credit ceiling.
