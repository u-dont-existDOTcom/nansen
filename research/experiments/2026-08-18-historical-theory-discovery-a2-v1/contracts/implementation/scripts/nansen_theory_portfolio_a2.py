#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from programs.nansen_theory_portfolio.runner import PortfolioError  # noqa: E402
from programs.nansen_theory_portfolio_a2 import (  # noqa: E402
    check_program_a2,
    initialize_program_a2,
    run_program_a2,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate frozen historical Program A2")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-a2", help="initialize successor Program A2 offline")
    run = commands.add_parser("run-a2", help="run or resume Program A2")
    run.add_argument("--manifest", type=Path, required=True)
    check = commands.add_parser("check-a2", help="verify Program A2 offline")
    check.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init-a2":
            result = {"manifest": str(initialize_program_a2(REPO_ROOT))}
        elif args.command == "run-a2":
            result = run_program_a2(args.manifest)
        else:
            result = check_program_a2(args.manifest)
    except PortfolioError as exc:
        print(f"portfolio A2 error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
