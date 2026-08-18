from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from .cohort_schema import CYCLE_COUNT, CohortSchemaError, parse_utc
from .cohort_selection import normalized_identity


class CohortAggregateError(RuntimeError):
    """Raised when terminal cohort records cannot be aggregated safely."""


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CohortAggregateError(f"{field} must be finite")
    number = float(value)
    if not math.isfinite(number):
        raise CohortAggregateError(f"{field} must be finite")
    return number


def _event_identity(row: dict[str, Any]) -> tuple[str, str]:
    try:
        return normalized_identity(row["chain"], row["token_address"])
    except (KeyError, ValueError) as exc:
        raise CohortAggregateError("event token identity is invalid") from exc


def _validated(records: Iterable[dict[str, Any]], *, rule_id: str) -> list[dict[str, Any]]:
    if not isinstance(rule_id, str) or not rule_id:
        raise CohortAggregateError("rule_id must be non-empty")
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, tuple[str, str], str]] = set()
    cycle_timestamps: dict[int, tuple[str, str]] = {}
    for source in records:
        if not isinstance(source, dict):
            raise CohortAggregateError("aggregate records must be objects")
        if source.get("rule_id") != rule_id:
            continue
        cycle = source.get("cycle_index")
        if (
            not isinstance(cycle, int)
            or isinstance(cycle, bool)
            or not 1 <= cycle <= CYCLE_COUNT
        ):
            raise CohortAggregateError("event cycle_index is invalid")
        identity = _event_identity(source)
        availability = source.get("availability")
        action = source.get("action")
        if availability not in {"AVAILABLE", "UNAVAILABLE"}:
            raise CohortAggregateError("event availability is invalid")
        if availability == "AVAILABLE" and action not in {"LONG", "ABSTAIN"}:
            raise CohortAggregateError("available event action must be LONG or ABSTAIN")
        if availability == "UNAVAILABLE" and action is not None:
            raise CohortAggregateError("unavailable event action must be null")
        outcome = source.get("outcome")
        if not isinstance(outcome, dict):
            raise CohortAggregateError("event outcome must be an object")
        status = outcome.get("status")
        if status not in {"SCORED", "UNFILLED_ENTRY", "UNFILLED_EXIT", "UNAVAILABLE"}:
            raise CohortAggregateError("event outcome status is invalid")
        key = (cycle, identity, rule_id)
        if key in seen:
            raise CohortAggregateError("aggregate records contain a duplicate opportunity")
        seen.add(key)
        scheduled_at = source.get("scheduled_at")
        decision_t0 = source.get("decision_t0")
        try:
            scheduled = parse_utc(scheduled_at, field="scheduled_at")
            decision = parse_utc(decision_t0, field="decision_t0")
        except CohortSchemaError as exc:
            raise CohortAggregateError("event timestamps are invalid") from exc
        if decision < scheduled:
            raise CohortAggregateError("decision_t0 precedes the scheduled cycle")
        timestamp_pair = (scheduled_at, decision_t0)
        if cycle in cycle_timestamps and cycle_timestamps[cycle] != timestamp_pair:
            raise CohortAggregateError("records disagree on cycle timestamps")
        cycle_timestamps[cycle] = timestamp_pair
        iso_year, iso_week, _ = decision.isocalendar()
        row = dict(source)
        row["identity"] = identity
        row["decision_datetime"] = decision
        row["utc_week"] = f"{iso_year:04d}-W{iso_week:02d}"
        if status == "SCORED":
            for field in ("gross_return", "base_return_100bps", "stress_return_250bps"):
                row[field] = _finite(outcome.get(field), field=field)
        result.append(row)
    return sorted(result, key=lambda row: (row["cycle_index"], row["identity"]))


def _token_equal(rows: Sequence[dict[str, Any]], field: str) -> float | None:
    if not rows:
        return None
    by_token: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        by_token[row["identity"]].append(float(row[field]))
    token_means = [statistics.fmean(values) for values in by_token.values()]
    return statistics.fmean(token_means)


def token_week_block_bootstrap(
    rows: Sequence[dict[str, Any]],
    *,
    program_id: str,
    replicates: int = 10_000,
) -> dict[str, Any]:
    if not isinstance(program_id, str) or not program_id:
        raise CohortAggregateError("program_id must be non-empty")
    if not isinstance(replicates, int) or isinstance(replicates, bool) or replicates <= 0:
        raise CohortAggregateError("bootstrap replicates must be positive")
    scored = [row for row in rows if row.get("outcome", {}).get("status") == "SCORED"]
    weeks = sorted({row["utc_week"] for row in scored})
    tokens = {row["identity"] for row in scored}
    if len(weeks) < 8 or len(tokens) < 20:
        return {
            "available": False,
            "reason": "fewer_than_8_weeks_or_20_tokens",
            "replicates": 0,
            "lower_95": None,
            "upper_95": None,
        }
    by_week_token: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in scored:
        by_week_token[row["utc_week"]][row["identity"]].append(row)
    seed = int.from_bytes(hashlib.sha256(program_id.encode("utf-8")).digest(), "big")
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(replicates):
        sampled_weeks = generator.choices(weeks, k=len(weeks))
        sampled_by_token: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for week in sampled_weeks:
            for token, token_rows in by_week_token[week].items():
                sampled_by_token[token].extend(token_rows)
        available_tokens = sorted(sampled_by_token)
        sampled: list[dict[str, Any]] = []
        for token_draw, token in enumerate(
            generator.choices(available_tokens, k=len(available_tokens))
        ):
            for row in sampled_by_token[token]:
                copy = dict(row)
                # Each program-level token draw is one synthetic token. This
                # preserves the point estimator: an equal mean of token means.
                copy["identity"] = ("token-draw", f"{token_draw:06d}")
                sampled.append(copy)
        estimate = _token_equal(sampled, "base_return_100bps")
        if estimate is None:  # pragma: no cover - guarded by availability above
            raise CohortAggregateError("bootstrap produced an empty replicate")
        estimates.append(estimate)
    estimates.sort()
    lower_index = max(0, math.floor(0.025 * replicates))
    upper_index = min(replicates - 1, math.ceil(0.975 * replicates) - 1)
    return {
        "available": True,
        "reason": None,
        "replicates": replicates,
        "lower_95": estimates[lower_index],
        "upper_95": estimates[upper_index],
        "seed_sha256": hashlib.sha256(program_id.encode("utf-8")).hexdigest(),
    }


def aggregate_rule(
    records: Iterable[dict[str, Any]],
    *,
    rule_id: str,
    program_id: str,
    terminal_cycle_count: int,
    outcome_cycle_count: int,
    availability_integrity_ok: bool,
    advance_eligible: bool,
    bootstrap_replicates: int = 10_000,
) -> dict[str, Any]:
    if (
        not isinstance(terminal_cycle_count, int)
        or isinstance(terminal_cycle_count, bool)
        or not 0 <= terminal_cycle_count <= CYCLE_COUNT
    ):
        raise CohortAggregateError("terminal_cycle_count is invalid")
    if (
        not isinstance(outcome_cycle_count, int)
        or isinstance(outcome_cycle_count, bool)
        or not 0 <= outcome_cycle_count <= terminal_cycle_count
    ):
        raise CohortAggregateError("outcome_cycle_count is invalid")
    if not isinstance(availability_integrity_ok, bool):
        raise CohortAggregateError("availability_integrity_ok must be boolean")
    if not isinstance(advance_eligible, bool):
        raise CohortAggregateError("advance_eligible must be boolean")
    rows = _validated(records, rule_id=rule_id)
    signals = [row for row in rows if row["action"] == "LONG"]
    unavailable_count = sum(row["availability"] != "AVAILABLE" for row in rows)
    scored_signals = [
        row for row in signals if row["outcome"].get("status") == "SCORED"
    ]
    counterfactual_scored = [
        row for row in rows if row["outcome"].get("status") == "SCORED"
    ]
    token_counts = Counter(row["identity"] for row in scored_signals)
    week_counts = Counter(row["utc_week"] for row in scored_signals)
    score_count = len(scored_signals)
    token_share = max(token_counts.values(), default=0) / score_count if score_count else None
    week_share = max(week_counts.values(), default=0) / score_count if score_count else None
    fill_rate = score_count / len(signals) if signals else None
    base = _token_equal(scored_signals, "base_return_100bps")
    stress = _token_equal(scored_signals, "stress_return_250bps")
    median = (
        statistics.median(row["base_return_100bps"] for row in scored_signals)
        if scored_signals
        else None
    )
    bootstrap = token_week_block_bootstrap(
        scored_signals,
        program_id=program_id,
        replicates=bootstrap_replicates,
    )
    reasons: list[str] = []
    if not advance_eligible:
        reasons.append("descriptive_rule_not_advance_eligible")
    if terminal_cycle_count != CYCLE_COUNT:
        reasons.append("not_all_32_cycles_terminal")
    if outcome_cycle_count != CYCLE_COUNT:
        reasons.append("one_or_more_cycles_unscorable")
    if not availability_integrity_ok:
        reasons.append("availability_or_budget_integrity_failure")
    if unavailable_count:
        reasons.append("one_or_more_rule_decisions_unavailable")
    decision_times = [row["decision_datetime"] for row in rows]
    observation_span = (
        max(decision_times) - min(decision_times) if decision_times else timedelta(0)
    )
    if observation_span < timedelta(days=56):
        reasons.append("observation_span_below_8_weeks")
    if len({row["utc_week"] for row in rows}) < 8:
        reasons.append("fewer_than_8_utc_weeks")
    if score_count < 100:
        reasons.append("insufficient_strategy_fills")
    if len(token_counts) < 20:
        reasons.append("fewer_than_20_unique_filled_tokens")
    if fill_rate is None or fill_rate < 0.70:
        reasons.append("strategy_fill_rate_below_70_percent")
    if base is None or base <= 0:
        reasons.append("nonpositive_token_equal_base_return")
    if median is None or median <= 0:
        reasons.append("nonpositive_event_median_base_return")
    if stress is None or stress <= 0:
        reasons.append("nonpositive_token_equal_stress_return")
    if token_share is None or token_share > 0.20:
        reasons.append("token_concentration_above_20_percent")
    if week_share is None or week_share > 0.25:
        reasons.append("week_concentration_above_25_percent")
    if not bootstrap["available"] or bootstrap["lower_95"] <= 0:
        reasons.append("bootstrap_lower_bound_not_positive")
    return {
        "schema_version": 1,
        "program_id": program_id,
        "rule_id": rule_id,
        "advance_eligible": advance_eligible,
        "selection_status": "advances" if not reasons else "does_not_advance",
        "counts": {
            "opportunities": len(rows),
            "available_opportunities": len(rows) - unavailable_count,
            "unavailable_opportunities": unavailable_count,
            "strategy_signals": len(signals),
            "counterfactual_scored": len(counterfactual_scored),
            "filled_strategy_signals": score_count,
            "unique_filled_tokens": len(token_counts),
            "represented_utc_weeks": len({row["utc_week"] for row in rows}),
            "observation_span_seconds": observation_span.total_seconds(),
            "terminal_cycles": terminal_cycle_count,
            "outcome_cycles": outcome_cycle_count,
        },
        "metrics": {
            "strategy_fill_rate": fill_rate,
            "token_equal_base_return": base,
            "event_median_base_return": median,
            "token_equal_stress_return": stress,
            "maximum_token_share": token_share,
            "maximum_week_share": week_share,
        },
        "bootstrap": bootstrap,
        "gate_reasons": reasons,
    }
