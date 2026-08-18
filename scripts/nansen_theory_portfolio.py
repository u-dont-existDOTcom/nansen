#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from programs.nansen_theory_portfolio.runner import (  # noqa: E402
    PortfolioError,
    check_program_a,
    initialize_program_a,
    run_program_a,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the frozen Nansen theory portfolio")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-a", help="initialize historical discovery Program A offline")
    run = subparsers.add_parser("run-a", help="run or resume historical discovery Program A")
    run.add_argument("--manifest", type=Path, required=True)
    check = subparsers.add_parser("check-a", help="verify Program A without provider access")
    check.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init-a":
            result = {"manifest": str(initialize_program_a(REPO_ROOT))}
        elif args.command == "run-a":
            result = run_program_a(args.manifest)
        else:
            result = check_program_a(args.manifest)
    except PortfolioError as exc:
        print(f"portfolio error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
