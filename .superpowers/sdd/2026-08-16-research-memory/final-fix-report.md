# Final review fix report

Date: 2026-08-16

Branch: `codex/research-memory`

Worktree: `/home/joel/Téléchargements/nansen-signal-lab/.worktrees/research-memory`

Required starting commit: `917d554e629b2fe228f11c256fb5a8148074d08f`

Starting commit verified before edits: yes

Commit range after completion: `917d554e629b2fe228f11c256fb5a8148074d08f..HEAD`

## Outcome

Every item in `final-review-findings.md` was implemented in one serialized fix wave. Raw evidence was not edited. Derived timestamps now represent bucket-end availability, all existing derived numerical values remain invariant after normalizing the uniform one-hour shift, manifest provenance is validated against evidence content, flow cache/output provenance is explicit, and explicit evidence outputs are overwrite-safe.

## Per-finding changes

### Critical: bucket availability timestamp

- Added strict timezone-aware ISO-8601 parsing for both raw `date` and `bucket_end`.
- Rejects missing, naive, malformed, equal, or decreasing source boundaries with `ExperimentError`.
- Uses parsed `bucket_end` as the internal availability index and emitted `timestamp`.
- Preserves raw `date` in `source_bucket_start` and raw `bucket_end` in `source_bucket_end` in both hourly and event CSVs.
- Bases one-hour deltas, trailing horizons, forward horizons, MFE, MAE, gap detection, and summary endpoint timestamps on availability time.
- Regenerated all three pilot CSVs. The complete derived availability window is now `2026-08-12T11:00:00Z` through `2026-08-16T09:00:00Z`.
- Updated the committed-pilot integration expectation for CDXR's largest bucket: source start `2026-08-15T21:00:00Z`, availability/event timestamp `2026-08-15T22:00:00Z`.
- Updated the manifest derivation metadata, report, architecture, evidence graph, append-only ledger correction, approved design, and conflicting implementation-plan sketches.
- Documented the downside precisely: availability/window labels move one hour later; intra-series numerical results do not change.

### Important: manifest provenance validation

- Added kind-specific validation for `tgm_flows` and `token_screener_candidates` evidence.
- Rejects unknown or incompatible evidence kind/endpoint combinations.
- Requires evidence request objects, aware retrieval timestamps, non-negative integer row/completeness counts, and expected candidate observed-bound semantics.
- Parses flow JSON, requires raw `data` to be a list, validates every raw row object and both temporal boundaries, and validates boolean completeness.
- Compares `row_count`, `complete_count`, `observed_from`, and `observed_to` against raw flow rows.
- Validates candidate CSV structure and row/completeness counts.
- Validates cohort flow references resolve to `tgm_flows` evidence.
- Validates persisted flow request chain/address against cohort identity when present, including nested exact payloads.
- Continues to accept the pilot's explicitly unavailable exact request boundaries without inventing values.
- Requires every `selection.candidate_evidence_id` to exist and resolve to Token Screener evidence.

### Important: numeric and row safety

- Rejects non-object raw rows with `ExperimentError` instead of leaking `AttributeError`.
- Parses both price and holdings inside a guarded conversion.
- Accepts only finite positive prices and finite non-negative holdings.
- Counts malformed strings, negative prices, malformed/negative/NaN/infinite holdings, and non-positive/non-finite prices as invalid metrics and excludes the affected row.
- Prevents those invalid values from reaching derived output or calculation paths.

### Important: cache provenance

- Preserved `NansenClient.post()` as a body-returning interface.
- Added `post_with_provenance()`, returning the body, `cache_hit`, and original `response_retrieved_at`.
- New network cache writes persist a sibling metadata record with endpoint, exact payload, original retrieval timestamp, response filename, and response SHA-256.
- Cache hits reuse the original persisted retrieval timestamp and validate endpoint, payload, and response hash.
- Legacy raw-only cache entries use UTC file mtime as the explicitly documented fallback.
- `cmd_flows` records `cache_hit`, `response_retrieved_at`, and `artifact_written_at` separately.

### Important: safe response/sidecar writes

- Serializes the response before the sidecar so the exact output SHA-256 is embedded in sidecar metadata.
- Writes both response and sidecar to sibling `mkstemp` files, flushes and `fsync`s each file, and only then replaces destinations.
- Cleans temporary files on both success and failure paths.
- Preserves default ignored `results/` overwrite behavior.
- Refuses an explicit `--output` when either response or sidecar exists, before client construction/API use.
- Adds `--force-output` for deliberate explicit replacement and repeats the no-overwrite check immediately before replacement.

### Minor hardening

- Changed `.gitignore` from `.venv/` to `.venv`, so both a directory and the worktree symlink are ignored.
- EVM-style `0x`/`0X` addresses are normalized case-insensitively.
- Non-EVM addresses, including Solana identities, preserve case.

## TDD evidence

The baseline at the required starting commit was:

```text
$ ./.venv/bin/python -m pytest -q
.................................                                        [100%]
33 passed in 1.55s
```

### Slice 1: availability timestamps and numeric/row safety

RED command:

```text
$ ./.venv/bin/python -m pytest -q tests/test_experiment.py -k 'bucket_end_availability or invalid_bucket_boundaries or non_object_rows or unsafe_price_or_holdings or feature_and_event_windows or build_analysis_uses'
14 failed, 28 deselected in 1.12s
```

Representative expected failures were start-time output (`00:00` instead of bucket-end `01:00`), no exception for naive/non-increasing boundaries, leaked `AttributeError` for a string row, leaked `ValueError` for malformed holdings, and accepted negative/non-finite metrics.

GREEN command:

```text
$ ./.venv/bin/python -m pytest -q tests/test_experiment.py -k 'bucket_end_availability or invalid_bucket_boundaries or non_object_rows or unsafe_price_or_holdings or feature_and_event_windows or build_analysis_uses or missing_or_zero_metrics or string_zero'
................                                                         [100%]
16 passed, 26 deselected in 0.53s
```

### Slice 2: manifest provenance and identity semantics

RED command:

```text
$ ./.venv/bin/python -m pytest -q tests/test_experiment.py -k 'invalid_evidence_kind or non_list_flow_data or provenance_that_disagrees or request_identity_mismatch or candidate_selection or flow_reference or evm_duplicate or solana_address'
14 failed, 1 passed, 41 deselected in 1.44s
```

The one pre-existing pass was the EVM duplicate case; the meaningful failures showed that impossible kind/endpoint pairs, non-list flow data, false counts/ranges, request/cohort mismatches, invalid evidence references, and distinct-case Solana addresses were not handled correctly.

GREEN command:

```text
$ ./.venv/bin/python -m pytest -q tests/test_experiment.py -k 'invalid_evidence_kind or non_list_flow_data or provenance_that_disagrees or request_identity_mismatch or candidate_selection or flow_reference or evm_duplicate or solana_address or manifest_accepts_matching or committed_pilot'
.................                                                        [100%]
17 passed, 39 deselected in 0.67s
```

### Slice 3: cache provenance and safe output writes

RED command:

```text
$ ./.venv/bin/python -m pytest -q tests/test_client.py tests/test_experiment.py -k 'network_response or cache_hit or legacy_raw_only or post_keeps or write_flow_artifacts or flows_parser or cmd_flows'
9 failed, 1 passed, 53 deselected in 1.99s
```

Expected failures included the missing provenance-aware client method, missing force flag, old sidecar signature, `cmd_flows` using body-only `post()`, and API client construction before overwrite refusal. The body-only `post()` compatibility test was the pre-existing-interface pass.

GREEN command:

```text
$ ./.venv/bin/python -m pytest -q tests/test_client.py tests/test_experiment.py -k 'network_response or cache_hit or legacy_raw_only or post_keeps or write_flow_artifacts or flows_parser or cmd_flows'
..........                                                               [100%]
10 passed, 53 deselected in 3.14s
```

Final focused verification after documentation/artifact updates:

```text
$ ./.venv/bin/python -m pytest -q tests/test_client.py
....                                                                     [100%]
4 passed in 8.86s

$ ./.venv/bin/python -m pytest -q tests/test_experiment.py
...........................................................              [100%]
59 passed in 13.02s
```

## Artifact and invariance verification

Pilot regeneration:

```text
$ ./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json
.../derived/hourly-features.csv
.../derived/event-windows.csv
.../derived/token-summary.csv
```

Deterministic check:

```text
$ ./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
verified: .../derived/hourly-features.csv
verified: .../derived/event-windows.csv
verified: .../derived/token-summary.csv
```

A CSV-aware comparison against the starting commit normalized each new feature/event `timestamp` back to `source_bucket_start`, removed the two new source-boundary columns, and normalized summary endpoints by minus one hour. Every other field matched byte-for-byte as parsed CSV text:

```text
derived invariance verified: hourly-features.csv=665 rows, event-windows.csv=259 rows, token-summary.csv=7 rows
```

## Raw evidence audit

`git diff --exit-code HEAD -- research/experiments/2026-08-16-seven-token-pilot/raw` exited `0` with no output. Fresh hashes match the starting baseline and manifest:

```text
4ed384e23a9156017e358c524ae8c837f40c251ec993d819e88fe801bb8fba43  candidates-20260816T094029Z.csv
469c9195c9d262dee393ae68ab5b78bbc9ffcd649da405303fcaf90e62f8b463  flows-base-0xadf0d31463.json
680fe1da9c1a317a975ad4a5b8ba347b0d391a7a14abd9740eae620bcd4c2785  flows-base-0xadf4d5b9d7.json
4f2b87e2df8e7909f10cd584023bdc71e1aebf3ac7223ca25bd6902de71fe243  flows-base-0xadf9afd4fa.json
3a50c97dfce1c4ce85b84a8bd65950ef09a3c409481347e158d95c1dcf603581  flows-base-0xadfd54cb29.json
b13039c4a9afd6f66ab621b9c0164892f8e625847e75c6bf71f68e2d358f4a3f  flows-ethereum-0x40aaf75454.json
7986503809d72c19b9cb5ab234ea416370edb3c3b9d35e26644bd6e43046472a  flows-solana-A13oRB9FFaiU.json
acf08cb859a347ed48346c154c7a9ace17d6301d3789237f106848079013c8f5  flows-solana-Ai66LHZG9MCz.json
```

## Final verification

```text
$ ./.venv/bin/python -m pytest -q
..................................................................       [100%]
66 passed in 1.71s

$ git diff --check
(no output; exit 0)

$ rg -n --hidden --glob '!*.md' --glob '!manifest.json' <credential-patterns> research src tests
(no matches; rg exit 1)

$ git ls-files .env .venv 'data/cache/**' 'results/**'
data/cache/.gitkeep
results/.gitkeep

$ git check-ignore -v .venv
.gitignore:1:.venv .venv
```

Changed paths are limited to the intended ignore rule, source, tests, research design/plan/docs, pilot manifest/report, regenerated derived CSVs, and this final report. No `.env`, cache payload, result payload, virtual environment, credential, or raw evidence path is included.

## Self-review

- Re-read every finding against the final source/test/document diff.
- Confirmed no start-time `row["date"]` feature timestamp remains in code or corrected plan sketches.
- Confirmed event rows inherit both source boundaries.
- Confirmed manifest validation precedes analysis and validates the committed pilot without reconstructing unavailable request boundaries.
- Confirmed explicit-output refusal occurs before `NansenClient` construction and is repeated before replacement.
- Confirmed default scratch overwrite remains covered.
- Confirmed response hashes are calculated over the exact bytes written.
- Confirmed no raw evidence diff and no non-timestamp derived value drift.

## Commits

- Base: `917d554e629b2fe228f11c256fb5a8148074d08f`
- Fix wave: the single commit containing this report; resolve as `HEAD` after commit (`917d554e629b2fe228f11c256fb5a8148074d08f..HEAD`).

## Residual concerns

None within the requested scope. Response and sidecar replacement is atomic per file rather than transactional across two filenames; the required sidecar SHA-256 makes an interruption between the two replaces detectable, and both complete temporary files are fsynced before either destination is replaced.
