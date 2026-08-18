# GPT prospective pilot citation-enum v6 preflight review

Date: 2026-08-18  
Source bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-schema-subset-v5/`  
Source terminal manifest SHA-256: `b761bb02189966181625be6f1749b969ffd83f720cc6f442460668bae84b88ef`  
Source snapshot SHA-256: `84b6fef7e5ce0e5100312f3d308c13e4c98651e28d13e6e40d86582fba6fb20c`

## Verdict

The separately versioned v6 model-only successor is ready for offline
preregistration. No Nansen or OpenAI request was made during this implementation
and review phase. The predecessor mapping permits v6 to adopt only a terminal,
hash-valid v5 bundle and preserves the exact blinded snapshot and selection
bytes.

## Correction

Both Pass 1 and Pass 2 now derive a deterministic enum of admissible non-null
scalar paths from the exact sealed snapshot. The provider schema applies that
enum to `evidence_for` and `evidence_against`; `missing_evidence` remains bounded
free text. Candidate identity, hourly row paths, formula text, and bookkeeping
or source/version fields are excluded. Local response validation independently
enforces the same set and retains the existing duplicate checks.

For the source snapshot, the derived set contains 124 paths. Each enum contains
5,877 characters, and the two repeated enums contribute 248 values. This is
below the current official Structured Outputs limits of 1,000 enum values across
the schema, 15,000 characters for a string enum with more than 250 values, and
120,000 total schema-string characters. The implementation also reserves space
for the protocol's other schema strings and rejects an oversized set before a
request artifact can be installed or transmission can begin.

Official references:

- [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
  confirms Structured Outputs support.
- [Structured Outputs supported schemas](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas)
  documents enum support and the current schema-size limits.

## Verification

- Targeted RED evidence: four tests initially failed on the absent path
  enumerator, strict pass options, and v6 lifecycle.
- Targeted GREEN: 4 passed.
- Independent review found two fail-closed recovery/order gaps: provider-limit
  validation followed the model-access preflight, and finalized-response
  recovery did not revalidate the exact archived schema request. Both were
  corrected and independently re-reviewed with no residual finding.
- Post-review targeted regression gate: 30 passed in 11.76 seconds.
- Focused protocol/lifecycle/schema/CLI gate after hardening: 135 passed in
  155.84 seconds.
- Full repository suite: 496 passed in 340.41 seconds.
- `python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

The v6 design is
`docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-citation-enum-v6.md`.
The live observation must be preregistered and committed before its model
preflight or either inference is transmitted.
