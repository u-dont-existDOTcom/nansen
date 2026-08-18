from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from programs.nansen_theory_portfolio import runner as original_runner
from programs.nansen_theory_portfolio_a2 import runtime
from programs.nansen_theory_portfolio_a2.runtime import (
    A2_ANCHORS,
    A2_SLOTS,
    BLOCKED_IDENTITY,
    BLOCKED_REASON,
    PREDECESSOR_HASHES,
    PROGRAM_A2_ALLOCATION,
    PROGRAM_A2_ALLOWED_INITIAL_REMAINING,
    PROGRAM_A2_MAX_CALLS,
    PROGRAM_A2_MAX_CREDITS,
    PROGRAM_A2_REQUIRED_INITIAL_REMAINING,
    PROGRAM_A2_RELATIVE_ROOT,
    _epoch_limits,
    _select_anchor_events,
    _source_paths,
    _verify_predecessor,
    check_program_a2,
    initialize_program_a2,
)


REPOSITORY = Path(__file__).resolve().parents[1]
A1_MANIFEST = (
    REPOSITORY
    / "research/experiments/2026-08-18-historical-theory-discovery-a-v1/program.json"
)


def _load_a1_lifecycle_helpers() -> Any:
    path = REPOSITORY / "tests/test_nansen_theory_portfolio_lifecycle.py"
    spec = importlib.util.spec_from_file_location("_a2_a1_lifecycle_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A1_HELPERS = _load_a1_lifecycle_helpers()
ExactPricingClient = A1_HELPERS.ExactPricingClient


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_size >= 1_000_000:
        try:
            os.link(source, destination)
            return
        except OSError:
            pass
    shutil.copy2(source, destination)


def _copy_source_tree(destination: Path) -> None:
    for source in _source_paths(REPOSITORY):
        _copy_file(source, destination / source.relative_to(REPOSITORY))
    # The independent predecessor replay checks its complete immutable claim
    # chain. Only the six A2-admissible identity artifacts enter A2's runtime
    # manifest; the remaining files exist here solely for that replay proof.
    predecessor_source = REPOSITORY / runtime.PREDECESSOR_ROOT
    predecessor_destination = destination / runtime.PREDECESSOR_ROOT
    for source in predecessor_source.glob("**/*"):
        if source.is_file() and not source.is_symlink():
            _copy_file(source, predecessor_destination / source.relative_to(predecessor_source))


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture(scope="module")
def initialized_repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repository = tmp_path_factory.mktemp("program-a2-initialized") / "repository"
    repository.mkdir()
    _copy_source_tree(repository)
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Program A2 lifecycle test")
    _git(repository, "config", "user.email", "program-a2@example.invalid")
    manifest = initialize_program_a2(repository)
    assert manifest == repository / PROGRAM_A2_RELATIVE_ROOT / "program.json"
    _git(repository, "add", "-A")
    _git(repository, "commit", "--quiet", "-m", "freeze synthetic Program A2")
    return repository


@pytest.fixture
def prepared_program(
    initialized_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    shutil.copytree(
        initialized_repository,
        repository,
        copy_function=A1_HELPERS._copy_or_link_large,
    )
    monkeypatch.setattr(runtime.runner_v1.time_module, "sleep", lambda _: None)
    manifest = repository / PROGRAM_A2_RELATIVE_ROOT / "program.json"
    return manifest, manifest.parent


def _client(program_root: Path, **kwargs: Any) -> Any:
    return ExactPricingClient(
        (program_root / "contracts/nansen-openapi.json").read_bytes(),
        **kwargs,
    )


def test_import_is_isolated_and_terminal_a1_remains_checkable() -> None:
    assert runtime.runner_v1 is not original_runner
    assert original_runner.PROGRAM_A_ID == (
        "2026-08-18-historical-theory-discovery-a-v1"
    )
    assert original_runner.check_program_a(A1_MANIFEST) == {
        "stage": "unscorable",
        "terminal_anchors": 5,
        "authenticated_attempts": 135,
        "billable_credits": 537,
    }


def test_schedule_budget_blocks_and_source_boundary_are_exact() -> None:
    limits = [_epoch_limits(index) for index in range(len(A2_ANCHORS))]
    assert len(A2_ANCHORS) == 59
    assert len(A2_SLOTS) == 358
    assert sum(slot.execution_calibration for slot in A2_SLOTS) == 59
    assert sum(item[0] for item in limits) == PROGRAM_A2_MAX_CALLS == 1_668
    assert sum(item[1] for item in limits) == PROGRAM_A2_MAX_CREDITS == 6_613
    assert PROGRAM_A2_ALLOCATION == 7_463
    assert PROGRAM_A2_REQUIRED_INITIAL_REMAINING == 49_526
    assert PROGRAM_A2_ALLOWED_INITIAL_REMAINING == {49_526, 49_531}
    assert A2_ANCHORS[0].isoformat() == "2025-06-29"
    assert A2_ANCHORS[-1].isoformat() == "2026-08-09"
    assert A2_SLOTS[0].event_id == "a07-s01-base-upper_tail"
    assert A2_SLOTS[-1].event_id == "a65-s06-bnb-upper_tail"
    assert Counter(slot.chain for slot in A2_SLOTS) == {
        "base": 90,
        "bnb": 90,
        "ethereum": 89,
        "solana": 89,
    }
    assert Counter(len([s for s in A2_SLOTS if s.anchor_index == i]) for i in range(59)) == {
        7: 4,
        6: 55,
    }
    paths = _source_paths(REPOSITORY)
    assert len(paths) == len({path.resolve() for path in paths})
    assert all((REPOSITORY / relative) in paths for relative in PREDECESSOR_HASHES)


def test_exact_blocked_identity_is_postselection_unavailable_with_provenance() -> None:
    slot = next(
        item
        for item in A2_SLOTS
        if item.chain == "solana" and item.stratum == "near_zero"
    )
    rows = [
        {
            "anchor": slot.anchor.isoformat(),
            "chain": "solana",
            "chain_provenance": {
                "raw": "solana",
                "normalized": "solana",
                "normalization": "identity",
            },
            "token_address": BLOCKED_IDENTITY[1],
            "token_symbol": "USDC",
            "price_usd": 1.0,
            "price_change": 0.0,
            "market_cap_usd": 1e9,
            "liquidity_usd": 1e8,
            "volume_usd": 1.0,
            "netflow_usd": 0.0,
            "netflow_to_market_cap": 0.0,
            "token_age_days": 100,
        },
        {
            "anchor": slot.anchor.isoformat(),
            "chain": "solana",
            "chain_provenance": {
                "raw": "solana",
                "normalized": "solana",
                "normalization": "identity",
            },
            "token_address": "replacement",
            "token_symbol": "OTHER",
            "price_usd": 1.0,
            "price_change": 0.0,
            "market_cap_usd": 1e6,
            "liquidity_usd": 250_000.0,
            "volume_usd": 1.0,
            "netflow_usd": 1.0,
            "netflow_to_market_cap": 1e-6,
            "token_age_days": 100,
        },
    ]
    event = _select_anchor_events([slot], rows)[0]
    assert event["status"] == "unavailable"
    assert event["reason"] == BLOCKED_REASON
    assert event["token_address"] == BLOCKED_IDENTITY[1]
    assert event["chain_provenance"]["normalization"] == "identity"
    assert event["token_address"] != "replacement"


def test_initialize_is_exact_committed_idempotent_and_parent_bound(
    initialized_repository: Path,
) -> None:
    manifest = initialized_repository / PROGRAM_A2_RELATIVE_ROOT / "program.json"
    before = manifest.read_bytes()
    assert initialize_program_a2(initialized_repository) == manifest
    assert manifest.read_bytes() == before
    runtime.runner_v1._assert_preregistration_committed(manifest)
    validated = runtime.runner_v1.validate_program_a(manifest)
    assert validated["stage"] == "preregistered"
    assert validated["max_authenticated_attempts"] == 1_668
    assert validated["max_billable_credits"] == 6_613
    assert validated["portfolio_id"] == runtime.PROGRAM_A2_PORTFOLIO_ID


@pytest.mark.parametrize("relative", list(PREDECESSOR_HASHES))
def test_predecessor_tamper_blocks_before_initialization(
    initialized_repository: Path,
    tmp_path: Path,
    relative: Path,
) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(initialized_repository, repository)
    target = repository / relative
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(runtime.runner_v1.PortfolioError, match="predecessor evidence"):
        _verify_predecessor(repository)


@pytest.mark.parametrize("remaining", [49_526, 49_531])
def test_allowed_initial_balance_completes_exact_anchor(
    prepared_program: tuple[Path, Path], remaining: int
) -> None:
    _, program_root = prepared_program
    client = _client(program_root, remaining=remaining)
    runtime.runner_v1._run_anchor(program_root, 0, client)
    assert runtime.runner_v1._terminal_anchor_status(program_root, 0) == "complete"
    assert client.fetch_calls == 1
    assert len(client.requests) == _epoch_limits(0)[0] == 32
    assert remaining - client.remaining == _epoch_limits(0)[1] == 127


@pytest.mark.parametrize("remaining", [49_525, 49_532])
def test_any_other_initial_balance_is_global_fatal_before_screener(
    prepared_program: tuple[Path, Path], remaining: int
) -> None:
    _, program_root = prepared_program
    client = _client(program_root, remaining=remaining)
    with pytest.raises(
        runtime.runner_v1.PortfolioError, match="account|predecessor|balance"
    ):
        runtime.runner_v1._run_anchor(program_root, 0, client)
    assert client.fetch_calls == 1
    assert [request[1] for request in client.requests] == [runtime.runner_v1.ACCOUNT_ENDPOINT]


@pytest.mark.parametrize("delta", [-1, 1])
def test_later_account_requires_exact_first_baseline_minus_sealed_spend(
    prepared_program: tuple[Path, Path], delta: int
) -> None:
    _, program_root = prepared_program
    first = _client(program_root, remaining=49_526)
    runtime.runner_v1._run_anchor(program_root, 0, first)
    second = _client(program_root, remaining=first.remaining + delta)
    with pytest.raises(runtime.runner_v1.PortfolioError, match="balance|minimum"):
        runtime.runner_v1._run_anchor(program_root, 1, second)
    assert second.fetch_calls == 1
    assert [request[1] for request in second.requests] == [
        runtime.runner_v1.ACCOUNT_ENDPOINT
    ]


def test_complete_resume_finalize_check_and_ranking_replay(
    prepared_program: tuple[Path, Path],
) -> None:
    manifest, program_root = prepared_program
    first = _client(program_root, remaining=49_526)
    runtime.runner_v1._run_anchor(program_root, 0, first)
    assert runtime.runner_v1._terminal_anchor_status(program_root, 0) == "complete"

    resume = _client(program_root, remaining=first.remaining)
    runtime.runner_v1._run_anchor(program_root, 0, resume)
    assert resume.fetch_calls == 0
    assert resume.requests == []

    invalid = _client(
        program_root,
        remaining=first.remaining,
        failure_mode="invalid_screener",
    )
    for anchor_index in range(1, len(A2_ANCHORS)):
        runtime.runner_v1._run_anchor(program_root, anchor_index, invalid)
        assert (
            runtime.runner_v1._terminal_anchor_status(program_root, anchor_index)
            == "unscorable"
        )

    records = runtime.runner_v1._final_records(program_root)
    assert len(records) == len(A2_SLOTS) == 358
    finalized = runtime.runner_v1._finalize(program_root)
    assert finalized["stage"] == "unscorable"
    assert finalized["terminal_reason"] == "insufficient_program_a2_support"
    assert check_program_a2(manifest) == {
        "stage": "unscorable",
        "terminal_anchors": 59,
        "authenticated_attempts": 148,
        "billable_credits": 417,
    }
    ranking = json.loads((program_root / "derived/candidate-ranking.json").read_text())
    calibration = json.loads(
        (program_root / "derived/execution-calibration.json").read_text()
    )
    assert ranking["planned_opportunities"] == 358
    assert ranking["selected_opportunities"] == 7
    assert ranking["program_support"] == {
        "complete_anchor_gate": False,
        "selected_opportunity_gate": False,
        "selected_proxy_coverage_gate": True,
        "complete_anchors": 1,
        "selected_opportunities": 7,
        "proxy_outcomes_available": 7,
        "selected_proxy_coverage": 1.0,
        "passed": False,
    }
    assert ranking["program_b_candidate_ids"] == []
    assert ranking["discovery_shortlist_candidate_ids"]
    assert ranking["b2_eligibility"] == {
        "eligible": False,
        "required_stage": "completed",
        "candidate_ids": [],
    }
    assert ranking["selection_missingness"] == {
        "total": 351,
        "by_reason": {"anchor_unscorable": 351},
        "by_chain": {"base": 88, "bnb": 88, "ethereum": 87, "solana": 88},
        "by_stratum": {
            "lower_tail": 88,
            "near_zero": 88,
            "upper_middle": 89,
            "upper_tail": 86,
        },
    }
    assert calibration["overall"]["planned"] == 59
    final = json.loads((program_root / "seals/final.json").read_text())
    assert (program_root / "REPORT.md").read_bytes() == runtime._completion_report(
        final, ranking, calibration
    )
    assert runtime.runner_v1._finalize(program_root) == finalized


def test_candidate_stability_uses_five_frozen_successor_blocks() -> None:
    records = []
    for index, anchor in enumerate(A2_ANCHORS):
        event = {
            "event_id": f"synthetic-{index}",
            "anchor": anchor.isoformat(),
            "chain": "base",
            "token_address": f"token-{index}",
            "status": "selected",
            "netflow_to_market_cap": 0.01,
            "price_change": 0.05,
        }
        records.append((event, {}, {"available": True, "base_return": 0.01, "stress_return": 0.005}))
    ranking = runtime._score_candidates(records)
    c11 = next(
        score
        for score in ranking["scores"]
        if score["candidate_id"] == "c11-screener-accumulation-benchmark"
    )
    assert c11["represented_calendar_blocks"] == 5
    assert c11["positive_calendar_blocks"] == 5


@pytest.mark.parametrize("failure_mode", ["charged_http", "charged_nonobject"])
def test_future_provider_failure_is_global_and_never_reaches_anchor_two(
    prepared_program: tuple[Path, Path], failure_mode: str
) -> None:
    _, program_root = prepared_program
    client = _client(program_root, remaining=49_526, failure_mode=failure_mode)
    with pytest.raises(runtime.runner_v1.PortfolioError):
        for anchor_index in range(2):
            runtime.runner_v1._run_anchor(program_root, anchor_index, client)
    assert client.fetch_calls == 1
    assert all("anchor-02" not in request[3] for request in client.requests)


def test_public_fatal_run_seals_a2_report_and_never_constructs_client_again(
    prepared_program: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, program_root = prepared_program
    client = _client(program_root, remaining=49_526, failure_mode="charged_http")
    monkeypatch.setattr(runtime.runner_v1, "NansenClient", lambda **_: client)
    monkeypatch.setattr(runtime.runner_v1, "_cohort_automation_inactive", lambda: None)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    terminal = runtime.run_program_a2(manifest, api_key="offline")
    assert terminal["stage"] == "unscorable"
    report = (program_root / "REPORT.md").read_text()
    assert report.startswith("# Historical theory discovery A2\n")
    assert "there is no A3" in report
    assert check_program_a2(manifest)["stage"] == "unscorable"

    def forbidden(**_: Any) -> Any:
        raise AssertionError("terminal A2 attempted to construct another client")

    monkeypatch.setattr(runtime.runner_v1, "NansenClient", forbidden)
    assert runtime.run_program_a2(manifest, api_key="offline") == terminal


@pytest.mark.parametrize("tamper", ["response", "anchor_seal"])
def test_a2_anchor_tamper_blocks_check_and_resume_before_provider(
    prepared_program: tuple[Path, Path], tamper: str
) -> None:
    manifest, program_root = prepared_program
    first = _client(program_root, remaining=49_526)
    runtime.runner_v1._run_anchor(program_root, 0, first)
    anchor = runtime.runner_v1._anchor_root(program_root, 0)
    target = (
        next((anchor / "raw/nansen").glob("*/attempt-1-response.json"))
        if tamper == "response"
        else anchor / "seals/terminal.json"
    )
    target.write_bytes(target.read_bytes() + b"not-json")
    with pytest.raises(RuntimeError):
        check_program_a2(manifest)
    resumed = _client(program_root, remaining=first.remaining)
    with pytest.raises(RuntimeError):
        runtime.runner_v1._run_anchor(program_root, 0, resumed)
    assert resumed.fetch_calls == 0
    assert resumed.requests == []


def test_a2_final_seal_tamper_is_rejected(
    prepared_program: tuple[Path, Path]
) -> None:
    manifest, program_root = prepared_program
    terminal = runtime._terminalize_program(program_root, "synthetic offline fatal")
    assert terminal["stage"] == "unscorable"
    seal = program_root / "seals/final.json"
    seal.write_bytes(seal.read_bytes() + b"not-json")
    with pytest.raises(RuntimeError):
        check_program_a2(manifest)


def test_transport_boundary_refuses_public_and_authenticated_calls_at_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AtCutoff:
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return runtime.runner_v1.REQUEST_START_CUTOFF

    class Underlying:
        def __init__(self) -> None:
            self.fetches = 0
            self.requests = 0

        def fetch_openapi(self) -> bytes:
            self.fetches += 1
            return b"{}"

        def request_evidence(self, *args: Any, **kwargs: Any) -> None:
            self.requests += 1

    monkeypatch.setattr(runtime.runner_v1, "datetime", AtCutoff)
    underlying = Underlying()
    guarded = runtime._DeadlineClient(underlying)
    with pytest.raises(runtime.runner_v1.PortfolioError, match="cutoff"):
        guarded.fetch_openapi()
    with pytest.raises(runtime.runner_v1.PortfolioError, match="cutoff"):
        guarded.request_evidence("GET", "account", None, caller_request_id="x")
    assert underlying.fetches == 0
    assert underlying.requests == 0


def test_provider_lock_is_shared_and_nonblocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with runtime.runner_v1._provider_lock():
        with pytest.raises(runtime.runner_v1.PortfolioError, match="provider job"):
            with runtime.runner_v1._provider_lock():
                pass


def test_systemd_unit_preserves_cohort_priority_cutoff_and_shared_lock() -> None:
    unit_path = REPOSITORY / "operations/nansen-signal-lab-program-a2.service"
    unit = unit_path.read_text()
    assert "Conflicts=nansen-signal-lab-cohort.timer" in unit
    assert "Conflicts=nansen-signal-lab-cohort.service" not in unit
    assert "--no-block start nansen-signal-lab-cohort.timer" in unit
    assert "RuntimeDirectory=nansen-signal-lab-provider" in unit
    assert "10:43:30Z" in unit
    assert "historical-theory-discovery-a2-v1/program.json" in unit
    subprocess.run(
        ["systemd-analyze", "verify", str(unit_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
