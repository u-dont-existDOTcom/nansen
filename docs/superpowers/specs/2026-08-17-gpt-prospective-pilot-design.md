# GPT Prospective Strategy Pilot Design

Date: 2026-08-17
Status: approved under the repository owner's standing instruction to choose the best safe in-scope trade-offs autonomously

## Objective

Run one prospective, point-in-time, identity-blinded paper experiment that tests GPT in two roles:

1. an independent decision analyst that chooses `LONG` or `ABSTAIN` from a sealed Nansen snapshot; and
2. a strategy critic that reviews the immutable first answer against the six frozen paper-strategy records before the outcome exists.

The primary question is whether the critiqued GPT decision earns a strictly higher four-hour net paper return than every applicable frozen comparator on the same token, timestamp, notional, and observed-trade fill evidence.

This is a one-token pilot observation. It cannot establish general GPT superiority, expected return, statistical significance, a validated strategy, or authorization to trade capital.

## Approach decision

Three approaches were considered:

1. **Prospective sealed snapshot — selected.** Select a live token before GPT sees its evidence, seal both model passes before entry, and collect the outcome afterward. This provides the strongest protection against outcome leakage, uses current one-credit Nansen endpoints, and stays within ten calls. Its cost is a minimum four-hour settlement delay.
2. **Post-cutoff historical replay.** Choose a historical observation after the model's knowledge cutoff and use Nansen's historical endpoints. This settles immediately and has richer point-in-time surfaces, but the researcher can already inspect the outcome, label dictionaries may be current for legacy endpoints, and historical endpoints cost five to twenty-five credits per call.
3. **Split historical and prospective trial.** Run one historical rehearsal and one prospective observation. Two observations sound broader, but ten calls would under-instrument both and weaken pagination, fill, and provenance guarantees.

The implementation uses approach 1 only. It does not revise any frozen strategy predicate or reuse a prior pilot token.

## Scope and boundaries

The implementation adds a schema-v4 prospective experiment type, collection commands, pure normalization and scoring units, an OpenAI Responses adapter, and deterministic repository artifacts.

It must not:

- modify schema-v1, schema-v2, or schema-v3 experiment bytes or meanings;
- submit an order, connect a wallet, create a venue account, or move capital;
- expose API keys in artifacts, logs, prompts, tests, or commits;
- let GPT use web search, tools, repository search, conversation memory, or token identity;
- let outcome, forward-return, later-trade, MFE, MAE, or prior feasibility-result fields enter either model request;
- exceed ten billable Nansen requests or ten Nansen credits;
- replace missing, incomplete, late, or invalid evidence with zero;
- retry an ambiguously completed paid request automatically.

The virtual notional is `min($1,000, 0.001 * point_in_time_liquidity_usd)`. All decisions use the same notional and are long-or-cash paper decisions; short selling is out of scope.

## Experiment lifecycle

The schema-v4 lifecycle is append-only:

1. `preregistered`: configuration and hashes exist; no paid call has occurred.
2. `snapshot_collected`: raw pre-decision evidence and normalized snapshot are sealed.
3. `decision_sealed`: both GPT responses and all deterministic comparator decisions are sealed before entry.
4. `entry_observed`: the prospective entry window is complete and its raw trades are sealed.
5. `settled`: the exit window and independent OHLCV outcome are sealed and scored.
6. `unscorable`: a terminal evidence failure prevents a defensible comparison.

Each stage artifact contains the prior stage's SHA-256, creating a hash chain. Earlier stage artifacts are never rewritten. Local Git commits provide durable rollback and an additional timestamped commitment; nothing is pushed without separate authorization.

## Deterministic blind selection

The screener request is fixed before collection:

- endpoint: `POST /api/v1/token-screener`;
- chains: `solana`, `ethereum`, `base`, `bnb`, `arbitrum`;
- timeframe: `24h`;
- page 1 with `per_page: 1000`;
- `trader_type: sm`;
- `include_stablecoins: false`;
- minimum token age: 3 days;
- minimum market cap: `$1,000,000`;
- minimum on-chain liquidity: `$250,000`;
- positive finite price, volume, liquidity, market cap, and netflow.

Every token present in any committed prior experiment cohort is excluded. Remaining rows are sorted locally by descending netflow, then ascending chain and normalized token address. The first row is selected. Empty eligibility is terminal `unscorable`; no thresholds are relaxed and no second query is made.

The full response is archived, but the selection claim is only “highest-ranked eligible row in the requested page,” not complete-universe coverage.

## Pre-decision evidence

The selected token receives four additional Nansen requests:

1. `tgm/token-information` with timeframe `1d` for current market, liquidity, holder, and trader context;
2. `tgm/flow-intelligence` with timeframe `1d` for segment-level current flow context;
3. `tgm/flows` with `label=smart_money` for the trailing 25-hour request range;
4. `tgm/flows` with `label=exchange` for the same range.

Both flow requests use page 1, `per_page: 1000`, and ascending date order. The response must declare its first page final. Only buckets with valid RFC 3339 timestamps, `is_complete=true`, and `bucket_end <= available_at` enter features. Exact 1-hour, 4-hour, and 12-hour lags must exist; gaps remain missing. Current response cache/freshness warnings are preserved and surfaced to GPT as availability metadata.

Every request archives:

- endpoint and canonical payload;
- request start and response retrieval timestamps;
- HTTP status and Nansen request ID;
- `X-Nansen-Credits-Cost`, `Used`, and `Remaining` headers when present;
- raw response bytes and SHA-256;
- page and completeness metadata;
- validated response schema version;
- warnings and observed availability limits.

Immediately after selection, `derived/selection.json` freezes the identity-bearing chain/address, exact selected screener row and its canonical hash, screener response hash/retrieval time, authoritative screener `liquidity`, the formula `min(1000, 0.001 * liquidity_usd)`, and its computed virtual notional. This artifact is sealed for later settlement/replay but is never placed in either GPT request. The identity-blinded snapshot contains only `candidate-1`, chain, point-in-time numeric evidence, the fixed formula, and the already-frozen notional.

The snapshot normalizer computes only trailing features already defined by the repository. It does not calculate a fitted score. Missing inputs stay explicit `null` values with reason codes.

## Identity-blinded GPT protocol

The formal model is `gpt-5.6-sol` through the OpenAI Responses API with structured outputs and high reasoning effort. No model substitution is allowed. The model-access preflight and every inference attempt store immutable request state before transmission, then archive the exact response bytes, requested and returned model IDs, response ID, creation time, reasoning configuration, token usage, prompt hash, schema hash, normalized-snapshot hash, and timing metadata. A request with no response artifact after its transmission may have begun is ambiguous and makes the run `unscorable`; it is never rerolled. Exact bit-for-bit model reproducibility is not claimed.

No tools are available to the model. The prompt replaces token symbol, address, name, URL, social metadata, and prior experiment membership with `candidate-1`. It retains chain because execution and market microstructure differ materially by chain. The model receives numeric point-in-time evidence, completeness and freshness flags, feature definitions, the virtual notional, and the fixed four-hour objective.

### Pass 1 — independent analyst

Pass 1 emits exactly:

- `action`: `LONG` or `ABSTAIN`;
- `confidence`: finite number in `[0, 1]`;
- `expected_direction_4h`: `UP`, `FLAT`, or `DOWN`;
- `evidence_for`: bounded list of snapshot field references;
- `evidence_against`: bounded list of snapshot field references;
- `missing_evidence`: bounded list of explicit roles;
- `rationale`: concise text;
- `risk_flags`: bounded enum list.

Every cited field must exist in the normalized snapshot. An archived, structurally parseable response that fails only local schema or citation validation gets one OpenAI-only repair attempt with the validation errors; refusal, HTTP failure, timeout, missing response bytes, or ambiguous transmission does not. Attempt-indexed immutable request/response artifacts preserve the invalid response, and a write-once final pointer names the validated attempt, exact normalized-snapshot hash, and request/response hashes. A second validation failure makes the experiment `unscorable` without consuming more Nansen credits.

### Pass 2 — frozen-strategy critic

Pass 2 receives the SHA-256 of the exact archived Pass 1 response bytes, the validated Pass 1 value, the exact same normalized snapshot and its sealed SHA-256, and the six frozen schema-v3 strategy records. The adapter rejects any Pass 2 snapshot-hash mismatch. It receives no prior feasibility summaries, selection verdict, historical returns, token identity, or future evidence. Re-serializing the parsed Pass 1 value is never used as the artifact hash.

It emits:

- `pass1_assessment`: `UPHOLD` or `OVERRULE`;
- `final_action`: `LONG` or `ABSTAIN`;
- one structured applicability assessment for each frozen record;
- conflicts between Pass 1 and frozen predicates;
- the same bounded evidence-reference and missing-evidence fields;
- a concise rationale.

Pass 2 cannot modify Pass 1. Both decisions are scored separately; the critiqued final action is the headline GPT decision.

## Frozen comparator semantics

The evaluator imports the six records by hash from the committed schema-v3 manifest and evaluates their predicates deterministically on the sealed snapshot.

- Entry, reference, and comparison records emit `LONG` when their unchanged predicates fire and `ABSTAIN` otherwise.
- The distribution veto is not treated as a standalone short. It records whether it would suppress a long and is applied to each firing long comparator as a paired risk-control variant.
- A predicate with missing or stale inputs is `UNAVAILABLE`, never false.
- The previously blocked buyer-breadth/exchange-confirmation theory remains blocked because this pilot does not spend calls on complete buyer/seller address pagination.

All applicable long decisions use the same prospective entry and exit observations. The common four-hour outcome standardizes the decision comparison; it does not rewrite the frozen records' native feasibility horizons.

## Prospective execution evidence

The decision seal time is `t0`. It must be greater than or equal to every sealed request, response-retrieval, and durable-write timestamp in the snapshot/model/comparator chain and must not precede the prior seal. A wall-clock rollback or contradictory provider timestamp terminates `unscorable`; the runner never backdates `t0` or opens an entry window before the decision evidence exists.

- Entry observation window: `[t0 + 5 minutes, t0 + 10 minutes)`.
- Exit observation window: `[entry_window_start + 4 hours, entry_window_start + 4 hours + 5 minutes)`.
- DEX-trade endpoint: `POST /api/v1/tgm/dex-trades`.
- Page size: 1000.
- Request and deterministic local ordering: `block_timestamp ASC`, then `transaction_hash ASC`.
- Entry side: observed `BUY` trades.
- Exit side: observed `SELL` trades.

The entry request filters to `action=BUY`; the exit request filters to `action=SELL`. For the entry, chronological observed trades are fractionally accumulated until their estimated USD value reaches the fixed liquidity-aware virtual notional; the corresponding token quantity and value-weighted price form the paper fill. For the exit, chronological sells are accumulated until they cover the entry token quantity. Fractional use of the final observed trade is allowed. Every candidate row must have finite positive amount, price, and estimated value, and `amount * price` must agree with estimated value within `max($0.01, 1% of estimated_value_usd)`. Any violation invalidates the complete window rather than dropping a row.

Each window may consume at most two pages. If page 2 is still not the last page, pagination contains duplicates or non-monotonic timestamps, or its boundary cannot be validated, the window is incomplete and the experiment becomes `unscorable`; no partial fill is cherry-picked. If the complete window has insufficient eligible volume, every long decision is `UNFILLED` and receives no return score rather than zero.

If no GPT, base-comparator, or paired-veto decision has action `LONG`, no fill is required: the runner records that fact and makes no DEX-trade request. This includes mixtures of `ABSTAIN` and `UNAVAILABLE`; unavailable evidence never creates an unnecessary outcome call.

One final `tgm/token-ohlcv` request retrieves closed 5-minute candles covering `t0` through the settled exit. The first required candle starts at `floor_5m(t0)`; the last starts at `floor_5m(exit_window_end - epsilon)` and ends at the next five-minute boundary. The request uses those aligned outer bounds and locally excludes a provider row whose `interval_start` equals the exclusive last end. It supplies an independent price-path check, MFE/MAE description, volume, and market-cap context. The response must form that exact contiguous five-minute UTC grid, and every admitted candle must have closed by retrieval time. The still-open last candle is excluded. A truncated response, duplicate interval, cadence error, or missing interior, entry, or exit candle makes the outcome `unscorable`.

Observed DEX transaction prices provide the paper execution proxy and embed contemporaneous swap-price conditions. Gas and the pilot's own market impact are not observed, so the result is explicitly not an executable-route or actual-cost claim.

## Call and credit budget

The ledger reserves:

1. one token-screener call;
2. one token-information call;
3. one flow-intelligence call;
4. one Smart-Money flows call;
5. one exchange flows call;
6. up to two entry DEX-trade pages;
7. up to two exit DEX-trade pages;
8. one settled OHLCV call.

The maximum is ten billable Nansen calls. The pinned live OpenAPI contract records every selected endpoint at one credit per call for both `free` and `pro`, so the maximum is also ten Nansen credits. Unneeded second pages are not called. Immediately before credentialed work, a public unauthenticated fetch of the live OpenAPI document must match the pinned full-document SHA-256; drift or an unavailable contract prevents all paid calls. The process then validates both credentials locally, requires Nansen's account body to report a `free` or `pro` plan and at least ten remaining credits, requires matching headers with zero account credit cost/use, and checks that the exact OpenAI model is accessible without substituting another model. Every successful paid response must explicitly report valid cost/use/remaining headers; missing/malformed headers or cost drift is archived, conservatively ledgered, and stops the run before another request.

A persistent budget ledger writes an immutable, hash-linked transition journal plus an atomically replaced mutable head before and after every request, including the nominally free account preflight. It records a stable logical-request ID, canonical method/endpoint/payload hash, `reserved`, `retryable_zero`, `confirmed_zero`, `confirmed_used`, `failed_before_pricing`, or `ambiguous`, plus a persisted attempt count. The exact Nansen attempt request artifact is installed and ledgered before transmission, so a timeout still leaves auditable request bytes; response evidence is installed separately. Every stage seal includes an immutable cumulative ledger snapshot and journal-head hash, so later settlement accounting cannot invalidate earlier seals and offline replay reconstructs the exact calls and credits. The account preflight reserves one conservative credit/call against undocumented pricing drift, then releases both only after explicit zero cost/use is confirmed. Any nonzero or ambiguous transmitted account result is archived and seals the experiment `unscorable`, so reinvocation cannot spend again. An ambiguous request consumes one conservative budget slot. The client refuses a request that could exceed either ceiling.

Only a 429 response with explicit zero credit use and an integer `Retry-After` from 0 through 60 seconds may enter `retryable_zero`. The same reservation records its retry deadline and may transition back to `reserved` exactly once, with attempt count two. A crash leaves enough state to resume that one retry or conservatively reconcile an in-flight `reserved` attempt to `ambiguous`; it can never create a fresh reservation for the same logical call.

OpenAI requests are separate from the Nansen ceiling. The pilot allows one normal call for each of the two passes plus at most one schema-repair call per pass, with bounded structured output. The model-access preflight and all OpenAI usage are archived and hash-bound.

## Scoring and verdict

For every `LONG` decision with complete fills:

`net_paper_return = exit_observed_usd / virtual_notional_usd - 1`

`ABSTAIN` has a cash return of exactly zero. `UNAVAILABLE`, `UNFILLED`, and `UNSCORABLE` are not converted to zero.

The report shows:

- Pass 1 return;
- Pass 2 return;
- every base frozen comparator return or non-applicability reason;
- every veto-paired comparator return or non-applicability reason;
- cash benchmark;
- gross OHLCV move and divergence from observed-fill return;
- confidence and decision-regret descriptions.

The headline `gpt_beats_frozen_strategies` is true only when:

1. the experiment reaches `settled`;
2. Pass 2 is scorable;
3. every comparison-capable entry/reference/comparison base predicate is resolved as available, whether it fires or not;
4. every applicable base or paired-veto comparator is scorable;
5. Pass 2 net return is strictly greater than the maximum applicable frozen-comparator return; and
6. at least one frozen comparator is applicable.

A tie is not a win. An unavailable comparison-capable base blocks a `true` headline because it might have fired; the headline is `unscorable` even if GPT beats every other resolved comparator. If all such bases resolve but none is applicable, GPT is compared with cash descriptively and the headline verdict is `not_tested`. One positive result remains a pilot observation, not an advancement decision.

## Failure handling

The system fails closed:

- no eligible candidate: terminal `unscorable`;
- malformed, non-finite, or schema-invalid response: archive and stop;
- missing complete lag history: affected features and predicates become unavailable;
- rate limit with an explicit unused-credit response: respect `Retry-After` within the same reserved slot;
- timeout or connection loss after request transmission: mark ambiguous and do not retry automatically;
- Nansen cost header above one credit: stop before the next request;
- cumulative calls or credits reaching ten: refuse further billable calls;
- incomplete second DEX page, inadequate observed volume, truncated OHLCV, or missing candle: no score;
- invalid GPT structured output after one repair: no score;
- clock moves backward or stage is invoked early: refuse the transition;
- artifact collision or hash mismatch: retain the installed artifact, place different newly received bytes plus metadata in content-addressed collision quarantine, stop, and report corruption.

Raw response installation reuses the repository's durable atomic response-plus-sidecar transaction. Secrets are read only from the environment and redacted from exceptions.

Stage advancement is a recoverable transaction. Before installing a stage seal, the runner records the exact prior-manifest hash, proposed seal hash, artifact hashes, immutable budget-snapshot hash, and proposed manifest hash. On restart it may adopt only that exact orphan seal/manifest transition; any divergent bytes terminate as corruption. Likewise, a stable logical request may adopt and ledger an already archived response after a crash, but a reserved attempt with no response artifact is reconciled to `ambiguous` and never retransmitted.

## Repository outputs

The experiment lives under `research/experiments/2026-08-17-gpt-prospective-pilot/` and contains:

- `manifest.json`: schema-v4 identity, status, lineage, budgets, and artifact hashes;
- `preregistration.json`: immutable selection, model, feature, execution, and scoring contract;
- `PREREGISTRATION.md`: immutable human-readable pre-call status and win rule;
- `budget/journal/<sequence>-<sha256>.json`, `budget/head.json`, and `budget/snapshots/<stage>.json`: immutable accounting transitions, mutable recovery head, and seal-bound cumulative snapshots;
- `raw/contracts/nansen-openapi.json`: exact bytes from the pre-call public contract recheck;
- `raw/nansen/<reservation-id>/attempt-<n>-*`: exact Nansen response bytes and request/provenance sidecars for every initial or permitted retry attempt;
- `normalized/snapshot.json`: identity-blinded model input plus completeness metadata;
- `derived/selection.json`: sealed identity-bearing selection, screener lineage, and virtual notional, excluded from GPT;
- `model/pass-1/attempt-<n>-request.json`, `attempt-<n>-response.json`, and write-once `final.json`;
- `model/pass-2/attempt-<n>-request.json`, `attempt-<n>-response.json`, and write-once `final.json`;
- `seals/decision.json`, `seals/entry.json`, and `seals/outcome.json`;
- `derived/comparator-decisions.json`;
- `derived/fills.json`;
- `derived/comparison.json`;
- `REPORT.md`: write-once terminal report, generated before and included in the `settled` or `unscorable` outcome seal.

The final task appends, rather than rewrites, `docs/RESEARCH-LEDGER.md` and `docs/RESEARCH-GRAPH.md`, and adds a concise README/architecture pointer. The report records null results, aborted stages, and adverse findings as carefully as positive ones.

## Testing

Implementation follows red-green-refactor cycles with fake Nansen, OpenAI, and clock adapters before any live call.

Tests cover:

- strict schema-v4 lifecycle transitions and append-only hash chaining;
- exact screener payload, prior-token exclusion, stable sorting, and no threshold relaxation;
- response-header credit accounting, hash-linked ledger replay, immutable per-stage snapshots, and refusal of an eleventh call or credit;
- ambiguous paid-call handling without automatic retry;
- complete-bucket filtering, exact lags, gaps, stale availability, and non-finite values;
- identity and outcome leakage scans over both GPT request artifacts;
- structured-output schemas, cited-field validation, one-repair limit, and Pass 1 immutability;
- exact import and evaluation of all six frozen records;
- paired-veto semantics and blocked-theory preservation;
- stable trade ordering, two-page ceiling, fractional final-trade fills, insufficient volume, and literal hand-calculated returns;
- closed-candle selection, exact five-minute cadence, truncation, missing outcomes, trade-value consistency, and DEX/OHLCV divergence;
- strict headline win, tie, cash, no-applicable-comparator, unfilled, and unscorable verdicts;
- atomic artifacts, content-addressed collision quarantine, crash-boundary stage/request recovery, secret redaction, deterministic offline replay, and byte-stable `--check` behavior;
- unchanged schema-v1/v2/v3 hashes and the full existing regression suite.

A zero-credit dry run must reproduce the entire lifecycle from fixtures. The live start command is separate, explicit, and prints the maximum remaining spend before proceeding. The settlement command refuses to run before the recorded deadline.

## Contract references checked 2026-08-17

- Nansen: [live official OpenAPI](https://api.nansen.ai/openapi.json) (download SHA-256 `d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548`; [pinned relevant extract](./2026-08-17-nansen-api-contract-snapshot.json)), [Token Screener](https://docs.nansen.ai/api/token-god-mode/token-screener), [Token Information](https://docs.nansen.ai/api/token-god-mode/token-information), [Flow Intelligence](https://docs.nansen.ai/api/token-god-mode/flow-intelligence), [Flows](https://docs.nansen.ai/api/token-god-mode/flows), [DEX Trades](https://docs.nansen.ai/api/token-god-mode/dex-trades), [Price OHLCV](https://docs.nansen.ai/api/token-god-mode/price-ohlcv), and [Credits](https://docs.nansen.ai/getting-started/credits).
- OpenAI: [model catalog and model capabilities](https://developers.openai.com/api/docs/models).

The implementation pins request payloads in tests and fails closed on contract drift; these links document the live surfaces but do not replace archived request/response evidence.

## Completion boundary

Implementation is complete only when the code, tests, schema-v4 fixture bundle, zero-credit dry run, documentation, and design/implementation records are committed and independently reviewed. The real pilot then has two explicit operational stages:

1. start and seal the point-in-time decision within the ten-call ceiling;
2. settle after the four-hour outcome window and commit the complete report.

No push, PR mutation, or publication is part of this design.
