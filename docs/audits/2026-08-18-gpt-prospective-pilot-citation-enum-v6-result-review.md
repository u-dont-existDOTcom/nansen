# GPT prospective pilot citation-enum v6 result review

Date: 2026-08-18
Bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-citation-enum-v6/`
Terminal manifest SHA-256: `655a446e994be368cf4364577a5e349c108f9a673ba0233e4ccc900cf439a4db`
Terminal report SHA-256: `868f7c2e667b57da3295d399c2de54f4059efaed5368c68e0584a84e4beaa0e7`

## Verdict

The terminal `unscorable` bundle passes offline replay and hash-chain checking.
It is valid evidence that the exact-citation enum correction produced one
locally valid `gpt-5.6-sol` Pass-1 response. It is not evidence about a
completed Pass 2, paper returns, strategy quality, or advancement. Pass 2
returned an incomplete provider response after exhausting the fixed output
allowance, so the no-reroll protocol correctly sealed v6 terminally. The v6
observation is immutable and must not be retried or rewritten.

## Exact-citation result

Both archived strict schemas enumerated the same 124 admissible paths for each
of `evidence_for` and `evidence_against`. Pass 1 completed on its first attempt
and used only literal members of that enum. It returned:

- action: `ABSTAIN`;
- confidence: `0.8`;
- expected four-hour direction: `FLAT`; and
- risk flags: `FLOW_CONCENTRATION`, `EXECUTION_RISK`, and
  `MODEL_UNCERTAINTY`.

The response cited nine distinct supporting paths and nine distinct opposing
paths, with no duplicates within or across the lists. Its rationale treated
the four-hour accumulation/price-divergence signal as constructive but found it
insufficient against the latest-hour distribution reversal, the twelve-hour
distribution regime, negative top-PnL flow, and unresolved warnings. This is a
model-protocol observation over the historical sealed snapshot, not a trading
recommendation.

Pass 1 has response ID
`resp_0399bb1688389857016a83d758ccf087d28698aa2561aacb2b`, request ID
`req_d52fce5c110c4a12a35c90edf088dee6`, and exact response SHA-256
`1faf5778e663672db1d92f0c742956d8bdf8797d14a2c3de7c7bbc0e9e52c777`.
Its validated final pointer has SHA-256
`ad89745c55ae2f82df1821366575674b1ae65b99c246a679bbc79c66895dd8c0`.

## Pass-2 terminal failure

Pass 2 returned HTTP 200 from the exact requested model but provider status
`incomplete`, with `incomplete_details.reason=max_output_tokens`. All 4,000
output tokens were reasoning tokens. The response contained reasoning items but
no structured `output_text`, so there was no locally valid Pass-2 object and no
Pass-2 final pointer. A provider-incomplete response is terminal under the
preregistered v6 rules; the single repair path is available only for a
completed, parseable structured response that fails local validation.

Pass 2 has response ID
`resp_01ddda497a119080016a83d76a4c5c819ea99cf2ba71cddaaf`, request ID
`req_933c6024edf2446c9154c48e46b0c935`, and exact response SHA-256
`80a4663380b51e672ccec08e2ee492706f74733faf6903b7fb9b47e015fc1b6d`.
No second attempt was transmitted.

Across the two inference responses, provider usage was 16,372 input tokens and
4,881 output tokens, including 4,501 reasoning tokens, for 21,253 total tokens.
The copied identity-blinded snapshot retained SHA-256
`84b6fef7e5ce0e5100312f3d308c13e4c98651e28d13e6e40d86582fba6fb20c`.

## Comparator and scope disposition

The seven frozen comparator records produced no applicable LONG decision: four
were predicate-mismatch abstentions, one was an inactive predicate-mismatch
veto, one was unavailable because a required twelve-hour acceleration feature
was absent, and the wallet-breadth/exchange record remained blocked by missing
historical evidence. Because Pass 2 did not complete, v6 cannot make a final
model-versus-comparator claim.

The successor made zero Nansen requests and used zero Nansen credits. It made
one OpenAI model-access preflight and one inference for each pass. No repair,
DEX, OHLCV, entry, settlement, score, order, wallet, gas, or venue action
occurred, and no `MODEL-RESULT.md` was produced.

## Verification

- Offline `pilot-replay` reconstructed terminal stage `unscorable`, 16 sealed
  artifacts, zero Nansen calls, and zero Nansen credits.
- Offline `pilot-check` verified the manifest, both seals, and every sealed
  source, comparator, request, response, response-metadata, and final-pointer
  artifact.
- An independent exact-citation audit confirmed that all 18 Pass-1 citations
  are unique members of the 124-path archived enum and that provider usage
  totals reconcile.
- Independent terminal-evidence review found no bundle or result-review issue;
  its separate stale-handoff finding was corrected before closeout.
- The full repository suite passed: 496 tests in 37.82 seconds.
- `python -m compileall -q src tests` and `git diff --check` passed.

## Completion

V6 was preregistered as the one final model-only successor observation. Its
terminal failure is part of the result, not authorization for an automatic v7.
Any future change to output allowance, reasoning effort, prompt, schema, or
model protocol would be a new experiment requiring its own owner-approved
design and committed preregistration. The current exact-citation objective is
complete at this terminal boundary.

The terminal seal SHA-256 is
`6fb3d151336ed7cf684c5c656fec6421ae316e3aa58cd0723cdd9160d06a0550`.
