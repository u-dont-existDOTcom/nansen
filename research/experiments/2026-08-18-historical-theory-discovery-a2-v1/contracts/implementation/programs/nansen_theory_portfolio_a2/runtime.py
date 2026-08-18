from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics
import sys
import types
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable


PROGRAM_A2_ID = "2026-08-18-historical-theory-discovery-a2-v1"
PROGRAM_A2_PORTFOLIO_ID = "2026-08-18-nansen-theory-successor-portfolio-v1"
PROGRAM_A2_DESIGN_PATH = (
    "docs/superpowers/specs/2026-08-18-nansen-theory-portfolio-a2-v1.md"
)
PROGRAM_A2_RELATIVE_ROOT = Path("research/experiments") / PROGRAM_A2_ID
PROGRAM_A2_PORTFOLIO_ROOT = Path("research/portfolios") / PROGRAM_A2_PORTFOLIO_ID
PROGRAM_A2_MAX_CALLS = 1_668
PROGRAM_A2_MAX_CREDITS = 6_613
PROGRAM_A2_ALLOCATION = 7_463
PROGRAM_A2_REQUIRED_INITIAL_REMAINING = 49_526
PROGRAM_A2_ALLOWED_INITIAL_REMAINING = frozenset({49_526, 49_531})
PREDECESSOR_CONSERVATIVE_CREDITS = 537
PREDECESSOR_PROGRAM_ID = "2026-08-18-historical-theory-discovery-a-v1"
PREDECESSOR_PORTFOLIO_ID = "2026-08-18-nansen-theory-portfolio-v1"
PREDECESSOR_TERMINAL_REASON = (
    "request a06-s03-solana-near_zero/flow halted the portfolio: Nansen HTTP 422"
)
BLOCKED_IDENTITY = (
    "solana",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
)
BLOCKED_REASON = "source_endpoint_unsupported"

PREDECESSOR_ROOT = Path("research/experiments") / PREDECESSOR_PROGRAM_ID
PREDECESSOR_PORTFOLIO = (
    Path("research/portfolios") / PREDECESSOR_PORTFOLIO_ID / "portfolio.json"
)
PREDECESSOR_EVIDENCE = PREDECESSOR_ROOT / (
    "anchors/anchor-06/raw/nansen/7cab75550ef61b61fb6e4be0/"
    "attempt-1-response.json"
)
PREDECESSOR_EVIDENCE_METADATA = PREDECESSOR_ROOT / (
    "anchors/anchor-06/raw/nansen/7cab75550ef61b61fb6e4be0/"
    "attempt-1-response-metadata.json"
)
PREDECESSOR_FINAL_SEAL = PREDECESSOR_ROOT / "seals/final.json"
PREDECESSOR_PROGRAM = PREDECESSOR_ROOT / "program.json"
PREDECESSOR_CANDIDATES = PREDECESSOR_ROOT / "contracts/candidates.json"
PREDECESSOR_HASHES = {
    PREDECESSOR_EVIDENCE: "bfec38c3d9daaff26d98c866df78306c990dc401f51c16166886dabe414b8b75",
    PREDECESSOR_EVIDENCE_METADATA: "bcedbc6f864b751f58ad650645dcf19f3c945156bd61e2d90c6bc6b2f22d6c69",
    PREDECESSOR_FINAL_SEAL: "3132f1bfaa5e99d535bd6ded819f9751a44bede2f6dbf0bf60689cd0c9c49230",
    PREDECESSOR_PROGRAM: "1bf53b689b8a582d53ccbe834d7097d7ade848dc4e52dfdae3a013e1e134b076",
    PREDECESSOR_PORTFOLIO: "c3fcd91d91f25cb70dc98e6ceba9c059aa46a76ab3e0761a6b2b98f8aa76c3bd",
    PREDECESSOR_CANDIDATES: "aa4d1085a0b3594a8a255584e0aec7a0bdab0a6438bcc63b7af0076a9f5d056a",
}


def _load_isolated_v1() -> tuple[Any, Any]:
    """Load the frozen implementation without mutating its import namespace."""

    source_root = Path(__file__).resolve().parents[1] / "nansen_theory_portfolio"
    package_name = f"{__package__}._isolated_v1"
    package = types.ModuleType(package_name)
    package.__package__ = package_name
    package.__path__ = [str(source_root)]
    sys.modules[package_name] = package

    loaded: dict[str, Any] = {}
    for name in ("design", "budget", "runner"):
        module_name = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(
            module_name,
            source_root / f"{name}.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot isolate Program A module {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["design"], loaded["runner"]


design_v1, runner_v1 = _load_isolated_v1()

_ORIGINAL_PROGRAM_ID = design_v1.PROGRAM_A_ID
_ORIGINAL_DESIGN_PATH = design_v1.DESIGN_PATH
_ORIGINAL_ANCHORS = design_v1.ANCHORS
_ORIGINAL_SLOTS = design_v1.PLANNED_SLOTS
_ORIGINAL_SELECT = design_v1.select_anchor_events
_ORIGINAL_SCORE = design_v1.score_candidates
_ORIGINAL_SOURCE_PATHS = runner_v1._source_paths
_ORIGINAL_CALL = runner_v1._call
_ORIGINAL_FRESH_OPENAPI = runner_v1._fresh_openapi_check
_ORIGINAL_VALIDATE_ARCHIVE = runner_v1._validate_anchor_archive
_ORIGINAL_VALIDATE_PROGRAM = runner_v1.validate_program_a
_ORIGINAL_INITIALIZE = runner_v1.initialize_program_a
_ORIGINAL_RUN = runner_v1.run_program_a
_ORIGINAL_CHECK = runner_v1.check_program_a
_ORIGINAL_COMPLETION_DOCUMENTS = runner_v1._completion_documents


def _a2_slots() -> tuple[Any, ...]:
    # The successor begins at untouched original anchor 7. Event IDs, chains,
    # strata, slot positions, and calibration flags remain exactly as frozen.
    return tuple(
        replace(slot, anchor_index=slot.anchor_index - 6)
        for slot in _ORIGINAL_SLOTS
        if slot.anchor_index >= 6
    )


A2_ANCHORS = _ORIGINAL_ANCHORS[6:]
A2_SLOTS = _a2_slots()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise runner_v1.PortfolioError(f"cannot read predecessor evidence {path}") from exc
    if not isinstance(value, dict):
        raise runner_v1.PortfolioError(f"predecessor evidence is not an object: {path}")
    return value


def _verify_predecessor(repo_root: Path) -> None:
    for relative, expected in PREDECESSOR_HASHES.items():
        path = repo_root / relative
        if path.is_symlink() or not path.is_file() or _sha256_file(path) != expected:
            raise runner_v1.PortfolioError(
                f"Program A2 predecessor evidence differs: {relative.as_posix()}"
            )

    final = _load_object(repo_root / PREDECESSOR_FINAL_SEAL)
    program = _load_object(repo_root / PREDECESSOR_PROGRAM)
    portfolio = _load_object(repo_root / PREDECESSOR_PORTFOLIO)
    response = _load_object(repo_root / PREDECESSOR_EVIDENCE)
    metadata = _load_object(repo_root / PREDECESSOR_EVIDENCE_METADATA)
    if (
        final.get("kind") != "fatal"
        or final.get("stage") != "unscorable"
        or final.get("terminal_reason") != PREDECESSOR_TERMINAL_REASON
        or final.get("authenticated_attempts") != 135
        or final.get("billable_credits") != PREDECESSOR_CONSERVATIVE_CREDITS
        or program.get("program_id") != PREDECESSOR_PROGRAM_ID
        or program.get("portfolio_id") != PREDECESSOR_PORTFOLIO_ID
        or program.get("stage") != "unscorable"
        or program.get("terminal_reason") != PREDECESSOR_TERMINAL_REASON
        or program.get("final_seal_sha256") != PREDECESSOR_HASHES[PREDECESSOR_FINAL_SEAL]
        or program.get("candidate_contract_sha256")
        != PREDECESSOR_HASHES[PREDECESSOR_CANDIDATES]
        or portfolio.get("portfolio_id") != PREDECESSOR_PORTFOLIO_ID
        or portfolio.get("max_new_research_credits") != 48_000
        or portfolio.get("allocations", {}).get("historical_pit_discovery") != 8_000
        or response.get("status") != 422
        or response.get("code") != "invalid_field_value"
        or BLOCKED_IDENTITY[1] not in response.get("message", "")
        or metadata.get("status_code") != 422
        or metadata.get("body_parse_status") != "json_object"
        or metadata.get("credit_cost") is not None
        or metadata.get("credit_used") is not None
        or metadata.get("credit_remaining") is not None
    ):
        raise runner_v1.PortfolioError("Program A2 predecessor semantics differ")
    artifacts = final.get("artifacts")
    evidence_path = PREDECESSOR_EVIDENCE.relative_to(PREDECESSOR_ROOT).as_posix()
    metadata_path = PREDECESSOR_EVIDENCE_METADATA.relative_to(
        PREDECESSOR_ROOT
    ).as_posix()
    expected_artifacts = {
        evidence_path: PREDECESSOR_HASHES[PREDECESSOR_EVIDENCE],
        metadata_path: PREDECESSOR_HASHES[PREDECESSOR_EVIDENCE_METADATA],
    }
    if not isinstance(artifacts, list) or any(
        not any(
            record.get("path") == path and record.get("sha256") == digest
            for record in artifacts
            if isinstance(record, dict)
        )
        for path, digest in expected_artifacts.items()
    ):
        raise runner_v1.PortfolioError("Program A2 predecessor seal omits 422 evidence")

    # Replay the pristine terminal program as a second, independent proof of
    # the entire ledger/seal chain. This module is deliberately not patched by
    # A2's isolated implementation namespace.
    from programs.nansen_theory_portfolio import runner as pristine_runner

    try:
        replay = pristine_runner.check_program_a(repo_root / PREDECESSOR_PROGRAM)
    except Exception as exc:
        raise runner_v1.PortfolioError("Program A2 predecessor replay failed") from exc
    if replay != {
        "stage": "unscorable",
        "terminal_anchors": 5,
        "authenticated_attempts": 135,
        "billable_credits": PREDECESSOR_CONSERVATIVE_CREDITS,
    }:
        raise runner_v1.PortfolioError("Program A2 predecessor replay totals differ")
    anchor_six = repo_root / PREDECESSOR_ROOT / "anchors/anchor-06"
    guard = pristine_runner.HistoricalPricingGuard(
        anchor_six, *pristine_runner._epoch_limits(5)
    )
    totals = guard.replay()
    ambiguous = [entry for entry in totals.entries if entry.state == "ambiguous"]
    if (
        totals.calls != 9
        or totals.credits != 40
        or totals.provider_remaining != 49_531
        or totals.halted_reason != "ambiguous transmitted request"
        or len(ambiguous) != 1
        or ambiguous[0].logical_request_id
        != "a06-s03-solana-near_zero/flow"
        or ambiguous[0].expected_credits != 5
    ):
        raise runner_v1.PortfolioError("Program A2 predecessor ambiguity differs")


def _select_anchor_events(
    slots: Iterable[Any], eligible: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events = _ORIGINAL_SELECT(slots, eligible)
    result: list[dict[str, Any]] = []
    for event in events:
        identity = (event.get("chain"), event.get("token_address"))
        if event.get("status") == "selected" and identity == BLOCKED_IDENTITY:
            unavailable = dict(event)
            unavailable.update({"status": "unavailable", "reason": BLOCKED_REASON})
            result.append(unavailable)
        else:
            result.append(event)
    return result


def _epoch_limits(anchor_index: int) -> tuple[int, int]:
    event_count = len(tuple(slot for slot in A2_SLOTS if slot.anchor_index == anchor_index))
    if event_count not in {6, 7}:
        raise runner_v1.PortfolioError("A2 anchor schedule event count differs")
    return (4 + 4 * event_count, 15 + 16 * event_count)


def _score_candidates(
    records: Iterable[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    materialized = list(records)
    result = _ORIGINAL_SCORE(materialized)
    selected = [row for row in materialized if row[0].get("status") == "selected"]
    scores = {score["candidate_id"]: score for score in result["scores"]}
    for candidate in design_v1.CANDIDATES:
        if candidate.get("cash"):
            continue
        blocks: dict[int, list[float]] = {}
        for event, feature, outcome in selected:
            values = design_v1.predicate_values(event, feature)
            if design_v1._candidate_decision(values, candidate["predicates"]) != "long":
                continue
            if outcome.get("available") is not True:
                continue
            anchor_offset = (date.fromisoformat(event["anchor"]) - A2_ANCHORS[0]).days // 7
            blocks.setdefault(anchor_offset // 12, []).append(float(outcome["base_return"]))
        score = scores[candidate["id"]]
        score["positive_calendar_blocks"] = sum(
            statistics.median(values) > 0 for values in blocks.values()
        )
        score["represented_calendar_blocks"] = len(blocks)

    selectable = [score for score in result["scores"] if score.get("support_eligible")]
    ranked = sorted(selectable, key=lambda score: score["candidate_id"])
    ranked.sort(key=design_v1._candidate_rank_key, reverse=True)
    apriori = next(score for score in result["scores"] if score.get("apriori"))
    advanced = [apriori["candidate_id"]]
    for score in ranked:
        if score["candidate_id"] not in advanced:
            advanced.append(score["candidate_id"])
        if len(advanced) == 5:
            break
    result["program_b_candidate_ids"] = advanced
    return result


def _completion_documents(
    program_root: Path, statuses: list[str | None]
) -> tuple[dict[str, Any], dict[str, Any], int, int, str, str | None]:
    ranking, calibration, calls, credits, _, _ = _ORIGINAL_COMPLETION_DOCUMENTS(
        program_root, statuses
    )
    records = runner_v1._final_records(program_root)
    selected = ranking["selected_opportunities"]
    proxy_available = sum(
        outcome.get("available") is True for _, _, outcome in records
    )
    support = {
        "complete_anchor_gate": statuses.count("complete") >= 54,
        "selected_opportunity_gate": selected >= 287,
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
    ranking["program_support"] = support
    unavailable = [event for event, _, _ in records if event.get("status") != "selected"]

    def grouped(field: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in unavailable:
            key = event.get(field)
            normalized = key if isinstance(key, str) and key else "unknown"
            counts[normalized] = counts.get(normalized, 0) + 1
        return dict(sorted(counts.items()))

    ranking["selection_missingness"] = {
        "total": len(unavailable),
        "by_reason": grouped("reason"),
        "by_chain": grouped("chain"),
        "by_stratum": grouped("stratum"),
    }
    discovery_shortlist = list(ranking["program_b_candidate_ids"])
    ranking["discovery_shortlist_candidate_ids"] = discovery_shortlist
    ranking["program_b_candidate_ids"] = discovery_shortlist if support["passed"] else []
    ranking["b2_eligibility"] = {
        "eligible": support["passed"],
        "required_stage": "completed",
        "candidate_ids": list(ranking["program_b_candidate_ids"]),
    }
    stage = "completed" if support["passed"] else "unscorable"
    reason = None if support["passed"] else "insufficient_program_a2_support"
    return ranking, calibration, calls, credits, stage, reason


def _completion_report(
    terminal: dict[str, Any], ranking: dict[str, Any], calibration: dict[str, Any]
) -> bytes:
    return (
        "# Historical theory discovery A2\n\n"
        f"Status: **{terminal['stage']} discovery**. This result is not confirmatory.\n\n"
        f"- Complete anchors: {terminal['complete_anchors']} / {len(A2_ANCHORS)}\n"
        f"- Unscorable anchors: {terminal['unscorable_anchors']}\n"
        f"- Authenticated attempts: {terminal['authenticated_attempts']} / {PROGRAM_A2_MAX_CALLS}\n"
        f"- Billable credits: {terminal['billable_credits']} / {PROGRAM_A2_MAX_CREDITS}\n"
        f"- Selected opportunities: {ranking['selected_opportunities']} / {len(A2_SLOTS)}\n"
        f"- Execution calibrations available: {calibration['overall']['execution_available']} / {len(A2_ANCHORS)}\n"
        f"- B2-eligible candidate IDs: {', '.join(ranking['program_b_candidate_ids']) or 'none'}\n"
        f"- Unavailable planned slots: {ranking['selection_missingness']['total']}\n"
        "- Historical exchange flow is an H5 analogue, not exact exchange-inventory evidence.\n"
    ).encode("utf-8")


def _portfolio_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "portfolio_id": PROGRAM_A2_PORTFOLIO_ID,
        "owner_authority": "continue-generating-and-testing-theories-until-credits-are-used",
        "supersedes": [
            "v1-terminal-A-underspend-to-D-only",
            "v1-B-accepts-only-A1-candidates",
        ],
        "predecessor_program_id": PREDECESSOR_PROGRAM_ID,
        "predecessor_program_sha256": PREDECESSOR_HASHES[PREDECESSOR_PROGRAM],
        "predecessor_final_sha256": PREDECESSOR_HASHES[PREDECESSOR_FINAL_SEAL],
        "predecessor_conservative_credits": PREDECESSOR_CONSERVATIVE_CREDITS,
        "proved_provider_floor": PROGRAM_A2_REQUIRED_INITIAL_REMAINING,
        "active_cohort_remaining_reserve": design_v1.ACTIVE_COHORT_RESERVE,
        "safety_margin": design_v1.PORTFOLIO_SAFETY_CREDITS,
        "max_remaining_research_credits": 47_463,
        "allocations": {
            "historical_successor_a2": PROGRAM_A2_ALLOCATION,
            "prospective_discovery_validation_b2": 12_240,
            "primary_prospective_holdout_c2": 13_786,
            "temporal_replication_d2": 13_974,
        },
        "a2_max_billable_credits": PROGRAM_A2_MAX_CREDITS,
        "roll_forward": "verified-A2-underspend-to-D2-only",
        "no_further_historical_successor": True,
        "a2_candidates_may_feed": "separately-frozen-B2-only",
        "accounting_authority": "predecessor-terminal-seal-plus-successor-ledgers",
    }


def _preregistration_text() -> bytes:
    return f"""# Preregistration — {PROGRAM_A2_ID}

Status: **preregistered discovery; no authenticated successor request has run**.

This separately versioned successor never pools, imports, or scores Program A
panels, features, calibrations, outcomes, or returns. It excludes Program A's
partly observed anchor 6 and uses the exact untouched original schedule slice
from anchors 7–65: 59 historical anchors, 358 fixed slots, and 59 DEX
calibrations. Original event IDs and schedule attributes are preserved.

- Maximum authenticated attempts: {PROGRAM_A2_MAX_CALLS}.
- Maximum billable credits: {PROGRAM_A2_MAX_CREDITS} inside the conservative
  {PROGRAM_A2_ALLOCATION}-credit successor allocation.
- Initial account remaining must be exactly 49,526 or 49,531 credits. Both
  retain the predecessor's ambiguous five-credit reservation; neither expands
  A2 authority. All B2/C2/D2 allocations, the 1,736-credit active-cohort
  reserve, and the 327-credit safety margin remain intact.
- The sole learned operability rule is endpoint+chain+address exact:
  `historical-token-flow-summary:{BLOCKED_IDENTITY[0]}:{BLOCKED_IDENTITY[1]}`.
  Ordinary frozen selection and duplicate handling run first. If that identity
  is selected, the slot is UNAVAILABLE with reason `{BLOCKED_REASON}` and keeps
  its raw and normalized provenance. It receives no event calls and no
  replacement, borrowing, reroll, symbol inference, sector inference, or
  page-two query.
- Exact predecessor response/final/program/portfolio/candidate hashes and
  terminal facts are validated before public or authenticated access. Only the
  sealed 422 compatibility fact and frozen schedule/candidate semantics are
  admissible; no Program A market evidence enters A2.
- Candidate stability uses five frozen calendar blocks: anchors 7–18, 19–30,
  31–42, 43–54, and 55–65 (12/12/12/12/11 anchors).
- Completion requires at least 54 of 59 complete anchors, 287 of 358 planned
  slots selected, and at least 90% common proxy coverage. Candidate gates are
  unchanged: 20 signals, ten tokens, eight weeks, 80% decision availability,
  90% common outcomes, and no missing emitted-signal outcomes.
- A2 is discovery-only. Only its sealed shortlist may feed a separately
  preregistered B2; it is not Program A evidence and cannot claim v1-B
  eligibility or profitability.
- Any other provider error or ambiguous transmission terminalizes A2 globally.
  A2 failure ends historical discovery: there is no A3, and A2 underspend may
  roll only into fixed-rule D2 replication.
- No public or authenticated request starts at or after 2026-08-20T10:42:00Z;
  the supervisor hard-stops at 10:43:30Z and restores the cohort timer.
""".encode("utf-8")


def _source_paths(repo_root: Path) -> tuple[Path, ...]:
    current_design = runner_v1.DESIGN_PATH
    runner_v1.DESIGN_PATH = _ORIGINAL_DESIGN_PATH
    try:
        paths = list(_ORIGINAL_SOURCE_PATHS(repo_root))
    finally:
        runner_v1.DESIGN_PATH = current_design
    paths.extend(sorted((repo_root / "programs/nansen_theory_portfolio_a2").glob("*.py")))
    paths.extend(
        [
            repo_root / "scripts/nansen_theory_portfolio_a2.py",
            repo_root / PROGRAM_A2_DESIGN_PATH,
            repo_root / "operations/nansen-signal-lab-program-a2.service",
            repo_root / "tests/test_historical_theory_a2.py",
            *[repo_root / relative for relative in PREDECESSOR_HASHES],
        ]
    )
    missing = [path for path in paths if not path.is_file() or path.is_symlink()]
    if missing:
        raise runner_v1.PortfolioError(f"A2 runtime source is missing: {missing[0]}")
    if len({path.resolve() for path in paths}) != len(paths):
        raise runner_v1.PortfolioError("A2 runtime source set contains duplicates")
    return tuple(paths)


def _account_remaining(program_root: Path, anchor_index: int) -> int | None:
    root = runner_v1._anchor_root(program_root, anchor_index)
    guard = runner_v1.HistoricalPricingGuard(root, *_epoch_limits(anchor_index))
    logical_id = f"anchor-{anchor_index + 1:02d}/account"
    entries = [
        entry
        for entry in guard.replay().entries
        if entry.logical_request_id == logical_id
    ]
    if not entries:
        return None
    body = runner_v1._response_body(root, guard, logical_id)
    remaining = body.get("credits_remaining") if isinstance(body, dict) else None
    if not isinstance(remaining, int) or isinstance(remaining, bool):
        raise runner_v1.ProgramFatal("Program A2 account remaining is invalid")
    return remaining


def _validate_account_continuity(program_root: Path, anchor_index: int) -> None:
    remaining = _account_remaining(program_root, anchor_index)
    if remaining is None:
        return
    if anchor_index == 0:
        valid = remaining in PROGRAM_A2_ALLOWED_INITIAL_REMAINING
    else:
        first_remaining = _account_remaining(program_root, 0)
        if first_remaining not in PROGRAM_A2_ALLOWED_INITIAL_REMAINING:
            raise runner_v1.ProgramFatal(
                "Program A2 initial provider remaining does not settle the frozen predecessor"
            )
        _, prior_credits = runner_v1._completed_epoch_totals(
            program_root, anchor_index
        )
        valid = remaining == first_remaining - prior_credits
    if not valid:
        raise runner_v1.ProgramFatal(
            "Program A2 provider balance continuity differs from sealed spend"
        )


class _DeadlineClient:
    """Place the frozen cutoff immediately at both transport boundaries."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _admit() -> None:
        if runner_v1.datetime.now(runner_v1.timezone.utc) >= runner_v1.REQUEST_START_CUTOFF:
            raise runner_v1.ProgramFatal("program-A2 request-start cutoff reached")

    def fetch_openapi(self) -> bytes:
        self._admit()
        return self._client.fetch_openapi()

    def request_evidence(self, *args: Any, **kwargs: Any) -> Any:
        self._admit()
        return self._client.request_evidence(*args, **kwargs)


def _fresh_openapi_check(
    program_root: Path, anchor_root: Path, client: Any
) -> Path:
    return _ORIGINAL_FRESH_OPENAPI(
        program_root, anchor_root, _DeadlineClient(client)
    )


def _call(**kwargs: Any) -> Any:
    guarded = dict(kwargs)
    guarded["client"] = _DeadlineClient(kwargs["client"])
    body = _ORIGINAL_CALL(**guarded)
    logical_id = kwargs.get("logical_id")
    if (
        isinstance(logical_id, str)
        and logical_id.startswith("anchor-")
        and logical_id.endswith("/account")
    ):
        try:
            anchor_index = int(logical_id.split("/", 1)[0].split("-", 1)[1]) - 1
        except (ValueError, IndexError) as exc:
            raise runner_v1.ProgramFatal("Program A2 account identity is invalid") from exc
        _validate_account_continuity(kwargs["program_root"], anchor_index)
    return body


def _validate_anchor_archive(
    program_root: Path,
    anchor_index: int,
    *,
    allow_contract_drift: bool = False,
) -> Any:
    totals = _ORIGINAL_VALIDATE_ARCHIVE(
        program_root,
        anchor_index,
        allow_contract_drift=allow_contract_drift,
    )
    if not allow_contract_drift:
        _validate_account_continuity(program_root, anchor_index)
    return totals


def _terminalize_program(program_root: Path, reason: str) -> dict[str, Any]:
    manifest_path = program_root / "program.json"
    manifest = runner_v1.validate_program_a(manifest_path, validate_live_runtime=True)
    if manifest["stage"] != "preregistered":
        return manifest
    if not isinstance(reason, str) or not reason:
        raise runner_v1.PortfolioError("program halt reason must be nonempty")
    intent = runner_v1._finalization_intent(program_root, "program-halt")
    reason_path = program_root / "derived/program-halt.json"
    if reason_path.is_file():
        reason_document = runner_v1._load_json(reason_path)
        if (
            reason_document.get("schema_version") != 1
            or reason_document.get("status") != "unscorable"
            or not isinstance(reason_document.get("reason"), str)
            or not reason_document["reason"]
        ):
            raise runner_v1.PortfolioError("program halt artifact is invalid")
        reason = reason_document["reason"]
    else:
        runner_v1._write_exact_json(
            reason_path,
            {"schema_version": 1, "status": "unscorable", "reason": reason},
            kind="program_a2_halt_reason",
        )
    calls, credits = runner_v1._program_totals(
        program_root,
        allow_contract_drift=True,
        enforce_ceiling=False,
    )
    report = (
        "# Historical theory discovery A2\n\n"
        "Status: **unscorable**. No later A2 provider request is authorized.\n\n"
        f"- Terminal reason: {reason}\n"
        f"- Authenticated attempts reserved: {calls} / {PROGRAM_A2_MAX_CALLS}\n"
        f"- Billable credits reserved or used: {credits} / {PROGRAM_A2_MAX_CREDITS}\n"
        "- Historical discovery ends here; there is no A3.\n"
    ).encode("utf-8")
    report_path = runner_v1._write_exact_bytes(
        program_root / "REPORT.md", report, kind="program_a2_report"
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
            runner_v1._artifact_record(program_root, reason_path),
            runner_v1._artifact_record(program_root, report_path),
            runner_v1._artifact_record(
                program_root, program_root / "derived/program-halt-intent.json"
            ),
            *[
                runner_v1._artifact_record(program_root, path)
                for path in evidence_paths
            ],
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
        "report_sha256": runner_v1._sha256_file(report_path),
        "artifacts": artifacts,
    }
    terminal_path = runner_v1._write_exact_json(
        program_root / "seals/final.json",
        terminal,
        kind="program_a2_terminal_seal",
    )
    updated = dict(manifest)
    updated.update(
        {
            "stage": "unscorable",
            "terminal_reason": reason,
            "final_seal_path": terminal_path.relative_to(program_root).as_posix(),
            "final_seal_sha256": runner_v1._sha256_file(terminal_path),
            "report_path": report_path.relative_to(program_root).as_posix(),
            "report_sha256": runner_v1._sha256_file(report_path),
        }
    )
    runner_v1.atomic_replace_bytes(
        manifest_path, runner_v1.canonical_json_bytes(updated)
    )
    return runner_v1.validate_program_a(manifest_path, validate_live_runtime=True)


def _validate_program(
    manifest_path: Path, *, validate_live_runtime: bool = True
) -> dict[str, Any]:
    manifest = _ORIGINAL_VALIDATE_PROGRAM(
        manifest_path, validate_live_runtime=validate_live_runtime
    )
    _verify_predecessor(runner_v1._program_repo_root(manifest_path))
    return manifest


def configure() -> Any:
    if runner_v1.PROGRAM_A_ID == PROGRAM_A2_ID:
        return runner_v1
    if runner_v1.PROGRAM_A_ID != _ORIGINAL_PROGRAM_ID:
        raise RuntimeError("the isolated runner is already configured differently")

    successor_allocations = {
        "historical_successor_a2": PROGRAM_A2_ALLOCATION,
        "prospective_discovery_validation_b2": 12_240,
        "primary_prospective_holdout_c2": 13_786,
        "temporal_replication_d2": 13_974,
    }
    for module in (design_v1, runner_v1):
        module.PORTFOLIO_ID = PROGRAM_A2_PORTFOLIO_ID
        module.PORTFOLIO_MAX_CREDITS = 47_463
        module.PROGRAM_ALLOCATIONS = successor_allocations
        module.PROGRAM_A_ID = PROGRAM_A2_ID
        module.PROGRAM_A_MAX_CALLS = PROGRAM_A2_MAX_CALLS
        module.PROGRAM_A_MAX_CREDITS = PROGRAM_A2_MAX_CREDITS
        module.PROGRAM_A_ALLOCATION = PROGRAM_A2_ALLOCATION
        module.PROVED_BALANCE = PROGRAM_A2_REQUIRED_INITIAL_REMAINING
        module.ANCHORS = A2_ANCHORS
        module.PLANNED_SLOTS = A2_SLOTS
        module.DESIGN_PATH = PROGRAM_A2_DESIGN_PATH
        module.select_anchor_events = _select_anchor_events
        module.planned_slots = lambda: A2_SLOTS
        module.score_candidates = _score_candidates

    runner_v1.PROGRAM_RELATIVE_ROOT = PROGRAM_A2_RELATIVE_ROOT
    runner_v1.PORTFOLIO_RELATIVE_ROOT = PROGRAM_A2_PORTFOLIO_ROOT
    runner_v1._epoch_limits = _epoch_limits
    runner_v1._fresh_openapi_check = _fresh_openapi_check
    runner_v1._call = _call
    runner_v1._validate_anchor_archive = _validate_anchor_archive
    runner_v1._terminalize_program = _terminalize_program
    runner_v1._completion_documents = _completion_documents
    runner_v1._completion_report = _completion_report
    runner_v1._portfolio_document = _portfolio_document
    runner_v1._preregistration_text = _preregistration_text
    runner_v1._source_paths = _source_paths
    runner_v1.validate_program_a = _validate_program
    return runner_v1


_runner = configure()


def initialize_program_a2(repo_root: Path) -> Path:
    repo_root = repo_root.resolve()
    _verify_predecessor(repo_root)
    return _ORIGINAL_INITIALIZE(repo_root)


def run_program_a2(manifest_path: Path, *, api_key: str | None = None) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    _verify_predecessor(_runner._program_repo_root(manifest_path))
    return _ORIGINAL_RUN(manifest_path, api_key=api_key)


def check_program_a2(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    _verify_predecessor(_runner._program_repo_root(manifest_path))
    return _ORIGINAL_CHECK(manifest_path)
