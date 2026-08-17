from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_complete(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short artifact write")
        offset += written
    os.fsync(descriptor)


@contextmanager
def _directory_lock(path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_replace_bytes(path: str | Path, content: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        _write_complete(descriptor, content)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        replaced = True
        _fsync_directory(target.parent)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if not replaced:
            temporary.unlink(missing_ok=True)
    return target


def _write_bytes_once_locked(target: Path, content: bytes) -> Path:
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        _write_complete(descriptor, content)
    except BaseException:
        os.close(descriptor)
        target.unlink(missing_ok=True)
        _fsync_directory(target.parent)
        raise
    else:
        os.close(descriptor)
        _fsync_directory(target.parent)
        return target


def write_bytes_once(path: str | Path, content: bytes) -> Path:
    target = Path(path)
    with _directory_lock(target):
        return _write_bytes_once_locked(target, content)


def write_json_once(path: str | Path, value: Any) -> Path:
    return write_bytes_once(path, canonical_json_bytes(value))


def _conflict_paths(target: Path, received_sha256: str) -> tuple[Path, Path]:
    directory = target.parent / ".conflicts" / target.name
    return (
        directory / f"{received_sha256}.bin",
        directory / f"{received_sha256}.json",
    )


def _write_or_adopt_exact_locked(target: Path, content: bytes) -> Path:
    try:
        return _write_bytes_once_locked(target, content)
    except FileExistsError:
        if target.is_file() and target.read_bytes() == content:
            return target
        raise


def _quarantine_collision_locked(
    target: Path, content: bytes, metadata: dict[str, Any]
) -> None:
    received_sha256 = hashlib.sha256(content).hexdigest()
    bytes_path, metadata_path = _conflict_paths(target, received_sha256)
    bytes_path.parent.mkdir(parents=True, exist_ok=True)
    _write_or_adopt_exact_locked(bytes_path, content)
    _write_or_adopt_exact_locked(metadata_path, canonical_json_bytes(metadata))


def install_or_quarantine_bytes_once(
    path: str | Path, content: bytes, *, metadata: dict[str, Any]
) -> Path:
    target = Path(path)
    with _directory_lock(target):
        try:
            return _write_bytes_once_locked(target, content)
        except FileExistsError:
            if target.is_file() and target.read_bytes() == content:
                raise
            _quarantine_collision_locked(target, content, metadata)
            raise RuntimeError(f"artifact collision at {target}")


def write_bytes_once_or_adopt_exact(
    path: str | Path, content: bytes, *, metadata: dict[str, Any]
) -> Path:
    target = Path(path)
    with _directory_lock(target):
        try:
            return _write_bytes_once_locked(target, content)
        except FileExistsError:
            if target.is_file() and target.read_bytes() == content:
                return target
            _quarantine_collision_locked(target, content, metadata)
            raise RuntimeError(f"artifact collision at {target}")


def _flow_artifact_paths(output_path):
    response_path = Path(output_path)
    request_path = response_path.with_name(f"{response_path.stem}.request.json")
    return response_path, request_path


def _write_sibling_temporary(path, content):
    return _write_sibling_staged(path, content, suffix=".tmp")


def _write_sibling_backup(path, content):
    return _write_sibling_staged(path, content, suffix=".bak")


def _sibling_staged_path(path, *, suffix):
    path = Path(path)
    return path.with_name(f".{path.name}{suffix}")


def _write_sibling_staged(path, content, *, suffix):
    temporary = _sibling_staged_path(path, suffix=suffix)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


@contextmanager
def _artifact_pair_lock(output_path):
    response_path, _ = _flow_artifact_paths(output_path)
    with _directory_lock(response_path):
        yield


def _install_artifact(temporary, target):
    temporary.replace(target)


def _fsync_directory(path):
    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _artifact_transaction_paths(response_path, request_path):
    marker = response_path.with_name(f".{response_path.name}.transaction.json")
    return {
        "response_temporary": _sibling_staged_path(response_path, suffix=".tmp"),
        "request_temporary": _sibling_staged_path(request_path, suffix=".tmp"),
        "response_backup": _sibling_staged_path(response_path, suffix=".bak"),
        "request_backup": _sibling_staged_path(request_path, suffix=".bak"),
        "marker": marker,
        "marker_temporary": _sibling_staged_path(marker, suffix=".tmp"),
    }


def _cleanup_artifact_transaction(paths):
    removed = False
    for name in (
        "response_temporary",
        "request_temporary",
        "response_backup",
        "request_backup",
        "marker_temporary",
        "marker",
    ):
        path = paths[name]
        if path.exists():
            path.unlink()
            removed = True
    if removed:
        _fsync_directory(paths["marker"].parent)


def _validate_installed_artifact_pair(
    response_path,
    request_path,
    *,
    response_sha256,
    request_sha256,
):
    if (
        not response_path.is_file()
        or hashlib.sha256(response_path.read_bytes()).hexdigest() != response_sha256
        or not request_path.is_file()
        or hashlib.sha256(request_path.read_bytes()).hexdigest() != request_sha256
    ):
        raise RuntimeError("incomplete evidence artifact transaction")
    try:
        metadata = json.loads(request_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid evidence artifact sidecar after transaction") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("response_file") != response_path.name
        or metadata.get("response_sha256") != response_sha256
    ):
        raise RuntimeError("evidence response does not match installed sidecar")


def _restore_artifact_transaction(response_path, request_path, transaction, paths):
    for backup_name, target, digest_name in (
        ("response_backup", response_path, "original_response_sha256"),
        ("request_backup", request_path, "original_request_sha256"),
    ):
        digest = transaction[digest_name]
        backup = paths[backup_name]
        if digest is None:
            if target.exists():
                target.unlink()
                _fsync_directory(target.parent)
            continue
        if backup.exists():
            backup.replace(target)
            _fsync_directory(target.parent)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("cannot roll back evidence artifact transaction")


def _recover_artifact_transaction(response_path, request_path):
    paths = _artifact_transaction_paths(response_path, request_path)
    marker_path = paths["marker"]
    if not marker_path.exists():
        _cleanup_artifact_transaction(paths)
        return
    try:
        transaction = json.loads(marker_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot recover evidence artifact transaction: {exc}") from exc
    expected = {
        "schema_version": 1,
        "response_file": response_path.name,
        "request_file": request_path.name,
    }
    if not isinstance(transaction, dict) or any(
        transaction.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("invalid evidence artifact transaction marker")
    phase = transaction.get("phase")
    response_sha256 = transaction.get("response_sha256")
    request_sha256 = transaction.get("request_sha256")
    original_response_sha256 = transaction.get("original_response_sha256")
    original_request_sha256 = transaction.get("original_request_sha256")
    if phase not in {"commit", "rollback"}:
        raise RuntimeError("invalid evidence artifact transaction phase")
    if not all(
        isinstance(digest, str) and len(digest) == 64
        for digest in (response_sha256, request_sha256)
    ):
        raise RuntimeError("invalid evidence artifact transaction hashes")
    if not all(
        digest is None or (isinstance(digest, str) and len(digest) == 64)
        for digest in (original_response_sha256, original_request_sha256)
    ):
        raise RuntimeError("invalid original evidence artifact transaction hashes")

    if phase == "rollback":
        _restore_artifact_transaction(response_path, request_path, transaction, paths)
        if original_response_sha256 is not None and original_request_sha256 is not None:
            _validate_installed_artifact_pair(
                response_path,
                request_path,
                response_sha256=original_response_sha256,
                request_sha256=original_request_sha256,
            )
    else:
        for temporary_name, target, digest in (
            ("response_temporary", response_path, response_sha256),
            ("request_temporary", request_path, request_sha256),
        ):
            temporary = paths[temporary_name]
            if temporary.exists():
                _install_artifact(temporary, target)
                _fsync_directory(target.parent)
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("cannot complete evidence artifact transaction")

        _validate_installed_artifact_pair(
            response_path,
            request_path,
            response_sha256=response_sha256,
            request_sha256=request_sha256,
        )
    _cleanup_artifact_transaction(paths)


def _write_artifact_transaction_marker(
    response_path,
    request_path,
    *,
    phase,
    response_sha256,
    request_sha256,
    original_response_sha256,
    original_request_sha256,
):
    paths = _artifact_transaction_paths(response_path, request_path)
    marker_bytes = (
        json.dumps(
            {
                "schema_version": 1,
                "phase": phase,
                "response_file": response_path.name,
                "request_file": request_path.name,
                "response_sha256": response_sha256,
                "request_sha256": request_sha256,
                "original_response_sha256": original_response_sha256,
                "original_request_sha256": original_request_sha256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    marker_temporary = _write_sibling_temporary(paths["marker"], marker_bytes)
    _install_artifact(marker_temporary, paths["marker"])
    _fsync_directory(response_path.parent)


def _raw_response_parse_status(raw_response_bytes: bytes) -> str:
    if not raw_response_bytes:
        return "empty"
    try:
        parsed = json.loads(raw_response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "non_json"
    return "json_object" if isinstance(parsed, dict) else "json_other"


def write_api_artifacts(
    *,
    body: Any | None,
    payload: dict[str, Any] | None,
    endpoint: str,
    output_path: str | Path,
    cache_hit: bool,
    response_retrieved_at: str,
    artifact_written_at: str,
    response_metadata: dict[str, Any] | None = None,
    overwrite: bool = True,
    raw_response_bytes: bytes | None = None,
) -> tuple[Path, Path]:
    response_path, request_path = _flow_artifact_paths(output_path)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    if body is not None and not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    response_parse_status = None
    if raw_response_bytes is None:
        if body is None:
            raise ValueError("body is required when raw_response_bytes is omitted")
        response_bytes = (
            json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
    elif body is None:
        response_bytes = raw_response_bytes
        response_parse_status = _raw_response_parse_status(raw_response_bytes)
    else:
        try:
            parsed_raw_response = json.loads(raw_response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("raw_response_bytes is not valid JSON for supplied body") from exc
        if parsed_raw_response != body:
            raise ValueError("raw_response_bytes does not match supplied body")
        response_bytes = raw_response_bytes
    metadata = {
        "schema_version": 2,
        "endpoint": endpoint,
        "payload": payload,
        "cache_hit": bool(cache_hit),
        "response_retrieved_at": response_retrieved_at,
        "artifact_written_at": artifact_written_at,
        "response_file": response_path.name,
        "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
    }
    if response_parse_status is not None:
        metadata["response_parse_status"] = response_parse_status
    if response_metadata is not None:
        metadata["response_metadata"] = dict(response_metadata)
    request_bytes = (
        json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    with _artifact_pair_lock(response_path):
        _recover_artifact_transaction(response_path, request_path)
        response_temporary = None
        request_temporary = None
        backups = {}
        installation_started = False
        cleanup_transaction = True
        transaction_paths = _artifact_transaction_paths(response_path, request_path)
        try:
            if not overwrite and (response_path.exists() or request_path.exists()):
                raise FileExistsError(
                    f"refusing to overwrite explicit flow output: {response_path} or {request_path}"
                )
            original_bytes = {
                response_path: response_path.read_bytes() if response_path.exists() else None,
                request_path: request_path.read_bytes() if request_path.exists() else None,
            }
            original_response_sha256 = (
                None
                if original_bytes[response_path] is None
                else hashlib.sha256(original_bytes[response_path]).hexdigest()
            )
            original_request_sha256 = (
                None
                if original_bytes[request_path] is None
                else hashlib.sha256(original_bytes[request_path]).hexdigest()
            )
            response_temporary = _write_sibling_temporary(response_path, response_bytes)
            request_temporary = _write_sibling_temporary(request_path, request_bytes)
            for path, original in original_bytes.items():
                if original is not None:
                    backups[path] = _write_sibling_backup(path, original)
            _write_artifact_transaction_marker(
                response_path,
                request_path,
                phase="commit",
                response_sha256=response_sha256,
                request_sha256=request_sha256,
                original_response_sha256=original_response_sha256,
                original_request_sha256=original_request_sha256,
            )
            installation_started = True
            _install_artifact(response_temporary, response_path)
            response_temporary = None
            _fsync_directory(response_path.parent)
            _install_artifact(request_temporary, request_path)
            request_temporary = None
            _fsync_directory(response_path.parent)
            _validate_installed_artifact_pair(
                response_path,
                request_path,
                response_sha256=response_sha256,
                request_sha256=request_sha256,
            )
        except BaseException:
            if installation_started:
                try:
                    _write_artifact_transaction_marker(
                        response_path,
                        request_path,
                        phase="rollback",
                        response_sha256=response_sha256,
                        request_sha256=request_sha256,
                        original_response_sha256=original_response_sha256,
                        original_request_sha256=original_request_sha256,
                    )
                    _restore_artifact_transaction(
                        response_path,
                        request_path,
                        {
                            "original_response_sha256": original_response_sha256,
                            "original_request_sha256": original_request_sha256,
                        },
                        transaction_paths,
                    )
                except BaseException:
                    cleanup_transaction = False
                    raise
            raise
        finally:
            if cleanup_transaction:
                if response_temporary is not None:
                    response_temporary.unlink(missing_ok=True)
                if request_temporary is not None:
                    request_temporary.unlink(missing_ok=True)
                for backup in backups.values():
                    backup.unlink(missing_ok=True)
                _cleanup_artifact_transaction(transaction_paths)
    return response_path, request_path


def write_flow_artifacts(
    *,
    body,
    payload,
    output_path,
    cache_hit,
    response_retrieved_at,
    artifact_written_at,
    overwrite=True,
):
    return write_api_artifacts(
        body=body,
        payload=payload,
        endpoint="tgm/flows",
        output_path=output_path,
        cache_hit=cache_hit,
        response_retrieved_at=response_retrieved_at,
        artifact_written_at=artifact_written_at,
        overwrite=overwrite,
    )
