#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from src.nansen_signal_lab.client import NansenClient

from programs.nansen_parallel_strategy_v1.activation import (
    operational_reconciliation,
    seal_operational_reconciliation,
)
from programs.nansen_parallel_strategy_v1.budget import ParallelStrategyBudget
from programs.nansen_parallel_strategy_v1.design import SCHEDULE
from programs.nansen_parallel_strategy_v1.evidence import EvidenceTransport
from programs.nansen_parallel_strategy_v1.runner import (
    check_cycle,
    check_program,
    finalize_program,
    freeze_discovery,
    repair_program_fatal,
    run_predecision,
    run_settlement,
)
from programs.nansen_parallel_strategy_v1.runtime import produce_terminal_v1_attestation
from programs.nansen_parallel_strategy_v1.schema import initialize_program, replay_program


def _live_transport(manifest: Path) -> EvidenceTransport:
    root = manifest.parent
    reconciliation_path = seal_operational_reconciliation(manifest)
    document = json.loads(reconciliation_path.read_bytes())
    budget = ParallelStrategyBudget(root, document)
    return EvidenceTransport(root, budget, NansenClient(), clock=lambda: datetime.now(timezone.utc))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen parallel-strategy prospective program")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("initialize")
    init.add_argument("--repo", type=Path, default=Path.cwd())
    for name in ("activate", "reconcile", "check", "freeze-discovery", "finalize", "replay", "repair-fatal"):
        item = commands.add_parser(name)
        item.add_argument("--manifest", required=True, type=Path)
    for name in ("predecision", "settlement"):
        item = commands.add_parser(name)
        item.add_argument("--manifest", required=True, type=Path)
        item.add_argument("--cycle", required=True, type=int)
    cycle_check = commands.add_parser("check-cycle")
    cycle_check.add_argument("--manifest", required=True, type=Path)
    cycle_check.add_argument("--cycle", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv(Path.cwd() / ".env")
    if args.command == "initialize":
        print(initialize_program(args.repo.resolve()))
        return 0
    manifest = args.manifest.resolve()
    if args.command == "activate":
        value = produce_terminal_v1_attestation(manifest)
        seal_operational_reconciliation(manifest)
        print(json.dumps({"stage": "activated", "terminal_cycles": value["terminal_cycles"]}, sort_keys=True))
        return 0
    if args.command == "reconcile":
        path = seal_operational_reconciliation(manifest)
        print(json.dumps({"path": str(path), "balances": operational_reconciliation(manifest)["opening_balance_candidates"]}, sort_keys=True))
        return 0
    if args.command == "replay":
        print(json.dumps(replay_program(manifest), sort_keys=True))
        return 0
    if args.command == "check":
        print(json.dumps(check_program(manifest), sort_keys=True))
        return 0
    if args.command == "check-cycle":
        print(json.dumps(check_cycle(manifest, args.cycle), sort_keys=True))
        return 0
    if args.command == "freeze-discovery":
        print(freeze_discovery(manifest))
        return 0
    if args.command == "repair-fatal":
        print(json.dumps(repair_program_fatal(manifest), sort_keys=True))
        return 0
    if args.command == "finalize":
        path = finalize_program(manifest)
        check_program(manifest)
        print(path)
        return 0
    transport = _live_transport(manifest)
    if args.command == "predecision":
        state = run_predecision(manifest, args.cycle, transport=transport)
    else:
        state = run_settlement(manifest, args.cycle, transport=transport)
    print(json.dumps(state, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
