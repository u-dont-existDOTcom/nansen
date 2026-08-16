import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
        evidence.append(EvidenceFile(
            id=evidence_id,
            kind=str(record.get("kind", "")),
            path=evidence_path,
            sha256=expected,
            metadata=dict(record),
        ))

    evidence_ids = {item.id for item in evidence}
    seen_tokens = set()
    cohort = manifest["cohort"]
    if not isinstance(cohort, list):
        raise ExperimentError(f"cohort must be a list ({context})")
    for member in cohort:
        if not isinstance(member, dict):
            raise ExperimentError(f"cohort member must be an object ({context})")
        identity = (str(member.get("chain", "")), str(member.get("address", "")).lower())
        if not all(identity) or identity in seen_tokens:
            raise ExperimentError(
                f"duplicate cohort token: {identity[0]}:{identity[1]} ({context})"
            )
        seen_tokens.add(identity)
        flow_id = str(member.get("flow_evidence_id", ""))
        if flow_id not in evidence_ids:
            raise ExperimentError(
                f"cohort token {identity[0]}:{identity[1]} has unknown flow evidence {flow_id} ({context})"
            )

    return Bundle(
        root=root,
        manifest_path=path,
        manifest=manifest,
        evidence=tuple(evidence),
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
        value = raw.get("date")
        if not isinstance(value, str):
            raise ExperimentError("flow row is missing a string date")
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError as exc:
            raise ExperimentError(f"invalid flow timestamp: {value}") from exc
        if timestamp in seen:
            raise ExperimentError(f"duplicate timestamp: {value}")
        seen.add(timestamp)
        if not raw.get("is_complete", True):
            incomplete_count += 1
            continue
        if raw.get("price_usd") in (None, 0) or raw.get("token_amount") is None:
            invalid_metric_count += 1
            continue
        row = dict(raw)
        row["_timestamp"] = timestamp
        row["price_usd"] = float(row["price_usd"])
        row["token_amount"] = float(row["token_amount"])
        rows.append(row)
    rows.sort(key=lambda row: row["_timestamp"])
    gaps = tuple(
        (left["date"], right["date"])
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
            "timestamp": row["date"],
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
