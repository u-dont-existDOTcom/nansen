from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

import src.nansen_signal_lab.artifacts as artifacts


def _exit_while_holding_artifact_lock(output_path):
    with artifacts._artifact_pair_lock(output_path):
        os._exit(0)

def test_write_api_artifacts_shallow_copies_response_metadata(tmp_path):
    """Fails if later caller changes can alter the top-level archived response metadata."""
    response_metadata = {"row_count": 1, "pagination_complete": True}
    _, request_path = artifacts.write_api_artifacts(
        body={"data": []},
        payload={},
        endpoint="tgm/who-bought-sold",
        output_path=tmp_path / "evidence.json",
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
        response_metadata=response_metadata,
    )
    response_metadata["row_count"] = 999

    assert json.loads(request_path.read_text())["response_metadata"] == {
        "row_count": 1,
        "pagination_complete": True,
    }


@pytest.mark.parametrize("existing_pair", [False, True])
def test_write_api_artifacts_rolls_back_pair_when_sidecar_install_fails(
    tmp_path, monkeypatch, existing_pair
):
    """Fails if a sidecar-install failure leaves an unpaired or stale response artifact."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    if existing_pair:
        output.write_bytes(b"old response\n")
        request.write_bytes(b"old sidecar\n")
    original_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    def fail_sidecar_install(temporary, target):
        if target == request:
            raise OSError("injected sidecar install failure")
        temporary.replace(target)

    monkeypatch.setattr(artifacts, "_install_artifact", fail_sidecar_install)

    with pytest.raises(OSError, match="injected sidecar install failure"):
        artifacts.write_api_artifacts(
            body={"data": ["new"]},
            payload={"page": 1},
            endpoint="tgm/who-bought-sold",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:01:00Z",
            artifact_written_at="2026-08-02T00:02:00Z",
        )

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_write_api_artifacts_pair_lock_blocks_second_writer_before_targets_change(tmp_path):
    """Fails if a concurrent writer can enter a response/sidecar pair commit without its lock."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    output.write_bytes(b"old response\n")
    request.write_bytes(b"old sidecar\n")
    started = threading.Event()
    finished = threading.Event()
    failures = []

    def second_writer():
        try:
            started.set()
            artifacts.write_api_artifacts(
                body={"data": ["new"]},
                payload={"page": 1},
                endpoint="tgm/who-bought-sold",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
                artifact_written_at="2026-08-02T00:02:00Z",
            )
        except BaseException as exc:  # Assert thread failures on the test thread.
            failures.append(exc)
        finally:
            finished.set()

    with artifacts._artifact_pair_lock(output):
        worker = threading.Thread(target=second_writer)
        worker.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.1)
        assert output.read_bytes() == b"old response\n"
        assert request.read_bytes() == b"old sidecar\n"

    assert finished.wait(timeout=10)
    worker.join()
    assert not failures
    assert json.loads(output.read_text()) == {"data": ["new"]}
    assert not list(tmp_path.glob(".*"))


def test_artifact_lock_uses_no_stale_path_sentinel(tmp_path):
    """Fails if an abrupt writer exit can strand a filesystem lock-path sentinel."""
    output = tmp_path / "evidence.json"

    with artifacts._artifact_pair_lock(output):
        assert not list(tmp_path.glob("*.pair.lock"))


def test_artifact_lock_is_released_after_abrupt_owner_exit(tmp_path):
    """Fails if a killed writer can permanently block later evidence collection."""
    output = tmp_path / "evidence.json"
    context = multiprocessing.get_context("fork")
    holder = context.Process(target=_exit_while_holding_artifact_lock, args=(output,))
    holder.start()
    holder.join(timeout=10)
    assert holder.exitcode == 0

    response_path, request_path = artifacts.write_api_artifacts(
        body={"data": []},
        payload={},
        endpoint="tgm/who-bought-sold",
        output_path=output,
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
    )

    assert response_path.exists()
    assert request_path.exists()


def test_write_api_artifacts_recovers_pair_after_real_subprocess_crash(tmp_path):
    """Fails if abrupt mid-commit exit leaves mismatched evidence or transaction debris."""
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output = artifact_dir / "evidence.json"
    request = artifact_dir / "evidence.request.json"
    artifacts.write_api_artifacts(
        body={"data": ["old"]},
        payload={"writer": "old"},
        endpoint="tgm/flows",
        output_path=output,
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
    )
    crash_marker = tmp_path / "response-installed.marker"
    child_code = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        import src.nansen_signal_lab.artifacts as artifacts

        output = Path(sys.argv[1])
        marker = Path(sys.argv[2])
        install_artifact = artifacts._install_artifact

        def crash_after_response_install(temporary, target):
            install_artifact(temporary, target)
            if target == output:
                with marker.open("wb") as signal:
                    signal.write(b"response-installed\\n")
                    signal.flush()
                    os.fsync(signal.fileno())
                os._exit(86)

        artifacts._install_artifact = crash_after_response_install
        artifacts.write_api_artifacts(
            body={"data": ["interrupted"]},
            payload={"writer": "interrupted"},
            endpoint="tgm/flows",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:03:00Z",
            artifact_written_at="2026-08-02T00:04:00Z",
        )
        """
    )

    crashed = subprocess.run(
        [sys.executable, "-c", child_code, str(output), str(crash_marker)],
        cwd=Path.cwd(),
        timeout=10,
        check=False,
    )

    assert crashed.returncode == 86
    assert crash_marker.read_text() == "response-installed\n"
    interrupted_metadata = json.loads(request.read_text())
    assert hashlib.sha256(output.read_bytes()).hexdigest() != interrupted_metadata[
        "response_sha256"
    ]

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        artifacts.write_api_artifacts(
            body={"data": ["must-not-overwrite"]},
            payload={"writer": "must-not-overwrite"},
            endpoint="tgm/flows",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:05:00Z",
            artifact_written_at="2026-08-02T00:06:00Z",
            overwrite=False,
        )

    recovered_metadata = json.loads(request.read_text())
    assert json.loads(output.read_text()) == {"data": ["interrupted"]}
    assert recovered_metadata["payload"] == {"writer": "interrupted"}
    assert hashlib.sha256(output.read_bytes()).hexdigest() == recovered_metadata[
        "response_sha256"
    ]
    assert not list(artifact_dir.glob(".*"))


@pytest.mark.parametrize(
    "restored_target",
    ["evidence.json", "evidence.request.json"],
    ids=["after-response-restore", "after-sidecar-restore"],
)
def test_write_api_artifacts_recovers_repeatedly_after_rollback_crash(
    tmp_path, restored_target
):
    """Fails if rollback direction is not durable and idempotent across process exit."""
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output = artifact_dir / "evidence.json"
    request = artifact_dir / "evidence.request.json"
    artifacts.write_api_artifacts(
        body={"data": ["old"]},
        payload={"writer": "old"},
        endpoint="tgm/flows",
        output_path=output,
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
    )
    old_response = output.read_bytes()
    old_request = request.read_bytes()
    crash_marker = tmp_path / f"{restored_target}.restored.marker"
    child_code = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        import src.nansen_signal_lab.artifacts as artifacts

        output = Path(sys.argv[1])
        request = Path(sys.argv[2])
        marker = Path(sys.argv[3])
        restored_target = sys.argv[4]
        install_artifact = artifacts._install_artifact
        path_replace = Path.replace

        def fail_sidecar_install(temporary, target):
            if target == request:
                raise OSError("injected sidecar install failure")
            install_artifact(temporary, target)

        def crash_after_target_restore(path, target):
            result = path_replace(path, target)
            if path.name.endswith(".bak") and Path(target).name == restored_target:
                with marker.open("wb") as signal:
                    signal.write(b"target-restored\\n")
                    signal.flush()
                    os.fsync(signal.fileno())
                os._exit(87)
            return result

        artifacts._install_artifact = fail_sidecar_install
        Path.replace = crash_after_target_restore
        artifacts.write_api_artifacts(
            body={"data": ["new"]},
            payload={"writer": "new"},
            endpoint="tgm/flows",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:03:00Z",
            artifact_written_at="2026-08-02T00:04:00Z",
        )
        """
    )

    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(output),
            str(request),
            str(crash_marker),
            restored_target,
        ],
        cwd=Path.cwd(),
        timeout=10,
        check=False,
    )

    assert crashed.returncode == 87
    assert crash_marker.read_text() == "target-restored\n"
    for attempt in range(2):
        with pytest.raises(FileExistsError, match="refusing to overwrite"):
            artifacts.write_api_artifacts(
                body={"data": [f"must-not-overwrite-{attempt}"]},
                payload={"writer": f"must-not-overwrite-{attempt}"},
                endpoint="tgm/flows",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:05:00Z",
                artifact_written_at="2026-08-02T00:06:00Z",
                overwrite=False,
            )

    assert output.read_bytes() == old_response
    assert request.read_bytes() == old_request
    recovered_metadata = json.loads(request.read_text())
    assert hashlib.sha256(output.read_bytes()).hexdigest() == recovered_metadata[
        "response_sha256"
    ]
    assert not list(artifact_dir.glob(".*"))


def test_write_api_artifacts_cleans_registered_backup_when_later_backup_fails(
    tmp_path, monkeypatch
):
    """Fails if a later backup error leaks an earlier staged backup file."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    output.write_bytes(b"old response\n")
    request.write_bytes(b"old sidecar\n")
    original_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    write_backup = artifacts._write_sibling_backup
    calls = 0

    def fail_second_backup(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second backup failure")
        return write_backup(path, content)

    monkeypatch.setattr(artifacts, "_write_sibling_backup", fail_second_backup)

    with pytest.raises(OSError, match="injected second backup failure"):
        artifacts.write_api_artifacts(
            body={"data": ["new"]},
            payload={"page": 1},
            endpoint="tgm/who-bought-sold",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:01:00Z",
            artifact_written_at="2026-08-02T00:02:00Z",
        )

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_write_api_artifacts_serializes_overwrite_refusal_between_real_writers(
    tmp_path, monkeypatch
):
    """Fails if writer B can pass its overwrite check while writer A commits the pair."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    response_install_started = threading.Event()
    release_first_writer = threading.Event()
    first_response_install = True
    writer_a_errors = []
    writer_b_errors = []
    writer_b_started = threading.Event()
    writer_b_finished = threading.Event()
    install_artifact = artifacts._install_artifact

    def pause_first_response_install(temporary, target):
        nonlocal first_response_install
        if target == output and first_response_install:
            first_response_install = False
            response_install_started.set()
            assert release_first_writer.wait(timeout=10)
        install_artifact(temporary, target)

    monkeypatch.setattr(artifacts, "_install_artifact", pause_first_response_install)

    def writer_a():
        try:
            artifacts.write_api_artifacts(
                body={"data": ["A"]},
                payload={"writer": "A"},
                endpoint="tgm/who-bought-sold",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
                artifact_written_at="2026-08-02T00:02:00Z",
                overwrite=False,
            )
        except BaseException as exc:
            writer_a_errors.append(exc)

    def writer_b():
        try:
            writer_b_started.set()
            artifacts.write_api_artifacts(
                body={"data": ["B"]},
                payload={"writer": "B"},
                endpoint="tgm/who-bought-sold",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
                artifact_written_at="2026-08-02T00:02:00Z",
                overwrite=False,
            )
        except BaseException as exc:
            writer_b_errors.append(exc)
        finally:
            writer_b_finished.set()

    first = threading.Thread(target=writer_a)
    first.start()
    assert response_install_started.wait(timeout=10)
    second = threading.Thread(target=writer_b)
    second.start()
    assert writer_b_started.wait(timeout=1)
    try:
        assert not writer_b_finished.wait(timeout=0.1)
    finally:
        release_first_writer.set()
    first.join(timeout=10)
    assert not first.is_alive()
    assert writer_b_finished.wait(timeout=10)
    second.join()

    assert not writer_a_errors
    assert len(writer_b_errors) == 1
    assert isinstance(writer_b_errors[0], FileExistsError)
    assert json.loads(output.read_text()) == {"data": ["A"]}
    assert json.loads(request.read_text())["payload"] == {"writer": "A"}
    assert not list(tmp_path.glob(".*"))


def test_write_api_artifacts_preserves_exact_raw_response_bytes(tmp_path):
    raw = b'{"data":[{"value":1}],"spacing":"provider"}'
    response, sidecar = artifacts.write_api_artifacts(
        body={"data": [{"value": 1}], "spacing": "provider"},
        raw_response_bytes=raw,
        payload={"chain": "base"},
        endpoint="tgm/flows",
        output_path=tmp_path / "response.json",
        cache_hit=False,
        response_retrieved_at="2026-08-17T10:00:01Z",
        artifact_written_at="2026-08-17T10:00:02Z",
        overwrite=False,
    )
    assert response.read_bytes() == raw
    assert json.loads(sidecar.read_text())["response_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (b"<html><body>rate limited</body></html>", "non_json"),
        (b"", "empty"),
        (b'["provider error"]', "json_other"),
        (b'{"error":"provider error"}', "json_object"),
        (b'{"error":NaN}', "non_json"),
        (b'{"error":Infinity}', "non_json"),
        (b'{"error":-Infinity}', "non_json"),
    ],
)
def test_write_api_artifacts_archives_raw_error_evidence(tmp_path, raw, status):
    """Fails if failed-provider evidence is rejected or silently normalized."""
    response, sidecar = artifacts.write_api_artifacts(
        body=None,
        raw_response_bytes=raw,
        payload={"chain": "base"},
        endpoint="tgm/flows",
        output_path=tmp_path / "response.json",
        cache_hit=False,
        response_retrieved_at="2026-08-17T10:00:01Z",
        artifact_written_at="2026-08-17T10:00:02Z",
        overwrite=False,
    )
    assert response.read_bytes() == raw
    metadata = json.loads(sidecar.read_text())
    assert metadata["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["response_parse_status"] == status


def test_write_api_artifacts_rejects_mismatched_parsed_raw_response(tmp_path):
    """Fails if the sidecar can attest to bytes different from the parsed response."""
    with pytest.raises(ValueError, match="does not match"):
        artifacts.write_api_artifacts(
            body={"data": ["parsed"]},
            raw_response_bytes=b'{"data":["different"]}',
            payload={},
            endpoint="tgm/flows",
            output_path=tmp_path / "response.json",
            cache_hit=False,
            response_retrieved_at="2026-08-17T10:00:01Z",
            artifact_written_at="2026-08-17T10:00:02Z",
            overwrite=False,
        )


def test_write_api_artifacts_requires_object_body_for_successful_response(tmp_path):
    """Fails if an endpoint success artifact can be an unstructured JSON value."""
    with pytest.raises(ValueError, match="JSON object"):
        artifacts.write_api_artifacts(
            body=["not an endpoint object"],
            payload={},
            endpoint="tgm/flows",
            output_path=tmp_path / "response.json",
            cache_hit=False,
            response_retrieved_at="2026-08-17T10:00:01Z",
            artifact_written_at="2026-08-17T10:00:02Z",
            overwrite=False,
        )


def test_write_bytes_once_rejects_identical_existing_target(tmp_path):
    """Fails if immutable output accepts a second write just because its bytes match."""
    target = tmp_path / "immutable.json"
    artifacts.write_bytes_once(target, b"same")
    with pytest.raises(FileExistsError):
        artifacts.write_bytes_once(target, b"same")
    assert target.read_bytes() == b"same"


def test_write_bytes_once_or_adopt_exact_accepts_only_matching_bytes(tmp_path):
    """Fails if deterministic recovery adopts bytes it did not intend to produce."""
    target = tmp_path / "derived.json"
    target.write_bytes(b"expected")
    assert artifacts.write_bytes_once_or_adopt_exact(
        target, b"expected", metadata={"kind": "derived"}
    ) == target
    with pytest.raises(RuntimeError, match="collision"):
        artifacts.write_bytes_once_or_adopt_exact(
            target, b"different", metadata={"kind": "derived"}
        )
    digest = hashlib.sha256(b"different").hexdigest()
    conflict_dir = tmp_path / ".conflicts" / target.name
    assert (conflict_dir / f"{digest}.bin").read_bytes() == b"different"
    assert json.loads((conflict_dir / f"{digest}.json").read_text()) == {"kind": "derived"}


def test_install_or_quarantine_bytes_once_preserves_different_collision(tmp_path):
    """Fails if a different received artifact overwrites evidence or is discarded."""
    target = tmp_path / "response.json"
    target.write_bytes(b"installed")
    with pytest.raises(RuntimeError, match="collision"):
        artifacts.install_or_quarantine_bytes_once(
            target,
            b"received",
            metadata={"endpoint": "tgm/flows", "reason": "sha mismatch"},
        )
    digest = hashlib.sha256(b"received").hexdigest()
    conflict_dir = tmp_path / ".conflicts" / target.name
    assert target.read_bytes() == b"installed"
    assert (conflict_dir / f"{digest}.bin").read_bytes() == b"received"
    assert json.loads((conflict_dir / f"{digest}.json").read_text()) == {
        "endpoint": "tgm/flows",
        "reason": "sha mismatch",
    }


def test_canonical_json_and_atomic_replace_bytes_write_canonical_durable_output(tmp_path):
    """Fails if deterministic JSON changes its bytes or replacement leaves stale bytes."""
    target = tmp_path / "output.json"
    assert artifacts.canonical_json_bytes({"z": 1, "a": "é"}) == b'{"a":"\xc3\xa9","z":1}\n'
    assert artifacts.atomic_replace_bytes(target, b"first") == target
    assert artifacts.atomic_replace_bytes(target, b"second") == target
    assert target.read_bytes() == b"second"
    assert artifacts.write_json_once(tmp_path / "canonical.json", {"z": 1, "a": "é"}).read_bytes() == b'{"a":"\xc3\xa9","z":1}\n'
