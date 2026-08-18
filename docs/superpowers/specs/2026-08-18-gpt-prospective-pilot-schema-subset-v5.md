# Prospective GPT Pilot: Schema-Subset Model Successor v5

Status: preregistration protocol for one model-only successor observation.

This document is a narrow normative overlay on the contract-context v4 design,
whose immutable SHA-256 is
`d23d8d25060fc6af3e2d644d41ad4663573a613b68cb2e780239fe0216f26909`.
The source v4 observation and all of its artifacts remain immutable. Version 5
does not reinterpret or retry that observation.

## Motivation

The v4 live run successfully sealed its Nansen snapshot, then OpenAI rejected
Pass 1 before inference with `invalid_json_schema`: the strict response schema
used `uniqueItems`, which the provider reported was not permitted. The response
had no model response ID, returned model ID, usage, or output. A new observation
is required to test the model itself.

## Frozen source

Version 5 is model-only. Before initialization it must verify the complete v4
hash chain and require the exact terminal v4 manifest, snapshot seal, blinded
`normalized/snapshot.json`, and identity-bearing `derived/selection.json`.
Preregistration binds the exact path and SHA-256 of each source. The successor
copies the exact snapshot and selection bytes into its own snapshot seal; it
does not renormalize, refresh, or select again.

The successor begins with an empty Nansen budget journal. It must make zero
Nansen HTTP requests and consume zero Nansen credits. Any attempt to enter the
ordinary collection or settlement paths is terminally refused before an HTTP
call.

## Provider schema correction

Both strict provider schemas omit `uniqueItems`. All local post-response checks
remain unchanged: duplicates within every evidence, missing-evidence, or risk
list are rejected, and evidence references must remain unique across the
supporting and opposing lists. One schema-valid but locally invalid response
may receive the already-preregistered single repair; a provider failure,
timeout, refusal, malformed response, or ambiguous transmission is terminal and
must never be rerolled.

The exact model remains `gpt-5.6-sol`, reasoning effort remains high, tools
remain disabled, and a fresh archived model-access preflight is required. Pass
2 must bind the exact copied snapshot hash and exact archived Pass-1 response
hash.

## Completion

The successor is complete at `decision_sealed` after both validated pass
responses, their final pointers, the comparator records, the decision record,
and `MODEL-RESULT.md` are sealed. It is a model-protocol test over a historical
point-in-time snapshot, not a new prospective market observation. No entry,
DEX, OHLCV, paper settlement, score, order, wallet, gas, or venue action is
authorized, and this single result cannot establish strategy advancement.
