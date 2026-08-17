from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .experiment import SignalBundle, load_signal_manifest, sha256_file, signal_fieldnames


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
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
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
                try:
                    finite = math.isfinite(float(item))
                except (OverflowError, ValueError):
                    finite = False
                if not finite:
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
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
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


def _finite_nonboolean_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


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
