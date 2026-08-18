# GPT prospective pilot schema-subset v5 result review

Date: 2026-08-18  
Bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-schema-subset-v5/`  
Terminal manifest SHA-256: `b761bb02189966181625be6f1749b969ffd83f720cc6f442460668bae84b88ef`  
Terminal report SHA-256: `e7ddd50e4b8ac12313b73adbb435ed35756c53e3759b12e1b5f65b3a0c2d7165`

## Verdict

The terminal `unscorable` bundle passes offline replay and hash-chain checking.
It is valid evidence about `gpt-5.6-sol` Pass-1 behavior, but not about Pass 2,
paper returns, or strategy quality. Both permitted Pass-1 inferences completed
successfully and returned schema-valid output; both then failed the stricter
local exact-citation check. The v5 observation is immutable and must not be
rerolled.

## Model result

Both independent completed responses chose the same high-level decision:

- action: `ABSTAIN`;
- confidence: `0.79`;
- expected four-hour direction: `FLAT`; and
- risk flags: `INCOMPLETE_DATA`, `EXCHANGE_PRESSURE`, `HIGH_VOLATILITY`, and
  `MODEL_UNCERTAINTY`.

The first response cited entries beginning with `snapshot.` but appended values
and prose to each field path. The one allowed repair supplied the validation
errors and original input. The second response retained the same decision and
evidence, changed the prefix to `original_input.snapshot.`, and still appended
values and prose. Neither list consisted of literal field paths, so the local
validator correctly rejected every citation. Pass 2 was never transmitted.

Attempt 1 has response ID
`resp_012b68453e09dbc0016a83bc6a71fc81a2b1c37870b7488ba3`, request ID
`req_c3420949b8794e50a0978c1d4d7bb321`, and exact response SHA-256
`e9b6a8f55e53d1f6aa5082a2daffc8830ee8d32dfa4c746fc3f20cd8390539c7`.
Attempt 2 has response ID
`resp_0387527bdd85a084016a83bc804f6487d2b82261632e678d5c`, request ID
`req_ba4d2a13939248e6a30c8e70ff4979fe`, and exact response SHA-256
`bafe72430a84a70e65a243026b56bd0c42a6141e8b7d5dec18527510754d259c`.
Both returned model ID `gpt-5.6-sol`.

Provider usage was 10,682 input tokens and 1,954 output tokens, including 764
reasoning tokens, for 12,636 total tokens across both attempts. The copied blind
snapshot retained SHA-256
`84b6fef7e5ce0e5100312f3d308c13e4c98651e28d13e6e40d86582fba6fb20c`.

## Cost and scope

The successor made zero Nansen requests and used zero Nansen credits. It made
one OpenAI model preflight and the two preregistered Pass-1 attempts. No Pass 2,
DEX, OHLCV, settlement, score, order, wallet, gas, or venue action occurred.

## Safe successor rule

A separately versioned final model-only successor may adopt the same exact
snapshot through the v5 source seal. Rather than relying on prose instructions,
its strict response schema should enumerate the admissible scalar snapshot
paths for `evidence_for` and `evidence_against`; local validation remains the
second line of defense. The official
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas)
documents support for enum constraints and currently permits up to 1,000 enum
values across a schema. Raw hourly row paths and identity metadata should be
excluded to keep the admissible set bounded and analytically relevant.

The terminal seal SHA-256 is
`947f04fffced217e30296bccfb599f79c84c3919920c93f41525f07f16a137a2`.
