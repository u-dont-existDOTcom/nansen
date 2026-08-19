from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from .design import (
    B2DesignError,
    DISCOVERY_CYCLES,
    HISTORICAL_VETO_PREDICATES,
    PARENT_CANDIDATE_CONTRACT_SHA256,
    PARENT_CANDIDATE_PREDICATES,
    PARENT_NONCASH_CANDIDATE_IDS,
    PROGRAM_ID,
    SCHEDULE,
    SETTLEMENT_OFFSET,
    SETTLEMENT_TRANSPORT_CUTOFF,
    TOKENS_PER_CYCLE,
    VALIDATION_CYCLES,
    canonical_sha256,
    compose_decision,
    identity_partition,
    normalize_identity,
    selection_hash,
    token_equal_mean,
    _execution_result,
)


BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_LOWER_INDEX = 249
APRIORI_ANCHOR_ID = "c01-buyer-breadth-exchange"

PHASE_GATES = {
    "discovery": {
        "cycles": DISCOVERY_CYCLES,
        "complete_cycles": 38,
        "selected": 492,
        "decision_availability": 0.90,
        "scored_longs": 30,
        "physical_tokens": 20,
        "weeks": 2,
        "fill_rate": 0.70,
        "represented_blocks": 6,
        "positive_blocks": 4,
        "max_token_share": 0.20,
        "max_block_share": 0.30,
        "max_chain_share": 0.60,
    },
    "validation": {
        "cycles": VALIDATION_CYCLES,
        "complete_cycles": 39,
        "selected": 504,
        "decision_availability": 0.95,
        "scored_longs": 70,
        "physical_tokens": 25,
        "weeks": 2,
        "fill_rate": 0.70,
        "represented_blocks": 6,
        "positive_blocks": 4,
        "max_token_share": 0.15,
        "max_week_share": 0.60,
        "max_chain_share": 0.60,
    },
}


def _phase_cycles(phase: str) -> tuple[Any, ...]:
    if phase not in PHASE_GATES:
        raise B2DesignError("phase must be discovery or validation")
    return tuple(cycle for cycle in SCHEDULE if cycle.phase == phase)


def _records_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    try:
        encoded = json.dumps(
            list(records),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise B2DesignError("phase records are not canonical JSON evidence") from exc
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _selection(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("selection")
    if not isinstance(value, Mapping):
        raise B2DesignError("opportunity record omits selection")
    return value


def _outcome(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("outcome")
    return value if isinstance(value, Mapping) else {}


def _cycle(record: Mapping[str, Any]) -> Any:
    index = record.get("cycle_index")
    if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= len(SCHEDULE):
        raise B2DesignError("opportunity has invalid cycle_index")
    return SCHEDULE[index - 1]


def _validated_phase_records(
    phase: str, records: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any], ...]:
    cycles = _phase_cycles(phase)
    expected = [
        (cycle.index, f"ps-c{cycle.index:03d}-b{band:02d}", band)
        for cycle in cycles
        for band in range(1, TOKENS_PER_CYCLE + 1)
    ]
    if len(records) != len(expected):
        raise B2DesignError("phase opportunity denominator differs from the frozen plan")
    actual: list[tuple[int, str, int]] = []
    for record in records:
        cycle = _cycle(record)
        selection = _selection(record)
        event_id = selection.get("event_id")
        band = selection.get("rank_band")
        if (
            cycle.phase != phase
            or selection.get("cycle_index") != cycle.index
            or selection.get("phase") != phase
            or not isinstance(event_id, str)
            or not isinstance(band, int)
            or isinstance(band, bool)
        ):
            raise B2DesignError("phase opportunity identity is invalid")
        status = selection.get("status")
        if status == "selected":
            try:
                chain, address = normalize_identity(
                    selection.get("chain"), selection.get("token_address")
                )
            except B2DesignError as exc:
                raise B2DesignError("selected opportunity identity is invalid") from exc
            required_partition = (
                "DISCOVERY" if phase == "discovery" else "VALIDATION"
            )
            if (
                selection.get("partition") != required_partition
                or identity_partition(chain, address) != required_partition
                or selection.get("selection_hash")
                != selection_hash(cycle, chain, address)
            ):
                raise B2DesignError("selected opportunity provenance differs")
        elif status != "unavailable":
            raise B2DesignError("opportunity status is invalid")
        actual.append((cycle.index, event_id, band))
    if actual != expected:
        raise B2DesignError("phase event IDs or rank bands differ from the frozen plan")
    return tuple(records)


def _validated_outcome_marker(
    selection: Mapping[str, Any], outcome: Mapping[str, Any]
) -> bool:
    identity = outcome.get("identity")
    evidence = outcome.get("evidence_sha256")
    if (
        outcome.get("schema_version") != 1
        or outcome.get("source_kind") != "validated_counterfactual_outcome"
        or outcome.get("event_id") != selection.get("event_id")
        or outcome.get("cycle_index") != selection.get("cycle_index")
        or outcome.get("phase") != selection.get("phase")
        or not isinstance(identity, Mapping)
        or not isinstance(evidence, Mapping)
        or not _is_sha256(outcome.get("replay_attestation_sha256"))
        or not isinstance(evidence.get("buy_pages"), list)
        or not 1 <= len(evidence["buy_pages"]) <= 2
        or not all(_is_sha256(value) for value in evidence["buy_pages"])
        or not isinstance(evidence.get("sell_pages"), list)
        or not 1 <= len(evidence["sell_pages"]) <= 2
        or not all(_is_sha256(value) for value in evidence["sell_pages"])
        or not _is_sha256(evidence.get("ohlcv"))
        or outcome.get("ohlcv_available") is not True
        or outcome.get("ohlcv_row_count") != 52
    ):
        return False
    cycle_index = selection.get("cycle_index")
    if not isinstance(cycle_index, int) or isinstance(cycle_index, bool):
        return False
    expected_scheduled = SCHEDULE[cycle_index - 1].scheduled_at.isoformat().replace(
        "+00:00", "Z"
    )
    if outcome.get("scheduled_at") != expected_scheduled:
        return False
    try:
        identity_matches = normalize_identity(
            identity.get("chain"), identity.get("token_address")
        ) == normalize_identity(
            selection.get("chain"), selection.get("token_address")
        )
        if not identity_matches:
            return False
        t0_text = outcome.get("t0")
        retrieved_text = outcome.get("retrieved_at")
        if not isinstance(t0_text, str) or not isinstance(retrieved_text, str):
            return False
        t0 = datetime.fromisoformat(t0_text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
        retrieved = datetime.fromisoformat(
            retrieved_text.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        cycle = SCHEDULE[cycle_index - 1]
        if (
            t0 < cycle.scheduled_at + timedelta(minutes=5)
            or t0 > cycle.scheduled_at + timedelta(minutes=50)
            or t0.minute % 5
            or t0.second
            or t0.microsecond
            or retrieved < t0 + SETTLEMENT_OFFSET
            or retrieved >= cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF
        ):
            return False
        entry = outcome.get("entry")
        exit_value = outcome.get("exit")
        if not isinstance(entry, Mapping) or (
            exit_value is not None and not isinstance(exit_value, Mapping)
        ):
            return False
        entry_fill = SimpleNamespace(
            side="BUY",
            requested_amount=entry.get("requested_amount"),
            filled_token_amount=entry.get("filled_token_amount"),
            observed_usd=entry.get("observed_usd"),
            vwap_usd=entry.get("vwap_usd"),
            trade_count=entry.get("trade_count"),
            fill_ratio=entry.get("fill_ratio"),
            is_complete=entry.get("is_complete"),
        )
        exit_fill = None
        if isinstance(exit_value, Mapping):
            exit_fill = SimpleNamespace(
                side="SELL",
                requested_amount=exit_value.get("requested_amount"),
                filled_token_amount=exit_value.get("sold_token_amount"),
                observed_usd=exit_value.get("observed_usd"),
                vwap_usd=exit_value.get("vwap_usd"),
                trade_count=exit_value.get("trade_count"),
                fill_ratio=exit_value.get("fill_ratio"),
                is_complete=exit_value.get("is_complete"),
            )
        replay = _execution_result(
            notional_usd=outcome.get("notional_usd"),
            entry_fill=entry_fill,
            exit_fill=exit_fill,
        )
        return all(outcome.get(key) == value for key, value in replay.items())
    except (B2DesignError, TypeError, ValueError, OverflowError):
        return False


def _validate_registered_decisions(record: Mapping[str, Any]) -> None:
    decisions = record.get("decisions")
    if decisions == {}:
        return
    if not isinstance(decisions, Mapping) or tuple(decisions) != PARENT_NONCASH_CANDIDATE_IDS:
        raise B2DesignError("selected opportunity decisions differ from the sealed registry")
    for candidate_id in PARENT_NONCASH_CANDIDATE_IDS:
        _decision(record, candidate_id)


def phase_support(
    *,
    phase: str,
    cycle_statuses: Mapping[int, str],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cycles = _phase_cycles(phase)
    materialized = _validated_phase_records(phase, records)
    expected_indices = {cycle.index for cycle in cycles}
    if set(cycle_statuses) != expected_indices:
        raise B2DesignError("cycle-status keys differ from the frozen phase schedule")
    if any(status not in {"complete", "unscorable"} for status in cycle_statuses.values()):
        raise B2DesignError("phase support requires every scheduled cycle terminal")
    expected_records = len(cycles) * TOKENS_PER_CYCLE
    selected = [
        record for record in materialized if _selection(record).get("status") == "selected"
    ]
    for record in selected:
        _validate_registered_decisions(record)
    ohlcv = sum(
        _validated_outcome_marker(_selection(record), _outcome(record))
        and _outcome(record).get("ohlcv_available") is True
        for record in selected
    )
    complete = sum(status == "complete" for status in cycle_statuses.values())
    for cycle in cycles:
        cycle_records = [record for record in materialized if record["cycle_index"] == cycle.index]
        derived_complete = all(
            _selection(record).get("status") == "selected"
            and isinstance(record.get("decisions"), Mapping)
            and tuple(record["decisions"]) == PARENT_NONCASH_CANDIDATE_IDS
            and _validated_outcome_marker(_selection(record), _outcome(record))
            and _outcome(record).get("ohlcv_available") is True
            for record in cycle_records
        )
        if (cycle_statuses[cycle.index] == "complete") != derived_complete:
            raise B2DesignError("sealed cycle status differs from its retained evidence")
    gates = PHASE_GATES[phase]
    metrics = {
        "all_cycles_terminal": True,
        "planned_opportunities": expected_records,
        "complete_cycles": complete,
        "selected_opportunities": len(selected),
        "selection_coverage": len(selected) / expected_records,
        "common_ohlcv_available": ohlcv,
        "common_ohlcv_coverage": 0.0 if not selected else ohlcv / len(selected),
    }
    checks = {
        "complete_cycles": complete >= gates["complete_cycles"],
        "selected_opportunities": len(selected) >= gates["selected"],
        "common_ohlcv_coverage": metrics["common_ohlcv_coverage"] >= 0.95,
    }
    return {**metrics, "checks": checks, "passed": all(checks.values())}


def _decision(record: Mapping[str, Any], candidate_id: str) -> str:
    if (
        not isinstance(candidate_id, str)
        or candidate_id not in PARENT_CANDIDATE_PREDICATES
    ):
        raise B2DesignError("candidate_id is outside the sealed eleven-rule registry")
    decisions = record.get("decisions")
    if decisions == {}:
        return "unavailable"
    if not isinstance(decisions, Mapping) or candidate_id not in decisions:
        raise B2DesignError("selected opportunity omits a sealed candidate decision")
    value = decisions.get(candidate_id)
    selection = _selection(record)
    cycle = _cycle(record)
    identity = value.get("identity") if isinstance(value, Mapping) else None
    input_sha256 = value.get("input_sha256") if isinstance(value, Mapping) else None
    candidate_predicates = (
        value.get("candidate_predicates") if isinstance(value, Mapping) else None
    )
    historical_veto_predicates = (
        value.get("historical_veto_predicates")
        if isinstance(value, Mapping)
        else None
    )
    prospective_veto = (
        value.get("prospective_veto") if isinstance(value, Mapping) else None
    )
    expected_candidate_predicates = PARENT_CANDIDATE_PREDICATES[candidate_id]

    def tri_state(item: Any) -> bool:
        return item is True or item is False or item is None

    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != 1
        or value.get("program_id") != PROGRAM_ID
        or value.get("source_kind") != "sealed_candidate_decision"
        or value.get("candidate_contract_sha256") != PARENT_CANDIDATE_CONTRACT_SHA256
        or value.get("candidate_id") != candidate_id
        or value.get("event_id") != selection.get("event_id")
        or value.get("cycle_index") != cycle.index
        or value.get("phase") != cycle.phase
        or value.get("scheduled_at")
        != cycle.scheduled_at.isoformat().replace("+00:00", "Z")
        or not isinstance(identity, Mapping)
        or not isinstance(input_sha256, Mapping)
        or set(input_sha256)
        != {"selection", "features", "candidate_definition", "sealed_crosswalk"}
        or not all(_is_sha256(item) for item in input_sha256.values())
        or input_sha256.get("selection") != canonical_sha256(selection)
        or not isinstance(candidate_predicates, Mapping)
        or set(candidate_predicates) != set(expected_candidate_predicates)
        or not all(tri_state(item) for item in candidate_predicates.values())
        or not isinstance(historical_veto_predicates, Mapping)
        or set(historical_veto_predicates) != set(HISTORICAL_VETO_PREDICATES)
        or not all(tri_state(item) for item in historical_veto_predicates.values())
        or not tri_state(prospective_veto)
    ):
        raise B2DesignError("sealed candidate decision provenance is invalid")
    try:
        if normalize_identity(
            identity.get("chain"), identity.get("token_address")
        ) != normalize_identity(
            selection.get("chain"), selection.get("token_address")
        ):
            raise B2DesignError("sealed candidate decision identity differs")
    except B2DesignError:
        raise
    decision = value.get("decision")
    replayed_decision = compose_decision(
        (candidate_predicates[name] for name in expected_candidate_predicates),
        (historical_veto_predicates[name] for name in HISTORICAL_VETO_PREDICATES),
        (prospective_veto,),
    )
    if (
        decision not in {"long", "abstain", "unavailable"}
        or decision != replayed_decision
    ):
        raise B2DesignError("sealed candidate decision is invalid")
    return decision


def _scored_event(record: Mapping[str, Any]) -> dict[str, Any] | None:
    outcome = _outcome(record)
    if outcome.get("status") != "filled":
        return None
    selection = _selection(record)
    if not _validated_outcome_marker(selection, outcome):
        raise B2DesignError("scored outcome lacks validated event provenance")
    base = outcome.get("base_return")
    stress = outcome.get("stress_return")
    if (
        isinstance(base, bool)
        or not isinstance(base, (int, float))
        or not math.isfinite(float(base))
        or isinstance(stress, bool)
        or not isinstance(stress, (int, float))
        or not math.isfinite(float(stress))
    ):
        raise B2DesignError("filled outcome omits finite base/stress returns")
    chain, address = normalize_identity(
        selection.get("chain"), selection.get("token_address")
    )
    cycle = _cycle(record)
    iso = cycle.scheduled_at.isocalendar()
    return {
        "chain": chain,
        "token_address": address,
        "cycle_index": cycle.index,
        "block": cycle.block,
        "week": f"{iso.year}-W{iso.week:02d}",
        "base_return": float(base),
        "stress_return": float(stress),
    }


def _max_share(values: Sequence[Any]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    return max(counts.values()) / len(values)


def _leave_best_token_out(events: Sequence[Mapping[str, Any]]) -> float | None:
    identities = sorted(
        {normalize_identity(event.get("chain"), event.get("token_address")) for event in events}
    )
    if len(identities) < 2:
        return None
    removals = []
    for identity in identities:
        retained = [
            event
            for event in events
            if normalize_identity(event.get("chain"), event.get("token_address")) != identity
        ]
        removals.append(token_equal_mean(retained, "base_return"))
    return min(removals)


def _bootstrap_seed(phase: str, candidate_id: str) -> int:
    digest = hashlib.sha256(
        f"{PROGRAM_ID}|{phase}|{candidate_id}|block-token-bootstrap-v1".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _bootstrap_lower_bound(
    events: Sequence[Mapping[str, Any]],
    *,
    phase: str,
    candidate_id: str,
    replicates: int,
) -> float:
    """Resample six phase blocks, then physical-token clusters.

    Each sampled block occurrence carries all of its events. Physical token IDs
    are then sampled from the resulting multiblock population, and each draw
    contributes the mean of all of that token's carried events. The replicate
    is the mean across token draws, retaining the token-equal estimand.
    """

    if not isinstance(replicates, int) or isinstance(replicates, bool) or replicates <= 0:
        raise B2DesignError("bootstrap replicate count must be positive")
    blocks = tuple(range(1, 7))
    by_block: dict[int, dict[tuple[str, str], tuple[float, ...]]] = {}
    for block in blocks:
        grouped: dict[tuple[str, str], list[float]] = {}
        for event in events:
            if event.get("block") != block:
                continue
            identity = normalize_identity(
                event.get("chain"), event.get("token_address")
            )
            grouped.setdefault(identity, []).append(float(event["base_return"]))
        by_block[block] = {
            identity: tuple(values) for identity, values in grouped.items()
        }
    if any(not by_block[block] for block in blocks):
        raise B2DesignError("bootstrap requires scored events in all six blocks")
    observed_returns = {
        value
        for grouped in by_block.values()
        for values in grouped.values()
        for value in values
    }
    # This is an exact algebraic shortcut, not a reduced-replicate path: every
    # possible resample of a constant outcome has the same statistic.
    if len(observed_returns) == 1:
        return next(iter(observed_returns))
    generator = random.Random(_bootstrap_seed(phase, candidate_id))
    results: list[float] = []
    carried_cache: dict[
        tuple[int, ...], tuple[tuple[tuple[str, str], ...], dict[tuple[str, str], float]]
    ] = {}
    for _ in range(replicates):
        sampled_blocks = [generator.choice(blocks) for _ in blocks]
        count_key = tuple(sampled_blocks.count(block) for block in blocks)
        cached = carried_cache.get(count_key)
        if cached is None:
            block_counts = dict(zip(blocks, count_key, strict=True))
            tokens = tuple(
                sorted(
                    {
                        identity
                        for block, repeats in block_counts.items()
                        if repeats
                        for identity in by_block[block]
                    }
                )
            )
            token_means: dict[tuple[str, str], float] = {}
            for identity in tokens:
                values = [
                    value
                    for block, repeats in block_counts.items()
                    for _ in range(repeats)
                    for value in by_block[block].get(identity, ())
                ]
                token_means[identity] = statistics.fmean(values)
            cached = (tokens, token_means)
            carried_cache[count_key] = cached
        tokens, token_means = cached
        sampled_tokens = [generator.choice(tokens) for _ in tokens]
        results.append(
            statistics.fmean(token_means[identity] for identity in sampled_tokens)
        )
    results.sort()
    index = min(max(0, math.ceil(0.025 * len(results)) - 1), len(results) - 1)
    if replicates == BOOTSTRAP_REPLICATES and index != BOOTSTRAP_LOWER_INDEX:
        raise AssertionError("frozen bootstrap lower index differs")
    return results[index]


def bootstrap_lower_bound(
    events: Sequence[Mapping[str, Any]], *, phase: str, candidate_id: str
) -> float:
    """Return the frozen 10,000-replicate classification statistic."""

    return _bootstrap_lower_bound(
        events,
        phase=phase,
        candidate_id=candidate_id,
        replicates=BOOTSTRAP_REPLICATES,
    )


def candidate_score(
    *,
    phase: str,
    candidate_id: str,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if candidate_id not in PARENT_NONCASH_CANDIDATE_IDS:
        raise B2DesignError("candidate_id is outside the sealed eleven-rule registry")
    materialized = _validated_phase_records(phase, records)
    selected = [
        record for record in materialized if _selection(record).get("status") == "selected"
    ]
    decisions = [_decision(record, candidate_id) for record in selected]
    available = sum(decision != "unavailable" for decision in decisions)
    long_records = [
        record
        for record, decision in zip(selected, decisions, strict=True)
        if decision == "long"
    ]
    scored = [event for record in long_records if (event := _scored_event(record)) is not None]
    base_values = [event["base_return"] for event in scored]
    stress_values = [event["stress_return"] for event in scored]
    token_ids = [(event["chain"], event["token_address"]) for event in scored]
    blocks = [event["block"] for event in scored]
    weeks = [event["week"] for event in scored]
    chains = [event["chain"] for event in scored]
    block_medians = {
        block: statistics.median(
            event["base_return"] for event in scored if event["block"] == block
        )
        for block in sorted(set(blocks))
    }
    metrics: dict[str, Any] = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "phase": phase,
        "records_sha256": _records_sha256(materialized),
        "candidate_id": candidate_id,
        "selected_opportunities": len(selected),
        "available_decisions": available,
        "decision_availability": 0.0 if not selected else available / len(selected),
        "long_signals": len(long_records),
        "scored_longs": len(scored),
        "signal_fill_rate": 0.0 if not long_records else len(scored) / len(long_records),
        "physical_tokens": len(set(token_ids)),
        "weeks": len(set(weeks)),
        "represented_blocks": len(set(blocks)),
        "positive_blocks": sum(value > 0 for value in block_medians.values()),
        "block_medians": block_medians,
        "max_token_share": _max_share(token_ids),
        "max_block_share": _max_share(blocks),
        "max_week_share": _max_share(weeks),
        "max_chain_share": _max_share(chains),
        "token_equal_base_mean": None,
        "event_base_median": None,
        "token_equal_stress_mean": None,
        "leave_best_token_out_base_mean": None,
        "bootstrap_lower_bound": None,
    }
    if scored:
        metrics.update(
            {
                "token_equal_base_mean": token_equal_mean(scored, "base_return"),
                "event_base_median": statistics.median(base_values),
                "token_equal_stress_mean": token_equal_mean(scored, "stress_return"),
                "leave_best_token_out_base_mean": _leave_best_token_out(scored),
            }
        )
        if len(set(blocks)) == 6:
            metrics["bootstrap_lower_bound"] = bootstrap_lower_bound(
                scored,
                phase=phase,
                candidate_id=candidate_id,
            )
    gates = PHASE_GATES[phase]
    checks = {
        "decision_availability": metrics["decision_availability"] >= gates["decision_availability"],
        "scored_longs": len(scored) >= gates["scored_longs"],
        "physical_tokens": metrics["physical_tokens"] >= gates["physical_tokens"],
        "weeks": metrics["weeks"] >= gates["weeks"],
        "signal_fill_rate": metrics["signal_fill_rate"] >= gates["fill_rate"],
        "represented_blocks": metrics["represented_blocks"] >= gates["represented_blocks"],
        "positive_blocks": metrics["positive_blocks"] >= gates["positive_blocks"],
        "token_equal_base_mean": metrics["token_equal_base_mean"] is not None and metrics["token_equal_base_mean"] > 0,
        "event_base_median": metrics["event_base_median"] is not None and metrics["event_base_median"] > 0,
        "token_equal_stress_mean": metrics["token_equal_stress_mean"] is not None and metrics["token_equal_stress_mean"] > 0,
        "leave_best_token_out_base_mean": metrics["leave_best_token_out_base_mean"] is not None and metrics["leave_best_token_out_base_mean"] > 0,
        "max_token_share": metrics["max_token_share"] <= gates["max_token_share"],
        "max_chain_share": metrics["max_chain_share"] <= gates["max_chain_share"],
    }
    if phase == "discovery":
        checks["max_block_share"] = (
            metrics["max_block_share"] <= gates["max_block_share"]
        )
    else:
        checks["max_week_share"] = (
            metrics["max_week_share"] <= gates["max_week_share"]
        )
    if phase == "validation":
        checks["bootstrap_lower_bound"] = (
            metrics["bootstrap_lower_bound"] is not None
            and metrics["bootstrap_lower_bound"] > 0
        )
    return {**metrics, "checks": checks, "passed": all(checks.values())}


def discovery_rank_key(score: Mapping[str, Any]) -> tuple[Any, ...]:
    def number(name: str) -> float:
        value = score.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else -math.inf

    return (
        number("token_equal_stress_mean"),
        number("token_equal_base_mean"),
        number("event_base_median"),
        number("bootstrap_lower_bound"),
        int(score.get("positive_blocks", 0)),
        -number("max_token_share"),
        -number("max_block_share"),
        -number("max_chain_share"),
        int(score.get("scored_longs", 0)),
    )


def phase_analysis(
    *,
    phase: str,
    cycle_statuses: Mapping[int, str],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive support and every registered score from one immutable record set."""

    materialized = _validated_phase_records(phase, records)
    support = phase_support(
        phase=phase,
        cycle_statuses=cycle_statuses,
        records=materialized,
    )
    scores = [
        candidate_score(phase=phase, candidate_id=candidate_id, records=materialized)
        for candidate_id in PARENT_NONCASH_CANDIDATE_IDS
    ]
    digest = _records_sha256(materialized)
    if any(score.get("records_sha256") != digest for score in scores):
        raise AssertionError("candidate scores do not bind one phase record set")
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "phase": phase,
        "records_sha256": digest,
        "candidate_ids": list(PARENT_NONCASH_CANDIDATE_IDS),
        "program_support": support,
        "scores": scores,
    }


def _validated_analysis(
    analysis: Mapping[str, Any], *, phase: str
) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]]]:
    if (
        not isinstance(analysis, Mapping)
        or analysis.get("schema_version") != 1
        or analysis.get("program_id") != PROGRAM_ID
        or analysis.get("phase") != phase
        or not isinstance(analysis.get("records_sha256"), str)
        or analysis.get("candidate_ids") != list(PARENT_NONCASH_CANDIDATE_IDS)
        or not isinstance(analysis.get("program_support"), Mapping)
        or not isinstance(analysis.get("scores"), list)
    ):
        raise B2DesignError("phase analysis differs from the sealed registry")
    by_id: dict[str, Mapping[str, Any]] = {}
    for score in analysis["scores"]:
        if not isinstance(score, Mapping):
            raise B2DesignError("phase analysis score is malformed")
        candidate_id = score.get("candidate_id")
        if (
            candidate_id not in PARENT_NONCASH_CANDIDATE_IDS
            or candidate_id in by_id
            or score.get("program_id") != PROGRAM_ID
            or score.get("phase") != phase
            or score.get("records_sha256") != analysis["records_sha256"]
        ):
            raise B2DesignError("phase analysis score provenance differs")
        by_id[candidate_id] = score
    if tuple(by_id) != PARENT_NONCASH_CANDIDATE_IDS:
        raise B2DesignError("phase analysis score order or candidate set differs")
    return analysis["program_support"], by_id


def freeze_discovery_family(
    *,
    cycle_statuses: Mapping[int, str],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    analysis = phase_analysis(
        phase="discovery", cycle_statuses=cycle_statuses, records=records
    )
    support, by_id = _validated_analysis(analysis, phase="discovery")
    if support.get("passed") is not True:
        return {
            "schema_version": 1,
            "program_id": PROGRAM_ID,
            "stage": "unscorable",
            "terminal_reason": "insufficient_discovery_program_support",
            "discovery_records_sha256": analysis["records_sha256"],
            "anchor_id": APRIORI_ANCHOR_ID,
            "challenger_id": None,
            "validation_family_ids": [],
        }
    challengers = [
        by_id[candidate_id]
        for candidate_id in PARENT_NONCASH_CANDIDATE_IDS
        if candidate_id != APRIORI_ANCHOR_ID
        and by_id[candidate_id].get("passed") is True
    ]
    ordered = sorted(challengers, key=lambda score: score["candidate_id"])
    ordered.sort(key=discovery_rank_key, reverse=True)
    challenger_id = ordered[0]["candidate_id"] if ordered else None
    family = [APRIORI_ANCHOR_ID]
    if challenger_id is not None:
        family.append(challenger_id)
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "stage": "validation_family_frozen",
        "terminal_reason": None,
        "discovery_records_sha256": analysis["records_sha256"],
        "anchor_id": APRIORI_ANCHOR_ID,
        "challenger_id": challenger_id,
        "eligible_challenger_count": len(challengers),
        "validation_family_ids": family,
    }


def validation_result(
    *,
    discovery_cycle_statuses: Mapping[int, str],
    discovery_records: Sequence[Mapping[str, Any]],
    validation_cycle_statuses: Mapping[int, str],
    validation_records: Sequence[Mapping[str, Any]],
    family_seal: Mapping[str, Any],
) -> dict[str, Any]:
    expected_family = freeze_discovery_family(
        cycle_statuses=discovery_cycle_statuses,
        records=discovery_records,
    )
    if dict(family_seal) != expected_family:
        raise B2DesignError("validation family seal does not replay from discovery evidence")
    analysis = phase_analysis(
        phase="validation",
        cycle_statuses=validation_cycle_statuses,
        records=validation_records,
    )
    support, by_id = _validated_analysis(analysis, phase="validation")
    family = family_seal.get("validation_family_ids")
    if (
        family_seal.get("schema_version") != 1
        or family_seal.get("program_id") != PROGRAM_ID
        or family_seal.get("stage") != "validation_family_frozen"
        or family_seal.get("anchor_id") != APRIORI_ANCHOR_ID
        or not isinstance(family, list)
        or not 1 <= len(family) <= 2
        or family[0] != APRIORI_ANCHOR_ID
        or len(set(family)) != len(family)
        or any(candidate_id not in PARENT_NONCASH_CANDIDATE_IDS for candidate_id in family)
    ):
        raise B2DesignError("validation family seal differs from the frozen contract")
    if support.get("passed") is not True:
        return {
            "schema_version": 1,
            "program_id": PROGRAM_ID,
            "stage": "unscorable",
            "terminal_reason": "insufficient_validation_program_support",
            "validation_records_sha256": analysis["records_sha256"],
            "formal_family_ids": list(family),
            "validated_candidate_ids": [],
            "advance_candidate_id": None,
        }
    passing = [by_id[candidate_id] for candidate_id in family if by_id[candidate_id].get("passed") is True]
    passing.sort(key=lambda score: score["candidate_id"])
    passing.sort(
        key=lambda score: (
            float(score["token_equal_stress_mean"]),
            float(score["token_equal_base_mean"]),
            float(score["bootstrap_lower_bound"]),
            float(score["event_base_median"]),
            -float(score["max_token_share"]),
            -float(score["max_week_share"]),
            -float(score["max_chain_share"]),
            int(score["scored_longs"]),
        ),
        reverse=True,
    )
    winner = passing[0]["candidate_id"] if passing else None
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "stage": "completed",
        "terminal_reason": None,
        "outcome": "validated_rule" if winner is not None else "no_rule",
        "validation_records_sha256": analysis["records_sha256"],
        "formal_family_ids": list(family),
        "validated_candidate_ids": [score["candidate_id"] for score in passing],
        "advance_candidate_id": winner,
    }
