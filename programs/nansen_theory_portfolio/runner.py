from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time as time_module
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from src.nansen_signal_lab.artifacts import (
    atomic_replace_bytes,
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
)
from src.nansen_signal_lab.budget import BudgetError
from src.nansen_signal_lab.client import NansenClient
from src.nansen_signal_lab.prospective_runner import PilotError, _nansen_call

from .budget import HistoricalPricingGuard, pricing_derivation_paths
from .design import (
    ACCOUNT_ENDPOINT,
    ACTIVE_COHORT_RESERVE,
    ANCHORS,
    CANDIDATES,
    DESIGN_PATH,
    DEX_ENDPOINT,
    FLOW_ENDPOINT,
    FULL_OPENAPI_SHA256,
    FULL_OPENAPI_SOURCE,
    OHLCV_ENDPOINT,
    PLANNED_SLOTS,
    PORTFOLIO_ID,
    PORTFOLIO_MAX_CREDITS,
    PORTFOLIO_SAFETY_CREDITS,
    PROGRAM_A_ALLOCATION,
    PROGRAM_A_ID,
    PROGRAM_A_MAX_CALLS,
    PROGRAM_A_MAX_CREDITS,
    PROGRAM_A_STOP_BEFORE,
    PROGRAM_ALLOCATIONS,
    PROVED_BALANCE,
    SCREENER_ENDPOINT,
    WBS_ENDPOINT,
    DesignError,
    candidate_contract,
    dex_payload,
    execution_outcome,
    flow_payload,
    ohlcv_payload,
    planned_slots,
    score_candidates,
    screener_payload,
    select_anchor_events,
    utc_text,
    validate_dex,
    validate_flow,
    validate_ohlcv,
    validate_screener,
    validate_wbs,
    wbs_payload,
)


class PortfolioError(RuntimeError):
    pass


class ProgramFatal(PortfolioError):
    """A frozen fail-closed condition that permanently ends Program A."""


class AnchorTerminal(PortfolioError):
    pass


PROGRAM_RELATIVE_ROOT = Path("research/experiments") / PROGRAM_A_ID
PORTFOLIO_RELATIVE_ROOT = Path("research/portfolios") / PORTFOLIO_ID
RUNTIME_PACKAGES = (
    # Direct runtime packages plus the complete installed default transport and
    # dataframe dependency closures used by this protocol. Optional extras are
    # deliberately excluded because Program A never imports them.
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
)
COHORT_TIMER = "nansen-signal-lab-cohort.timer"
COHORT_SERVICE = "nansen-signal-lab-cohort.service"
# The supervisor's absolute kill boundary is 10:43:30Z. Stop initiating the
# public 60-second contract fetch or any authenticated 60-second request a full
# 90 seconds before that, leaving recovery margin before the cohort's 10:45Z
# guard boundary.
REQUEST_START_CUTOFF = PROGRAM_A_STOP_BEFORE - timedelta(minutes=3)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise PortfolioError(f"cannot read JSON artifact {path}") from exc
    if not isinstance(value, dict):
        raise PortfolioError(f"JSON artifact is not an object: {path}")
    return value


def _write_exact_json(path: Path, value: Any, *, kind: str) -> Path:
    return write_bytes_once_or_adopt_exact(
        path,
        canonical_json_bytes(value),
        metadata={"kind": kind, "path": path.name},
    )


def _write_exact_bytes(path: Path, value: bytes, *, kind: str) -> Path:
    return write_bytes_once_or_adopt_exact(
        path,
        value,
        metadata={"kind": kind, "path": path.name},
    )


def _source_paths(repo_root: Path) -> tuple[Path, ...]:
    paths = list(sorted((repo_root / "src/nansen_signal_lab").glob("*.py")))
    paths.extend(sorted((repo_root / "programs/nansen_theory_portfolio").glob("*.py")))
    paths.extend(
        [
            repo_root / "programs/__init__.py",
            repo_root / "scripts/nansen_theory_portfolio.py",
            repo_root / "scripts/prospective_cohort_timer.py",
            repo_root / "requirements.txt",
            repo_root / "nansen-lab",
            repo_root / DESIGN_PATH,
        ]
    )
    paths.extend(sorted((repo_root / "ops/systemd").glob("*.service")))
    paths.extend(sorted((repo_root / "ops/systemd").glob("*.timer")))
    paths.extend(sorted((repo_root / "tests").glob("test_nansen_theory_portfolio*.py")))
    paths.append(repo_root / FULL_OPENAPI_SOURCE)
    missing = [path for path in paths if not path.is_file() or path.is_symlink()]
    if missing:
        raise PortfolioError(f"runtime source is missing: {missing[0]}")
    unique = {path.resolve() for path in paths}
    if len(unique) != len(paths):
        raise PortfolioError("runtime source set contains duplicate paths")
    return tuple(paths)


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in RUNTIME_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise PortfolioError(f"required runtime package is missing: {package}") from exc
    return versions


def _runtime_manifest(repo_root: Path, program_root: Path) -> dict[str, Any]:
    records = []
    implementation_root = program_root / "contracts/implementation"
    for source in _source_paths(repo_root):
        relative = source.relative_to(repo_root)
        destination = implementation_root / relative
        content = source.read_bytes()
        _write_exact_bytes(destination, content, kind="runtime_source_copy")
        records.append(
            {
                "path": relative.as_posix(),
                "sha256": _sha256_bytes(content),
                "archived_path": destination.relative_to(program_root).as_posix(),
            }
        )
    return {
        "schema_version": 1,
        "program_id": PROGRAM_A_ID,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "dependencies": _dependency_versions(),
        "sources": records,
    }


def _portfolio_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "portfolio_id": PORTFOLIO_ID,
        "proved_provider_balance": PROVED_BALANCE,
        "active_cohort_remaining_reserve": ACTIVE_COHORT_RESERVE,
        "safety_margin": PORTFOLIO_SAFETY_CREDITS,
        "max_new_research_credits": PORTFOLIO_MAX_CREDITS,
        "allocations": dict(PROGRAM_ALLOCATIONS),
        "accounting_authority": "terminal-program-ledger-snapshots",
        "roll_forward": "verified-terminal-underspend-to-temporal-replication-only",
    }


def _schedule_document() -> dict[str, Any]:
    slots = planned_slots()
    return {
        "schema_version": 1,
        "program_id": PROGRAM_A_ID,
        "anchors": [anchor.isoformat() for anchor in ANCHORS],
        "stop_before": utc_text(PROGRAM_A_STOP_BEFORE),
        "slots": [
            {
                "event_id": slot.event_id,
                "anchor_index": slot.anchor_index,
                "anchor": slot.anchor.isoformat(),
                "slot_index": slot.slot_index,
                "chain": slot.chain,
                "stratum": slot.stratum,
                "execution_calibration": slot.execution_calibration,
            }
            for slot in slots
        ],
    }


def _preregistration_text() -> bytes:
    return f"""# Preregistration — {PROGRAM_A_ID}

Status: **preregistered discovery; no authenticated provider request has run**.

This is Program A of `{PORTFOLIO_ID}`. It uses 65 fixed historical anchors,
400 chain-balanced prefix-relative token slots, point-in-time historical flow
and Smart-Money breadth, common five-minute OHLCV proxy outcomes, and 65
prescheduled historical DEX calibrations. It is discovery only.

- Maximum authenticated attempts: {PROGRAM_A_MAX_CALLS}.
- Maximum billable credits: {PROGRAM_A_MAX_CREDITS} inside an
  {PROGRAM_A_ALLOCATION}-credit allocation.
- Active-cohort reserve: {ACTIVE_COHORT_RESERVE}; portfolio safety margin:
  {PORTFOLIO_SAFETY_CREDITS}; total new-research cap: {PORTFOLIO_MAX_CREDITS}.
- Every anchor is an independent account/budget epoch. Automatic request
  retries and replacement observations are forbidden. Every reservation,
  including the zero-cost account preflight, consumes one attempt slot.
- No public or authenticated request starts at or after 2026-08-20T10:42:00Z;
  the supervisor hard-stops at 10:43:30Z.
- Historical list endpoints use page one only. Non-final WBS/DEX evidence is
  unavailable, never extrapolated.
- Historical screener response chain `bsc` is the sole source alias and is
  preserved then normalized to contract-native `bnb`.
- The historical selling-pressure veto is not the frozen four-hour H1 veto.
- Program completion requires 59 anchors, 320 selected slots, and 90% selected
  proxy coverage; otherwise support is unscorable. Provider/contract failures
  terminalize the whole program before another anchor.
- No Program-A result can satisfy a prospective or capital-deployment gate.
""".encode("utf-8")


def initialize_program_a(repo_root: Path) -> Path:
    repo_root = repo_root.resolve()
    program_root = repo_root / PROGRAM_RELATIVE_ROOT
    portfolio_root = repo_root / PORTFOLIO_RELATIVE_ROOT
    manifest_path = program_root / "program.json"
    if manifest_path.exists():
        validate_program_a(manifest_path, validate_live_runtime=True)
        return manifest_path
    if program_root.exists() and any(program_root.iterdir()):
        raise PortfolioError("program root exists without a valid manifest")
    program_root.mkdir(parents=True, exist_ok=True)
    portfolio_root.mkdir(parents=True, exist_ok=True)

    portfolio_path = _write_exact_json(
        portfolio_root / "portfolio.json",
        _portfolio_document(),
        kind="portfolio_allocation",
    )
    openapi_source = repo_root / FULL_OPENAPI_SOURCE
    if _sha256_file(openapi_source) != FULL_OPENAPI_SHA256:
        raise PortfolioError("archived full OpenAPI source differs from its frozen hash")
    openapi_path = _write_exact_bytes(
        program_root / "contracts/nansen-openapi.json",
        openapi_source.read_bytes(),
        kind="full_openapi_contract",
    )
    design_source = repo_root / DESIGN_PATH
    design_path = _write_exact_bytes(
        program_root / "contracts/design.md",
        design_source.read_bytes(),
        kind="portfolio_design_copy",
    )
    schedule_path = _write_exact_json(
        program_root / "schedule.json",
        _schedule_document(),
        kind="program_a_schedule",
    )
    candidates_path = _write_exact_json(
        program_root / "contracts/candidates.json",
        candidate_contract(),
        kind="program_a_candidate_contract",
    )
    preregistration_path = _write_exact_bytes(
        program_root / "PREREGISTRATION.md",
        _preregistration_text(),
        kind="program_a_preregistration",
    )
    runtime_path = _write_exact_json(
        program_root / "contracts/runtime-manifest.json",
        _runtime_manifest(repo_root, program_root),
        kind="runtime_manifest",
    )
    manifest = {
        "schema_version": 1,
        "portfolio_id": PORTFOLIO_ID,
        "program_id": PROGRAM_A_ID,
        "stage": "preregistered",
        "terminal_reason": None,
        "design_path": design_path.relative_to(program_root).as_posix(),
        "design_sha256": _sha256_file(design_path),
        "openapi_path": openapi_path.relative_to(program_root).as_posix(),
        "openapi_sha256": _sha256_file(openapi_path),
        "runtime_manifest_path": runtime_path.relative_to(program_root).as_posix(),
        "runtime_manifest_sha256": _sha256_file(runtime_path),
        "schedule_path": schedule_path.relative_to(program_root).as_posix(),
        "schedule_sha256": _sha256_file(schedule_path),
        "candidate_contract_path": candidates_path.relative_to(program_root).as_posix(),
        "candidate_contract_sha256": _sha256_file(candidates_path),
        "preregistration_path": preregistration_path.relative_to(program_root).as_posix(),
        "preregistration_sha256": _sha256_file(preregistration_path),
        "portfolio_path": portfolio_path.relative_to(repo_root).as_posix(),
        "portfolio_sha256": _sha256_file(portfolio_path),
        "max_authenticated_attempts": PROGRAM_A_MAX_CALLS,
        "max_billable_credits": PROGRAM_A_MAX_CREDITS,
        "allocation_credits": PROGRAM_A_ALLOCATION,
        "active_cohort_reserve": ACTIVE_COHORT_RESERVE,
        "portfolio_safety_margin": PORTFOLIO_SAFETY_CREDITS,
        "stop_before": utc_text(PROGRAM_A_STOP_BEFORE),
    }
    _write_exact_json(manifest_path, manifest, kind="program_a_manifest")
    validate_program_a(manifest_path, validate_live_runtime=True)
    return manifest_path


def _program_repo_root(manifest_path: Path) -> Path:
    expected_parts = PROGRAM_RELATIVE_ROOT.parts
    resolved = manifest_path.resolve()
    if resolved.name != "program.json" or tuple(resolved.parent.parts[-len(expected_parts):]) != expected_parts:
        raise PortfolioError("program manifest is not at the frozen repository path")
    return resolved.parents[len(expected_parts)]


def validate_runtime(program_root: Path, repo_root: Path) -> None:
    manifest_path = program_root / "contracts/runtime-manifest.json"
    runtime = _load_json(manifest_path)
    if runtime.get("python") != platform.python_version() or runtime.get(
        "python_implementation"
    ) != platform.python_implementation():
        raise PortfolioError("Python runtime differs from preregistration")
    if runtime.get("dependencies") != _dependency_versions():
        raise PortfolioError("dependency versions differ from preregistration")
    sources = runtime.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PortfolioError("runtime manifest source set is invalid")
    expected_live = {path.relative_to(repo_root).as_posix() for path in _source_paths(repo_root)}
    recorded_live: set[str] = set()
    for record in sources:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "archived_path"}:
            raise PortfolioError("runtime manifest source record is invalid")
        live = repo_root / record["path"]
        archived = program_root / record["archived_path"]
        if live.is_symlink() or archived.is_symlink() or not live.is_file() or not archived.is_file():
            raise PortfolioError("runtime source is missing or symlinked")
        digest = _sha256_file(live)
        if digest != record["sha256"] or _sha256_file(archived) != digest:
            raise PortfolioError(f"runtime source drift: {record['path']}")
        recorded_live.add(record["path"])
    if recorded_live != expected_live:
        raise PortfolioError("runtime source set differs from preregistration")


def validate_program_a(
    manifest_path: Path, *, validate_live_runtime: bool = True
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    repo_root = _program_repo_root(manifest_path)
    program_root = manifest_path.parent
    manifest = _load_json(manifest_path)
    expected_scalars = {
        "schema_version": 1,
        "portfolio_id": PORTFOLIO_ID,
        "program_id": PROGRAM_A_ID,
        "max_authenticated_attempts": PROGRAM_A_MAX_CALLS,
        "max_billable_credits": PROGRAM_A_MAX_CREDITS,
        "allocation_credits": PROGRAM_A_ALLOCATION,
        "active_cohort_reserve": ACTIVE_COHORT_RESERVE,
        "portfolio_safety_margin": PORTFOLIO_SAFETY_CREDITS,
        "stop_before": utc_text(PROGRAM_A_STOP_BEFORE),
    }
    for key, expected in expected_scalars.items():
        if manifest.get(key) != expected:
            raise PortfolioError(f"program manifest {key} differs")
    if manifest.get("stage") not in {"preregistered", "completed", "unscorable"}:
        raise PortfolioError("program manifest stage is invalid")
    for path_key, hash_key in (
        ("design_path", "design_sha256"),
        ("openapi_path", "openapi_sha256"),
        ("runtime_manifest_path", "runtime_manifest_sha256"),
        ("schedule_path", "schedule_sha256"),
        ("candidate_contract_path", "candidate_contract_sha256"),
        ("preregistration_path", "preregistration_sha256"),
    ):
        relative = manifest.get(path_key)
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PortfolioError(f"program manifest {path_key} is invalid")
        path = program_root / relative
        if path.is_symlink() or not path.is_file() or _sha256_file(path) != manifest.get(hash_key):
            raise PortfolioError(f"program manifest {path_key} hash differs")
    portfolio_path = repo_root / manifest.get("portfolio_path", "")
    if (
        portfolio_path.resolve() != (repo_root / PORTFOLIO_RELATIVE_ROOT / "portfolio.json").resolve()
        or portfolio_path.is_symlink()
        or not portfolio_path.is_file()
        or _sha256_file(portfolio_path) != manifest.get("portfolio_sha256")
        or _load_json(portfolio_path) != _portfolio_document()
    ):
        raise PortfolioError("portfolio allocation differs")
    if manifest.get("openapi_sha256") != FULL_OPENAPI_SHA256:
        raise PortfolioError("program full OpenAPI hash differs")
    if _load_json(program_root / manifest["schedule_path"]) != _schedule_document():
        raise PortfolioError("program schedule differs")
    if _load_json(program_root / manifest["candidate_contract_path"]) != candidate_contract():
        raise PortfolioError("program candidate contract differs")
    if manifest["stage"] in {"completed", "unscorable"}:
        for path_key, hash_key in (
            ("final_seal_path", "final_seal_sha256"),
            ("report_path", "report_sha256"),
        ):
            relative = manifest.get(path_key)
            if (
                not isinstance(relative, str)
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
            ):
                raise PortfolioError(f"terminal program {path_key} is invalid")
            path = program_root / relative
            if not path.is_file() or path.is_symlink() or _sha256_file(path) != manifest.get(hash_key):
                raise PortfolioError(f"terminal program {path_key} differs")
        final = _load_json(program_root / manifest["final_seal_path"])
        if (
            final.get("schema_version") != 1
            or final.get("stage") != manifest["stage"]
            or final.get("terminal_reason") != manifest.get("terminal_reason")
            or not isinstance(final.get("artifacts"), list)
        ):
            raise PortfolioError("program final seal identity differs")
        for record in final["artifacts"]:
            if (
                not isinstance(record, dict)
                or set(record) != {"path", "sha256"}
                or not isinstance(record["path"], str)
                or Path(record["path"]).is_absolute()
                or ".." in Path(record["path"]).parts
            ):
                raise PortfolioError("program final seal artifact record is invalid")
            path = program_root / record["path"]
            if path.is_symlink() or not path.is_file() or _sha256_file(path) != record["sha256"]:
                raise PortfolioError("program final seal artifact differs")
    elif manifest.get("terminal_reason") is not None:
        raise PortfolioError("preregistered program has a terminal reason")
    if validate_live_runtime:
        validate_runtime(program_root, repo_root)
    return manifest


def _assert_preregistration_committed(manifest_path: Path) -> None:
    repo_root = _program_repo_root(manifest_path)
    program_root = manifest_path.resolve().parent
    required = [
        PROGRAM_RELATIVE_ROOT / "program.json",
        PROGRAM_RELATIVE_ROOT / "schedule.json",
        PROGRAM_RELATIVE_ROOT / "PREREGISTRATION.md",
        PROGRAM_RELATIVE_ROOT / "contracts/design.md",
        PROGRAM_RELATIVE_ROOT / "contracts/nansen-openapi.json",
        PROGRAM_RELATIVE_ROOT / "contracts/runtime-manifest.json",
        PROGRAM_RELATIVE_ROOT / "contracts/candidates.json",
        PORTFOLIO_RELATIVE_ROOT / "portfolio.json",
    ]
    required.extend(path.relative_to(repo_root) for path in _source_paths(repo_root))
    runtime = _load_json(program_root / "contracts/runtime-manifest.json")
    for record in runtime.get("sources", []):
        if not isinstance(record, dict) or not isinstance(record.get("archived_path"), str):
            raise PortfolioError("runtime source record is invalid")
        required.append(PROGRAM_RELATIVE_ROOT / record["archived_path"])

    unique = sorted({path.as_posix() for path in required})
    for relative in unique:
        path = repo_root / relative
        if path.is_symlink() or not path.is_file():
            raise PortfolioError(f"required preregistration file is unavailable: {relative}")
        result = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{relative}"],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise PortfolioError(f"preregistration file is absent from HEAD: {relative}")
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if committed.returncode != 0 or committed.stdout != path.read_bytes():
            raise PortfolioError(f"preregistration file differs from HEAD: {relative}")
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "HEAD", "--", *unique],
        cwd=repo_root,
        check=False,
    )
    if staged.returncode != 0:
        raise PortfolioError("preregistration files have staged changes from HEAD")


def _cohort_automation_inactive() -> None:
    for unit in (COHORT_TIMER, COHORT_SERVICE):
        states: dict[str, str] = {}
        for field in ("LoadState", "ActiveState"):
            result = subprocess.run(
                ["systemctl", "--user", "show", unit, f"--property={field}", "--value"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise PortfolioError(f"cannot prove cohort automation state: {unit}")
            states[field] = result.stdout.strip()
        if states != {"LoadState": "loaded", "ActiveState": "inactive"}:
            raise PortfolioError(
                f"cohort automation is not loaded and inactive: {unit} {states}"
            )


@contextmanager
def _provider_lock():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise PortfolioError("XDG_RUNTIME_DIR is unavailable for provider lock")
    lock_path = Path(runtime_dir) / "nansen-signal-lab-provider" / "provider.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PortfolioError("another cooperating Nansen provider job is active") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _anchor_root(program_root: Path, anchor_index: int) -> Path:
    return program_root / "anchors" / f"anchor-{anchor_index + 1:02d}"


def _slots_for_anchor(anchor_index: int) -> tuple[Any, ...]:
    return tuple(slot for slot in PLANNED_SLOTS if slot.anchor_index == anchor_index)


def _epoch_limits(anchor_index: int) -> tuple[int, int]:
    event_count = len(_slots_for_anchor(anchor_index))
    expected_events = 7 if anchor_index < 10 else 6
    if event_count != expected_events:
        raise PortfolioError("anchor schedule event count differs")
    return (2 + 4 * event_count + 2, 5 + 16 * event_count + 10)


def _completed_epoch_totals(program_root: Path, before_anchor: int) -> tuple[int, int]:
    calls = 0
    credits = 0
    for anchor_index in range(before_anchor):
        root = _anchor_root(program_root, anchor_index)
        if not (root / "seals/terminal.json").is_file():
            raise PortfolioError("earlier anchor is not terminal")
        max_calls, max_credits = _epoch_limits(anchor_index)
        totals = HistoricalPricingGuard(root, max_calls, max_credits).replay()
        calls += totals.calls
        credits += totals.credits
    return calls, credits


def _artifact_record(root: Path, path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise PortfolioError(f"seal artifact is missing: {path}")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
    }


def _validate_stage_seal(
    root: Path,
    guard: HistoricalPricingGuard,
    stage: str,
    *,
    required_paths: Iterable[Path] = (),
    expected_status: str | None = None,
    require_current_head: bool = False,
) -> dict[str, Any]:
    seal_path = root / "seals" / f"{stage}.json"
    seal = _load_json(seal_path)
    if set(seal) != {
        "schema_version",
        "stage",
        "status",
        "reason",
        "recorded_at",
        "budget_snapshot",
        "artifacts",
    }:
        raise PortfolioError(f"{stage} seal has invalid keys")
    if seal.get("schema_version") != 1 or seal.get("stage") != stage:
        raise PortfolioError(f"{stage} seal identity differs")
    if seal.get("status") not in {"complete", "unscorable"}:
        raise PortfolioError(f"{stage} seal status is invalid")
    if expected_status is not None and seal["status"] != expected_status:
        raise PortfolioError(f"{stage} seal status differs")
    if seal["status"] == "complete" and seal.get("reason") is not None:
        raise PortfolioError(f"{stage} complete seal has a reason")
    if seal["status"] == "unscorable" and (
        not isinstance(seal.get("reason"), str) or not seal["reason"]
    ):
        raise PortfolioError(f"{stage} unscorable seal lacks a reason")
    recorded_at = seal.get("recorded_at")
    if not isinstance(recorded_at, str):
        raise PortfolioError(f"{stage} seal timestamp is invalid")
    try:
        datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortfolioError(f"{stage} seal timestamp is invalid") from exc

    records = seal.get("artifacts")
    if not isinstance(records, list) or not records:
        raise PortfolioError(f"{stage} seal artifact list is invalid")
    by_path: dict[str, dict[str, str]] = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "sha256"}
            or not isinstance(record["path"], str)
            or Path(record["path"]).is_absolute()
            or ".." in Path(record["path"]).parts
            or not isinstance(record["sha256"], str)
            or record["path"] in by_path
        ):
            raise PortfolioError(f"{stage} seal artifact record is invalid")
        path = root / record["path"]
        if path.is_symlink() or not path.is_file() or _sha256_file(path) != record["sha256"]:
            raise PortfolioError(f"{stage} seal artifact differs: {record['path']}")
        by_path[record["path"]] = record
    for path in required_paths:
        relative = path.relative_to(root).as_posix()
        if relative not in by_path or by_path[relative] != _artifact_record(root, path):
            raise PortfolioError(f"{stage} seal does not bind required artifact: {relative}")

    snapshot_record = seal.get("budget_snapshot")
    expected_snapshot_path = f"budget/snapshots/{stage}.json"
    if snapshot_record != by_path.get(expected_snapshot_path):
        raise PortfolioError(f"{stage} seal budget snapshot record differs")
    snapshot = _load_json(root / expected_snapshot_path)
    if (
        snapshot.get("schema_version") != 1
        or snapshot.get("stage") != stage
        or snapshot.get("recorded_at") != recorded_at
        or not isinstance(snapshot.get("transition_sha256s"), list)
        or not isinstance(snapshot.get("totals"), dict)
    ):
        raise PortfolioError(f"{stage} budget snapshot identity differs")
    totals = guard.replay()
    transitions = snapshot["transition_sha256s"]
    if transitions != list(totals.transition_sha256s[: len(transitions)]):
        raise PortfolioError(f"{stage} budget snapshot is not a journal prefix")
    expected_head = None if not transitions else transitions[-1]
    if snapshot.get("journal_head_sha256") != expected_head:
        raise PortfolioError(f"{stage} budget snapshot head differs")
    if require_current_head and (
        expected_head != totals.journal_head_sha256
        or snapshot.get("totals")
        != {"calls": totals.calls, "credits": totals.credits}
        or snapshot.get("provider_remaining") != totals.provider_remaining
        or snapshot.get("halted_reason") != totals.halted_reason
    ):
        raise PortfolioError(f"{stage} budget snapshot is not the current journal head")
    return seal


def _seal_stage(
    root: Path,
    guard: HistoricalPricingGuard,
    stage: str,
    paths: Iterable[Path],
    *,
    status: str = "complete",
    reason: str | None = None,
) -> Path:
    seal_path = root / "seals" / f"{stage}.json"
    materialized_paths = tuple(paths)
    if seal_path.exists():
        _validate_stage_seal(
            root,
            guard,
            stage,
            required_paths=materialized_paths,
            expected_status=status,
            require_current_head=stage == "terminal",
        )
        return seal_path
    snapshot = root / "budget" / "snapshots" / f"{stage}.json"
    if snapshot.is_file():
        existing = _load_json(snapshot)
        recorded_at = existing.get("recorded_at")
        if not isinstance(recorded_at, str):
            raise PortfolioError(f"{stage} snapshot timestamp is invalid")
    else:
        recorded_at = utc_text(datetime.now(timezone.utc))
        snapshot = guard.snapshot(stage, recorded_at=recorded_at)
    totals = guard.replay()
    snapshot_document = _load_json(snapshot)
    if (
        snapshot_document.get("stage") != stage
        or snapshot_document.get("recorded_at") != recorded_at
        or snapshot_document.get("journal_head_sha256") != totals.journal_head_sha256
        or snapshot_document.get("totals")
        != {"calls": totals.calls, "credits": totals.credits}
        or snapshot_document.get("provider_remaining") != totals.provider_remaining
        or snapshot_document.get("halted_reason") != totals.halted_reason
    ):
        raise PortfolioError(f"{stage} snapshot does not match the current budget head")
    records = [_artifact_record(root, path) for path in materialized_paths]
    records.append(_artifact_record(root, snapshot))
    for path in pricing_derivation_paths(root):
        records.append(_artifact_record(root, path))
    result = _write_exact_json(
        seal_path,
        {
            "schema_version": 1,
            "stage": stage,
            "status": status,
            "reason": reason,
            "recorded_at": recorded_at,
            "budget_snapshot": _artifact_record(root, snapshot),
            "artifacts": sorted(records, key=lambda record: record["path"]),
        },
        kind="program_a_stage_seal",
    )
    _validate_stage_seal(
        root,
        guard,
        stage,
        required_paths=materialized_paths,
        expected_status=status,
        require_current_head=stage == "terminal",
    )
    return result


def _terminalize_anchor(
    root: Path,
    guard: HistoricalPricingGuard,
    reason: str,
    paths: Iterable[Path] = (),
) -> None:
    reason_path = _write_exact_json(
        root / "derived/terminal-reason.json",
        {"schema_version": 1, "status": "unscorable", "reason": reason},
        kind="anchor_terminal_reason",
    )
    _seal_stage(
        root,
        guard,
        "terminal",
        [*paths, reason_path],
        status="unscorable",
        reason=reason,
    )


def _fresh_openapi_check(program_root: Path, anchor_root: Path, client: NansenClient) -> Path:
    if datetime.now(timezone.utc) >= REQUEST_START_CUTOFF:
        raise ProgramFatal("program-A request-start cutoff reached")
    contract_root = anchor_root / "raw/contracts"
    sequence = len(tuple(contract_root.glob("openapi-observation-*.json"))) + 1
    metadata_path = contract_root / f"openapi-observation-{sequence:03d}.json"
    raw = client.fetch_openapi()
    digest = _sha256_bytes(raw)
    observed = {
        "schema_version": 1,
        "observed_at": utc_text(datetime.now(timezone.utc)),
        "sha256": digest,
        "expected_sha256": FULL_OPENAPI_SHA256,
        "matched": digest == FULL_OPENAPI_SHA256,
    }
    if digest != FULL_OPENAPI_SHA256:
        _write_exact_bytes(
            contract_root / f"openapi-drift-{sequence:03d}.json",
            raw,
            kind="public_openapi_drift",
        )
    metadata = _write_exact_json(
        metadata_path,
        observed,
        kind="public_openapi_observation",
    )
    if digest != FULL_OPENAPI_SHA256:
        raise ProgramFatal("public OpenAPI bytes differ from frozen contract")
    archived = program_root / "contracts/nansen-openapi.json"
    if raw != archived.read_bytes():
        raise ProgramFatal("public OpenAPI bytes differ despite matching hash")
    return metadata


def _refuse_prior_contract_failure(anchor_root: Path) -> None:
    contract_root = anchor_root / "raw/contracts"
    if any(contract_root.glob("openapi-drift-*.json")):
        raise ProgramFatal("prior public OpenAPI drift evidence forbids resume")
    for observation_path in sorted(
        contract_root.glob("openapi-observation-*.json")
    ):
        observation = _load_json(observation_path)
        if (
            observation.get("schema_version") != 1
            or observation.get("expected_sha256") != FULL_OPENAPI_SHA256
            or observation.get("sha256") != FULL_OPENAPI_SHA256
            or observation.get("matched") is not True
        ):
            raise ProgramFatal("prior public OpenAPI observation forbids resume")


def _refuse_prior_provider_failure(
    anchor_root: Path, guard: HistoricalPricingGuard
) -> None:
    """Refuse a crash-resume after any archived non-success HTTP response."""

    for entry in guard.replay().entries:
        if entry.response_artifact_sha256 is None:
            continue
        metadata_path = (
            anchor_root
            / "raw/nansen"
            / entry.reservation_id
            / f"attempt-{entry.attempt_count}-response-metadata.json"
        )
        metadata = _load_json(metadata_path)
        status = metadata.get("status_code")
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 200 <= status < 300
        ):
            raise ProgramFatal(
                f"prior non-success HTTP response for {entry.logical_request_id} "
                "forbids resume"
            )


def _call(
    *,
    program_root: Path,
    anchor_root: Path,
    guard: HistoricalPricingGuard,
    client: NansenClient,
    logical_id: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    expected_credits: int,
    minimum_remaining: int = 0,
) -> Any:
    if datetime.now(timezone.utc) >= REQUEST_START_CUTOFF:
        raise ProgramFatal("program-A request-start cutoff reached")
    validate_runtime(program_root, _program_repo_root(program_root / "program.json"))
    try:
        response, _ = _nansen_call(
            root=anchor_root,
            guard=guard,
            nansen=client,
            logical_request_id=logical_id,
            method=method,
            endpoint=endpoint,
            payload=payload,
            expected_credits=expected_credits,
            clock=lambda: datetime.now(timezone.utc),
            sleep=time_module.sleep,
            account_baseline_version="account-baseline-v2" if endpoint == ACCOUNT_ENDPOINT else None,
            openapi_sha256=FULL_OPENAPI_SHA256 if endpoint == ACCOUNT_ENDPOINT else None,
            account_minimum_remaining=minimum_remaining,
            allow_retry=False,
        )
    except (PilotError, BudgetError) as exc:
        raise ProgramFatal(f"request {logical_id} halted the portfolio: {exc}") from exc
    if endpoint == ACCOUNT_ENDPOINT:
        body = response.body
        plan = body.get("plan") if isinstance(body, dict) else None
        body_remaining = (
            body.get("credits_remaining") if isinstance(body, dict) else None
        )
        totals = guard.replay()
        account_entry = _entry_by_logical_id(guard, logical_id)
        valid = (
            200 <= response.status_code < 300
            and response.body_parse_status == "json_object"
            and isinstance(plan, str)
            and plan in {"free", "pro"}
            and isinstance(body_remaining, int)
            and not isinstance(body_remaining, bool)
            and body_remaining >= minimum_remaining
            and not response.credit_header_errors
            and response.credit_cost == 0
            and response.credit_used in {None, 0}
            and response.credit_remaining in {None, body_remaining}
            and totals.provider_remaining == body_remaining
        )
        proof = {
            "schema_version": 1,
            "policy": "program-a-account-minimum-v1",
            "logical_request_id": logical_id,
            "openapi_sha256": FULL_OPENAPI_SHA256,
            "response_metadata_sha256": account_entry.response_artifact_sha256,
            "plan": plan,
            "body_credits_remaining": body_remaining,
            "header_credit_cost": response.credit_cost,
            "header_credit_used": response.credit_used,
            "header_credit_remaining": response.credit_remaining,
            "minimum_remaining": minimum_remaining,
            "ledger_provider_remaining": totals.provider_remaining,
            "passed": valid,
        }
        proof_path = anchor_root / "derived/account-validation.json"
        _write_exact_json(proof_path, proof, kind="program_a_account_validation")
        if not valid:
            raise ProgramFatal(
                f"request {logical_id} failed the sealed account minimum validation"
            )
    if endpoint != ACCOUNT_ENDPOINT:
        time_module.sleep(0.25)
    return response.body


def _validate_panel_document(panel: dict[str, Any], anchor_index: int) -> None:
    slots = _slots_for_anchor(anchor_index)
    if (
        panel.get("schema_version") != 1
        or panel.get("anchor_index") != anchor_index
        or panel.get("anchor") != ANCHORS[anchor_index].isoformat()
        or panel.get("prefix_universe") is not True
        or panel.get("planned_slots") != len(slots)
        or not isinstance(panel.get("eligible_rows"), int)
        or isinstance(panel.get("eligible_rows"), bool)
        or panel["eligible_rows"] < 0
        or not isinstance(panel.get("events"), list)
        or len(panel["events"]) != len(slots)
    ):
        raise PortfolioError("panel identity or denominator differs from schedule")
    selected = 0
    identities: set[tuple[str, str]] = set()
    for slot, event in zip(slots, panel["events"], strict=True):
        if (
            not isinstance(event, dict)
            or event.get("event_id") != slot.event_id
            or event.get("anchor") != slot.anchor.isoformat()
            or event.get("chain") != slot.chain
            or event.get("stratum") != slot.stratum
            or event.get("execution_calibration") is not slot.execution_calibration
            or event.get("status") not in {"selected", "unavailable"}
        ):
            raise PortfolioError("panel event differs from its scheduled slot")
        if event["status"] == "selected":
            address = event.get("token_address")
            if not isinstance(address, str) or not address:
                raise PortfolioError("selected panel event lacks a token identity")
            identity = (slot.chain, address)
            if identity in identities:
                raise PortfolioError("panel reuses a token within an anchor")
            identities.add(identity)
            selected += 1
    if panel.get("selected_events") != selected:
        raise PortfolioError("panel selected-event count differs")


def _validate_feature_document(
    document: dict[str, Any], anchor_index: int, panel: dict[str, Any]
) -> None:
    selected_ids = [
        event["event_id"] for event in panel["events"] if event["status"] == "selected"
    ]
    features = document.get("features")
    if (
        document.get("schema_version") != 1
        or document.get("anchor_index") != anchor_index
        or not isinstance(features, list)
        or [record.get("event_id") for record in features if isinstance(record, dict)]
        != selected_ids
    ):
        raise PortfolioError("feature document identity differs from sealed panel")


def _validate_outcome_document(
    document: dict[str, Any], anchor_index: int, panel: dict[str, Any]
) -> None:
    selected = [event for event in panel["events"] if event["status"] == "selected"]
    outcomes = document.get("outcomes")
    if (
        document.get("schema_version") != 1
        or document.get("anchor_index") != anchor_index
        or not isinstance(outcomes, list)
        or [record.get("event_id") for record in outcomes if isinstance(record, dict)]
        != [event["event_id"] for event in selected]
    ):
        raise PortfolioError("outcome document identity differs from sealed panel")
    by_id = {event["event_id"]: event for event in selected}
    for record in outcomes:
        if not isinstance(record.get("proxy"), dict):
            raise PortfolioError("outcome record lacks proxy result")
        event = by_id[record["event_id"]]
        if event["execution_calibration"] and not isinstance(record.get("execution"), dict):
            raise PortfolioError("calibration event lacks execution result")
        if not event["execution_calibration"] and "execution" in record:
            raise PortfolioError("non-calibration event contains execution result")


def _entry_by_logical_id(
    guard: HistoricalPricingGuard, logical_id: str
) -> Any:
    matches = [
        entry for entry in guard.replay().entries if entry.logical_request_id == logical_id
    ]
    if len(matches) != 1:
        raise PortfolioError(f"request ledger does not contain exactly one {logical_id}")
    return matches[0]


def _response_body(anchor_root: Path, guard: HistoricalPricingGuard, logical_id: str) -> Any:
    entry = _entry_by_logical_id(guard, logical_id)
    if entry.state not in {"confirmed_zero", "confirmed_used"}:
        raise PortfolioError(f"request {logical_id} is not confirmed")
    path = (
        anchor_root
        / "raw/nansen"
        / entry.reservation_id
        / f"attempt-{entry.attempt_count}-response.json"
    )
    if path.is_symlink() or not path.is_file():
        raise PortfolioError(f"request {logical_id} response body is unavailable")
    try:
        return json.loads(path.read_bytes())
    except json.JSONDecodeError as exc:
        raise PortfolioError(f"request {logical_id} response body is invalid") from exc


def _expected_request_plan(
    anchor_index: int, panel: dict[str, Any] | None
) -> dict[str, tuple[str, str, dict[str, Any] | None, int]]:
    prefix = f"anchor-{anchor_index + 1:02d}"
    plan: dict[str, tuple[str, str, dict[str, Any] | None, int]] = {
        f"{prefix}/account": ("GET", ACCOUNT_ENDPOINT, None, 0),
        f"{prefix}/screener": (
            "POST",
            SCREENER_ENDPOINT,
            screener_payload(ANCHORS[anchor_index]),
            5,
        ),
    }
    if panel is None:
        return plan
    _validate_panel_document(panel, anchor_index)
    for event in panel["events"]:
        if event["status"] != "selected":
            continue
        plan.update(
            {
                f"{event['event_id']}/flow": (
                    "POST",
                    FLOW_ENDPOINT,
                    flow_payload(event),
                    5,
                ),
                f"{event['event_id']}/wbs-buy": (
                    "POST",
                    WBS_ENDPOINT,
                    wbs_payload(event, "BUY"),
                    5,
                ),
                f"{event['event_id']}/wbs-sell": (
                    "POST",
                    WBS_ENDPOINT,
                    wbs_payload(event, "SELL"),
                    5,
                ),
                f"{event['event_id']}/ohlcv": (
                    "POST",
                    OHLCV_ENDPOINT,
                    ohlcv_payload(event),
                    1,
                ),
            }
        )
        if event["execution_calibration"]:
            plan[f"{event['event_id']}/dex-buy"] = (
                "POST",
                DEX_ENDPOINT,
                dex_payload(event, "BUY"),
                5,
            )
            plan[f"{event['event_id']}/dex-sell"] = (
                "POST",
                DEX_ENDPOINT,
                dex_payload(event, "SELL"),
                5,
            )
    return plan


def _recompute_complete_anchor(
    anchor_root: Path, anchor_index: int, guard: HistoricalPricingGuard
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    anchor = ANCHORS[anchor_index]
    try:
        eligible = validate_screener(
            _response_body(
                anchor_root,
                guard,
                f"anchor-{anchor_index + 1:02d}/screener",
            ),
            anchor,
        )
        events = select_anchor_events(_slots_for_anchor(anchor_index), eligible)
    except DesignError as exc:
        raise PortfolioError(f"sealed screener evidence no longer validates: {exc}") from exc
    panel = {
        "schema_version": 1,
        "anchor_index": anchor_index,
        "anchor": anchor.isoformat(),
        "prefix_universe": True,
        "eligible_rows": len(eligible),
        "planned_slots": len(_slots_for_anchor(anchor_index)),
        "selected_events": sum(event["status"] == "selected" for event in events),
        "events": events,
    }
    features: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for event in events:
        if event["status"] != "selected":
            continue
        feature: dict[str, Any] = {"event_id": event["event_id"]}
        for key, suffix, validator in (
            ("flow", "flow", validate_flow),
            ("buy", "wbs-buy", lambda body: validate_wbs(body, "BUY")),
            ("sell", "wbs-sell", lambda body: validate_wbs(body, "SELL")),
        ):
            body = _response_body(anchor_root, guard, f"{event['event_id']}/{suffix}")
            try:
                feature[key] = validator(body)
            except DesignError as exc:
                feature[key] = {"available": False, "reason": str(exc)}
        features.append(feature)

        proxy_body = _response_body(
            anchor_root, guard, f"{event['event_id']}/ohlcv"
        )
        try:
            proxy = validate_ohlcv(proxy_body, event)
        except DesignError as exc:
            proxy = {"available": False, "reason": str(exc)}
        outcome: dict[str, Any] = {"event_id": event["event_id"], "proxy": proxy}
        if event["execution_calibration"]:
            try:
                buys = validate_dex(
                    _response_body(
                        anchor_root, guard, f"{event['event_id']}/dex-buy"
                    ),
                    event,
                    "BUY",
                )
                sells = validate_dex(
                    _response_body(
                        anchor_root, guard, f"{event['event_id']}/dex-sell"
                    ),
                    event,
                    "SELL",
                )
                outcome["execution"] = {
                    "available": True,
                    **execution_outcome(event, buys, sells),
                }
            except DesignError as exc:
                outcome["execution"] = {"available": False, "reason": str(exc)}
        outcomes.append(outcome)
    feature_document = {
        "schema_version": 1,
        "anchor_index": anchor_index,
        "features": features,
    }
    outcome_document = {
        "schema_version": 1,
        "anchor_index": anchor_index,
        "outcomes": outcomes,
    }
    return panel, feature_document, outcome_document


def _collect_panel(
    program_root: Path,
    anchor_root: Path,
    anchor_index: int,
    guard: HistoricalPricingGuard,
    client: NansenClient,
    prerequisite_paths: list[Path],
) -> tuple[Path, dict[str, Any]]:
    panel_path = anchor_root / "derived/panel.json"
    seal_path = anchor_root / "seals/panel.json"
    if seal_path.is_file():
        _validate_stage_seal(
            anchor_root, guard, "panel", required_paths=[panel_path]
        )
        panel = _load_json(panel_path)
        _validate_panel_document(panel, anchor_index)
        return panel_path, panel
    anchor = ANCHORS[anchor_index]
    body = _call(
        program_root=program_root,
        anchor_root=anchor_root,
        guard=guard,
        client=client,
        logical_id=f"anchor-{anchor_index + 1:02d}/screener",
        method="POST",
        endpoint=SCREENER_ENDPOINT,
        payload=screener_payload(anchor),
        expected_credits=5,
    )
    try:
        eligible = validate_screener(body, anchor)
        events = select_anchor_events(_slots_for_anchor(anchor_index), eligible)
    except DesignError as exc:
        raise AnchorTerminal(f"screener evidence invalid: {exc}") from exc
    panel = {
        "schema_version": 1,
        "anchor_index": anchor_index,
        "anchor": anchor.isoformat(),
        "prefix_universe": True,
        "eligible_rows": len(eligible),
        "planned_slots": len(_slots_for_anchor(anchor_index)),
        "selected_events": sum(event["status"] == "selected" for event in events),
        "events": events,
    }
    _write_exact_json(panel_path, panel, kind="program_a_panel")
    _validate_panel_document(panel, anchor_index)
    _seal_stage(anchor_root, guard, "panel", [*prerequisite_paths, panel_path])
    return panel_path, panel


def _collect_features(
    program_root: Path,
    anchor_root: Path,
    anchor_index: int,
    guard: HistoricalPricingGuard,
    client: NansenClient,
    panel_path: Path,
    panel: dict[str, Any],
    prerequisite_paths: Iterable[Path] = (),
) -> tuple[Path, dict[str, Any]]:
    features_path = anchor_root / "derived/features.json"
    seal_path = anchor_root / "seals/features.json"
    if seal_path.is_file():
        _validate_stage_seal(
            anchor_root,
            guard,
            "features",
            required_paths=[panel_path, features_path],
        )
        document = _load_json(features_path)
        _validate_feature_document(document, anchor_index, panel)
        return features_path, document
    features = []
    for event in panel["events"]:
        if event["status"] != "selected":
            continue
        flow_body = _call(
            program_root=program_root,
            anchor_root=anchor_root,
            guard=guard,
            client=client,
            logical_id=f"{event['event_id']}/flow",
            method="POST",
            endpoint=FLOW_ENDPOINT,
            payload=flow_payload(event),
            expected_credits=5,
        )
        buy_body = _call(
            program_root=program_root,
            anchor_root=anchor_root,
            guard=guard,
            client=client,
            logical_id=f"{event['event_id']}/wbs-buy",
            method="POST",
            endpoint=WBS_ENDPOINT,
            payload=wbs_payload(event, "BUY"),
            expected_credits=5,
        )
        sell_body = _call(
            program_root=program_root,
            anchor_root=anchor_root,
            guard=guard,
            client=client,
            logical_id=f"{event['event_id']}/wbs-sell",
            method="POST",
            endpoint=WBS_ENDPOINT,
            payload=wbs_payload(event, "SELL"),
            expected_credits=5,
        )
        record: dict[str, Any] = {"event_id": event["event_id"]}
        for key, body, validator in (
            ("flow", flow_body, validate_flow),
            ("buy", buy_body, lambda value: validate_wbs(value, "BUY")),
            ("sell", sell_body, lambda value: validate_wbs(value, "SELL")),
        ):
            try:
                record[key] = validator(body)
            except DesignError as exc:
                record[key] = {"available": False, "reason": str(exc)}
        features.append(record)
    document = {
        "schema_version": 1,
        "anchor_index": anchor_index,
        "features": features,
    }
    _write_exact_json(features_path, document, kind="program_a_features")
    _validate_feature_document(document, anchor_index, panel)
    _seal_stage(
        anchor_root,
        guard,
        "features",
        [panel_path, *prerequisite_paths, features_path],
    )
    return features_path, document


def _collect_outcomes(
    program_root: Path,
    anchor_root: Path,
    anchor_index: int,
    guard: HistoricalPricingGuard,
    client: NansenClient,
    panel_path: Path,
    panel: dict[str, Any],
    features_path: Path,
    prerequisite_paths: Iterable[Path] = (),
) -> tuple[Path, dict[str, Any]]:
    outcomes_path = anchor_root / "derived/outcomes.json"
    seal_path = anchor_root / "seals/outcomes.json"
    if seal_path.is_file():
        _validate_stage_seal(
            anchor_root,
            guard,
            "outcomes",
            required_paths=[panel_path, features_path, outcomes_path],
        )
        document = _load_json(outcomes_path)
        _validate_outcome_document(document, anchor_index, panel)
        return outcomes_path, document
    outcomes = []
    for event in panel["events"]:
        if event["status"] != "selected":
            continue
        body = _call(
            program_root=program_root,
            anchor_root=anchor_root,
            guard=guard,
            client=client,
            logical_id=f"{event['event_id']}/ohlcv",
            method="POST",
            endpoint=OHLCV_ENDPOINT,
            payload=ohlcv_payload(event),
            expected_credits=1,
        )
        try:
            proxy = validate_ohlcv(body, event)
        except DesignError as exc:
            proxy = {"available": False, "reason": str(exc)}
        record: dict[str, Any] = {"event_id": event["event_id"], "proxy": proxy}
        if event["execution_calibration"]:
            buy_body = _call(
                program_root=program_root,
                anchor_root=anchor_root,
                guard=guard,
                client=client,
                logical_id=f"{event['event_id']}/dex-buy",
                method="POST",
                endpoint=DEX_ENDPOINT,
                payload=dex_payload(event, "BUY"),
                expected_credits=5,
            )
            sell_body = _call(
                program_root=program_root,
                anchor_root=anchor_root,
                guard=guard,
                client=client,
                logical_id=f"{event['event_id']}/dex-sell",
                method="POST",
                endpoint=DEX_ENDPOINT,
                payload=dex_payload(event, "SELL"),
                expected_credits=5,
            )
            try:
                buys = validate_dex(buy_body, event, "BUY")
                sells = validate_dex(sell_body, event, "SELL")
                record["execution"] = {
                    "available": True,
                    **execution_outcome(event, buys, sells),
                }
            except DesignError as exc:
                record["execution"] = {"available": False, "reason": str(exc)}
        outcomes.append(record)
    document = {
        "schema_version": 1,
        "anchor_index": anchor_index,
        "outcomes": outcomes,
    }
    _write_exact_json(outcomes_path, document, kind="program_a_outcomes")
    _validate_outcome_document(document, anchor_index, panel)
    _seal_stage(
        anchor_root,
        guard,
        "outcomes",
        [panel_path, features_path, *prerequisite_paths, outcomes_path],
    )
    return outcomes_path, document


def _validate_anchor_archive(
    program_root: Path, anchor_index: int, *, allow_contract_drift: bool = False
) -> Any:
    root = _anchor_root(program_root, anchor_index)
    max_calls, max_credits = _epoch_limits(anchor_index)
    guard = HistoricalPricingGuard(root, max_calls, max_credits)
    totals = guard.replay()
    observations = sorted(
        (root / "raw/contracts").glob("openapi-observation-*.json")
    )
    for observation_path in observations:
        observation = _load_json(observation_path)
        valid_keys = set(observation) == {
            "schema_version",
            "observed_at",
            "sha256",
            "expected_sha256",
            "matched",
        }
        matched = (
            valid_keys
            and observation.get("schema_version") == 1
            and observation.get("sha256") == FULL_OPENAPI_SHA256
            and observation.get("expected_sha256") == FULL_OPENAPI_SHA256
            and observation.get("matched") is True
        )
        sequence = observation_path.stem.rsplit("-", 1)[-1]
        drift_path = observation_path.with_name(f"openapi-drift-{sequence}.json")
        admitted_drift = (
            allow_contract_drift
            and valid_keys
            and observation.get("schema_version") == 1
            and observation.get("expected_sha256") == FULL_OPENAPI_SHA256
            and observation.get("matched") is False
            and isinstance(observation.get("sha256"), str)
            and observation.get("sha256") != FULL_OPENAPI_SHA256
            and drift_path.is_file()
            and not drift_path.is_symlink()
            and _sha256_file(drift_path) == observation.get("sha256")
        )
        if not matched and not admitted_drift:
            raise PortfolioError("public OpenAPI observation differs from frozen contract")
    panel: dict[str, Any] | None = None
    if (root / "seals/panel.json").is_file():
        panel_path = root / "derived/panel.json"
        _validate_stage_seal(root, guard, "panel", required_paths=[panel_path])
        panel = _load_json(panel_path)
        _validate_panel_document(panel, anchor_index)
    plan = _expected_request_plan(anchor_index, panel)
    expected_requests: set[Path] = set()
    expected_responses: set[Path] = set()
    expected_metadata: set[Path] = set()
    expected_pricing_derivations: set[Path] = set()
    logical_ids: set[str] = set()
    for entry in totals.entries:
        if entry.logical_request_id in logical_ids:
            raise PortfolioError("anchor ledger repeats a logical request")
        logical_ids.add(entry.logical_request_id)
        expected = plan.get(entry.logical_request_id)
        if expected is None:
            raise PortfolioError(
                f"anchor ledger contains request outside frozen plan: {entry.logical_request_id}"
            )
        method, endpoint, payload, expected_credits = expected
        if entry.endpoint != endpoint or entry.expected_credits != expected_credits:
            raise PortfolioError("anchor ledger request contract differs")
        if entry.request_artifact_sha256 is None:
            if entry.state not in {"reserved", "failed_before_pricing"}:
                raise PortfolioError("budget entry lacks its request artifact")
            continue
        base = root / "raw/nansen" / entry.reservation_id
        request = base / f"attempt-{entry.attempt_count}-request.json"
        if not request.is_file() or request.is_symlink() or _sha256_file(request) != entry.request_artifact_sha256:
            raise PortfolioError("request artifact differs from budget ledger")
        request_document = _load_json(request)
        if (
            request_document.get("method") != method
            or request_document.get("endpoint") != endpoint
            or request_document.get("payload") != payload
            or request_document.get("caller_request_id") != entry.logical_request_id
        ):
            raise PortfolioError("request artifact differs from frozen request plan")
        expected_requests.add(request.resolve())
        if entry.response_artifact_sha256 is not None:
            try:
                guard._verify_response_artifact(entry, entry.response_artifact_sha256)
            except BudgetError as exc:
                raise PortfolioError("response artifact differs from budget ledger") from exc
            response_path = base / f"attempt-{entry.attempt_count}-response.json"
            metadata_path = base / f"attempt-{entry.attempt_count}-response-metadata.json"
            expected_responses.add(response_path.resolve())
            expected_metadata.add(metadata_path.resolve())
            metadata = _load_json(metadata_path)
            if (
                entry.endpoint
                in {SCREENER_ENDPOINT, FLOW_ENDPOINT, WBS_ENDPOINT, DEX_ENDPOINT}
                and entry.credit_cost == 5
                and metadata.get("credit_cost") is None
            ):
                derivation = (
                    root
                    / "derived/pricing"
                    / f"{entry.reservation_id}-attempt-{entry.attempt_count}.json"
                )
                if derivation.is_symlink() or not derivation.is_file():
                    raise PortfolioError("missing historical pricing derivation")
                derived = _load_json(derivation)
                previous_remaining = derived.get("proof", {}).get(
                    "previous_remaining"
                )
                if (
                    set(derived)
                    != {
                        "schema_version",
                        "policy",
                        "openapi_sha256",
                        "endpoint",
                        "logical_request_id",
                        "reservation_id",
                        "attempt",
                        "response_metadata_sha256",
                        "raw",
                        "effective",
                        "proof",
                    }
                    or derived.get("schema_version") != 1
                    or derived.get("policy") != "historical-beta-missing-quote-v1"
                    or derived.get("openapi_sha256") != FULL_OPENAPI_SHA256
                    or derived.get("endpoint") != entry.endpoint
                    or derived.get("logical_request_id") != entry.logical_request_id
                    or derived.get("reservation_id") != entry.reservation_id
                    or derived.get("attempt") != entry.attempt_count
                    or derived.get("response_metadata_sha256")
                    != entry.response_artifact_sha256
                    or derived.get("raw")
                    != {
                        "credit_cost": None,
                        "credit_used": 5,
                        "credit_remaining": entry.credit_remaining,
                    }
                    or derived.get("effective")
                    != {
                        "credit_cost": 5,
                        "credit_used": 5,
                        "credit_remaining": entry.credit_remaining,
                    }
                    or not isinstance(previous_remaining, int)
                    or isinstance(previous_remaining, bool)
                    or derived.get("proof")
                    != {
                        "pinned_contract_cost": 5,
                        "reserved_cost": entry.expected_credits,
                        "previous_remaining": previous_remaining,
                        "remaining_delta": 5,
                    }
                    or entry.credit_remaining != previous_remaining - 5
                ):
                    raise PortfolioError("historical pricing derivation differs")
                expected_pricing_derivations.add(derivation.resolve())

    account_id = f"anchor-{anchor_index + 1:02d}/account"
    account_entries = [
        entry for entry in totals.entries if entry.logical_request_id == account_id
    ]
    account_validation = root / "derived/account-validation.json"
    account_derivation = root / "derived/account-baseline.json"
    if account_entries and account_entries[0].state == "confirmed_zero":
        account = account_entries[0]
        body = _response_body(root, guard, account_id)
        metadata_path = (
            root
            / "raw/nansen"
            / account.reservation_id
            / f"attempt-{account.attempt_count}-response-metadata.json"
        )
        metadata = _load_json(metadata_path)
        _, prior_credits = _completed_epoch_totals(program_root, anchor_index)
        minimum_remaining = PROVED_BALANCE - prior_credits
        plan_name = body.get("plan") if isinstance(body, dict) else None
        body_remaining = (
            body.get("credits_remaining") if isinstance(body, dict) else None
        )
        passed = (
            200 <= metadata.get("status_code", -1) < 300
            and metadata.get("body_parse_status") == "json_object"
            and isinstance(plan_name, str)
            and plan_name in {"free", "pro"}
            and isinstance(body_remaining, int)
            and not isinstance(body_remaining, bool)
            and body_remaining >= minimum_remaining
            and metadata.get("credit_header_errors") == []
            and metadata.get("credit_cost") == 0
            and metadata.get("credit_used") in {None, 0}
            and metadata.get("credit_remaining") in {None, body_remaining}
            and account.credit_remaining == body_remaining
        )
        expected_validation = {
            "schema_version": 1,
            "policy": "program-a-account-minimum-v1",
            "logical_request_id": account_id,
            "openapi_sha256": FULL_OPENAPI_SHA256,
            "response_metadata_sha256": account.response_artifact_sha256,
            "plan": plan_name,
            "body_credits_remaining": body_remaining,
            "header_credit_cost": metadata.get("credit_cost"),
            "header_credit_used": metadata.get("credit_used"),
            "header_credit_remaining": metadata.get("credit_remaining"),
            "minimum_remaining": minimum_remaining,
            "ledger_provider_remaining": account.credit_remaining,
            "passed": passed,
        }
        if account_validation.is_file() and _load_json(account_validation) != expected_validation:
            raise PortfolioError("account minimum-validation derivation differs")
        if len(totals.entries) > 1 and (
            not account_validation.is_file() or not passed
        ):
            raise PortfolioError("paid request followed an invalid account minimum proof")

        fallback_used = metadata.get("credit_used") is None or metadata.get(
            "credit_remaining"
        ) is None
        if fallback_used:
            if account_derivation.is_symlink() or not account_derivation.is_file():
                raise PortfolioError("account fallback derivation is missing")
            derived = _load_json(account_derivation)
            written_at = derived.get("artifact_written_at")
            if (
                not isinstance(written_at, str)
                or derived
                != {
                    "schema_version": 1,
                    "rule_version": "account-baseline-v2",
                    "openapi_sha256": FULL_OPENAPI_SHA256,
                    "response_metadata_path": metadata_path.relative_to(root).as_posix(),
                    "response_metadata_sha256": account.response_artifact_sha256,
                    "body": {
                        "plan": plan_name,
                        "credits_remaining": body_remaining,
                    },
                    "observed": {
                        "credit_cost": metadata.get("credit_cost"),
                        "credit_used": metadata.get("credit_used"),
                        "credit_remaining": metadata.get("credit_remaining"),
                    },
                    "effective": {
                        "credit_cost": 0,
                        "credit_used": 0,
                        "credit_remaining": body_remaining,
                    },
                    "artifact_written_at": written_at,
                }
            ):
                raise PortfolioError("account fallback derivation differs")
        elif account_derivation.exists():
            raise PortfolioError("exact-header account has an unexpected fallback derivation")
    elif account_validation.exists() or account_derivation.exists():
        raise PortfolioError("account derivation exists without a confirmed account baseline")
    actual_requests = {
        path.resolve() for path in root.glob("raw/nansen/*/attempt-*-request.json")
    }
    actual_responses = {
        path.resolve() for path in root.glob("raw/nansen/*/attempt-*-response.json")
    }
    actual_metadata = {
        path.resolve()
        for path in root.glob("raw/nansen/*/attempt-*-response-metadata.json")
    }
    actual_pricing_derivations = {
        path.resolve() for path in pricing_derivation_paths(root)
    }
    if (
        actual_requests != expected_requests
        or actual_responses != expected_responses
        or actual_metadata != expected_metadata
        or actual_pricing_derivations != expected_pricing_derivations
    ):
        raise PortfolioError("anchor archive does not exactly match its ledgers")

    stage_names = {
        path.stem for path in (root / "seals").glob("*.json")
    }
    if not stage_names <= {"panel", "features", "outcomes", "terminal"}:
        raise PortfolioError("anchor contains an unexpected stage seal")
    for stage in sorted(stage_names):
        _validate_stage_seal(
            root,
            guard,
            stage,
            require_current_head=stage == "terminal",
        )
    terminal = root / "seals/terminal.json"
    if terminal.is_file():
        sealed_paths = {
            record["path"]
            for stage in stage_names
            for record in _load_json(root / "seals" / f"{stage}.json").get(
                "artifacts", []
            )
        }
        if any(
            observation.relative_to(root).as_posix() not in sealed_paths
            for observation in observations
        ):
            raise PortfolioError("terminal anchor has an unsealed OpenAPI observation")
    if terminal.is_file() and _load_json(terminal).get("status") == "complete":
        if stage_names != {"panel", "features", "outcomes", "terminal"}:
            raise PortfolioError("complete anchor lacks its full sealed stage chain")
        if logical_ids != set(plan):
            raise PortfolioError("complete anchor request set differs from frozen plan")
        expected_panel, expected_features, expected_outcomes = _recompute_complete_anchor(
            root, anchor_index, guard
        )
        if _load_json(root / "derived/panel.json") != expected_panel:
            raise PortfolioError("sealed panel differs from raw evidence replay")
        if _load_json(root / "derived/features.json") != expected_features:
            raise PortfolioError("sealed features differ from raw evidence replay")
        if _load_json(root / "derived/outcomes.json") != expected_outcomes:
            raise PortfolioError("sealed outcomes differ from raw evidence replay")
    elif terminal.is_file():
        terminal_seal = _load_json(terminal)
        expected_ids = {
            f"anchor-{anchor_index + 1:02d}/account",
            f"anchor-{anchor_index + 1:02d}/screener",
        }
        if stage_names != {"terminal"} or logical_ids != expected_ids:
            raise PortfolioError(
                "unscorable anchor does not match the frozen screener-failure path"
            )
        try:
            validate_screener(
                _response_body(
                    root,
                    guard,
                    f"anchor-{anchor_index + 1:02d}/screener",
                ),
                ANCHORS[anchor_index],
            )
        except DesignError as exc:
            expected_reason = f"screener evidence invalid: {exc}"
        else:
            raise PortfolioError("unscorable anchor screener evidence is valid")
        reason_path = root / "derived/terminal-reason.json"
        expected_reason_document = {
            "schema_version": 1,
            "status": "unscorable",
            "reason": expected_reason,
        }
        if (
            terminal_seal.get("reason") != expected_reason
            or _load_json(reason_path) != expected_reason_document
        ):
            raise PortfolioError("unscorable anchor reason differs from raw evidence replay")
        _validate_stage_seal(
            root,
            guard,
            "terminal",
            required_paths=[reason_path],
            expected_status="unscorable",
            require_current_head=True,
        )
    return totals


def _run_anchor(
    program_root: Path,
    anchor_index: int,
    client: NansenClient,
) -> None:
    root = _anchor_root(program_root, anchor_index)
    terminal = root / "seals/terminal.json"
    max_calls, max_credits = _epoch_limits(anchor_index)
    guard = HistoricalPricingGuard(root, max_calls, max_credits)
    if terminal.is_file():
        _validate_anchor_archive(program_root, anchor_index)
        terminal_seal = _validate_stage_seal(
            root, guard, "terminal", require_current_head=True
        )
        if terminal_seal["status"] == "complete":
            panel_path = root / "derived/panel.json"
            features_path = root / "derived/features.json"
            outcomes_path = root / "derived/outcomes.json"
            panel, features, outcomes = _recompute_complete_anchor(
                root, anchor_index, guard
            )
            if _load_json(panel_path) != panel:
                raise PortfolioError("sealed panel differs from archived screener evidence")
            if _load_json(features_path) != features:
                raise PortfolioError("sealed features differ from archived provider evidence")
            if _load_json(outcomes_path) != outcomes:
                raise PortfolioError("sealed outcomes differ from archived provider evidence")
            for stage, paths in (
                ("panel", [panel_path]),
                ("features", [panel_path, features_path]),
                ("outcomes", [panel_path, features_path, outcomes_path]),
            ):
                _validate_stage_seal(root, guard, stage, required_paths=paths)
        return
    _refuse_prior_contract_failure(root)
    try:
        guard.reconcile_inflight()
    except BudgetError as exc:
        raise ProgramFatal(
            "response artifact or budget-ledger reconciliation failed before resume"
        ) from exc
    _refuse_prior_provider_failure(root, guard)
    if guard.replay().halted_reason is not None:
        raise ProgramFatal(
            f"anchor {anchor_index + 1} has a terminal budget halt: "
            f"{guard.replay().halted_reason}"
        )
    _validate_anchor_archive(program_root, anchor_index)
    prerequisite_paths: list[Path] = []
    try:
        stages_missing = any(
            not (root / "seals" / f"{stage}.json").is_file()
            for stage in ("panel", "features", "outcomes")
        )
        if stages_missing:
            openapi_observation = _fresh_openapi_check(program_root, root, client)
            prerequisite_paths.extend(
                sorted(
                    openapi_observation.parent.glob("openapi-observation-*.json")
                )
            )
            _, prior_credits = _completed_epoch_totals(program_root, anchor_index)
            minimum_remaining = PROVED_BALANCE - prior_credits
            _call(
                program_root=program_root,
                anchor_root=root,
                guard=guard,
                client=client,
                logical_id=f"anchor-{anchor_index + 1:02d}/account",
                method="GET",
                endpoint=ACCOUNT_ENDPOINT,
                payload=None,
                expected_credits=0,
                minimum_remaining=minimum_remaining,
            )
            account_validation = root / "derived/account-validation.json"
            if not account_validation.is_file():
                raise ProgramFatal("account preflight lacks its sealed minimum proof")
            prerequisite_paths.append(account_validation)
            account_derivation = root / "derived/account-baseline.json"
            if account_derivation.is_file():
                prerequisite_paths.append(account_derivation)
        panel_path, panel = _collect_panel(
            program_root,
            root,
            anchor_index,
            guard,
            client,
            prerequisite_paths,
        )
        features_path, _ = _collect_features(
            program_root,
            root,
            anchor_index,
            guard,
            client,
            panel_path,
            panel,
            prerequisite_paths,
        )
        outcomes_path, _ = _collect_outcomes(
            program_root,
            root,
            anchor_index,
            guard,
            client,
            panel_path,
            panel,
            features_path,
            prerequisite_paths,
        )
        _seal_stage(
            root,
            guard,
            "terminal",
            [*prerequisite_paths, panel_path, features_path, outcomes_path],
            status="complete",
        )
    except AnchorTerminal as exc:
        _terminalize_anchor(root, guard, str(exc), prerequisite_paths)


def _terminal_anchor_status(program_root: Path, anchor_index: int) -> str | None:
    seal = _anchor_root(program_root, anchor_index) / "seals/terminal.json"
    if not seal.is_file():
        return None
    return _load_json(seal).get("status")


def _final_records(program_root: Path) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    result = []
    for anchor_index in range(len(ANCHORS)):
        root = _anchor_root(program_root, anchor_index)
        if _terminal_anchor_status(program_root, anchor_index) != "complete":
            for slot in _slots_for_anchor(anchor_index):
                result.append(
                    (
                        {
                            "event_id": slot.event_id,
                            "anchor": slot.anchor.isoformat(),
                            "chain": slot.chain,
                            "stratum": slot.stratum,
                            "status": "unavailable",
                            "reason": "anchor_unscorable",
                        },
                        {},
                        {"available": False, "reason": "anchor_unscorable"},
                    )
                )
            continue
        max_calls, max_credits = _epoch_limits(anchor_index)
        guard = HistoricalPricingGuard(root, max_calls, max_credits)
        _validate_stage_seal(
            root, guard, "terminal", expected_status="complete", require_current_head=True
        )
        panel, feature_document, outcome_document = _recompute_complete_anchor(
            root, anchor_index, guard
        )
        if _load_json(root / "derived/panel.json") != panel:
            raise PortfolioError("final panel differs from raw evidence replay")
        if _load_json(root / "derived/features.json") != feature_document:
            raise PortfolioError("final features differ from raw evidence replay")
        if _load_json(root / "derived/outcomes.json") != outcome_document:
            raise PortfolioError("final outcomes differ from raw evidence replay")
        features = {record["event_id"]: record for record in feature_document["features"]}
        outcomes = {record["event_id"]: record for record in outcome_document["outcomes"]}
        events = {event["event_id"]: event for event in panel["events"]}
        for slot in _slots_for_anchor(anchor_index):
            event = events[slot.event_id]
            if event["status"] != "selected":
                result.append(
                    (
                        event,
                        {},
                        {"available": False, "reason": event.get("reason")},
                    )
                )
                continue
            feature = features[event["event_id"]]
            proxy = outcomes[event["event_id"]]["proxy"]
            result.append((event, feature, proxy))
    if len(result) != len(PLANNED_SLOTS):
        raise PortfolioError("final opportunity denominator differs from 400 planned slots")
    if [event["event_id"] for event, _, _ in result] != [
        slot.event_id for slot in PLANNED_SLOTS
    ]:
        raise PortfolioError("final opportunity IDs differ from the frozen schedule")
    return result


def _liquidity_bucket(event: dict[str, Any]) -> str:
    liquidity = event.get("liquidity_usd")
    if not isinstance(liquidity, (int, float)) or isinstance(liquidity, bool):
        return "unavailable"
    if liquidity < 500_000:
        return "250k_to_500k"
    if liquidity < 2_000_000:
        return "500k_to_2m"
    return "2m_plus"


def _calibration_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    available = [row for row in rows if row["execution_available"]]
    comparable = [
        row
        for row in available
        if isinstance(row.get("execution_base_return"), (int, float))
        and isinstance(row.get("proxy_base_return"), (int, float))
    ]
    return {
        "planned": len(rows),
        "selected": sum(row["selected"] for row in rows),
        "execution_available": len(available),
        "full_round_trip": sum(row["full_round_trip"] for row in rows),
        "proxy_available": sum(row["proxy_available"] for row in rows),
        "comparable_returns": len(comparable),
        "mean_execution_minus_proxy_base_return": (
            None
            if not comparable
            else sum(
                row["execution_base_return"] - row["proxy_base_return"]
                for row in comparable
            )
            / len(comparable)
        ),
    }


def _execution_calibration(program_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for slot in (item for item in PLANNED_SLOTS if item.execution_calibration):
        root = _anchor_root(program_root, slot.anchor_index)
        row: dict[str, Any] = {
            "event_id": slot.event_id,
            "anchor": slot.anchor.isoformat(),
            "chain": slot.chain,
            "stratum": slot.stratum,
            "liquidity_bucket": "unavailable",
            "selected": False,
            "proxy_available": False,
            "execution_available": False,
            "full_round_trip": False,
            "proxy_base_return": None,
            "execution_base_return": None,
            "entry_fill_ratio": None,
            "exit_fill_ratio": None,
            "reason": "anchor_unscorable",
        }
        if _terminal_anchor_status(program_root, slot.anchor_index) == "complete":
            max_calls, max_credits = _epoch_limits(slot.anchor_index)
            guard = HistoricalPricingGuard(root, max_calls, max_credits)
            panel, _, outcomes = _recompute_complete_anchor(
                root, slot.anchor_index, guard
            )
            event = next(item for item in panel["events"] if item["event_id"] == slot.event_id)
            if event["status"] == "selected":
                outcome = next(
                    item for item in outcomes["outcomes"] if item["event_id"] == slot.event_id
                )
                proxy = outcome["proxy"]
                execution = outcome["execution"]
                row.update(
                    {
                        "liquidity_bucket": _liquidity_bucket(event),
                        "selected": True,
                        "proxy_available": proxy.get("available") is True,
                        "execution_available": execution.get("available") is True,
                        "full_round_trip": (
                            execution.get("available") is True
                            and execution.get("entry_fill_ratio") == 1.0
                            and execution.get("exit_fill_ratio") == 1.0
                            and isinstance(execution.get("base_return"), (int, float))
                        ),
                        "proxy_base_return": (
                            proxy.get("base_return")
                            if proxy.get("available") is True
                            else None
                        ),
                        "execution_base_return": (
                            execution.get("base_return")
                            if execution.get("available") is True
                            else None
                        ),
                        "entry_fill_ratio": execution.get("entry_fill_ratio"),
                        "exit_fill_ratio": execution.get("exit_fill_ratio"),
                        "reason": (
                            None
                            if execution.get("available") is True
                            else execution.get("reason", "execution_unavailable")
                        ),
                    }
                )
            else:
                row["reason"] = event.get("reason", "slot_unavailable")
        rows.append(row)
    if len(rows) != len(ANCHORS) or len({row["event_id"] for row in rows}) != len(rows):
        raise PortfolioError("execution calibration schedule differs from 65 events")

    def grouped(field: str) -> dict[str, Any]:
        values = sorted({str(row[field]) for row in rows})
        return {
            value: _calibration_group(
                [row for row in rows if str(row[field]) == value]
            )
            for value in values
        }

    return {
        "schema_version": 1,
        "program_id": PROGRAM_A_ID,
        "discovery_only": True,
        "excluded_from_candidate_ranking": True,
        "overall": _calibration_group(rows),
        "by_chain": grouped("chain"),
        "by_stratum": grouped("stratum"),
        "by_liquidity_bucket": grouped("liquidity_bucket"),
        "events": rows,
    }


def _program_totals(
    program_root: Path,
    *,
    allow_contract_drift: bool = False,
    enforce_ceiling: bool = True,
) -> tuple[int, int]:
    calls = 0
    credits = 0
    for anchor_index in range(len(ANCHORS)):
        root = _anchor_root(program_root, anchor_index)
        if not root.exists():
            continue
        totals = _validate_anchor_archive(
            program_root,
            anchor_index,
            allow_contract_drift=allow_contract_drift,
        )
        calls += totals.calls
        credits += totals.credits
    if enforce_ceiling and (
        calls > PROGRAM_A_MAX_CALLS or credits > PROGRAM_A_MAX_CREDITS
    ):
        raise PortfolioError("program-A cumulative budget ceiling exceeded")
    return calls, credits


def _finalization_intent(program_root: Path, name: str) -> dict[str, Any]:
    path = program_root / "derived" / f"{name}-intent.json"
    if path.is_file():
        intent = _load_json(path)
        if (
            intent.get("schema_version") != 1
            or intent.get("name") != name
            or not isinstance(intent.get("recorded_at"), str)
        ):
            raise PortfolioError(f"{name} intent is invalid")
        return intent
    intent = {
        "schema_version": 1,
        "name": name,
        "recorded_at": utc_text(datetime.now(timezone.utc)),
    }
    _write_exact_json(path, intent, kind="program_a_finalization_intent")
    return intent


def _completion_report(
    terminal: dict[str, Any], ranking: dict[str, Any], calibration: dict[str, Any]
) -> bytes:
    return (
        "# Historical theory discovery A\n\n"
        f"Status: **{terminal['stage']} discovery**. This result is not confirmatory.\n\n"
        f"- Complete anchors: {terminal['complete_anchors']} / {len(ANCHORS)}\n"
        f"- Unscorable anchors: {terminal['unscorable_anchors']}\n"
        f"- Authenticated attempts: {terminal['authenticated_attempts']} / {PROGRAM_A_MAX_CALLS}\n"
        f"- Billable credits: {terminal['billable_credits']} / {PROGRAM_A_MAX_CREDITS}\n"
        f"- Selected opportunities: {ranking['selected_opportunities']} / {len(PLANNED_SLOTS)}\n"
        f"- Execution calibrations available: {calibration['overall']['execution_available']} / {len(ANCHORS)}\n"
        f"- Program-B candidate IDs: {', '.join(ranking['program_b_candidate_ids'])}\n"
        "- Historical exchange flow is an H5 analogue, not exact exchange-inventory evidence.\n"
    ).encode("utf-8")


def _terminalize_program(program_root: Path, reason: str) -> dict[str, Any]:
    manifest_path = program_root / "program.json"
    manifest = validate_program_a(manifest_path, validate_live_runtime=True)
    if manifest["stage"] != "preregistered":
        return manifest
    if not isinstance(reason, str) or not reason:
        raise PortfolioError("program halt reason must be nonempty")
    intent = _finalization_intent(program_root, "program-halt")
    reason_path = program_root / "derived/program-halt.json"
    if reason_path.is_file():
        reason_document = _load_json(reason_path)
        if (
            reason_document.get("schema_version") != 1
            or reason_document.get("status") != "unscorable"
            or not isinstance(reason_document.get("reason"), str)
            or not reason_document["reason"]
        ):
            raise PortfolioError("program halt artifact is invalid")
        reason = reason_document["reason"]
    else:
        _write_exact_json(
            reason_path,
            {"schema_version": 1, "status": "unscorable", "reason": reason},
            kind="program_a_halt_reason",
        )
    calls, credits = _program_totals(
        program_root,
        allow_contract_drift=True,
        enforce_ceiling=False,
    )
    report = (
        "# Historical theory discovery A\n\n"
        "Status: **unscorable**. No later provider request is authorized.\n\n"
        f"- Terminal reason: {reason}\n"
        f"- Authenticated attempts reserved: {calls} / {PROGRAM_A_MAX_CALLS}\n"
        f"- Billable credits reserved or used: {credits} / {PROGRAM_A_MAX_CREDITS}\n"
    ).encode("utf-8")
    report_path = _write_exact_bytes(
        program_root / "REPORT.md", report, kind="program_a_report"
    )
    evidence_paths = [
        path
        for path in sorted((program_root / "anchors").glob("**/*"))
        if path.is_file()
        and not path.is_symlink()
        and ".conflicts" not in path.parts
        and not path.name.endswith(".lock")
    ]
    artifacts = sorted(
        [
            _artifact_record(program_root, reason_path),
            _artifact_record(program_root, report_path),
            _artifact_record(
                program_root, program_root / "derived/program-halt-intent.json"
            ),
            *[_artifact_record(program_root, path) for path in evidence_paths],
        ],
        key=lambda record: record["path"],
    )
    terminal = {
        "schema_version": 1,
        "kind": "fatal",
        "stage": "unscorable",
        "terminal_reason": reason,
        "recorded_at": intent["recorded_at"],
        "authenticated_attempts": calls,
        "billable_credits": credits,
        "report_path": report_path.relative_to(program_root).as_posix(),
        "report_sha256": _sha256_file(report_path),
        "artifacts": artifacts,
    }
    terminal_path = _write_exact_json(
        program_root / "seals/final.json",
        terminal,
        kind="program_a_terminal_seal",
    )
    updated = dict(manifest)
    updated.update(
        {
            "stage": "unscorable",
            "terminal_reason": reason,
            "final_seal_path": terminal_path.relative_to(program_root).as_posix(),
            "final_seal_sha256": _sha256_file(terminal_path),
            "report_path": report_path.relative_to(program_root).as_posix(),
            "report_sha256": _sha256_file(report_path),
        }
    )
    atomic_replace_bytes(manifest_path, canonical_json_bytes(updated))
    return validate_program_a(manifest_path, validate_live_runtime=True)


def _completion_documents(
    program_root: Path, statuses: list[str | None]
) -> tuple[dict[str, Any], dict[str, Any], int, int, str, str | None]:
    records = _final_records(program_root)
    ranking = score_candidates(records)
    calibration = _execution_calibration(program_root)
    calls, credits = _program_totals(program_root)
    selected = sum(event.get("status") == "selected" for event, _, _ in records)
    proxy_available = sum(outcome.get("available") is True for _, _, outcome in records)
    support = {
        "complete_anchor_gate": statuses.count("complete") >= 59,
        "selected_opportunity_gate": selected >= 320,
        "selected_proxy_coverage_gate": (
            selected > 0 and proxy_available / selected >= 0.90
        ),
        "complete_anchors": statuses.count("complete"),
        "selected_opportunities": selected,
        "proxy_outcomes_available": proxy_available,
        "selected_proxy_coverage": (
            0.0 if selected == 0 else proxy_available / selected
        ),
    }
    support["passed"] = all(
        support[key]
        for key in (
            "complete_anchor_gate",
            "selected_opportunity_gate",
            "selected_proxy_coverage_gate",
        )
    )
    final_stage = "completed" if support["passed"] else "unscorable"
    terminal_reason = None if support["passed"] else "insufficient_program_a_support"
    ranking["authenticated_attempts"] = calls
    ranking["billable_credits"] = credits
    ranking["complete_anchors"] = statuses.count("complete")
    ranking["unscorable_anchors"] = statuses.count("unscorable")
    ranking["selected_opportunities"] = selected
    ranking["program_support"] = support
    return ranking, calibration, calls, credits, final_stage, terminal_reason


def _finalize(program_root: Path) -> dict[str, Any]:
    manifest_path = program_root / "program.json"
    manifest = validate_program_a(manifest_path, validate_live_runtime=True)
    if manifest["stage"] != "preregistered":
        return manifest
    statuses = [_terminal_anchor_status(program_root, index) for index in range(len(ANCHORS))]
    if any(status is None for status in statuses):
        raise PortfolioError("cannot finalize before every anchor is terminal")
    ranking, calibration, calls, credits, final_stage, terminal_reason = (
        _completion_documents(program_root, statuses)
    )
    support = ranking["program_support"]
    ranking_path = _write_exact_json(
        program_root / "derived/candidate-ranking.json",
        ranking,
        kind="program_a_candidate_ranking",
    )
    calibration_path = _write_exact_json(
        program_root / "derived/execution-calibration.json",
        calibration,
        kind="program_a_execution_calibration",
    )
    intent = _finalization_intent(program_root, "completion")
    terminal = {
        "schema_version": 1,
        "stage": final_stage,
        "terminal_reason": terminal_reason,
        "recorded_at": intent["recorded_at"],
        "ranking_path": ranking_path.relative_to(program_root).as_posix(),
        "ranking_sha256": _sha256_file(ranking_path),
        "calibration_path": calibration_path.relative_to(program_root).as_posix(),
        "calibration_sha256": _sha256_file(calibration_path),
        "authenticated_attempts": calls,
        "billable_credits": credits,
        "complete_anchors": statuses.count("complete"),
        "unscorable_anchors": statuses.count("unscorable"),
        "program_support": support,
    }
    report = _completion_report(terminal, ranking, calibration)
    report_path = _write_exact_bytes(
        program_root / "REPORT.md", report, kind="program_a_report"
    )
    terminal["report_path"] = report_path.relative_to(program_root).as_posix()
    terminal["report_sha256"] = _sha256_file(report_path)
    terminal["terminal_anchor_seals"] = [
        _artifact_record(
            program_root,
            _anchor_root(program_root, anchor_index) / "seals/terminal.json",
        )
        for anchor_index in range(len(ANCHORS))
    ]
    terminal["artifacts"] = sorted(
        [
            _artifact_record(program_root, ranking_path),
            _artifact_record(program_root, calibration_path),
            _artifact_record(program_root, report_path),
            _artifact_record(
                program_root, program_root / "derived/completion-intent.json"
            ),
            *terminal["terminal_anchor_seals"],
        ],
        key=lambda record: record["path"],
    )
    terminal_path = _write_exact_json(
        program_root / "seals/final.json",
        terminal,
        kind="program_a_terminal_seal",
    )
    updated = dict(manifest)
    updated.update(
        {
            "stage": final_stage,
            "terminal_reason": terminal_reason,
            "final_seal_path": terminal_path.relative_to(program_root).as_posix(),
            "final_seal_sha256": _sha256_file(terminal_path),
            "report_path": report_path.relative_to(program_root).as_posix(),
            "report_sha256": _sha256_file(report_path),
        }
    )
    atomic_replace_bytes(manifest_path, canonical_json_bytes(updated))
    return validate_program_a(manifest_path, validate_live_runtime=True)


def run_program_a(manifest_path: Path, *, api_key: str | None = None) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = validate_program_a(manifest_path, validate_live_runtime=True)
    if manifest["stage"] != "preregistered":
        return manifest
    _assert_preregistration_committed(manifest_path)
    _cohort_automation_inactive()
    halt_reason_path = manifest_path.parent / "derived/program-halt.json"
    halt_intent_path = manifest_path.parent / "derived/program-halt-intent.json"
    if halt_reason_path.is_file():
        reason = _load_json(halt_reason_path).get("reason")
        if not isinstance(reason, str) or not reason:
            raise PortfolioError("interrupted program halt reason is invalid")
        return _terminalize_program(manifest_path.parent, reason)
    if halt_intent_path.is_file():
        return _terminalize_program(
            manifest_path.parent, "interrupted program halt transition"
        )
    if datetime.now(timezone.utc) >= REQUEST_START_CUTOFF:
        return _terminalize_program(
            manifest_path.parent, "program-A request-start cutoff reached"
        )
    client = NansenClient(api_key=api_key)
    with _provider_lock():
        try:
            for anchor_index in range(len(ANCHORS)):
                if datetime.now(timezone.utc) >= REQUEST_START_CUTOFF:
                    raise ProgramFatal("program-A request-start cutoff reached")
                _run_anchor(manifest_path.parent, anchor_index, client)
            if all(
                _terminal_anchor_status(manifest_path.parent, index) is not None
                for index in range(len(ANCHORS))
            ):
                return _finalize(manifest_path.parent)
        except ProgramFatal as exc:
            return _terminalize_program(manifest_path.parent, str(exc))
    return validate_program_a(manifest_path, validate_live_runtime=True)


def check_program_a(manifest_path: Path) -> dict[str, Any]:
    manifest = validate_program_a(manifest_path, validate_live_runtime=True)
    program_root = manifest_path.resolve().parent
    final_document = (
        _load_json(program_root / manifest["final_seal_path"])
        if manifest["stage"] in {"completed", "unscorable"}
        else None
    )
    allow_contract_drift = (
        isinstance(final_document, dict) and final_document.get("kind") == "fatal"
    )
    calls = 0
    credits = 0
    terminal = 0
    for anchor_index in range(len(ANCHORS)):
        root = _anchor_root(program_root, anchor_index)
        if not root.exists():
            continue
        totals = _validate_anchor_archive(
            program_root,
            anchor_index,
            allow_contract_drift=allow_contract_drift,
        )
        seal_path = root / "seals/terminal.json"
        if seal_path.is_file():
            terminal += 1
        calls += totals.calls
        credits += totals.credits
    if (
        calls > PROGRAM_A_MAX_CALLS or credits > PROGRAM_A_MAX_CREDITS
    ) and not allow_contract_drift:
        raise PortfolioError("program-A cumulative budget ceiling exceeded")
    if manifest["stage"] in {"completed", "unscorable"}:
        assert final_document is not None
        final = final_document
        if final.get("kind") == "fatal":
            if final.get("authenticated_attempts") != calls or final.get(
                "billable_credits"
            ) != credits:
                raise PortfolioError("fatal program seal totals differ from replay")
        else:
            if terminal != len(ANCHORS):
                raise PortfolioError("finalized program does not have 65 terminal anchors")
            statuses = [
                _terminal_anchor_status(program_root, index)
                for index in range(len(ANCHORS))
            ]
            ranking, calibration, replay_calls, replay_credits, stage, reason = (
                _completion_documents(program_root, statuses)
            )
            if stage != manifest["stage"] or reason != manifest.get("terminal_reason"):
                raise PortfolioError("program terminal classification differs on replay")
            ranking_path = program_root / final.get("ranking_path", "")
            calibration_path = program_root / final.get("calibration_path", "")
            if (
                final.get("ranking_path") != "derived/candidate-ranking.json"
                or final.get("calibration_path") != "derived/execution-calibration.json"
                or final.get("report_path") != "REPORT.md"
                or final.get("ranking_sha256") != _sha256_file(ranking_path)
                or final.get("calibration_sha256") != _sha256_file(calibration_path)
                or final.get("report_sha256")
                != _sha256_file(program_root / "REPORT.md")
            ):
                raise PortfolioError("program final output identity differs")
            if _load_json(ranking_path) != ranking:
                raise PortfolioError("candidate ranking differs on deterministic replay")
            if _load_json(calibration_path) != calibration:
                raise PortfolioError("execution calibration differs on deterministic replay")
            if replay_calls != calls or replay_credits != credits:
                raise PortfolioError("program terminal totals differ on replay")
            expected_report = _completion_report(final, ranking, calibration)
            if (program_root / manifest["report_path"]).read_bytes() != expected_report:
                raise PortfolioError("program report differs on deterministic replay")
            expected_terminal_seals = [
                _artifact_record(
                    program_root,
                    _anchor_root(program_root, anchor_index)
                    / "seals/terminal.json",
                )
                for anchor_index in range(len(ANCHORS))
            ]
            expected_artifacts = sorted(
                [
                    _artifact_record(program_root, ranking_path),
                    _artifact_record(program_root, calibration_path),
                    _artifact_record(program_root, program_root / "REPORT.md"),
                    _artifact_record(
                        program_root,
                        program_root / "derived/completion-intent.json",
                    ),
                    *expected_terminal_seals,
                ],
                key=lambda record: record["path"],
            )
            if (
                final.get("terminal_anchor_seals") != expected_terminal_seals
                or final.get("artifacts") != expected_artifacts
            ):
                raise PortfolioError("program final claim chain differs")
    return {
        "stage": manifest["stage"],
        "terminal_anchors": terminal,
        "authenticated_attempts": calls,
        "billable_credits": credits,
    }
