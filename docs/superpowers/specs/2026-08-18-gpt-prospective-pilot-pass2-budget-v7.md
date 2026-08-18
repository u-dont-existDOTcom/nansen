# Prospective GPT Pilot: Pass-2 Output-Budget Successor v7

Status: preregistration protocol for one controlled model-only successor
observation.

This document is a narrow normative overlay on the exact-citation v6 design.
The terminal v6 observation and every v6 artifact remain immutable. Version 7
does not retry, rewrite, or reinterpret v6.

## Motivation

V6 Pass 1 completed on its first attempt with a locally valid structured
response and exact enum citations. V6 Pass 2 returned HTTP 200 with provider
status `incomplete` and reason `max_output_tokens`: all 4,000 generated tokens
were reasoning tokens, and no visible structured output was produced. The v6
no-reroll rule correctly made that observation terminally unscorable.

## Frozen source and single variable

Version 7 is model-only. Before initialization it must verify the complete v6
hash chain and require the exact terminal v6 manifest, snapshot seal, blinded
`normalized/snapshot.json`, and identity-bearing `derived/selection.json`.
Preregistration binds each source path and SHA-256. The successor copies the
exact snapshot and selection bytes into its own snapshot seal; it does not
renormalize, refresh, or select again.

The only protocol parameter changed from v6 is the Pass-2
`max_output_tokens`: it increases from 4,000 to 25,000. Pass 1 remains at
4,000. The exact model remains `gpt-5.6-sol`, reasoning effort remains `high`,
tools remain disabled, both prompts and strict schemas remain unchanged, and
the same deterministic exact-citation enum and local validators apply.

The successor begins with an empty Nansen budget journal. It makes zero Nansen
HTTP requests and consumes zero Nansen credits. It authorizes no DEX, OHLCV,
entry, settlement, score, order, wallet, gas, or venue action.

## Attempts and terminal behavior

A fresh archived model-access preflight is required. Each pass begins with one
inference attempt. The already-established single repair remains permitted only
when a completed, parseable structured response fails local validation. A
provider failure, timeout, refusal, malformed or incomplete response, or
ambiguous transmission is terminal and must never be rerolled. Pass 2 binds the
exact copied snapshot hash and the exact newly archived Pass-1 response hash.

## Completion

The successor is complete at `decision_sealed` after both validated pass
responses, their final pointers, comparator records, decision record, and
`MODEL-RESULT.md` are sealed. Any terminal provider or protocol failure is
sealed at `unscorable` and is part of the observation. This historical
model-protocol test cannot establish performance or strategy advancement.
