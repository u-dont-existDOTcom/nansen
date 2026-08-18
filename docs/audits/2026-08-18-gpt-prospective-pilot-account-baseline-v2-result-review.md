# GPT prospective pilot account-baseline v2 result review

Date: 2026-08-18  
Bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-account-baseline-v2/`  
Terminal manifest SHA-256: `bee4e99ad3c0bfebec46012601e13d65307adfe76d34a86a8706195c6a88bbd7`  
Terminal report SHA-256: `ed5ff060ac589243bc235a546f102ed00a9f1334410d9640f8aa175bd45c5c57`

## Verdict

The terminal `unscorable` bundle is internally consistent and passes offline
replay and hash-chain verification. It is not evidence about GPT signal quality:
the run stopped during snapshot normalization before either structured GPT pass.
The observation must remain immutable and must not be retried under the same v2
protocol.

## What worked

- The live unauthenticated Nansen OpenAPI bytes matched the pinned full-document
  SHA-256.
- The exact `gpt-5.6-sol` model-access preflight succeeded. Its archived response
  SHA-256 is
  `37b917dc1858980b31ef19e82da2783d3f669dce320314922428c8117ab2cb20`.
- The v2 account-only fallback matched the observed provider shape: explicit
  cost zero, omitted used/remaining headers, body plan `free`, and body balance
  90. The write-once derivation SHA-256 is
  `9e3ea0136d3fd9658a0aae65f2885ea74746cdbc18a4a2e7974d399f800dd928`.
- Five paid Nansen calls each carried complete one-credit accounting. Replay
  records five calls, five credits, and provider balance 85. No sixth paid call,
  DEX call, OHLCV call, GPT inference, order, wallet action, or venue submission
  occurred after the invalid snapshot became known.

## Terminal evidence

The frozen snapshot cutoff was `2026-08-18T00:16:19.263206Z`. Both flow requests
used that instant as `date.to`. Each response was a final first page with 25 rows:
24 completed hourly buckets followed by the current bucket starting at
`2026-08-18T00:00:00Z`, ending at `2026-08-18T01:00:00Z`, and explicitly marked
`is_complete=false`. The strict validator rejected Smart-Money row index 24
before constructing features. The archived Smart-Money and exchange response
SHA-256 values are respectively
`6b9558edc111627ebee9d57ae8793f12f75a1d8a63848fbff7187775ab9c006d`
and
`144aba3177aec0a7bc74cc3924cdd7784aba92bd922fce7625af7228cb061929`.

The terminal seal was recorded at `2026-08-18T00:16:53.155024Z`. It binds budget
journal head
`6921a16f62c78097354884e3f0eee98212069d17584008afff8c31e051ac0a4e`
and budget snapshot
`14b48cb158466d6c459497350bca42bdfcc6e7e70e5e2928d2d68b3297acf6d8`.

## Root cause and future-run correction

The fail-closed validator was consistent with the implementation plan, but the
v2 request range made an incomplete final row inevitable at a non-hour-aligned
cutoff. This is an operational protocol defect, not provider contract drift and
not a model failure.

A future observation may use a separately versioned completed-hour protocol.
It must preserve strict row validation and change only the flow request range:
freeze the current UTC-hour boundary, request 25 hourly starts ending one
microsecond before that boundary, and continue to reject any returned row whose
`is_complete` is not literal `true` or whose `bucket_end` exceeds the original
snapshot cutoff. Exact non-hour-aligned request literals and the current-bucket
exclusion require regression coverage before another preregistration. The v2
bundle and this review must remain unchanged.
