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

## 2026-08-17 — Preregistered identity-blinded GPT prospective pilot

- **Bundle:** [`research/experiments/2026-08-17-gpt-prospective-pilot/`](../research/experiments/2026-08-17-gpt-prospective-pilot/) — schema-v4 prospective lifecycle bound to the six frozen schema-v3 records, the design, and the pinned Nansen OpenAPI contract extract.
- **Status:** Preregistered; no paid Nansen call or GPT inference has run. There is no `REPORT.md`, outcome seal, result field, selected token, or forward evidence in the bundle.
- **Question:** Can an identity-blinded two-pass `gpt-5.6-sol` decision strictly outperform every applicable scorable frozen strategy on one common observed four-hour paper outcome?
- **Contract:** Page-one highest eligible Smart-Money netflow after fixed age/market-cap/liquidity filters; prior cohort identities excluded; virtual notional `min(1000, 0.001 * screener_liquidity_usd)`; ten-call/ten-credit Nansen ceiling; no model tools; common DEX fills; closed five-minute OHLCV; ties are not wins.
- **Integrity:** Exact request/response bytes, credit headers, timestamps, canonical request hashes, immutable budget transitions/snapshots, stage transactions, collision quarantine, blinded snapshot hash, Pass 1 response hash, and terminal report hash are preserved. Ambiguous transmission, incomplete evidence, or unavailable required comparison becomes `unscorable`, never zero.
- **Limitations:** One purposively selected page-local token and one four-hour observation cannot establish predictive validity or advancement. The experiment is paper-only and cannot create an order, wallet action, venue submission, executable-route claim, or capital movement.
- **Follow-up:** Run only after the committed preregistration, full offline verification, and whole-branch pre-spend review are clean; then settle no earlier than the sealed closed-candle deadline and append the immutable outcome as a new ledger entry.

## 2026-08-17 — GPT prospective pilot terminal preflight failure and invalidation

- **Bundle:** [`research/experiments/2026-08-17-gpt-prospective-pilot/`](../research/experiments/2026-08-17-gpt-prospective-pilot/) — immutable schema-v4 terminal bundle; manifest SHA-256 `43b6da8703d14b2190bb10c93ade00f7ca3c0cee363c18a4eb77c975d717cfc3`.
- **Terminal report:** [`REPORT.md`](../research/experiments/2026-08-17-gpt-prospective-pilot/REPORT.md) — verdict `unscorable`; report SHA-256 `70ff849a6d535889d85e666d3bba03763913583c717ade02ae105f26b7241f54`.
- **Observed event:** The public Nansen OpenAPI hash matched. The OpenAI model-access preflight then returned HTTP 401 with provider code `invalid_api_key`, so the no-reroll protocol sealed the experiment before selection or inference.
- **Accounting:** Zero Nansen calls and zero Nansen credits. No selected token, blinded snapshot, GPT output, comparator decision, paper fill, market outcome, or headline comparison exists.
- **Audit disposition:** Unusable as a model or strategy observation. The client accepted a nonempty credential that did not have an OpenAI API-key shape instead of rejecting it locally, contrary to the preregistered credential gate. See the [terminal-audit erratum](audits/2026-08-17-gpt-prospective-pilot-erratum.md).
- **Follow-up:** Fix and verify local credential-shape validation for future runs. Any actual GPT test must use a separately named, separately committed preregistration; the sealed terminal bundle is never rerun or rewritten.

## 2026-08-17 — Preregistered GPT prospective pilot successor

- **Bundle:** [`research/experiments/2026-08-17-gpt-prospective-pilot-successor/`](../research/experiments/2026-08-17-gpt-prospective-pilot-successor/) — new schema-v4 observation created only after the original terminal bundle was preserved and the credential-shape guard passed its full regression gate.
- **Status:** Preregistered; zero Nansen calls, zero Nansen credits, no GPT inference, no selected token, no outcome, and no `REPORT.md`.
- **Contract:** Unchanged from the original preregistration: exact `gpt-5.6-sol`, two identity-blinded no-tool passes, the same six hash-bound frozen records, page-local eligible selection, common observed fills, strict-greater-than scoring, and the ten-call/ten-credit ceiling.
- **Independence:** This bundle has a distinct experiment ID and creation time. It does not overwrite, reopen, or reuse any request from the terminal HTTP-401 observation.
- **Follow-up:** Run only from its committed preregistered state. Any transmitted failure remains terminal and is never rerolled.

## 2026-08-17 — GPT prospective pilot successor terminal account-evidence failure

- **Bundle:** [`research/experiments/2026-08-17-gpt-prospective-pilot-successor/`](../research/experiments/2026-08-17-gpt-prospective-pilot-successor/) — immutable terminal schema-v4 bundle; manifest SHA-256 `5d0c5cb7517ebeec53bc1f8628f7271f6e83786ef90e3d9d1d3d379cf346b52b`.
- **Terminal report:** [`REPORT.md`](../research/experiments/2026-08-17-gpt-prospective-pilot-successor/REPORT.md) — verdict `unscorable`; report SHA-256 `5bc9038e983b5cb8b0701f06430cbfafa7fa2f4ed26ac1d7445630a83bbaec62`.
- **OpenAI result:** The corrected key passed; the exact `gpt-5.6-sol` model-access preflight returned HTTP 200. No inference was requested.
- **Nansen result:** The free account response returned plan `free`, body balance `90`, and cost header `0`, but omitted the declared use and remaining headers. The protocol stopped before the screener or any token-specific request.
- **Accounting:** One ambiguous call/credit is reserved conservatively; no provider deduction is confirmed. No selected token, snapshot, GPT output, comparator decision, paper fill, or market outcome exists.
- **Disposition:** Correct fail-closed outcome, not evidence about model quality. See the [successor result review](audits/2026-08-17-gpt-prospective-pilot-successor-result-review.md). Do not create another automatic retry while the account endpoint omits the required headers.

## 2026-08-18 — Historical holder-breadth discovery terminal pricing failure

- **Bundle:** [`research/experiments/2026-08-18-holder-breadth-historical-discovery-v1/`](../research/experiments/2026-08-18-holder-breadth-historical-discovery-v1/) — immutable terminal discovery bundle; manifest SHA-256 `af8f77f9d0a5a9d5043401e8a676a173326b008ff0802fb6ecdca3efac936b23`.
- **Question:** Does positive four-day Smart-Money holder breadth improve twelve-day cost-adjusted outcomes over otherwise matched non-positive breadth on a fixed 20-token, eight-week daily cohort?
- **Terminal event:** The account baseline proved 75 credits. The beta historical screener returned HTTP 200, reported five credits used and 70 remaining, but omitted its quoted-cost header. The fail-closed protocol sealed `unscorable` before holdings or OHLCV collection.
- **Accounting:** Two authenticated attempts, one paid call, five credits used, no retry. The screener returned 153 final-page rows. It also returned BNB rows as `bsc` after a request for `bnb`, so the frozen literal-chain validator would reject the body independently of pricing.
- **Interpretation:** No cohort, strategy event, return, arm comparison, or advancement decision exists. This is provider-evidence failure, not evidence against holder breadth or any community profitability claim.
- **Reviewed result:** [`docs/audits/2026-08-18-holder-breadth-historical-discovery-v1-result-review.md`](audits/2026-08-18-holder-breadth-historical-discovery-v1-result-review.md).
- **Follow-up:** Open — a separately preregistered successor may reuse the exact paid screener bytes without rerunning them, provided it binds the terminal source, preregisters the `bsc -> bnb` alias and a narrow observed-cost derivation, and leaves every strategy rule and gate unchanged.

## 2026-08-18 — Historical holder-breadth source recovery completed

- **Bundle:** [`research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/`](../research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/) — immutable terminal recovery; manifest SHA-256 `9b478afb4bbf1baec1b894526aaf52b37a77193016996b1811d35df67d1a64ef`.
- **Recovery:** Reused the exact outcome-unseen paid screener bytes, froze raw and normalized provenance plus the 20-token cohort and four OHLCV payloads, and made no screener request. The source-only `bsc -> bnb` alias and observed five-credit source derivation were explicit preregistered recovery changes.
- **Accounting:** Seven successor attempt-1 requests, six additional credits, no retry, current proved balance 64. Source plus successor used nine authenticated attempts and eleven credits.
- **Result:** Positive holder breadth had 26 events/16 tokens and non-positive breadth had 15 events/13 tokens, both at 100% outcome coverage. Token-equal base spread was `+21.9805` points and three of four block spreads were positive, but event-median spread was `-2.9096` points (`-6.1055%` versus `-3.1959%`).
- **Decision:** `does_not_advance`. The exact daily analogue failed only its frozen event-median gate. A post-result concentration diagnostic found its `+8.7630%` positive-arm token-equal mean falls to `-5.0093%` after removing the single highest token; this diagnostic explains the gate but does not change it.
- **Interpretation:** This is a valid negative result for the exact frozen analogue, not a general falsification of holder breadth or community profitability claims. Do not rerun or tune on this cohort.
- **Reviewed result:** [`docs/audits/2026-08-18-holder-breadth-historical-recovery-v2-result-review.md`](audits/2026-08-18-holder-breadth-historical-recovery-v2-result-review.md).
- **Follow-up:** A profitability-capable next step requires a multi-cycle prospective cohort with unbiased counterfactual outcomes, buyer/seller breadth, labelled exchange flow, point-in-time execution evidence, costs, and block statistics. The remaining 64 credits can fund one plumbing cycle, not the required evidence program.
