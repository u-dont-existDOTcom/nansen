from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.nansen_signal_lab.cohort_schema import (
    CONTRACT_SOURCE_PATH,
    CYCLE_COUNT,
    CohortSchemaError,
    budget_plan,
    build_schedule,
    initialize_cohort_program,
    load_cohort_program,
    remaining_required_credits,
    STRATEGY_SOURCE_PATH,
)


def _repo(tmp_path: Path) -> Path:
    source_repo = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    design = repo / "docs/superpowers/specs/2026-08-18-prospective-multi-cycle-cohort-v1.md"
    design.parent.mkdir(parents=True)
    design.write_bytes(
        (source_repo / "docs/superpowers/specs/2026-08-18-prospective-multi-cycle-cohort-v1.md").read_bytes()
    )
    contract = repo / CONTRACT_SOURCE_PATH
    contract.parent.mkdir(parents=True)
    contract.write_bytes((source_repo / CONTRACT_SOURCE_PATH).read_bytes())
    strategy = repo / STRATEGY_SOURCE_PATH
    strategy.parent.mkdir(parents=True, exist_ok=True)
    strategy.write_bytes((source_repo / STRATEGY_SOURCE_PATH).read_bytes())
    return repo


def test_schedule_and_budget_are_exact():
    first = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)
    schedule = build_schedule(first)
    assert len(schedule) == 32
    assert schedule[0]["scheduled_at"] == "2026-08-24T12:05:00Z"
    assert schedule[-1]["scheduled_at"] == "2026-10-20T08:05:00Z"
    assert budget_plan()["maximum_program_credits"] == 1792
    assert budget_plan()["maximum_program_authenticated_attempts"] == 1824


@pytest.mark.parametrize(
    "first",
    (
        datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 12, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 12, 5),
    ),
)
def test_schedule_rejects_noncanonical_start(first):
    with pytest.raises(CohortSchemaError):
        build_schedule(first)


def test_init_and_load_bind_design_contract_schedule_and_funding(tmp_path):
    repo = _repo(tmp_path)
    root = repo / "research/experiments/program-fixture"
    created = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    first = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)
    program = initialize_cohort_program(
        root, created_at=created, first_cycle_at=first, repo_root=repo
    )

    assert program.manifest["stage"] == "preregistered"
    assert program.manifest["budget"]["maximum_program_credits"] == 1792
    assert remaining_required_credits(program, 1) == 1792
    assert remaining_required_credits(program, 32) == 56
    assert load_cohort_program(root / "program.json", repo_root=repo).manifest == program.manifest


def test_load_rejects_manifest_contract_and_schedule_tampering(tmp_path):
    repo = _repo(tmp_path)
    root = repo / "research/experiments/program-fixture"
    initialize_cohort_program(
        root,
        created_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        first_cycle_at=datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc),
        repo_root=repo,
    )
    document = json.loads((root / "program.json").read_text())
    document["schedule"][2]["scheduled_at"] = "2026-08-30T00:05:00Z"
    (root / "program.json").write_text(json.dumps(document))
    with pytest.raises(CohortSchemaError, match="schedule"):
        load_cohort_program(root / "program.json", repo_root=repo)


def test_init_is_offline_and_refuses_existing_or_outside_roots(tmp_path):
    repo = _repo(tmp_path)
    kwargs = {
        "created_at": datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        "first_cycle_at": datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc),
        "repo_root": repo,
    }
    root = repo / "research/experiments/program-fixture"
    initialize_cohort_program(root, **kwargs)
    with pytest.raises(FileExistsError):
        initialize_cohort_program(root, **kwargs)
    with pytest.raises(CohortSchemaError, match="research/experiments"):
        initialize_cohort_program(tmp_path / "outside", **kwargs)
