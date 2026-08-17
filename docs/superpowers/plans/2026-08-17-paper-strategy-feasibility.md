# Paper Strategy Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline schema-v3 evaluator that compares fixed Smart-Money strategy theories on the frozen discovery bundle and emits an explicitly unvalidated paper-only shortlist.

**Architecture:** Add a focused `evaluation.py` module rather than expanding the already large experiment loader. A strict schema-v3 manifest binds to the schema-v2 signal manifest and rebuilds its validated schema-v1 lineage in memory; pure functions evaluate whitelisted predicates, conservative next-hour outcomes, costs, cooldown, chronological blocks, and paper-feasibility gates. The CLI only dispatches the offline evaluator, and a committed discovery bundle freezes its outputs and conclusions.

**Tech Stack:** Python 3.12 standard library, existing `pytest` 8.x suite, existing deterministic CSV/JSON research-bundle conventions.

## Global Constraints

- Preserve every existing schema-v1 and schema-v2 raw, manifest, and derived byte exactly.
- Do not make a live/current/paid Nansen call, construct `NansenClient`, access `.env`, or add a dependency.
- Source `point_in_time_guarantee=unknown` remains discovery-only and may produce only `selected_for_paper_discovery`, never validated or capital-trading vocabulary.
- Predicate inputs are restricted to trailing fields from `signal_fieldnames()`; label, selection, cohort-role, forward-return, MFE, and MAE fields are outcome-only or forbidden.
- Use the exact next-hour price as the frozen executable proxy; require an exact mature fixed exit and one open episode per token.
- Use 100 basis points per side for base cost and 250 basis points per side for stress cost.
- No paper output may contain a current token order, account, wallet, venue, credential, submission, or live-trade instruction.
- All production changes follow strict RED -> GREEN -> REFACTOR; every task ends with focused and neighboring verification.

---

### Task 1: Strict schema-v3 evaluation loader

**Files:**
- Create: `src/nansen_signal_lab/evaluation.py`
- Create: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: `load_signal_manifest()`, `SignalBundle`, `sha256_file()`, and `signal_fieldnames()` from the existing source modules.
- Produces: `EvaluationError`, `Predicate`, `TheorySpec`, `TimeBlock`, `CostScenario`, `ComparisonSpec`, `EvaluationBundle`, and `load_evaluation_manifest(path)`.

- [ ] **Step 1: Write the failing strict-loader tests**

Create `tests/test_evaluation.py` with a helper that copies the two frozen experiment directories under a temporary `experiments/` root and writes a schema-v3 sibling manifest. The minimal valid manifest must include all keys from the design and use the real copied schema-v2 SHA:

```python
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.nansen_signal_lab.evaluation import EvaluationError, load_evaluation_manifest

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "research/experiments/2026-08-16-seven-token-pilot"
SHADOW = ROOT / "research/experiments/2026-08-16-community-signal-shadow"


def _manifest(root: Path) -> dict:
    shadow = root / "2026-08-16-community-signal-shadow/manifest.json"
    return {
        "schema_version": 3,
        "experiment_id": "fixture-evaluation",
        "title": "fixture",
        "status": "discovery",
        "created_at": "2026-08-17T00:00:00Z",
        "hypothesis": "fixed theories",
        "source_signal_manifest": "../2026-08-16-community-signal-shadow/manifest.json",
        "source_signal_manifest_sha256": hashlib.sha256(shadow.read_bytes()).hexdigest(),
        "evaluation_window": {
            "from": "2026-08-13T11:00:00Z",
            "to": "2026-08-16T10:00:00Z",
        },
        "time_blocks": [
            {"id": "b1", "from": "2026-08-13T11:00:00Z", "to": "2026-08-14T11:00:00Z"},
            {"id": "b2", "from": "2026-08-14T11:00:00Z", "to": "2026-08-16T10:00:00Z"},
        ],
        "execution": {
            "proxy_version": "next-hour-fixed-exit-v1",
            "entry_lag_hours": 1,
            "non_overlap": "one_open_episode_per_token",
        },
        "cost_scenarios": [
            {"id": "base", "per_side_bps": 100},
            {"id": "stress", "per_side_bps": 250},
        ],
        "theories": [{
            "id": "entry-v1",
            "role": "entry",
            "objective": "positive_return",
            "holding_period_hours": 4,
            "all": [{"feature": "market_phase_4h", "operator": "eq", "value": "markup", "lag_hours": 0}],
        }],
        "comparisons": [],
        "blocked_theories": [],
        "paper_feasibility_gates": {
            "entry_min_events": 5,
            "entry_min_tokens": 3,
            "entry_min_positive_blocks": 2,
            "veto_min_events": 3,
            "veto_min_tokens": 3,
        },
        "prospective_advancement_gates": {
            "min_calendar_weeks": 8,
            "min_fills": 100,
            "min_tokens": 20,
            "min_fill_rate": 0.70,
            "max_token_pnl_contribution": 0.20,
        },
    }


def _bundle(tmp_path: Path) -> tuple[Path, dict]:
    experiments = tmp_path / "experiments"
    shutil.copytree(PILOT, experiments / PILOT.name)
    shutil.copytree(SHADOW, experiments / SHADOW.name)
    root = experiments / "fixture-evaluation"
    root.mkdir()
    manifest = _manifest(experiments)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path, manifest


def test_load_evaluation_manifest_binds_valid_signal_lineage(tmp_path):
    path, _ = _bundle(tmp_path)
    bundle = load_evaluation_manifest(path)
    assert bundle.experiment_id == "fixture-evaluation"
    assert bundle.source_bundle.manifest["schema_version"] == 2
    assert bundle.source_bundle.manifest["point_in_time_guarantee"] == "unknown"


@pytest.mark.parametrize("mutation, message", [
    (lambda m: m.update(schema_version=4), "unsupported evaluation schema"),
    (lambda m: m.update(status="holdout"), "unknown point-in-time source"),
    (lambda m: m.update(source_signal_manifest_sha256="0" * 64), "source signal manifest checksum"),
    (lambda m: m["cost_scenarios"].append({"id": "base", "per_side_bps": 1}), "duplicate cost scenario"),
    (lambda m: m["time_blocks"].append({"id": "overlap", "from": "2026-08-14T00:00:00Z", "to": "2026-08-15T00:00:00Z"}), "time blocks must be ordered and non-overlapping"),
])
def test_load_evaluation_manifest_rejects_invalid_contract(tmp_path, mutation, message):
    path, manifest = _bundle(tmp_path)
    mutation(manifest)
    path.write_text(json.dumps(manifest))
    with pytest.raises(EvaluationError, match=message):
        load_evaluation_manifest(path)
```

Add separate tests for missing/extra top-level keys, a source outside the trusted sibling root, a symlinked source, naive timestamps, a block outside the evaluation window, cost IDs other than exactly `base` and `stress`, non-finite/negative cost values, duplicate theory IDs, invalid roles/objectives/holding periods, and malformed feasibility gates.

- [ ] **Step 2: Run the loader tests and verify RED**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py -q
```

Expected: collection fails because `src.nansen_signal_lab.evaluation` does not exist.

- [ ] **Step 3: Implement the minimal loader and immutable types**

Create `src/nansen_signal_lab/evaluation.py` with these public types and signatures:

```python
class EvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Predicate:
    feature: str
    operator: str
    value: Any
    lag_hours: int


@dataclass(frozen=True)
class TheorySpec:
    id: str
    role: str
    objective: str
    holding_period_hours: int
    predicates: tuple[Predicate, ...]


@dataclass(frozen=True)
class TimeBlock:
    id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CostScenario:
    id: str
    per_side_bps: float


@dataclass(frozen=True)
class ComparisonSpec:
    id: str
    positive_arm: str
    reference_arm: str


@dataclass(frozen=True)
class EvaluationBundle:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    source_bundle: SignalBundle
    theories: tuple[TheorySpec, ...]
    blocks: tuple[TimeBlock, ...]
    costs: tuple[CostScenario, ...]
    comparisons: tuple[ComparisonSpec, ...]

    @property
    def experiment_id(self) -> str:
        return str(self.manifest["experiment_id"])


```

Implement the documented strict-key/type/range checks. Normalize the caller path with `Path(os.path.abspath(os.fspath(manifest_path)))` before resolving symlinks. Require the source manifest to be a real file in one direct sibling directory, verify SHA-256 before calling `load_signal_manifest()`, and reject `status=holdout` when the source guarantee is not `provider_pit` or `live_snapshot`.

Build the allowed predicate feature set from `signal_fieldnames(tuple(source_manifest["horizons_hours"]))`, then remove the six identity/provenance fields. Accept only `eq`, `in`, `gt`, `gte`, `lt`, and `lte`; require `lag_hours` to be a non-negative integer; require `in` to receive a non-empty unique scalar list; and reject every non-finite numeric predicate value.

- [ ] **Step 4: Run loader tests and neighboring signal-manifest tests**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py tests/test_experiment.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Inspect and commit Task 1**

Run `git diff --check`, inspect only `evaluation.py` and `test_evaluation.py`, then commit:

```bash
git add src/nansen_signal_lab/evaluation.py tests/test_evaluation.py
git commit -m "Add strict strategy evaluation manifests"
```

### Task 2: Predicate evaluation and conservative executable episodes

**Files:**
- Modify: `src/nansen_signal_lab/evaluation.py`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: Task 1 types plus `build_signal_analysis()` and `build_analysis()`.
- Produces: `entry_objective_pct()`, `veto_objective_pct()`, `predicate_matches()`, and `build_theory_events()`.

- [ ] **Step 1: Write failing pure episode tests**

Add hand-derived tests with two tokens and literal hourly rows. Name the mutation each catches in its docstring. Required cases:

```python
def test_entry_objective_applies_two_multiplicative_sides():
    # 100 -> 110 with 1% paid on entry and exit.
    assert entry_objective_pct(10.0, 100) == pytest.approx(7.811)


def test_veto_objective_subtracts_exit_and_reentry_costs():
    assert veto_objective_pct(-5.0, 100) == pytest.approx(3.0)


def test_exact_lag_predicate_rejects_missing_prior_hour():
    predicate = Predicate(
        feature="holdings_acceleration_4h_pct_per_hour",
        operator="lte",
        value=0,
        lag_hours=1,
    )
    assert predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T02:00:00Z"},
        by_timestamp={},
    ) is False


def test_build_theory_events_enters_next_hour_and_exits_after_fixed_horizon():
    # Signal t=01:00, entry t=02:00 at 100, exit t=06:00 at 110.
    events = build_theory_events(
        signal_rows=_literal_signal_rows(),
        source_rows=_literal_source_rows(),
        theories=(_literal_entry_theory(),),
        evaluation_id="e",
        evaluation_start=_utc("2026-08-01T00:00:00Z"),
        evaluation_end=_utc("2026-08-02T00:00:00Z"),
        blocks=(TimeBlock("b", _utc("2026-08-01T00:00:00Z"), _utc("2026-08-02T00:00:00Z")),),
        entry_lag_hours=1,
        costs=(CostScenario("base", 100), CostScenario("stress", 250)),
    )
    assert events == ({
        "evaluation_id": "e",
        "theory_id": "entry-v1",
        "theory_role": "entry",
        "block_id": "b",
        "chain": "base",
        "symbol": "X",
        "token_address": "0xtoken",
        "signal_timestamp": "2026-08-01T01:00:00Z",
        "entry_timestamp": "2026-08-01T02:00:00Z",
        "exit_timestamp": "2026-08-01T06:00:00Z",
        "holding_period_hours": 4,
        "gross_return_pct": 10.0,
        "gross_objective_pct": 10.0,
        "base_objective_pct": pytest.approx(7.811),
        "stress_objective_pct": pytest.approx(4.56875),
    },)
```

Also test `eq`, `in`, every numeric comparator, unavailable values, label-side poison fields, missing entry/exit hours, non-positive prices, signal outside the evaluation window, exact block assignment, censored exit, deterministic ordering, entry versus veto objective, and same-token cooldown. The cooldown test must include signals one hour apart and prove only the first remains while a different token at the same time remains eligible.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py -q
```

Expected: failures identify the missing objective, predicate, and event-building functions.

- [ ] **Step 3: Implement predicates, prices, maturity, and cooldown**

Add:

```python
def entry_objective_pct(gross_return_pct: float, per_side_bps: float) -> float:
    ratio = 1 + gross_return_pct / 100
    cost = per_side_bps / 10_000
    return 100 * (ratio * (1 - cost) * (1 - cost) - 1)


def veto_objective_pct(gross_return_pct: float, per_side_bps: float) -> float:
    return -gross_return_pct - 2 * per_side_bps / 100


def predicate_matches(
    predicate: Predicate,
    *,
    current: dict[str, Any],
    by_timestamp: dict[datetime, dict[str, Any]],
) -> bool:
    timestamp = _parse_utc(current["timestamp"])
    row = by_timestamp.get(timestamp - timedelta(hours=predicate.lag_hours))
    if row is None or row.get(predicate.feature) is None:
        return False
    actual = row[predicate.feature]
    operations = {
        "eq": lambda left, right: left == right,
        "in": lambda left, right: left in right,
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
    }
    return operations[predicate.operator](actual, predicate.value)
```

Implement `build_theory_events(signal_rows, source_rows, theories, *, evaluation_id, evaluation_start, evaluation_end, blocks, entry_lag_hours, costs)` with the exact return type `tuple[dict[str, Any], ...]`. Its core must follow this concrete order:

```python
for theory in sorted(theories, key=lambda item: item.id):
    next_allowed: dict[tuple[str, str], datetime] = {}
    for current in sorted(signal_rows, key=_signal_sort_key):
        timestamp = _parse_utc(current["timestamp"])
        identity = _normalized_identity(current["chain"], current["token_address"])
        if not evaluation_start <= timestamp < evaluation_end:
            continue
        if timestamp < next_allowed.get(identity, datetime.min.replace(tzinfo=timezone.utc)):
            continue
        token_signals = signal_index[identity]
        if not all(
            predicate_matches(predicate, current=current, by_timestamp=token_signals)
            for predicate in theory.predicates
        ):
            continue
        entry_at = timestamp + timedelta(hours=entry_lag_hours)
        exit_at = entry_at + timedelta(hours=theory.holding_period_hours)
        entry = price_index.get((*identity, entry_at))
        exit_price = price_index.get((*identity, exit_at))
        block = next((item for item in blocks if item.start <= timestamp < item.end), None)
        if entry is None or exit_price is None or block is None:
            continue
        gross = 100 * (exit_price / entry - 1)
        objective = -gross if theory.role == "veto" else gross
        events.append(_event_row(theory, current, block, entry_at, exit_at, gross, objective, costs))
        next_allowed[identity] = exit_at
```

Index signal rows per normalized `(chain, token_address)` and exact UTC timestamp. Index source prices per normalized `(chain, address)` and timestamp. Evaluate predicates solely against the signal index. For each theory, sort matches by timestamp then token identity, require exact entry/exit rows and positive finite prices, and apply cooldown independently per `(theory_id, chain, token_address)` until the prior exit timestamp. Emit only evaluable roles; skip manifest-blocked theories.

- [ ] **Step 4: Run focused and signal tests GREEN**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py tests/test_signals.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Inspect and commit Task 2**

Run `git diff --check`, inspect the task diff for any label-side read, then commit:

```bash
git add src/nansen_signal_lab/evaluation.py tests/test_evaluation.py
git commit -m "Evaluate fixed paper strategy episodes"
```

### Task 3: Token-equal summaries, comparisons, and paper-only selection

**Files:**
- Modify: `src/nansen_signal_lab/evaluation.py`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: Task 2 event rows and Task 1 gates/comparisons.
- Produces: `EvaluationTables`, `build_theory_summaries()`, `build_comparison_results()`, `build_paper_selection()`, and `build_evaluation()`.

- [ ] **Step 1: Write failing summary and gate tests**

Use literal event fixtures where token A has many small wins and token B has one loss, proving the headline token-equal mean is the mean of token means rather than the event mean. Add exact tests for:

```python
def test_summary_is_token_equal_not_event_frequency_weighted():
    events = _events_for_token_equal_test()  # A: +2,+2,+2; B: -2
    summary = build_theory_summaries(events, _summary_bundle())[0]
    assert summary["event_mean_base_objective_pct"] == pytest.approx(1.0)
    assert summary["token_equal_mean_base_objective_pct"] == pytest.approx(0.0)


def test_entry_gate_selects_only_best_eligible_theory_with_lexical_tie_break():
    selection = build_paper_selection(_selection_bundle(), _eligible_tied_summaries(), ())
    assert selection["selected_entry_theory_id"] == "entry-a-v1"
    assert selection["selection_status"] == "selected_for_paper_discovery"


def test_no_eligible_entry_does_not_force_a_winner():
    selection = build_paper_selection(_selection_bundle(), _insufficient_summaries(), ())
    assert selection["selected_entry_theory_id"] is None
    assert selection["selection_status"] == "no_paper_strategy_selected"
```

Test global and block rows, zero-event blocks, event median and win rate, positive-contribution concentration, all entry gate reasons, all veto gate reasons, benchmark/comparison exclusions, H3 positive/reference mean and median spreads, blocked theory reason preservation, source guarantee vocabulary, selected theory definitions, exact paper execution policy, exact prospective gates, and absence of keys matching `order`, `wallet`, `account`, `venue`, `credential`, `submit`, or `live_trade`.

- [ ] **Step 2: Run summary tests and verify RED**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py -q
```

Expected: failures name the missing aggregation, comparison, and selection functions.

- [ ] **Step 3: Implement deterministic aggregation and selection**

Add:

```python
@dataclass(frozen=True)
class EvaluationTables:
    events: tuple[dict[str, Any], ...]
    summaries: tuple[dict[str, Any], ...]
    comparisons: tuple[dict[str, Any], ...]
    paper_selection: dict[str, Any]


def build_theory_summaries(
    events: tuple[dict[str, Any], ...],
    bundle: EvaluationBundle,
) -> tuple[dict[str, Any], ...]:
    groups = _declared_summary_groups(events, bundle)
    return tuple(
        _summarize_group(bundle.experiment_id, theory, block_id, rows)
        for theory, block_id, rows in groups
    )


def build_comparison_results(
    summaries: tuple[dict[str, Any], ...],
    comparisons: tuple[ComparisonSpec, ...],
) -> tuple[dict[str, Any], ...]:
    overall = {(row["theory_id"], row["block_id"]): row for row in summaries}
    return tuple(
        _comparison_row(
            comparison,
            overall[(comparison.positive_arm, "all")],
            overall[(comparison.reference_arm, "all")],
        )
        for comparison in sorted(comparisons, key=lambda item: item.id)
    )


def build_paper_selection(
    bundle: EvaluationBundle,
    summaries: tuple[dict[str, Any], ...],
    comparisons: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    overall = [row for row in summaries if row["block_id"] == "all"]
    entry_candidates = sorted(
        (row for row in overall if row["theory_role"] == "entry" and row["gate_status"] == "eligible"),
        key=lambda row: (-row["token_equal_mean_base_objective_pct"], row["theory_id"]),
    )
    veto_candidates = sorted(
        (row for row in overall if row["theory_role"] == "veto" and row["gate_status"] == "eligible"),
        key=lambda row: (-row["token_equal_mean_base_objective_pct"], row["theory_id"]),
    )
    return _paper_document(
        bundle,
        selected_entry=None if not entry_candidates else entry_candidates[0]["theory_id"],
        selected_veto=None if not veto_candidates else veto_candidates[0]["theory_id"],
        summaries=overall,
        comparisons=comparisons,
    )
```

Implement `build_evaluation(bundle)` by rebuilding `signal_rows = build_signal_analysis(bundle.source_bundle)` and `source_rows = build_analysis(bundle.source_bundle.source_bundle).hourly_features`, then calling `build_theory_events`, `build_theory_summaries`, `build_comparison_results`, and `build_paper_selection` once each in that dependency order. Return the four results in `EvaluationTables`.

Produce one `all` summary and one row for every declared block/theory pair, including zero-event rows with numeric metrics set to `None`. Compute token means first, then their arithmetic mean. Define positive P&L concentration as the largest token positive-objective sum divided by total positive-objective sum, or `None` when no positive total exists. Use stable sorted reason codes and lexical ID tie-breaking.

The paper JSON must copy selected predicates and policies from the validated manifest, report source `unknown` and `discovery`, use only `selected_for_paper_discovery` or `no_paper_strategy_selected`, and include the fixed warning that the shortlist is unvalidated and paper-only.

- [ ] **Step 4: Run evaluation and experiment suites GREEN**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py tests/test_experiment.py tests/test_signals.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Inspect and commit Task 3**

Run `git diff --check`, inspect the gate outcomes on literal fixtures, then commit:

```bash
git add src/nansen_signal_lab/evaluation.py tests/test_evaluation.py
git commit -m "Select paper-only strategy theories"
```

### Task 4: Deterministic outputs and offline CLI

**Files:**
- Modify: `src/nansen_signal_lab/evaluation.py`
- Modify: `src/nansen_signal_lab/cli.py`
- Modify: `tests/test_evaluation.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_evaluation()`.
- Produces: `evaluate_manifest(path, check=False)`, three deterministic derived files, and `nansen-lab evaluate`.

- [ ] **Step 1: Write failing rendering/check/CLI tests**

Add tests that:

- assert exact event and summary CSV headers;
- assert JSON serialization uses sorted keys, two-space indentation, and a trailing newline;
- write outputs, modify one byte, and prove `--check` raises `EvaluationError("derived output differs")`;
- prove a second write is byte-identical;
- parse `evaluate --manifest fixture/manifest.json --check`;
- monkeypatch `cli.NansenClient` to raise on construction, invoke `cmd_evaluate`, and assert it prints three `verified:` paths without constructing the client.

The public output headers are:

```python
EVENT_FIELDS = (
    "evaluation_id", "theory_id", "theory_role", "block_id", "chain", "symbol",
    "token_address", "signal_timestamp", "entry_timestamp", "exit_timestamp",
    "holding_period_hours", "gross_return_pct", "gross_objective_pct",
    "base_objective_pct", "stress_objective_pct",
)

SUMMARY_FIELDS = (
    "evaluation_id", "theory_id", "theory_role", "block_id", "event_count",
    "token_count", "event_mean_gross_objective_pct", "event_median_gross_objective_pct",
    "event_mean_base_objective_pct", "event_median_base_objective_pct",
    "event_mean_stress_objective_pct", "event_median_stress_objective_pct",
    "event_win_rate_base", "token_equal_mean_gross_objective_pct",
    "token_equal_mean_base_objective_pct", "token_equal_mean_stress_objective_pct",
    "max_token_positive_pnl_contribution", "gate_status", "gate_reason_codes",
)
```

- [ ] **Step 2: Run output/CLI tests and verify RED**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py tests/test_cli.py -q
```

Expected: failures identify missing rendering, write/check dispatch, and CLI command.

- [ ] **Step 3: Implement deterministic write/check and CLI dispatch**

Add `render_evaluation_outputs(bundle, tables) -> dict[str, bytes]` and:

```python
def evaluate_manifest(manifest_path: str | Path, *, check: bool = False) -> tuple[Path, ...]:
    bundle = load_evaluation_manifest(manifest_path)
    rendered = render_evaluation_outputs(bundle, build_evaluation(bundle))
    paths = tuple(bundle.root / "derived" / name for name in rendered)
    if check:
        for path in paths:
            if not path.is_file() or path.read_bytes() != rendered[path.name]:
                raise EvaluationError(f"derived output differs: {path}")
        return paths
    derived = bundle.root / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    for path in paths:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as output:
            output.write(rendered[path.name])
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    descriptor = os.open(derived, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return paths
```

Write each file through a same-directory `.tmp`, `fsync` the file, replace the target, and `fsync` the derived directory. Do not reuse the paired API evidence transaction writer because these outputs are fully reproducible derived files, not independently retrieved evidence.

In `cli.py`, import `evaluate_manifest`, add `cmd_evaluate(args)`, and register:

```python
s = sub.add_parser("evaluate", help="evaluate fixed paper-only strategy theories offline")
s.add_argument("--manifest", required=True)
s.add_argument("--check", action="store_true")
s.set_defaults(func=cmd_evaluate)
```

- [ ] **Step 4: Run focused, CLI, and full suites GREEN**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_evaluation.py tests/test_cli.py -q
../../.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Inspect and commit Task 4**

Run `git diff --check`, confirm `evaluation.py` has no client import, then commit:

```bash
git add src/nansen_signal_lab/evaluation.py src/nansen_signal_lab/cli.py tests/test_evaluation.py tests/test_cli.py
git commit -m "Add offline paper strategy evaluation"
```

### Task 5: Freeze the feasibility bundle and research conclusions

**Files:**
- Create: `research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json`
- Create: `research/experiments/2026-08-17-paper-strategy-feasibility/derived/theory-events.csv`
- Create: `research/experiments/2026-08-17-paper-strategy-feasibility/derived/theory-summary.csv`
- Create: `research/experiments/2026-08-17-paper-strategy-feasibility/derived/paper-strategies.json`
- Create: `research/experiments/2026-08-17-paper-strategy-feasibility/REPORT.md`
- Modify: `docs/RESEARCH-LEDGER.md`
- Modify: `docs/RESEARCH-GRAPH.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: schema-v3 evaluator and the frozen schema-v2 SHA `881c544e2591f76c727bc30e2663d77df408e72acadd7866ee3c240f5c06b1b8`.
- Produces: a byte-reproducible discovery bundle, exact paper-only shortlist, and durable limitations/follow-up record.

- [ ] **Step 1: Create the exact schema-v3 manifest**

Use:

- evaluation window `[2026-08-13T11:00:00Z, 2026-08-16T10:00:00Z)`;
- blocks `[2026-08-13T11:00:00Z, 2026-08-14T11:00:00Z)`, `[2026-08-14T11:00:00Z, 2026-08-15T11:00:00Z)`, and `[2026-08-15T11:00:00Z, 2026-08-16T10:00:00Z)`;
- the six evaluable records covering five hypotheses, with predicates verbatim from the design;
- H3 comparison ID `holder-breadth-incremental-v1` with positive/reference arm IDs;
- blocked H5 with missing roles `wallet_buyer_breadth` and `exchange_labelled_flow`;
- all cost, feasibility, and advancement gates verbatim from the design.

- [ ] **Step 2: Render the bundle and inspect actual selection**

Run:

```bash
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
```

Inspect every summary and the selection JSON. Do not rewrite thresholds to improve the result. If no entry or veto clears the frozen gates, retain the empty selection and report it.

- [ ] **Step 3: Write the evidence-led report and durable memory**

The report must state exact counts, token coverage, block stability, base/stress sensitivities, H3 spread result, every selected/unselected/blocked reason, and the source guarantee. It must distinguish “selected for paper discovery” from validation and must not call event rows independent trades.

Append—not rewrite—a dated ledger entry. Extend the Mermaid graph with stable nodes for each theory, the feasibility experiment, any selected paper strategy, the blocked beta/PIT evidence requirement, and edges `tests`, `selected_for_paper_discovery`, `rejected_for_paper`, and `blocked_by`. Update README and architecture with the offline command and the beta backtesting follow-up path.

- [ ] **Step 4: Run deterministic, frozen-hash, and hygiene verification**

Run:

```bash
../../.venv/bin/python -m pytest -q
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
sha256sum research/experiments/2026-08-16-seven-token-pilot/manifest.json \
  research/experiments/2026-08-16-seven-token-pilot/derived/hourly-features.csv \
  research/experiments/2026-08-16-seven-token-pilot/derived/event-windows.csv \
  research/experiments/2026-08-16-seven-token-pilot/derived/token-summary.csv \
  research/experiments/2026-08-16-community-signal-shadow/manifest.json \
  research/experiments/2026-08-16-community-signal-shadow/derived/signal-features.csv
git diff --check
git status --short
```

Expected frozen hashes are the six values recorded in `handoff.md`; no `.env`, cache, result scratch, bytecode, lock, backup, temporary, or transaction artifact may enter the diff.

- [ ] **Step 5: Commit the frozen bundle and documentation**

Stage exactly the new experiment directory plus four documentation files and commit:

```bash
git add research/experiments/2026-08-17-paper-strategy-feasibility \
  docs/RESEARCH-LEDGER.md docs/RESEARCH-GRAPH.md docs/ARCHITECTURE.md README.md
git commit -m "Evaluate paper strategy theories"
```

### Task 6: Independent review, final verification, and reversible local integration

**Files:**
- Modify only files required by confirmed Critical or Important review findings.
- Preserve: `.superpowers/sdd/2026-08-17-paper-strategy-feasibility/` as ignored local evidence until integration is complete.

**Interfaces:**
- Consumes: complete branch diff from `91f1822` through Task 5.
- Produces: reviewed commits, a fresh green verification record, and a reversible local fast-forward into `main` if no substantive conflict exists.

- [ ] **Step 1: Request a whole-branch code and research review**

The reviewer must assess schema trust, feature/outcome separation, timing, cost math, cooldown, aggregation, gate determinism, discovery vocabulary, current-bundle calculations, frozen hashes, and absence of live/network behavior. Fix confirmed Critical or Important findings with fresh failing regressions and one scoped re-review.

- [ ] **Step 2: Run final fresh verification on the reviewed branch**

Repeat the full test, all three deterministic checks, six frozen hashes, `git diff --check`, secret/artifact scan, and status inspection. Record exact commands, exit statuses, pass counts, and output hashes in the SDD ledger.

- [ ] **Step 3: Establish rollback and fast-forward locally**

Confirm source `codex/strategy-validation`, target `main`, and that `main` is the branch fork point. Create a reachable rollback branch named `codex/rollback-main-before-strategy-validation-20260817`, then use a fast-forward-only local merge. Do not push, publish, rebase, or delete the feature worktree.

- [ ] **Step 4: Verify the merged tree**

On local `main`, rerun the full test suite and all three deterministic checks. Record the resulting merge commit and rollback ref. If the merge or merged verification has a substantive conflict/failure, leave both branches and the worktree intact and report the exact reason rather than resolving an ambiguous conflict.
