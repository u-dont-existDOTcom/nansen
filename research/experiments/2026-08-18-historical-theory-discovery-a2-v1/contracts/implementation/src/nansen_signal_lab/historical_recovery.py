from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .artifacts import (
    atomic_replace_bytes,
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
    write_json_once,
)
from .budget import BudgetError, BudgetGuard
from .historical_discovery import (
    BASE_BPS,
    CHAINS,
    COHORT_SIZE,
    DESIGN_VERSION as SOURCE_RULE_VERSION,
    EXPECTED_OPENAPI_SHA256,
    FEATURE_FIELDS,
    EVENT_FIELDS,
    HOLDINGS_ENDPOINT,
    HOLDINGS_FROM,
    HOLDINGS_TO,
    HOLD_DAYS,
    LOOKBACK_DAYS,
    OHLCV_ENDPOINT,
    SELECTION_DATE,
    SIGNAL_FROM,
    SIGNAL_TO,
    STRESS_BPS,
    HistoricalDiscoveryError,
    _artifact_records,
    _csv_bytes,
    _holdings_payload,
    _ohlcv_requests,
    _parse_holdings,
    _parse_ohlcv_body,
    _regular_relative_path,
    _reject_bundle_symlinks,
    _repo_root_for_experiment,
    _report_text,
    _response_for,
    _sha256_bytes,
    _sha256_file,
    _terminal_recorded_at,
    _utc_text,
    _validate_page,
    _verified_request_attempt_count,
    build_discovery,
    check_historical_discovery,
    load_historical_manifest,
    select_cohort,
)
from .prospective_runner import PilotError, _nansen_call


class HistoricalRecoveryError(HistoricalDiscoveryError):
    pass


DESIGN_VERSION = "holder-breadth-daily-source-recovery-v2"
EXPERIMENT_ID = "2026-08-18-holder-breadth-historical-recovery-v2"
DESIGN_PATH = (
    "docs/superpowers/specs/"
    "2026-08-18-historical-holder-breadth-source-recovery-v2.md"
)
EXPECTED_DESIGN_SHA256 = "baa3e80e18d8d12fc04a05a2caaf5184632f7422f19cc9f2937dbc76b4dd19f7"
MAX_CALLS = 7
MAX_CREDITS = 6
SOURCE_CALLS = 2
SOURCE_CREDITS = 5
CUMULATIVE_MAX_CALLS = SOURCE_CALLS + MAX_CALLS
CUMULATIVE_MAX_CREDITS = SOURCE_CREDITS + MAX_CREDITS
EXPECTED_OHLCV_REQUESTS = 4
SOURCE_MANIFEST_PATH = (
    "research/experiments/"
    "2026-08-18-holder-breadth-historical-discovery-v1/manifest.json"
)
SOURCE_TERMINAL_REASON = (
    "Nansen pricing validation failed: pricing evidence is incomplete or malformed"
)
SOURCE_BINDINGS = {
    "manifest": "af8f77f9d0a5a9d5043401e8a676a173326b008ff0802fb6ecdca3efac936b23",
    "seal": "171950eb403fed73f3f23762c8779febd64da0e118db81f2d5c61f21a1a183b5",
    "design": "2af79f91417a1f734c527cdabc6c13c629739a332a8263d372c81a9e7bd9efd7",
    "preregistration": "edaacd53f2695718a7f443be62d32bcacb0750f79b242668180aee22a209decf",
    "openapi": EXPECTED_OPENAPI_SHA256,
    "account_request": "7b9bc4c3cf60f632e59825c7af48bce675d81081d70cfe8c1be14e293d0264b7",
    "account_response": "fc799564ad039459d5454b8eb35f81713d897338f08a7dd69af6a6225bab4a00",
    "account_metadata": "7dc1668a9e3ad0893be65599527bb17b1238c0c65835b48e4183fbb5c6166a83",
    "screener_request": "350860a365b7890bfa2a41ae8c1fb9f3db47428a0a38fdba59f6dbbd857d6569",
    "screener_response": "136fb8db5d7b505e9b7fcf1cb88bddb4dea76a1734cd4f7dbbce3ae0a0c70556",
    "screener_metadata": "45c68aacf9a75ced06fb80a1c548bfe2070222d612e448fb33d48582409949ee",
    "normalized_screener": "09766a6d273cedc3d60bbd7e37c8589a483b8386ebdef1c4d266eb2d03d28fcf",
    "source_selection": "452e191053b5893e281b68e1623d0764eee3036dbdb9afe35b989efae650286c",
}

SOURCE_FILES = {
    "manifest": ("manifest.json", "adopted/source-manifest.json"),
    "seal": ("seals/unscorable.json", "adopted/source-seal.json"),
    "preregistration": ("PREREGISTRATION.md", "adopted/source-preregistration.md"),
    "openapi": ("raw/contracts/nansen-openapi.json", "adopted/nansen-openapi.json"),
    "account_request": (
        "raw/nansen/9af211329b2fc82e5efe9060/attempt-1-request.json",
        "adopted/account-request.json",
    ),
    "account_response": (
        "raw/nansen/9af211329b2fc82e5efe9060/attempt-1-response.json",
        "adopted/account-response.json",
    ),
    "account_metadata": (
        "raw/nansen/9af211329b2fc82e5efe9060/attempt-1-response-metadata.json",
        "adopted/account-response-metadata.json",
    ),
    "screener_request": (
        "raw/nansen/9fef5142867f0f43a7d3949c/attempt-1-request.json",
        "adopted/screener-request.json",
    ),
    "screener_response": (
        "raw/nansen/9fef5142867f0f43a7d3949c/attempt-1-response.json",
        "adopted/screener-response.json",
    ),
    "screener_metadata": (
        "raw/nansen/9fef5142867f0f43a7d3949c/attempt-1-response-metadata.json",
        "adopted/screener-response-metadata.json",
    ),
}


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalRecoveryError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise HistoricalRecoveryError(f"{label} is not an object")
    return value


def _normalize_source_screener(raw_body: dict[str, Any]) -> dict[str, Any]:
    rows, _ = _validate_page(raw_body, page=1, label="adopted historical screener")
    normalized = json.loads(canonical_json_bytes(raw_body))
    changed = 0
    for row in normalized["data"]:
        chain = row.get("chain")
        if chain == "bsc":
            row["chain"] = "bnb"
            changed += 1
        elif chain not in CHAINS:
            raise HistoricalRecoveryError("adopted screener contains an unapproved chain")
    if changed == 0 or len(rows) != len(normalized["data"]):
        raise HistoricalRecoveryError("adopted screener does not exercise the frozen alias")
    return normalized


def _recovery_selection(
    raw_body: dict[str, Any], normalized_body: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_selection = select_cohort(normalized_body)
    if _sha256_bytes(canonical_json_bytes(source_selection)) != SOURCE_BINDINGS["source_selection"]:
        raise HistoricalRecoveryError("normalized source cohort differs from the audited cohort")
    raw_by_normalized_identity: dict[tuple[str, str], dict[str, Any]] = {}
    normalized_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_row, normalized_row in zip(raw_body["data"], normalized_body["data"], strict=True):
        chain = normalized_row.get("chain")
        address = normalized_row.get("token_address")
        if not isinstance(chain, str) or not isinstance(address, str):
            continue
        identity = (chain, address.lower() if address.lower().startswith("0x") else address)
        raw_by_normalized_identity[identity] = raw_row
        normalized_by_identity[identity] = normalized_row
    members = []
    for source_member in source_selection["members"]:
        identity = (source_member["chain"], source_member["token_address"])
        if identity not in raw_by_normalized_identity:
            raise HistoricalRecoveryError("selected token cannot be traced to a raw source row")
        member = dict(source_member)
        normalized_row_sha256 = member.pop("source_row_sha256")
        if normalized_row_sha256 != _sha256_bytes(
            canonical_json_bytes(normalized_by_identity[identity])
        ):
            raise HistoricalRecoveryError("normalized row hash is inconsistent")
        member["source_raw_row_sha256"] = _sha256_bytes(
            canonical_json_bytes(raw_by_normalized_identity[identity])
        )
        member["normalized_row_sha256"] = normalized_row_sha256
        members.append(member)
    selection = {
        "schema_version": 2,
        "rule_version": SOURCE_RULE_VERSION,
        "recovery_version": DESIGN_VERSION,
        "selection_date": SELECTION_DATE.isoformat(),
        "source_endpoint": "v1beta1/token-screener/historical",
        "source_raw_file_sha256": SOURCE_BINDINGS["screener_response"],
        "source_raw_body_canonical_sha256": _sha256_bytes(canonical_json_bytes(raw_body)),
        "normalized_body_sha256": _sha256_bytes(canonical_json_bytes(normalized_body)),
        "normalization": {"response_chain_alias": {"bsc": "bnb"}},
        "members": members,
    }
    return source_selection, selection


def _source_state(repo_root: Path) -> dict[str, Any]:
    source_manifest_path = repo_root / SOURCE_MANIFEST_PATH
    if _sha256_file(source_manifest_path) != SOURCE_BINDINGS["manifest"]:
        raise HistoricalRecoveryError("source terminal manifest hash differs")
    source_manifest = load_historical_manifest(source_manifest_path)
    if (
        source_manifest["stage"] != "unscorable"
        or source_manifest["terminal_reason"] != SOURCE_TERMINAL_REASON
        or source_manifest["design_sha256"] != SOURCE_BINDINGS["design"]
        or source_manifest["preregistration_sha256"] != SOURCE_BINDINGS["preregistration"]
    ):
        raise HistoricalRecoveryError("source terminal identity differs")
    check_historical_discovery(source_manifest_path)
    source_root = source_manifest_path.parent
    copies: dict[str, tuple[Path, str]] = {}
    for name, (relative, destination) in SOURCE_FILES.items():
        path = source_root / relative
        if _sha256_file(path) != SOURCE_BINDINGS[name]:
            raise HistoricalRecoveryError(f"source {name} hash differs")
        copies[destination] = (path, SOURCE_BINDINGS[name])

    account = _read_json(_source_file_path(source_root, "account_response"), label="source account")
    metadata = _read_json(
        _source_file_path(source_root, "screener_metadata"),
        label="source screener metadata",
    )
    raw_body = _read_json(
        _source_file_path(source_root, "screener_response"),
        label="source screener response",
    )
    openapi = _read_json(
        _source_file_path(source_root, "openapi"), label="source OpenAPI"
    )
    try:
        screener_costs = openapi["paths"]["/api/v1beta1/token-screener/historical"][
            "post"
        ]["x-credit-cost"]
    except (KeyError, TypeError) as exc:
        raise HistoricalRecoveryError("source OpenAPI has no screener cost contract") from exc
    if screener_costs != {"free": 5, "pro": 5}:
        raise HistoricalRecoveryError("source OpenAPI screener cost differs")
    if account != {"plan": "free", "credits_remaining": 75}:
        raise HistoricalRecoveryError("source account baseline differs")
    if (
        metadata.get("status_code") != 200
        or metadata.get("credit_cost") is not None
        or metadata.get("credit_used") != 5
        or metadata.get("credit_remaining") != 70
        or metadata.get("credit_header_errors") != []
        or metadata.get("response_sha256") != SOURCE_BINDINGS["screener_response"]
    ):
        raise HistoricalRecoveryError("source pricing observation differs")
    normalized = _normalize_source_screener(raw_body)
    if _sha256_bytes(canonical_json_bytes(normalized)) != SOURCE_BINDINGS["normalized_screener"]:
        raise HistoricalRecoveryError("normalized screener body differs from audit")
    source_selection, selection = _recovery_selection(raw_body, normalized)
    requests = _ohlcv_requests(source_selection)
    if len(requests) != EXPECTED_OHLCV_REQUESTS:
        raise HistoricalRecoveryError("source cohort does not produce exactly four OHLCV batches")
    plan = {
        "schema_version": 1,
        "requests": [
            {
                "logical_request_id": logical_id,
                "endpoint": OHLCV_ENDPOINT,
                "payload": payload,
                "payload_sha256": _sha256_bytes(canonical_json_bytes(payload)),
            }
            for logical_id, payload in requests
        ],
    }
    pricing = {
        "schema_version": 1,
        "policy": "source-observed-cost-recovery-v1",
        "source_account_before": 75,
        "source_credit_cost_header": None,
        "source_credit_used_header": 5,
        "source_credit_remaining_header": 70,
        "pinned_openapi_expected_cost": 5,
        "accepted_source_cost": 5,
        "acceptance_scope": "adopted historical screener only",
    }
    return {
        "copies": copies,
        "raw_body": raw_body,
        "normalized_body": normalized,
        "source_selection": source_selection,
        "selection": selection,
        "plan": plan,
        "pricing": pricing,
    }


def _source_file_path(source_root: Path, name: str) -> Path:
    return source_root / SOURCE_FILES[name][0]


def _preregistration_text(experiment_id: str) -> str:
    return f"""# Preregistration — {experiment_id}

Status: **preregistered recovery; no successor provider access has run**.

This is an outcome-unseen, source-bound recovery of the terminal v1 discovery,
not the untouched original preregistration. It adopts the exact paid screener
bytes from source manifest `{SOURCE_BINDINGS['manifest']}` and never calls the
historical screener again.

- Recovery-only policies learned from the source are frozen: the missing quoted
  cost is accepted only because pinned cost, expected cost, observed use, and
  the 75-to-70 balance transition all equal five; response chain `bsc` is mapped
  to requested chain `bnb` only in the adopted screener.
- Raw provider rows and normalized rows have separate hashes. The exact
  normalized body, top-{COHORT_SIZE} cohort, and {EXPECTED_OHLCV_REQUESTS} OHLCV
  payloads are sealed before any successor provider request.
- Dates, eligibility, feature thresholds, arms, twelve-day outcome, 100/250-bps
  cost sensitivities, missingness handling, and advancement gates are unchanged
  from `{SOURCE_RULE_VERSION}`.
- New holdings and OHLCV responses must use contract-native `bnb`; no alias is
  applied to newly collected evidence.
- Successor ceiling: `{MAX_CALLS}` authenticated request attempts and
  `{MAX_CREDITS}` additional credits: account preflight 0, at most two holdings
  pages at 1 each, and exactly four OHLCV batches at 1 each. Retries are disabled.
- Cumulative study ceiling, including the sealed source: at most
  `{CUMULATIVE_MAX_CALLS}` authenticated attempts and `{CUMULATIVE_MAX_CREDITS}`
  credits. Reports must state incremental and cumulative accounting.

This discovery can only decide whether the daily holder-breadth analogue merits
prospective plumbing. It cannot establish profitability or authorize capital.
"""


def _fixed_documents(repo_root: Path) -> dict[str, bytes]:
    state = _source_state(repo_root)
    documents = {
        destination: path.read_bytes()
        for destination, (path, _) in state["copies"].items()
    }
    documents.update({
        "derived/normalized-source-screener.json": canonical_json_bytes(state["normalized_body"]),
        "derived/selection.json": canonical_json_bytes(state["selection"]),
        "derived/ohlcv-request-plan.json": canonical_json_bytes(state["plan"]),
        "derived/source-pricing-recovery.json": canonical_json_bytes(state["pricing"]),
    })
    return documents


def initialize_historical_recovery(
    root: Path,
    *,
    created_at: datetime,
    design_path: Path,
) -> Path:
    root = Path(root).absolute()
    repo_root = _repo_root_for_experiment(root)
    if root.name != EXPERIMENT_ID:
        raise HistoricalRecoveryError("historical recovery experiment identity is fixed")
    if root.exists() and any(root.iterdir()):
        raise HistoricalRecoveryError("historical recovery directory is not empty")
    expected_design = repo_root / DESIGN_PATH
    if Path(design_path).absolute() != expected_design:
        raise HistoricalRecoveryError("historical recovery design path is not fixed")
    _regular_relative_path(repo_root, expected_design, label="historical recovery design")
    if _sha256_file(expected_design) != EXPECTED_DESIGN_SHA256:
        raise HistoricalRecoveryError("historical recovery design hash is not frozen")
    fixed_documents = _fixed_documents(repo_root)
    root.mkdir(parents=True, exist_ok=True)
    preregistration = _preregistration_text(root.name).encode("utf-8")
    prereg_path = write_bytes_once_or_adopt_exact(
        root / "PREREGISTRATION.md",
        preregistration,
        metadata={"kind": "historical_recovery_preregistration"},
    )
    frozen = {}
    for relative, content in sorted(fixed_documents.items()):
        path = write_bytes_once_or_adopt_exact(
            root / relative,
            content,
            metadata={"kind": "historical_recovery_frozen_input", "path": relative},
        )
        frozen[relative] = _sha256_file(path)
    manifest = {
        "schema_version": 1,
        "experiment_id": root.name,
        "stage": "preregistered",
        "created_at": _utc_text(created_at),
        "design_version": DESIGN_VERSION,
        "design_path": DESIGN_PATH,
        "design_sha256": EXPECTED_DESIGN_SHA256,
        "preregistration_path": "PREREGISTRATION.md",
        "preregistration_sha256": _sha256_file(prereg_path),
        "openapi_sha256": EXPECTED_OPENAPI_SHA256,
        "budget": {
            "incremental_max_calls": MAX_CALLS,
            "incremental_max_credits": MAX_CREDITS,
            "source_calls": SOURCE_CALLS,
            "source_credits": SOURCE_CREDITS,
            "cumulative_max_calls": CUMULATIVE_MAX_CALLS,
            "cumulative_max_credits": CUMULATIVE_MAX_CREDITS,
        },
        "source": {
            "manifest_path": SOURCE_MANIFEST_PATH,
            "bindings": SOURCE_BINDINGS,
            "terminal_reason": SOURCE_TERMINAL_REASON,
            "response_chain_alias": {"bsc": "bnb"},
        },
        "study": {
            "selection_date": SELECTION_DATE.isoformat(),
            "holdings_from": HOLDINGS_FROM.isoformat(),
            "signal_from": SIGNAL_FROM.isoformat(),
            "signal_to": SIGNAL_TO.isoformat(),
            "holdings_to": HOLDINGS_TO.isoformat(),
            "chains": list(CHAINS),
            "cohort_size": COHORT_SIZE,
            "lookback_days": LOOKBACK_DAYS,
            "holding_period_days": HOLD_DAYS,
            "base_per_side_bps": BASE_BPS,
            "stress_per_side_bps": STRESS_BPS,
        },
        "frozen_inputs": frozen,
        "artifacts": [],
        "terminal_reason": None,
    }
    write_json_once(root / "manifest.json", manifest)
    return root / "manifest.json"


def load_historical_recovery_manifest(path: Path) -> dict[str, Any]:
    path = Path(path).absolute()
    if path.name != "manifest.json":
        raise HistoricalRecoveryError("historical recovery manifest filename is not fixed")
    root = path.parent
    repo_root = _repo_root_for_experiment(root)
    _regular_relative_path(root, path, label="historical recovery manifest")
    raw = path.read_bytes()
    manifest = _read_json(path, label="historical recovery manifest")
    expected_keys = {
        "schema_version", "experiment_id", "stage", "created_at", "design_version",
        "design_path", "design_sha256", "preregistration_path", "preregistration_sha256",
        "openapi_sha256", "budget", "source", "study", "frozen_inputs", "artifacts",
        "terminal_reason",
    }
    if set(manifest) != expected_keys or canonical_json_bytes(manifest) != raw:
        raise HistoricalRecoveryError("historical recovery manifest shape or encoding is invalid")
    if (
        manifest["schema_version"] != 1
        or manifest["experiment_id"] != root.name
        or manifest["experiment_id"] != EXPERIMENT_ID
    ):
        raise HistoricalRecoveryError("historical recovery identity differs")
    if manifest["stage"] not in {"preregistered", "completed", "unscorable"}:
        raise HistoricalRecoveryError("historical recovery stage is invalid")
    fixed = {
        "design_version": DESIGN_VERSION,
        "design_path": DESIGN_PATH,
        "design_sha256": EXPECTED_DESIGN_SHA256,
        "preregistration_path": "PREREGISTRATION.md",
        "openapi_sha256": EXPECTED_OPENAPI_SHA256,
        "budget": {
            "incremental_max_calls": MAX_CALLS,
            "incremental_max_credits": MAX_CREDITS,
            "source_calls": SOURCE_CALLS,
            "source_credits": SOURCE_CREDITS,
            "cumulative_max_calls": CUMULATIVE_MAX_CALLS,
            "cumulative_max_credits": CUMULATIVE_MAX_CREDITS,
        },
        "source": {
            "manifest_path": SOURCE_MANIFEST_PATH,
            "bindings": SOURCE_BINDINGS,
            "terminal_reason": SOURCE_TERMINAL_REASON,
            "response_chain_alias": {"bsc": "bnb"},
        },
        "study": {
            "selection_date": SELECTION_DATE.isoformat(),
            "holdings_from": HOLDINGS_FROM.isoformat(),
            "signal_from": SIGNAL_FROM.isoformat(),
            "signal_to": SIGNAL_TO.isoformat(),
            "holdings_to": HOLDINGS_TO.isoformat(),
            "chains": list(CHAINS),
            "cohort_size": COHORT_SIZE,
            "lookback_days": LOOKBACK_DAYS,
            "holding_period_days": HOLD_DAYS,
            "base_per_side_bps": BASE_BPS,
            "stress_per_side_bps": STRESS_BPS,
        },
    }
    for key, value in fixed.items():
        if manifest.get(key) != value:
            raise HistoricalRecoveryError(f"historical recovery changed {key}")
    design = repo_root / DESIGN_PATH
    prereg = root / "PREREGISTRATION.md"
    _regular_relative_path(repo_root, design, label="historical recovery design")
    _regular_relative_path(root, prereg, label="historical recovery preregistration")
    expected_prereg = _preregistration_text(root.name).encode("utf-8")
    if (
        _sha256_file(design) != EXPECTED_DESIGN_SHA256
        or prereg.read_bytes() != expected_prereg
        or manifest["preregistration_sha256"] != _sha256_bytes(expected_prereg)
    ):
        raise HistoricalRecoveryError("historical recovery design/preregistration differs")
    expected_documents = _fixed_documents(repo_root)
    expected_frozen = {
        relative: _sha256_bytes(content)
        for relative, content in sorted(expected_documents.items())
    }
    if manifest["frozen_inputs"] != expected_frozen:
        raise HistoricalRecoveryError("historical recovery frozen bindings differ")
    for relative, content in expected_documents.items():
        candidate = root / relative
        _regular_relative_path(root, candidate, label="historical recovery frozen input")
        if candidate.read_bytes() != content:
            raise HistoricalRecoveryError("historical recovery frozen input bytes differ")
    if manifest["stage"] == "preregistered" and (
        manifest["artifacts"] != [] or manifest["terminal_reason"] is not None
    ):
        raise HistoricalRecoveryError("preregistered recovery has terminal state")
    if manifest["stage"] == "completed" and manifest["terminal_reason"] is not None:
        raise HistoricalRecoveryError("completed recovery has a terminal reason")
    if manifest["stage"] == "unscorable" and not isinstance(manifest["terminal_reason"], str):
        raise HistoricalRecoveryError("unscorable recovery has no reason")
    return manifest


def _selection_for_collection(root: Path) -> dict[str, Any]:
    selection = _read_json(root / "derived/selection.json", label="frozen recovery selection")
    if selection.get("schema_version") != 2 or len(selection.get("members", [])) != COHORT_SIZE:
        raise HistoricalRecoveryError("frozen recovery selection is invalid")
    return selection


def _request_plan(root: Path) -> tuple[tuple[str, dict[str, Any]], ...]:
    document = _read_json(root / "derived/ohlcv-request-plan.json", label="OHLCV plan")
    rows = document.get("requests")
    if document.get("schema_version") != 1 or not isinstance(rows, list):
        raise HistoricalRecoveryError("OHLCV plan shape is invalid")
    requests = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "logical_request_id", "endpoint", "payload", "payload_sha256"
        }:
            raise HistoricalRecoveryError("OHLCV plan row is invalid")
        if (
            row["endpoint"] != OHLCV_ENDPOINT
            or _sha256_bytes(canonical_json_bytes(row["payload"])) != row["payload_sha256"]
        ):
            raise HistoricalRecoveryError("OHLCV plan binding differs")
        requests.append((row["logical_request_id"], row["payload"]))
    if len(requests) != EXPECTED_OHLCV_REQUESTS:
        raise HistoricalRecoveryError("OHLCV plan does not contain four requests")
    return tuple(requests)


def _recovery_report(
    summary: dict[str, Any], *, incremental_attempts: int, incremental_credits: int
) -> bytes:
    original = _report_text(summary)
    accounting = f"""

## Recovery provenance and accounting

This is the source-bound recovery protocol, not the untouched v1
preregistration. It adopted the exact outcome-unseen screener response, accepted
the source's five-credit charge under the frozen observed-cost derivation, and
mapped `bsc` to `bnb` only in that adopted response. New evidence received no
chain aliasing.

The successor used {incremental_attempts} authenticated attempts and
{incremental_credits} additional credits. Including the sealed source, the
study used {SOURCE_CALLS + incremental_attempts} authenticated attempts and
{SOURCE_CREDITS + incremental_credits} credits, within hard maxima of
{CUMULATIVE_MAX_CALLS} attempts and {CUMULATIVE_MAX_CREDITS} credits.
"""
    return (original.rstrip() + accounting).encode("utf-8")


def _render_outputs(
    normalized_screener: dict[str, Any],
    holdings_pages: list[dict[str, Any]],
    ohlcv_bodies: dict[str, dict[str, Any]],
    *,
    incremental_attempts: int,
    incremental_credits: int,
) -> dict[str, bytes]:
    _, features, events, summary = build_discovery(
        normalized_screener, holdings_pages, ohlcv_bodies
    )
    return {
        "derived/daily-features.csv": _csv_bytes(features, FEATURE_FIELDS),
        "derived/events.csv": _csv_bytes(events, EVENT_FIELDS),
        "derived/summary.json": canonical_json_bytes(summary),
        "REPORT.md": _recovery_report(
            summary,
            incremental_attempts=incremental_attempts,
            incremental_credits=incremental_credits,
        ),
    }


def _finalize(
    manifest_path: Path,
    manifest: dict[str, Any],
    guard: BudgetGuard,
    *,
    stage: str,
    reason: str | None,
    clock: Callable[[], datetime],
    output_paths: Iterable[Path],
) -> dict[str, Any]:
    root = manifest_path.parent
    recorded_at = _terminal_recorded_at(root, stage, clock)
    attempts = _verified_request_attempt_count(root, guard)
    totals = guard.replay()
    if attempts > MAX_CALLS or (stage == "completed" and totals.credits > MAX_CREDITS):
        raise HistoricalRecoveryError("incremental recovery ceiling exceeded")
    snapshot = guard.snapshot(stage, recorded_at=recorded_at)
    paths = [
        path for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json" and "seals" not in path.parts
    ]
    paths.extend(output_paths)
    paths.append(snapshot)
    records = _artifact_records(root, paths)
    seal = {
        "schema_version": 1,
        "stage": stage,
        "recorded_at": recorded_at,
        "terminal_reason": reason,
        "incremental_authenticated_request_attempts": attempts,
        "incremental_credits": totals.credits,
        "cumulative_authenticated_request_attempts": SOURCE_CALLS + attempts,
        "cumulative_credits": SOURCE_CREDITS + totals.credits,
        "artifacts": records,
        "budget_snapshot_path": snapshot.relative_to(root).as_posix(),
        "budget_snapshot_sha256": _sha256_file(snapshot),
    }
    seal_path = write_bytes_once_or_adopt_exact(
        root / f"seals/{stage}.json",
        canonical_json_bytes(seal),
        metadata={"kind": "historical_recovery_seal", "stage": stage},
    )
    updated = dict(manifest)
    updated["stage"] = stage
    updated["terminal_reason"] = reason
    updated["artifacts"] = records + [{
        "path": seal_path.relative_to(root).as_posix(),
        "sha256": _sha256_file(seal_path),
    }]
    atomic_replace_bytes(manifest_path, canonical_json_bytes(updated))
    return updated


def _finalize_unscorable(
    manifest_path: Path,
    manifest: dict[str, Any],
    guard: BudgetGuard,
    reason: str,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    report_path = manifest_path.parent / "REPORT.md"
    prefix = "# Historical holder-breadth source recovery\n\nStatus: **unscorable** — "
    reason_end = ". No automatic rerun is permitted.\n"
    if report_path.exists():
        existing = report_path.read_text()
        if not existing.startswith(prefix) or reason_end not in existing:
            raise HistoricalRecoveryError("unscorable recovery report is invalid")
        reason = existing[len(prefix):].split(reason_end, 1)[0]
    attempts = _verified_request_attempt_count(manifest_path.parent, guard)
    totals = guard.replay()
    accounting = (
        "\nAccounting: "
        f"{attempts} incremental authenticated attempts and {totals.credits} actual "
        f"incremental credits; {SOURCE_CALLS + attempts} cumulative attempts and "
        f"{SOURCE_CREDITS + totals.credits} cumulative actual credits. The authorized "
        f"incremental ceiling was {MAX_CALLS} attempts/{MAX_CREDITS} credits; a "
        "provider-reported overcharge is preserved as a terminal breach, not accepted "
        "as authorized spend.\n"
    )
    report_path = write_bytes_once_or_adopt_exact(
        report_path,
        f"{prefix}{reason}{reason_end}{accounting}".encode("utf-8"),
        metadata={"kind": "historical_recovery_unscorable_report"},
    )
    return _finalize(
        manifest_path, manifest, guard, stage="unscorable", reason=reason,
        clock=clock, output_paths=(report_path,),
    )


def start_historical_recovery(
    manifest_path: Path,
    *,
    nansen: Any,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path).absolute()
    manifest = load_historical_recovery_manifest(manifest_path)
    if manifest["stage"] in {"completed", "unscorable"}:
        check_historical_recovery(manifest_path)
        return manifest
    root = manifest_path.parent
    _reject_bundle_symlinks(root)
    guard = BudgetGuard(root, MAX_CALLS, MAX_CREDITS)
    try:
        guard.reconcile_inflight()
        openapi = nansen.fetch_openapi()
        if not isinstance(openapi, bytes):
            raise HistoricalRecoveryError("public OpenAPI response is not exact bytes")
        contract_path = write_bytes_once_or_adopt_exact(
            root / "raw/contracts/nansen-openapi.json",
            openapi,
            metadata={"kind": "nansen_openapi"},
        )
        metadata_path = write_bytes_once_or_adopt_exact(
            root / "raw/contracts/nansen-openapi-metadata.json",
            canonical_json_bytes({
                "schema_version": 1,
                "source_url": "https://api.nansen.ai/openapi.json",
                "source_sha256": _sha256_bytes(openapi),
            }),
            metadata={"kind": "nansen_openapi_metadata"},
        )
        if _sha256_bytes(openapi) != EXPECTED_OPENAPI_SHA256:
            return _finalize_unscorable(
                manifest_path, manifest, guard,
                "live Nansen OpenAPI checksum differs from preregistration", clock,
            )
        account, _ = _nansen_call(
            root=root, guard=guard, nansen=nansen, logical_request_id="account",
            method="GET", endpoint="account", payload=None, expected_credits=0,
            clock=clock, sleep=sleep, account_baseline_version="account-baseline-v2",
            openapi_sha256=EXPECTED_OPENAPI_SHA256,
            account_minimum_remaining=MAX_CREDITS,
            allow_retry=False,
        )
        body = account.body
        if (
            not isinstance(body, dict)
            or body.get("plan") not in {"free", "pro"}
            or isinstance(body.get("credits_remaining"), bool)
            or not isinstance(body.get("credits_remaining"), int)
            or body["credits_remaining"] < MAX_CREDITS
        ):
            return _finalize_unscorable(
                manifest_path, manifest, guard,
                "account preflight does not prove the six-credit ceiling is available", clock,
            )
        selection = _selection_for_collection(root)
        holdings_pages = []
        last = False
        for page in (1, 2):
            response, _ = _nansen_call(
                root=root, guard=guard, nansen=nansen,
                logical_request_id=f"historical-holdings-page-{page}", method="POST",
                endpoint=HOLDINGS_ENDPOINT, payload=_holdings_payload(selection, page),
                expected_credits=1, clock=clock, sleep=sleep, allow_retry=False,
            )
            if not isinstance(response.body, dict):
                raise HistoricalRecoveryError("historical holdings response is not an object")
            holdings_pages.append(response.body)
            rows, last = _validate_page(
                response.body, page=page, label="historical recovery holdings"
            )
            if any(row.get("chain") == "bsc" for row in rows):
                raise HistoricalRecoveryError("new holdings evidence used forbidden bsc alias")
            if last:
                break
        if not last:
            raise HistoricalRecoveryError("historical holdings exceeds the two-page cap")
        _parse_holdings(holdings_pages, selection)
        ohlcv_bodies = {}
        for logical_id, payload in _request_plan(root):
            response, _ = _nansen_call(
                root=root, guard=guard, nansen=nansen,
                logical_request_id=logical_id, method="POST", endpoint=OHLCV_ENDPOINT,
                payload=payload, expected_credits=1, clock=clock, sleep=sleep,
                allow_retry=False,
            )
            if not isinstance(response.body, dict):
                raise HistoricalRecoveryError("OHLCV response is not an object")
            if response.body.get("chain") == "bsc":
                raise HistoricalRecoveryError("new OHLCV evidence used forbidden bsc alias")
            _parse_ohlcv_body(payload, response.body)
            ohlcv_bodies[logical_id] = response.body
        attempts = _verified_request_attempt_count(root, guard)
        totals = guard.replay()
        normalized = _read_json(
            root / "derived/normalized-source-screener.json",
            label="normalized source screener",
        )
        rendered = _render_outputs(
            normalized, holdings_pages, ohlcv_bodies,
            incremental_attempts=attempts, incremental_credits=totals.credits,
        )
        output_paths = []
        for relative, content in rendered.items():
            output_paths.append(write_bytes_once_or_adopt_exact(
                root / relative,
                content,
                metadata={"kind": "historical_recovery_output", "path": relative},
            ))
        return _finalize(
            manifest_path, manifest, guard, stage="completed", reason=None,
            clock=clock, output_paths=(contract_path, metadata_path, *output_paths),
        )
    except (HistoricalRecoveryError, HistoricalDiscoveryError, PilotError, BudgetError) as exc:
        return _finalize_unscorable(manifest_path, manifest, guard, str(exc), clock)


def check_historical_recovery(manifest_path: Path) -> tuple[Path, ...]:
    manifest_path = Path(manifest_path).absolute()
    manifest = load_historical_recovery_manifest(manifest_path)
    if manifest["stage"] not in {"completed", "unscorable"}:
        raise HistoricalRecoveryError("historical recovery is not terminal")
    root = manifest_path.parent
    verified = []
    seen = set()
    for record in manifest["artifacts"]:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise HistoricalRecoveryError("terminal artifact record is invalid")
        relative = record["path"]
        if (
            not isinstance(relative, str) or not relative or relative in seen
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(relative).parts)
        ):
            raise HistoricalRecoveryError("terminal artifact path is invalid")
        seen.add(relative)
        path = root / relative
        _regular_relative_path(root, path, label="historical recovery terminal artifact")
        if _sha256_file(path) != record["sha256"]:
            raise HistoricalRecoveryError("terminal artifact hash differs")
        verified.append(path)
    seal_relative = f"seals/{manifest['stage']}.json"
    if seal_relative not in seen:
        raise HistoricalRecoveryError("terminal recovery manifest does not bind its seal")
    seal = _read_json(root / seal_relative, label="historical recovery seal")
    seal_record = next(record for record in manifest["artifacts"] if record["path"] == seal_relative)
    unsealed = [record for record in manifest["artifacts"] if record is not seal_record]
    guard = BudgetGuard(root, MAX_CALLS, MAX_CREDITS)
    attempts = _verified_request_attempt_count(root, guard)
    totals = guard.replay()
    if (
        seal.get("schema_version") != 1
        or seal.get("stage") != manifest["stage"]
        or seal.get("terminal_reason") != manifest["terminal_reason"]
        or seal.get("incremental_authenticated_request_attempts") != attempts
        or seal.get("incremental_credits") != totals.credits
        or seal.get("cumulative_authenticated_request_attempts") != SOURCE_CALLS + attempts
        or seal.get("cumulative_credits") != SOURCE_CREDITS + totals.credits
        or attempts > MAX_CALLS
        or (manifest["stage"] == "completed" and totals.credits > MAX_CREDITS)
        or seal.get("artifacts") != unsealed
        or seal.get("budget_snapshot_path") != f"budget/snapshots/{manifest['stage']}.json"
    ):
        raise HistoricalRecoveryError("terminal recovery seal differs")
    snapshot_path = root / seal["budget_snapshot_path"]
    if seal.get("budget_snapshot_sha256") != _sha256_file(snapshot_path):
        raise HistoricalRecoveryError("terminal recovery budget snapshot hash differs")
    snapshot = _read_json(snapshot_path, label="historical recovery budget snapshot")
    if (
        snapshot.get("stage") != manifest["stage"]
        or snapshot.get("totals") != {"calls": totals.calls, "credits": totals.credits}
        or snapshot.get("provider_remaining") != totals.provider_remaining
        or snapshot.get("journal_head_sha256") != totals.journal_head_sha256
        or snapshot.get("transition_sha256s") != list(totals.transition_sha256s)
        or snapshot.get("halted_reason") != totals.halted_reason
    ):
        raise HistoricalRecoveryError("terminal recovery budget snapshot differs")
    if manifest["stage"] == "completed":
        selection = _selection_for_collection(root)
        pages = []
        entries = guard.replay().entries
        for page in (1, 2):
            logical = f"historical-holdings-page-{page}"
            if any(entry.logical_request_id == logical for entry in entries):
                pages.append(_response_for(root, guard, logical).body)
        ohlcv = {
            logical_id: _response_for(root, guard, logical_id).body
            for logical_id, _ in _request_plan(root)
        }
        normalized = _read_json(
            root / "derived/normalized-source-screener.json",
            label="normalized source screener",
        )
        expected = _render_outputs(
            normalized, pages, ohlcv,
            incremental_attempts=attempts, incremental_credits=totals.credits,
        )
        for relative, content in expected.items():
            path = root / relative
            if not path.is_file() or path.read_bytes() != content:
                raise HistoricalRecoveryError(f"derived recovery output differs: {relative}")
        if len(selection["members"]) != COHORT_SIZE:
            raise HistoricalRecoveryError("completed recovery cohort size differs")
    return tuple(sorted(set(verified), key=lambda path: path.as_posix()))
