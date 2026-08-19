from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .contract import SOURCE_RELATIVE_PATH, load_parallel_strategy_contract
from .design import (
    DESIGN_PATH,
    MAX_PROGRAM_ATTEMPTS,
    MAX_PROGRAM_CREDITS,
    PORTFOLIO_ID,
    PROGRAM_ID,
    SCHEDULE,
    budget_contract,
)


class ParallelStrategySchemaError(RuntimeError):
    """Raised when a frozen parallel-strategy program cannot be trusted."""


PROGRAM_RELATIVE_ROOT = Path("research/experiments") / PROGRAM_ID
OPENAPI_SOURCE_RELATIVE_PATH = Path(
    "research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/"
    "adopted/nansen-openapi.json"
)
EXPECTED_OPENAPI_SHA256 = (
    "d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548"
)
V1_PROGRAM_RELATIVE_PATH = Path(
    "research/experiments/2026-08-18-prospective-multi-cycle-cohort-v1/"
    "program.json"
)
V1_PROGRAM_ID = "2026-08-18-prospective-multi-cycle-cohort-v1"

RUNTIME_DISTRIBUTIONS = (
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


@dataclass(frozen=True)
class ParallelStrategyProgram:
    repo_root: Path
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def stage(self) -> str:
        return str(self.manifest["stage"])


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    _regular_file(path, label="hash input")
    return sha256_bytes(path.read_bytes())


def utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ParallelStrategySchemaError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ParallelStrategySchemaError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParallelStrategySchemaError(
            f"{field} must be an RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParallelStrategySchemaError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _strict_relative(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ParallelStrategySchemaError(f"{field} must be a normalized relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ParallelStrategySchemaError(f"{field} must be a normalized relative path")
    return relative


def _confined(root: Path, value: Any, *, field: str) -> Path:
    relative = _strict_relative(value, field=field)
    cursor = root
    if cursor.is_symlink():
        raise ParallelStrategySchemaError(f"{field} cannot traverse a symlink")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ParallelStrategySchemaError(f"{field} cannot traverse a symlink")
    try:
        cursor.absolute().relative_to(root.absolute())
    except ValueError as exc:  # pragma: no cover - strict PurePosix already prevents this
        raise ParallelStrategySchemaError(f"{field} escapes its authority root") from exc
    return cursor


def _regular_file(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ParallelStrategySchemaError(
            f"{label} must be a regular non-symlink file: {path}"
        )
    return path


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    _regular_file(path, label=label)
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ParallelStrategySchemaError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ParallelStrategySchemaError(f"{label} must be a JSON object")
    return value


def _write_once(path: Path, content: bytes) -> Path:
    return atomic_write_once(path, content)


def atomic_write_once(path: Path, content: bytes) -> Path:
    """Durably create one immutable artifact without exposing partial bytes.

    The temporary file is fully written and fsynced before an atomic hard-link
    installs the final name.  A power loss can therefore leave an unreferenced
    temporary file, but never a partially written immutable artifact at the
    protocol path.  Existing byte-identical artifacts are adopted.
    """

    if not isinstance(content, bytes):
        raise ParallelStrategySchemaError("immutable artifact content must be bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ParallelStrategySchemaError(f"refusing symlink output: {path}")
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ParallelStrategySchemaError(f"existing artifact differs: {path}")
        return path
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise ParallelStrategySchemaError(f"existing artifact differs: {path}")
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return path


def atomic_replace_json(path: Path, value: Any) -> Path:
    """Durably replace one mutable manifest without following symlinks."""

    if path.is_symlink():
        raise ParallelStrategySchemaError(f"refusing symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(value)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return path


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in RUNTIME_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ParallelStrategySchemaError(
                f"required runtime distribution is unavailable: {distribution}"
            ) from exc
    return versions


def _source_paths(repo_root: Path) -> tuple[Path, ...]:
    package = repo_root / "programs/nansen_parallel_strategy_v1"
    if package.is_symlink():
        raise ParallelStrategySchemaError("parallel-strategy package cannot be a symlink")
    paths = list(sorted(package.glob("*.py"))) if package.is_dir() else []
    # Activation imports the frozen cohort replay, and the live runner/evidence
    # layer uses its budget, transport, feature, and execution helpers. Archive
    # the complete first-party package rather than trying to maintain a brittle
    # hand-written approximation of Python's transitive import graph.
    frozen_src = repo_root / "src/nansen_signal_lab"
    if frozen_src.is_symlink():
        raise ParallelStrategySchemaError("frozen Nansen package cannot be a symlink")
    if frozen_src.is_dir():
        paths.extend(sorted(frozen_src.glob("*.py")))
    programs_init = repo_root / "programs/__init__.py"
    if programs_init.is_file() and not programs_init.is_symlink():
        paths.append(programs_init)
    scripts = repo_root / "scripts"
    if scripts.is_symlink():
        raise ParallelStrategySchemaError("parallel-strategy scripts root cannot be a symlink")
    if scripts.is_dir():
        paths.extend(sorted(scripts.glob("*parallel_strategy*.py")))
    units = repo_root / "operations"
    if units.is_symlink():
        raise ParallelStrategySchemaError("parallel-strategy units root cannot be a symlink")
    if units.is_dir():
        paths.extend(sorted(units.glob("nansen-signal-lab-parallel-strategy*")))
        cohort_dropin = (
            units
            / "nansen-signal-lab-cohort-parallel-strategy-dropin.conf"
        )
        if cohort_dropin.is_file() and not cohort_dropin.is_symlink():
            paths.append(cohort_dropin)
    tests = repo_root / "tests"
    if tests.is_symlink():
        raise ParallelStrategySchemaError("parallel-strategy tests root cannot be a symlink")
    if tests.is_dir():
        paths.extend(sorted(tests.glob("test_parallel_strategy*.py")))
    requirements = repo_root / "requirements.txt"
    if requirements.is_file() and not requirements.is_symlink():
        paths.append(requirements)

    required_names = {
        "__init__.py",
        "aggregate.py",
        "contract.py",
        "design.py",
        "runtime.py",
        "schema.py",
        "timing.py",
    }
    observed_names = {path.name for path in paths if path.parent == package}
    if not required_names <= observed_names:
        missing = sorted(required_names - observed_names)
        raise ParallelStrategySchemaError(
            f"parallel-strategy source closure is incomplete: {missing[0]}"
        )
    if not any(path.parent == tests for path in paths):
        raise ParallelStrategySchemaError("parallel-strategy tests are unavailable")
    if not any(path.parent == frozen_src for path in paths):
        raise ParallelStrategySchemaError("frozen Nansen source closure is unavailable")
    unique: dict[str, Path] = {}
    for path in paths:
        _regular_file(path, label="runtime source")
        try:
            relative = path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise ParallelStrategySchemaError("runtime source escapes repository") from exc
        if relative in unique:
            raise ParallelStrategySchemaError("runtime source path is duplicated")
        unique[relative] = path
    return tuple(unique[name] for name in sorted(unique))


def _schedule_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "cycles": [
            {
                "cycle_index": cycle.index,
                "phase": cycle.phase,
                "phase_index": cycle.phase_index,
                "scheduled_at": utc_text(cycle.scheduled_at),
                "block": cycle.block,
            }
            for cycle in SCHEDULE
        ],
    }


def _artifact(path: str, digest: str) -> dict[str, str]:
    return {"path": path, "sha256": digest}


def _runtime_document(repo_root: Path, program_root: Path) -> dict[str, Any]:
    records: list[dict[str, str]] = []
    for source in _source_paths(repo_root):
        relative = source.relative_to(repo_root).as_posix()
        archived = program_root / "contracts/implementation" / relative
        content = source.read_bytes()
        _write_once(archived, content)
        records.append(
            {
                "path": relative,
                "sha256": sha256_bytes(content),
                "archived_path": archived.relative_to(program_root).as_posix(),
            }
        )
    return {
        "schema_version": 1,
        "kind": "parallel-strategy-runtime-freeze-v1",
        "program_id": PROGRAM_ID,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "dependencies": _dependency_versions(),
        "sources": records,
    }


def _preregistration_text() -> bytes:
    return (
        f"# Preregistration — {PROGRAM_ID}\n\n"
        "Status: **preregistered; no public or authenticated provider request "
        "is authorized by initialization**.\n\n"
        "- Eleven definitions are frozen independently of Program-A/A2 results.\n"
        "- A replay-valid terminal cohort v1 is required before the first action.\n"
        f"- Maximum authenticated attempts: {MAX_PROGRAM_ATTEMPTS}.\n"
        f"- Maximum billable credits: {MAX_PROGRAM_CREDITS}.\n"
        "- Automatic retries, ambiguous retransmission, replacement, and backfill "
        "are forbidden.\n"
    ).encode("utf-8")


def initialize_program(
    repo_root: Path,
    *,
    created_at: datetime | None = None,
) -> Path:
    """Create the complete preregistration offline and return its manifest path."""

    repository = Path(repo_root).resolve()
    root = repository / PROGRAM_RELATIVE_ROOT
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"refusing to reuse parallel-strategy root: {root}")

    design_source = _regular_file(
        _confined(repository, DESIGN_PATH, field="design specification path"),
        label="design specification",
    )
    openapi_source = _regular_file(
        _confined(
            repository,
            OPENAPI_SOURCE_RELATIVE_PATH.as_posix(),
            field="pinned OpenAPI path",
        ),
        label="pinned OpenAPI",
    )
    candidate_source = _regular_file(
        _confined(
            repository,
            SOURCE_RELATIVE_PATH.as_posix(),
            field="candidate definition source path",
        ),
        label="candidate definition source",
    )
    v1_program = _regular_file(
        _confined(
            repository,
            V1_PROGRAM_RELATIVE_PATH.as_posix(),
            field="terminal-v1 prerequisite path",
        ),
        label="terminal-v1 prerequisite",
    )
    if sha256_file(openapi_source) != EXPECTED_OPENAPI_SHA256:
        raise ParallelStrategySchemaError("pinned OpenAPI SHA-256 differs")

    strategy_contract = load_parallel_strategy_contract(repository)
    schedule = _schedule_document()
    created = datetime.now(timezone.utc) if created_at is None else created_at
    created_text = utc_text(created)
    if created.astimezone(timezone.utc) >= SCHEDULE[0].scheduled_at:
        raise ParallelStrategySchemaError("preregistration must precede the first cycle")

    root.mkdir(parents=True, mode=0o700)
    design_copy = _write_once(root / "contracts/design.md", design_source.read_bytes())
    openapi_copy = _write_once(
        root / "contracts/nansen-openapi.json", openapi_source.read_bytes()
    )
    candidate_copy = _write_once(
        root / "contracts/candidate-definitions.json", candidate_source.read_bytes()
    )
    strategy_path = _write_once(
        root / "contracts/parallel-strategy-contract.json",
        canonical_json_bytes(strategy_contract),
    )
    schedule_path = _write_once(root / "schedule.json", canonical_json_bytes(schedule))
    preregistration_path = _write_once(root / "PREREGISTRATION.md", _preregistration_text())
    runtime = _runtime_document(repository, root)
    runtime_path = _write_once(
        root / "contracts/runtime-manifest.json", canonical_json_bytes(runtime)
    )

    manifest = {
        "schema_version": 1,
        "kind": "parallel-strategy-program-v1",
        "program_id": PROGRAM_ID,
        "portfolio_id": PORTFOLIO_ID,
        "stage": "preregistered",
        "terminal_reason": None,
        "created_at": created_text,
        "design": _artifact("contracts/design.md", sha256_file(design_copy)),
        "openapi": _artifact("contracts/nansen-openapi.json", sha256_file(openapi_copy)),
        "candidate_definitions": _artifact(
            "contracts/candidate-definitions.json", sha256_file(candidate_copy)
        ),
        "strategy_contract": _artifact(
            "contracts/parallel-strategy-contract.json", sha256_file(strategy_path)
        ),
        "schedule": _artifact("schedule.json", sha256_file(schedule_path)),
        "runtime": _artifact("contracts/runtime-manifest.json", sha256_file(runtime_path)),
        "preregistration": _artifact(
            "PREREGISTRATION.md", sha256_file(preregistration_path)
        ),
        "budget": {
            **budget_contract(),
            "maximum_authenticated_attempts": MAX_PROGRAM_ATTEMPTS,
            "maximum_billable_credits": MAX_PROGRAM_CREDITS,
        },
        "activation_prerequisite": {
            "kind": "terminal-v1-operational-attestation-v1",
            "program_id": V1_PROGRAM_ID,
            "path": V1_PROGRAM_RELATIVE_PATH.as_posix(),
            "program_sha256": sha256_file(v1_program),
            "required_terminal_cycles": 32,
            "maximum_authenticated_attempts": 1_824,
            "maximum_billable_credits": 1_792,
        },
        "activation": None,
    }
    manifest_path = _write_once(root / "program.json", canonical_json_bytes(manifest))
    load_program(manifest_path)
    return manifest_path


def _record_path(
    program: ParallelStrategyProgram,
    record: Any,
    *,
    field: str,
) -> Path:
    if (
        not isinstance(record, dict)
        or set(record) != {"path", "sha256"}
        or not isinstance(record.get("sha256"), str)
        or len(record["sha256"]) != 64
    ):
        raise ParallelStrategySchemaError(f"{field} artifact record differs")
    path = _confined(program.root, record.get("path"), field=f"{field} path")
    _regular_file(path, label=field)
    if sha256_file(path) != record["sha256"]:
        raise ParallelStrategySchemaError(f"{field} SHA-256 differs")
    return path


def _validate_runtime(program: ParallelStrategyProgram, *, live: bool) -> dict[str, Any]:
    runtime_path = _record_path(program, program.manifest["runtime"], field="runtime")
    runtime = _read_object(runtime_path, label="runtime manifest")
    if (
        set(runtime)
        != {
            "schema_version",
            "kind",
            "program_id",
            "python_implementation",
            "python_version",
            "dependencies",
            "sources",
        }
        or runtime.get("schema_version") != 1
        or runtime.get("kind") != "parallel-strategy-runtime-freeze-v1"
        or runtime.get("program_id") != PROGRAM_ID
        or not isinstance(runtime.get("sources"), list)
        or not runtime["sources"]
    ):
        raise ParallelStrategySchemaError("runtime manifest schema differs")
    if live and (
        runtime["python_implementation"] != platform.python_implementation()
        or runtime["python_version"] != platform.python_version()
        or runtime["dependencies"] != _dependency_versions()
    ):
        raise ParallelStrategySchemaError("runtime dependency environment drifted")

    recorded: set[str] = set()
    for item in runtime["sources"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "archived_path"}
            or not isinstance(item.get("sha256"), str)
        ):
            raise ParallelStrategySchemaError("runtime source record differs")
        relative = _strict_relative(item.get("path"), field="runtime source path")
        archived = _confined(
            program.root,
            item.get("archived_path"),
            field="archived runtime source path",
        )
        _regular_file(archived, label="archived runtime source")
        if sha256_file(archived) != item["sha256"]:
            raise ParallelStrategySchemaError("archived runtime source drifted")
        relative_text = relative.as_posix()
        if relative_text in recorded:
            raise ParallelStrategySchemaError("runtime source record is duplicated")
        recorded.add(relative_text)
        if live:
            source = _confined(
                program.repo_root, relative_text, field="live runtime source path"
            )
            _regular_file(source, label="live runtime source")
            if sha256_file(source) != item["sha256"]:
                raise ParallelStrategySchemaError(
                    f"live runtime source drifted: {relative_text}"
                )
    if live:
        observed = {
            path.relative_to(program.repo_root).as_posix()
            for path in _source_paths(program.repo_root)
        }
        if observed != recorded:
            raise ParallelStrategySchemaError("runtime source set drifted")
    return runtime


def _validate_authorities(program: ParallelStrategyProgram) -> None:
    design_copy = _record_path(program, program.manifest["design"], field="design")
    openapi_copy = _record_path(program, program.manifest["openapi"], field="OpenAPI")
    candidate_copy = _record_path(
        program, program.manifest["candidate_definitions"], field="candidate definitions"
    )
    strategy_copy = _record_path(
        program, program.manifest["strategy_contract"], field="strategy contract"
    )
    schedule_copy = _record_path(
        program, program.manifest["schedule"], field="schedule"
    )
    _record_path(program, program.manifest["preregistration"], field="preregistration")

    live_design = _regular_file(
        _confined(program.repo_root, DESIGN_PATH, field="live design path"),
        label="live design specification",
    )
    live_openapi = _regular_file(
        _confined(
            program.repo_root,
            OPENAPI_SOURCE_RELATIVE_PATH.as_posix(),
            field="live OpenAPI path",
        ),
        label="live pinned OpenAPI",
    )
    live_candidates = _regular_file(
        _confined(
            program.repo_root,
            SOURCE_RELATIVE_PATH.as_posix(),
            field="live candidate path",
        ),
        label="live candidate definitions",
    )
    if design_copy.read_bytes() != live_design.read_bytes():
        raise ParallelStrategySchemaError("design specification drifted")
    if (
        openapi_copy.read_bytes() != live_openapi.read_bytes()
        or sha256_file(openapi_copy) != EXPECTED_OPENAPI_SHA256
    ):
        raise ParallelStrategySchemaError("pinned OpenAPI drifted")
    if candidate_copy.read_bytes() != live_candidates.read_bytes():
        raise ParallelStrategySchemaError("candidate definition source drifted")
    expected_contract = canonical_json_bytes(
        load_parallel_strategy_contract(program.repo_root)
    )
    if strategy_copy.read_bytes() != expected_contract:
        raise ParallelStrategySchemaError("parallel strategy contract drifted")
    if schedule_copy.read_bytes() != canonical_json_bytes(_schedule_document()):
        raise ParallelStrategySchemaError("parallel strategy schedule drifted")


def _repo_from_manifest(manifest_path: Path) -> tuple[Path, Path]:
    resolved = Path(manifest_path).absolute()
    if resolved.name != "program.json":
        raise ParallelStrategySchemaError("program manifest must be named program.json")
    expected = PROGRAM_RELATIVE_ROOT.parts
    if tuple(resolved.parent.parts[-len(expected) :]) != expected:
        raise ParallelStrategySchemaError("program manifest is outside its fixed path")
    repo_root = resolved.parents[len(expected)]
    if repo_root.is_symlink():
        raise ParallelStrategySchemaError("program path cannot traverse a symlink")
    cursor = repo_root
    for part in PROGRAM_RELATIVE_ROOT.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ParallelStrategySchemaError("program path cannot traverse a symlink")
    return repo_root, resolved.parent


def load_program(
    manifest_path: Path,
    *,
    validate_live_runtime: bool = True,
) -> ParallelStrategyProgram:
    repo_root, root = _repo_from_manifest(manifest_path)
    path = root / "program.json"
    manifest = _read_object(path, label="parallel-strategy manifest")
    expected_keys = {
        "schema_version",
        "kind",
        "program_id",
        "portfolio_id",
        "stage",
        "terminal_reason",
        "created_at",
        "design",
        "openapi",
        "candidate_definitions",
        "strategy_contract",
        "schedule",
        "runtime",
        "preregistration",
        "budget",
        "activation_prerequisite",
        "activation",
    }
    if set(manifest) != expected_keys:
        raise ParallelStrategySchemaError("parallel-strategy manifest keys differ")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "parallel-strategy-program-v1"
        or manifest.get("program_id") != PROGRAM_ID
        or manifest.get("portfolio_id") != PORTFOLIO_ID
        or manifest.get("stage") not in {"preregistered", "activated"}
        or manifest.get("terminal_reason") is not None
    ):
        raise ParallelStrategySchemaError("parallel-strategy manifest identity differs")
    created = _parse_utc(manifest.get("created_at"), field="created_at")
    if created >= SCHEDULE[0].scheduled_at:
        raise ParallelStrategySchemaError("parallel-strategy creation time differs")
    if manifest.get("budget") != {
        **budget_contract(),
        "maximum_authenticated_attempts": MAX_PROGRAM_ATTEMPTS,
        "maximum_billable_credits": MAX_PROGRAM_CREDITS,
    }:
        raise ParallelStrategySchemaError("parallel-strategy budget differs")

    prerequisite = manifest.get("activation_prerequisite")
    if (
        not isinstance(prerequisite, dict)
        or set(prerequisite)
        != {
            "kind",
            "program_id",
            "path",
            "program_sha256",
            "required_terminal_cycles",
            "maximum_authenticated_attempts",
            "maximum_billable_credits",
        }
        or prerequisite.get("kind") != "terminal-v1-operational-attestation-v1"
        or prerequisite.get("program_id") != V1_PROGRAM_ID
        or prerequisite.get("path") != V1_PROGRAM_RELATIVE_PATH.as_posix()
        or prerequisite.get("required_terminal_cycles") != 32
        or prerequisite.get("maximum_authenticated_attempts") != 1_824
        or prerequisite.get("maximum_billable_credits") != 1_792
        or not isinstance(prerequisite.get("program_sha256"), str)
    ):
        raise ParallelStrategySchemaError("terminal-v1 prerequisite differs")
    v1_path = _regular_file(
        _confined(
            repo_root,
            V1_PROGRAM_RELATIVE_PATH.as_posix(),
            field="terminal-v1 program path",
        ),
        label="terminal-v1 program",
    )
    if sha256_file(v1_path) != prerequisite["program_sha256"]:
        raise ParallelStrategySchemaError("terminal-v1 program manifest drifted")

    if manifest["stage"] == "preregistered" and manifest.get("activation") is not None:
        raise ParallelStrategySchemaError("preregistered program has activation evidence")
    if manifest["stage"] == "activated" and (
        not isinstance(manifest.get("activation"), dict)
        or set(manifest["activation"]) != {"path", "sha256"}
    ):
        raise ParallelStrategySchemaError("activated program omits activation evidence")

    program = ParallelStrategyProgram(
        repo_root=repo_root,
        root=root,
        manifest_path=path,
        manifest=manifest,
    )
    _validate_authorities(program)
    _validate_runtime(program, live=validate_live_runtime)
    if manifest["stage"] == "activated":
        # Imported lazily to avoid a schema/runtime cycle at module import.
        from .runtime import validate_terminal_v1_attestation

        validate_terminal_v1_attestation(program)
    return program


def _git_required_paths(program: ParallelStrategyProgram) -> tuple[str, ...]:
    paths = {
        PROGRAM_RELATIVE_ROOT.joinpath("program.json").as_posix(),
        PROGRAM_RELATIVE_ROOT.joinpath("PREREGISTRATION.md").as_posix(),
        PROGRAM_RELATIVE_ROOT.joinpath("schedule.json").as_posix(),
        DESIGN_PATH,
        SOURCE_RELATIVE_PATH.as_posix(),
        OPENAPI_SOURCE_RELATIVE_PATH.as_posix(),
        V1_PROGRAM_RELATIVE_PATH.as_posix(),
    }
    for field in (
        "design",
        "openapi",
        "candidate_definitions",
        "strategy_contract",
        "runtime",
        "preregistration",
    ):
        paths.add(PROGRAM_RELATIVE_ROOT.joinpath(program.manifest[field]["path"]).as_posix())
    runtime = _read_object(
        program.root / program.manifest["runtime"]["path"], label="runtime manifest"
    )
    for item in runtime["sources"]:
        paths.add(item["path"])
        paths.add(PROGRAM_RELATIVE_ROOT.joinpath(item["archived_path"]).as_posix())
    return tuple(sorted(paths))


def assert_preregistration_committed(manifest_path: Path) -> None:
    """Require every preregistration byte to equal the current local HEAD."""

    program = load_program(manifest_path)
    if program.stage != "preregistered":
        raise ParallelStrategySchemaError(
            "the byte-exact preregistration gate applies before activation"
        )
    required = _git_required_paths(program)
    for relative in required:
        live = _confined(program.repo_root, relative, field="preregistration Git path")
        _regular_file(live, label="preregistration Git input")
        exists = subprocess.run(
            ("git", "cat-file", "-e", f"HEAD:{relative}"),
            cwd=program.repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if exists.returncode != 0:
            raise ParallelStrategySchemaError(
                f"preregistration file is absent from HEAD: {relative}"
            )
        committed = subprocess.run(
            ("git", "show", f"HEAD:{relative}"),
            cwd=program.repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if committed.returncode != 0 or committed.stdout != live.read_bytes():
            raise ParallelStrategySchemaError(
                f"preregistration file differs from HEAD: {relative}"
            )
    staged = subprocess.run(
        ("git", "diff", "--cached", "--quiet", "HEAD", "--", *required),
        cwd=program.repo_root,
        check=False,
    )
    if staged.returncode != 0:
        raise ParallelStrategySchemaError("preregistration has staged changes from HEAD")


def replay_program(manifest_path: Path) -> dict[str, Any]:
    """Replay only frozen schema and operational activation state."""

    program = load_program(manifest_path)
    result: dict[str, Any] = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "stage": program.stage,
        "scheduled_cycles": len(SCHEDULE),
        "maximum_authenticated_attempts": MAX_PROGRAM_ATTEMPTS,
        "maximum_billable_credits": MAX_PROGRAM_CREDITS,
        "terminal_v1_attested": program.stage == "activated",
    }
    if program.stage == "activated":
        activation_path = _record_path(
            program, program.manifest["activation"], field="activation"
        )
        activation = _read_object(activation_path, label="activation attestation")
        result["terminal_v1"] = {
            "source_program_sha256": activation["source_program_sha256"],
            "source_aggregate_sha256": activation["source_aggregate_sha256"],
            "terminal_cycles": activation["terminal_cycles"],
            "authenticated_attempts": activation["authenticated_attempts"],
            "billable_credits": activation["billable_credits"],
        }
    return result


def iter_runtime_source_records(program: ParallelStrategyProgram) -> Iterable[dict[str, Any]]:
    """Expose frozen source records to the runner without exposing mutable paths."""

    runtime = _read_object(
        program.root / program.manifest["runtime"]["path"], label="runtime manifest"
    )
    return tuple(dict(item) for item in runtime["sources"])
