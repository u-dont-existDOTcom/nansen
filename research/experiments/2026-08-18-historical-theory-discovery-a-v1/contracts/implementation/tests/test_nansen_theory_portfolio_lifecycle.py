from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from programs.nansen_theory_portfolio import runner
from programs.nansen_theory_portfolio.budget import HistoricalPricingGuard
from programs.nansen_theory_portfolio.design import (
    ACCOUNT_ENDPOINT,
    ANCHORS,
    CHAINS,
    DEX_ENDPOINT,
    FLOW_ENDPOINT,
    FLOW_FIELDS,
    FULL_OPENAPI_SOURCE,
    OHLCV_ENDPOINT,
    PLANNED_SLOTS,
    PROGRAM_A_ID,
    PROVED_BALANCE,
    SCREENER_ENDPOINT,
    WBS_ENDPOINT,
)
from programs.nansen_theory_portfolio.runner import (
    PROGRAM_RELATIVE_ROOT,
    PortfolioError,
    _anchor_root,
    _completion_documents,
    _epoch_limits,
    _final_records,
    _finalize,
    _run_anchor,
    _terminal_anchor_status,
    _terminalize_anchor,
    _terminalize_program,
    check_program_a,
    initialize_program_a,
    run_program_a,
)
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure


SOURCE_REPOSITORY = Path(__file__).resolve().parents[1]


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_synthetic_source_tree(destination: Path) -> None:
    openapi_source = SOURCE_REPOSITORY / FULL_OPENAPI_SOURCE
    for source in runner._source_paths(SOURCE_REPOSITORY):
        if source == openapi_source:
            continue
        _copy_file(source, destination / source.relative_to(SOURCE_REPOSITORY))
    # Keep lifecycle tests in the synthetic commit even if the runtime source
    # freeze is tightened to include its independent verification surface.
    for relative in (
        Path("tests/test_nansen_theory_portfolio.py"),
        Path("tests/test_nansen_theory_portfolio_lifecycle.py"),
    ):
        source = SOURCE_REPOSITORY / relative
        if source.is_file():
            _copy_file(source, destination / relative)

    openapi_destination = destination / FULL_OPENAPI_SOURCE
    openapi_destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(openapi_source, openapi_destination)
    except OSError:
        # CI commonly places pytest's base directory on another filesystem.
        shutil.copy2(openapi_source, openapi_destination)


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
    repository = tmp_path_factory.mktemp("program-a-initialized") / "repository"
    repository.mkdir()
    _copy_synthetic_source_tree(repository)
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Program A lifecycle test")
    _git(repository, "config", "user.email", "program-a-lifecycle@example.invalid")

    manifest_path = initialize_program_a(repository)
    assert manifest_path == repository / PROGRAM_RELATIVE_ROOT / "program.json"
    _git(repository, "add", "-A")
    _git(repository, "commit", "--quiet", "-m", "freeze synthetic Program A")
    return repository


def _copy_or_link_large(source: str, destination: str) -> str:
    source_path = Path(source)
    if source_path.stat().st_size >= 1_000_000:
        os.link(source, destination)
        return destination
    return shutil.copy2(source, destination)


@pytest.fixture
def prepared_program(
    initialized_repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    shutil.copytree(
        initialized_repository,
        repository,
        copy_function=_copy_or_link_large,
    )
    # Paid-call spacing is a production concern, not part of this offline test.
    monkeypatch.setattr(runner.time_module, "sleep", lambda _: None)
    manifest_path = repository / PROGRAM_RELATIVE_ROOT / "program.json"
    return manifest_path, manifest_path.parent


class ExactPricingClient:
    """Dynamic, network-free provider with exact per-endpoint pricing evidence."""

    def __init__(
        self,
        openapi: bytes,
        *,
        failure_mode: str | None = None,
        remaining: int = PROVED_BALANCE,
    ) -> None:
        self.openapi = openapi
        self.failure_mode = failure_mode
        self.remaining = remaining
        self.fetch_calls = 0
        self.requests: list[tuple[str, str, dict[str, Any] | None, str]] = []

    def fetch_openapi(self) -> bytes:
        self.fetch_calls += 1
        return b"{}" if self.failure_mode == "openapi" else self.openapi

    def request_evidence(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
        *,
        caller_request_id: str,
    ) -> NansenEvidenceResponse:
        self.requests.append((method, endpoint, payload, caller_request_id))
        if self.failure_mode == "ambiguous" and endpoint == SCREENER_ENDPOINT:
            raise NansenRequestFailure(
                "synthetic ambiguous transmission",
                transmitted=True,
                response=None,
            )

        if endpoint == ACCOUNT_ENDPOINT:
            if self.failure_mode == "account_exact_low":
                return self._response(
                    {"plan": "free", "credits_remaining": 49_000},
                    cost=0,
                    used=0,
                    remaining=49_000,
                    caller_request_id=caller_request_id,
                )
            if self.failure_mode == "account_mismatch":
                return self._response(
                    {"plan": "free", "credits_remaining": PROVED_BALANCE},
                    cost=0,
                    used=0,
                    remaining=PROVED_BALANCE - 1,
                    caller_request_id=caller_request_id,
                )
            if self.failure_mode == "account":
                return self._response(
                    {"plan": "free", "credits_remaining": PROVED_BALANCE - 1},
                    cost=0,
                    used=None,
                    remaining=None,
                    caller_request_id=caller_request_id,
                )
            return self._response(
                {"plan": "free", "credits_remaining": self.remaining},
                cost=0,
                used=0,
                remaining=self.remaining,
                caller_request_id=caller_request_id,
            )

        cost = 1 if endpoint == OHLCV_ENDPOINT else 5
        used = cost
        if self.failure_mode == "pricing" and endpoint == SCREENER_ENDPOINT:
            used = cost + 1
        self.remaining -= used
        if (
            self.failure_mode in {"charged_http", "charged_nonobject"}
            and endpoint == SCREENER_ENDPOINT
        ):
            nonobject = self.failure_mode == "charged_nonobject"
            response = self._response(
                ["synthetic charged provider failure"]
                if nonobject
                else {"error": "synthetic charged provider failure"},
                cost=cost,
                used=used,
                remaining=self.remaining,
                caller_request_id=caller_request_id,
                status_code=200 if nonobject else 500,
                body_parse_status="json_other" if nonobject else "json_object",
            )
            raise NansenRequestFailure(
                "synthetic charged provider failure",
                transmitted=True,
                response=response,
            )
        return self._response(
            self._body(endpoint, payload, caller_request_id),
            cost=cost,
            used=used,
            remaining=self.remaining,
            caller_request_id=caller_request_id,
        )

    @staticmethod
    def _screener_body() -> dict[str, Any]:
        rows = []
        netflows = (-4_000.0, -100.0, 1_000.0, 5_000.0)
        for chain_index, chain in enumerate(CHAINS):
            for row_index, netflow in enumerate(netflows):
                rows.append(
                    {
                        "chain": chain,
                        "token_address": f"token-{chain_index}-{row_index}",
                        "token_symbol": f"T{chain_index}{row_index}",
                        "price_usd": 1.0,
                        "price_change": 0.05,
                        "market_cap_usd": 2_000_000.0,
                        "liquidity": 500_000.0,
                        "volume": 100_000.0,
                        "netflow": netflow,
                        "token_age_days": 30,
                    }
                )
        rows.sort(key=lambda row: (-row["netflow"], row["chain"], row["token_address"]))
        return {
            "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
            "data": rows,
        }

    @staticmethod
    def _flow_body() -> dict[str, Any]:
        row: dict[str, Any] = {
            field: (2 if field.endswith("wallet_count") else 100.0)
            for field in FLOW_FIELDS
        }
        row["exchange_net_flow_usd"] = -100.0
        return {"data": [row], "warnings": []}

    @staticmethod
    def _wbs_body(payload: dict[str, Any]) -> dict[str, Any]:
        direction = payload["buy_or_sell"]
        if direction == "BUY":
            rows = [
                {
                    "address": "buyer-1",
                    "is_smart_money": True,
                    "bought_volume_usd": 70.0,
                    "bought_token_volume": 70.0,
                },
                {
                    "address": "buyer-2",
                    "is_smart_money": True,
                    "bought_volume_usd": 30.0,
                    "bought_token_volume": 30.0,
                },
            ]
        else:
            rows = [
                {
                    "address": "seller-1",
                    "is_smart_money": True,
                    "sold_volume_usd": 20.0,
                    "sold_token_volume": 20.0,
                }
            ]
        return {
            "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
            "data": rows,
        }

    @staticmethod
    def _ohlcv_body(payload: dict[str, Any]) -> dict[str, Any]:
        start = datetime.fromisoformat(payload["date"]["from"].replace("Z", "+00:00"))
        candles = []
        for index in range(52):
            price = 100.0 + index / 10
            candles.append(
                {
                    "interval_start": (
                        start + timedelta(minutes=5 * index)
                    ).isoformat().replace("+00:00", "Z"),
                    "open": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price,
                    "volume": 1.0,
                }
            )
        return {
            "chain": payload["chain"],
            "token_address": payload["token_address"],
            "timeframe": "5m",
            "truncated": False,
            "data": candles,
        }

    @staticmethod
    def _dex_body(
        payload: dict[str, Any], caller_request_id: str
    ) -> dict[str, Any]:
        direction = payload["filters"]["action"]
        price = 1.0 if direction == "BUY" else 1.1
        return {
            "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
            "data": [
                {
                    "action": direction,
                    "block_timestamp": payload["date_range"]["from"],
                    "transaction_hash": f"tx-{caller_request_id}",
                    "token_amount": 1_000.0,
                    "estimated_swap_price_usd": price,
                    "estimated_value_usd": 1_000.0 * price,
                }
            ],
        }

    def _body(
        self,
        endpoint: str,
        payload: dict[str, Any] | None,
        caller_request_id: str,
    ) -> dict[str, Any]:
        assert payload is not None
        if endpoint == SCREENER_ENDPOINT:
            body = self._screener_body()
            if self.failure_mode == "invalid_screener":
                body["data"][0]["price_change"] = 20.01
            return body
        if endpoint == FLOW_ENDPOINT:
            return self._flow_body()
        if endpoint == WBS_ENDPOINT:
            return self._wbs_body(payload)
        if endpoint == OHLCV_ENDPOINT:
            return self._ohlcv_body(payload)
        if endpoint == DEX_ENDPOINT:
            return self._dex_body(payload, caller_request_id)
        raise AssertionError(f"unexpected synthetic endpoint: {endpoint}")

    @staticmethod
    def _response(
        body: Any,
        *,
        cost: int | None,
        used: int | None,
        remaining: int | None,
        caller_request_id: str,
        status_code: int = 200,
        body_parse_status: str = "json_object",
    ) -> NansenEvidenceResponse:
        headers = {}
        for name, value in (
            ("X-Nansen-Credits-Cost", cost),
            ("X-Nansen-Credits-Used", used),
            ("X-Nansen-Credits-Remaining", remaining),
        ):
            if value is not None:
                headers[name] = str(value)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return NansenEvidenceResponse(
            body=body,
            body_parse_status=body_parse_status,
            raw_body=raw,
            status_code=status_code,
            request_started_at="2026-08-18T18:00:00Z",
            response_retrieved_at="2026-08-18T18:00:01Z",
            response_headers=headers,
            request_id=f"request-{caller_request_id}",
            credit_cost=cost,
            credit_used=used,
            credit_remaining=remaining,
            credit_header_errors=(),
        )


def _exact_client(program_root: Path, **kwargs: Any) -> ExactPricingClient:
    return ExactPricingClient(
        (program_root / "contracts/nansen-openapi.json").read_bytes(),
        **kwargs,
    )


def _complete_first_anchor(program_root: Path) -> ExactPricingClient:
    client = _exact_client(program_root)
    _run_anchor(program_root, 0, client)
    assert _terminal_anchor_status(program_root, 0) == "complete"
    assert client.fetch_calls == 1
    assert len(client.requests) == _epoch_limits(0)[0] == 32
    assert PROVED_BALANCE - client.remaining == _epoch_limits(0)[1] == 127
    return client


def _terminalize_remaining_anchors(program_root: Path, remaining: int) -> None:
    client = _exact_client(
        program_root,
        failure_mode="invalid_screener",
        remaining=remaining,
    )
    for anchor_index in range(1, len(ANCHORS)):
        _run_anchor(program_root, anchor_index, client)
        assert _terminal_anchor_status(program_root, anchor_index) == "unscorable"


def test_initialize_program_a_is_exact_committed_and_idempotent(
    initialized_repository: Path,
) -> None:
    manifest_path = initialized_repository / PROGRAM_RELATIVE_ROOT / "program.json"
    before = manifest_path.read_bytes()
    assert initialize_program_a(initialized_repository) == manifest_path
    assert manifest_path.read_bytes() == before
    manifest = runner.validate_program_a(manifest_path, validate_live_runtime=True)
    assert manifest["program_id"] == PROGRAM_A_ID
    assert manifest["stage"] == "preregistered"
    runner._assert_preregistration_committed(manifest_path)


@pytest.mark.parametrize("mutation", ["untracked", "staged_only", "dirty_test"])
def test_commit_gate_rejects_every_uncommitted_protocol_surface(
    prepared_program: tuple[Path, Path], mutation: str
) -> None:
    manifest_path, _ = prepared_program
    repository = runner._program_repo_root(manifest_path)
    if mutation == "untracked":
        target = repository / "tests/test_nansen_theory_portfolio_untracked.py"
        target.write_text("# untracked protocol test\n")
    elif mutation == "dirty_test":
        target = repository / "tests/test_nansen_theory_portfolio.py"
        target.write_bytes(target.read_bytes() + b"\n# dirty protocol test\n")
    else:
        relative = Path("scripts/nansen_theory_portfolio.py")
        target = repository / relative
        target.write_bytes(target.read_bytes() + b"\n# staged-only mutation\n")
        _git(repository, "add", relative.as_posix())
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative.as_posix()}"],
            cwd=repository,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        target.write_bytes(committed)

    with pytest.raises(PortfolioError, match="HEAD|staged|unavailable"):
        runner._assert_preregistration_committed(manifest_path)


def test_cohort_inactivity_proof_fails_closed_on_user_manager_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Failed())
    with pytest.raises(PortfolioError, match="cannot prove"):
        runner._cohort_automation_inactive()


def test_runtime_freezes_the_complete_active_dependency_closure(
    prepared_program: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, program_root = prepared_program
    expected = {
        "anyio",
        "certifi",
        "h11",
        "httpcore",
        "httpx",
        "idna",
        "numpy",
        "pandas",
        "python-dateutil",
        "python-dotenv",
        "pytz",
        "six",
        "typing_extensions",
        "tzdata",
    }
    assert set(runner.RUNTIME_PACKAGES) == expected
    runtime = json.loads(
        (program_root / "contracts/runtime-manifest.json").read_text()
    )
    assert set(runtime["dependencies"]) == expected

    original = runner.importlib.metadata.version

    def drifted_version(package: str) -> str:
        value = original(package)
        return f"{value}.drift" if package == "httpcore" else value

    monkeypatch.setattr(runner.importlib.metadata, "version", drifted_version)
    with pytest.raises(PortfolioError, match="dependency versions differ"):
        runner.validate_runtime(
            program_root,
            runner._program_repo_root(program_root / "program.json"),
        )


def test_complete_resume_finalize_check_and_ranking_replay(
    prepared_program: tuple[Path, Path],
) -> None:
    manifest_path, program_root = prepared_program
    first_client = _complete_first_anchor(program_root)

    resume_client = _exact_client(program_root, remaining=first_client.remaining)
    _run_anchor(program_root, 0, resume_client)
    assert resume_client.fetch_calls == 0
    assert resume_client.requests == []

    _terminalize_remaining_anchors(program_root, first_client.remaining)
    assert len(_final_records(program_root)) == len(PLANNED_SLOTS) == 400

    finalized = _finalize(program_root)
    assert finalized["stage"] == "unscorable"
    assert finalized["terminal_reason"] == "insufficient_program_a_support"
    checked = check_program_a(manifest_path)
    assert checked == {
        "stage": "unscorable",
        "terminal_anchors": len(ANCHORS),
        "authenticated_attempts": 160,
        "billable_credits": 447,
    }

    stored_ranking = json.loads(
        (program_root / "derived/candidate-ranking.json").read_text()
    )
    stored_calibration = json.loads(
        (program_root / "derived/execution-calibration.json").read_text()
    )
    statuses = [
        _terminal_anchor_status(program_root, anchor_index)
        for anchor_index in range(len(ANCHORS))
    ]
    (
        replayed_ranking,
        replayed_calibration,
        replayed_calls,
        replayed_credits,
        replayed_stage,
        replayed_reason,
    ) = _completion_documents(program_root, statuses)
    assert (replayed_calls, replayed_credits) == (160, 447)
    assert (replayed_stage, replayed_reason) == (
        "unscorable",
        "insufficient_program_a_support",
    )
    assert stored_ranking == replayed_ranking
    assert stored_calibration == replayed_calibration
    assert stored_ranking["planned_opportunities"] == 400
    assert stored_ranking["selected_opportunities"] == 7
    assert stored_ranking["selection_coverage"] == pytest.approx(7 / 400)
    assert stored_ranking["program_support"] == {
        "complete_anchor_gate": False,
        "selected_opportunity_gate": False,
        "selected_proxy_coverage_gate": True,
        "complete_anchors": 1,
        "selected_opportunities": 7,
        "proxy_outcomes_available": 7,
        "selected_proxy_coverage": 1.0,
        "passed": False,
    }
    assert all(
        score["planned_opportunities"] == 400
        and score["selected_opportunities"] == 7
        and score["selection_coverage"] == pytest.approx(7 / 400)
        for score in stored_ranking["scores"]
    )
    assert stored_calibration["overall"]["planned"] == len(ANCHORS)
    assert stored_calibration["overall"]["selected"] == 1
    assert stored_calibration["overall"]["execution_available"] == 1
    assert stored_calibration["overall"]["full_round_trip"] == 1
    assert len(stored_calibration["events"]) == len(ANCHORS)
    assert _finalize(program_root) == finalized


def test_resume_fetches_fresh_contract_before_next_authenticated_request(
    prepared_program: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, program_root = prepared_program
    original = runner._collect_features

    def crash_before_features(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic process crash after panel seal")

    monkeypatch.setattr(runner, "_collect_features", crash_before_features)
    first = _exact_client(program_root)
    with pytest.raises(RuntimeError, match="synthetic process crash"):
        _run_anchor(program_root, 0, first)
    assert len(first.requests) == 2
    monkeypatch.setattr(runner, "_collect_features", original)

    drift = _exact_client(
        program_root,
        failure_mode="openapi",
        remaining=first.remaining,
    )
    with pytest.raises(PortfolioError, match="OpenAPI"):
        _run_anchor(program_root, 0, drift)
    assert drift.fetch_calls == 1
    assert drift.requests == []


@pytest.mark.parametrize("write_boundary", ["before_observation", "after_observation"])
def test_openapi_drift_crash_can_never_resume_into_authenticated_work(
    prepared_program: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    write_boundary: str,
) -> None:
    manifest_path, program_root = prepared_program
    original = runner._write_exact_json

    def crash_observation(path: Path, value: Any, *, kind: str) -> Path:
        if kind != "public_openapi_observation":
            return original(path, value, kind=kind)
        if write_boundary == "after_observation":
            original(path, value, kind=kind)
        raise OSError(f"synthetic crash {write_boundary}")

    monkeypatch.setattr(runner, "_write_exact_json", crash_observation)
    drift = _exact_client(program_root, failure_mode="openapi")
    with pytest.raises(OSError, match="synthetic crash"):
        _run_anchor(program_root, 0, drift)
    assert list((_anchor_root(program_root, 0) / "raw/contracts").glob("openapi-drift-*.json"))
    monkeypatch.setattr(runner, "_write_exact_json", original)

    matching = _exact_client(program_root)
    with pytest.raises(PortfolioError, match="prior public OpenAPI drift") as caught:
        _run_anchor(program_root, 0, matching)
    assert matching.fetch_calls == 0
    assert matching.requests == []
    terminal = _terminalize_program(program_root, str(caught.value))
    assert terminal["stage"] == "unscorable"
    assert check_program_a(manifest_path)["stage"] == "unscorable"


@pytest.mark.parametrize("failure_mode", ["charged_http", "charged_nonobject"])
def test_charged_provider_failure_commit_then_crash_can_never_resume_authenticated_work(
    prepared_program: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    manifest_path, program_root = prepared_program
    original_fail = HistoricalPricingGuard.fail

    def crash_after_failure_commit(self: Any, *args: Any, **kwargs: Any) -> None:
        original_fail(self, *args, **kwargs)
        raise KeyboardInterrupt("synthetic kill after charged HTTP ledger commit")

    monkeypatch.setattr(HistoricalPricingGuard, "fail", crash_after_failure_commit)
    failed = _exact_client(program_root, failure_mode=failure_mode)
    with pytest.raises(KeyboardInterrupt, match="charged HTTP ledger commit"):
        _run_anchor(program_root, 0, failed)
    assert [request[1] for request in failed.requests] == [
        ACCOUNT_ENDPOINT,
        SCREENER_ENDPOINT,
    ]
    monkeypatch.setattr(HistoricalPricingGuard, "fail", original_fail)

    resumed = _exact_client(program_root, remaining=failed.remaining)
    with pytest.raises(PortfolioError, match="failed provider response") as caught:
        _run_anchor(program_root, 0, resumed)
    assert resumed.fetch_calls == 0
    assert resumed.requests == []
    terminal = _terminalize_program(program_root, str(caught.value))
    assert terminal["stage"] == "unscorable"
    assert check_program_a(manifest_path)["stage"] == "unscorable"


@pytest.mark.parametrize("tamper", ["response", "metadata"])
def test_midstage_archive_tamper_blocks_resume_before_provider(
    prepared_program: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    _, program_root = prepared_program
    original = runner._call

    def crash_after_first_flow(*args: Any, **kwargs: Any) -> Any:
        body = original(*args, **kwargs)
        if str(kwargs.get("logical_id", "")).endswith("/flow"):
            raise RuntimeError("synthetic crash after confirmed flow")
        return body

    monkeypatch.setattr(runner, "_call", crash_after_first_flow)
    first = _exact_client(program_root)
    with pytest.raises(RuntimeError, match="confirmed flow"):
        _run_anchor(program_root, 0, first)
    monkeypatch.setattr(runner, "_call", original)

    anchor_root = _anchor_root(program_root, 0)
    pattern = (
        "*/attempt-1-response.json"
        if tamper == "response"
        else "*/attempt-1-response-metadata.json"
    )
    targets = [
        path
        for path in (anchor_root / "raw/nansen").glob(pattern)
        if "/flow" not in path.as_posix()
    ]
    flow_entry = next(
        entry
        for entry in HistoricalPricingGuard(
            anchor_root, *_epoch_limits(0)
        ).replay().entries
        if entry.logical_request_id.endswith("/flow")
    )
    target = (
        anchor_root
        / "raw/nansen"
        / flow_entry.reservation_id
        / ("attempt-1-response.json" if tamper == "response" else "attempt-1-response-metadata.json")
    )
    assert target in targets or target.is_file()
    target.write_bytes(target.read_bytes() + b"\n")

    resume = _exact_client(program_root, remaining=first.remaining)
    with pytest.raises(PortfolioError, match="response artifact"):
        _run_anchor(program_root, 0, resume)
    assert resume.fetch_calls == 0
    assert resume.requests == []


@pytest.mark.parametrize("tamper", ["panel", "response"])
def test_sealed_anchor_tampering_blocks_check_and_resume_before_provider(
    prepared_program: tuple[Path, Path], tamper: str
) -> None:
    manifest_path, program_root = prepared_program
    _complete_first_anchor(program_root)
    anchor_root = _anchor_root(program_root, 0)
    if tamper == "panel":
        target = anchor_root / "derived/panel.json"
    else:
        target = next((anchor_root / "raw/nansen").glob("*/attempt-1-response.json"))
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(RuntimeError):
        check_program_a(manifest_path)

    client = _exact_client(program_root, remaining=PROVED_BALANCE - 127)
    with pytest.raises(RuntimeError):
        _run_anchor(program_root, 0, client)
    assert client.fetch_calls == 0
    assert client.requests == []


@pytest.mark.parametrize(
    "failure_mode",
    [
        "ambiguous",
        "pricing",
        "openapi",
        "account",
        "account_exact_low",
        "account_mismatch",
    ],
)
def test_global_fatal_stops_before_any_next_anchor_provider_call(
    prepared_program: tuple[Path, Path], failure_mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, program_root = prepared_program
    client = _exact_client(program_root, failure_mode=failure_mode)

    with pytest.raises(PortfolioError) as caught:
        # This is the same exception boundary used by run_program_a: a global
        # fatal escapes the anchor loop instead of becoming an unscorable anchor.
        for anchor_index in range(2):
            _run_anchor(program_root, anchor_index, client)

    assert client.fetch_calls == 1
    assert all("anchor-02" not in request[3] for request in client.requests)
    if failure_mode.startswith("account"):
        assert [request[1] for request in client.requests] == [ACCOUNT_ENDPOINT]
    assert _terminal_anchor_status(program_root, 0) is None
    terminal = _terminalize_program(program_root, str(caught.value))
    assert terminal["stage"] == "unscorable"
    assert check_program_a(manifest_path)["stage"] == "unscorable"

    def forbidden_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("terminal Program A attempted to construct a provider client")

    monkeypatch.setattr(runner, "NansenClient", forbidden_client)
    assert run_program_a(manifest_path) == terminal
