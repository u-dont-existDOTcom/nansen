from __future__ import annotations

import copy
import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .experiment import (
    SignalBundle,
    build_analysis,
    build_signal_analysis,
    load_signal_manifest,
    sha256_file,
    signal_fieldnames,
)


class EvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Predicate:
    feature: str
    operator: str
    value: Any
    lag_hours: int


@dataclass(frozen=True)
class TheorySpec:
    id: str
    role: str
    objective: str
    holding_period_hours: int
    predicates: tuple[Predicate, ...]


@dataclass(frozen=True)
class TimeBlock:
    id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CostScenario:
    id: str
    per_side_bps: float


@dataclass(frozen=True)
class ComparisonSpec:
    id: str
    positive_arm: str
    reference_arm: str


@dataclass(frozen=True)
class BlockedTheorySpec:
    id: str
    reason: str
    missing_roles: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationBundle:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    source_bundle: SignalBundle
    theories: tuple[TheorySpec, ...]
    blocks: tuple[TimeBlock, ...]
    costs: tuple[CostScenario, ...]
    comparisons: tuple[ComparisonSpec, ...]
    blocked_theories: tuple[BlockedTheorySpec, ...]

    @property
    def experiment_id(self) -> str:
        return str(self.manifest["experiment_id"])


@dataclass(frozen=True)
class EvaluationTables:
    events: tuple[dict[str, Any], ...]
    summaries: tuple[dict[str, Any], ...]
    comparisons: tuple[dict[str, Any], ...]
    paper_selection: dict[str, Any]


_TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment_id",
    "title",
    "status",
    "created_at",
    "hypothesis",
    "source_signal_manifest",
    "source_signal_manifest_sha256",
    "evaluation_window",
    "time_blocks",
    "execution",
    "cost_scenarios",
    "theories",
    "comparisons",
    "blocked_theories",
    "paper_feasibility_gates",
    "prospective_advancement_gates",
}
_WINDOW_KEYS = {"from", "to"}
_BLOCK_KEYS = {"id", "from", "to"}
_EXECUTION_KEYS = {"proxy_version", "entry_lag_hours", "non_overlap"}
_COST_KEYS = {"id", "per_side_bps"}
_THEORY_KEYS = {"id", "role", "objective", "holding_period_hours", "all"}
_PREDICATE_KEYS = {"feature", "operator", "value", "lag_hours"}
_COMPARISON_KEYS = {"id", "positive_arm", "reference_arm"}
_BLOCKED_THEORY_KEYS = {"id", "reason", "missing_roles"}
_PAPER_GATE_KEYS = {
    "entry_min_events",
    "entry_min_tokens",
    "entry_min_positive_blocks",
    "veto_min_events",
    "veto_min_tokens",
}
_ADVANCEMENT_GATE_KEYS = {
    "min_calendar_weeks",
    "min_fills",
    "min_tokens",
    "min_fill_rate",
    "max_token_pnl_contribution",
}
_OPERATORS = {"eq", "in", "gt", "gte", "lt", "lte"}
_ROLES = {"entry", "veto", "reference", "comparison"}
_OBJECTIVES = {"positive_return", "avoided_loss"}
_ROLE_OBJECTIVES = {
    "entry": "positive_return",
    "veto": "avoided_loss",
    "reference": "positive_return",
    "comparison": "positive_return",
}
_IDENTITY_FIELDS = {
    "source_experiment_id",
    "feature_set_version",
    "chain",
    "symbol",
    "token_address",
    "timestamp",
}


def _context(manifest: Any) -> str:
    if isinstance(manifest, dict) and manifest.get("experiment_id"):
        return f" (experiment_id={manifest['experiment_id']})"
    return ""


def _keys(record: Any, expected: set[str], *, label: str, context: str) -> None:
    if not isinstance(record, dict):
        raise EvaluationError(f"{label} must be an object{context}")
    missing = sorted(expected - set(record))
    if missing:
        raise EvaluationError(f"{label} missing keys: {', '.join(missing)}{context}")
    unknown = sorted(set(record) - expected)
    if unknown:
        raise EvaluationError(f"{label} has unknown keys: {', '.join(unknown)}{context}")


def _nonempty_string(value: Any, *, field: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{field} must be a non-empty string{context}")
    return value


def _timestamp(value: Any, *, field: str, context: str) -> datetime:
    if not isinstance(value, str):
        raise EvaluationError(f"{field} must be a timezone-aware ISO timestamp{context}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError(f"{field} must be a valid timestamp{context}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError(f"{field} must be timezone-aware{context}")
    return parsed.astimezone(timezone.utc)


def _strict_nonnegative_int(value: Any, *, field: str, context: str, positive: bool = False) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or (value <= 0 if positive else value < 0)
    ):
        qualifier = "positive" if positive else "non-negative"
        raise EvaluationError(f"{field} must be a {qualifier} integer{context}")
    return value


def _finite_number(value: Any, *, field: str, context: str, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{field} must be finite non-negative numeric{context}")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise EvaluationError(f"{field} must be finite non-negative numeric{context}") from exc
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise EvaluationError(f"{field} must be finite non-negative numeric{context}")
    return number


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _finite_nonboolean_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _validate_predicate(record: Any, allowed_features: set[str], *, context: str) -> Predicate:
    _keys(record, _PREDICATE_KEYS, label="predicate", context=context)
    feature = _nonempty_string(record["feature"], field="predicate feature", context=context)
    if feature not in allowed_features:
        raise EvaluationError(f"predicate feature is not an allowed trailing signal field: {feature}{context}")
    operator = record["operator"]
    if operator not in _OPERATORS:
        raise EvaluationError(f"invalid predicate operator: {operator}{context}")
    value = record["value"]
    if operator in {"gt", "gte", "lt", "lte"} and (
        not _finite_nonboolean_number(value)
    ):
        raise EvaluationError(
            f"ordered predicate value must be a finite non-boolean numeric scalar{context}"
        )
    if operator == "in":
        values = value
        if not isinstance(values, list) or not values or any(not _scalar(item) for item in values):
            raise EvaluationError(f"predicate in value must be a non-empty unique scalar list{context}")
        for item in values:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if not _finite_nonboolean_number(item):
                    raise EvaluationError(f"predicate value must be finite{context}")
        try:
            unique = len(values) == len(set(values))
        except TypeError:
            unique = False
        if not unique:
            raise EvaluationError(f"predicate in value must be a non-empty unique scalar list{context}")
        value = tuple(values)
    elif not _scalar(value):
        raise EvaluationError(f"predicate value must be scalar{context}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not _finite_nonboolean_number(value):
            raise EvaluationError(f"predicate value must be finite{context}")
    lag_hours = _strict_nonnegative_int(record["lag_hours"], field="predicate lag_hours", context=context)
    return Predicate(feature=feature, operator=operator, value=value, lag_hours=lag_hours)


def _validate_gates(record: Any, expected: set[str], *, label: str, context: str) -> None:
    _keys(record, expected, label=label, context=context)
    for field in expected:
        value = record[field]
        if field in {"min_fill_rate", "max_token_pnl_contribution"}:
            number = _finite_number(value, field=f"{label} {field}", context=context)
            if not 0 <= number <= 1:
                raise EvaluationError(f"{label} {field} must be between 0 and 1{context}")
        else:
            _strict_nonnegative_int(value, field=f"{label} {field}", context=context)


def load_evaluation_manifest(manifest_path: str | Path) -> EvaluationBundle:
    requested_path = Path(os.path.abspath(os.fspath(manifest_path)))
    requested_experiments_root = requested_path.parent.parent.resolve()
    path = requested_path.resolve()
    if path.parent.parent != requested_experiments_root:
        raise EvaluationError(
            "evaluation manifest must remain under the requested trusted experiments root "
            f"{requested_experiments_root}: requested {requested_path}, resolved {path}"
        )
    context = ""
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read evaluation manifest {path}: {exc}") from exc
    context = _context(manifest)
    _keys(manifest, _TOP_LEVEL_KEYS, label="evaluation manifest", context=context)
    if manifest["schema_version"] != 3:
        raise EvaluationError(f"unsupported evaluation schema: {manifest['schema_version']}{context}")
    for field in ("experiment_id", "title", "hypothesis", "source_signal_manifest", "source_signal_manifest_sha256"):
        _nonempty_string(manifest[field], field=field, context=context)
    _timestamp(manifest["created_at"], field="created_at", context=context)
    if manifest["status"] not in {"discovery", "holdout"}:
        raise EvaluationError(f"invalid evaluation status: {manifest['status']}{context}")

    evaluation_window = manifest["evaluation_window"]
    _keys(evaluation_window, _WINDOW_KEYS, label="evaluation_window", context=context)
    evaluation_start = _timestamp(evaluation_window["from"], field="evaluation_window.from", context=context)
    evaluation_end = _timestamp(evaluation_window["to"], field="evaluation_window.to", context=context)
    if evaluation_end <= evaluation_start:
        raise EvaluationError(f"evaluation window must end after it starts{context}")

    blocks_raw = manifest["time_blocks"]
    if not isinstance(blocks_raw, list) or not blocks_raw:
        raise EvaluationError(f"time_blocks must be a non-empty list{context}")
    blocks: list[TimeBlock] = []
    block_ids: set[str] = set()
    for record in blocks_raw:
        _keys(record, _BLOCK_KEYS, label="time block", context=context)
        block_id = _nonempty_string(record["id"], field="time block id", context=context)
        if block_id in block_ids:
            raise EvaluationError(f"duplicate time block id: {block_id}{context}")
        block_ids.add(block_id)
        start = _timestamp(record["from"], field=f"time block {block_id}.from", context=context)
        end = _timestamp(record["to"], field=f"time block {block_id}.to", context=context)
        if end <= start:
            raise EvaluationError(f"time block {block_id} must end after it starts{context}")
        if start < evaluation_start or end > evaluation_end:
            raise EvaluationError(f"time block {block_id} must be within evaluation window{context}")
        if blocks and start < blocks[-1].end:
            raise EvaluationError(f"time blocks must be ordered and non-overlapping{context}")
        if blocks and start < blocks[-1].start:
            raise EvaluationError(f"time blocks must be ordered and non-overlapping{context}")
        blocks.append(TimeBlock(block_id, start, end))
    if (
        blocks[0].start != evaluation_start
        or blocks[-1].end != evaluation_end
        or any(left.end != right.start for left, right in zip(blocks, blocks[1:]))
    ):
        raise EvaluationError(
            f"time blocks must form a contiguous full partition of evaluation window{context}"
        )

    execution = manifest["execution"]
    _keys(execution, _EXECUTION_KEYS, label="execution", context=context)
    _nonempty_string(execution["proxy_version"], field="execution proxy_version", context=context)
    if execution["proxy_version"] != "next-hour-fixed-exit-v1":
        raise EvaluationError(f"unsupported execution proxy version: {execution['proxy_version']}{context}")
    _strict_nonnegative_int(execution["entry_lag_hours"], field="execution entry_lag_hours", context=context)
    if execution["entry_lag_hours"] != 1:
        raise EvaluationError(f"execution entry_lag_hours must be 1{context}")
    if execution["non_overlap"] != "one_open_episode_per_token":
        raise EvaluationError(f"unsupported execution non_overlap policy: {execution['non_overlap']}{context}")

    costs_raw = manifest["cost_scenarios"]
    if not isinstance(costs_raw, list):
        raise EvaluationError(f"cost_scenarios must be a list{context}")
    costs: list[CostScenario] = []
    seen_costs: set[str] = set()
    for record in costs_raw:
        _keys(record, _COST_KEYS, label="cost scenario", context=context)
        cost_id = _nonempty_string(record["id"], field="cost scenario id", context=context)
        if cost_id in seen_costs:
            raise EvaluationError(f"duplicate cost scenario: {cost_id}{context}")
        seen_costs.add(cost_id)
        costs.append(CostScenario(cost_id, _finite_number(record["per_side_bps"], field="cost per_side_bps", context=context, nonnegative=True)))
    if seen_costs != {"base", "stress"}:
        raise EvaluationError(f"cost scenario IDs must be exactly base and stress{context}")
    cost_values = {item.id: item.per_side_bps for item in costs}
    for cost_id, expected in {"base": 100.0, "stress": 250.0}.items():
        if cost_values[cost_id] != expected:
            raise EvaluationError(
                f"cost scenario {cost_id} must be exactly {expected:g} per_side_bps{context}"
            )

    # The source is checked before the existing loader is called so an altered or
    # redirected source cannot be silently accepted by lineage validation.
    experiments_root = path.parent.parent.resolve()
    source_requested = path.parent / manifest["source_signal_manifest"]
    source_path = source_requested.resolve()
    if source_requested.is_symlink() or source_requested.parent.is_symlink() or source_path.is_symlink():
        raise EvaluationError(f"source manifest cannot be a symlink or redirected file: {source_requested}")
    if (
        source_path.name != "manifest.json"
        or source_path.parent == path.parent
        or source_path.parent.parent != experiments_root
        or not source_path.is_file()
    ):
        raise EvaluationError(f"source manifest must be a sibling bundle under trusted experiments root {experiments_root}: {source_path}")
    expected_hash = manifest["source_signal_manifest_sha256"]
    actual_hash = sha256_file(source_path)
    if actual_hash != expected_hash:
        raise EvaluationError(f"source signal manifest checksum mismatch: expected {expected_hash}, got {actual_hash}{context}")
    try:
        source_bundle = load_signal_manifest(source_path)
    except Exception as exc:
        # Keep the public loader error type stable while preserving the existing
        # lineage loader's useful diagnostic.
        raise EvaluationError(str(exc)) from exc
    if manifest["status"] == "holdout" and source_bundle.manifest["point_in_time_guarantee"] not in {"provider_pit", "live_snapshot"}:
        raise EvaluationError(f"unknown point-in-time source cannot be holdout{context}")

    allowed_features = set(signal_fieldnames(tuple(source_bundle.manifest["horizons_hours"]))) - _IDENTITY_FIELDS
    theories_raw = manifest["theories"]
    if not isinstance(theories_raw, list) or not theories_raw:
        raise EvaluationError(f"theories must be a non-empty list{context}")
    theories: list[TheorySpec] = []
    theory_ids: set[str] = set()
    for record in theories_raw:
        _keys(record, _THEORY_KEYS, label="theory", context=context)
        theory_id = _nonempty_string(record["id"], field="theory id", context=context)
        if theory_id in theory_ids:
            raise EvaluationError(f"duplicate theory id: {theory_id}{context}")
        theory_ids.add(theory_id)
        role = record["role"]
        if role not in _ROLES:
            raise EvaluationError(f"invalid theory role: {role}{context}")
        objective = record["objective"]
        if objective not in _OBJECTIVES:
            raise EvaluationError(f"invalid theory objective: {objective}{context}")
        required_objective = _ROLE_OBJECTIVES[role]
        if objective != required_objective:
            raise EvaluationError(
                f"theory {theory_id} role {role} requires objective {required_objective}{context}"
            )
        holding = _strict_nonnegative_int(record["holding_period_hours"], field="theory holding_period_hours", context=context, positive=True)
        predicates_raw = record["all"]
        if not isinstance(predicates_raw, list) or not predicates_raw:
            raise EvaluationError(f"theory {theory_id} all must be a non-empty list{context}")
        predicates = tuple(_validate_predicate(item, allowed_features, context=context) for item in predicates_raw)
        theories.append(TheorySpec(theory_id, role, objective, holding, predicates))

    comparisons_raw = manifest["comparisons"]
    if not isinstance(comparisons_raw, list):
        raise EvaluationError(f"comparisons must be a list{context}")
    comparisons: list[ComparisonSpec] = []
    comparison_ids: set[str] = set()
    for record in comparisons_raw:
        _keys(record, _COMPARISON_KEYS, label="comparison", context=context)
        comparison_id = _nonempty_string(record["id"], field="comparison id", context=context)
        if comparison_id in comparison_ids:
            raise EvaluationError(f"duplicate comparison id: {comparison_id}{context}")
        comparison_ids.add(comparison_id)
        positive = _nonempty_string(record["positive_arm"], field="comparison positive_arm", context=context)
        reference = _nonempty_string(record["reference_arm"], field="comparison reference_arm", context=context)
        comparisons.append(ComparisonSpec(comparison_id, positive, reference))
    blocked_raw = manifest["blocked_theories"]
    if not isinstance(blocked_raw, list):
        raise EvaluationError(f"blocked_theories must be a list{context}")
    blocked: list[BlockedTheorySpec] = []
    blocked_ids: set[str] = set()
    for record in blocked_raw:
        _keys(record, _BLOCKED_THEORY_KEYS, label="blocked theory", context=context)
        blocked_id = _nonempty_string(record["id"], field="blocked theory id", context=context)
        if blocked_id in blocked_ids:
            raise EvaluationError(f"blocked theory IDs must be unique: {blocked_id}{context}")
        blocked_ids.add(blocked_id)
        reason = _nonempty_string(record["reason"], field="blocked theory reason", context=context)
        missing_roles = record["missing_roles"]
        if not isinstance(missing_roles, list) or not missing_roles:
            raise EvaluationError(f"blocked theory missing_roles must be a non-empty list{context}")
        if any(not isinstance(role, str) or not role.strip() for role in missing_roles):
            raise EvaluationError(f"blocked theory missing_roles must contain non-empty strings{context}")
        if len(missing_roles) != len(set(missing_roles)):
            raise EvaluationError(f"blocked theory missing_roles must be unique{context}")
        blocked.append(BlockedTheorySpec(blocked_id, reason, tuple(missing_roles)))
    overlapping_theory_ids = sorted(theory_ids & blocked_ids)
    if overlapping_theory_ids:
        raise EvaluationError(
            "blocked theory IDs must not also be evaluable: "
            f"{', '.join(overlapping_theory_ids)}{context}"
        )
    _validate_gates(manifest["paper_feasibility_gates"], _PAPER_GATE_KEYS, label="paper feasibility gate", context=context)
    _validate_gates(manifest["prospective_advancement_gates"], _ADVANCEMENT_GATE_KEYS, label="prospective advancement gate", context=context)

    return EvaluationBundle(
        root=path.parent.resolve(),
        manifest_path=path,
        manifest=manifest,
        source_bundle=source_bundle,
        theories=tuple(theories),
        blocks=tuple(blocks),
        costs=tuple(costs),
        comparisons=tuple(comparisons),
        blocked_theories=tuple(blocked),
    )


def _parse_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _normalized_identity(chain: Any, address: Any) -> tuple[str, str]:
    normalized_chain = str(chain)
    normalized_address = str(address)
    if normalized_address.lower().startswith("0x"):
        normalized_address = normalized_address.lower()
    return normalized_chain, normalized_address


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _signal_sort_key(row: dict[str, Any]) -> tuple[datetime, str, str, str]:
    timestamp = _parse_utc(row["timestamp"])
    chain, address = _normalized_identity(row["chain"], row["token_address"])
    return timestamp, chain, address, str(row.get("symbol", ""))


def _positive_finite_price(row: dict[str, Any]) -> float | None:
    value = row.get("price_usd")
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def entry_objective_pct(gross_return_pct: float, per_side_bps: float) -> float:
    ratio = 1 + gross_return_pct / 100
    cost = per_side_bps / 10_000
    return 100 * (ratio * (1 - cost) * (1 - cost) - 1)


def veto_objective_pct(gross_return_pct: float, per_side_bps: float) -> float:
    return -gross_return_pct - 2 * per_side_bps / 100


def _scalar_kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "none"
    return None


def _compatible_scalar_kinds(left: Any, right: Any) -> bool:
    left_kind = _scalar_kind(left)
    right_kind = _scalar_kind(right)
    return left_kind is not None and left_kind == right_kind


def _scalar_equals(left: Any, right: Any) -> bool:
    return _compatible_scalar_kinds(left, right) and bool(left == right)


def predicate_matches(
    predicate: Predicate,
    *,
    current: dict[str, Any],
    by_timestamp: dict[datetime, dict[str, Any]],
) -> bool:
    timestamp = _parse_utc(current["timestamp"])
    row = by_timestamp.get(timestamp - timedelta(hours=predicate.lag_hours))
    if row is None or row.get(predicate.feature) is None:
        return False
    actual = row[predicate.feature]
    if predicate.operator == "eq":
        return _scalar_equals(actual, predicate.value)
    if predicate.operator == "in":
        return any(_scalar_equals(actual, candidate) for candidate in predicate.value)
    if not (
        _finite_nonboolean_number(actual)
        and _finite_nonboolean_number(predicate.value)
    ):
        return False
    if not _compatible_scalar_kinds(actual, predicate.value):
        return False
    operations = {
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
    }
    try:
        return bool(operations[predicate.operator](actual, predicate.value))
    except (KeyError, TypeError):
        return False


def _event_row(
    theory: TheorySpec,
    current: dict[str, Any],
    block: TimeBlock,
    entry_at: datetime,
    exit_at: datetime,
    gross_return_pct: float,
    gross_objective_pct: float,
    costs: tuple[CostScenario, ...],
    *,
    evaluation_id: str,
) -> dict[str, Any]:
    row = {
        "evaluation_id": evaluation_id,
        "theory_id": theory.id,
        "theory_role": theory.role,
        "block_id": block.id,
        "chain": str(current["chain"]),
        "symbol": str(current["symbol"]),
        "token_address": str(current["token_address"]),
        "signal_timestamp": _timestamp_text(_parse_utc(current["timestamp"])),
        "entry_timestamp": _timestamp_text(entry_at),
        "exit_timestamp": _timestamp_text(exit_at),
        "holding_period_hours": theory.holding_period_hours,
        "gross_return_pct": gross_return_pct,
        "gross_objective_pct": gross_objective_pct,
    }
    for cost in costs:
        row[f"{cost.id}_objective_pct"] = (
            veto_objective_pct(gross_return_pct, cost.per_side_bps)
            if theory.role == "veto"
            else entry_objective_pct(gross_return_pct, cost.per_side_bps)
        )
    return row


def build_theory_events(
    signal_rows: tuple[dict[str, Any], ...],
    source_rows: tuple[dict[str, Any], ...],
    theories: tuple[TheorySpec, ...],
    *,
    evaluation_id: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
    blocks: tuple[TimeBlock, ...],
    entry_lag_hours: int,
    costs: tuple[CostScenario, ...],
) -> tuple[dict[str, Any], ...]:
    signal_index: dict[tuple[str, str], dict[datetime, dict[str, Any]]] = {}
    for row in signal_rows:
        try:
            identity = _normalized_identity(row["chain"], row["token_address"])
            timestamp = _parse_utc(row["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        signal_index.setdefault(identity, {}).setdefault(timestamp, row)

    price_index: dict[tuple[str, str, datetime], float] = {}
    for row in source_rows:
        try:
            identity = _normalized_identity(row["chain"], row["address"])
            timestamp = _parse_utc(row["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        price = _positive_finite_price(row)
        if price is not None:
            price_index.setdefault((*identity, timestamp), price)

    earliest = datetime.min.replace(tzinfo=timezone.utc)
    events: list[dict[str, Any]] = []
    for theory in sorted(theories, key=lambda item: item.id):
        next_allowed: dict[tuple[str, str], datetime] = {}
        for current in sorted(signal_rows, key=_signal_sort_key):
            timestamp = _parse_utc(current["timestamp"])
            identity = _normalized_identity(current["chain"], current["token_address"])
            if not evaluation_start <= timestamp < evaluation_end:
                continue
            if timestamp < next_allowed.get(identity, earliest):
                continue
            token_signals = signal_index.get(identity, {})
            if not all(
                predicate_matches(predicate, current=current, by_timestamp=token_signals)
                for predicate in theory.predicates
            ):
                continue
            entry_at = timestamp + timedelta(hours=entry_lag_hours)
            exit_at = entry_at + timedelta(hours=theory.holding_period_hours)
            if exit_at > evaluation_end:
                continue
            entry = price_index.get((*identity, entry_at))
            exit_price = price_index.get((*identity, exit_at))
            block = next((item for item in blocks if item.start <= timestamp < item.end), None)
            if entry is None or exit_price is None or block is None:
                continue
            gross = round(100 * (exit_price / entry - 1), 12)
            if not math.isfinite(gross):
                continue
            objective = -gross if theory.role == "veto" else gross
            events.append(
                _event_row(
                    theory,
                    current,
                    block,
                    entry_at,
                    exit_at,
                    gross,
                    objective,
                    costs,
                    evaluation_id=evaluation_id,
                )
            )
            next_allowed[identity] = exit_at
    return tuple(events)


_SUMMARY_METRICS = (
    "event_mean_gross_objective_pct",
    "event_median_gross_objective_pct",
    "event_mean_base_objective_pct",
    "event_median_base_objective_pct",
    "event_mean_stress_objective_pct",
    "event_median_stress_objective_pct",
    "event_win_rate_base",
    "token_equal_mean_gross_objective_pct",
    "token_equal_mean_base_objective_pct",
    "token_equal_mean_stress_objective_pct",
    "max_token_positive_pnl_contribution",
)


def _numeric_values(
    rows: tuple[dict[str, Any], ...], field: str
) -> tuple[float, ...] | None:
    values: list[float] = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        values.append(number)
    return tuple(values)


def _token_equal_mean(
    rows: tuple[dict[str, Any], ...], field: str
) -> float | None:
    token_values: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        values = _numeric_values((row,), field)
        if values is None:
            return None
        identity = _normalized_identity(row.get("chain"), row.get("token_address"))
        token_values.setdefault(identity, []).append(values[0])
    if not token_values:
        return None
    return statistics.fmean(
        statistics.fmean(values) for _, values in sorted(token_values.items())
    )


def _positive_contribution(rows: tuple[dict[str, Any], ...]) -> float | None:
    positive_by_token: dict[tuple[str, str], float] = {}
    for row in rows:
        values = _numeric_values((row,), "base_objective_pct")
        if values is None:
            return None
        identity = _normalized_identity(row.get("chain"), row.get("token_address"))
        positive_by_token[identity] = positive_by_token.get(identity, 0.0) + max(
            values[0], 0.0
        )
    total = sum(positive_by_token.values())
    if total <= 0:
        return None
    return max(positive_by_token.values()) / total


def _summary_metrics(rows: tuple[dict[str, Any], ...]) -> dict[str, float | None]:
    if not rows:
        return {field: None for field in _SUMMARY_METRICS}
    gross = _numeric_values(rows, "gross_objective_pct")
    base = _numeric_values(rows, "base_objective_pct")
    stress = _numeric_values(rows, "stress_objective_pct")
    return {
        "event_mean_gross_objective_pct": None if gross is None else statistics.fmean(gross),
        "event_median_gross_objective_pct": None if gross is None else statistics.median(gross),
        "event_mean_base_objective_pct": None if base is None else statistics.fmean(base),
        "event_median_base_objective_pct": None if base is None else statistics.median(base),
        "event_mean_stress_objective_pct": None if stress is None else statistics.fmean(stress),
        "event_median_stress_objective_pct": None if stress is None else statistics.median(stress),
        "event_win_rate_base": None
        if base is None
        else sum(value > 0 for value in base) / len(base),
        "token_equal_mean_gross_objective_pct": _token_equal_mean(
            rows, "gross_objective_pct"
        ),
        "token_equal_mean_base_objective_pct": _token_equal_mean(
            rows, "base_objective_pct"
        ),
        "token_equal_mean_stress_objective_pct": _token_equal_mean(
            rows, "stress_objective_pct"
        ),
        "max_token_positive_pnl_contribution": _positive_contribution(rows),
    }


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value > 0
    )


def _gate_result(
    theory: TheorySpec,
    row: dict[str, Any],
    *,
    positive_blocks: int,
    gates: dict[str, Any],
) -> tuple[str, str]:
    if theory.role == "reference":
        return "ineligible", "benchmark_only"
    if theory.role == "comparison":
        return "ineligible", "comparison_only"
    reasons: list[str] = []
    if theory.role == "entry":
        if row["event_count"] < gates["entry_min_events"]:
            reasons.append("insufficient_events")
        if row["token_count"] < gates["entry_min_tokens"]:
            reasons.append("insufficient_tokens")
        if not _positive_number(row["token_equal_mean_base_objective_pct"]):
            reasons.append("nonpositive_token_equal_mean_base")
        if not _positive_number(row["event_median_base_objective_pct"]):
            reasons.append("nonpositive_event_median_base")
        if positive_blocks < gates["entry_min_positive_blocks"]:
            reasons.append("insufficient_positive_blocks")
    elif theory.role == "veto":
        if row["event_count"] < gates["veto_min_events"]:
            reasons.append("insufficient_events")
        if row["token_count"] < gates["veto_min_tokens"]:
            reasons.append("insufficient_tokens")
        if not _positive_number(row["token_equal_mean_base_objective_pct"]):
            reasons.append("nonpositive_token_equal_mean_base")
        if not _positive_number(row["event_median_base_objective_pct"]):
            reasons.append("nonpositive_event_median_base")
    else:
        reasons.append("unsupported_role")
    ordered = ";".join(sorted(reasons))
    return ("eligible", "") if not reasons else ("ineligible", ordered)


def _summarize_group(
    evaluation_id: str,
    theory: TheorySpec,
    block_id: str,
    rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    token_ids = {
        _normalized_identity(row.get("chain"), row.get("token_address"))
        for row in rows
    }
    return {
        "evaluation_id": evaluation_id,
        "theory_id": theory.id,
        "theory_role": theory.role,
        "block_id": block_id,
        "event_count": len(rows),
        "token_count": len(token_ids),
        **_summary_metrics(rows),
        "gate_status": "not_applicable",
        "gate_reason_codes": "",
    }


def build_theory_summaries(
    events: tuple[dict[str, Any], ...],
    bundle: EvaluationBundle,
) -> tuple[dict[str, Any], ...]:
    summaries: list[dict[str, Any]] = []
    for theory in sorted(bundle.theories, key=lambda item: item.id):
        theory_rows = tuple(row for row in events if row.get("theory_id") == theory.id)
        block_rows = [
            _summarize_group(
                bundle.experiment_id,
                theory,
                block.id,
                tuple(row for row in theory_rows if row.get("block_id") == block.id),
            )
            for block in bundle.blocks
        ]
        overall = _summarize_group(bundle.experiment_id, theory, "all", theory_rows)
        positive_blocks = sum(
            _positive_number(row["token_equal_mean_base_objective_pct"])
            for row in block_rows
            if row["event_count"] > 0
        )
        status, reasons = _gate_result(
            theory,
            overall,
            positive_blocks=positive_blocks,
            gates=bundle.manifest["paper_feasibility_gates"],
        )
        overall["gate_status"] = status
        overall["gate_reason_codes"] = reasons
        summaries.extend((overall, *block_rows))
    return tuple(summaries)


def _comparison_row(
    comparison: ComparisonSpec,
    positive: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    mean_values = (
        positive.get("token_equal_mean_base_objective_pct"),
        reference.get("token_equal_mean_base_objective_pct"),
    )
    median_values = (
        positive.get("event_median_base_objective_pct"),
        reference.get("event_median_base_objective_pct"),
    )
    mean_spread = (
        mean_values[0] - mean_values[1]
        if all(_finite_nonboolean_number(value) for value in mean_values)
        else None
    )
    median_spread = (
        median_values[0] - median_values[1]
        if all(_finite_nonboolean_number(value) for value in median_values)
        else None
    )
    reasons: list[str] = []
    if mean_spread is None:
        reasons.append("missing_token_equal_mean_base_spread")
    elif mean_spread <= 0:
        reasons.append("nonpositive_token_equal_mean_base_spread")
    if median_spread is None:
        reasons.append("missing_event_median_base_spread")
    elif median_spread <= 0:
        reasons.append("nonpositive_event_median_spread")
    if mean_spread is None or median_spread is None:
        status = "insufficient_evidence"
    elif reasons:
        status = "does_not_advance"
    else:
        status = "advances_for_paper_discovery"
    return {
        "comparison_id": comparison.id,
        "positive_arm_theory_id": comparison.positive_arm,
        "reference_arm_theory_id": comparison.reference_arm,
        "token_equal_mean_base_spread_pct": mean_spread,
        "event_median_base_spread_pct": median_spread,
        "comparison_status": status,
        "reason_codes": ";".join(sorted(reasons)),
    }


def build_comparison_results(
    summaries: tuple[dict[str, Any], ...],
    comparisons: tuple[ComparisonSpec, ...],
) -> tuple[dict[str, Any], ...]:
    overall = {
        (row.get("theory_id"), row.get("block_id")): row for row in summaries
    }
    results: list[dict[str, Any]] = []
    for comparison in sorted(comparisons, key=lambda item: item.id):
        positive = overall.get((comparison.positive_arm, "all"), {})
        reference = overall.get((comparison.reference_arm, "all"), {})
        results.append(_comparison_row(comparison, positive, reference))
    return tuple(results)


_PAPER_EXECUTION_POLICY = {
    "signal_time": "max(bucket_end, provider_available_at)",
    "minimum_quote_delay_minutes": 5,
    "maximum_quote_age_seconds": 60,
    "fixed_exit_hours": 4,
    "non_overlap": "one_open_episode_per_token",
    "virtual_notional_usd": "min(1000, 0.001 * point_in_time_liquidity_usd)",
    "unfilled_conditions": ["missing_route", "one_way_quoted_cost_above_2_5_pct"],
    "recorded_costs": ["fee", "gas", "spread", "slippage"],
    "real_execution_enabled": False,
}

_PROSPECTIVE_ADVANCEMENT_REQUIREMENTS = {
    "timestamped_quotes_required": True,
    "point_in_time_liquidity_required": True,
    "actual_simulated_cost_mean_must_be_positive": True,
    "actual_simulated_cost_median_must_be_positive": True,
    "token_week_block_bootstrap_lower_one_sided_95_pct_must_be_positive": True,
    "stress_expectancy_must_be_non_negative": True,
}


def _reason_list(row: dict[str, Any]) -> list[str]:
    raw = row.get("gate_reason_codes")
    if not isinstance(raw, str):
        return ["missing_gate_evidence"]
    return [item for item in raw.split(";") if item]


def _manifest_theory(bundle: EvaluationBundle, theory_id: str) -> dict[str, Any]:
    record = next(
        item for item in bundle.manifest["theories"] if item["id"] == theory_id
    )
    return copy.deepcopy(record)


def build_paper_selection(
    bundle: EvaluationBundle,
    summaries: tuple[dict[str, Any], ...],
    comparisons: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    overall = {
        row.get("theory_id"): row
        for row in summaries
        if row.get("block_id") == "all"
    }
    entry_candidates = sorted(
        (
            row
            for row in overall.values()
            if row.get("theory_role") == "entry"
            and row.get("gate_status") == "eligible"
            and _finite_nonboolean_number(
                row.get("token_equal_mean_base_objective_pct")
            )
        ),
        key=lambda row: (
            -float(row["token_equal_mean_base_objective_pct"]),
            str(row["theory_id"]),
        ),
    )
    selected_entry = None if not entry_candidates else str(entry_candidates[0]["theory_id"])
    veto_candidates = sorted(
        (
            row
            for row in overall.values()
            if row.get("theory_role") == "veto"
            and row.get("gate_status") == "eligible"
            and _finite_nonboolean_number(
                row.get("token_equal_mean_base_objective_pct")
            )
        ),
        key=lambda row: (
            -float(row["token_equal_mean_base_objective_pct"]),
            str(row["theory_id"]),
        ),
    )
    selected_veto = (
        None
        if selected_entry is None or not veto_candidates
        else str(veto_candidates[0]["theory_id"])
    )
    selected_ids = [
        theory_id
        for theory_id in (selected_entry, selected_veto)
        if theory_id is not None
    ]
    unselected: list[dict[str, Any]] = []
    for theory in sorted(bundle.theories, key=lambda item: item.id):
        if theory.id in selected_ids:
            continue
        row = overall.get(theory.id, {})
        if theory.role == "reference":
            reasons = ["benchmark_only"]
        elif theory.role == "comparison":
            reasons = ["comparison_only"]
        elif theory.role == "veto" and selected_entry is None and row.get("gate_status") == "eligible":
            reasons = ["requires_selected_entry"]
        elif row.get("gate_status") == "eligible":
            reasons = [
                "lower_ranked_eligible_veto"
                if theory.role == "veto"
                else "lower_ranked_eligible_entry"
            ]
        else:
            reasons = _reason_list(row)
        unselected.append(
            {"id": theory.id, "role": theory.role, "reason_codes": sorted(reasons)}
        )
    source_manifest = bundle.source_bundle.manifest
    return {
        "evaluation_id": bundle.experiment_id,
        "mode": "paper_only",
        "source": {
            "signal_experiment_id": source_manifest["experiment_id"],
            "point_in_time_guarantee": source_manifest["point_in_time_guarantee"],
            "evidence_status": bundle.manifest["status"],
        },
        "selection_status": (
            "selected_for_paper_discovery"
            if selected_entry is not None
            else "no_paper_strategy_selected"
        ),
        "warning": "Unvalidated discovery shortlist for prospective paper testing only.",
        "selected_entry_theory_id": selected_entry,
        "selected_veto_theory_id": selected_veto,
        "selected_theories": [
            _manifest_theory(bundle, theory_id) for theory_id in selected_ids
        ],
        "unselected_theories": unselected,
        "blocked_theories": [
            {
                "id": item.id,
                "reason": item.reason,
                "missing_roles": list(item.missing_roles),
            }
            for item in sorted(bundle.blocked_theories, key=lambda item: item.id)
        ],
        "comparisons": [copy.deepcopy(item) for item in comparisons],
        "evaluation_execution": copy.deepcopy(bundle.manifest["execution"]),
        "paper_execution_policy": copy.deepcopy(_PAPER_EXECUTION_POLICY),
        "prospective_advancement_gates": {
            **copy.deepcopy(bundle.manifest["prospective_advancement_gates"]),
            **copy.deepcopy(_PROSPECTIVE_ADVANCEMENT_REQUIREMENTS),
        },
    }


def build_evaluation(bundle: EvaluationBundle) -> EvaluationTables:
    signal_rows = build_signal_analysis(bundle.source_bundle)
    source_rows = build_analysis(bundle.source_bundle.source_bundle).hourly_features
    evaluation_window = bundle.manifest["evaluation_window"]
    events = build_theory_events(
        signal_rows,
        source_rows,
        bundle.theories,
        evaluation_id=bundle.experiment_id,
        evaluation_start=_parse_utc(evaluation_window["from"]),
        evaluation_end=_parse_utc(evaluation_window["to"]),
        blocks=bundle.blocks,
        entry_lag_hours=bundle.manifest["execution"]["entry_lag_hours"],
        costs=bundle.costs,
    )
    summaries = build_theory_summaries(events, bundle)
    comparisons = build_comparison_results(summaries, bundle.comparisons)
    paper_selection = build_paper_selection(bundle, summaries, comparisons)
    return EvaluationTables(events, summaries, comparisons, paper_selection)
