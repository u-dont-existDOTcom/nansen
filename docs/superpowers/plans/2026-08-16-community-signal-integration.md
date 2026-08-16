# Community Signal Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independently implemented, point-in-time-safe community-inspired signal components, provenance-safe evidence collectors, and a reproducible v2 companion experiment without changing the immutable v1 pilot.

**Architecture:** A pure `signals.py` module derives only trailing features from completed hourly Smart-Money balance snapshots. Existing schema-v1 analysis stays byte-identical; a schema-v2 companion manifest references the v1 source manifest by hash and emits a separate deterministic signal table. CLI collectors archive exact exchange-flow and buyer/seller requests without making calls unless the user explicitly invokes them.

**Tech Stack:** Python 3.12, standard-library dataclasses/CSV/JSON/datetime, pytest, existing cache-first Nansen REST client, Markdown and Mermaid.

## Global Constraints

- Preserve all schema-v1 raw files and derived CSVs byte-for-byte.
- Use `bucket_end` as the earliest feature availability timestamp.
- Never use selection-time market cap, liquidity, netflow, or role metadata as model inputs before their recorded availability.
- Call `holders_count`-derived metrics holder breadth, never buyer breadth.
- Do not infer transfer attribution from paired Smart-Money and exchange balance changes.
- Do not copy code from Smart Money Rotation Radar; its repository has no declared license.
- Do not adopt community performance claims or fixed score weights as expected returns.
- Do not make automatic paid API calls.
- Do not publish raw Smart Money holdings where Nansen redistribution rules prohibit publication.
- Use strict red-green-refactor for every production behavior change.

## File structure

- Create `src/nansen_signal_lab/signals.py`: pure trailing signal calculations with no I/O, selection metadata, or forward labels.
- Create `tests/test_signals.py`: hand-derived behavioral tests for every signal and availability rule.
- Modify `src/nansen_signal_lab/cli.py`: generic provenance writer, labelled flow collection, and `who-bought-sold` collection.
- Create `tests/test_cli.py`: collector payload, provenance, collision, validation, and overwrite tests.
- Modify `src/nansen_signal_lab/experiment.py`: strict schema-v2 companion-manifest loading, source-manifest hashing, deterministic signal rendering, and schema dispatch.
- Modify `tests/test_experiment.py`: v1 stability and v2 validation/rendering coverage.
- Create `research/experiments/2026-08-16-community-signal-shadow/manifest.json`: discovery-only v2 companion manifest.
- Create `research/experiments/2026-08-16-community-signal-shadow/derived/signal-features.csv`: deterministic output derived from the immutable pilot.
- Create `research/experiments/2026-08-16-community-signal-shadow/REPORT.md`: formulas, descriptive findings, limitations, and next data collection.
- Modify `docs/RESEARCH-LEDGER.md`, `docs/RESEARCH-GRAPH.md`, `docs/ARCHITECTURE.md`, and `README.md`: durable research memory and usage.

---

### Task 1: Pure trailing community signal features

**Files:**
- Create: `src/nansen_signal_lab/signals.py`
- Create: `tests/test_signals.py`

**Interfaces:**
- Consumes: ordered hourly feature dictionaries containing `timestamp`, `price_usd`, `token_amount`, `holders_count`, and identity fields.
- Produces: `build_signal_features(features: tuple[dict[str, Any], ...], *, horizons: tuple[int, ...], source_experiment_id: str, feature_set_version: str) -> tuple[dict[str, Any], ...]`.
- Produces only trailing features. It must not accept selection metadata or emit any `forward_`, `mfe_`, or `mae_` field.

- [ ] **Step 1: Write the failing happy-path test with literal expectations**

Create a seven-hour fixture with UTC timestamps, prices `[100, 99, 98, 100, 101, 100, 99]`, holdings `[100, 110, 120, 115, 125, 140, 150]`, and holders `[2, 3, 4, 4, 5, 6, 7]`. Assert at hour 6 for horizon 2:

```python
assert row["holdings_change_2h_pct"] == pytest.approx(20.0)
assert row["price_return_2h_pct"] == pytest.approx(-1.9801980198019802)
assert row["positive_holdings_delta_hours_2h"] == 2
assert row["negative_holdings_delta_hours_2h"] == 0
assert row["accumulation_persistence_2h"] == 1.0
assert row["distribution_persistence_2h"] == 0.0
assert row["holdings_velocity_2h_pct_per_hour"] == pytest.approx(10.0)
assert row["holdings_acceleration_2h_pct_per_hour"] == pytest.approx(
    10.0 - ((125 / 115 - 1) * 100 / 2)
)
assert row["holder_count_change_2h"] == 2
assert row["accumulation_retention_2h"] == 1.0
assert row["flow_price_divergence_2h_pct"] == pytest.approx(
    20.0 - (-1.9801980198019802)
)
assert row["market_phase_2h"] == "accumulation_divergence"
```

The production mutation this test catches is a wrong window boundary, use of future rows, overlapping acceleration windows, or wrong phase sign.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_signals.py::test_build_signal_features_uses_disjoint_trailing_windows -q
```

Expected: collection fails because `src.nansen_signal_lab.signals` does not exist.

- [ ] **Step 3: Implement the minimal pure calculator**

Implement:

```python
SUPPORTED_FEATURE_SET = "community-signals-v1"

def build_signal_features(
    features: tuple[dict[str, Any], ...],
    *,
    horizons: tuple[int, ...],
    source_experiment_id: str,
    feature_set_version: str,
) -> tuple[dict[str, Any], ...]:
    ...
```

For each timestamp and horizon, require exact hourly points from `t-h` through `t`. Acceleration additionally requires `t-2h` through `t`. Compute:

```python
holdings_change_pct = 100 * (end_holdings / start_holdings - 1)
price_return_pct = 100 * (end_price / start_price - 1)
velocity = holdings_change_pct / horizon
acceleration = velocity - prior_disjoint_velocity
retention = max(end_holdings - start_holdings, 0) / gross_positive_deltas
divergence = holdings_change_pct - price_return_pct
```

Return `None` for a metric whose complete inputs are unavailable. Phase rules are:

```python
if holdings_change_pct > 0 and price_return_pct <= 0:
    phase = "accumulation_divergence"
elif holdings_change_pct > 0 and price_return_pct > 0:
    phase = "markup"
elif holdings_change_pct < 0 and price_return_pct >= 0:
    phase = "distribution_divergence"
elif holdings_change_pct < 0 and price_return_pct < 0:
    phase = "markdown"
else:
    phase = "flat"
```

Rows without a complete window use `unavailable` phase. Preserve identity and raw availability columns only; never copy arbitrary input keys.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Step 2 command. Expected: one test passes.

- [ ] **Step 5: Add failing availability, naming, and edge-case tests**

Add literal tests proving:

- a missing intermediate hour blanks every feature for that horizon;
- acceleration is blank with fewer than `2h` contiguous hours;
- zero gross positive deltas produce `None` retention;
- missing intermediate holder counts blank only holder breadth, not price/holdings features;
- flat holdings produce `flat` even when price moves;
- the output has no `selection_`, `buyer`, `forward_`, `mfe_`, or `mae_` field;
- unsupported feature-set names and non-positive/duplicate horizons raise `SignalError`.

Run each newly added test before implementation and confirm it fails for the intended missing validation or edge behavior.

- [ ] **Step 6: Implement edge behavior and refactor while green**

Use small helpers `_parse_timestamp`, `_contiguous_rows`, `_safe_percent_change`, and `_market_phase`. Reject duplicate timestamps and non-finite/non-positive prices or negative holdings at the pure-module boundary. Keep the public function under 80 lines by delegating calculations.

- [ ] **Step 7: Verify Task 1**

Run:

```bash
.venv/bin/python -m pytest tests/test_signals.py -q
.venv/bin/python -m pytest -q
git diff --check
```

Expected: signal tests and all pre-existing tests pass; v1 files remain unmodified.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/nansen_signal_lab/signals.py tests/test_signals.py
git commit -m "Add trailing community signal features"
```

---

### Task 2: Provenance-safe exchange-flow and buyer-breadth collectors

**Files:**
- Modify: `src/nansen_signal_lab/cli.py`
- Create: `tests/test_cli.py`
- Modify: `tests/test_experiment.py` only where an existing writer test moves to the new generic interface.

**Interfaces:**
- Produces: `write_api_artifacts(*, body, payload, endpoint, output_path, cache_hit, response_retrieved_at, artifact_written_at, overwrite=True) -> tuple[Path, Path]`.
- Preserves: `write_flow_artifacts(...)` as a compatibility wrapper passing `endpoint="tgm/flows"`.
- Extends: `flows --label {smart_money,exchange}`, default `smart_money`.
- Adds: `who-bought-sold --chain CHAIN --token ADDRESS --side {BUY,SELL} --from ISO --to ISO [--labels ...] [--min-volume-usd N] [--limit N] [--output PATH] [--force-output] [--refresh]`.

- [ ] **Step 1: Write failing generic-writer and labelled-flow tests**

Assert that `write_api_artifacts` records the literal endpoint, exact payload, response hash, cache provenance, and artifact timestamps without an API key. Assert `flows --label exchange` sends `"label": "exchange"` and defaults to a collision-free `results/flows-exchange-CHAIN-TOKEN.json`; retain the historical Smart-Money default filename for backward compatibility.

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_exchange_flows_preserve_label_and_use_distinct_default_path -q
```

Expected: RED because the parser rejects `--label` or the generic writer is absent.

- [ ] **Step 2: Implement the generic writer and labelled flow command**

Move the existing atomic response/sidecar logic into `write_api_artifacts`. The sidecar schema stays version 2 and contains:

```python
{
    "schema_version": 2,
    "endpoint": endpoint,
    "payload": payload,
    "cache_hit": bool(cache_hit),
    "response_retrieved_at": response_retrieved_at,
    "artifact_written_at": artifact_written_at,
    "response_file": response_path.name,
    "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
}
```

Validate the flow label in argparse choices. For default output use the legacy name for `smart_money` and prefix `flows-exchange-` for `exchange`.

- [ ] **Step 3: Verify GREEN for labelled flows and existing writer behavior**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_experiment.py -q
```

Expected: the focused new test and all existing writer/flow tests pass.

- [ ] **Step 4: Write failing buyer/seller payload and completeness tests**

Use a fake client and assert the exact BUY payload:

```python
{
    "chain": "base",
    "token_address": "0xtoken",
    "buy_or_sell": "BUY",
    "date": {"from": "2026-08-01T00:00:00Z", "to": "2026-08-02T00:00:00Z"},
    "pagination": {"page": 1, "per_page": 20},
    "filters": {
        "include_smart_money_labels": ["Fund", "Smart Trader", "30D Smart Trader"],
        "trade_volume_usd": {"min": 1000.0},
    },
    "order_by": [{"field": "bought_volume_usd", "direction": "DESC"}],
}
```

Assert SELL changes only side and ordering field. Assert naïve timestamps, `from >= to`, limits outside `1..100`, negative volume, duplicate/empty labels, malformed `data`, and malformed pagination fail before artifact writing. A response whose `is_last_page` is false is archived but prints and records a `pagination_complete: false` warning rather than representing the row count as complete breadth.

- [ ] **Step 5: Implement `who-bought-sold` with exact provenance**

Use endpoint `tgm/who-bought-sold`. Parse both timestamps with timezone awareness and normalize to UTC `Z`. Validate response `data` as a list and pagination as an object with boolean `is_last_page`. Add sidecar `response_metadata` containing only:

```python
{
    "row_count": len(body["data"]),
    "pagination_complete": body["pagination"]["is_last_page"],
}
```

Default filename: `results/who-bought-sold-SIDE-CHAIN-TOKEN.json`. Refuse explicit response or sidecar overwrite before constructing the API client unless `--force-output` is present.

- [ ] **Step 6: Verify Task 2**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_experiment.py -q
.venv/bin/python -m pytest -q
git diff --check
```

Expected: all tests pass and no network call occurs.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/nansen_signal_lab/cli.py tests/test_cli.py tests/test_experiment.py
git commit -m "Add signal evidence collectors"
```

---

### Task 3: Schema-v2 companion experiment and deterministic signal table

**Files:**
- Modify: `src/nansen_signal_lab/experiment.py`
- Modify: `tests/test_experiment.py`
- Create: `research/experiments/2026-08-16-community-signal-shadow/manifest.json`
- Generate: `research/experiments/2026-08-16-community-signal-shadow/derived/signal-features.csv`

**Interfaces:**
- Produces: `SignalBundle` dataclass with `root`, `manifest_path`, `manifest`, and validated `source_bundle`.
- Produces: `load_signal_manifest(path: str | Path) -> SignalBundle`.
- Extends: `analyze_manifest()` dispatches schema 1 to the unchanged v1 renderer and schema 2 to the signal renderer.
- Schema 2 emits exactly one deterministic `signal-features.csv`.

- [ ] **Step 1: Freeze the v1 contract with a failing mutation-sensitive test**

Add a test that copies the committed v1 bundle to a temporary directory, runs `analyze_manifest(..., check=True)`, and asserts the returned filenames are exactly:

```python
("hourly-features.csv", "event-windows.csv", "token-summary.csv")
```

Also capture and assert the committed SHA-256 of each derived file using literal hashes measured before implementation. This catches accidental column or renderer changes.

Run the test before production edits. It should pass as a characterization baseline; then temporarily monkeypatch the expected filename list or renderer to prove the assertion fails, restore it, and record the red-green proof in the task report.

- [ ] **Step 2: Write failing strict schema-v2 validation tests**

Use a minimal temporary v2 manifest with these exact required keys:

```json
{
  "schema_version": 2,
  "experiment_id": "signal-shadow",
  "title": "Signal shadow",
  "status": "discovery",
  "created_at": "2026-08-16T12:00:00Z",
  "hypothesis": "Trailing community-inspired components may separate regimes.",
  "feature_set_version": "community-signals-v1",
  "horizons_hours": [1, 2],
  "source_manifest": "../source/manifest.json",
  "source_manifest_sha256": "2662998bdf21d2d3b80d11003b8e62db59e1ebee68ad33858f2bf0008c8a93d0",
  "point_in_time_guarantee": "unknown",
  "availability_policy": "bucket_end"
}
```

Assert failures for unknown/missing keys, path escape outside the experiments directory, hash mismatch, non-v1 source, unsupported feature set, horizons not contained in source horizons, status `holdout` with guarantee `unknown`, invalid guarantees, and any availability policy other than `bucket_end`.

- [ ] **Step 3: Implement minimal v2 loading without altering v1 loading**

Add a schema peek helper used only by `analyze_manifest`. Keep `load_and_validate_manifest` as the v1 entry point. `load_signal_manifest` resolves the source path within the common `research/experiments` directory, verifies its SHA-256 before loading, calls the existing v1 validator, and returns `SignalBundle`.

Allowed guarantees are `provider_pit`, `live_snapshot`, and `unknown`. Discovery accepts all three; holdout accepts only the first two.

- [ ] **Step 4: Verify schema validation GREEN**

Run the focused v2 validator tests. Expected: all pass and every pre-existing manifest test remains green.

- [ ] **Step 5: Write failing deterministic renderer tests**

Build source hourly features with the existing `build_analysis`, pass each token series to `build_signal_features`, and assert:

- signal rows sort by `(chain, symbol, timestamp)`;
- all fields exactly match `signal_fieldnames(horizons)`;
- no `selection_`, `buyer`, or forward-label columns exist;
- `analyze_manifest(check=False)` writes one atomic CSV;
- `check=True` succeeds for identical bytes and fails after one-byte mutation;
- repeated rendering is byte-identical.

- [ ] **Step 6: Implement v2 rendering and dispatch**

Create `signal_fieldnames(horizons)` from identity fields followed by the twelve horizon-specific fields defined in Task 1. Reuse `csv_text` and the existing temporary-replace output pattern. The schema-v2 output path is `derived/signal-features.csv`.

- [ ] **Step 7: Add and generate the real shadow manifest**

Measure the current v1 manifest SHA-256 and create the real v2 manifest with:

```json
{
  "schema_version": 2,
  "experiment_id": "2026-08-16-community-signal-shadow",
  "title": "Community-inspired trailing signal shadow",
  "status": "discovery",
  "created_at": "2026-08-16T12:00:00Z",
  "hypothesis": "Persistence, acceleration, holder breadth, retention, and price divergence may distinguish accumulation regimes better than flow magnitude alone.",
  "feature_set_version": "community-signals-v1",
  "horizons_hours": [1, 4, 12, 24],
  "source_manifest": "../2026-08-16-seven-token-pilot/manifest.json",
  "source_manifest_sha256": "2662998bdf21d2d3b80d11003b8e62db59e1ebee68ad33858f2bf0008c8a93d0",
  "point_in_time_guarantee": "unknown",
  "availability_policy": "bucket_end"
}
```

The manifest hash above was measured from the committed v1 source manifest before implementation.

Generate and verify:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
```

- [ ] **Step 8: Verify Task 3**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py tests/test_signals.py -q
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
.venv/bin/python -m pytest -q
git diff --check
```

Expected: both versions verify deterministically, all tests pass, and `git diff` shows no v1 raw or derived changes.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/nansen_signal_lab/experiment.py tests/test_experiment.py research/experiments/2026-08-16-community-signal-shadow/manifest.json research/experiments/2026-08-16-community-signal-shadow/derived/signal-features.csv
git commit -m "Add versioned community signal shadow"
```

---

### Task 4: Findings, evidence graph, usage, and publication

**Files:**
- Create: `research/experiments/2026-08-16-community-signal-shadow/REPORT.md`
- Modify: `docs/RESEARCH-LEDGER.md`
- Modify: `docs/RESEARCH-GRAPH.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md`

**Interfaces:**
- Documents the exact formulas and descriptive results from committed v2 bytes.
- Preserves append-only ledger semantics and stable graph node IDs.

- [ ] **Step 1: Compute a reproducible descriptive audit from the signal CSV**

Use a read-only Python command to report, per token and horizon, phase counts, maximum persistence, latest acceleration, and divergence. Separately join by exact token/timestamp to the existing event-window forward labels for descriptive analysis only; do not write a fitted score or claim independent observations.

Record the command and its output in the task report so every number placed in `REPORT.md` is traceable.

- [ ] **Step 2: Write the shadow report**

The report must state:

- source bundle and manifest hash;
- why this is a discovery shadow rather than a new backtest;
- exact feature formulas and availability rules;
- per-token descriptive findings supported by committed rows;
- that the seven tokens cannot determine weights or predictive accuracy;
- that `point_in_time_guarantee=unknown` prevents holdout use;
- that buyer breadth, exchange withdrawal, label-specific rotation, historical liquidity, and execution costs remain missing;
- the next collection order: point-in-time state, buyer/seller breadth, exchange-labelled flows, then transfer attribution;
- Nansen attribution and community source links with license caveats.

- [ ] **Step 3: Append the ledger and graph**

Append a ledger entry `2026-08-16 — Community-signal candidate audit` with adopted components, rejected shortcuts, confidence, and replication status.

Add Mermaid nodes with stable IDs:

```text
src_nansen_cli_builds
src_nansen_divergence
src_smrr
lead_supply_control
lead_four_hour_rotation
hyp_persistence_acceleration
hyp_buyer_breadth
hyp_exchange_outflow_confirmation
req_point_in_time_state
exp_20260816_community_signal_shadow
```

Use labeled edges through intermediate nodes so claims remain leads: `inspired_by`, `requires`, `not_yet_replicated`, `tests`, and `blocked_by`. Do not connect a community performance claim to a validated result.

- [ ] **Step 4: Update architecture and README**

Document the v1/v2 split, the `who-bought-sold` and labelled-flow commands, the no-automatic-credit policy, and both deterministic check commands. Explain that `holders_count` measures holder breadth, while buyer breadth requires wallet-level buyer evidence.

- [ ] **Step 5: Verify documentation and the entire branch**

Run fresh:

```bash
.venv/bin/python -m pytest -q
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
git diff --check main...HEAD
git status --short
```

Also verify all v1 raw manifest checksums, the v1 derived file hashes captured in Task 3, absence of `.env`/credentials/cache/results/sidecars from the diff, and exact source links in the report/ledger/graph.

- [ ] **Step 6: Commit Task 4**

```bash
git add research/experiments/2026-08-16-community-signal-shadow/REPORT.md docs/RESEARCH-LEDGER.md docs/RESEARCH-GRAPH.md docs/ARCHITECTURE.md README.md
git commit -m "Record community signal research leads"
```

- [ ] **Step 7: Independent whole-branch review and fix loop**

Request an independent reviewer for the full diff from `118e9cd` to the final task commit. The reviewer must separately assess spec compliance and code quality, with special attention to v1 immutability, timestamp/label leakage, formula correctness, API payload contracts, redistribution, licensing, and misleading prediction claims. Fix all Critical and Important findings through new failing tests before publication.

- [ ] **Step 8: Publish into the existing draft PR**

After final verification, push `codex/research-memory`, confirm draft PR #1 still targets `main`, and update its body with the adopted signal components, explicit missing-data gates, new verification counts, and shadow-report link.
