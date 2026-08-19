#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from programs.nansen_parallel_strategy_v1.supervisor import DueAction, drain_due_actions


def _network_ready() -> None:
    clock = subprocess.run(
        ("/usr/bin/timedatectl", "show", "-p", "NTPSynchronized", "--value"),
        check=False,
        capture_output=True,
        text=True,
    )
    if clock.returncode or clock.stdout.strip() != "yes":
        raise RuntimeError("host clock is not NTP-synchronized")
    network = subprocess.run(("/usr/bin/nm-online", "-q", "--timeout=30"), check=False)
    if network.returncode:
        raise RuntimeError("NetworkManager did not confirm network readiness")


def execute(manifest: Path, action: DueAction) -> None:
    if action.requires_network:
        _network_ready()
    repository = Path(__file__).resolve().parents[1]
    cli = repository / "scripts/nansen_parallel_strategy.py"
    command = [str(repository / ".venv/bin/python"), str(cli), action.command, "--manifest", str(manifest)]
    if action.cycle_index is not None:
        command.extend(("--cycle", str(action.cycle_index)))
    print(
        json.dumps(
            {
                "action": action.command,
                "cycle_index": action.cycle_index,
                "due_at": action.due_at.isoformat().replace("+00:00", "Z"),
                "requires_network": action.requires_network,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    completed = subprocess.run(command, cwd=repository, check=False)
    if completed.returncode:
        raise RuntimeError(f"parallel-strategy action failed: {action.command}")
    verification = [
        str(repository / ".venv/bin/python"),
        str(cli),
        "check-cycle" if action.cycle_index is not None else "check",
        "--manifest",
        str(manifest),
    ]
    if action.cycle_index is not None:
        verification.extend(("--cycle", str(action.cycle_index)))
    checked = subprocess.run(verification, cwd=repository, check=False)
    if checked.returncode:
        raise RuntimeError(
            f"parallel-strategy post-action integrity check failed: {action.command}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance all due parallel-strategy actions")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    manifest = args.manifest.resolve()
    if args.dry_run:
        from programs.nansen_parallel_strategy_v1.supervisor import choose_due_action

        action = choose_due_action(manifest, datetime.now(timezone.utc))
        print(json.dumps(None if action is None else action.__dict__, default=str, sort_keys=True))
        return 0
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    directory = runtime / "nansen-signal-lab-parallel-strategy"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "supervisor.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        drain_due_actions(
            manifest,
            clock=lambda: datetime.now(timezone.utc),
            executor=lambda action: execute(manifest, action),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
