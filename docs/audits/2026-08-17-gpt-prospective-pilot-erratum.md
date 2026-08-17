# Prospective GPT pilot terminal-audit erratum

Date: 2026-08-17

Disposition: **unusable observation; no model or strategy comparison occurred**

Terminal manifest SHA-256: `43b6da8703d14b2190bb10c93ade00f7ca3c0cee363c18a4eb77c975d717cfc3`

Terminal report SHA-256: `70ff849a6d535889d85e666d3bba03763913583c717ade02ae105f26b7241f54`

## Sealed outcome

The public Nansen OpenAPI document matched the preregistered full-document SHA-256. The next and only transmitted request was the exact `GET /v1/models/gpt-5.6-sol` model-access preflight. OpenAI returned HTTP 401 with provider code `invalid_api_key`; the runner archived the response and sealed the bundle terminally `unscorable`.

No Nansen credentialed request, Nansen billable call, Nansen credit, token selection, normalized snapshot, GPT inference, comparator decision, paper fill, or outcome collection occurred. Offline replay reports zero calls, zero credits, seven artifacts, and no headline result.

## Audit finding

The configured `OPENAI_API_KEY` value was nonempty but did not begin with the OpenAI API-key prefix `sk-`. The transport constructor checked only for absence, so it transmitted an obviously wrong credential class instead of rejecting it locally. This contradicts the preregistered requirement to validate both credentials locally before provider work.

Because the response was received after transmission, the no-reroll rule correctly made this bundle immutable and terminal. The terminal files must not be rewritten, deleted, or presented as evidence about `gpt-5.6-sol` signal quality. A corrected credential requires a separately named and separately preregistered successor observation after the client fix is verified.

## Evidence and disclosure audit

- `pilot-replay` and `pilot-check` pass against the terminal seal.
- The report reason, response status, provider error code, model request path, raw-response hash, seal hash chain, and zero-call budget snapshot agree.
- The complete configured OpenAI and Nansen credential values do not occur in any bundle artifact.
- The provider's exact error body contains only its own masked API-key representation. The archived response metadata contains a transient Cloudflare bot-management cookie, not an OpenAI account credential; the bundle remains local and is not automatically pushed or published.
- No order, signing, wallet, venue-submission, custody, gas, executable-route, or capital-movement field exists in the terminal evidence.

## Remediation

Add a local format guard that rejects non-`sk-`, whitespace-bearing, or implausibly short OpenAI API-key values with `transmitted=false`, plus a response-header allowlist that excludes `set-cookie` and credential-shaped headers from future metadata. Commit and verify that future-run fix separately from this immutable failed observation.
