from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import canonical_json_bytes, write_bytes_once, write_json_once


class CohortSchemaError(RuntimeError):
    """Raised when a cohort program cannot be trusted or initialized safely."""


PROGRAM_VERSION = "prospective-multi-cycle-cohort-v1"
CYCLE_COUNT = 32
PANEL_SIZE = 5
CYCLE_SPACING = timedelta(hours=44)
MAX_CYCLE_CREDITS = 56
MAX_CYCLE_ATTEMPTS = 57
MAX_PROGRAM_CREDITS = CYCLE_COUNT * MAX_CYCLE_CREDITS
MAX_PROGRAM_ATTEMPTS = CYCLE_COUNT * MAX_CYCLE_ATTEMPTS
DESIGN_PATH = "docs/superpowers/specs/2026-08-18-prospective-multi-cycle-cohort-v1.md"
CONTRACT_SOURCE_PATH = (
    "research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/"
    "adopted/nansen-openapi.json"
)
EXPECTED_CONTRACT_SHA256 = (
    "d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548"
)
STRATEGY_SOURCE_PATH = (
    "research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json"
)
EXPECTED_STRATEGY_SHA256 = (
    "5d5859be0c03bd1f786436ad199aac48de9c6688883392836796c0f8e3ccf6d5"
)
COMPARATOR_PATH = "contracts/frozen-comparator-definitions.json"
IMPLEMENTATION_PATH = "contracts/protocol-implementation.json"
WBS_LABELS = ("Fund", "Smart Trader", "30D Smart Trader")
PRIMARY_RULE_ID = "buyer-breadth-exchange-comovement-v1+distribution-veto"
STRATA = (
    "early_accumulation",
    "middle_accumulation",
    "momentum_accumulation",
    "neutral_control",
    "distribution_control",
)
CYCLE_STAGES = {
    "planned",
    "universe_sealed",
    "features_sealed",
    "decisions_sealed",
    "outcome_sealed",
    "unscorable",
}
TERMINAL_CYCLE_STAGES = {"outcome_sealed", "unscorable"}


@dataclass(frozen=True)
class CohortProgram:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def program_id(self) -> str:
        return str(self.manifest["program_id"])


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CohortSchemaError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return _utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise CohortSchemaError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CohortSchemaError(f"{field} must be an RFC 3339 timestamp") from exc
    return _utc(parsed, field=field)


def _regular_no_symlink(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise CohortSchemaError(f"{label} must be a regular non-symlink file")
    return path


def _strict_relative(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CohortSchemaError(f"{field} must be a normalized relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise CohortSchemaError(f"{field} must be a normalized relative path")
    return relative


def _confined(root: Path, relative: Any, *, field: str) -> Path:
    rel = _strict_relative(relative, field=field)
    path = root.joinpath(*rel.parts)
    cursor = root
    if cursor.is_symlink():
        raise CohortSchemaError(f"{field} cannot traverse a symlink")
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CohortSchemaError(f"{field} cannot traverse a symlink")
    return path


def build_schedule(first_cycle_at: datetime) -> tuple[dict[str, Any], ...]:
    first = _utc(first_cycle_at, field="first_cycle_at")
    if first.minute != 5 or first.second != 0 or first.microsecond != 0:
        raise CohortSchemaError("first_cycle_at must be aligned to HH:05:00 UTC")
    return tuple(
        {
            "cycle_index": index,
            "cycle_id": f"cycle-{index:02d}",
            "scheduled_at": utc_text(first + (index - 1) * CYCLE_SPACING),
        }
        for index in range(1, CYCLE_COUNT + 1)
    )


def budget_plan(*, cycles: int = CYCLE_COUNT, tokens: int = PANEL_SIZE) -> dict[str, Any]:
    if cycles != CYCLE_COUNT or tokens != PANEL_SIZE:
        raise CohortSchemaError("cohort v1 is fixed at 32 cycles and five tokens")
    typical_per_cycle = 1 + tokens * 7
    maximum_per_cycle = 1 + tokens * 11
    return {
        "schema_version": 1,
        "cycles": cycles,
        "tokens_per_cycle": tokens,
        "opportunities": cycles * tokens,
        "typical_billable_credits_per_cycle": typical_per_cycle,
        "maximum_billable_credits_per_cycle": maximum_per_cycle,
        "maximum_authenticated_attempts_per_cycle": maximum_per_cycle + 1,
        "typical_program_credits": cycles * typical_per_cycle,
        "maximum_program_credits": cycles * maximum_per_cycle,
        "maximum_program_authenticated_attempts": cycles * (maximum_per_cycle + 1),
        "account_preflight_credits": 0,
        "automatic_retries": 0,
    }


def _program_document(
    *,
    program_id: str,
    created_at: datetime,
    first_cycle_at: datetime,
    design_sha256: str,
    contract_sha256: str,
    strategy_sha256: str,
    comparator_sha256: str,
    implementation_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "program_version": PROGRAM_VERSION,
        "program_id": program_id,
        "created_at": utc_text(created_at),
        "stage": "preregistered",
        "design_path": DESIGN_PATH,
        "design_sha256": design_sha256,
        "contract_path": "contracts/nansen-openapi.json",
        "contract_sha256": contract_sha256,
        "strategy_path": "contracts/frozen-strategy-manifest.json",
        "strategy_sha256": strategy_sha256,
        "comparator_path": COMPARATOR_PATH,
        "comparator_sha256": comparator_sha256,
        "implementation_path": IMPLEMENTATION_PATH,
        "implementation_sha256": implementation_sha256,
        "schedule": list(build_schedule(first_cycle_at)),
        "selection": {
            "panel_size": PANEL_SIZE,
            "strata": list(STRATA),
            "empty_stratum_policy": "cycle_unscorable_before_token_calls",
            "rotation_inputs": ["prior_selection_count"],
            "outcome_adaptive": False,
        },
        "strategies": {
            "frozen_comparators": ["H0", "H1", "H2", "H3", "H4"],
            "new_rule": "buyer-breadth-exchange-comovement-v1",
            "confirmatory_primary_rule": PRIMARY_RULE_ID,
            "all_other_rules": "descriptive_not_advance_eligible",
            "wbs_labels": list(WBS_LABELS),
            "training_inside_program": False,
        },
        "execution": {
            "horizon_hours": 4,
            "virtual_notional_formula": "min(1000, 0.001 * liquidity_usd)",
            "base_per_side_bps": 100,
            "stress_per_side_bps": 250,
            "always_collect_counterfactual": True,
            "decision_seal_deadline_minutes_after_schedule": 45,
            "ohlcv_grid": "inclusive_start_through_inclusive_exit_end",
        },
        "budget": budget_plan(),
        "advancement_gates": {
            "minimum_weeks": 8,
            "minimum_filled_rule_signals": 100,
            "minimum_unique_tokens": 20,
            "minimum_signal_fill_rate": 0.70,
            "maximum_token_share": 0.20,
            "maximum_week_share": 0.25,
            "bootstrap_replicates": 10_000,
            "bootstrap_confidence": 0.95,
            "complete_rule_decision_availability_required": True,
            "all_cycles_must_be_outcome_sealed": True,
        },
    }


def _comparator_document(strategy_bytes: bytes) -> dict[str, Any]:
    try:
        strategy = json.loads(strategy_bytes)
    except json.JSONDecodeError as exc:  # pragma: no cover - pinned bytes are valid JSON
        raise CohortSchemaError("frozen strategy manifest is not valid JSON") from exc
    if (
        not isinstance(strategy, dict)
        or not isinstance(strategy.get("theories"), list)
        or len(strategy["theories"]) != 6
        or not isinstance(strategy.get("blocked_theories"), list)
    ):
        raise CohortSchemaError("frozen strategy definitions are unavailable")
    return {
        "schema_version": 1,
        "source_manifest_sha256": EXPECTED_STRATEGY_SHA256,
        "theories": strategy["theories"],
        "blocked_theories": strategy["blocked_theories"],
    }


def _runtime_repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_implementation_files(repository: Path | None = None) -> tuple[Path, ...]:
    root = _runtime_repository() if repository is None else Path(repository).resolve()
    package = root / "src/nansen_signal_lab"
    files = sorted(package.rglob("*.py")) if package.is_dir() else []
    for relative in ("requirements.txt", "nansen-lab"):
        path = root / relative
        if path.is_file() and not path.is_symlink():
            files.append(path)
    if not files or any(path.is_symlink() or not path.is_file() for path in files):
        raise CohortSchemaError("protocol implementation source set is unavailable")
    return tuple(sorted(set(files)))


def _implementation_document(repository: Path | None = None) -> dict[str, Any]:
    root = _runtime_repository() if repository is None else Path(repository).resolve()
    records = []
    for path in _runtime_implementation_files(root):
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
        )
    return {
        "schema_version": 1,
        "binding": "exact-runtime-source-v1",
        "files": records,
        "runtime": _runtime_environment(),
    }


def _runtime_environment() -> dict[str, Any]:
    packages = {}
    for distribution in ("httpx", "python-dotenv", "pandas"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise CohortSchemaError(
                f"required runtime distribution is unavailable: {distribution}"
            ) from exc
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "packages": packages,
    }


def _load_implementation_document(program: CohortProgram) -> dict[str, Any]:
    path = _confined(
        program.root,
        program.manifest["implementation_path"],
        field="implementation_path",
    )
    _regular_no_symlink(path, label="protocol implementation manifest")
    raw = path.read_bytes()
    if _sha256(raw) != program.manifest["implementation_sha256"]:
        raise CohortSchemaError("protocol implementation manifest hash differs")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CohortSchemaError("protocol implementation manifest is invalid") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "binding", "files", "runtime"}
        or document.get("schema_version") != 1
        or document.get("binding") != "exact-runtime-source-v1"
        or not isinstance(document.get("files"), list)
        or not document["files"]
    ):
        raise CohortSchemaError("protocol implementation manifest schema differs")
    return document


def validate_runtime_implementation(program: CohortProgram) -> None:
    """Refuse protocol execution or replay under code different from init bytes."""

    document = _load_implementation_document(program)
    if document["runtime"] != _runtime_environment():
        raise CohortSchemaError("runtime protocol dependency environment drifted")
    runtime_root = _runtime_repository()
    current_paths = {
        path.relative_to(runtime_root).as_posix()
        for path in _runtime_implementation_files(runtime_root)
    }
    archived_paths: set[str] = set()
    expected_archived_files: set[Path] = set()
    for record in document["files"]:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "sha256"}
        ):
            raise CohortSchemaError("protocol implementation file record differs")
        relative = _strict_relative(record["path"], field="implementation file path")
        relative_text = relative.as_posix()
        if relative_text in archived_paths:
            raise CohortSchemaError("protocol implementation file path is duplicated")
        archived_paths.add(relative_text)
        archived = _confined(
            program.root,
            f"contracts/implementation/{relative_text}",
            field="archived implementation file",
        )
        expected_archived_files.add(archived.absolute())
        _regular_no_symlink(archived, label="archived implementation file")
        if _sha256(archived.read_bytes()) != record["sha256"]:
            raise CohortSchemaError("archived protocol implementation bytes differ")
        live = runtime_root.joinpath(*relative.parts)
        _regular_no_symlink(live, label="runtime protocol implementation file")
        if _sha256(live.read_bytes()) != record["sha256"]:
            raise CohortSchemaError(
                f"runtime protocol implementation drifted: {relative_text}"
            )
    if archived_paths != current_paths:
        raise CohortSchemaError("runtime protocol implementation source set drifted")
    archive_root = program.root / "contracts/implementation"
    actual_archived_files = {
        path.absolute() for path in archive_root.rglob("*") if path.is_file()
    }
    if actual_archived_files != expected_archived_files:
        raise CohortSchemaError("archived protocol implementation source set differs")


def initialize_cohort_program(
    root: Path,
    *,
    created_at: datetime,
    first_cycle_at: datetime,
    repo_root: Path | None = None,
) -> CohortProgram:
    root = Path(root)
    repository = (
        Path(repo_root).resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[2]
    )
    experiments = repository / "research" / "experiments"
    if root.parent.resolve() != experiments.resolve() or root.is_symlink():
        raise CohortSchemaError(
            "program root must be a direct non-symlink child of research/experiments"
        )
    if root.exists():
        raise FileExistsError(f"refusing to reuse cohort program directory: {root}")
    program_id = root.name
    if not program_id or program_id in {".", ".."}:
        raise CohortSchemaError("program id is invalid")

    design = _regular_no_symlink(repository / DESIGN_PATH, label="cohort design")
    contract = _regular_no_symlink(
        repository / CONTRACT_SOURCE_PATH, label="pinned Nansen contract"
    )
    strategy = _regular_no_symlink(
        repository / STRATEGY_SOURCE_PATH, label="frozen strategy manifest"
    )
    design_bytes = design.read_bytes()
    contract_bytes = contract.read_bytes()
    strategy_bytes = strategy.read_bytes()
    contract_hash = _sha256(contract_bytes)
    if contract_hash != EXPECTED_CONTRACT_SHA256:
        raise CohortSchemaError("pinned Nansen contract hash differs")
    strategy_hash = _sha256(strategy_bytes)
    if strategy_hash != EXPECTED_STRATEGY_SHA256:
        raise CohortSchemaError("frozen strategy manifest hash differs")
    comparator_document = _comparator_document(strategy_bytes)
    comparator_bytes = canonical_json_bytes(comparator_document)
    implementation_root = _runtime_repository()
    implementation_document = _implementation_document(implementation_root)
    implementation_bytes = canonical_json_bytes(implementation_document)

    created = _utc(created_at, field="created_at")
    first = _utc(first_cycle_at, field="first_cycle_at")
    if first <= created:
        raise CohortSchemaError("first_cycle_at must be after program creation")
    document = _program_document(
        program_id=program_id,
        created_at=created,
        first_cycle_at=first,
        design_sha256=_sha256(design_bytes),
        contract_sha256=contract_hash,
        strategy_sha256=strategy_hash,
        comparator_sha256=_sha256(comparator_bytes),
        implementation_sha256=_sha256(implementation_bytes),
    )

    root.mkdir(parents=True)
    try:
        write_bytes_once(root / "contracts" / "nansen-openapi.json", contract_bytes)
        write_bytes_once(
            root / "contracts" / "frozen-strategy-manifest.json", strategy_bytes
        )
        write_bytes_once(root / COMPARATOR_PATH, comparator_bytes)
        write_bytes_once(root / IMPLEMENTATION_PATH, implementation_bytes)
        for source in _runtime_implementation_files(implementation_root):
            write_bytes_once(
                root
                / "contracts/implementation"
                / source.relative_to(implementation_root),
                source.read_bytes(),
            )
        write_json_once(root / "program.json", document)
    except BaseException:
        # Initialization is the only point at which this directory can be
        # incomplete and it has made no external calls. Remove only files this
        # function just created, leaving collision evidence visible otherwise.
        for path in (
            root / "program.json",
            root / "contracts" / "nansen-openapi.json",
            root / "contracts" / "frozen-strategy-manifest.json",
            root / COMPARATOR_PATH,
            root / IMPLEMENTATION_PATH,
        ):
            if path.is_file() and not path.is_symlink():
                path.unlink()
        implementation_root_path = root / "contracts/implementation"
        if implementation_root_path.exists() and not implementation_root_path.is_symlink():
            for path in sorted(
                implementation_root_path.rglob("*"),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                if path.is_file() and not path.is_symlink():
                    path.unlink()
                elif path.is_dir() and not path.is_symlink():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                implementation_root_path.rmdir()
            except OSError:
                pass
        for directory in (root / "contracts", root):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return load_cohort_program(root / "program.json", repo_root=repository)


def load_cohort_program(
    manifest_path: Path, *, repo_root: Path | None = None
) -> CohortProgram:
    path = Path(manifest_path)
    _regular_no_symlink(path, label="cohort program manifest")
    if path.name != "program.json":
        raise CohortSchemaError("cohort program manifest must be named program.json")
    root = path.parent
    repository = (
        Path(repo_root).resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[2]
    )
    experiments = repository / "research" / "experiments"
    if root.is_symlink() or root.parent.resolve() != experiments.resolve():
        raise CohortSchemaError(
            "program root must be a direct non-symlink child of research/experiments"
        )
    try:
        document = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise CohortSchemaError("cohort program manifest is unreadable") from exc
    if not isinstance(document, dict):
        raise CohortSchemaError("cohort program manifest must be an object")
    expected_keys = {
        "schema_version", "program_version", "program_id", "created_at", "stage",
        "design_path", "design_sha256", "contract_path", "contract_sha256",
        "strategy_path", "strategy_sha256", "comparator_path", "comparator_sha256",
        "implementation_path", "implementation_sha256",
        "schedule", "selection", "strategies", "execution", "budget",
        "advancement_gates",
    }
    if set(document) != expected_keys:
        raise CohortSchemaError("cohort program manifest keys differ from schema v1")
    if (
        document["schema_version"] != 1
        or document["program_version"] != PROGRAM_VERSION
        or document["program_id"] != root.name
        or document["stage"] != "preregistered"
    ):
        raise CohortSchemaError("cohort program identity differs")
    created = parse_utc(document["created_at"], field="created_at")
    schedule = document.get("schedule")
    if not isinstance(schedule, list) or len(schedule) != CYCLE_COUNT:
        raise CohortSchemaError("cohort schedule must contain exactly 32 cycles")
    if any(not isinstance(item, dict) for item in schedule):
        raise CohortSchemaError("cohort schedule entries must be objects")
    first = parse_utc(schedule[0].get("scheduled_at"), field="first scheduled_at")
    if schedule != list(build_schedule(first)):
        raise CohortSchemaError("cohort schedule differs from the fixed 44-hour cadence")
    if first <= created:
        raise CohortSchemaError("cohort schedule begins before creation")
    if document["budget"] != budget_plan():
        raise CohortSchemaError("cohort budget differs from the fixed plan")
    expected = _program_document(
        program_id=root.name,
        created_at=created,
        first_cycle_at=first,
        design_sha256=document.get("design_sha256"),
        contract_sha256=document.get("contract_sha256"),
        strategy_sha256=document.get("strategy_sha256"),
        comparator_sha256=document.get("comparator_sha256"),
        implementation_sha256=document.get("implementation_sha256"),
    )
    if document != expected:
        raise CohortSchemaError("cohort program configuration differs from v1")

    design_rel = _strict_relative(document["design_path"], field="design_path")
    design_path = repository.joinpath(*design_rel.parts)
    _regular_no_symlink(design_path, label="cohort design")
    if _sha256(design_path.read_bytes()) != document["design_sha256"]:
        raise CohortSchemaError("cohort design hash differs")
    contract_path = _confined(root, document["contract_path"], field="contract_path")
    _regular_no_symlink(contract_path, label="cohort contract")
    if (
        _sha256(contract_path.read_bytes()) != document["contract_sha256"]
        or document["contract_sha256"] != EXPECTED_CONTRACT_SHA256
    ):
        raise CohortSchemaError("cohort contract hash differs")
    strategy_path = _confined(root, document["strategy_path"], field="strategy_path")
    _regular_no_symlink(strategy_path, label="frozen strategy manifest")
    if (
        _sha256(strategy_path.read_bytes()) != document["strategy_sha256"]
        or document["strategy_sha256"] != EXPECTED_STRATEGY_SHA256
    ):
        raise CohortSchemaError("frozen strategy manifest hash differs")
    comparator_path = _confined(root, document["comparator_path"], field="comparator_path")
    _regular_no_symlink(comparator_path, label="frozen comparator definitions")
    expected_comparator = canonical_json_bytes(_comparator_document(strategy_path.read_bytes()))
    if (
        comparator_path.read_bytes() != expected_comparator
        or _sha256(expected_comparator) != document["comparator_sha256"]
    ):
        raise CohortSchemaError("frozen comparator definitions differ")
    program = CohortProgram(root=root, manifest_path=path, manifest=document)
    _load_implementation_document(program)
    return program


def remaining_required_credits(program: CohortProgram, cycle_index: int) -> int:
    if not isinstance(cycle_index, int) or isinstance(cycle_index, bool):
        raise CohortSchemaError("cycle_index must be an integer")
    if not 1 <= cycle_index <= CYCLE_COUNT:
        raise CohortSchemaError("cycle_index is outside the program")
    return (CYCLE_COUNT - cycle_index + 1) * MAX_CYCLE_CREDITS
