# Prospective GPT pilot pre-spend review

Date: 2026-08-17

Planning checkpoint: `402042d`

Runner checkpoint: `e4024e9`

Preregistration checkpoint: `3a6546c`

Scope: the complete prospective-pilot branch before any Nansen or OpenAI provider call

## Gate result

The reviewed implementation is suitable to cross the pre-spend gate after the fixes recorded below and the final verification block. The real bundle remains `preregistered`: zero Nansen calls, zero Nansen credits, no GPT inference, no selected token, no outcome artifact, and no `REPORT.md`.

The review used the design, implementation plan, pinned Nansen contract extract, exact branch diff, fake-adapter lifecycle tests, offline replay/check, and the frozen prior experiment manifests. It asked independently whether identity or outcome data can leak into GPT, whether accounting can exceed the fixed ceiling or reroll an ambiguous request, whether incomplete evidence can become zero, whether evidence and lifecycle state survive interruption, whether all six comparators retain their frozen semantics, and whether any execution path can create a real trade.

## Important findings closed

### 1. Nansen ledger did not bind response metadata

The budget journal previously recorded the SHA-256 of only the raw response bytes. A later change to archived credit headers or request/retrieval timestamps could therefore leave the ledger apparently valid even though its accounting evidence had changed.

The ledger now binds the canonical response-metadata artifact. That artifact binds the raw response filename and SHA-256, exact response headers, status, request ID, credit cost/use/remaining fields, parse status, request/retrieval/write timestamps, and attempt number. Confirmation, failure, retry, reconciliation, and replay all validate the metadata/raw pair. A regression mutating metadata while preserving raw bytes now fails closed.

### 2. Predecision snapshot cutoff was not durable before requests

The point-in-time `available_at` cutoff was previously held only in memory. If the process stopped after provider responses but before snapshot sealing, a later invocation could derive a new cutoff and canonical payload for the same logical request, preventing safe adoption or risking inconsistent evidence.

`derived/snapshot-cutoff.json` is now installed write-once before the screener request and reused on recovery. Its `available_at` and durable-write timestamp are validated, and the artifact enters the next seal. The interruption regression confirms recovery reuses all prior responses and makes no duplicate provider calls.

### 3. Terminal recovery could replace the first failure reason

If execution stopped after installing the terminal reason and `REPORT.md` but before the terminal seal, a later invocation could derive a different transient error message and collide with the write-once artifacts.

Recovery now validates and adopts the first durable terminal reason verbatim. Collision-quarantine references are hash-bound into that reason, and unsealed derived/normalized evidence is included in the terminal seal. The regression interrupts immediately before terminal commit and confirms that resume makes no HTTP call and preserves the first report.

### 4. Exchange-flow ordering was not explicitly validated

Both flow labels already required explicit complete rows, final-page metadata, and no future bucket. Exchange rows nevertheless could reach normalization with duplicate or non-monotonic `(bucket_end, date)` keys.

Both labels now require strictly increasing `(bucket_end, date)` keys before the permissive legacy preparation path is called. Duplicate and reversed exchange rows are rejected by a focused regression.

## Review conclusions

- GPT Pass 1 receives only the identity-blinded normalized snapshot. Pass 2 receives that same sealed snapshot plus only the archived Pass-1 reasoning summary; neither prompt contains the selected chain, address, symbol, prior results, outcome evidence, tool access, or comparator decisions.
- The budget guard reserves conservatively, binds canonical request identity before transmission, enforces ten billable calls and ten credits, and permits only one persisted retry for a received 429 with explicit zero use and bounded integer `Retry-After`. Transmitted requests without complete evidence are never rerolled.
- Missing, stale, incomplete, malformed, non-final, non-monotonic, duplicate, or unavailable evidence remains unavailable or terminal `unscorable`; it is never coerced to false or zero. Strict-greater-than scoring excludes ties.
- Raw bytes, metadata, conflicts, journal transitions, immutable stage snapshots, seals, and the terminal report are write-once or exact-adopted and hash-verified. Recovery regressions cover request, response, ledger, derived-artifact, model, seal, report, and manifest interruption boundaries.
- The six frozen base/paired records are imported from the hash-bound schema-v3 manifest. Base applicability and paired distribution-veto availability are represented separately, including veto-unavailable propagation.
- The prospective workflow computes paper observations only. It contains no order construction, signing, wallet action, venue submission, custody, or capital-movement path.

## Verification evidence

The final pre-spend focused surface passed:

```text
.venv/bin/python -m pytest -q tests/test_budget.py tests/test_prospective_runner.py tests/test_prospective_snapshot.py tests/test_prospective_schema.py tests/test_client.py
124 passed in 463.44s
```

The real bundle replayed as:

```json
{"artifact_count":0,"gpt_beats_frozen_strategies":null,"nansen_calls":0,"nansen_credits":0,"stage":"preregistered"}
```

The complete exact-tree regression suite passed:

```text
.venv/bin/python -m pytest -q
464 passed in 143.20s
```

`compileall`, `git diff --check`, both historical `analyze --check` commands, the frozen-strategy `evaluate --check`, and the real pilot `pilot-replay`/`pilot-check` all exited zero on the same final audit tree. No provider call was made by this verification block.
