# Prospective GPT Pilot: Exact-Citation Enum Successor v6

Status: preregistration protocol for one final model-only successor observation.

This document is a narrow normative overlay on the schema-subset v5 design,
whose immutable result is recorded in
`docs/audits/2026-08-18-gpt-prospective-pilot-schema-subset-v5-result-review.md`.
The source v5 observation and every source artifact remain immutable. Version 6
does not reinterpret or retry that observation.

## Motivation

Both permitted v5 Pass-1 inferences completed with schema-valid JSON but used
narrative strings instead of literal snapshot field paths. The one repair kept
the same semantic error, so the local exact-citation validator correctly made
v5 terminally unscorable before Pass 2.

## Frozen source

Version 6 is model-only. Before initialization it must verify the complete v5
hash chain and require the exact terminal v5 manifest, snapshot seal, blinded
`normalized/snapshot.json`, and identity-bearing `derived/selection.json`.
Preregistration binds the exact path and SHA-256 of each source. The successor
copies the exact snapshot and selection bytes into its own snapshot seal; it
does not renormalize, refresh, or select again.

The successor begins with an empty Nansen budget journal. It must make zero
Nansen HTTP requests and consume zero Nansen credits. Any attempt to enter the
ordinary collection or settlement paths is terminally refused before an HTTP
call.

## Provider schema correction

For both passes, the strict provider schema must enumerate the admissible exact
snapshot paths for every `evidence_for` and `evidence_against` item. The enum is
derived deterministically from the exact copied snapshot before transmission.
It contains only non-null scalar leaves and excludes candidate identity,
hourly `rows`, schema/durable-write bookkeeping, feature-set/source identifiers,
and formula text. `missing_evidence` remains bounded free text because it names
information absent from the snapshot.

The enumerated paths and resulting schema are archived inside each exact request
artifact and therefore hash-bound. Local validation independently rejects any
supporting or opposing citation outside the same admissible set, duplicates
within a list, or duplicates across lists. Provider enum limits must be checked
locally before any request artifact is installed or transmission begins.

The exact model remains `gpt-5.6-sol`, reasoning effort remains high, tools
remain disabled, and a fresh archived model-access preflight is required. One
schema-valid but locally invalid response may receive the already-preregistered
single repair. A provider failure, timeout, refusal, malformed response, or
ambiguous transmission is terminal and must never be rerolled. Pass 2 must bind
the exact copied snapshot hash and exact archived Pass-1 response hash.

## Completion

The successor is complete at `decision_sealed` after both validated pass
responses, their final pointers, the comparator records, the decision record,
and `MODEL-RESULT.md` are sealed. It is a model-protocol test over a historical
point-in-time snapshot, not a new prospective market observation. No entry,
DEX, OHLCV, paper settlement, score, order, wallet, gas, or venue action is
authorized, and this single result cannot establish strategy advancement.
