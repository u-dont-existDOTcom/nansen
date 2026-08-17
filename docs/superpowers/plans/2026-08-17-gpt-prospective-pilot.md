# GPT Prospective Strategy Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, independently verify, and run one schema-v4 prospective experiment in which identity-blinded `gpt-5.6-sol` makes and critiques a long-or-cash token decision before a common four-hour paper outcome is observed.

**Architecture:** A fail-closed staged runner creates append-only, SHA-256-linked experiment seals. Pure modules handle selection, snapshot normalization, GPT contracts, frozen comparators, observed fills, and scoring; narrow HTTP adapters and a persistent budget ledger own external effects. The first five one-credit Nansen calls seal the decision, and at most five later calls collect entry/exit trades and OHLCV without ever creating an order.

**Tech Stack:** Python 3.12, standard library dataclasses/JSON/hashlib/fcntl, existing `httpx`, `python-dotenv`, `pandas`, and `pytest`; OpenAI Responses API over `httpx`; Nansen API v1.

## Global Constraints

- Preserve every committed schema-v1, schema-v2, and schema-v3 byte and hash.
- Use `gpt-5.6-sol` with `reasoning.effort: high`; do not substitute another model.
- Give GPT no tools, web access, repository access, conversation memory, token name, symbol, address, social metadata, prior result, or future/outcome field.
- The paper action space is exactly `LONG` or `ABSTAIN`; no short, order, wallet, account mutation, venue submission, or capital movement.
- Use virtual notional `min($1,000, 0.001 * point_in_time_liquidity_usd)` and one common four-hour outcome.
- Make at most ten billable Nansen requests and consume at most ten Nansen credits; ambiguous transmissions consume a conservative slot and are never retried automatically.
- Archive exact raw response bytes, canonical request payloads, allowed response headers, request/retrieval times, request IDs, schemas, pagination/completeness markers, and hashes.
- Missing, incomplete, stale, malformed, non-finite, unfilled, or unscorable evidence never becomes zero.
- Follow strict red-green-refactor cycles and commit each task separately.
- Keep `handoff.md` untracked and out of every commit unless the repository owner separately asks to publish it.
- Do not push, mutate a PR, publish, or merge remotely.

---

## Pre-implementation checkpoint

Before Task 1, validate and commit the governing records so every later preregistration hash resolves to committed history:

```bash
python -m json.tool docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json >/dev/null
git diff --check
git add docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-design.md docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json docs/superpowers/plans/2026-08-17-gpt-prospective-pilot.md
git commit -m "Plan prospective GPT strategy pilot"
git status --short
```

Expected: the commit contains exactly those three files; afterward only the owner-managed `handoff.md` is untracked. Record the commit hash and use it as the planning rollback/checkpoint. Do not begin implementation from uncommitted governing bytes.

---

## File structure

Create focused modules instead of extending the already large CLI:

- `src/nansen_signal_lab/artifacts.py`: canonical JSON, atomic single-file writes, and durable response-plus-sidecar transactions extracted from `cli.py`.
- `src/nansen_signal_lab/budget.py`: locked persistent call/credit reservations and settlements.
- `src/nansen_signal_lab/prospective_schema.py`: schema-v4 validation, lifecycle transitions, artifact hashes, and stage seals.
- `src/nansen_signal_lab/prospective_snapshot.py`: screener contract, prior-token exclusion, deterministic selection, complete-bucket normalization, and identity blinding.
- `src/nansen_signal_lab/openai_client.py`: exact no-tool Responses API transport and model-access preflight.
- `src/nansen_signal_lab/gpt_protocol.py`: Pass 1/Pass 2 JSON schemas, prompts, local validation, repair bounds, and leakage scans.
- `src/nansen_signal_lab/prospective_comparators.py`: immutable schema-v3 import, predicate application, and paired-veto decisions.
- `src/nansen_signal_lab/prospective_execution.py`: DEX page contracts, observed fills, closed OHLCV validation, scoring, and verdicts.
- `src/nansen_signal_lab/prospective_runner.py`: dependency-injected lifecycle orchestration; the only module coordinating external calls.
- `src/nansen_signal_lab/cli.py`: thin `pilot-init`, `pilot-start`, `pilot-settle`, `pilot-replay`, and `pilot-check` command adapters.

Tests mirror those units in `tests/test_artifacts.py`, `tests/test_budget.py`, `tests/test_prospective_schema.py`, `tests/test_prospective_snapshot.py`, `tests/test_openai_client.py`, `tests/test_gpt_protocol.py`, `tests/test_prospective_comparators.py`, `tests/test_prospective_execution.py`, and `tests/test_prospective_runner.py`.

---

### Task 1: Extract and extend durable artifact storage

**Files:**
- Create: `src/nansen_signal_lab/artifacts.py`
- Modify: `src/nansen_signal_lab/cli.py`
- Create: `tests/test_artifacts.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `canonical_json_bytes(value: Any) -> bytes`
- Produces: `atomic_replace_bytes(path: str | Path, content: bytes) -> Path`
- Produces: `write_bytes_once(path: str | Path, content: bytes) -> Path`
- Produces: `write_bytes_once_or_adopt_exact(path: str | Path, content: bytes, *, metadata: dict[str, Any]) -> Path`
- Produces: `install_or_quarantine_bytes_once(path: str | Path, content: bytes, *, metadata: dict[str, Any]) -> Path`
- Produces: `write_json_once(path: str | Path, value: Any) -> Path`
- Produces: `write_api_artifacts(*, body: Any | None, payload: dict[str, Any] | None, endpoint: str, output_path: str | Path, cache_hit: bool, response_retrieved_at: str, artifact_written_at: str, response_metadata: dict[str, Any] | None = None, overwrite: bool = True, raw_response_bytes: bytes | None = None) -> tuple[Path, Path]`
- Preserves: the existing `write_api_artifacts` public behavior and durable recovery semantics.

- [ ] **Step 1: Move the artifact regressions behind the new module and make them fail**

Create `tests/test_artifacts.py` by moving the transaction, crash-recovery, concurrency, collision, and exact-pair tests currently targeting private helpers in `tests/test_cli.py`. Import `src.nansen_signal_lab.artifacts as artifacts`. Add this new failing contract:

```python
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
```

Add a second case with `body=None` and HTML error bytes. Assert the exact bytes install successfully and the sidecar records `response_parse_status="non_json"`; add empty-body and JSON-array error cases as well. Add a different-bytes collision case: the installed target remains unchanged, the newly received bytes and collision metadata survive under `.conflicts/<target-name>/<received-sha256>.*`, and the call raises. An identical-bytes collision may raise without a second copy.

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_artifacts.py
```

Expected: collection/import failure because `artifacts.py` does not exist.

- [ ] **Step 3: Extract the transaction code and add canonical single-file primitives**

Move `_flow_artifact_paths` through `write_api_artifacts` from `cli.py` into `artifacts.py`, retaining directory locking, fsync, transaction markers, recovery, rollback, and overwrite refusal. Add:

```python
def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

def write_json_once(path: str | Path, value: Any) -> Path:
    return write_bytes_once(path, canonical_json_bytes(value))
```

Implement `atomic_replace_bytes` with a same-directory `mkstemp`, complete byte write, file flush/fsync, `os.replace`, and parent-directory fsync; unlink the temporary file on every pre-replace exception. Implement `write_bytes_once` under the existing directory lock by opening the resolved path with flags `O_CREAT | O_EXCL | O_WRONLY` and mode `0o600`, followed by a complete byte write, file flush/fsync, and parent-directory fsync; reject every existing target even when the bytes match, and unlink a partially created target on write/fsync failure. `install_or_quarantine_bytes_once` uses that primitive, but on a different-bytes collision writes the received bytes and canonical metadata with content-addressed, write-once names under the adjacent `.conflicts` directory before raising corruption; it never overwrites either version. The narrower `write_bytes_once_or_adopt_exact` exists only for deterministic recovery outputs: under the same lock it returns an existing path only when its exact SHA matches the precomputed intended bytes; different bytes go to collision quarantine and raise. Add tests proving ordinary writes still reject identical collisions while recovery adoption accepts only exact bytes.

When `raw_response_bytes` is supplied and `body` is not `None`, parse it, require equality with `body`, and install those exact bytes. When `body=None`, install the raw bytes without requiring JSON and record parse status as `empty`, `non_json`, `json_other`, or `json_object` in the sidecar. Successful endpoint callers must still supply a parsed JSON object; this broader artifact contract exists so malformed and non-2xx evidence is representable. When raw bytes are omitted, require `body` and retain the existing pretty-JSON serialization so old callers and tests remain byte-compatible.

- [ ] **Step 4: Make CLI use the extracted public functions**

Import `write_api_artifacts` and `write_flow_artifacts` from `.artifacts`. Remove the duplicate implementations from `cli.py`. Keep command behavior and output text unchanged.

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_artifacts.py tests/test_cli.py
.venv/bin/python -m pytest -q tests/test_client.py tests/test_experiment.py tests/test_evaluation.py
```

Expected: all pass; crash and concurrent-writer tests remain green.

- [ ] **Step 6: Commit only the extraction**

```bash
git add src/nansen_signal_lab/artifacts.py src/nansen_signal_lab/cli.py tests/test_artifacts.py tests/test_cli.py
git commit -m "Extract durable evidence artifacts"
```

---

### Task 2: Add exact Nansen evidence transport and a persistent budget guard

**Files:**
- Modify: `src/nansen_signal_lab/client.py`
- Create: `src/nansen_signal_lab/budget.py`
- Modify: `tests/test_client.py`
- Create: `tests/test_budget.py`

**Interfaces:**
- Produces: `NansenEvidenceResponse`
- Produces: `NansenRequestFailure`
- Produces: `NansenClient.request_evidence(method: str, endpoint: str, payload: dict[str, Any] | None, *, caller_request_id: str) -> NansenEvidenceResponse`
- Produces: `BudgetGuard(root: Path, max_calls: int = 10, max_credits: int = 10)`
- Produces: `BudgetGuard.reserve(logical_request_id: str, request_sha256: str, endpoint: str, expected_credits: int) -> BudgetReservation`
- Produces: `BudgetGuard.confirm(reservation: BudgetReservation, response: NansenEvidenceResponse, *, response_artifact_sha256: str) -> None`
- Produces: `BudgetGuard.mark_retryable_zero(reservation: BudgetReservation, failure: NansenRequestFailure, *, failure_artifact_sha256: str, retry_not_before: datetime) -> None`
- Produces: `BudgetGuard.begin_retry(reservation: BudgetReservation, *, now: datetime) -> BudgetReservation`
- Produces: `BudgetGuard.reconcile_inflight() -> None`
- Produces: `BudgetGuard.fail(reservation: BudgetReservation, failure: NansenRequestFailure, *, failure_artifact_sha256: str | None) -> None`
- Produces: `BudgetGuard.snapshot(stage: str, *, recorded_at: str) -> Path`
- Produces: `BudgetGuard.replay() -> BudgetTotals`

- [ ] **Step 1: Write failing exact-response and ambiguity tests**

Add to `tests/test_client.py`:

```python
def test_evidence_request_returns_exact_bytes_and_credit_headers(tmp_path, monkeypatch):
    raw = b'{"data":[]}'
    install_transport(monkeypatch, lambda request: httpx.Response(
        200,
        content=raw,
        headers={
            "X-Request-Id": "nansen-1",
            "X-Nansen-Credits-Cost": "1",
            "X-Nansen-Credits-Used": "1",
            "X-Nansen-Credits-Remaining": "9",
        },
    ))
    response = NansenClient(api_key="test", cache_dir=tmp_path).request_evidence(
        "POST", "tgm/flows", {"chain": "base"}, caller_request_id="pilot-1"
    )
    assert response.raw_body == raw
    assert response.request_id == "nansen-1"
    assert (response.credit_cost, response.credit_used, response.credit_remaining) == (1, 1, 9)
```

Also require that a timeout raised after request transmission becomes `NansenRequestFailure(transmitted=True, response=None)`, that every non-2xx response preserves exact bytes and allowed headers in `failure.response`, and that API keys are absent from every exception string and representation. Include a 429 with HTML bytes and a 503 with an empty body: both failures must be constructible and archivable even though `body is None`.

Pass a nonexistent `tmp_path / "cache"` to an evidence-only client and assert it remains absent after `request_evidence`; move cache-directory creation out of `NansenClient.__init__` and into legacy cached methods only. Keep the existing legacy cache regressions green.

Create `tests/test_budget.py` with literal cases for reserve, idempotent lookup of the same stable logical-request ID, rejection of one logical ID with different endpoint/payload identity, confirmed-zero release, confirmed-positive use, rejection of request 11, rejection above ten credits, zero-used failure release, positive-credit error consumption, ambiguous-slot consumption, persisted one-time `retryable_zero`, too-early retry refusal, second-retry refusal, and reconciliation of a crash-left `reserved` attempt to `ambiguous`. Require every transition to be an immutable hash-linked journal entry, rebuild the mutable head and exact totals solely from the journal, and prove that a settlement transition leaves the earlier `decision_sealed` budget snapshot byte-for-byte unchanged. Inject a crash after journal install but before head replacement for both reserve and confirm: a head that is an exact verified journal prefix must be rebuilt automatically, while a non-prefix or divergent head is corruption.

- [ ] **Step 2: Run tests to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_client.py tests/test_budget.py
```

Expected: imports or attributes fail because the new contracts do not exist.

- [ ] **Step 3: Implement the no-cache, no-retry evidence transport**

Add immutable records:

```python
@dataclass(frozen=True)
class NansenEvidenceResponse:
    body: Any | None
    body_parse_status: str
    raw_body: bytes
    status_code: int
    request_started_at: str
    response_retrieved_at: str
    response_headers: dict[str, str]
    request_id: str | None
    credit_cost: int | None
    credit_used: int | None
    credit_remaining: int | None
    credit_header_errors: tuple[str, ...]

class NansenRequestFailure(NansenError):
    def __init__(
        self,
        message: str,
        *,
        transmitted: bool,
        response: NansenEvidenceResponse | None = None,
    ) -> None:
        super().__init__(message)
        self.transmitted = transmitted
        self.response = response
```

`request_evidence` must use `httpx.Client.request` exactly once, send `X-Request-Id`, retain `response.content`, expose only the documented provenance/rate-limit headers (including `Retry-After`), parse non-negative integer credit headers strictly, and never touch the cache. Parse JSON when possible, record `body_parse_status` as `json_object`, `json_other`, `non_json`, or `empty`, but require an object only for a successful 2xx endpoint result. Preserve malformed raw credit headers in the allowed-header map, set the corresponding parsed value to `None`, and name each error in `credit_header_errors` so the response remains archivable before failing closed. A non-2xx response may carry any JSON value, malformed text, or no body and still attaches a complete `NansenEvidenceResponse` to `NansenRequestFailure`; the artifact layer, not this record, preserves its exact bytes. A 2xx non-object/malformed response is also a transmitted failure with the complete response attached. For a transport failure after transmission, set `transmitted=True` with no response. Preserve `post_with_provenance` for existing commands.

- [ ] **Step 4: Implement the locked budget state machine**

Persist the mutable recovery head as canonical JSON with:

```json
{"schema_version":1,"max_calls":10,"max_credits":10,"journal_head_sha256":null,"entries":[]}
```

Every entry has stable `logical_request_id` and `reservation_id`, the SHA-256 of canonical `{method, normalized_relative_endpoint, payload}`, endpoint, expected credits, `attempt_count`, state (`reserved`, `retryable_zero`, `confirmed_zero`, `confirmed_used`, `failed_before_pricing`, or `ambiguous`), retry deadline, request-artifact hash, response/failure-artifact hash, and actual credit headers. The head also carries the last explicit provider remaining balance, initialized from the zero-cost account response. Under one `fcntl` directory lock, every mutation first writes a content-addressed immutable `budget/journal/<sequence>-<sha256>.json` transition containing the previous transition hash, then atomically replaces `budget/head.json`. `replay()` treats the journal as authoritative: a missing sequence, hash break, or illegal transition is corruption; an exact-prefix head left by a crash is atomically rebuilt to the verified journal tip, while a non-prefix/divergent head is corruption. Reserve with `attempt_count=1`; a repeated exact logical-request ID returns its existing state rather than creating a second reservation, while any request-hash mismatch is terminal corruption. Before transmission, install the attempt request artifact containing canonical method/relative endpoint/payload, request SHA, caller ID, start time, and `transmission_may_begin=true`, then bind its hash in the ledger. Archive every received response or failure before settling its reservation, then bind the response/failure artifact SHA in the next transition. Count `reserved`, `retryable_zero`, `confirmed_used`, and `ambiguous` against calls; count expected credits for unresolved/ambiguous entries and actual used credits for confirmed entries. A successful explicit `credit_used=0` becomes `confirmed_zero` and releases its reservation from both totals. `fail` records `failed_before_pricing` only for a pre-transmission failure or an explicit `credit_used=0`; it records `confirmed_used` for a positive explicit credit use and `ambiguous` for a transmitted failure without usable credit evidence.

`mark_retryable_zero` accepts only a 429 with explicit zero use, a persisted failure artifact, `attempt_count=1`, and a retry deadline no more than 60 seconds after the response time. `begin_retry` requires that deadline to have passed, atomically returns the same reservation to `reserved`, and sets `attempt_count=2`; no state can begin a third attempt. On runner startup, recovery first checks the stable attempt artifact paths: an exact response artifact for a still-reserved entry is validated, ledgered, and reused without HTTP; a confirmed ledger entry and exact response are reused; confirmed-without-artifact is terminal evidence loss; and an installed request artifact with no response is reconciled to `ambiguous` and never retransmitted. A separately persisted `retryable_zero` entry may resume its one recorded retry. `snapshot()` writes a stage-named, immutable cumulative view containing current totals, ordered transition hashes, and journal-head hash; only this snapshot and the immutable transitions it names enter a stage seal, never mutable `head.json`.

`confirm` requires explicit, parseable cost, used, and remaining headers. Starting from the account baseline, each response remaining balance must equal `previous_remaining - credit_used`; zero-use responses must preserve it. A 2xx response with any missing/malformed pricing header or remaining-balance mismatch is archived, consumes the conservative reservation as `ambiguous`, and raises before the next request. For a billable reservation, explicit cost other than one is pricing drift: ledger the actual positive `credit_used` when known (otherwise `ambiguous`), then terminate before another call. An explicit zero-used response may become `confirmed_zero`; an explicit positive use becomes `confirmed_used`. Add literal tests for 2xx missing cost, malformed used, mismatched remaining, `credit_cost=2`, and used credits that would push the replayed total above ten; all must preserve evidence and refuse the next request.

- [ ] **Step 5: Run focused tests and the client regression suite**

```bash
.venv/bin/python -m pytest -q tests/test_client.py tests/test_budget.py
.venv/bin/python -m pytest -q tests/test_cli.py
```

Expected: all pass and legacy cache behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/nansen_signal_lab/client.py src/nansen_signal_lab/budget.py tests/test_client.py tests/test_budget.py
git commit -m "Guard prospective Nansen evidence budget"
```

---

### Task 3: Implement schema-v4 lifecycle and append-only stage seals

**Files:**
- Create: `src/nansen_signal_lab/prospective_schema.py`
- Create: `tests/test_prospective_schema.py`

**Interfaces:**
- Consumes: `canonical_json_bytes`, `write_json_once`, `atomic_replace_bytes`
- Produces: `ProspectiveError`
- Produces: `ProspectiveBundle(root: Path, manifest_path: Path, manifest: dict[str, Any])`
- Produces: `load_prospective_manifest(path: str | Path) -> ProspectiveBundle`
- Produces: `commit_stage(bundle: ProspectiveBundle, stage: str, recorded_at: str, artifacts: tuple[Path, ...], budget_snapshot: Path) -> ProspectiveBundle`
- Produces: `recover_stage_transaction(bundle: ProspectiveBundle) -> ProspectiveBundle`
- Produces: `verify_hash_chain(bundle: ProspectiveBundle) -> None`

- [ ] **Step 1: Write the strict manifest and lifecycle tests**

Build a minimal temporary schema-v4 manifest. Parameterize `test_schema_v4_accepts_only_next_lifecycle_transition` over `preregistered -> snapshot_collected`, `snapshot_collected -> decision_sealed`, `decision_sealed -> entry_observed`, and `entry_observed -> settled`. For each pair, build the source-stage bundle, write the next-stage artifacts and immutable budget snapshot, commit the stage transaction, reload it, and assert the new stage and complete hash chain.

Also test `decision_sealed -> unscorable`, direct stage skips, stage reversal, unknown keys, path escapes, symlink escapes, a non-sibling schema-v3 source, source hash drift, seal collision, prior-seal hash drift, a manifest claiming a stage whose seal is absent, seal time earlier than the prior seal, seal time earlier than any referenced request/retrieval/durable-write timestamp, and a provider timestamp later than local durable-write time. Inject a crash (1) after transaction marker, (2) after seal install, and (3) after manifest replace; recovery must either complete the exact recorded transition idempotently or recognize it as complete. A changed prior manifest, seal, artifact, budget snapshot, or proposed manifest under the same marker must terminate as corruption without inventing a new seal.

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_schema.py
```

- [ ] **Step 3: Implement strict schema-v4 loading**

Require exact top-level keys for identity, hypothesis, stage, source strategy manifest/hash, preregistration/hash, design path/hash, pinned Nansen contract path/hash, `max_nansen_calls`, `max_nansen_credits`, budget root, ordered seals, and artifact registry. Require both maxima to equal ten. Lexically normalize before symlink resolution; require the source strategy manifest to be the committed direct sibling `2026-08-17-paper-strategy-feasibility/manifest.json`; require the design and Nansen contract paths to resolve without symlinks to the repository files `docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-design.md` and `docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json`; verify all three SHA-256 values; and call `load_evaluation_manifest` to reuse schema-v3 validation.

Use this transition map:

```python
NEXT_STAGE = {
    "preregistered": {"snapshot_collected", "unscorable"},
    "snapshot_collected": {"decision_sealed", "unscorable"},
    "decision_sealed": {"entry_observed", "unscorable"},
    "entry_observed": {"settled", "unscorable"},
    "settled": set(),
    "unscorable": set(),
}
```

- [ ] **Step 4: Implement append-only seals and hash-chain verification**

Each seal must contain `schema_version`, `experiment_id`, `stage`, `recorded_at`, `previous_seal_sha256`, the immutable budget-snapshot path/hash and its journal-head hash, and a sorted list of `{path, sha256}`. Every stage-owned JSON sidecar/derived artifact must carry its relevant request, retrieval, provider-created, and `artifact_written_at` fields. Reject any internal reversal and require the seal `recorded_at` to be greater than or equal to all referenced times and the prior seal time.

`commit_stage` holds the experiment lock and deterministically computes both seal and next manifest. It atomically writes `.transactions/stage.json` first with the exact prior-manifest hash, proposed stage, seal path/bytes hash, artifact paths/hashes, budget-snapshot hash, and proposed-manifest hash; installs the seal write-once; atomically replaces only `manifest.json`; then removes the recovery marker. `recover_stage_transaction` accepts only four exact states: marker plus unchanged prior manifest and no seal (finish both writes), marker plus exact orphan seal and unchanged prior manifest (adopt it), marker plus exact seal and exact proposed manifest (clear marker), or no marker with a self-consistent manifest. Every other combination is corruption. A seal path is never repurposed, and `commit_stage` refuses an unrecorded existing seal even when proposed bytes match.

- [ ] **Step 5: Run focused tests plus all older schema tests**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_schema.py tests/test_experiment.py tests/test_evaluation.py
```

- [ ] **Step 6: Commit**

```bash
git add src/nansen_signal_lab/prospective_schema.py tests/test_prospective_schema.py
git commit -m "Add prospective experiment lifecycle"
```

---

### Task 4: Build deterministic blind selection and the point-in-time snapshot

**Files:**
- Create: `src/nansen_signal_lab/prospective_snapshot.py`
- Create: `tests/test_prospective_snapshot.py`

**Interfaces:**
- Consumes: `prepare_flow_rows`, `build_signal_features`, `SUPPORTED_FEATURE_SET`
- Produces: `Candidate(chain: str, token_address: str, token_symbol: str, liquidity_usd: float, row: dict[str, Any])`
- Produces: `screener_payload() -> dict[str, Any]`
- Produces: `prior_token_identities(experiments_root: Path) -> frozenset[tuple[str, str]]`
- Produces: `select_candidate(body: dict[str, Any], excluded: frozenset[tuple[str, str]]) -> Candidate`
- Produces: `freeze_selection(candidate: Candidate, *, screener_response_sha256: str, screener_retrieved_at: str) -> dict[str, Any]`
- Produces: `predecision_requests(candidate: Candidate, available_at: datetime) -> tuple[tuple[str, str, dict[str, Any]], ...]`
- Produces: `normalize_snapshot(selection: dict[str, Any], token_information: dict[str, Any], flow_intelligence: dict[str, Any], smart_money_flows: dict[str, Any], exchange_flows: dict[str, Any], *, available_at: datetime) -> dict[str, Any]`
- Produces: `blind_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: Write literal payload, ranking, gap, and redaction tests**

The screener test must require this exact payload:

```python
{
    "chains": ["solana", "ethereum", "base", "bnb", "arbitrum"],
    "timeframe": "24h",
    "pagination": {"page": 1, "per_page": 1000},
    "filters": {
        "trader_type": "sm",
        "include_stablecoins": False,
        "token_age_days": {"min": 3},
        "market_cap_usd": {"min": 1_000_000},
        "liquidity": {"min": 250_000},
    },
    "order_by": [{"field": "netflow", "direction": "DESC"}],
}
```

Test that local sorting is `(-netflow, chain, normalized_address)`, EVM addresses compare case-insensitively, Solana addresses remain case-sensitive, prior tokens are excluded, non-positive/non-finite metrics are rejected, and empty eligibility raises without relaxing filters. Add literal selection-freeze cases: screener liquidity `$250,000` produces notional `$250`, liquidity `$2,000,000` caps at `$1,000`, and the artifact binds the exact selected-row hash, screener response hash, retrieval time, formula, chain, and address. Assert token-information liquidity cannot override that value.

For `candidate = Candidate("solana", "So111", ...)` and `available_at=2026-08-17T10:00:00Z`, assert `predecision_requests` returns these exact method/path/body triples and no extra key:

```python
(
    ("POST", "tgm/token-information", {
        "chain": "solana", "token_address": "So111", "timeframe": "1d",
    }),
    ("POST", "tgm/flow-intelligence", {
        "chain": "solana", "token_address": "So111", "timeframe": "1d",
    }),
    ("POST", "tgm/flows", {
        "chain": "solana", "token_address": "So111",
        "date": {"from": "2026-08-16T09:00:00Z", "to": "2026-08-17T10:00:00Z"},
        "label": "smart_money",
        "pagination": {"page": 1, "per_page": 1000},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }),
    ("POST", "tgm/flows", {
        "chain": "solana", "token_address": "So111",
        "date": {"from": "2026-08-16T09:00:00Z", "to": "2026-08-17T10:00:00Z"},
        "label": "exchange",
        "pagination": {"page": 1, "per_page": 1000},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }),
)
```

The triples use relative endpoint IDs because `NansenClient.BASE_URL` already ends in `/api/v1`; a transport integration test must assert they produce the exact final HTTP paths `/api/v1/tgm/token-information`, `/api/v1/tgm/flow-intelligence`, and `/api/v1/tgm/flows` without duplicating the prefix. Pin the screener adapter as `POST`, relative endpoint `token-screener`, exact body above, final path `/api/v1/token-screener`; also pin the account adapter as `GET`, relative endpoint `account`, no request body, final path `/api/v1/account`. These literals must match the repository OpenAPI-contract extract.

Create Smart-Money and exchange flow fixtures with a live incomplete row, a missing `is_complete`, a future `bucket_end`, a non-final first page, and an intermediate hourly gap. Require both label responses to declare `is_last_page=true`; require every admitted row to contain the literal boolean `is_complete=true` and `bucket_end <= available_at`; reject rather than silently default or drop a missing completeness flag. Require exact 1h/4h/12h Smart-Money features to become unavailable rather than bridge the gap. Recursively assert the blinded snapshot contains none of the literal name, symbol, address, URL, social values, `forward_`, `mfe`, `mae`, `selection_status`, or prior return fields.

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_snapshot.py
```

- [ ] **Step 3: Implement candidate selection and request construction**

Require screener pagination metadata but claim only page-local top selection. Build exactly the method/path/body triples above: token-information `1d`, flow-intelligence `1d`, and two 25-hour `tgm/flows` requests with page 1, `per_page: 1000`, ascending date order, and labels `smart_money`/`exchange`. Format range timestamps in canonical UTC `Z` form and apply half-open cutoff locally even if the provider treats `date.to` inclusively.

- [ ] **Step 4: Implement strict normalization and blinding**

Set `available_at` to the screener response's recorded retrieval time and use it for all four pre-decision request ranges and normalization. Freeze `derived/selection.json` once before candidate-specific calls: use only the screener row's `liquidity` field, compute `min(1000, 0.001 * liquidity)` with finite decimal arithmetic, and bind the canonical selected-row hash plus exact raw screener response hash/retrieval time. Before calling existing helpers, validate both flow responses as final first pages and validate every Smart-Money and exchange row has explicit boolean `is_complete=true`, a valid RFC 3339 `bucket_end`, and `bucket_end <= available_at`; reject the snapshot on any violation because `prepare_flow_rows` treats a missing completeness key as true. For Smart-Money rows, call `prepare_flow_rows`, map rows to `build_signal_features(..., horizons=(1, 4, 12), feature_set_version=SUPPORTED_FEATURE_SET)`, and retain the final available feature plus the exact prior-hour feature needed by lagged predicates. Keep the validated exchange rows and flow-intelligence/token-information fields in separate named sections with freshness and warning metadata. Construct the blinded snapshot from a whitelist and include the frozen formula/notional without identity or the selection-row hash. Do not assign decision time `t0` until both GPT passes and deterministic comparator outputs have been durably written.

`blind_snapshot` must construct a fresh whitelist object rather than delete keys from a copy. Set identity to `candidate-1` and retain only chain.

- [ ] **Step 5: Run signal and snapshot tests**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_snapshot.py tests/test_signals.py tests/test_experiment.py
```

- [ ] **Step 6: Commit**

```bash
git add src/nansen_signal_lab/prospective_snapshot.py tests/test_prospective_snapshot.py
git commit -m "Build blind prospective signal snapshots"
```

---

### Task 5: Implement the no-tool OpenAI adapter and two-pass GPT protocol

**Files:**
- Create: `src/nansen_signal_lab/openai_client.py`
- Create: `src/nansen_signal_lab/gpt_protocol.py`
- Create: `tests/test_openai_client.py`
- Create: `tests/test_gpt_protocol.py`

**Interfaces:**
- Produces: `OpenAIError`, `OpenAIEvidenceResponse`
- Produces: `GPTPassResult(value: dict[str, Any], snapshot_sha256: str, request_path: Path, request_sha256: str, response_path: Path, response_sha256: str, response_id: str, returned_model_id: str)`
- Produces: `OpenAIClient.preflight_model(model_id: str) -> OpenAIEvidenceResponse`
- Produces: `OpenAIClient.create_structured(*, model_id: str, instructions: str, input_json: dict[str, Any], schema_name: str, schema: dict[str, Any]) -> OpenAIEvidenceResponse`
- Produces: `PASS1_SCHEMA`, `PASS2_SCHEMA`
- Produces: `run_pass1(client, snapshot, writer) -> GPTPassResult`
- Produces: `run_pass2(client, snapshot, pass1: GPTPassResult, theory_records, writer) -> GPTPassResult`

- [ ] **Step 1: Write exact HTTP-body and schema-validation tests**

Use `httpx.MockTransport` to require `GET /v1/models/gpt-5.6-sol` and `POST /v1/responses`. Assert the create body contains:

```python
{
    "model": "gpt-5.6-sol",
    "reasoning": {"effort": "high"},
    "input": ANY,
    "text": {"format": {
        "type": "json_schema",
        "name": "prospective_pass_1",
        "strict": True,
        "schema": PASS1_SCHEMA,
    }},
    "max_output_tokens": 4000,
    "store": False,
}
```

Assert there is no `tools`, `previous_response_id`, token identity, or outcome key. Test exact model mismatch, absent/refused output, malformed JSON, unknown output fields, invalid enum/confidence, a cited field not in the snapshot, duplicate evidence references, more than one repair, and Pass 2 mutation of either the Pass 1 response hash or normalized-snapshot hash. Require both passes' request bodies, returned `GPTPassResult`, and write-once `final.json` to bind the same `sha256(normalized/snapshot.json exact bytes)`. Require attempt-indexed immutable paths for the first and repair calls, assert both invalid and repaired exact response bytes survive, and assert `final.json` binds the validated attempt plus snapshot/request/response hashes. Write deliberately non-canonical valid Pass 1 response bytes, assert `GPTPassResult.response_sha256 == sha256(response_path.read_bytes())`, and assert Pass 2 receives that literal hash rather than the hash of a parsed-value reserialization.

Archive the model-access preflight at `model/preflight/attempt-1-{request|response}.json` and include both hashes in the snapshot-stage seal. Before each preflight or inference transmission, install the immutable request artifact containing `attempt`, `transmission_may_begin=true`, exact input hashes, and request start time. Test a crash/timeout after send, a request artifact with no response on restart, HTTP failure, and refusal: all terminate `unscorable` with no automatic reroll or repair. Only an archived, parseable model response that fails local schema/citation validation may receive the single repair attempt.

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_openai_client.py tests/test_gpt_protocol.py
```

- [ ] **Step 3: Implement the raw OpenAI transport**

Use existing `httpx`, `Authorization: Bearer`, one request per method call, exact response bytes, request/retrieval timestamps, response ID/model/usage extraction, and redacted exceptions. Do not add an SDK dependency. `preflight_model` must require the returned `id` to equal `gpt-5.6-sol`. Transport methods do not retry. The protocol writer installs the immutable request artifact before invoking transport and the response artifact immediately on receipt; on restart, request-without-response is an ambiguous prior transmission and must not call the model again.

- [ ] **Step 4: Implement strict Pass 1 and Pass 2 schemas and prompts**

Pass 1 requires `action`, `confidence`, `expected_direction_4h`, bounded unique `evidence_for`, `evidence_against`, `missing_evidence`, `rationale`, and `risk_flags`. Hash the exact sealed normalized-snapshot bytes before constructing either pass. Write every call to `model/pass-1/attempt-{1|2}-{request|response}.json` without overwrite. After local validation, hash the installed files, write `model/pass-1/final.json` once with the validated attempt, snapshot hash, and request/response hashes, and return them with the parsed value as one `GPTPassResult`. Pass 2 requires `snapshot_sha256`, `pass1.response_sha256`, the validated Pass 1 value, `pass1_assessment`, `final_action`, one unique assessment per frozen record, conflicts, evidence references, missing evidence, and rationale; reject a caller-supplied snapshot whose exact bytes differ from Pass 1 and use the same attempt-indexed layout under `model/pass-2/`.

Local validation must independently enforce every enum, bound, list length, uniqueness rule, exact key set, evidence-path existence, six-record coverage, snapshot hash, and Pass 1 response hash. On a parseable output that fails only those local rules, archive the invalid response and make one repair call whose input contains only the invalid response, validation errors, original schema, and original immutable inputs. Network, HTTP, refusal, absent-output, or ambiguous failures never enter repair.

- [ ] **Step 5: Run focused tests**

```bash
.venv/bin/python -m pytest -q tests/test_openai_client.py tests/test_gpt_protocol.py
```

- [ ] **Step 6: Commit**

```bash
git add src/nansen_signal_lab/openai_client.py src/nansen_signal_lab/gpt_protocol.py tests/test_openai_client.py tests/test_gpt_protocol.py
git commit -m "Add sealed two-pass GPT protocol"
```

---

### Task 6: Evaluate the six frozen comparator records without changing them

**Files:**
- Create: `src/nansen_signal_lab/prospective_comparators.py`
- Create: `tests/test_prospective_comparators.py`

**Interfaces:**
- Consumes: `EvaluationBundle`, `Predicate`, `TheorySpec`, `load_evaluation_manifest`, `predicate_matches`
- Produces: `ComparatorDecision(decision_id: str, theory_id: str, role: str, variant: str, action: str | None, availability: str, applicable: bool, veto_theory_id: str | None, veto_triggered: bool | None, reasons: tuple[str, ...])`
- Produces: `load_frozen_records(path: Path, expected_sha256: str) -> tuple[dict[str, Any], ...]`
- Produces: `evaluate_comparators(bundle: EvaluationBundle, current_features: dict[str, Any], prior_features: dict[str, Any] | None, *, available_at: datetime) -> tuple[ComparatorDecision, ...]`
- Produces: `pair_distribution_veto(decisions: tuple[ComparatorDecision, ...]) -> tuple[ComparatorDecision, ...]`

- [ ] **Step 1: Write all-record, lag, unavailable, and veto tests**

Use the committed schema-v3 manifest in one integration test and hand-written literal features in unit tests. Require all six base IDs exactly once as distinct `decision_id={theory_id}::base` records. A missing lag-1 feature, absent/`None` predicate feature, non-finite numeric input, `market_phase_*="unavailable"`, or current timestamp older than the latest completed UTC hourly bucket at `available_at` must produce `availability="UNAVAILABLE"`, no scored action, and `applicable=false`; false predicates with complete fresh inputs produce `ABSTAIN` with `applicable=false`; firing entry/reference/comparison records produce `LONG` with `applicable=true`; the standalone veto produces only `veto_triggered` suppression metadata with `applicable=false`, never `SHORT`. Availability and applicability are separate: the scorer must inspect every comparison-capable entry/reference/comparison base record's availability even when `applicable=false`.

Require one distinct paired variant per firing long comparator with `decision_id={theory_id}::paired::{veto_theory_id}`, `variant="distribution_veto"`, `applicable=true`, and a back-link to the veto theory: if the veto fires, paired action is `ABSTAIN`; if it is clear, paired action is `LONG`; if veto inputs are unavailable, paired availability is `UNAVAILABLE` with no scored action. Assert base and paired IDs are unique. Preserve the blocked buyer-breadth theory separately as `BLOCKED` and do not count it among six evaluable records.

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_comparators.py
```

- [ ] **Step 3: Implement immutable import and snapshot evaluation**

Verify the source file hash before calling `load_evaluation_manifest`. Require the current feature timestamp to equal the latest completed UTC hourly bucket boundary at `available_at`. Resolve `lag_hours=0` from current features and `lag_hours=1` only from an exact one-hour prior feature record. Before calling `predicate_matches`, classify the theory `UNAVAILABLE` if any required row/key/value is absent, stale, non-finite, or carries the literal unavailable sentinel; only complete available inputs may reach the existing operator logic. Call the existing `predicate_matches`; do not copy or reinterpret its operator logic.

- [ ] **Step 4: Run comparator and schema-v3 regressions**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_comparators.py tests/test_evaluation.py
```

- [ ] **Step 5: Commit**

```bash
git add src/nansen_signal_lab/prospective_comparators.py tests/test_prospective_comparators.py
git commit -m "Evaluate frozen prospective comparators"
```

---

### Task 7: Build observed fills, OHLCV validation, scoring, and verdicts

**Files:**
- Create: `src/nansen_signal_lab/prospective_execution.py`
- Create: `tests/test_prospective_execution.py`

**Interfaces:**
- Consumes: `Candidate`, GPT actions, and `ComparatorDecision`
- Produces: `dex_trade_payload(candidate, action, start, end, page) -> dict[str, Any]`
- Produces: `ohlcv_payload(candidate, start, end) -> dict[str, Any]`
- Produces: `ObservedFill(side: str, notional_usd: float, token_amount: float, observed_usd: float, vwap_usd: float, trade_count: int)`
- Produces: `build_entry_fill(pages, virtual_notional_usd) -> ObservedFill | None`
- Produces: `build_exit_fill(pages, entry_token_amount) -> ObservedFill | None`
- Produces: `validate_closed_ohlcv(body, *, required_start, required_exit, retrieved_at) -> tuple[dict[str, Any], ...]`
- Produces: `score_decisions(*, pass1_action: str, pass2_action: str, comparator_decisions: tuple[ComparatorDecision, ...], entry_fill: ObservedFill | None, exit_fill: ObservedFill | None, ohlcv: tuple[dict[str, Any], ...], virtual_notional_usd: float) -> dict[str, Any]`

- [ ] **Step 1: Write literal fill arithmetic and terminal-state tests**

Use three trades where the last is fractionally consumed:

```python
trades = [
    {"block_timestamp": "2026-08-17T10:05:01Z", "transaction_hash": "a", "action": "BUY", "token_amount": 2.0, "estimated_swap_price_usd": 100.0, "estimated_value_usd": 200.0},
    {"block_timestamp": "2026-08-17T10:05:02Z", "transaction_hash": "b", "action": "BUY", "token_amount": 3.0, "estimated_swap_price_usd": 110.0, "estimated_value_usd": 330.0},
    {"block_timestamp": "2026-08-17T10:05:03Z", "transaction_hash": "c", "action": "BUY", "token_amount": 10.0, "estimated_swap_price_usd": 120.0, "estimated_value_usd": 1200.0},
]
```

For a `$1,000` target, assert full use of the first two trades and fractional use of `$470` from the third, with exact token amount and VWAP. Add tests for duplicate `(timestamp, tx_hash)`, wrong side, out-of-window rows, zero/negative/non-finite amount, price, or estimated value, insufficient volume, page 2 incomplete, OHLCV `truncated=true`, an open final candle, duplicate intervals, a missing interior candle, a missing entry/exit candle, and DEX/OHLCV divergence reporting. Require `token_amount * estimated_swap_price_usd` to match `estimated_value_usd` within `max(0.01, 0.01 * estimated_value_usd)`; test equality at the boundary and terminal rejection just beyond it rather than row dropping.

For `candidate=(solana, So111)`, an entry `[10:05Z,10:10Z)`, exit `[14:05Z,14:10Z)`, and page 1, require exact `POST /api/v1/tgm/dex-trades` bodies:

```python
{
    "chain": "solana", "token_address": "So111", "only_smart_money": False,
    "date": {"from": "2026-08-17T10:05:00Z", "to": "2026-08-17T10:10:00Z"},
    "pagination": {"page": 1, "per_page": 1000},
    "filters": {"action": "BUY"},
    "order_by": [
        {"field": "block_timestamp", "direction": "ASC"},
        {"field": "transaction_hash", "direction": "ASC"},
    ],
}
```

The exit literal differs only in its recorded range and `"action": "SELL"`. Require the exact OHLCV triple `POST /api/v1/tgm/token-ohlcv` with body:

```python
{
    "chain": "solana", "token_address": "So111",
    "date": {"from": "2026-08-17T10:00:00Z", "to": "2026-08-17T14:10:00Z"},
    "timeframe": "5m",
}
```

These literals must match the pinned OpenAPI-contract extract. Treat provider `to` as potentially inclusive and reject/exclude any row at the half-open local boundary before fill computation.

Add a non-aligned decision-time case with `t0=2026-08-17T10:02:37Z` and exit-window end `2026-08-17T14:12:37Z`. The required OHLCV grid/request must start at `floor_5m(t0)=10:00:00Z`, end exclusively at the next boundary after the last intersecting interval (`14:15:00Z`), require every interval from `10:00` through `14:10`, and locally exclude a provider candle at `14:15`. The earliest settlement is that exclusive end plus 60 seconds.

Score cases must include distinct Pass 1 and Pass 2 actions, GPT long vs a nonfiring base `ABSTAIN` with `applicable=false` (headline `not_tested` when every comparison-capable base is available and no comparator applies), GPT long vs a veto-suppressed paired `ABSTAIN` with `applicable=true`, GPT abstain vs losing baseline long, a strict tie, an unavailable applicable comparator, and one unavailable non-applicable base while GPT beats another firing base (headline must be `unscorable`, never `true`). Also test unfilled long and the exact formula `exit_observed_usd / virtual_notional_usd - 1`.

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_execution.py
```

- [ ] **Step 3: Implement payloads and stable page validation**

DEX requests use the exact literal method/path/body contract above, including the five-minute range, `only_smart_money=false`, `filters.action`, page size 1000, and ordering `block_timestamp ASC`, then `transaction_hash ASC`. Page 1 may trigger exactly one page-2 request. Validate `(block_timestamp, transaction_hash)` keys are strictly increasing within each raw page and across the page boundary; a non-final page 2 or any ordering/duplicate violation is terminal `UNSCORABLE`. After complete validation, preserve that order. OHLCV uses the exact literal method/path/body contract above with `timeframe: 5m`; compute aligned request bounds as `floor_5m(t0)` and `ceil_5m(exit_window_end)`, treating the latter as exclusive locally.

- [ ] **Step 4: Implement fractional fills and strict scoring**

Sort only after strict completeness, ordering, duplicate, positivity, finiteness, and amount/price/value consistency validation; never silently deduplicate or discard an invalid row. Fractionally scale both USD and token amount on the last trade. For OHLCV, require `truncated is false`, unique ascending `interval_start` values on the exact contiguous five-minute UTC grid from `floor_5m(t0)` through `floor_5m(exit_window_end - epsilon)`, and `interval_start + 5 minutes <= retrieved_at`; require finite positive OHLC prices and finite non-negative volume. Score Pass 1 and Pass 2 separately. `ABSTAIN` scores zero; `UNAVAILABLE`, `UNFILLED`, and `UNSCORABLE` remain status values with no return. Set `gpt_beats_frozen_strategies=true` only for Pass 2 and only when all six design conditions hold: every comparison-capable base is available, every applicable comparator is scorable, strict `>`, and at least one applicable base or paired-veto comparator. Any unavailable comparison-capable base makes the headline `unscorable` even though that record is non-applicable.

- [ ] **Step 5: Run focused tests**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_execution.py
```

- [ ] **Step 6: Commit**

```bash
git add src/nansen_signal_lab/prospective_execution.py tests/test_prospective_execution.py
git commit -m "Score prospective observed paper fills"
```

---

### Task 8: Orchestrate init, start, settle, replay, and check commands

**Files:**
- Create: `src/nansen_signal_lab/prospective_runner.py`
- Modify: `src/nansen_signal_lab/cli.py`
- Create: `tests/test_prospective_runner.py`
- Modify: `tests/test_cli.py`
- Create: `tests/fixtures/prospective-pilot/nansen/*.json`
- Create: `tests/fixtures/prospective-pilot/openai/*.json`

**Interfaces:**
- Consumes: every interface from Tasks 1–7.
- Produces: `initialize_pilot(experiment_root: Path, *, created_at: datetime) -> ProspectiveBundle`
- Produces: `start_pilot(bundle, *, nansen, openai, clock, sleep) -> ProspectiveBundle`
- Produces: `settle_pilot(bundle, *, nansen, clock) -> ProspectiveBundle`
- Produces: `replay_pilot(bundle) -> dict[str, Any]`
- Produces: `check_pilot(bundle) -> tuple[Path, ...]`
- Produces CLI: `pilot-init`, `pilot-start`, `pilot-settle`, `pilot-replay`, `pilot-check`.

- [ ] **Step 1: Write end-to-end fake-adapter tests**

Create deterministic fixture responses for one eligible candidate, four pre-decision evidence calls, two valid GPT passes, one-page entry/exit windows, and settled OHLCV. Use a fake clock at fixed UTC times. Assert:

```python
assert billable_calls == [
    "token-screener",
    "tgm/token-information",
    "tgm/flow-intelligence",
    "tgm/flows:smart_money",
    "tgm/flows:exchange",
    "tgm/dex-trades:BUY:1",
    "tgm/dex-trades:SELL:1",
    "tgm/token-ohlcv",
]
assert final.manifest["stage"] == "settled"
```

First assert an unauthenticated `GET https://api.nansen.ai/openapi.json` returns exact bytes whose SHA-256 equals the preregistered full-source hash, and archive those bytes write-once at `raw/contracts/nansen-openapi.json` before any credentialed external call; mismatch prevents all Nansen paid calls and all GPT inference, while a network failure returns safely with the manifest still preregistered so the free check may be invoked later. Then assert the archived model preflight happens before the Nansen account preflight. Require the exact account adapter call `GET`, relative endpoint `account`, no body, and captured HTTP path `/api/v1/account`; require body string `plan` to case-fold to `free` or `pro`, integer `credits_remaining >= 10`, matching header remaining, and both credit cost and use headers zero. The pinned official OpenAPI contract records all selected endpoints as one credit on either plan. A positive or ambiguous transmitted account result archives evidence, renders the terminal report, and seals `preregistered -> unscorable`; reinvoking that terminal manifest makes zero HTTP calls. Assert a 2xx paid response with missing/malformed cost/use/remaining headers or any cost other than one archives and stops before the next paid request. Assert start stops after `decision_sealed`, settle refuses an early clock, repeated start/settle calls do not spend, a stage collision stops, an ambiguous Nansen or OpenAI request is not retried, an explicit zero-credit 429 gets at most one persisted same-reservation retry only when `Retry-After` is an integer from 0 through 60 seconds, a crash/reinvoke at `retryable_zero` resumes no more than that one attempt, and fixture replay makes zero HTTP calls. Require both 429 and retry artifacts at `raw/nansen/{reservation_id}/attempt-{1|2}-{request|response}.json`, with both hashes sealed and no overwrite.

Inject crashes after each of: Nansen request artifact, received response artifact, budget transition journal, budget head replace, deterministic derived-artifact install, OpenAI request install, OpenAI response install, GPT final pointer, stage marker, stage seal, terminal report install, and manifest replace. On restart, exact archived responses and exact deterministic outputs are adopted, already-ledgered logical requests are reused, and the exact recorded stage transition completes without a second HTTP call. Reserved-without-response becomes terminal ambiguous; confirmed-without-response is evidence loss; a different request hash or artifact bytes is quarantined and terminal. Inject backward clocks before snapshot, model completion, comparator write, decision `t0`, entry seal, and outcome seal; every case must fail closed without a backdated seal or later external call. Assert every stage seal validates after later budget transitions, and replay reconstructs identical total calls/credits from the immutable journal.

Add a no-fill case with Pass 1 `ABSTAIN`, Pass 2 `ABSTAIN`, one base `UNAVAILABLE`, and all other decisions `ABSTAIN`: because no action is `LONG`, it must write `fill_required=false`, make zero DEX calls, still collect the single OHLCV outcome, and report the unavailable status without converting it to zero.

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_runner.py tests/test_cli.py
```

- [ ] **Step 3: Implement dependency-injected orchestration**

`start_pilot` must:

1. load and verify preregistration/design/source/contract hashes, recover an exact pending stage transaction, replay the budget journal, and reconcile each stable request attempt by its canonical request SHA; then fetch the public live OpenAPI without credentials, archive its exact bytes, and require its SHA-256 to equal the preregistered full-source hash before any credentialed or billable call. A fetch failure leaves `preregistered` unchanged; a received hash mismatch renders `REPORT.md` and seals terminal `unscorable` without GPT or paid Nansen access;
2. require `OPENAI_API_KEY` and `NANSEN_API_KEY` without printing either;
3. install and archive the exact model-access preflight request before transmission and its exact response on receipt; a preflight request without a response on restart is ambiguous and terminal. Then reserve one conservative call/credit for pricing drift and call `request_evidence("GET", "account", None, ...)` once, which must produce final HTTP path `/api/v1/account`; archive the response, confirm the reservation, and require the body `plan` string to case-fold into `{"free", "pro"}`, integer body `credits_remaining >= 10`, an equal `X-Nansen-Credits-Remaining`, `X-Nansen-Credits-Cost: 0`, and `X-Nansen-Credits-Used: 0`; the preregistration binds `docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json`, whose official OpenAPI extract records every selected endpoint as one credit on both plans; the explicit zero confirmation releases the conservative reservation, while any nonzero or ambiguous transmitted result remains ledgered, writes `REPORT.md`, commits an immutable budget snapshot in the `unscorable` seal directly from `preregistered`, and makes every later invocation side-effect-free;
4. execute every Nansen attempt through `BudgetGuard` using a stable logical ID and canonical method/relative-endpoint/payload SHA, and archive it before ledger settlement at `raw/nansen/{reservation_id}/attempt-{attempt_count}-{request|response}.json`; use only relative endpoint IDs so the client emits one `/api/v1` prefix. Execute screener endpoint `token-screener`, terminate `unscorable` if selection is empty, then execute the four candidate-specific calls. Recover artifact-before-ledger crashes from the exact stable paths without retransmission;
5. precompute canonical identity-bearing `derived/selection.json` and the complete identity-blinded `normalized/snapshot.json`, install or exactly adopt each deterministic output, write the immutable `snapshot_collected` budget snapshot, and commit the stage transaction; every later external call/replay loads the candidate and frozen notional only from the hash-verified selection artifact, never from GPT input;
6. evaluate and install-or-adopt deterministic comparator outputs, then run/archive Pass 1 and Pass 2 using the exact sealed snapshot SHA for both. Each OpenAI attempt installs immutable request state before transmission; exact completed request/response/final artifacts may be adopted on restart, but a request without response is ambiguous and no model call is rerolled;
7. set `t0` only after both model artifacts and comparator decisions are durably written; require `t0` to be no earlier than every request, retrieval, provider-created, artifact-write, and prior-seal timestamp, then write the immutable `decision_sealed` budget snapshot and commit the decision stage with `t0`, entry window, exit window, selection-artifact hash, snapshot hash, frozen notional, and earliest settlement time equal to the first UTC five-minute boundary after the exit-window end plus a 60-second closed-candle safety lag;
8. return without sleeping or collecting outcome evidence.

`settle_pilot` must recover first, enforce the recorded deadline, reload and hash-verify `derived/selection.json`, and reconstruct `Candidate` plus virtual notional from that sealed identity-bearing record. If no Pass 1, Pass 2, base-comparator, or paired-veto action equals `LONG`, write `fill_required=false` with the exact action/status set, make no entry or exit DEX call, snapshot the budget, and commit `entry_observed`; this explicitly covers mixed `ABSTAIN`/`UNAVAILABLE`. Otherwise collect entry pages and commit `entry_observed` with its immutable budget snapshot; collect exit pages only when the entry fill completed. A malformed/incomplete/contradictory DEX page is already terminal: archive and ledger it, render `REPORT.md`, and seal `unscorable` immediately without spending on OHLCV. On every still-scorable, complete-but-`UNFILLED`, or no-LONG descriptive path, collect the single OHLCV response, score, precompute and install-or-adopt all terminal derived artifacts plus write-once `REPORT.md`, create the terminal budget snapshot, and only then commit `settled` or terminal `unscorable` so the report hash is in the outcome seal. Add a page-2-incomplete regression that asserts zero OHLCV requests. Run `check_pilot` after the terminal commit. Every earlier stage seal must remain valid after these later journal transitions.

- [ ] **Step 4: Implement thin CLI commands**

Required command shapes:

```bash
./nansen-lab pilot-init --experiment-dir research/experiments/2026-08-17-gpt-prospective-pilot
./nansen-lab pilot-start --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json --max-nansen-calls 10 --max-nansen-credits 10
./nansen-lab pilot-settle --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
./nansen-lab pilot-replay --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
```

`pilot-start` must print the hard ceiling and preflight result, then proceed without an interactive approval prompt. No `--force`, model override, token override, threshold override, or budget-increase flag is allowed.

At CLI dispatch, define an explicit offline set containing `evaluate`, `pilot-init`, `pilot-replay`, and `pilot-check`. Those commands must not call `load_dotenv`, read either credential, construct an HTTP client, or touch the network. Add CLI tests that replace `load_dotenv`, client constructors, and network access with functions that raise; all three offline pilot commands must still pass. `pilot-start` and `pilot-settle` remain credentialed commands.

- [ ] **Step 5: Run dry-run and CLI tests**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_runner.py tests/test_cli.py
./nansen-lab pilot-replay --manifest tests/fixtures/prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest tests/fixtures/prospective-pilot/manifest.json
```

Expected: fixture lifecycle settles, replay/check pass, and no credential or network access occurs.

- [ ] **Step 6: Commit**

```bash
git add src/nansen_signal_lab/prospective_runner.py src/nansen_signal_lab/cli.py tests/test_prospective_runner.py tests/test_cli.py tests/fixtures/prospective-pilot
git commit -m "Orchestrate prospective GPT pilot"
```

---

### Task 9: Create the preregistered bundle, documentation, and independent verification gate

**Files:**
- Create: `research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json`
- Create: `research/experiments/2026-08-17-gpt-prospective-pilot/preregistration.json`
- Create: `research/experiments/2026-08-17-gpt-prospective-pilot/PREREGISTRATION.md`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/RESEARCH-LEDGER.md`
- Modify: `docs/RESEARCH-GRAPH.md`
- Modify: `tests/test_prospective_schema.py`

**Interfaces:**
- Consumes: `pilot-init`, `pilot-replay`, and `pilot-check`.
- Produces: the real, committed `preregistered` schema-v4 bundle with exact source hash, design hash, pinned Nansen OpenAPI-contract-extract hash, model contract, selection thresholds, call ledger ceilings, stage deadlines formula, scoring rule, and no outcome.

- [ ] **Step 1: Add a committed-bundle regression before creating the bundle**

```python
def test_committed_prospective_preregistration_is_valid_and_has_no_outcome():
    bundle = load_prospective_manifest(
        "research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json"
    )
    assert bundle.manifest["stage"] == "preregistered"
    assert bundle.manifest["max_nansen_calls"] == 10
    assert bundle.manifest["max_nansen_credits"] == 10
    assert bundle.manifest["nansen_contract_sha256"] == sha256_file(
        "docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json"
    )
    assert not (bundle.root / "seals/outcome.json").exists()
    assert not (bundle.root / "REPORT.md").exists()
```

- [ ] **Step 2: Run to verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_prospective_schema.py::test_committed_prospective_preregistration_is_valid_and_has_no_outcome
```

- [ ] **Step 3: Initialize the real bundle offline**

```bash
./nansen-lab pilot-init --experiment-dir research/experiments/2026-08-17-gpt-prospective-pilot
```

The immutable `PREREGISTRATION.md` must say `Status: preregistered; no paid call or GPT inference has run`, state the strict win rule and one-token limitation, and link the design. `REPORT.md` must not exist before a terminal outcome; this prevents an initial sealed report from being rewritten later. The preregistration must contain no empty result field that could be mistaken for zero.

- [ ] **Step 4: Append the preregistration to durable research documentation**

Add one dated ledger entry and stable graph node. Document schema-v4 lifecycle, budget ledger, identity blinding, two-pass protocol, common observed fills, and `unscorable` behavior. Do not rewrite prior conclusions.

- [ ] **Step 5: Run complete verification before any paid call**

```bash
.venv/bin/python -m pytest -q
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
./nansen-lab pilot-replay --manifest tests/fixtures/prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest tests/fixtures/prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
git diff --check
```

Expected: every command exits 0; the real bundle remains `preregistered`; old frozen hashes match.

- [ ] **Step 6: Commit the preregistered bundle and docs**

```bash
git add research/experiments/2026-08-17-gpt-prospective-pilot README.md docs/ARCHITECTURE.md docs/RESEARCH-LEDGER.md docs/RESEARCH-GRAPH.md tests/test_prospective_schema.py
git commit -m "Preregister prospective GPT pilot"
```

- [ ] **Step 7: Run an independent whole-branch review before spending**

Give a fresh reviewer the design, plan, base commit `7906b53`, final implementation commit, and these independent questions:

- Can any outcome, identity, prior result, or model tool leak into either GPT pass?
- Can any code path exceed ten calls/credits or retry an ambiguous transmission?
- Can incomplete pages/buckets/candles or unavailable predicates become false/zero?
- Are raw bytes, headers, hashes, lifecycle transitions, and collision behavior durable?
- Are the six frozen records imported unchanged and veto semantics non-short?
- Can any path create an order, wallet action, venue submission, or capital movement?

Resolve every Critical or Important issue with a new failing regression, minimal fix, focused rerun, and one scoped re-review. Re-run the full verification block after the final review fix.

---

### Task 10: Run, settle, save, and verify the real pilot

**Files:**
- Modify only stage-owned files under: `research/experiments/2026-08-17-gpt-prospective-pilot/`
- Modify after settlement: `docs/RESEARCH-LEDGER.md`
- Modify after settlement: `docs/RESEARCH-GRAPH.md`
- Modify after settlement if needed: `README.md`
- Create after an adverse final audit if needed: `docs/audits/2026-08-17-gpt-prospective-pilot-erratum.md`

**Interfaces:**
- Consumes: reviewed `pilot-start`, `pilot-settle`, `pilot-replay`, and `pilot-check` commands.
- Produces: exact raw evidence, snapshot, two GPT passes, comparator decisions, stage seals, observed fills, comparison, and final `REPORT.md` committed locally.

- [ ] **Step 1: Establish the live rollback and cleanliness checkpoint**

Record `git rev-parse HEAD`, create a local rollback branch at that commit, verify only `handoff.md` is untracked, and run `git diff --check`. Do not delete any worktree or branch.

- [ ] **Step 2: Start and seal the live decision**

```bash
./nansen-lab pilot-start --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json --max-nansen-calls 10 --max-nansen-credits 10
```

Expected: exact model and account preflights pass; no more than five Nansen credits are confirmed; stage is `decision_sealed`; the command prints entry and settlement timestamps without secrets or token identity from the blinded prompt.

- [ ] **Step 3: Verify and commit the sealed decision before the outcome**

```bash
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
git diff --check
git add research/experiments/2026-08-17-gpt-prospective-pilot
git commit -m "Seal prospective GPT decision"
```

Inspect the staged files before committing. Confirm the prompt contains `candidate-1` and contains none of the raw token identity or forbidden outcome keys.

- [ ] **Step 4: Settle only after the recorded deadline**

Do not block a shell with a multi-hour sleep. When the deadline has passed, run:

```bash
./nansen-lab pilot-settle --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
```

Expected: at most five remaining billable calls; stage becomes `settled` or terminal `unscorable`; every null/adverse outcome remains explicit; write-once `REPORT.md` already exists and its hash is part of the terminal seal.

- [ ] **Step 5: Reproduce and validate the already sealed final findings offline**

```bash
./nansen-lab pilot-replay --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
.venv/bin/python -m pytest -q
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
git diff --check
```

Expected: fresh deterministic pass evidence, an unchanged sealed `REPORT.md` hash, and unchanged older bundles.

- [ ] **Step 6: Independently audit the immutable result before documenting it**

Have a fresh reviewer independently recompute the selected candidate, feature snapshot, all six predicate decisions, observed fills, net returns, and strict headline verdict from raw artifacts. The reviewer must also scan the exact to-be-committed files for credentials and forbidden live-trading fields. Verify the sealed `REPORT.md` leads with the literal verdict (`true`, `false`, `not_tested`, or `unscorable`) and reports Pass 1, Pass 2, every comparator, cash, fills, call/credit use, completeness, OHLCV divergence, limitations, and the one-observation boundary.

The terminal bundle is immutable. If this audit finds a Critical or Important result-integrity error, preserve it unchanged, create a dated external erratum that hashes the terminal manifest/report and marks the observation unusable, and reflect that invalidation in the research ledger/graph. Add a failing regression and fix the code only for future runs; never rewrite, delete, or silently "correct" the sealed observation.

- [ ] **Step 7: Append the audited result to durable research documentation**

Do not rewrite `REPORT.md`. Append the audited result—or the explicit external invalidation—to the ledger/graph without revising frozen thresholds, and update the README pointer only if needed.

- [ ] **Step 8: Commit the settled findings and audit disposition**

```bash
git add research/experiments/2026-08-17-gpt-prospective-pilot docs/RESEARCH-LEDGER.md docs/RESEARCH-GRAPH.md README.md
# Only when the adverse-audit file was created:
git add docs/audits/2026-08-17-gpt-prospective-pilot-erratum.md
git commit -m "Record prospective GPT pilot result"
```

Inspect the staged set first. Omit `README.md` or the erratum path when it does not exist. If the audit found an implementation defect, commit its regression/future-run fix separately from the immutable result and erratum, rerun the full non-live regression suite, and do not claim the sealed observation passed replay under corrected code unless it actually does.

No push or PR update follows automatically.
