#!/usr/bin/env python3
"""Independent desktop alerts for the frozen Nansen research program.

This file deliberately lives outside the rapid program's frozen source glob.
It observes durable artifacts and systemd state but never imports, mutates, or
advances the research protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROGRAM_ID = "2026-08-20-rapid-prospective-parallel-strategy-v1"
DEFAULT_MANIFEST = Path(
    "/mnt/hdd/home/joel/Téléchargements/nansen-signal-lab/"
    "research/experiments/2026-08-20-rapid-prospective-parallel-strategy-v1/"
    "program.json"
)
RESEARCH_TIMER = "nansen-signal-lab-rapid-research.timer"
RESEARCH_SERVICE = "nansen-signal-lab-rapid-research.service"


class AlertMonitorError(RuntimeError):
    """Raised when alert observation state cannot be trusted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_object(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise AlertMonitorError(f"{label} is absent or unsafe: {path}")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise AlertMonitorError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise AlertMonitorError(f"{label} must be a JSON object")
    return value, raw


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = _canonical(value)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _event_name(event_id: str) -> str:
    return f"{_sha256(event_id.encode('utf-8'))}.json"


def queue_event(
    state_root: Path,
    *,
    event_id: str,
    title: str,
    body: str,
    urgency: str,
) -> Path:
    if urgency not in {"low", "normal", "critical"}:
        raise AlertMonitorError("alert urgency is invalid")
    path = state_root / "events" / _event_name(event_id)
    value = {
        "schema_version": 1,
        "event_id": event_id,
        "title": title,
        "body": body,
        "urgency": urgency,
        "created_at": _utc_now(),
    }
    if path.exists():
        existing, _ = _read_object(path, label="queued alert")
        comparable = {key: existing.get(key) for key in value if key != "created_at"}
        expected = {key: item for key, item in value.items() if key != "created_at"}
        if comparable != expected:
            raise AlertMonitorError("queued alert identity was reused with different content")
        return path
    _atomic_json(path, value)
    return path


def _notify(event: dict[str, Any]) -> bool:
    command = [
        "/usr/bin/notify-send",
        "--app-name=Nansen Research",
        f"--urgency={event['urgency']}",
        "--expire-time=0" if event["urgency"] == "critical" else "--expire-time=15000",
        str(event["title"]),
        str(event["body"]),
    ]
    try:
        result = subprocess.run(command, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def deliver_pending(
    state_root: Path,
    *,
    notifier: Callable[[dict[str, Any]], bool] = _notify,
) -> int:
    events = state_root / "events"
    if not events.exists():
        return 0
    if events.is_symlink() or not events.is_dir():
        raise AlertMonitorError("alert event directory is unsafe")
    delivered_count = 0
    for event_path in sorted(events.glob("*.json")):
        if event_path.is_symlink():
            raise AlertMonitorError("queued alert cannot be a symlink")
        delivered_path = state_root / "delivered" / event_path.name
        if delivered_path.exists():
            continue
        event, raw = _read_object(event_path, label="queued alert")
        if set(event) != {
            "schema_version",
            "event_id",
            "title",
            "body",
            "urgency",
            "created_at",
        }:
            raise AlertMonitorError("queued alert schema differs")
        if not notifier(event):
            continue
        _atomic_json(
            delivered_path,
            {
                "schema_version": 1,
                "event_sha256": _sha256(raw),
                "delivered_at": _utc_now(),
            },
        )
        delivered_count += 1
    return delivered_count


def _systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/systemctl", "--user", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _unit_value(unit: str, property_name: str) -> str:
    result = _systemctl("show", unit, f"--property={property_name}", "--value")
    if result.returncode != 0:
        raise AlertMonitorError(f"cannot inspect systemd unit {unit}")
    return result.stdout.strip()


def detect_events(manifest_path: Path, state_root: Path) -> None:
    manifest, _ = _read_object(manifest_path, label="rapid research manifest")
    if manifest.get("program_id") != PROGRAM_ID:
        raise AlertMonitorError("rapid research manifest identity differs")
    root = manifest_path.parent

    enabled = _systemctl("is-enabled", RESEARCH_TIMER)
    active = _systemctl("is-active", RESEARCH_TIMER)
    if enabled.stdout.strip() != "enabled" or active.stdout.strip() != "active":
        queue_event(
            state_root,
            event_id="rapid-timer-not-running",
            title="Nansen research needs attention",
            body=(
                "The rapid-research timer is not enabled and active. "
                "Reopen Codex and ask it to repair the research timer."
            ),
            urgency="critical",
        )

    result = _unit_value(RESEARCH_SERVICE, "Result")
    invocation = _unit_value(RESEARCH_SERVICE, "InvocationID") or "unknown"
    if result not in {"", "success"}:
        queue_event(
            state_root,
            event_id=f"rapid-service-failure:{invocation}",
            title="Nansen research service failed",
            body=(
                f"The research service reported {result}. "
                "Reopen Codex and ask it to inspect the rapid research failure."
            ),
            urgency="critical",
        )

    fatal_path = root / "intents/program-fatal.json"
    if fatal_path.exists():
        fatal, raw = _read_object(fatal_path, label="program fatal intent")
        reason = str(fatal.get("reason", "unspecified fatal condition"))[:300]
        queue_event(
            state_root,
            event_id=f"program-fatal:{_sha256(raw)}",
            title="Nansen research stopped safely",
            body=(
                f"The program stopped before making further requests: {reason}. "
                "Reopen Codex; your input may be needed."
            ),
            urgency="critical",
        )

    discovery_path = root / "derived/discovery-family.json"
    if discovery_path.exists():
        _, raw = _read_object(discovery_path, label="discovery family")
        queue_event(
            state_root,
            event_id=f"discovery-ready:{_sha256(raw)}",
            title="Nansen preliminary results are ready",
            body=(
                "The discovery phase has been sealed. Reopen Codex and ask it "
                "to review the rapid research status."
            ),
            urgency="normal",
        )

    final_path = root / "derived/final-result.json"
    if final_path.exists():
        final, raw = _read_object(final_path, label="final result")
        stage = str(final.get("stage", "terminal"))
        queue_event(
            state_root,
            event_id=f"final-ready:{_sha256(raw)}",
            title="Nansen validated results are ready",
            body=(
                f"The rapid program reached its sealed {stage} result. "
                "Reopen Codex and ask it to review the final research result."
            ),
            urgency="normal",
        )


def queue_service_failure(state_root: Path, unit: str) -> None:
    if unit != RESEARCH_SERVICE:
        raise AlertMonitorError("failure alert was requested for an unknown unit")
    invocation = _unit_value(unit, "InvocationID") or _utc_now()
    result = _unit_value(unit, "Result") or "failed"
    status = _unit_value(unit, "ExecMainStatus") or "unknown"
    queue_event(
        state_root,
        event_id=f"rapid-service-failure:{invocation}",
        title="Nansen research service failed",
        body=(
            f"The timer action failed (result={result}, status={status}). "
            "The research protocol stopped or will retry safely. Reopen Codex "
            "and ask it to inspect the failure."
        ),
        urgency="critical",
    )


def _default_state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local/state"
    return base / "nansen-signal-lab-alerts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Nansen research desktop alerts")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--state-root", type=Path, default=_default_state_root())
    parser.add_argument("--service-failure", metavar="UNIT")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        queue_event(
            args.state_root,
            event_id=f"setup-test:{_utc_now()}",
            title="Nansen research alerts enabled",
            body=(
                "Desktop alerts are active. You can close Codex; important "
                "research failures and result milestones will appear here."
            ),
            urgency="normal",
        )
    elif args.service_failure:
        queue_service_failure(args.state_root, args.service_failure)
    else:
        detect_events(args.manifest.resolve(), args.state_root)

    delivered = deliver_pending(args.state_root)
    print(json.dumps({"delivered": delivered, "state_root": str(args.state_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
