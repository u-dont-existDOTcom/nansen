from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .artifacts import (
    atomic_replace_bytes,
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
    write_json_once,
)
from .budget import BudgetError, BudgetGuard
from .client import NansenEvidenceResponse
from .evaluation import entry_objective_pct
from .prospective_runner import PilotError, _load_nansen_response, _nansen_call


class HistoricalDiscoveryError(RuntimeError):
    pass


DESIGN_VERSION = "holder-breadth-daily-v1"
EXPECTED_OPENAPI_SHA256 = "d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548"
DESIGN_PATH = "docs/superpowers/specs/2026-08-18-historical-holder-breadth-discovery-v1.md"
EXPECTED_DESIGN_SHA256 = "2af79f91417a1f734c527cdabc6c13c629739a332a8263d372c81a9e7bd9efd7"
MAX_CALLS = 9
MAX_CREDITS = 12
COHORT_SIZE = 20
SCREENER_ENDPOINT = "v1beta1/token-screener/historical"
HOLDINGS_ENDPOINT = "smart-money/historical-holdings"
OHLCV_ENDPOINT = "tgm/token-ohlcv"
SELECTION_DATE = date(2026, 6, 1)
HOLDINGS_FROM = date(2026, 5, 28)
SIGNAL_FROM = date(2026, 6, 2)
SIGNAL_TO = date(2026, 7, 27)
HOLDINGS_TO = date(2026, 8, 9)
CHAINS = ("ethereum", "solana", "base", "bnb")
LOOKBACK_DAYS = 4
HOLD_DAYS = 12
BASE_BPS = 100
STRESS_BPS = 250

FEATURE_FIELDS = (
    "signal_date",
    "block_id",
    "chain",
    "token_address",
    "token_symbol",
    "balance_change_4d_pct",
    "accumulation_persistence_4d",
    "accumulation_retention_4d",
    "holder_count_change_4d",
    "base_predicate",
    "arm",
    "entry_date",
    "exit_date",
    "entry_price_usd",
    "exit_price_usd",
    "outcome_available",
)
EVENT_FIELDS = (
    "arm",
    "block_id",
    "chain",
    "token_address",
    "token_symbol",
    "signal_date",
    "entry_date",
    "exit_date",
    "selection_liquidity_usd",
    "virtual_notional_usd",
    "outcome_status",
    "gross_return_pct",
    "base_objective_pct",
    "stress_objective_pct",
)
PARTIAL_RESPONSE_METADATA_FIELDS = {
    "schema_version",
    "attempt",
    "status_code",
    "request_started_at",
    "response_retrieved_at",
    "artifact_written_at",
    "response_headers",
    "request_id",
    "credit_cost",
    "credit_used",
    "credit_remaining",
    "credit_header_errors",
    "body_parse_status",
    "response_file",
    "response_sha256",
}


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalDiscoveryError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _finite(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _identity(chain: Any, address: Any) -> tuple[str, str]:
    if not isinstance(chain, str) or not chain or not isinstance(address, str) or not address:
        raise HistoricalDiscoveryError("token identity is incomplete")
    normalized = address.lower() if address.lower().startswith("0x") else address
    return chain.lower(), normalized


def _screener_payload() -> dict[str, Any]:
    return {
        "to_date": SELECTION_DATE.isoformat(),
        "timeframe_days": 1,
        "chains": list(CHAINS),
        "exclude_sectors": ["Stablecoin"],
        "trader_type": "sm",
        "filters": {
            "market_cap_usd": {"min": 1_000_000},
            "liquidity_usd": {"min": 250_000},
            "token_age_days": {"min": 3},
        },
        "pagination": {"page": 1, "per_page": 1000},
        "order_by": [{"field": "netflow", "direction": "DESC"}],
        # A current blacklist could leak failures learned after the historical
        # selection date, so it is deliberately not used for cohort selection.
        "apply_blacklist_filter": False,
    }


def _holdings_payload(selection: dict[str, Any], page: int) -> dict[str, Any]:
    members = selection["members"]
    return {
        "date_range": {
            "from": HOLDINGS_FROM.isoformat(),
            "to": HOLDINGS_TO.isoformat(),
        },
        "chains": sorted({member["chain"] for member in members}),
        "filters": {
            "include_stablecoins": False,
            "include_native_tokens": False,
            "token_address": [member["token_address"] for member in members],
        },
        "pagination": {"page": page, "per_page": 1000},
    }


def _ohlcv_requests(selection: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for member in selection["members"]:
        grouped[member["chain"]].append(member["token_address"])
    requests = []
    for chain in sorted(grouped):
        addresses = sorted(grouped[chain])
        for offset in range(0, len(addresses), 10):
            chunk = offset // 10 + 1
            requests.append((
                f"ohlcv-{chain}-chunk-{chunk}",
                {
                    "chain": chain,
                    "token_addresses": addresses[offset:offset + 10],
                    "date": {
                        "from": f"{(SIGNAL_FROM + timedelta(days=1)).isoformat()}T00:00:00Z",
                        "to": f"{HOLDINGS_TO.isoformat()}T23:59:59Z",
                    },
                    "timeframe": "1d",
                },
            ))
    if len(requests) > 5:
        raise HistoricalDiscoveryError("OHLCV batching exceeds the fixed five-call cap")
    return tuple(requests)


def _preregistration_text(experiment_id: str) -> str:
    return f"""# Preregistration — {experiment_id}

Status: **preregistered; no paid collection has run**.

This immutable discovery experiment tests a daily-granularity analogue of the
previously advanced holder-breadth comparison. It does not modify or rerun any
sealed GPT pilot.

- Selection: historical Smart-Money screener page one at `{SELECTION_DATE}`; top
  `{COHORT_SIZE}` eligible tokens by netflow with deterministic chain/address
  tie-breaks, market cap at least $1m, liquidity at least $250k, age at least
  three days, and stablecoins excluded.
- Evidence: historical Smart-Money daily holdings from `{HOLDINGS_FROM}` through
  `{HOLDINGS_TO}`, at most two complete pages, plus independent daily OHLCV in
  at most five chain-batched calls. The provider broadly describes the
  historical holdings surface as no-lookahead; wallet-label effective-date
  semantics remain a discovery limitation.
- Signal dates: `{SIGNAL_FROM}` through `{SIGNAL_TO}` (eight weeks).
- Positive arm: positive four-day balance change, at least 50% positive daily
  deltas, at least 80% accumulation retention, and positive four-day holder
  breadth. Reference arm has the same accumulation predicates and non-positive
  holder breadth.
- Availability: signal at day `t`, entry at independent OHLCV close on `t+1`,
  and exit twelve days later. A non-overlapping eligible signal remains in the
  denominator when OHLCV is missing; advancement requires 100% contiguous
  outcome coverage.
- Costs: 100 bps/side base and 250 bps/side stress. These are sensitivities, not
  timestamped executable quotes.
- Descriptive advancement requires at least 10 non-overlapping events and five
  tokens in each arm; positive aggregate token-equal and event-median spreads;
  positive stress token-equal mean in the positive arm; and positive block
  spread in at least three of four fixed 14-day blocks.
- Hard provider ceiling: `{MAX_CALLS}` authenticated request attempts and
  `{MAX_CREDITS}` credits (account 0, historical screener 5, holdings pages and
  OHLCV batches 1 each). Automatic retries are disabled.

No result from this historical beta discovery can satisfy the repository's
prospective profitability gates. Any surviving rule still requires an untouched
prospective, execution-aware holdout.
"""


def _repo_root_for_experiment(root: Path) -> Path:
    root = Path(root).absolute()
    if (
        root.name in {"", ".", ".."}
        or root.parent.name != "experiments"
        or root.parent.parent.name != "research"
        or root.is_symlink()
        or root.parent.is_symlink()
        or root.parent.parent.is_symlink()
        or root.parent.parent.parent.is_symlink()
    ):
        raise HistoricalDiscoveryError(
            "historical discovery must be research/experiments/<experiment-id>"
        )
    return root.parent.parent.parent


def _regular_relative_path(root: Path, path: Path, *, label: str) -> Path:
    root = Path(root).absolute()
    path = Path(path).absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise HistoricalDiscoveryError(f"{label} escapes its fixed root") from exc
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or root.is_symlink()
    ):
        raise HistoricalDiscoveryError(f"{label} path is invalid")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise HistoricalDiscoveryError(f"{label} path contains a symlink")
    if not path.is_file():
        raise HistoricalDiscoveryError(f"{label} is not a regular file")
    return relative


def _reject_bundle_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise HistoricalDiscoveryError("historical discovery root is a symlink")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise HistoricalDiscoveryError("historical discovery bundle contains a symlink")


def initialize_historical_discovery(
    root: Path,
    *,
    created_at: datetime,
    design_path: Path,
) -> Path:
    root = Path(root).absolute()
    repo_root = _repo_root_for_experiment(root)
    if root.exists() and any(root.iterdir()):
        raise HistoricalDiscoveryError("historical discovery directory is not empty")
    expected_design = repo_root / DESIGN_PATH
    supplied_design = Path(design_path).absolute()
    if supplied_design != expected_design:
        raise HistoricalDiscoveryError("historical discovery design path is not the fixed repository design")
    _regular_relative_path(repo_root, expected_design, label="historical discovery design")
    if _sha256_file(expected_design) != EXPECTED_DESIGN_SHA256:
        raise HistoricalDiscoveryError("historical discovery design hash is not the fixed contract")
    root.mkdir(parents=True, exist_ok=True)
    experiment_id = root.name
    preregistration = _preregistration_text(experiment_id).encode("utf-8")
    prereg_path = write_bytes_once_or_adopt_exact(
        root / "PREREGISTRATION.md",
        preregistration,
        metadata={"kind": "historical_discovery_preregistration"},
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "stage": "preregistered",
        "created_at": _utc_text(created_at),
        "design_version": DESIGN_VERSION,
        "design_path": DESIGN_PATH,
        "design_sha256": EXPECTED_DESIGN_SHA256,
        "preregistration_path": "PREREGISTRATION.md",
        "preregistration_sha256": _sha256_file(prereg_path),
        "openapi_sha256": EXPECTED_OPENAPI_SHA256,
        "budget": {"max_calls": MAX_CALLS, "max_credits": MAX_CREDITS},
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
        "artifacts": [],
        "terminal_reason": None,
    }
    write_json_once(root / "manifest.json", manifest)
    return root / "manifest.json"


def load_historical_manifest(path: Path) -> dict[str, Any]:
    path = Path(path).absolute()
    if path.name != "manifest.json":
        raise HistoricalDiscoveryError("historical discovery manifest filename is not fixed")
    root = path.parent
    repo_root = _repo_root_for_experiment(root)
    _regular_relative_path(root, path, label="historical discovery manifest")
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalDiscoveryError("historical discovery manifest is unreadable") from exc
    expected = {
        "schema_version", "experiment_id", "stage", "created_at", "design_version",
        "design_path", "design_sha256", "preregistration_path", "preregistration_sha256",
        "openapi_sha256", "budget", "study", "artifacts", "terminal_reason",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise HistoricalDiscoveryError("historical discovery manifest shape is invalid")
    if canonical_json_bytes(manifest) != raw:
        raise HistoricalDiscoveryError("historical discovery manifest is not canonical JSON")
    fixed = {
        "schema_version": 1,
        "design_version": DESIGN_VERSION,
        "design_path": DESIGN_PATH,
        "design_sha256": EXPECTED_DESIGN_SHA256,
        "preregistration_path": "PREREGISTRATION.md",
        "openapi_sha256": EXPECTED_OPENAPI_SHA256,
        "budget": {"max_calls": MAX_CALLS, "max_credits": MAX_CREDITS},
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
            raise HistoricalDiscoveryError(f"historical discovery manifest changed {key}")
    if manifest["stage"] not in {"preregistered", "completed", "unscorable"}:
        raise HistoricalDiscoveryError("historical discovery stage is invalid")
    if manifest["experiment_id"] != root.name:
        raise HistoricalDiscoveryError("historical discovery experiment identity is invalid")
    design = repo_root / DESIGN_PATH
    prereg = root / "PREREGISTRATION.md"
    expected_preregistration = _preregistration_text(root.name).encode("utf-8")
    _regular_relative_path(repo_root, design, label="historical discovery design")
    _regular_relative_path(root, prereg, label="historical discovery preregistration")
    if _sha256_file(design) != EXPECTED_DESIGN_SHA256:
        raise HistoricalDiscoveryError("historical discovery design binding is invalid")
    if (
        prereg.read_bytes() != expected_preregistration
        or manifest["preregistration_sha256"] != _sha256_bytes(expected_preregistration)
    ):
        raise HistoricalDiscoveryError("historical discovery preregistration binding is invalid")
    if manifest["stage"] == "preregistered" and (
        manifest["artifacts"] != [] or manifest["terminal_reason"] is not None
    ):
        raise HistoricalDiscoveryError("preregistered historical discovery has terminal state")
    if manifest["stage"] == "completed" and manifest["terminal_reason"] is not None:
        raise HistoricalDiscoveryError("completed historical discovery has a terminal reason")
    if manifest["stage"] == "unscorable" and not isinstance(manifest["terminal_reason"], str):
        raise HistoricalDiscoveryError("unscorable historical discovery lacks a reason")
    return manifest


def _validate_page(body: Any, *, page: int, label: str) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise HistoricalDiscoveryError(f"{label} response data must be a list")
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, dict)
        or pagination.get("page") != page
        or pagination.get("per_page") != 1000
        or not isinstance(pagination.get("is_last_page"), bool)
    ):
        raise HistoricalDiscoveryError(f"{label} pagination is invalid")
    if any(not isinstance(row, dict) for row in body["data"]):
        raise HistoricalDiscoveryError(f"{label} contains a non-object row")
    return list(body["data"]), pagination["is_last_page"]


def select_cohort(screener_body: dict[str, Any]) -> dict[str, Any]:
    rows, _ = _validate_page(screener_body, page=1, label="historical screener")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        try:
            identity = _identity(row.get("chain"), row.get("token_address"))
        except HistoricalDiscoveryError:
            continue
        if identity[0] not in CHAINS:
            raise HistoricalDiscoveryError("historical screener returned an unrequested chain")
        symbol = row.get("token_symbol")
        netflow = _finite(row.get("netflow"))
        liquidity = _finite(row.get("liquidity"), positive=True)
        market_cap = _finite(row.get("market_cap_usd"), positive=True)
        age = _finite(row.get("token_age_days"), positive=True)
        sectors = row.get("sectors")
        if (
            not isinstance(symbol, str)
            or not symbol
            or not isinstance(sectors, list)
            or any(not isinstance(sector, str) for sector in sectors)
            or "Stablecoin" in sectors
            or netflow is None
            or liquidity is None
            or liquidity < 250_000
            or market_cap is None
            or market_cap < 1_000_000
            or age is None
            or age < 3
        ):
            continue
        if identity in seen:
            raise HistoricalDiscoveryError("historical screener contains a duplicate token identity")
        seen.add(identity)
        candidates.append({
            "chain": identity[0],
            "token_address": identity[1],
            "token_symbol": symbol,
            "netflow_usd": netflow,
            "liquidity_usd": liquidity,
            "market_cap_usd": market_cap,
            "token_age_days": age,
            "source_row_sha256": _sha256_bytes(canonical_json_bytes(row)),
        })
    candidates.sort(key=lambda row: (-row["netflow_usd"], row["chain"], row["token_address"]))
    if len(candidates) < COHORT_SIZE:
        raise HistoricalDiscoveryError(
            f"historical screener has only {len(candidates)} complete eligible tokens"
        )
    members = candidates[:COHORT_SIZE]
    return {
        "schema_version": 1,
        "rule_version": DESIGN_VERSION,
        "selection_date": SELECTION_DATE.isoformat(),
        "source_endpoint": SCREENER_ENDPOINT,
        "source_response_sha256": _sha256_bytes(canonical_json_bytes(screener_body)),
        "members": members,
    }


def _parse_holdings(
    pages: Iterable[dict[str, Any]], selection: dict[str, Any]
) -> dict[tuple[str, str], dict[date, dict[str, Any]]]:
    pages = tuple(pages)
    if not 1 <= len(pages) <= 2:
        raise HistoricalDiscoveryError("historical holdings requires one or two pages")
    allowed = {
        _identity(member["chain"], member["token_address"]): member
        for member in selection["members"]
    }
    result: dict[tuple[str, str], dict[date, dict[str, Any]]] = defaultdict(dict)
    for page_number, body in enumerate(pages, start=1):
        rows, is_last = _validate_page(body, page=page_number, label="historical holdings")
        if is_last != (page_number == len(pages)):
            raise HistoricalDiscoveryError("historical holdings pagination is incomplete or overrun")
        for row in rows:
            identity = _identity(row.get("chain"), row.get("token_address"))
            if identity not in allowed:
                raise HistoricalDiscoveryError("historical holdings returned an unselected token")
            try:
                day = date.fromisoformat(row.get("date"))
            except (TypeError, ValueError) as exc:
                raise HistoricalDiscoveryError("historical holdings date is invalid") from exc
            if not HOLDINGS_FROM <= day <= HOLDINGS_TO:
                raise HistoricalDiscoveryError("historical holdings row is outside the request range")
            if day in result[identity]:
                raise HistoricalDiscoveryError("historical holdings contains a duplicate token/day")
            if row.get("token_symbol") != allowed[identity]["token_symbol"]:
                raise HistoricalDiscoveryError("historical holdings token symbol changed from selection")
            balance = _finite(row.get("balance"))
            if balance is not None and balance < 0:
                raise HistoricalDiscoveryError("historical holdings balance is negative")
            holders = row.get("holders_count")
            if isinstance(holders, bool) or not isinstance(holders, int) or holders < 0:
                holders = None
            result[identity][day] = {
                "balance": balance,
                "holders_count": holders,
            }
    return result


def _parse_ohlcv(
    selection: dict[str, Any], bodies: dict[str, dict[str, Any]]
) -> dict[tuple[str, str], dict[date, float]]:
    result: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    for logical_id, payload in _ohlcv_requests(selection):
        body = bodies.get(logical_id)
        if not isinstance(body, dict):
            raise HistoricalDiscoveryError(f"missing OHLCV response for {logical_id}")
        parsed = _parse_ohlcv_body(payload, body)
        for identity, prices in parsed.items():
            if set(result[identity]).intersection(prices):
                raise HistoricalDiscoveryError("OHLCV batches duplicate a token/day")
            result[identity].update(prices)
    return result


def _parse_ohlcv_body(
    payload: dict[str, Any], body: dict[str, Any]
) -> dict[tuple[str, str], dict[date, float]]:
    if body.get("chain") != payload["chain"] or body.get("timeframe") != "1d":
        raise HistoricalDiscoveryError("OHLCV response identity does not match its request")
    if body.get("truncated", False) is not False:
        raise HistoricalDiscoveryError("OHLCV response is truncated")
    requested = {
        _identity(payload["chain"], address) for address in payload["token_addresses"]
    }
    if isinstance(body.get("tokens"), list):
        token_rows = body["tokens"]
    elif len(requested) == 1 and isinstance(body.get("data"), list):
        token_rows = [{"token_address": body.get("token_address"), "data": body["data"]}]
    else:
        raise HistoricalDiscoveryError("OHLCV response has an invalid batch shape")
    returned: set[tuple[str, str]] = set()
    result: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    for token_row in token_rows:
        if not isinstance(token_row, dict) or not isinstance(token_row.get("data"), list):
            raise HistoricalDiscoveryError("OHLCV token record is invalid")
        identity = _identity(payload["chain"], token_row.get("token_address"))
        if identity not in requested or identity in returned:
            raise HistoricalDiscoveryError("OHLCV returned an unexpected or duplicate token")
        returned.add(identity)
        previous: datetime | None = None
        for candle in token_row["data"]:
            if not isinstance(candle, dict):
                raise HistoricalDiscoveryError("OHLCV candle is not an object")
            raw_start = candle.get("interval_start")
            if not isinstance(raw_start, str):
                raise HistoricalDiscoveryError("OHLCV interval_start is invalid")
            try:
                start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HistoricalDiscoveryError("OHLCV interval_start is invalid") from exc
            if start.tzinfo is None or start.utcoffset() is None:
                raise HistoricalDiscoveryError("OHLCV interval_start is not timezone-aware")
            start = start.astimezone(timezone.utc)
            if start.time() != datetime.min.time() or (previous is not None and start <= previous):
                raise HistoricalDiscoveryError(
                    "OHLCV daily candles are not strictly ordered at 00:00 UTC"
                )
            previous = start
            day = start.date()
            if not SIGNAL_FROM + timedelta(days=1) <= day <= HOLDINGS_TO:
                raise HistoricalDiscoveryError("OHLCV candle is outside the requested date range")
            close = _finite(candle.get("close"), positive=True)
            if close is not None:
                result[identity][day] = close
    if returned != requested:
        raise HistoricalDiscoveryError("OHLCV response omits a requested token")
    return result


def _block_id(day: date) -> str:
    offset = (day - SIGNAL_FROM).days
    return f"block-{offset // 14 + 1}"


def _feature_rows(
    holdings: dict[tuple[str, str], dict[date, dict[str, Any]]],
    prices: dict[tuple[str, str], dict[date, float]],
    selection: dict[str, Any],
) -> list[dict[str, Any]]:
    members = {
        _identity(member["chain"], member["token_address"]): member
        for member in selection["members"]
    }
    rows: list[dict[str, Any]] = []
    for identity in sorted(members):
        member = members[identity]
        series = holdings.get(identity, {})
        day = SIGNAL_FROM
        while day <= SIGNAL_TO:
            window_days = [day - timedelta(days=index) for index in range(LOOKBACK_DAYS, -1, -1)]
            window = [series.get(item) for item in window_days]
            entry_day = day + timedelta(days=1)
            exit_day = entry_day + timedelta(days=HOLD_DAYS)
            price_path = prices.get(identity, {})
            outcome_days = [
                entry_day + timedelta(days=offset) for offset in range(HOLD_DAYS + 1)
            ]
            outcome_available = all(item in price_path for item in outcome_days)
            metrics: dict[str, Any] = {
                "balance_change_4d_pct": None,
                "accumulation_persistence_4d": None,
                "accumulation_retention_4d": None,
                "holder_count_change_4d": None,
            }
            if all(item is not None for item in window):
                balances = [item["balance"] for item in window]
                holders = [item["holders_count"] for item in window]
                if all(value is not None for value in balances) and balances[0] > 0:
                    deltas = [balances[index] - balances[index - 1] for index in range(1, len(balances))]
                    gross_positive = sum(max(delta, 0.0) for delta in deltas)
                    net_positive = max(balances[-1] - balances[0], 0.0)
                    metrics["balance_change_4d_pct"] = 100.0 * (balances[-1] / balances[0] - 1.0)
                    metrics["accumulation_persistence_4d"] = sum(delta > 0 for delta in deltas) / LOOKBACK_DAYS
                    metrics["accumulation_retention_4d"] = (
                        None if gross_positive <= 0 else net_positive / gross_positive
                    )
                if holders[0] is not None and holders[-1] is not None:
                    metrics["holder_count_change_4d"] = holders[-1] - holders[0]
            base = (
                metrics["balance_change_4d_pct"] is not None
                and metrics["balance_change_4d_pct"] > 0
                and metrics["accumulation_persistence_4d"] >= 0.5
                and metrics["accumulation_retention_4d"] is not None
                and metrics["accumulation_retention_4d"] >= 0.8
                and metrics["holder_count_change_4d"] is not None
            )
            arm = "inactive"
            if base:
                arm = (
                    "holder-breadth-positive-daily-v1"
                    if metrics["holder_count_change_4d"] > 0
                    else "holder-breadth-nonpositive-daily-v1"
                )
            rows.append({
                "signal_date": day.isoformat(),
                "block_id": _block_id(day),
                "chain": identity[0],
                "token_address": identity[1],
                "token_symbol": member["token_symbol"],
                **metrics,
                "base_predicate": base,
                "arm": arm,
                "entry_date": entry_day.isoformat(),
                "exit_date": exit_day.isoformat(),
                "entry_price_usd": price_path.get(entry_day),
                "exit_price_usd": price_path.get(exit_day),
                "outcome_available": outcome_available,
            })
            day += timedelta(days=1)
    return rows


def _event_rows(features: list[dict[str, Any]], selection: dict[str, Any]) -> list[dict[str, Any]]:
    members = {
        _identity(member["chain"], member["token_address"]): member
        for member in selection["members"]
    }
    last_exit: dict[tuple[str, str, str], date] = {}
    events: list[dict[str, Any]] = []
    for row in sorted(features, key=lambda item: (item["signal_date"], item["chain"], item["token_address"])):
        if row["arm"] == "inactive":
            continue
        identity = _identity(row["chain"], row["token_address"])
        signal_day = date.fromisoformat(row["signal_date"])
        key = (*identity, row["arm"])
        if key in last_exit and signal_day < last_exit[key]:
            continue
        exit_day = date.fromisoformat(row["exit_date"])
        last_exit[key] = exit_day
        gross = (
            100.0 * (row["exit_price_usd"] / row["entry_price_usd"] - 1.0)
            if row["outcome_available"] else None
        )
        liquidity = members[identity]["liquidity_usd"]
        events.append({
            "arm": row["arm"],
            "block_id": row["block_id"],
            "chain": row["chain"],
            "token_address": row["token_address"],
            "token_symbol": row["token_symbol"],
            "signal_date": row["signal_date"],
            "entry_date": row["entry_date"],
            "exit_date": row["exit_date"],
            "selection_liquidity_usd": liquidity,
            "virtual_notional_usd": min(1000.0, 0.001 * liquidity),
            "outcome_status": "scored" if gross is not None else "missing_contiguous_ohlcv",
            "gross_return_pct": gross,
            "base_objective_pct": None if gross is None else entry_objective_pct(gross, BASE_BPS),
            "stress_objective_pct": None if gross is None else entry_objective_pct(gross, STRESS_BPS),
        })
    return events


def _token_equal(rows: list[dict[str, Any]], field: str) -> float | None:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        grouped[_identity(row["chain"], row["token_address"])].append(float(row[field]))
    if not grouped:
        return None
    return statistics.fmean(statistics.fmean(values) for values in grouped.values())


def _arm_summary(events: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    eligible = [row for row in events if row["arm"] == arm]
    rows = [row for row in eligible if row["outcome_status"] == "scored"]
    tokens = {_identity(row["chain"], row["token_address"]) for row in eligible}
    block_means = {
        block: _token_equal([row for row in rows if row["block_id"] == block], "base_objective_pct")
        for block in ("block-1", "block-2", "block-3", "block-4")
    }
    return {
        "arm": arm,
        "eligible_episodes": len(eligible),
        "scored_episodes": len(rows),
        "outcome_coverage_rate": None if not eligible else len(rows) / len(eligible),
        "tokens": len(tokens),
        "token_equal_base_mean_pct": _token_equal(rows, "base_objective_pct"),
        "event_median_base_pct": None if not rows else statistics.median(row["base_objective_pct"] for row in rows),
        "token_equal_stress_mean_pct": _token_equal(rows, "stress_objective_pct"),
        "block_token_equal_base_mean_pct": block_means,
    }


def _spread(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def build_discovery(
    screener_body: dict[str, Any],
    holdings_pages: list[dict[str, Any]],
    ohlcv_bodies: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selection = select_cohort(screener_body)
    holdings = _parse_holdings(holdings_pages, selection)
    prices = _parse_ohlcv(selection, ohlcv_bodies)
    features = _feature_rows(holdings, prices, selection)
    events = _event_rows(features, selection)
    positive = _arm_summary(events, "holder-breadth-positive-daily-v1")
    reference = _arm_summary(events, "holder-breadth-nonpositive-daily-v1")
    block_spreads = {
        block: _spread(
            positive["block_token_equal_base_mean_pct"][block],
            reference["block_token_equal_base_mean_pct"][block],
        )
        for block in ("block-1", "block-2", "block-3", "block-4")
    }
    comparison = {
        "token_equal_base_spread_pct": _spread(
            positive["token_equal_base_mean_pct"], reference["token_equal_base_mean_pct"]
        ),
        "event_median_base_spread_pct": _spread(
            positive["event_median_base_pct"], reference["event_median_base_pct"]
        ),
        "block_spreads_pct": block_spreads,
    }
    positive_blocks = sum(value is not None and value > 0 for value in block_spreads.values())
    reasons = []
    if positive["eligible_episodes"] < 10 or reference["eligible_episodes"] < 10:
        reasons.append("fewer than 10 non-overlapping events in an arm")
    if positive["outcome_coverage_rate"] != 1.0 or reference["outcome_coverage_rate"] != 1.0:
        reasons.append("outcome coverage is below 100% in an arm")
    if positive["tokens"] < 5 or reference["tokens"] < 5:
        reasons.append("fewer than five tokens in an arm")
    if comparison["token_equal_base_spread_pct"] is None or comparison["token_equal_base_spread_pct"] <= 0:
        reasons.append("non-positive token-equal base spread")
    if comparison["event_median_base_spread_pct"] is None or comparison["event_median_base_spread_pct"] <= 0:
        reasons.append("non-positive event-median base spread")
    if positive["token_equal_stress_mean_pct"] is None or positive["token_equal_stress_mean_pct"] <= 0:
        reasons.append("non-positive positive-arm stress mean")
    if positive_blocks < 3:
        reasons.append("positive block spread in fewer than three of four blocks")
    summary = {
        "schema_version": 1,
        "rule_version": DESIGN_VERSION,
        "selection_status": (
            "advances_to_prospective_plumbing" if not reasons else "does_not_advance"
        ),
        "arms": [positive, reference],
        "comparison": comparison,
        "gate_reasons": reasons,
        "evidence_limits": [
            "historical beta discovery, not an untouched prospective holdout",
            "daily OHLCV close proxy, not a timestamped executable quote",
            "liquidity observed only at cohort selection",
            "fixed cost sensitivities, not observed fees or slippage",
            "Smart-Money wallet-label effective-date semantics are not separately documented",
        ],
    }
    return selection, features, events, summary


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in fields})
    return output.getvalue().encode("utf-8")


def _format_pct(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:+.4f}%"


def _report_text(summary: dict[str, Any]) -> str:
    arms = {row["arm"]: row for row in summary["arms"]}
    positive = arms["holder-breadth-positive-daily-v1"]
    reference = arms["holder-breadth-nonpositive-daily-v1"]
    comparison = summary["comparison"]
    reasons = summary["gate_reasons"] or ["none"]
    return f"""# Historical holder-breadth discovery

Status: **{summary['selection_status']}**. This is discovery-only paper evidence,
not a finding of profitability.

The positive-breadth arm produced {positive['eligible_episodes']} non-overlapping
eligible events across {positive['tokens']} tokens, with
{positive['scored_episodes']} scored outcomes. The non-positive reference
produced {reference['eligible_episodes']} eligible events across
{reference['tokens']} tokens, with {reference['scored_episodes']} scored.

| Metric | Positive breadth | Non-positive breadth | Spread |
| --- | ---: | ---: | ---: |
| Token-equal base mean | {_format_pct(positive['token_equal_base_mean_pct'])} | {_format_pct(reference['token_equal_base_mean_pct'])} | {_format_pct(comparison['token_equal_base_spread_pct'])} |
| Event median after base costs | {_format_pct(positive['event_median_base_pct'])} | {_format_pct(reference['event_median_base_pct'])} | {_format_pct(comparison['event_median_base_spread_pct'])} |
| Token-equal stress mean | {_format_pct(positive['token_equal_stress_mean_pct'])} | {_format_pct(reference['token_equal_stress_mean_pct'])} | — |

Gate reasons: {', '.join(reasons)}.

The test uses provider historical daily holdings and independent daily OHLCV
close proxies. Liquidity is frozen only at cohort selection, Smart-Money
wallet-label effective-date semantics are not separately documented, and
100/250 bps per-side costs are sensitivities rather than observed fills. A
survivor may enter the next prospective plumbing cycle; it still cannot satisfy
the eight-week, 100-fill, 20-token, actual-cost profitability gates.
"""


def _render_outputs(
    screener_body: dict[str, Any],
    holdings_pages: list[dict[str, Any]],
    ohlcv_bodies: dict[str, dict[str, Any]],
) -> dict[str, bytes]:
    selection, features, events, summary = build_discovery(
        screener_body, holdings_pages, ohlcv_bodies
    )
    return {
        "derived/selection.json": canonical_json_bytes(selection),
        "derived/daily-features.csv": _csv_bytes(features, FEATURE_FIELDS),
        "derived/events.csv": _csv_bytes(events, EVENT_FIELDS),
        "derived/summary.json": canonical_json_bytes(summary),
        "REPORT.md": _report_text(summary).encode("utf-8"),
    }


def _response_for(root: Path, guard: BudgetGuard, logical_id: str) -> NansenEvidenceResponse:
    entry = next((item for item in guard.replay().entries if item.logical_request_id == logical_id), None)
    if entry is None or entry.state not in {"confirmed_zero", "confirmed_used"}:
        raise HistoricalDiscoveryError(f"missing confirmed response for {logical_id}")
    base = root / "raw/nansen" / entry.reservation_id
    prefix = f"attempt-{entry.attempt_count}"
    return _load_nansen_response(
        base / f"{prefix}-response.json", base / f"{prefix}-response-metadata.json"
    )


def _artifact_records(root: Path, paths: Iterable[Path]) -> list[dict[str, str]]:
    unique = sorted(
        {Path(path).absolute() for path in paths},
        key=lambda path: _regular_relative_path(root, path, label="terminal artifact").as_posix(),
    )
    return [
        {
            "path": _regular_relative_path(root, path, label="terminal artifact").as_posix(),
            "sha256": _sha256_file(path),
        }
        for path in unique
    ]


def _terminal_recorded_at(
    root: Path,
    stage: str,
    clock: Callable[[], datetime],
) -> str:
    recorded: list[str] = []
    for path in (root / f"budget/snapshots/{stage}.json", root / f"seals/{stage}.json"):
        if not path.exists():
            continue
        _regular_relative_path(root, path, label="terminal transaction artifact")
        try:
            document = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoricalDiscoveryError("terminal transaction artifact is unreadable") from exc
        value = document.get("recorded_at") if isinstance(document, dict) else None
        if not isinstance(document, dict) or document.get("stage") != stage or not isinstance(value, str):
            raise HistoricalDiscoveryError("terminal transaction artifact has invalid identity")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise HistoricalDiscoveryError("terminal transaction timestamp is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None or _utc_text(parsed) != value:
            raise HistoricalDiscoveryError("terminal transaction timestamp is not canonical UTC")
        recorded.append(value)
    if len(set(recorded)) > 1:
        raise HistoricalDiscoveryError("terminal transaction timestamps disagree")
    return recorded[0] if recorded else _utc_text(clock())


def _parse_evidence_time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalDiscoveryError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDiscoveryError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalDiscoveryError(f"{label} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _validate_metadata_only_response(
    request_path: Path,
    metadata_path: Path,
    *,
    response_filename: str,
) -> None:
    try:
        request = json.loads(request_path.read_bytes())
        raw_metadata = metadata_path.read_bytes()
        metadata = json.loads(raw_metadata)
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalDiscoveryError("partial Nansen response metadata is unreadable") from exc
    credit_values = (
        metadata.get(name) for name in ("credit_cost", "credit_used", "credit_remaining")
    ) if isinstance(metadata, dict) else ()
    if (
        not isinstance(request, dict)
        or not isinstance(metadata, dict)
        or set(metadata) != PARTIAL_RESPONSE_METADATA_FIELDS
        or canonical_json_bytes(metadata) != raw_metadata
        or metadata.get("schema_version") != 1
        or metadata.get("attempt") != 1
        or isinstance(metadata.get("status_code"), bool)
        or not isinstance(metadata.get("status_code"), int)
        or metadata.get("response_file") != response_filename
        or not isinstance(metadata.get("response_sha256"), str)
        or len(metadata["response_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in metadata["response_sha256"])
        or not isinstance(metadata.get("response_headers"), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.get("response_headers", {}).items()
        )
        or (
            metadata.get("request_id") is not None
            and not isinstance(metadata.get("request_id"), str)
        )
        or not isinstance(metadata.get("credit_header_errors"), list)
        or any(not isinstance(value, str) for value in metadata["credit_header_errors"])
        or not isinstance(metadata.get("body_parse_status"), str)
        or not metadata["body_parse_status"]
        or any(
            value is not None
            and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
            for value in credit_values
        )
    ):
        raise HistoricalDiscoveryError("partial Nansen response metadata is invalid")
    request_started = _parse_evidence_time(
        request.get("request_started_at"), label="request-artifact start"
    )
    request_written = _parse_evidence_time(
        request.get("artifact_written_at"), label="request-artifact write"
    )
    started = _parse_evidence_time(metadata["request_started_at"], label="response start")
    retrieved = _parse_evidence_time(
        metadata["response_retrieved_at"], label="response retrieval"
    )
    written = _parse_evidence_time(
        metadata["artifact_written_at"], label="response metadata write"
    )
    if not request_started <= request_written <= started <= retrieved <= written:
        raise HistoricalDiscoveryError("partial Nansen response timestamps are reversed")


def _verified_request_attempt_count(root: Path, guard: BudgetGuard) -> int:
    expected_requests: set[Path] = set()
    expected_responses: set[Path] = set()
    expected_metadata: set[Path] = set()
    for entry in guard.replay().entries:
        if entry.attempt_count != 1:
            raise HistoricalDiscoveryError("historical discovery contains a forbidden retry")
        base = root / "raw/nansen" / entry.reservation_id
        request_path = base / "attempt-1-request.json"
        if entry.request_artifact_sha256 is None:
            response_path = base / "attempt-1-response.json"
            metadata_path = base / "attempt-1-response-metadata.json"
            if (
                entry.state == "failed_before_pricing"
                and not request_path.exists()
                and not response_path.exists()
                and not metadata_path.exists()
            ):
                continue
            raise HistoricalDiscoveryError("budget entry has no bound request-attempt artifact")
        _regular_relative_path(root, request_path, label="Nansen request-attempt artifact")
        if _sha256_file(request_path) != entry.request_artifact_sha256:
            raise HistoricalDiscoveryError("Nansen request-attempt hash differs from the ledger")
        expected_requests.add(request_path.absolute())

        response_path = base / "attempt-1-response.json"
        metadata_path = base / "attempt-1-response-metadata.json"
        if entry.response_artifact_sha256 is None:
            if (
                entry.state == "ambiguous"
                and response_path.exists() is False
                and metadata_path.exists()
            ):
                _regular_relative_path(
                    root, metadata_path, label="partial Nansen response metadata"
                )
                _validate_metadata_only_response(
                    request_path,
                    metadata_path,
                    response_filename=response_path.name,
                )
                expected_metadata.add(metadata_path.absolute())
                continue
            if response_path.exists() or metadata_path.exists():
                raise HistoricalDiscoveryError("unbound Nansen response evidence exists")
            continue
        _regular_relative_path(root, response_path, label="Nansen response artifact")
        _regular_relative_path(root, metadata_path, label="Nansen response metadata")
        try:
            guard._verify_response_artifact(entry, entry.response_artifact_sha256)
        except BudgetError as exc:
            raise HistoricalDiscoveryError("Nansen response evidence differs from the ledger") from exc
        expected_responses.add(response_path.absolute())
        expected_metadata.add(metadata_path.absolute())

    actual_requests = {
        path.absolute() for path in root.glob("raw/nansen/*/attempt-*-request.json")
    }
    actual_responses = {
        path.absolute() for path in root.glob("raw/nansen/*/attempt-*-response.json")
    }
    actual_metadata = {
        path.absolute()
        for path in root.glob("raw/nansen/*/attempt-*-response-metadata.json")
    }
    if (
        actual_requests != expected_requests
        or actual_responses != expected_responses
        or actual_metadata != expected_metadata
    ):
        raise HistoricalDiscoveryError("Nansen archive does not exactly match the budget ledger")
    return len(expected_requests)


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
    request_attempts = _verified_request_attempt_count(root, guard)
    if request_attempts > MAX_CALLS:
        raise HistoricalDiscoveryError("authenticated request-attempt ceiling exceeded")
    budget_snapshot = guard.snapshot(stage, recorded_at=recorded_at)
    before_seal = [
        path for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json" and "seals" not in path.parts
    ]
    before_seal.extend(output_paths)
    before_seal.append(budget_snapshot)
    records = _artifact_records(root, before_seal)
    seal = {
        "schema_version": 1,
        "stage": stage,
        "recorded_at": recorded_at,
        "terminal_reason": reason,
        "authenticated_request_attempts": request_attempts,
        "artifacts": records,
        "budget_snapshot_path": budget_snapshot.relative_to(root).as_posix(),
        "budget_snapshot_sha256": _sha256_file(budget_snapshot),
    }
    seal_path = write_bytes_once_or_adopt_exact(
        root / f"seals/{stage}.json",
        canonical_json_bytes(seal),
        metadata={"kind": "historical_discovery_seal", "stage": stage},
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
    prefix = "# Historical holder-breadth discovery\n\nStatus: **unscorable** — "
    suffix = ". No automatic rerun is permitted.\n"
    if report_path.exists():
        _regular_relative_path(
            manifest_path.parent, report_path, label="unscorable report"
        )
        try:
            existing = report_path.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            raise HistoricalDiscoveryError("unscorable report is unreadable") from exc
        if not existing.startswith(prefix) or not existing.endswith(suffix):
            raise HistoricalDiscoveryError("unscorable report has invalid recovery bytes")
        reason = existing[len(prefix):-len(suffix)]
        if not reason:
            raise HistoricalDiscoveryError("unscorable report has no recovery reason")
    report = (
        f"{prefix}{reason}{suffix}"
    ).encode("utf-8")
    report_path = write_bytes_once_or_adopt_exact(
        report_path, report,
        metadata={"kind": "historical_discovery_unscorable_report"},
    )
    return _finalize(
        manifest_path, manifest, guard, stage="unscorable", reason=reason,
        clock=clock, output_paths=(report_path,),
    )


def start_historical_discovery(
    manifest_path: Path,
    *,
    nansen: Any,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path).absolute()
    manifest = load_historical_manifest(manifest_path)
    if manifest["stage"] in {"completed", "unscorable"}:
        check_historical_discovery(manifest_path)
        return manifest
    root = manifest_path.parent
    _reject_bundle_symlinks(root)
    guard = BudgetGuard(root, MAX_CALLS, MAX_CREDITS)
    try:
        guard.reconcile_inflight()
        openapi = nansen.fetch_openapi()
        if not isinstance(openapi, bytes):
            raise HistoricalDiscoveryError("public OpenAPI response is not exact bytes")
        contract_path = write_bytes_once_or_adopt_exact(
            root / "raw/contracts/nansen-openapi.json", openapi,
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
                "account preflight does not prove the twelve-credit ceiling is available", clock,
            )

        screener_response, _ = _nansen_call(
            root=root, guard=guard, nansen=nansen,
            logical_request_id="historical-screener-page-1", method="POST",
            endpoint=SCREENER_ENDPOINT, payload=_screener_payload(), expected_credits=5,
            clock=clock, sleep=sleep, allow_retry=False,
        )
        if not isinstance(screener_response.body, dict):
            raise HistoricalDiscoveryError("historical screener response is not an object")
        selection = select_cohort(screener_response.body)

        holdings_pages = []
        for page in (1, 2):
            response, _ = _nansen_call(
                root=root, guard=guard, nansen=nansen,
                logical_request_id=f"historical-holdings-page-{page}", method="POST",
                endpoint=HOLDINGS_ENDPOINT, payload=_holdings_payload(selection, page),
                expected_credits=1, clock=clock, sleep=sleep, allow_retry=False,
            )
            if not isinstance(response.body, dict):
                raise HistoricalDiscoveryError("historical holdings response is not an object")
            holdings_pages.append(response.body)
            _, last = _validate_page(response.body, page=page, label="historical holdings")
            if last:
                break
        if not last:
            raise HistoricalDiscoveryError("historical holdings exceeds the preregistered two-page cap")

        ohlcv_bodies: dict[str, dict[str, Any]] = {}
        for logical_id, payload in _ohlcv_requests(selection):
            response, _ = _nansen_call(
                root=root, guard=guard, nansen=nansen,
                logical_request_id=logical_id, method="POST",
                endpoint=OHLCV_ENDPOINT, payload=payload, expected_credits=1,
                clock=clock, sleep=sleep, allow_retry=False,
            )
            if not isinstance(response.body, dict):
                raise HistoricalDiscoveryError("OHLCV response is not an object")
            ohlcv_bodies[logical_id] = response.body

        rendered = _render_outputs(screener_response.body, holdings_pages, ohlcv_bodies)
        output_paths = []
        for relative, content in rendered.items():
            output_paths.append(write_bytes_once_or_adopt_exact(
                root / relative, content,
                metadata={"kind": "historical_discovery_output", "path": relative},
            ))
        return _finalize(
            manifest_path, manifest, guard, stage="completed", reason=None,
            clock=clock, output_paths=(contract_path, metadata_path, *output_paths),
        )
    except (HistoricalDiscoveryError, PilotError, BudgetError) as exc:
        return _finalize_unscorable(manifest_path, manifest, guard, str(exc), clock)


def check_historical_discovery(manifest_path: Path) -> tuple[Path, ...]:
    manifest_path = Path(manifest_path).absolute()
    manifest = load_historical_manifest(manifest_path)
    if manifest["stage"] not in {"completed", "unscorable"}:
        raise HistoricalDiscoveryError("historical discovery is not terminal")
    root = manifest_path.parent
    verified = []
    seen_paths: set[str] = set()
    for record in manifest["artifacts"]:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise HistoricalDiscoveryError("terminal artifact record is invalid")
        relative = record["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative in seen_paths
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(relative).parts)
        ):
            raise HistoricalDiscoveryError("terminal artifact path is invalid or duplicated")
        seen_paths.add(relative)
        path = root / relative
        _regular_relative_path(root, path, label="terminal artifact")
        if _sha256_file(path) != record["sha256"]:
            raise HistoricalDiscoveryError(f"terminal artifact mismatch: {record['path']}")
        verified.append(path)
    seal_relative = f"seals/{manifest['stage']}.json"
    if seal_relative not in seen_paths:
        raise HistoricalDiscoveryError("terminal manifest does not bind its stage seal")
    seal = json.loads((root / seal_relative).read_text())
    seal_record = next(record for record in manifest["artifacts"] if record["path"] == seal_relative)
    unsealed_records = [record for record in manifest["artifacts"] if record is not seal_record]
    guard = BudgetGuard(root, MAX_CALLS, MAX_CREDITS)
    if (
        not isinstance(seal, dict)
        or seal.get("schema_version") != 1
        or seal.get("stage") != manifest["stage"]
        or seal.get("terminal_reason") != manifest["terminal_reason"]
        or seal.get("authenticated_request_attempts")
        != _verified_request_attempt_count(root, guard)
        or seal.get("authenticated_request_attempts", MAX_CALLS + 1) > MAX_CALLS
        or seal.get("artifacts") != unsealed_records
        or seal.get("budget_snapshot_path") != f"budget/snapshots/{manifest['stage']}.json"
    ):
        raise HistoricalDiscoveryError("terminal stage seal does not match the manifest")
    budget_snapshot_path = root / seal["budget_snapshot_path"]
    if seal.get("budget_snapshot_sha256") != _sha256_file(budget_snapshot_path):
        raise HistoricalDiscoveryError("terminal stage seal has a mismatched budget snapshot")
    totals = guard.replay()
    budget_snapshot = json.loads(budget_snapshot_path.read_text())
    if (
        budget_snapshot.get("stage") != manifest["stage"]
        or budget_snapshot.get("totals") != {"calls": totals.calls, "credits": totals.credits}
        or budget_snapshot.get("provider_remaining") != totals.provider_remaining
        or budget_snapshot.get("journal_head_sha256") != totals.journal_head_sha256
        or budget_snapshot.get("transition_sha256s") != list(totals.transition_sha256s)
        or budget_snapshot.get("halted_reason") != totals.halted_reason
    ):
        raise HistoricalDiscoveryError("terminal budget snapshot does not match replay")
    contract = root / "raw/contracts/nansen-openapi.json"
    if contract.is_file() and _sha256_file(contract) != EXPECTED_OPENAPI_SHA256:
        if manifest["stage"] != "unscorable":
            raise HistoricalDiscoveryError("completed bundle has a mismatched OpenAPI")
    if manifest["stage"] == "completed":
        screener = _response_for(root, guard, "historical-screener-page-1").body
        pages = []
        for page in (1, 2):
            logical = f"historical-holdings-page-{page}"
            if any(entry.logical_request_id == logical for entry in guard.replay().entries):
                pages.append(_response_for(root, guard, logical).body)
        selection = select_cohort(screener)
        ohlcv_bodies = {
            logical_id: _response_for(root, guard, logical_id).body
            for logical_id, _ in _ohlcv_requests(selection)
        }
        expected = _render_outputs(screener, pages, ohlcv_bodies)
        for relative, content in expected.items():
            path = root / relative
            if not path.is_file() or path.read_bytes() != content:
                raise HistoricalDiscoveryError(f"derived output mismatch: {relative}")
    return tuple(sorted(set(verified), key=lambda path: path.as_posix()))
