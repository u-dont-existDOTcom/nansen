import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
from math import isfinite
from pathlib import Path
from typing import Any

from .signals import SUPPORTED_FEATURE_SET, build_signal_features


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


@dataclass(frozen=True)
class SignalBundle:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    source_bundle: Bundle


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


def sha256_file(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def _experiment_context(manifest: Any) -> str:
    if isinstance(manifest, dict):
        experiment_id = manifest.get("experiment_id")
        if experiment_id is not None and str(experiment_id):
            return f"experiment_id={experiment_id}"
    return "experiment_id=unknown"


def _parse_aware_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ExperimentError(f"flow row is missing a string {field}")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentError(f"invalid flow timestamp for {field}: {value}") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ExperimentError(f"flow timestamp for {field} must be timezone-aware: {value}")
    return timestamp.astimezone(timezone.utc)


def _require_nonnegative_int(record: dict[str, Any], field: str, *, evidence_id: str) -> int:
    value = record.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ExperimentError(
            f"evidence {evidence_id} {field} must be a non-negative integer"
        )
    return value


def _validate_declared_timestamp(
    value: Any,
    *,
    field: str,
    evidence_id: str,
    allow_none: bool = False,
) -> datetime | None:
    if value is None and allow_none:
        return None
    try:
        return _parse_aware_timestamp(value, field=field)
    except ExperimentError as exc:
        raise ExperimentError(f"evidence {evidence_id} has invalid {field}: {exc}") from exc


def _validate_flow_evidence(record: dict[str, Any], path: Path, *, evidence_id: str) -> None:
    try:
        body = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read flow evidence {evidence_id}: {exc}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ExperimentError(f"flow response data must be a list (evidence_id={evidence_id})")

    raw_rows = body["data"]
    starts = []
    complete_count = 0
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ExperimentError(f"flow row must be an object (evidence_id={evidence_id})")
        starts.append(_parse_aware_timestamp(raw.get("date"), field="date"))
        bucket_end = _parse_aware_timestamp(raw.get("bucket_end"), field="bucket_end")
        if bucket_end <= starts[-1]:
            raise ExperimentError(
                f"flow bucket_end must be after bucket start (evidence_id={evidence_id})"
            )
        is_complete = raw.get("is_complete", True)
        if not isinstance(is_complete, bool):
            raise ExperimentError(
                f"flow row is_complete must be boolean (evidence_id={evidence_id})"
            )
        complete_count += int(is_complete)

    declared_row_count = _require_nonnegative_int(record, "row_count", evidence_id=evidence_id)
    declared_complete_count = _require_nonnegative_int(
        record, "complete_count", evidence_id=evidence_id
    )
    if declared_row_count != len(raw_rows):
        raise ExperimentError(
            f"evidence {evidence_id} row_count mismatch: "
            f"declared {declared_row_count}, raw {len(raw_rows)}"
        )
    if declared_complete_count != complete_count:
        raise ExperimentError(
            f"evidence {evidence_id} complete_count mismatch: "
            f"declared {declared_complete_count}, raw {complete_count}"
        )

    expected_from = min(starts) if starts else None
    expected_to = max(starts) if starts else None
    declared_from = _validate_declared_timestamp(
        record.get("observed_from"),
        field="observed_from",
        evidence_id=evidence_id,
        allow_none=True,
    )
    declared_to = _validate_declared_timestamp(
        record.get("observed_to"),
        field="observed_to",
        evidence_id=evidence_id,
        allow_none=True,
    )
    if declared_from != expected_from:
        raise ExperimentError(
            f"evidence {evidence_id} observed_from mismatch: "
            f"declared {record.get('observed_from')}, raw {expected_from}"
        )
    if declared_to != expected_to:
        raise ExperimentError(
            f"evidence {evidence_id} observed_to mismatch: "
            f"declared {record.get('observed_to')}, raw {expected_to}"
        )


def _validate_candidate_evidence(record: dict[str, Any], path: Path, *, evidence_id: str) -> None:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
    except (OSError, csv.Error, UnicodeError) as exc:
        raise ExperimentError(f"cannot read candidate evidence {evidence_id}: {exc}") from exc
    required_columns = {"chain", "token_address"}
    if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
        raise ExperimentError(
            f"candidate evidence {evidence_id} must contain chain and token_address columns"
        )
    declared_row_count = _require_nonnegative_int(record, "row_count", evidence_id=evidence_id)
    declared_complete_count = _require_nonnegative_int(
        record, "complete_count", evidence_id=evidence_id
    )
    if declared_row_count != len(rows):
        raise ExperimentError(
            f"evidence {evidence_id} row_count mismatch: "
            f"declared {declared_row_count}, raw {len(rows)}"
        )
    if declared_complete_count != len(rows):
        raise ExperimentError(
            f"evidence {evidence_id} complete_count mismatch: "
            f"declared {declared_complete_count}, raw {len(rows)}"
        )
    if record.get("observed_from") is not None or record.get("observed_to") is not None:
        raise ExperimentError(
            f"candidate evidence {evidence_id} observed bounds must be null"
        )


def _normalized_token_identity(chain: Any, address: Any) -> tuple[str, str]:
    normalized_chain = str(chain)
    normalized_address = str(address)
    if normalized_address.lower().startswith("0x"):
        normalized_address = normalized_address.lower()
    return normalized_chain, normalized_address


def load_and_validate_manifest(manifest_path: str | Path) -> Bundle:
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read manifest {path}: {exc}") from exc

    context = _experiment_context(manifest)
    if not isinstance(manifest, dict):
        raise ExperimentError(f"manifest must be an object ({context})")

    required = {
        "schema_version", "experiment_id", "title", "status", "created_at",
        "hypothesis", "horizons_hours", "source", "cohort", "evidence",
        "exclusions",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ExperimentError(f"manifest missing keys: {', '.join(missing)} ({context})")
    if manifest["schema_version"] != 1:
        raise ExperimentError(f"unsupported schema version: {manifest['schema_version']} ({context})")
    if manifest["status"] not in {"discovery", "holdout"}:
        raise ExperimentError(f"invalid experiment status: {manifest['status']} ({context})")

    horizons = manifest["horizons_hours"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in horizons)
        or len(horizons) != len(set(horizons))
    ):
        raise ExperimentError(f"horizons_hours must contain unique positive integers ({context})")

    root = path.parent.resolve()
    evidence = []
    seen_evidence_ids = set()
    evidence_records = manifest["evidence"]
    if not isinstance(evidence_records, list):
        raise ExperimentError(f"evidence must be a list ({context})")
    for record in evidence_records:
        if not isinstance(record, dict):
            raise ExperimentError(f"evidence record must be an object (evidence_id=unknown, {context})")
        evidence_id = str(record.get("id", ""))
        if not evidence_id or evidence_id in seen_evidence_ids:
            evidence_label = evidence_id or "unknown"
            raise ExperimentError(
                f"duplicate or empty evidence id: {evidence_label} ({context})"
            )
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
        kind = str(record.get("kind", ""))
        endpoint = str(record.get("endpoint", ""))
        expected_endpoints = {
            "tgm_flows": "tgm/flows",
            "token_screener_candidates": "token-screener",
        }
        if kind not in expected_endpoints or endpoint != expected_endpoints[kind]:
            raise ExperimentError(
                f"invalid evidence kind/endpoint combination for {evidence_id}: {kind}/{endpoint}"
            )
        request = record.get("request")
        if not isinstance(request, dict):
            raise ExperimentError(f"evidence {evidence_id} request must be an object")
        _validate_declared_timestamp(
            record.get("retrieved_at"),
            field="retrieved_at",
            evidence_id=evidence_id,
        )
        if kind == "tgm_flows":
            _validate_flow_evidence(record, evidence_path, evidence_id=evidence_id)
        else:
            _validate_candidate_evidence(record, evidence_path, evidence_id=evidence_id)
        evidence.append(EvidenceFile(
            id=evidence_id,
            kind=kind,
            path=evidence_path,
            sha256=expected,
            metadata=dict(record),
        ))

    evidence_by_id = {item.id: item for item in evidence}
    seen_tokens = set()
    cohort = manifest["cohort"]
    if not isinstance(cohort, list):
        raise ExperimentError(f"cohort must be a list ({context})")
    for member in cohort:
        if not isinstance(member, dict):
            raise ExperimentError(f"cohort member must be an object ({context})")
        identity = _normalized_token_identity(member.get("chain", ""), member.get("address", ""))
        if not all(identity) or identity in seen_tokens:
            raise ExperimentError(
                f"duplicate cohort token: {identity[0]}:{identity[1]} ({context})"
            )
        seen_tokens.add(identity)
        flow_id = str(member.get("flow_evidence_id", ""))
        if flow_id not in evidence_by_id:
            raise ExperimentError(
                f"cohort token {identity[0]}:{identity[1]} has unknown flow evidence {flow_id} ({context})"
            )
        flow_evidence = evidence_by_id[flow_id]
        if flow_evidence.kind != "tgm_flows":
            raise ExperimentError(
                f"cohort flow evidence {flow_id} is not tgm_flows ({context})"
            )
        request = flow_evidence.metadata["request"]
        request_identity = request.get("payload") if isinstance(request.get("payload"), dict) else request
        request_chain = request_identity.get("chain")
        request_address = request_identity.get("token_address")
        if request_chain is not None and str(request_chain) != identity[0]:
            raise ExperimentError(
                f"flow request identity mismatch for {flow_id}: "
                f"chain {request_chain} != {identity[0]} ({context})"
            )
        if request_address is not None:
            requested = _normalized_token_identity(identity[0], request_address)[1]
            if requested != identity[1]:
                raise ExperimentError(
                    f"flow request identity mismatch for {flow_id}: "
                    f"token_address {request_address} != {member.get('address')} ({context})"
                )
        selection = member.get("selection")
        if not isinstance(selection, dict):
            raise ExperimentError(
                f"cohort token {identity[0]}:{identity[1]} selection must be an object ({context})"
            )
        candidate_id = str(selection.get("candidate_evidence_id", ""))
        if candidate_id not in evidence_by_id:
            raise ExperimentError(
                f"cohort token {identity[0]}:{identity[1]} has unknown candidate evidence "
                f"{candidate_id or 'unknown'} ({context})"
            )
        if evidence_by_id[candidate_id].kind != "token_screener_candidates":
            raise ExperimentError(
                f"candidate evidence {candidate_id} is not token_screener_candidates ({context})"
            )

    return Bundle(
        root=root,
        manifest_path=path,
        manifest=manifest,
        evidence=tuple(evidence),
    )


def load_signal_manifest(manifest_path: str | Path) -> SignalBundle:
    requested_path = Path(os.path.abspath(os.fspath(manifest_path)))
    experiments_root = requested_path.parent.parent.resolve()
    path = requested_path.resolve()
    if path.name != "manifest.json" or path.parent.parent != experiments_root:
        raise ExperimentError(
            "companion manifest must be a direct bundle under trusted experiments root "
            f"{experiments_root}: requested {requested_path}, resolved {path}"
        )
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read manifest {path}: {exc}") from exc
    context = _experiment_context(manifest)
    if not isinstance(manifest, dict):
        raise ExperimentError(f"manifest must be an object ({context})")
    required = {
        "schema_version", "experiment_id", "title", "status", "created_at",
        "hypothesis", "feature_set_version", "horizons_hours", "source_manifest",
        "source_manifest_sha256", "point_in_time_guarantee", "availability_policy",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ExperimentError(f"manifest missing keys: {', '.join(missing)} ({context})")
    unknown = sorted(set(manifest) - required)
    if unknown:
        raise ExperimentError(f"manifest has unknown keys: {', '.join(unknown)} ({context})")
    if manifest["schema_version"] != 2:
        raise ExperimentError(
            f"unsupported signal schema version: {manifest['schema_version']} ({context})"
        )
    status = manifest["status"]
    if status not in {"discovery", "holdout"}:
        raise ExperimentError(f"invalid experiment status: {status} ({context})")
    guarantee = manifest["point_in_time_guarantee"]
    if guarantee not in {"provider_pit", "live_snapshot", "unknown"}:
        raise ExperimentError(
            f"invalid point_in_time_guarantee: {guarantee} ({context})"
        )
    if status == "holdout" and guarantee == "unknown":
        raise ExperimentError(
            f"point-in-time guarantee unknown is discovery-only ({context})"
        )
    if manifest["availability_policy"] != "bucket_end":
        raise ExperimentError(
            f"availability_policy must be bucket_end ({context})"
        )
    if manifest["feature_set_version"] != SUPPORTED_FEATURE_SET:
        raise ExperimentError(
            f"unsupported feature set: {manifest['feature_set_version']} ({context})"
        )
    horizons = manifest["horizons_hours"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in horizons
        )
        or len(horizons) != len(set(horizons))
    ):
        raise ExperimentError(
            f"horizons_hours must contain unique positive integers ({context})"
        )
    root = path.parent.resolve()
    source_path = (root / str(manifest["source_manifest"])).resolve()
    if (
        source_path.name != "manifest.json"
        or source_path.parent == root
        or source_path.parent.parent != experiments_root
    ):
        raise ExperimentError(
            f"source manifest must be a sibling bundle under {experiments_root}: {source_path}"
        )
    expected_hash = str(manifest["source_manifest_sha256"])
    actual_hash = sha256_file(source_path)
    if actual_hash != expected_hash:
        raise ExperimentError(
            f"source manifest checksum mismatch: expected {expected_hash}, got {actual_hash}"
        )
    source_bundle = load_and_validate_manifest(source_path)
    source_horizons = set(source_bundle.manifest["horizons_hours"])
    if not set(horizons).issubset(source_horizons):
        raise ExperimentError(
            f"horizons_hours must be a subset of source horizons ({context})"
        )
    return SignalBundle(
        root=root,
        manifest_path=path,
        manifest=manifest,
        source_bundle=source_bundle,
    )


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
            raise ExperimentError(
                f"flow bucket_end must be after bucket start: {raw.get('date')} -> {raw.get('bucket_end')}"
            )
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
        if (
            not isfinite(price_usd)
            or price_usd <= 0
            or not isfinite(token_amount)
            or token_amount < 0
        ):
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
            history = [
                by_time.get(timestamp - timedelta(hours=offset))
                for offset in range(1, horizon + 1)
            ]
            old = history[-1]
            available = all(history)
            feature[f"trailing_price_return_{horizon}h_pct"] = (
                None if not available else 100.0 * (row["price_usd"] / old["price_usd"] - 1.0)
            )
            feature[f"trailing_holdings_change_{horizon}h_pct"] = (
                None if not available or old["token_amount"] == 0
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
    history_24h = [
        by_time.get(last_time - timedelta(hours=offset))
        for offset in range(1, 25)
    ]
    old_24h = history_24h[-1] if all(row is not None for row in history_24h) else None

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


def build_signal_analysis(bundle: SignalBundle) -> tuple[dict[str, Any], ...]:
    horizons = tuple(sorted(int(value) for value in bundle.manifest["horizons_hours"]))
    source_features = build_analysis(bundle.source_bundle).hourly_features
    by_token: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for feature in source_features:
        key = (feature["chain"], feature["symbol"], feature["address"])
        source = dict(feature)
        source["token_address"] = feature["address"]
        by_token.setdefault(key, []).append(source)

    signal_rows = []
    for features in by_token.values():
        signal_rows.extend(build_signal_features(
            tuple(features),
            horizons=horizons,
            source_experiment_id=bundle.source_bundle.experiment_id,
            feature_set_version=bundle.manifest["feature_set_version"],
        ))

    fields = signal_fieldnames(horizons)
    normalized = ({field: row.get(field) for field in fields} for row in signal_rows)
    return tuple(sorted(
        normalized,
        key=lambda row: (row["chain"], row["symbol"], row["timestamp"]),
    ))


def csv_text(rows: tuple[dict[str, Any], ...], fieldnames: tuple[str, ...]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
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


def signal_fieldnames(horizons: tuple[int, ...]) -> tuple[str, ...]:
    return (
        "source_experiment_id",
        "feature_set_version",
        "chain",
        "symbol",
        "token_address",
        "timestamp",
    ) + tuple(
        name
        for horizon in horizons
        for name in (
            f"holdings_change_{horizon}h_pct",
            f"price_return_{horizon}h_pct",
            f"positive_holdings_delta_hours_{horizon}h",
            f"negative_holdings_delta_hours_{horizon}h",
            f"accumulation_persistence_{horizon}h",
            f"distribution_persistence_{horizon}h",
            f"holdings_velocity_{horizon}h_pct_per_hour",
            f"holdings_acceleration_{horizon}h_pct_per_hour",
            f"holder_count_change_{horizon}h",
            f"accumulation_retention_{horizon}h",
            f"flow_price_divergence_{horizon}h_pct",
            f"market_phase_{horizon}h",
        )
    )


def render_analysis_csvs(bundle: Bundle, tables: AnalysisTables) -> dict[str, str]:
    horizons = tuple(sorted(int(value) for value in bundle.manifest["horizons_hours"]))
    feature_fields, event_fields, summary_fields = analysis_fieldnames(horizons)
    return {
        "hourly-features.csv": csv_text(tables.hourly_features, feature_fields),
        "event-windows.csv": csv_text(tables.event_windows, event_fields),
        "token-summary.csv": csv_text(tables.token_summary, summary_fields),
    }


def _peek_manifest_schema_version(manifest_path: str | Path) -> Any:
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read manifest {path}: {exc}") from exc
    return manifest.get("schema_version") if isinstance(manifest, dict) else None


def analyze_manifest(manifest_path: str | Path, *, check: bool = False) -> tuple[Path, ...]:
    if _peek_manifest_schema_version(manifest_path) == 2:
        signal_bundle = load_signal_manifest(manifest_path)
        horizons = tuple(sorted(
            int(value) for value in signal_bundle.manifest["horizons_hours"]
        ))
        rendered = csv_text(
            build_signal_analysis(signal_bundle),
            signal_fieldnames(horizons),
        )
        path = signal_bundle.root / "derived" / "signal-features.csv"
        if check:
            if not path.is_file() or path.read_bytes() != rendered.encode("utf-8"):
                raise ExperimentError(f"derived output differs: {path}")
            return (path,)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8", newline="")
        temporary.replace(path)
        return (path,)

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
