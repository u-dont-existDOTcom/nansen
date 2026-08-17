# Community Signal Integration Design

Date: 2026-08-16
Status: approved

## Objective

Integrate the strongest reproducible concepts from public Nansen community projects into the research harness without copying unlicensed code, accepting unverified performance claims, mutating the immutable v1 pilot, or introducing point-in-time leakage.

The result is a versioned v2 research surface that exposes measurable signal components. It does not publish a fitted composite score or claim predictive performance before a sufficiently broad discovery sample and untouched holdout exist.

## Evidence assessment

The implementation is informed by, but does not treat as validated:

- Nansen's community catalog, which reports projects and submitter claims while explicitly disclaiming affiliation and asking users to perform their own due diligence.
- Nansen Divergence's MIT-licensed concepts of flow-versus-price regimes, wallet diversity, holdings conviction, log-scaled normalization, and prospective outcome tracking.
- Smart Money Rotation Radar's concepts of fresh rotation versus structural holdings, liquidity and breadth gates, staleness, and partial-data penalties. Its repository has no declared license, so no code or expression is copied.
- Supply Control Scanner's unreplicated claim that exchange withdrawal can precede price appreciation. No exact rule, horizon, or public implementation is available.
- Superior Trade's four-hour evaluation cadence and small-sample performance claim. No exact public strategy is available.
- Nansen's point-in-time backtesting release, which makes historical label, price, holder, flow, PnL, screener, and wallet state the required evidence standard for retrospective ROI claims.

Every community claim is recorded as a replication lead rather than a fact about expected returns.

## Versioning and immutability

The existing `2026-08-16-seven-token-pilot` remains a schema-v1 discovery bundle. Its raw files, manifest checksums, and three derived CSV files must remain byte-for-byte reproducible.

New signal components use an explicit feature-set version rather than silently appending columns to v1 outputs. V2 derivation must be opt-in from a manifest declaration. V1 analysis and `analyze --check` behavior remain unchanged.

Candidate-selection market cap, liquidity, and netflow were observed after the historical hourly flow buckets. They remain selection metadata and must not be used as point-in-time event features. A future bundle may use them only when its evidence records an `available_at` no later than the feature timestamp.

## Signal components derivable from current hourly evidence

A focused pure module computes only backward-looking features from contiguous, completed `tgm/flows` rows. Every output is available at the current row's timezone-aware `bucket_end`.

For each configured horizon `h`:

1. `accumulation_persistence_h`: the fraction of the last `h` hourly balance deltas that are strictly positive.
2. `distribution_persistence_h`: the fraction that are strictly negative.
3. `holdings_velocity_h_pct_per_hour`: the point-in-time trailing holdings percentage change divided by `h`.
4. `holdings_acceleration_h_pct_per_hour`: recent `h`-hour velocity minus the immediately preceding, disjoint `h`-hour velocity. It is unavailable until `2h` contiguous history exists.
5. `holder_count_change_h`: the change in Smart-Money holder count over `h` hours. This is holder breadth, never buyer breadth.
6. `accumulation_retention_h`: net positive balance change divided by gross positive balance deltas in the window. It is unavailable when gross accumulation is zero.
7. `flow_price_divergence_h_pct`: trailing holdings percentage change minus trailing price return.
8. `market_phase_h`: one of `accumulation_divergence`, `markup`, `distribution_divergence`, `markdown`, `flat`, or `unavailable`, based only on the signs of trailing holdings and price changes.

Missing or gapped history produces unavailable values; it never shortens a requested window or fills missing data with zero. Non-finite inputs remain rejected by the existing row preparation boundary.

No fixed weights or composite conviction score are added. Weights must be learned or preregistered on a broader discovery cohort and evaluated once on an untouched holdout.

## Signals requiring new evidence

The CLI gains provenance-preserving collection support, but the implementation does not make network calls during tests or automatically spend Nansen credits.

### Exchange-flow confirmation

`flows --label exchange` archives a separate `tgm/flows` response and request sidecar. Paired exchange outflow and Smart-Money accumulation support a co-movement hypothesis only. They do not prove that Smart Money received tokens from an exchange.

True supply-control attribution requires transfer-level counterparty evidence, circulating supply, and explicit exchange/entity labels. That remains disabled until such evidence is archived.

### Buyer breadth

A `who-bought-sold` collector archives wallet-level aggregate DEX buyer or seller rows for a fixed interval. Buyer breadth is the count of qualifying distinct buyer addresses from this endpoint, not `holders_count` and not undocumented `total_inflows_count` fields.

Pagination coverage, query limits, minimum-volume filters, labels, and exact request bounds are persisted. A truncated page must not be represented as complete breadth.

### Structural holdings and point-in-time state

Historical holdings or point-in-time endpoints are recorded only after their exact official request schema is confirmed. Evidence must include provider-as-of, labels-as-of, prices-as-of, observation time, availability time, endpoint, exact payload, response retrieval time, response hash, and a guarantee kind:

- `provider_pit`: provider explicitly reconstructs historical state as of the requested timestamp;
- `live_snapshot`: observed prospectively and never backdated;
- `unknown`: provenance is insufficient.

Holdout analysis rejects `unknown`. Legacy v1 evidence remains discovery-only.

## Evidence and API boundaries

Durable response writers use the existing atomic, overwrite-safe response-plus-sidecar pattern. Sidecars never contain API keys. New collectors validate timezone-aware intervals, label values, pagination, and response shape before writing.

The manifest validator remains strict. Future v2 evidence records declare a semantic role, provider guarantee, observation bounds, availability timestamp, exact request, row counts, completeness or pagination coverage, and SHA-256. Evidence roles must agree with request labels and token identity.

Raw Smart Money holdings data must not be published where Nansen redistribution rules prohibit it. The public repository may contain permitted derived aggregates and permitted endpoint evidence with attribution; prohibited raw evidence remains local and is referenced by hash/provenance only.

## Research memory

The append-only research ledger receives a new entry describing the community review, the adopted components, rejected shortcuts, confidence, licensing boundaries, and required replications.

The Mermaid graph receives source nodes, hypothesis nodes, data-requirement nodes, and edges distinguishing:

- `inspired_by` concept relationships;
- `requires` evidence dependencies;
- `not_yet_replicated` community claims;
- `tests` future experiments;
- `blocked_by` missing point-in-time or execution evidence.

The graph never connects a community performance claim directly to a validated prediction result.

## Testing

Development follows strict red-green-refactor cycles.

Tests cover:

- v1 byte-for-byte output stability;
- literal, hand-derived persistence, velocity, acceleration, retention, breadth, divergence, and phase expectations;
- gaps, insufficient history, zero gross accumulation, flat values, and non-finite rejection;
- proof that selection-time metadata cannot enter conservative point-in-time signal features;
- explicit exchange labels and exact sidecar payloads;
- buyer/seller request validation, pagination completeness, and overwrite refusal;
- evidence-role versus request-label mismatches;
- provider guarantee enforcement for holdout analysis;
- deterministic v2 rendering and `--check` behavior;
- full regression coverage for the existing CLI, cache, manifest, and analyzer.

Final verification includes the complete test suite, deterministic analysis checks, raw checksum verification, diff hygiene, secret/forbidden-artifact audit, and an independent whole-branch review before pushing the existing draft PR.

## Deliberate exclusions

- No automated trading or order execution.
- No copied community implementation.
- No adoption of the reported 82.4%, 12/17, or +32% values as priors or expected ROI.
- No composite score optimized on seven purposively selected tokens.
- No automatic paid API collection.
- No claim that exchange outflow caused Smart-Money accumulation.
- No retrospective ROI result without point-in-time labels, prices, universe membership, realistic costs, liquidity rules, and fixed exits.
