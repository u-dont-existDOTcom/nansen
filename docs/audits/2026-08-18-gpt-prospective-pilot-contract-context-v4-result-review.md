# GPT prospective pilot contract-context v4 result review

Date: 2026-08-18  
Bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-contract-context-v4/`  
Terminal manifest SHA-256: `f856e66a4b0d71ce70dbe916b7b2efbf9dadbdb109bf6db94fcc85b1ae828ec7`  
Terminal report SHA-256: `11354d5a850045177253625fbcfb84760d7bc8c865dba46bbd3f68f5b6a74988`

## Verdict

The terminal `unscorable` bundle passes offline replay and hash-chain checking.
The v4 Nansen contract normalization worked and produced a sealed prospective
snapshot. The run is not evidence about GPT signal quality because OpenAI
rejected the strict response schema before model inference began. This
observation is immutable and must not be retried under the same v4 protocol.

## What v4 established

- The live OpenAPI checksum, exact `gpt-5.6-sol` model preflight, account
  baseline fallback, completed-hour flow range, and per-call Nansen credit
  accounting all passed.
- The live contract-context shapes normalized successfully. The sealed blind
  snapshot SHA-256 is
  `84b6fef7e5ce0e5100312f3d308c13e4c98651e28d13e6e40d86582fba6fb20c`.
- The runner selected Solana token `ANSEM`, froze $1,000 virtual notional, and
  sealed the selection separately from the identity-blinded model input.
- Five paid Nansen calls used five credits, moving the provider balance from 80
  to 75. No DEX, OHLCV, order, wallet, gas, or venue action followed the model
  protocol failure.

## Terminal OpenAI response

Pass 1 was transmitted once. OpenAI returned HTTP 400 with request ID
`req_616f79b121014f25b3ccd6223826520a` and error code
`invalid_json_schema`:

> Invalid schema for response_format 'prospective_pass_1': In
> context=('properties', 'evidence_against'), 'uniqueItems' is not permitted.

The exact archived response SHA-256 is
`e00b679e0e20a62c71a1c9333babec0a6a520bc8c616d060a81176f7fa335dad`.
It contains no model response ID, returned model ID, provider creation time, or
token usage. Pass 2 was never requested. Therefore the API was reached, but no
completed `gpt-5.6-sol` inference occurred.

The official [GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
lists Structured Outputs as supported. The official
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas)
states that strict output schemas use a subset of JSON Schema and that an
unsupported schema produces an API error. Its supported array constraints list
does not include `uniqueItems`, matching the archived provider rejection.

## Safe successor rule

A separately versioned model-only successor may reuse the exact sealed v4 blind
snapshot without making another Nansen request. It must remove `uniqueItems`
from both pass schemas before transmission while retaining the existing local
post-response uniqueness checks. It must bind both requests to the exact v4
snapshot hash, archive every model attempt, and treat any transmitted failure as
terminal. The v4 artifacts themselves remain unchanged.

The snapshot seal was recorded at `2026-08-18T01:05:27.698052Z`; the terminal
seal was recorded at `2026-08-18T01:05:34.173380Z`. Both bind budget journal
head `0936a3b6e56f670b7e7bd00df2767f4f5a6d582f423915b0df7ff8365a687990`.
