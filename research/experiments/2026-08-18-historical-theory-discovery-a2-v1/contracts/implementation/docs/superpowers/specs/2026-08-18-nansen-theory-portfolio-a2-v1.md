# Nansen historical theory discovery Program A2

Status: successor design. It authorizes no provider request until the exact
implementation, predecessor proof, fixture lifecycle, source archive, and
systemd unit are independently reviewed and committed.

## Authority and separation

The owner directed the system to continue generating and testing its best
theories until the funded API credits are consumed, subject only to pauses for
genuine owner decisions. This document creates a separately versioned successor
portfolio; it does not reopen or edit terminal Program A.

Program A ended after four complete anchors, one evidence-invalid unscorable
anchor, and a partially queried sixth anchor. The sixth anchor selected Solana
USDC `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`; the historical flow-summary
endpoint returned a sealed HTTP 422 saying that exact endpoint/chain/address is
unsupported. Program A ended with 135 authenticated attempts and a conservative
537-credit charge, of which five credits remain reserved for the headerless
ambiguous 422.

A2 supersedes exactly two governance provisions of portfolio v1: terminal A
underspend may fund this one operational successor, and A2's sealed shortlist
may feed a separately frozen B2. It does not reinterpret A1 as successful,
does not make A2 evidence eligible for v1 B, and changes no B/C/D estimand or
capital boundary. There is no A3. A2 failure ends historical discovery, and A2
underspend may roll only into fixed-rule D2 replication.

A1 panels, features, execution calibrations, outcomes, scores, and returns are
quarantined. Only the exact frozen schedule, candidate semantics, terminal
accounting, and provider-compatibility fact are admissible. The runner must
validate the exact predecessor response, metadata, terminal seal, program,
portfolio, candidate-contract hashes, and their declared semantics before any
public or authenticated access.

## Untouched schedule and sole operability amendment

A2 drops the partly observed original anchor 6. It uses the exact original
schedule slice for anchors 7 through 65 (`2025-06-29` through `2026-08-09`):
59 anchors, 358 planned slots, and 59 calibration flags. It preserves original
event IDs, dates, slot indices, chains, strata, and calibration identities,
while using local zero-based anchor indices only for storage.

The original selection and within-anchor duplicate handling run unchanged.
Only afterward, an exact selected event matching all three values

```text
endpoint = v1beta1/tgm/historical-token-flow-summary
chain = solana
address = EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
```

becomes `UNAVAILABLE/source_endpoint_unsupported`. Its selected row, raw/normal
chain provenance, identity, and slot remain archived. It receives zero feature,
OHLCV, or DEX calls and no replacement, borrowing, reroll, page-two query,
symbol/sector inference, or broader stablecoin rule. The unavailable slot stays
in the 358 planned denominator but not the selected/candidate-decision
denominator. Reports retain missingness by reason, chain, and stratum. Any other
non-2xx, non-object body, ambiguity, pricing error, or contract drift is still a
program-global fatal.

## Budget firewall

The original provider proof was 50,063 credits. A1 conservatively reserved 537,
leaving a floor of 49,526. A2 receives a nonexpandable 7,463-credit authority;
its maximum request plan is smaller:

```text
4 seven-slot anchors * (32 attempts, 127 credits)
+ 55 six-slot anchors * (28 attempts, 111 credits)
= 1,668 authenticated attempts and 6,613 credits
```

Thus A1 plus A2 can use at most 7,150 of the original 8,000-credit historical
allocation, leaving 850 credits. The other allocations remain B2 12,240, C2
13,786, and D2 13,974, totaling 40,000. The active cohort retains 1,736 and the
safety margin remains 327. The worst-case post-A2 floor is 42,913:

```text
49,526 - 6,613 = 42,913
40,000 + 1,736 + 327 + 850 = 42,913
```

The first A2 account response must be exactly 49,526 (422 charged five) or
49,531 (422 charged zero). Both retain the conservative predecessor reserve and
neither expands A2's ceilings. A value below, above, or otherwise different is
a balance discontinuity. Later anchor baselines must preserve the exact
per-A2-ledger chain and the conservative floor. Every transmitted attempt,
including account, is counted. Retries and ambiguous retransmissions are
forbidden.

## Frozen analysis

All feature validation, candidate truth tables, outcome windows, costs, DEX
simulation, availability semantics, concentration metrics, ranking order,
source/dependency pinning, request archiving, crash recovery, and replay use the
reviewed Program-A implementation and exact candidate contract
`aa4d1085a0b3594a8a255584e0aec7a0bdab0a6438bcc63b7af0076a9f5d056a`.
The implementation is loaded into an isolated namespace; importing A2 must not
change or impair A1 replay.

The only analysis adaptation is the stability partition. A2 uses five fixed
calendar blocks: original anchors 7–18, 19–30, 31–42, 43–54, and 55–65, with
sizes 12/12/12/12/11. This replaces the inherited 13-anchor arithmetic that
would create an unbalanced final fragment after slicing.

Candidate gates remain at least 20 scored signals, ten physical tokens, eight
weeks, 80% decision availability, 90% common-outcome coverage, and zero missing
outcomes among emitted signals. The same a-priori candidate occupies one of at
most five discovery-only shortlist positions. A2 is scientifically complete
only when all 59 anchors are terminal and all of these proportional gates pass:

- at least 54 complete anchors;
- at least 287 of 358 planned slots selected; and
- at least 90% common proxy-outcome coverage among selected opportunities.

Otherwise it seals all evidence and ranking but ends
`unscorable/insufficient_program_a2_support`. A2 makes no confirmatory,
executable-profitability, causal, or capital-deployment claim.
An unscorable A2 has an empty machine-readable B2-eligible candidate list; its
descriptive shortlist is retained under a separate field and cannot feed B2.

## Runtime and lifecycle

A2 lives outside the source globs frozen by terminal A1 and the active v1
cohort. Its runtime manifest hashes the entire inherited implementation,
standalone A2 adapter, this design, unit, tests, OpenAPI contract, complete
dependency closure, and exact predecessor artifacts. Live validation refuses
any byte, source-set, dependency, or predecessor drift before provider access.

Every anchor has a fresh public OpenAPI proof and independent account/budget
epoch. The shared global provider lock serializes all authenticated work. The
cohort timer is stopped, its oneshot is proved loaded and inactive, and it is
restored on every exit. No request starts at or after
`2026-08-20T10:42:00Z`; the systemd supervisor hard-stops at 10:43:30Z, leaving
the active cohort's cycle-two guard window untouched.

Before live execution, the committed offline lifecycle must cover initialization
and HEAD gating, same-process A1 checkability, exact schedule/budget arithmetic,
predecessor tamper rejection, first-account settlement boundaries, a complete
anchor and zero-call resume, response/seal tamper rejection, fatal-provider
boundaries, all-anchor finalization, deterministic ranking/calibration replay,
the shared-lock/systemd handoff, and static unit verification.
