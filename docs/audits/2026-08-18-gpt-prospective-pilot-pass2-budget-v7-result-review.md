# GPT prospective pilot Pass-2 budget v7 result review

Date: 2026-08-18
Bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-pass2-budget-v7/`
Implementation commit: `e82c098`
Preregistration commit: `be9e208`
Terminal manifest SHA-256: `77bbc451c7f95efb26c2f8746f4038ca2f620e00a918444edd3c0890106409f8`

## Verdict

V7 completed successfully at its preregistered model-only terminal stage,
`decision_sealed`. Both `gpt-5.6-sol` passes returned completed, locally valid
structured responses on attempt 1. Pass 2 used 5,808 output tokens, including
4,792 reasoning tokens, and therefore completed within the changed 25,000-token
allowance that replaced v6's insufficient 4,000-token allowance.

The result is a completed model-protocol observation over an immutable
historical snapshot. It is not a prospective return, performance score,
strategy-advancement result, trading recommendation, or authorization to
settle the generic entry-window metadata present in the decision artifact.
Model-only settlement remains forbidden.

## Controlled change and source provenance

The terminal v6 source manifest was bound at SHA-256
`655a446e994be368cf4364577a5e349c108f9a673ba0233e4ccc900cf439a4db`.
V7 copied the exact v6 snapshot and selection bytes:

- snapshot SHA-256:
  `84b6fef7e5ce0e5100312f3d308c13e4c98651e28d13e6e40d86582fba6fb20c`;
- selection SHA-256:
  `0795aa5f5b1aca15c39a64e389bb5ac6784aa4f66bb6b7ccb3648d496b9d0bc8`;
- v6 snapshot-seal SHA-256:
  `e3802cbd26410206628dc15df477e65b3629a66cbe86005cd21e8d943c4cd4d6`.

The archived v6 and v7 Pass-1 request bodies have the same canonical SHA-256,
`7f5cc4f947c08ada6f95115f9fb3389d97547ac3edcdce017601ab589e64df74`.
The Pass-2 request bodies, after excluding the newly generated Pass-1 input and
the preregistered output allowance, have the same canonical SHA-256,
`6f097e591ea32df91865fb5de0b040a49a0dbea51c6b6b1b6706f413669564e7`.
Pass 1 retained `max_output_tokens=4000`; Pass 2 alone used
`max_output_tokens=25000`. Both retained reasoning effort `high`, the same
prompts, exact-citation schemas, tools-disabled setting, and local validators.

## Model result

Pass 1 returned `ABSTAIN`, confidence `0.86`, and expected four-hour direction
`FLAT`. It cited eight supporting paths and twelve opposing paths and flagged
`INCOMPLETE_DATA`, `FLOW_CONCENTRATION`, `HIGH_VOLATILITY`, and
`MODEL_UNCERTAINTY`. Its rationale recognized the constructive four-hour
accumulation/divergence features but treated the one-hour and twelve-hour
distribution regimes, negative shorter/longer-horizon holdings changes,
negative four-hour price movement, negative top-PnL flow, and warnings as
insufficient for `LONG`.

Pass 2 returned `UPHOLD` and final action `ABSTAIN`. It assessed all six frozen
theories, reported no conflicts, and cited eight supporting and eight opposing
paths. It found no applicable confirmed four-hour `LONG`: the flow-only and
breadth-acceleration entries failed frozen predicates, the risk-off veto was
inactive, the two holder-breadth comparison arms were outside the requested
four-hour horizon, and the sustained-markup record was unavailable because its
required twelve-hour acceleration value was absent.

All 20 Pass-1 citations and all 16 Pass-2 citations are distinct within their
respective response, nonoverlapping across supporting/opposing lists, and
literal members of the identical archived 124-path enum. Both request inputs
pass the local identity-blinding guard.

## Provider evidence and usage

Pass 1:

- request ID: `req_a3a65599fcaf466497a00c02acd374c1`;
- response ID: `resp_05b0848181fa9a51016a84156f48e487d2ad2b649d86dac08d`;
- response SHA-256:
  `0ca2343934c692889c105dbe499390b9269abd916e8c5cb425f67664260bb13f`;
- final-pointer SHA-256:
  `9dd14d42246e660b8c4c5be75205d1f65ee552999923fd575b5b5d9ced81b8c8`;
- usage: 7,561 input, 1,210 output, 784 reasoning, 8,771 total tokens.

Pass 2:

- request ID: `req_46e4bb7f4055490f8f3f9bff6b2eaf36`;
- response ID: `resp_0c424d0ffb163543016a8415862c7887d2becd092bc7b7be18`;
- response SHA-256:
  `b154a99b9ce523f38d39487ebba46a1cc6b4ed5f3c24df3b7a8762742d9a61be`;
- final-pointer SHA-256:
  `8158e0d2e194cc5882ff7434a4308a2006d716dd2dec39c724742f4bfee94b4b`;
- usage: 8,849 input, 5,808 output, 4,792 reasoning, 14,657 total tokens.

Combined inference usage was 16,410 input tokens and 7,018 output tokens,
including 5,576 reasoning tokens, for 23,428 total tokens. Of the input total,
16,404 tokens were reported as cache-write tokens. At the
[official model rates](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
of $5 per million input tokens, 1.25 times that rate for cache writes, and $30
per million output tokens, this corresponds to approximately $0.313 for the two
inference responses before provider rounding; the archived usage, not this
estimate, is the canonical evidence.

The model-access preflight and both inference responses returned HTTP 200 from
the exact requested model. Only `attempt-1` artifacts and one final pointer per
pass exist. No repair, retry, reroll, or ambiguous transmission occurred.

## Comparator and scope disposition

The seven frozen comparator records produced no applicable `LONG`: four
available records ended in predicate-mismatch `ABSTAIN`, the available veto was
inactive after predicate mismatches, one entry was unavailable because the
required twelve-hour acceleration feature was missing, and one record remained
blocked by absent buyer-breadth/exchange evidence. V7 therefore completed the
protocol, but it cannot support a model-versus-strategy performance claim.

V7 made zero Nansen requests and used zero Nansen credits. It performed one
OpenAI model-access preflight and one inference for each pass. No DEX, OHLCV,
entry, settlement, score, order, wallet, gas, or venue action occurred.

## Verification

- Offline `pilot-replay` reconstructed `decision_sealed`, 17 sealed artifacts,
  zero Nansen calls, and zero Nansen credits.
- Offline `pilot-check` verified the preregistration, snapshot and decision
  seals, copied inputs, comparator/decision artifacts, and every archived model
  request, response, metadata record, and final pointer.
- Terminal decision SHA-256:
  `57867a61b8f3f1f36dbe6a6bc469bd55584dd0be4326740605571d479b6444d6`.
- Terminal decision-seal SHA-256:
  `d90265e93cb1767efc92329a886819afca0023fb44af4d4138bdf8c10377c143`.
- `MODEL-RESULT.md` SHA-256:
  `8408aa21148660f244449ee6c781dee3d3bed9a30af954291bf3dc20e4080b94`.
- Exact-citation, request-identity, provider-usage, credential-marker, and
  predecessor-body comparisons passed.
- An independent offline audit confirmed the source provenance, single-variable
  protocol change, sealed hashes, attempts, citations, usage, comparator
  interpretation, zero-Nansen scope, identity blinding, and credential safety
  with no substantive finding. Its transient stale-handoff finding was resolved
  and rechecked before closeout.
- The full repository suite passed: 499 tests in 44.86 seconds.
- `python -m compileall -q src tests` passed. The staged whitespace check passed
  for every non-sealed file. `MODEL-RESULT.md` retains seven generator-produced
  Markdown hard breaks because editing that file would invalidate the terminal
  manifest and decision seal.

## Completion

The approved v7 observation is complete and immutable at `decision_sealed`.
Do not run `pilot-start` or `pilot-settle` for this bundle. Any further model,
reasoning, prompt, schema, budget, snapshot, comparator, or evaluation change
would be a new experiment requiring its own design and committed
preregistration.
