# Research Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve each Nansen experiment as a validated, immutable evidence bundle with deterministic prediction features, forward labels, a durable lesson ledger, and a GitHub-rendered evidence graph.

**Architecture:** A versioned JSON manifest indexes byte-for-byte raw evidence and its SHA-256 checksums. Pure functions in `experiment.py` validate evidence and transform hourly flows into deterministic CSV tables; the CLI exposes generation and drift checking. Human-reviewed reports, an append-only ledger, and a Mermaid graph explain what the numerical evidence supports.

**Tech Stack:** Python 3.12, standard-library `dataclasses`, `csv`, `datetime`, `hashlib`, `json`, and `pathlib`; existing `argparse`, pandas-free analysis code, and pytest 8.

**Final-review correction:** The no-look-ahead invariant governs over the original sketches below: raw `date` is the source bucket start, while the feature/event timestamp and every horizon lookup use the timezone-aware `bucket_end` availability instant. Both raw boundaries are retained in derived rows. The corrected sketches in this plan reflect the implemented contract.

## Global Constraints

- Never commit `.env`, API credentials, `.venv`, `data/cache/`, or unrelated `results/` scratch files.
- Commit only Nansen Token Screener and `tgm/flows` evidence, with visible "Powered by Nansen API" attribution and links to <https://nansen.ai/> and <https://docs.nansen.ai/guides/redistribution-guide>.
- Raw bundle files are immutable after commit; corrections and follow-ups create new evidence files or bundles.
- Use percentage points for returns: `(end / start - 1) * 100`.
- Exclude incomplete rows and rows missing price or holdings; count exclusions explicitly.
- Reject duplicate timestamps; allow hourly gaps but never calculate a horizon across a gap.
- Require timezone-aware `date` and `bucket_end`, require `bucket_end > date`, and never label finalized bucket contents before `bucket_end`.
- Accept only finite positive prices and finite non-negative holdings; count and exclude invalid metric rows.
- Forward returns, MFE, and MAE are labels only and never model features.
- Missing horizons remain `None` in memory and empty in CSV; never zero-fill them.
- Keep the seven-token pilot marked `discovery`; do not optimize thresholds or claim a fitted trading model.
- Use TDD for every behavior change and make a focused local commit after each task.

---

## File map

- Create `src/nansen_signal_lab/experiment.py`: manifest types, validation, time-series calculations, deterministic CSV generation, and check mode.
- Modify `src/nansen_signal_lab/cli.py`: add `analyze`, optional flow output paths, and request metadata sidecars.
- Modify `src/nansen_signal_lab/client.py`: retain body-returning `post()` and add cache-aware response provenance.
- Create `tests/test_experiment.py`: unit and integration coverage for manifests, calculations, deterministic outputs, and the committed pilot.
- Create `tests/test_client.py`: network, cache-hit, and legacy-cache provenance coverage.
- Modify `tests/test_metrics.py`: add CLI-parser assertions only if parser behavior is not clearer in `test_experiment.py`.
- Create `research/experiments/2026-08-16-seven-token-pilot/`: immutable raw evidence, manifest, derived CSVs, and reviewed report.
- Create `docs/RESEARCH-LEDGER.md`: append-only experiment index and lessons.
- Create `docs/RESEARCH-GRAPH.md`: Mermaid hypothesis/evidence graph.
- Modify `README.md`: explain durable research bundles and validation commands.

---

### Task 1: Manifest model and evidence validation

**Files:**
- Create: `src/nansen_signal_lab/experiment.py`
- Create: `tests/test_experiment.py`

**Interfaces:**
- Produces: `ExperimentError`, `EvidenceFile`, `Bundle`, `sha256_file(path)`, and `load_and_validate_manifest(manifest_path)`.
- Consumes: JSON manifests with `schema_version`, `experiment_id`, `status`, `horizons_hours`, `cohort`, and `evidence`.

- [ ] **Step 1: Write failing tests for valid manifests, path containment, duplicate identities, and checksum drift**

Create temporary raw evidence and a minimal manifest. Include these tests:

```python
from __future__ import annotations

import hashlib
import json
import json
from pathlib import Path

import pytest

from src.nansen_signal_lab.experiment import ExperimentError, load_and_validate_manifest


def write_bundle(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    body = {"data": []}
    raw_path = raw_dir / "flows.json"
    raw_path.write_text(json.dumps(body))
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "experiment_id": "fixture",
        "title": "Fixture",
        "status": "discovery",
        "created_at": "2026-08-16T00:00:00Z",
        "hypothesis": "Fixture hypothesis",
        "horizons_hours": [1, 4, 12, 24],
        "source": {"provider": "Nansen", "attribution": "Powered by Nansen API"},
        "cohort": [{
            "chain": "base",
            "symbol": "FIX",
            "address": "0xfixture",
            "role": "early",
            "flow_evidence_id": "flows-fix",
            "selection": {},
        }],
        "evidence": [{
            "id": "flows-fix",
            "kind": "tgm_flows",
            "path": "raw/flows.json",
            "sha256": digest,
            "endpoint": "tgm/flows",
            "request": {"chain": "base", "token_address": "0xfixture"},
            "retrieved_at": "2026-08-16T00:00:00Z",
            "observed_from": None,
            "observed_to": None,
            "row_count": 0,
            "complete_count": 0,
        }],
        "exclusions": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_manifest_accepts_matching_evidence(tmp_path):
    bundle = load_and_validate_manifest(write_bundle(tmp_path))
    assert bundle.experiment_id == "fixture"
    assert bundle.evidence_by_id["flows-fix"].path.name == "flows.json"


def test_manifest_rejects_checksum_drift(tmp_path):
    manifest = write_bundle(tmp_path)
    (tmp_path / "raw" / "flows.json").write_text('{"data":[1]}')
    with pytest.raises(ExperimentError, match="checksum mismatch"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_path_escape(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"][0]["path"] = "../outside.json"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="outside bundle"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_duplicate_token_identity(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"].append(dict(data["cohort"][0]))
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="duplicate cohort token"):
        load_and_validate_manifest(manifest)
```

- [ ] **Step 2: Run the focused tests and confirm the import fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
```

Expected: collection fails because `src.nansen_signal_lab.experiment` does not exist.

- [ ] **Step 3: Implement the immutable manifest types and validator**

Add these public types and signatures:

```python
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


class ExperimentError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceFile:
    id: str
    kind: str
    path: Path
    sha256: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Bundle:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    evidence: tuple[EvidenceFile, ...]

    @property
    def experiment_id(self) -> str:
        return str(self.manifest["experiment_id"])

    @property
    def evidence_by_id(self) -> dict[str, EvidenceFile]:
        return {item.id: item for item in self.evidence}


def sha256_file(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def load_and_validate_manifest(manifest_path: str | Path) -> Bundle:
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read manifest {path}: {exc}") from exc

    required = {
        "schema_version", "experiment_id", "title", "status", "created_at",
        "hypothesis", "horizons_hours", "source", "cohort", "evidence",
        "exclusions",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ExperimentError(f"manifest missing keys: {', '.join(missing)}")
    if manifest["schema_version"] != 1:
        raise ExperimentError(f"unsupported schema version: {manifest['schema_version']}")
    if manifest["status"] not in {"discovery", "holdout"}:
        raise ExperimentError(f"invalid experiment status: {manifest['status']}")

    horizons = manifest["horizons_hours"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in horizons)
        or len(horizons) != len(set(horizons))
    ):
        raise ExperimentError("horizons_hours must contain unique positive integers")

    root = path.parent.resolve()
    evidence = []
    seen_evidence_ids = set()
    for record in manifest["evidence"]:
        evidence_id = str(record.get("id", ""))
        if not evidence_id or evidence_id in seen_evidence_ids:
            raise ExperimentError(f"duplicate or empty evidence id: {evidence_id}")
        seen_evidence_ids.add(evidence_id)
        evidence_path = (root / str(record.get("path", ""))).resolve()
        if evidence_path != root and root not in evidence_path.parents:
            raise ExperimentError(f"evidence {evidence_id} is outside bundle: {evidence_path}")
        if not evidence_path.is_file():
            raise ExperimentError(f"evidence {evidence_id} is missing: {evidence_path}")
        expected = str(record.get("sha256", ""))
        actual = sha256_file(evidence_path)
        if actual != expected:
            raise ExperimentError(
                f"checksum mismatch for evidence {evidence_id}: expected {expected}, got {actual}"
            )
        evidence.append(EvidenceFile(
            id=evidence_id,
            kind=str(record.get("kind", "")),
            path=evidence_path,
            sha256=expected,
            metadata=dict(record),
        ))

    evidence_ids = {item.id for item in evidence}
    seen_tokens = set()
    for member in manifest["cohort"]:
        address = str(member.get("address", ""))
        normalized_address = address.lower() if address.lower().startswith("0x") else address
        identity = (str(member.get("chain", "")), normalized_address)
        if not all(identity) or identity in seen_tokens:
            raise ExperimentError(f"duplicate cohort token: {identity[0]}:{identity[1]}")
        seen_tokens.add(identity)
        flow_id = str(member.get("flow_evidence_id", ""))
        if flow_id not in evidence_ids:
            raise ExperimentError(f"cohort token {identity[0]}:{identity[1]} has unknown flow evidence {flow_id}")

    return Bundle(
        root=root,
        manifest_path=path,
        manifest=manifest,
        evidence=tuple(evidence),
    )
```

The validator must enforce schema version `1`, statuses `discovery` or `holdout`, strictly positive unique integer horizons, required top-level keys, unique evidence IDs, EVM-case-insensitive but Solana-case-sensitive `(chain, address)` cohort identities, kind/endpoint compatibility, raw flow counts/completeness/observed bounds, consistent request/cohort identity when present, valid flow and candidate evidence references, evidence paths contained under the resolved bundle root, file existence, and exact SHA-256 matches. Build explicit error strings containing the experiment or evidence ID.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Run the existing tests to catch regressions**

Run:

```bash
.venv/bin/python -m pytest tests/test_metrics.py -q
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/nansen_signal_lab/experiment.py tests/test_experiment.py
git commit -m "Add research bundle validation"
```

---

### Task 2: Point-in-time features and forward labels

**Files:**
- Modify: `src/nansen_signal_lab/experiment.py`
- Modify: `tests/test_experiment.py`

**Interfaces:**
- Consumes: `Bundle` from Task 1 and raw `{"data": [...]}` flow bodies.
- Produces: `PreparedRows`, `AnalysisTables`, `prepare_flow_rows(body)`, `build_hourly_features(...)`, `build_event_windows(...)`, `build_token_summary(...)`, and `build_analysis(bundle)`.

- [ ] **Step 1: Add failing tests for incomplete rows, duplicates, gaps, returns, and label maturity**

Use this deterministic hourly fixture:

```python
def flow_row(hour, price, holdings, *, complete=True, holders=2):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=hour)
    end = start + timedelta(hours=1)
    return {
        "date": start.isoformat().replace("+00:00", "Z"),
        "bucket_end": end.isoformat().replace("+00:00", "Z"),
        "is_complete": complete,
        "price_usd": price,
        "token_amount": holdings,
        "value_usd": price * holdings,
        "holders_count": holders,
        "total_inflows_count": 0,
        "total_outflows_count": 0,
    }


def test_prepare_rows_excludes_incomplete_and_counts_it():
    body = {"data": [flow_row(0, 10, 100), flow_row(1, 11, 110, complete=False)]}
    prepared = prepare_flow_rows(body)
    assert len(prepared.rows) == 1
    assert prepared.incomplete_count == 1


def test_prepare_rows_rejects_duplicate_timestamp():
    body = {"data": [flow_row(0, 10, 100), flow_row(0, 11, 110)]}
    with pytest.raises(ExperimentError, match="duplicate timestamp"):
        prepare_flow_rows(body)


def test_feature_and_event_windows_do_not_cross_gap_or_future():
    body = {"data": [
        flow_row(0, 10, 100),
        flow_row(1, 11, 110),
        flow_row(2, 12, 120),
        flow_row(4, 15, 130),
    ]}
    prepared = prepare_flow_rows(body)
    features = build_hourly_features(
        experiment_id="fixture",
        cohort_member={"chain": "base", "symbol": "FIX", "address": "0xfixture", "role": "early", "selection": {}},
        prepared=prepared,
        horizons=(1, 2),
    )
    assert features[1]["trailing_price_return_1h_pct"] == pytest.approx(10.0)
    assert features[-1]["trailing_price_return_2h_pct"] is None
    events = build_event_windows(features, horizons=(1, 2))
    event_available_at_hour_2 = next(
        row for row in events if row["timestamp"] == "2026-08-01T02:00:00Z"
    )
    assert event_available_at_hour_2["forward_price_return_1h_pct"] == pytest.approx(100 * (12 / 11 - 1))
    assert event_available_at_hour_2["forward_price_return_2h_pct"] is None
    assert event_available_at_hour_2["forward_2h_available"] is False


def test_mfe_and_mae_use_only_prices_inside_mature_horizon():
    body = {"data": [
        flow_row(0, 10, 100),
        flow_row(1, 9, 110),
        flow_row(2, 12, 110),
        flow_row(3, 8, 110),
    ]}
    prepared = prepare_flow_rows(body)
    features = build_hourly_features(
        experiment_id="fixture",
        cohort_member={"chain": "base", "symbol": "FIX", "address": "0xfixture", "role": "early", "selection": {}},
        prepared=prepared,
        horizons=(2,),
    )
    event = build_event_windows(features, horizons=(2,))[0]
    assert event["mfe_2h_pct"] == pytest.approx(100 * (12 / 9 - 1))
    assert event["mae_2h_pct"] == pytest.approx(100 * (8 / 9 - 1))
```

Also add a test that a missing/zero price or missing holdings row increments `invalid_metric_count` and is excluded.

- [ ] **Step 2: Run the new calculation tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
```

Expected: failures name the missing preparation and calculation interfaces.

- [ ] **Step 3: Implement preparation and feature calculation**

Add:

```python
@dataclass(frozen=True)
class PreparedRows:
    rows: tuple[dict[str, Any], ...]
    raw_count: int
    incomplete_count: int
    invalid_metric_count: int
    gaps: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AnalysisTables:
    hourly_features: tuple[dict[str, Any], ...]
    event_windows: tuple[dict[str, Any], ...]
    token_summary: tuple[dict[str, Any], ...]


def prepare_flow_rows(body: dict[str, Any]) -> PreparedRows:
    raw_rows = body.get("data")
    if not isinstance(raw_rows, list):
        raise ExperimentError("flow response data must be a list")
    rows = []
    seen = set()
    incomplete_count = 0
    invalid_metric_count = 0
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ExperimentError("flow row must be an object")
        bucket_start = _parse_aware_timestamp(raw.get("date"), field="date")
        timestamp = _parse_aware_timestamp(raw.get("bucket_end"), field="bucket_end")
        if timestamp <= bucket_start:
            raise ExperimentError("flow bucket_end must be after bucket start")
        if timestamp in seen:
            raise ExperimentError(f"duplicate timestamp: {raw.get('bucket_end')}")
        seen.add(timestamp)
        if not raw.get("is_complete", True):
            incomplete_count += 1
            continue
        try:
            price_usd = float(raw.get("price_usd"))
            token_amount = float(raw.get("token_amount"))
        except (TypeError, ValueError):
            invalid_metric_count += 1
            continue
        if not isfinite(price_usd) or price_usd <= 0 or not isfinite(token_amount) or token_amount < 0:
            invalid_metric_count += 1
            continue
        row = dict(raw)
        row["_timestamp"] = timestamp
        row["price_usd"] = price_usd
        row["token_amount"] = token_amount
        rows.append(row)
    rows.sort(key=lambda row: row["_timestamp"])
    gaps = tuple(
        (left["bucket_end"], right["bucket_end"])
        for left, right in zip(rows, rows[1:])
        if right["_timestamp"] - left["_timestamp"] != timedelta(hours=1)
    )
    return PreparedRows(
        rows=tuple(rows),
        raw_count=len(raw_rows),
        incomplete_count=incomplete_count,
        invalid_metric_count=invalid_metric_count,
        gaps=gaps,
    )


def build_hourly_features(
    *,
    experiment_id: str,
    cohort_member: dict[str, Any],
    prepared: PreparedRows,
    horizons: tuple[int, ...],
) -> tuple[dict[str, Any], ...]:
    by_time = {row["_timestamp"]: row for row in prepared.rows}
    selection = dict(cohort_member.get("selection", {}))
    output = []
    for row in prepared.rows:
        timestamp = row["_timestamp"]
        previous = by_time.get(timestamp - timedelta(hours=1))
        delta = None if previous is None else row["token_amount"] - previous["token_amount"]
        feature = {
            "experiment_id": experiment_id,
            "cohort_role": cohort_member["role"],
            "chain": cohort_member["chain"],
            "symbol": cohort_member["symbol"],
            "address": cohort_member["address"],
            "timestamp": row["bucket_end"],
            "source_bucket_start": row["date"],
            "source_bucket_end": row["bucket_end"],
            "price_usd": row["price_usd"],
            "token_amount": row["token_amount"],
            "value_usd": row.get("value_usd"),
            "holders_count": row.get("holders_count"),
            "holdings_delta_tokens": delta,
            "holdings_delta_pct": (
                None if delta is None or previous["token_amount"] == 0
                else 100.0 * delta / previous["token_amount"]
            ),
            "holdings_delta_notional_usd": None if delta is None else delta * row["price_usd"],
            "selection_market_cap_usd": selection.get("market_cap_usd"),
            "selection_liquidity_usd": selection.get("liquidity_usd"),
            "selection_token_age_days": selection.get("token_age_days"),
            "selection_netflow_usd": selection.get("netflow_usd"),
            "selection_flow_mcap_ratio": selection.get("flow_mcap_ratio"),
        }
        for horizon in horizons:
            old = by_time.get(timestamp - timedelta(hours=horizon))
            feature[f"trailing_price_return_{horizon}h_pct"] = (
                None if old is None else 100.0 * (row["price_usd"] / old["price_usd"] - 1.0)
            )
            feature[f"trailing_holdings_change_{horizon}h_pct"] = (
                None if old is None or old["token_amount"] == 0
                else 100.0 * (row["token_amount"] / old["token_amount"] - 1.0)
            )
        output.append(feature)
    return tuple(output)


def build_event_windows(
    features: tuple[dict[str, Any], ...],
    *,
    horizons: tuple[int, ...],
) -> tuple[dict[str, Any], ...]:
    by_time = {
        datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc): row
        for row in features
    }
    events = []
    for feature in features:
        delta = feature["holdings_delta_tokens"]
        if delta in (None, 0):
            continue
        timestamp = datetime.fromisoformat(feature["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
        event = dict(feature)
        event["event_type"] = "accumulation" if delta > 0 else "distribution"
        for horizon in horizons:
            future = [
                by_time.get(timestamp + timedelta(hours=offset))
                for offset in range(1, horizon + 1)
            ]
            available = all(row is not None for row in future)
            event[f"forward_{horizon}h_available"] = available
            if not available:
                event[f"forward_price_return_{horizon}h_pct"] = None
                event[f"mfe_{horizon}h_pct"] = None
                event[f"mae_{horizon}h_pct"] = None
                continue
            start_price = feature["price_usd"]
            returns = [100.0 * (row["price_usd"] / start_price - 1.0) for row in future]
            event[f"forward_price_return_{horizon}h_pct"] = returns[-1]
            event[f"mfe_{horizon}h_pct"] = max(returns)
            event[f"mae_{horizon}h_pct"] = min(returns)
        events.append(event)
    return tuple(events)
```

Parse both source boundaries as timezone-aware UTC datetimes, require increasing bounds, and index only by `bucket_end` availability. Calculate a horizon only when the exact availability timestamp exists. An event is any non-zero one-hour holdings delta. MFE and MAE use prices available at `t+1` through `t+h`, inclusive, only when the endpoint exists and every intervening hour is present.

Hourly rows must include identity columns, `timestamp`, `source_bucket_start`, `source_bucket_end`, source metrics, `holdings_delta_tokens`, `holdings_delta_pct`, `holdings_delta_notional_usd`, and trailing price/holdings columns for each configured horizon. Forward columns appear only in event rows.

- [ ] **Step 4: Implement token summaries and bundle-wide analysis**

Add:

```python
def build_token_summary(
    features: tuple[dict[str, Any], ...],
    events: tuple[dict[str, Any], ...],
    prepared: PreparedRows,
    *,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    if not features:
        raise ExperimentError("cannot summarize an empty token series")
    first, last = features[0], features[-1]
    last_time = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
    by_time = {
        datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc): row
        for row in features
    }
    old_24h = by_time.get(last_time - timedelta(hours=24))

    def change(start: float | None, end: float | None) -> float | None:
        if start in (None, 0) or end is None:
            return None
        return 100.0 * (float(end) / float(start) - 1.0)

    def weighted(key: str) -> float | None:
        pairs = [
            (float(row["holdings_delta_tokens"]), float(row[key]))
            for row in events
            if row["holdings_delta_tokens"] > 0 and row.get(key) is not None
        ]
        if not pairs:
            return None
        total = sum(weight for weight, _ in pairs)
        return sum(weight * value for weight, value in pairs) / total

    deltas = [row["holdings_delta_tokens"] for row in features if row["holdings_delta_tokens"] is not None]
    summary = {
        "experiment_id": first["experiment_id"],
        "cohort_role": first["cohort_role"],
        "chain": first["chain"],
        "symbol": first["symbol"],
        "address": first["address"],
        "observed_from": first["timestamp"],
        "observed_to": last["timestamp"],
        "valid_row_count": len(features),
        "raw_row_count": prepared.raw_count,
        "incomplete_row_count": prepared.incomplete_count,
        "invalid_metric_row_count": prepared.invalid_metric_count,
        "gap_count": len(prepared.gaps),
        "price_return_24h_pct": None if old_24h is None else change(old_24h["price_usd"], last["price_usd"]),
        "holdings_change_24h_pct": None if old_24h is None else change(old_24h["token_amount"], last["token_amount"]),
        "price_return_all_pct": change(first["price_usd"], last["price_usd"]),
        "holdings_change_all_pct": change(first["token_amount"], last["token_amount"]),
        "wallets_change_all": (
            None if first["holders_count"] is None or last["holders_count"] is None
            else int(last["holders_count"] - first["holders_count"])
        ),
        "gross_accumulation_tokens": sum(value for value in deltas if value > 0),
        "gross_distribution_tokens": sum(-value for value in deltas if value < 0),
        "accumulation_event_count": sum(value > 0 for value in deltas),
        "distribution_event_count": sum(value < 0 for value in deltas),
    }
    for horizon in horizons:
        summary[f"accumulation_weighted_trailing_{horizon}h_pct"] = weighted(
            f"trailing_price_return_{horizon}h_pct"
        )
        summary[f"accumulation_weighted_forward_{horizon}h_pct"] = weighted(
            f"forward_price_return_{horizon}h_pct"
        )
    return summary


def build_analysis(bundle: Bundle) -> AnalysisTables:
    horizons = tuple(sorted(int(value) for value in bundle.manifest["horizons_hours"]))
    evidence_by_id = bundle.evidence_by_id
    all_features = []
    all_events = []
    all_summaries = []
    for member in bundle.manifest["cohort"]:
        evidence = evidence_by_id[member["flow_evidence_id"]]
        if evidence.kind != "tgm_flows":
            raise ExperimentError(f"evidence {evidence.id} is not tgm_flows")
        body = json.loads(evidence.path.read_text())
        prepared = prepare_flow_rows(body)
        features = build_hourly_features(
            experiment_id=bundle.experiment_id,
            cohort_member=member,
            prepared=prepared,
            horizons=horizons,
        )
        events = build_event_windows(features, horizons=horizons)
        summary = build_token_summary(features, events, prepared, horizons=horizons)
        all_features.extend(features)
        all_events.extend(events)
        all_summaries.append(summary)
    key = lambda row: (row["chain"], row["symbol"], row.get("timestamp", ""))
    return AnalysisTables(
        hourly_features=tuple(sorted(all_features, key=key)),
        event_windows=tuple(sorted(all_events, key=key)),
        token_summary=tuple(sorted(all_summaries, key=key)),
    )
```

For each cohort token, load the referenced `tgm_flows` response. Sort the combined output by `(chain, symbol, timestamp)`. Summary endpoints use the first and last valid complete row. Gross accumulation sums positive deltas; gross distribution sums absolute negative deltas. Size-weighted returns use only positive-delta events with an available value and weight by positive token delta.

- [ ] **Step 5: Run calculation tests and the full suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass; the existing 3 tests remain green.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/nansen_signal_lab/experiment.py tests/test_experiment.py
git commit -m "Calculate point-in-time research features"
```

---

### Task 3: Deterministic CSV generation and analyze CLI

**Files:**
- Modify: `src/nansen_signal_lab/experiment.py`
- Modify: `src/nansen_signal_lab/cli.py:11-12,115-145`
- Modify: `tests/test_experiment.py`

**Interfaces:**
- Consumes: `AnalysisTables` from Task 2.
- Produces: `render_analysis_csvs(bundle, tables)`, `analyze_manifest(manifest_path, check=False)`, CLI command `analyze --manifest PATH [--check]`.

- [ ] **Step 1: Add failing tests for deterministic generation and drift checking**

```python
def write_bundle_with_four_hour_flow_fixture(tmp_path):
    manifest = write_bundle(tmp_path)
    body = {"data": [
        flow_row(0, 10, 100),
        flow_row(1, 11, 110),
        flow_row(2, 12, 120),
        flow_row(3, 13, 130),
    ]}
    raw_path = tmp_path / "raw" / "flows.json"
    raw_path.write_text(json.dumps(body))
    data = json.loads(manifest.read_text())
    data["horizons_hours"] = [1, 2]
    data["evidence"][0].update({
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "observed_from": "2026-08-01T00:00:00Z",
        "observed_to": "2026-08-01T03:00:00Z",
        "row_count": 4,
        "complete_count": 4,
    })
    manifest.write_text(json.dumps(data))
    return manifest


def test_analyze_writes_deterministic_csvs(tmp_path):
    manifest = write_bundle_with_four_hour_flow_fixture(tmp_path)
    paths = analyze_manifest(manifest)
    first = {path.name: path.read_bytes() for path in paths}
    analyze_manifest(manifest)
    second = {path.name: path.read_bytes() for path in paths}
    assert first == second
    assert set(first) == {"hourly-features.csv", "event-windows.csv", "token-summary.csv"}


def test_analyze_check_rejects_derived_drift(tmp_path):
    manifest = write_bundle_with_four_hour_flow_fixture(tmp_path)
    paths = analyze_manifest(manifest)
    paths[0].write_text("mutated\n")
    with pytest.raises(ExperimentError, match="derived output differs"):
        analyze_manifest(manifest, check=True)


def test_analyze_parser_accepts_manifest_and_check():
    parser = build_parser()
    args = parser.parse_args(["analyze", "--manifest", "bundle/manifest.json", "--check"])
    assert args.manifest == "bundle/manifest.json"
    assert args.check is True
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
```

Expected: failures identify missing CSV rendering, check mode, and CLI parser support.

- [ ] **Step 3: Implement deterministic CSV rendering**

Add stable field lists derived from configured horizons and serialize with:

```python
import csv
from io import StringIO


def csv_text(rows: tuple[dict[str, Any], ...], fieldnames: tuple[str, ...]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fieldnames})
    return output.getvalue()


def analysis_fieldnames(horizons: tuple[int, ...]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    feature = (
        "experiment_id", "cohort_role", "chain", "symbol", "address", "timestamp",
        "source_bucket_start", "source_bucket_end",
        "price_usd", "token_amount", "value_usd", "holders_count",
        "holdings_delta_tokens", "holdings_delta_pct", "holdings_delta_notional_usd",
        "selection_market_cap_usd", "selection_liquidity_usd", "selection_token_age_days",
        "selection_netflow_usd", "selection_flow_mcap_ratio",
    ) + tuple(
        name
        for horizon in horizons
        for name in (
            f"trailing_price_return_{horizon}h_pct",
            f"trailing_holdings_change_{horizon}h_pct",
        )
    )
    event = feature + ("event_type",) + tuple(
        name
        for horizon in horizons
        for name in (
            f"forward_{horizon}h_available",
            f"forward_price_return_{horizon}h_pct",
            f"mfe_{horizon}h_pct",
            f"mae_{horizon}h_pct",
        )
    )
    summary = (
        "experiment_id", "cohort_role", "chain", "symbol", "address",
        "observed_from", "observed_to", "valid_row_count", "raw_row_count",
        "incomplete_row_count", "invalid_metric_row_count", "gap_count",
        "price_return_24h_pct", "holdings_change_24h_pct", "price_return_all_pct",
        "holdings_change_all_pct", "wallets_change_all", "gross_accumulation_tokens",
        "gross_distribution_tokens", "accumulation_event_count", "distribution_event_count",
    ) + tuple(
        name
        for horizon in horizons
        for name in (
            f"accumulation_weighted_trailing_{horizon}h_pct",
            f"accumulation_weighted_forward_{horizon}h_pct",
        )
    )
    return feature, event, summary


def render_analysis_csvs(bundle: Bundle, tables: AnalysisTables) -> dict[str, str]:
    horizons = tuple(sorted(int(value) for value in bundle.manifest["horizons_hours"]))
    feature_fields, event_fields, summary_fields = analysis_fieldnames(horizons)
    return {
        "hourly-features.csv": csv_text(tables.hourly_features, feature_fields),
        "event-windows.csv": csv_text(tables.event_windows, event_fields),
        "token-summary.csv": csv_text(tables.token_summary, summary_fields),
    }


def analyze_manifest(manifest_path: str | Path, *, check: bool = False) -> tuple[Path, ...]:
    bundle = load_and_validate_manifest(manifest_path)
    tables = build_analysis(bundle)
    rendered = render_analysis_csvs(bundle, tables)
    derived = bundle.root / "derived"
    paths = tuple(derived / name for name in rendered)
    if check:
        for path in paths:
            expected = rendered[path.name].encode("utf-8")
            if not path.is_file() or path.read_bytes() != expected:
                raise ExperimentError(f"derived output differs: {path}")
        return paths
    derived.mkdir(parents=True, exist_ok=True)
    for path in paths:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(rendered[path.name], encoding="utf-8", newline="")
        temporary.replace(path)
    return paths
```

Write through temporary sibling files followed by `Path.replace()` so interrupted generation does not leave partial CSVs. In check mode, compare UTF-8 bytes and raise without writing.

- [ ] **Step 4: Add the analyze CLI command**

Import `analyze_manifest`, add `cmd_analyze(args)`, and register:

```python
s = sub.add_parser("analyze", help="generate or verify a committed research bundle")
s.add_argument("--manifest", required=True)
s.add_argument("--check", action="store_true")
s.set_defaults(func=cmd_analyze)
```

`cmd_analyze` prints one line per derived path and prefixes successful check-mode output with `verified:`.

- [ ] **Step 5: Run focused and full verification**

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
.venv/bin/python -m pytest -q
./nansen-lab --help
```

Expected: tests pass and help lists `analyze`.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/nansen_signal_lab/experiment.py src/nansen_signal_lab/cli.py tests/test_experiment.py
git commit -m "Add deterministic experiment analysis"
```

---

### Task 4: Exact request provenance for future flow pulls

**Files:**
- Modify: `src/nansen_signal_lab/cli.py:83-103,132-140`
- Modify: `tests/test_experiment.py`

**Interfaces:**
- Produces: provenance-aware `NansenClient.post_with_provenance()`, `write_flow_artifacts(...)`, `flows --output PATH [--force-output]`, and a `.request.json` sidecar.
- Preserves: the existing body-returning `NansenClient.post()` interface and default ignored `results/` overwrite behavior.

- [ ] **Step 1: Add failing tests for explicit outputs and secret-free metadata**

```python
def test_write_flow_artifacts_preserves_payload_and_response(tmp_path):
    output = tmp_path / "raw" / "cdxr-followup.json"
    payload = {
        "chain": "ethereum",
        "token_address": "0x40aaf75454036bed56f3266ccf18f6b7befd6aca",
        "date": {"from": "2026-08-15T22:00:00Z", "to": "2026-08-16T23:00:00Z"},
        "label": "smart_money",
        "pagination": {"page": 1, "per_page": 100},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }
    response_path, request_path = write_flow_artifacts(
        body={"data": []},
        payload=payload,
        output_path=output,
        cache_hit=True,
        response_retrieved_at="2026-08-16T23:01:00Z",
        artifact_written_at="2026-08-17T00:01:00Z",
    )
    assert json.loads(response_path.read_text()) == {"data": []}
    metadata = json.loads(request_path.read_text())
    assert metadata["endpoint"] == "tgm/flows"
    assert metadata["payload"] == payload
    assert metadata["cache_hit"] is True
    assert metadata["response_retrieved_at"] == "2026-08-16T23:01:00Z"
    assert metadata["artifact_written_at"] == "2026-08-17T00:01:00Z"
    assert metadata["response_sha256"] == hashlib.sha256(response_path.read_bytes()).hexdigest()
    assert "apikey" not in request_path.read_text().lower()


def test_flows_parser_accepts_explicit_output():
    args = build_parser().parse_args([
        "flows", "--chain", "ethereum", "--token", "0xtoken",
        "--days", "2", "--output", "research/raw/flows.json", "--force-output",
    ])
    assert args.output == "research/raw/flows.json"
    assert args.force_output is True
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
```

Expected: missing writer and parser option failures.

- [ ] **Step 3: Implement response and request sidecar writing**

Add:

```python
def write_flow_artifacts(
    *, body, payload, output_path, cache_hit,
    response_retrieved_at, artifact_written_at, overwrite=True,
):
    response_path, request_path = _flow_artifact_paths(output_path)
    response_bytes = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode()
    metadata = {
        "schema_version": 2,
        "endpoint": "tgm/flows",
        "payload": payload,
        "cache_hit": cache_hit,
        "response_retrieved_at": response_retrieved_at,
        "artifact_written_at": artifact_written_at,
        "response_file": response_path.name,
        "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
    }
    # Write and fsync sibling temporary files, then replace both destinations.
    # If overwrite is false, refuse when either destination already exists.
    return response_path, request_path
```

Register `--output` and `--force-output`. When `--output` is omitted, preserve the current `results/flows-{chain}-{token-prefix}.json` response path and overwrite behavior. For an explicit path, refuse before the API call if either response or sidecar exists unless `--force-output` is supplied. Always write both through flushed and fsynced sibling temporary files before replacement. Cache metadata preserves the original network retrieval time; legacy raw-only caches use file mtime rather than a newly invented retrieval. Never include headers, environment values, or the API key.

- [ ] **Step 4: Run focused tests and the full suite**

```bash
.venv/bin/python -m pytest tests/test_experiment.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/nansen_signal_lab/cli.py tests/test_experiment.py
git commit -m "Record flow request provenance"
```

---

### Task 5: Commit the seven-token pilot, ledger, and evidence graph

**Files:**
- Create: `research/experiments/2026-08-16-seven-token-pilot/manifest.json`
- Create: `research/experiments/2026-08-16-seven-token-pilot/raw/candidates-20260816T094029Z.csv`
- Create: seven `research/experiments/2026-08-16-seven-token-pilot/raw/flows-*.json` files
- Generate: three `research/experiments/2026-08-16-seven-token-pilot/derived/*.csv` files
- Create: `research/experiments/2026-08-16-seven-token-pilot/REPORT.md`
- Create: `docs/RESEARCH-LEDGER.md`
- Create: `docs/RESEARCH-GRAPH.md`
- Modify: `README.md`
- Modify: `tests/test_experiment.py`

**Interfaces:**
- Consumes: the analyzer from Task 3 and immutable scratch evidence listed below.
- Produces: the first complete public experiment bundle and durable research memory.

- [ ] **Step 1: Add an integration test that freezes the seven-line summary**

```python
def test_committed_pilot_summary_matches_observed_results():
    manifest = Path("research/experiments/2026-08-16-seven-token-pilot/manifest.json")
    tables = build_analysis(load_and_validate_manifest(manifest))
    actual = {
        row["symbol"]: (
            round(row["price_return_24h_pct"], 2),
            round(row["holdings_change_24h_pct"], 2),
            round(row["price_return_all_pct"], 2),
            round(row["holdings_change_all_pct"], 2),
        )
        for row in tables.token_summary
    }
    assert actual == {
        "CDXR": (0.04, 45.55, 0.72, 52.50),
        "AI-HEDGE-FUND": (-27.01, 0.70, -20.14, 0.14),
        "CHEAT.SH": (39.80, 1.28, 171.94, 1.14),
        "MONGO": (-20.48, 0.20, -64.11, 1.31),
        "PRISMA": (168.92, 0.86, 264.03, -0.15),
        "TOAD": (-6.98, 5.35, -46.95, 32.58),
        "CATE": (27.23, 3.71, 9.48, 11.28),
}
```

- [ ] **Step 2: Run the integration test and confirm it fails because the bundle is absent**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py::test_committed_pilot_summary_matches_observed_results -q
```

Expected: fail because `research/experiments/2026-08-16-seven-token-pilot/manifest.json` does not exist. A different failure must be corrected before proceeding.

- [ ] **Step 3: Copy the canonical raw evidence byte-for-byte**

Create the bundle directories, then copy only these files from `results/`:

```text
candidates-20260816T094029Z.csv
flows-ethereum-0x40aaf75454.json
flows-base-0xadf4d5b9d7.json
flows-base-0xadfd54cb29.json
flows-base-0xadf9afd4fa.json
flows-base-0xadf0d31463.json
flows-solana-A13oRB9FFaiU.json
flows-solana-Ai66LHZG9MCz.json
```

Do not copy `.env`, cache files, the byte-identical earlier candidates CSV, or `flows-solana-Ai66LHZG9MCz.pre-pilot.json`.

- [ ] **Step 4: Write the manifest with exact checksums and provenance**

Use these immutable checksums:

```text
candidates-20260816T094029Z.csv  4ed384e23a9156017e358c524ae8c837f40c251ec993d819e88fe801bb8fba43
flows-ethereum-0x40aaf75454.json  b13039c4a9afd6f66ab621b9c0164892f8e625847e75c6bf71f68e2d358f4a3f
flows-base-0xadf4d5b9d7.json  680fe1da9c1a317a975ad4a5b8ba347b0d391a7a14abd9740eae620bcd4c2785
flows-base-0xadfd54cb29.json  3a50c97dfce1c4ce85b84a8bd65950ef09a3c409481347e158d95c1dcf603581
flows-base-0xadf9afd4fa.json  4f2b87e2df8e7909f10cd584023bdc71e1aebf3ac7223ca25bd6902de71fe243
flows-base-0xadf0d31463.json  469c9195c9d262dee393ae68ab5b78bbc9ffcd649da405303fcaf90e62f8b463
flows-solana-A13oRB9FFaiU.json  7986503809d72c19b9cb5ab234ea416370edb3c3b9d35e26644bd6e43046472a
flows-solana-Ai66LHZG9MCz.json  acf08cb859a347ed48346c154c7a9ace17d6301d3789237f106848079013c8f5
```

All seven flow files have 96 source rows whose `date` starts run from `2026-08-12T10:00:00Z` through `2026-08-16T09:00:00Z`, with 95 complete rows. Their complete derived availability window is `2026-08-12T11:00:00Z` through `2026-08-16T09:00:00Z`. Record each file's retrieval time from the design specification evidence audit and record the original invocation using `--days 4 --limit 100`. Mark the exact original request boundaries as unavailable because the old CLI did not persist them.

The cohort must contain CDXR, AI-HEDGE-FUND, CHEAT.SH, MONGO, PRISMA, TOAD, and CATE with the addresses and chain assignments from the approved design context. Copy selection-time market cap, liquidity, age, netflow, and normalized price change from the candidate CSV by matching address.

- [ ] **Step 5: Run the integration test green and generate deterministic derived files**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment.py::test_committed_pilot_summary_matches_observed_results -q
```

Expected: pass. Resolve any manifest or calculation defect rather than changing expected values without direct raw-evidence inspection. Then generate:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json
```

Run `analyze --check`; expected: all three derived files verify.

- [ ] **Step 6: Write the reviewed report, ledger, graph, and README section**

`REPORT.md` must state the seven-line summary, event-weighted timing analysis, CDXR's immature 24-hour label, endpoint limitations, selection bias, no-trading-advice disclaimer, excluded duplicate/pre-pilot artifacts, and Nansen attribution.

`docs/RESEARCH-LEDGER.md` starts with an append-only format rule and one dated entry linking the bundle/report. `docs/RESEARCH-GRAPH.md` contains a Mermaid `flowchart LR` with stable IDs for the hypothesis, experiment, five observed behavior nodes, limitations, and the fixed-window CDXR next test. Each observation node links to `REPORT.md` outside the Mermaid block.

Add a README section with:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
```

and explain that `results/` is scratch space while `research/experiments/` is committed evidence.

- [ ] **Step 7: Verify attribution, secrets, determinism, and tests**

```bash
rg -n "Powered by Nansen API|redistribution-guide" README.md docs research/experiments/2026-08-16-seven-token-pilot/REPORT.md
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
.venv/bin/python -m pytest -q
git diff --check
git status --short
```

Verify explicitly that `.env`, `data/cache/`, `results/`, and `.request.json` scratch sidecars are absent from `git status`.

- [ ] **Step 8: Commit Task 5**

```bash
git add README.md docs/RESEARCH-LEDGER.md docs/RESEARCH-GRAPH.md tests/test_experiment.py research/experiments/2026-08-16-seven-token-pilot
git commit -m "Archive seven-token Nansen pilot"
```

---

### Task 6: Final verification and GitHub publication

**Files:**
- Verify: all committed files on `codex/research-memory`
- Publish: draft pull request targeting `main`

**Interfaces:**
- Consumes: all prior task commits.
- Produces: a pushed feature branch and draft PR with reproducibility evidence.

- [ ] **Step 1: Run fresh end-to-end verification**

```bash
.venv/bin/python -m pytest -q
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
git diff --check main...HEAD
git status --short --branch
```

Expected: all tests pass, check mode verifies three derived CSVs, diff check is silent, and the branch is clean.

- [ ] **Step 2: Audit tracked paths and scan for credential-shaped content**

```bash
git diff --name-only main...HEAD
git ls-files .env data/cache results .venv
rg -n --hidden --glob '!*.md' --glob '!manifest.json' 'NANSEN_API_KEY=.|apiKey["'"']?\s*[:=]\s*["'"'][^"'"']+' research src tests
```

Expected: tracked paths are limited to intended source, tests, docs, and the experiment bundle; the forbidden-path command prints nothing; the credential scan prints nothing.

- [ ] **Step 3: Review the complete diff against the approved design**

Confirm that each design requirement maps to a committed file or test, that the report does not overstate predictive evidence, and that raw checksums still match the manifest.

- [ ] **Step 4: Publish through the GitHub workflow**

Use the `github:yeet` skill to push `codex/research-memory` and open a draft PR against `main`. The PR body must include:

- purpose and bundle layout;
- the seven-token pilot headline conclusions;
- Nansen attribution and redistribution link;
- full test and `analyze --check` evidence;
- explicit note that CDXR's decisive 24-hour label is pending.

- [ ] **Step 5: Report the branch, commit range, draft PR URL, and remaining timed follow-up**

Do not claim the CDXR forward test is complete. State that the fixed-window follow-up becomes collectible after `2026-08-16T22:00:00Z`.
