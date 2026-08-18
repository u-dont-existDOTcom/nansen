# Prospective GPT Pilot: Completed-Flow Protocol v3

Status: preregistration protocol for new observations only.

This document is a narrow normative overlay on the account-baseline v2 design,
whose immutable SHA-256 is
`9f8d334b43d9fff56321fe68224ba5729e4c076616d96d3aa9de1eddaa7deffe`.
The v2 account fallback and every unchanged requirement inherited there from the
base pilot design remain normative. All earlier sealed observations retain their
original design hashes and must never be rewritten or reinterpreted under v3.

## Motivation

The v2 observation froze a non-hour-aligned cutoff and sent that instant as each
flow request's `date.to`. Nansen correctly returned the current hourly bucket,
explicitly marked `is_complete=false`, after 24 completed buckets. The strict
validator correctly stopped, but the request range made the failure inevitable.

## Version-3 completed-hour range

Let `available_at` remain the immutable snapshot cutoff. Define
`completed_boundary` as `available_at` floored to the current UTC hour. For both
Smart-Money and exchange flow requests, use exactly:

- `date.from = completed_boundary - 25 hours`; and
- `date.to = completed_boundary - 1 microsecond`.

This requests the 25 hourly bucket starts in the half-open interval
`[completed_boundary - 25h, completed_boundary)` while expressing it through
Nansen's inclusive RFC 3339 date bounds. `available_at` itself remains unchanged
for all evidence timing, freshness, selection, normalization, and decision-seal
checks.

Strict response validation is not relaxed. Both pages must still be final first
pages, every returned row must carry literal `is_complete=true`, every
`bucket_end` must be no later than the original `available_at`, ordering must be
strictly increasing, and exact lag/gap rules remain unchanged. Any current,
future, incomplete, missing-completeness, duplicate, or non-final evidence is
terminal `unscorable`.

## Unchanged scope

The account-baseline fallback, full OpenAPI hash preflight, exact model, prompts,
selection, blinding, comparators, paper execution, four-hour outcome, ceilings,
retries, lifecycle, artifact immutability, reporting, and advancement rule are
unchanged. This remains one paper-only observation and cannot establish strategy
advancement.
