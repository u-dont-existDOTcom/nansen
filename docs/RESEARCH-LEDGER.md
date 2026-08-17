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

## 2026-08-16 — Community-signal candidate audit

- **Bundle:** [`research/experiments/2026-08-16-community-signal-shadow/`](../research/experiments/2026-08-16-community-signal-shadow/) — schema-v2 discovery companion of the frozen schema-v1 seven-token pilot; source manifest SHA-256 `2662998bdf21d2d3b80d11003b8e62db59e1ebee68ad33858f2bf0008c8a93d0`.
- **Adopted components:** Backward-looking persistence, disjoint-window acceleration, holder breadth, retention, holdings/price divergence, and sign-only market phase from contiguous completed hourly flow rows. Every feature is available no earlier than `bucket_end`.
- **Rejected shortcuts:** No composite score or fixed community weights; no selection-time market cap, liquidity, netflow, or role metadata; no claim that `holders_count` is buyer breadth; no inferred exchange-to-Smart-Money transfer; no raw Smart-Money holdings publication; and no copied [Smart Money Rotation Radar](https://github.com/Iziedking/Narrative-pulse) code or expression. Its README labels the project MIT, while no separate license file was detected; that source text does not expand the no-copy boundary.
- **Confidence:** High for deterministic rendering and the bounded descriptive audit; low for any predictive interpretation. The seven selected token series have no independent holdout and `point_in_time_guarantee=unknown`, so they cannot determine accuracy or weights.
- **Replication status:** Nansen CLI Builds, Nansen Divergence, Supply Control Scanner, Smart Money Rotation Radar, and Superior Trade remain inspiration/replication leads only. Community performance claims are not connected to a validated result. See the [source catalog](https://release.nansen.ai/en/help/articles/6399546-nansen-cli-builds) and the [shadow report](../research/experiments/2026-08-16-community-signal-shadow/REPORT.md).
- **Follow-up:** Open — collect point-in-time state, then complete wallet-level buyer/seller breadth, then exchange-labelled flows, then transfer-level attribution. Historical liquidity and execution costs remain required before any return claim.

## 2026-08-17 — Preregistered paper-strategy feasibility evaluation

- **Bundle:** [`research/experiments/2026-08-17-paper-strategy-feasibility/`](../research/experiments/2026-08-17-paper-strategy-feasibility/) — schema-v3 offline evaluation bound to schema-v2 source manifest SHA-256 `881c544e2591f76c727bc30e2663d77df408e72acadd7866ee3c240f5c06b1b8`.
- **Reviewed report:** [`REPORT.md`](../research/experiments/2026-08-17-paper-strategy-feasibility/REPORT.md)
- **Question:** Do any fixed trailing Smart-Money entry theories clear low, preregistered paper-feasibility gates after conservative next-hour execution and 100 basis-point per-side costs, and does holder breadth improve a matched 12-hour comparison?
- **Decision:** No entry theory qualified, so no entry or paired veto was selected. The distribution-risk veto was individually eligible but had evidence only in the first chronological block. The holder-breadth positive arm advanced descriptively over the non-positive arm by `+8.4145` percentage points on token-equal base objective and `+0.6657` points on event-median base objective.
- **Rejected/blocked:** Breadth/acceleration had only three episodes across two tokens and a negative token-equal base mean. Sustained markup had a positive token-equal mean but a negative base-cost event median. Flow-only remained benchmark-only. Buyer-breadth plus exchange-outflow confirmation remained blocked by absent point-in-time wallet and exchange-flow history.
- **Confidence:** High for deterministic rendering of the fixed contract and its null selection; low for predictive interpretation. The 95 emitted rows are dependent theory episodes over seven selected tokens, not independent trades.
- **Follow-up:** Open — collect a separately versioned point-in-time historical/beta dataset with complete pagination, quotes, liquidity, costs, and hashes. Do not revise the frozen thresholds to improve this result or advance beyond paper discovery before all prospective gates are met.
