# Prospective multi-cycle cohort v1 implementation review

Date: 2026-08-18

## Scope

Independent offline review of the implementation introduced by `22f5d2a`.
The review covered lifecycle and budget recovery, source freezing, deterministic
selection, flow and breadth validation, execution evidence, aggregation, and
the frozen contract. No provider or model call was made.

## Material findings and dispositions

1. **Runtime drift was not bound.** Initialization now archives exact protocol
   source copies, exact Python/dependency versions, and a hash manifest. Every
   live command and replay checks the complete runtime environment against that
   archive before provider access.
2. **Comparator replay depended on mutable external research files.** The
   cohort now archives a self-contained comparator-definition document derived
   from the exact frozen strategy manifest and uses it for decisions and replay.
3. **A pre-transmission crash could become an unverifiable terminal cycle.** A
   provably unbound reservation is now durably marked failed before pricing and
   counted as zero attempts before the unscorable seal. A transmissible request
   remains conservative and is never retransmitted.
4. **An early operator invocation destroyed a future cycle.** Early starts now
   raise a nonterminal error with zero provider access; only a missed 15-minute
   start window terminalizes.
5. **Real provider outflows were rejected.** Archived Nansen evidence shows
   signed nonpositive outflow fields. The validator and contract now preserve
   this observed convention while requiring nonnegative inflows. The concrete
   evidence is
   `research/experiments/2026-08-18-gpt-prospective-pilot-completed-flow-v3/raw/nansen/846898d85cbdf955427a1b5a/attempt-1-response.json`.
6. **A price-change semantics failure was silently filtered.** Any finite raw
   `price_change` magnitude above 20 now terminalizes as
   `provider_semantics_failure`. Incomplete universes and empty strata retain
   the frozen `insufficient_universe` and `insufficient_strata` reason codes.
7. **Response bounds and page sizes were under-validated.** Flow rows must now
   stay within the exact 26-hour request buffer; screener, breadth, and DEX
   pages cannot exceed 1,000 rows; integer pagination fields reject booleans and
   floats.
8. **Partial entry fills discarded exit evidence.** A partial entry now exits
   every acquired token it can and records the observed exit fill while the
   outcome correctly remains `UNFILLED_ENTRY`.
9. **Unscorable outcome cycles disappeared from aggregate counts.** Aggregation
   now retains every sealed selection and decision, records attempted
   counterfactual fills separately, and represents missing outcomes as
   unavailable rather than dropping opportunities or signals.
10. **Optional OHLCV fields were treated as mandatory.** `volume_usd` and
    object-valued market cap are validated when present but are not fabricated
    or required when omitted by the pinned response schema.

## Verification

- Focused selection, feature, execution, aggregation, and schema tests:
  65 passed.
- Complete cohort lifecycle tests: 26 passed.
- Full offline suite: 635 passed in 95.01 seconds.
- `compileall`: passed.
- `git diff --check`: passed.
- Live Nansen/OpenAI calls: zero.

## Disposition

The reviewed implementation is ready to commit as an offline protocol. It is
not authorization to initialize or run a cohort. The last proved Nansen balance
remains 64 credits; cycle one requires a fresh zero-credit account proof of the
full 1,792-credit remaining ceiling before its first billable request.
