# Prospective GPT Pilot: Account-Baseline Protocol v2

Status: preregistration protocol for new observations only.

This document is a narrow normative overlay on
`2026-08-17-gpt-prospective-pilot-design.md`, whose immutable SHA-256 is
`a3625d519dc8bb9b7f34563aecc998994dc7f26e5f1f75db6abb7110ef1b1521`.
Every requirement in that design remains unchanged except the account-baseline
rule below. The two already sealed observations continue to bind the original
design and must never be rewritten or reinterpreted under this version.

## Motivation and observed provider behavior

The successor observation matched the preregistered full Nansen OpenAPI SHA-256
exactly. Its successful `GET /api/v1/account` response reported plan `free` and
`credits_remaining: 90` in the JSON body and included
`X-Nansen-Credits-Cost: 0`, but omitted `X-Nansen-Credits-Used` and
`X-Nansen-Credits-Remaining`. The matched contract assigns the account endpoint
zero credits on both plans, says that it consumes no credits, and defines the
body balance as the account's remaining credits. Repeating the strict request
would add no information.

## Version-2 account-baseline rule

The strict three-header path remains preferred. A new version-2 observation may
use the account-only fallback if and only if all of these conditions hold:

- the unauthenticated live OpenAPI bytes exactly match the pinned full-document
  SHA-256 before any credentialed Nansen call;
- the request is exactly `GET /api/v1/account` with no body and the response is
  a successful JSON object;
- `plan` is exactly `free` or `pro`, and `credits_remaining` is a non-boolean
  non-negative integer of at least ten;
- `X-Nansen-Credits-Cost` is present, well formed, and exactly zero;
- `X-Nansen-Credits-Used`, if present, is well formed and exactly zero;
- `X-Nansen-Credits-Remaining`, if present, is well formed and exactly equals
  the body `credits_remaining`; and
- no other credit header is malformed.

When either optional header is absent, effective account usage is deterministically
recorded as zero and the effective provider balance is taken from the body. The
ledger records a `confirmed_zero` account reservation with effective cost zero,
usage zero, and the body balance. The exact raw response and metadata remain
unchanged. A write-once `derived/account-baseline.json` records the rule version,
matched OpenAPI SHA-256, response-metadata SHA-256, observed header values, body
values, inferred effective values, and durable-write timestamp; it is included
in the next stage seal.

Any failure of these conditions remains ambiguous, consumes the conservative
reservation, terminates the observation `unscorable`, and permits no subsequent
Nansen call. This fallback is forbidden for every paid endpoint: all successful
paid responses still require explicit, well-formed cost, used, and remaining
headers with the original balance and cost-drift checks. Recovery must adopt the
same exact response and derivation artifacts without retransmission.

## Unchanged scope

The model, prompts, token selection, blinding, comparators, paper execution,
four-hour outcome, ten-call and ten-credit ceilings, retry policy, lifecycle,
hash-chain, reporting, and advancement rule are unchanged. This remains one
paper-only observation and cannot establish strategy advancement.
