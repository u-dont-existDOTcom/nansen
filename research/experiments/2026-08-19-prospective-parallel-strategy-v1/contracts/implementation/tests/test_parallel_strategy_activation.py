from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import programs.nansen_parallel_strategy_v1.activation as activation
from programs.nansen_parallel_strategy_v1.budget import (
    reconstruct_operational_balances,
)


REPO = Path(__file__).resolve().parents[1]


def _program(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "parallel-program"
    root.mkdir()
    return SimpleNamespace(repo_root=REPO, root=root)


def _v1_attestation(credits: int = 321) -> dict:
    return {
        "source_program_id": "2026-08-18-prospective-multi-cycle-cohort-v1",
        "operational_replay_sha256": "f" * 64,
        "billable_credits": credits,
        "cycles": [
            {"cycle_index": 1, "billable_credits": 1},
        ],
    }


def test_activation_reconciliation_is_exact_operational_only_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program = _program(tmp_path)
    monkeypatch.setattr(
        activation, "require_terminal_v1_activation", lambda _: program
    )
    monkeypatch.setattr(
        activation, "validate_terminal_v1_attestation", lambda _: _v1_attestation()
    )

    document = activation.operational_reconciliation(tmp_path / "program.json")
    assert document["opening_balance_candidates"] == [50_063]
    assert reconstruct_operational_balances(document) == (
        50_063 - 532 - 4_828 - (321 - 1) - 1,
        50_063 - 532 - 4_828 - (321 - 1),
    )
    assert [item["confirmed_spend_credits"] for item in document["operational_ledgers"]] == [
        532,
        4_828,
        320,
    ]
    assert [item["reserved_spend_candidates"] for item in document["operational_ledgers"]] == [
        [0],
        [0, 1],
        [0],
    ]
    encoded = json.dumps(document, sort_keys=True)
    for forbidden in ("token_address", "decision", "outcome", "ranking", "return"):
        assert forbidden not in encoded

    first = activation.seal_operational_reconciliation(tmp_path / "program.json")
    assert activation.seal_operational_reconciliation(
        tmp_path / "program.json"
    ).read_bytes() == first.read_bytes()


def test_activation_rejects_predecessor_seal_drift_before_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    program = _program(tmp_path)
    monkeypatch.setattr(
        activation, "require_terminal_v1_activation", lambda _: program
    )
    monkeypatch.setattr(
        activation, "validate_terminal_v1_attestation", lambda _: _v1_attestation()
    )
    monkeypatch.setattr(activation, "_sha256", lambda _: "0" * 64)

    with pytest.raises(
        activation.ParallelStrategyActivationError,
        match="operational predecessor differs",
    ):
        activation.operational_reconciliation(tmp_path / "program.json")


@pytest.mark.parametrize(
    ("v1_total_credits", "accepted"),
    [(1_758, True), (1_759, False)],
)
def test_activation_enforces_exact_frozen_future_authority_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    v1_total_credits: int,
    accepted: bool,
):
    program = _program(tmp_path)
    monkeypatch.setattr(
        activation, "require_terminal_v1_activation", lambda _: program
    )
    monkeypatch.setattr(
        activation,
        "validate_terminal_v1_attestation",
        lambda _: _v1_attestation(credits=v1_total_credits),
    )

    if accepted:
        document = activation.operational_reconciliation(tmp_path / "program.json")
        assert reconstruct_operational_balances(document)[0] == 42_945
    else:
        with pytest.raises(
            activation.ParallelStrategyActivationError,
            match="cannot fund",
        ):
            activation.operational_reconciliation(tmp_path / "program.json")
