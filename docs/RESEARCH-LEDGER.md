# Research ledger

This ledger is append-only: add new dated entries at the end; do not rewrite prior observations, confidence labels, limitations, or follow-up states. Corrections must be new entries that link to the superseded record.

[Powered by Nansen API](https://nansen.ai/). See Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide) for the public evidence used here.

## 2026-08-16 — Seven-token Smart-Money accumulation pilot

- **Bundle:** [`research/experiments/2026-08-16-seven-token-pilot/`](../research/experiments/2026-08-16-seven-token-pilot/)
- **Reviewed report:** [`REPORT.md`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md)
- **Question:** Does accumulation before a large move show different forward-return behavior from accumulation after momentum is visible?
- **Lesson:** The selected tokens were heterogeneous. CDXR accumulated with nearly flat price but its decisive late 24-hour label was immature; AI-HEDGE-FUND, MONGO, and TOAD did not show a reversal; CHEAT.SH and PRISMA participated in momentum; CATE was mixed.
- **Confidence:** Low for predictive interpretation; high only for reproducibility of this bounded, descriptive observation.
- **Limitations:** Seven purposively selected tokens, no eligible-token controls or holdout, one short endpoint-derived window, dependent within-token events, unvalidated endpoint prices, and right-censored late labels.
- **Follow-up:** Open — collect a fixed-window CDXR follow-up after `2026-08-16T22:00:00Z` with exact `--from`/`--to` request provenance and new immutable evidence.

## 2026-08-16 — Pilot availability-timestamp correction

- **Supersedes:** Only the pilot's original derived timestamp labels and window wording; raw evidence and numerical return/holdings results are unchanged.
- **Correction:** Hourly flow contents become available at `bucket_end`, not at the raw `date` bucket start. Derived features/events now retain both source boundaries and use bucket end for every trailing/forward lookup.
- **Pilot impact:** The complete derived availability window is `2026-08-12T11:00:00Z` through `2026-08-16T09:00:00Z`. CDXR's largest bucket starts at `2026-08-15T21:00:00Z` and becomes available at `22:00:00Z`; its decisive 24-hour label remains immature in this bundle.
- **Evidence integrity:** All eight committed raw files and their manifest SHA-256 values remain unchanged.
