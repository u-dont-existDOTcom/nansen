# Prospective GPT pilot live-run log

## 2026-08-17T17:01:21Z — stopped before transmission

Reviewed implementation commit: `a35b23c718b76a922a8b535aef494ae4ea78aca5`

Rollback branch: `codex/gpt-prospective-pilot-pre-live-20260817`

The first explicit `pilot-start` invocation stopped locally while constructing the OpenAI transport because `OPENAI_API_KEY` was not set. The exception was classified `transmitted=false`; no public-contract fetch, OpenAI request, credentialed Nansen request, or billable call began.

Post-stop offline replay and filesystem inspection confirmed:

```json
{"artifact_count":0,"gpt_beats_frozen_strategies":null,"nansen_calls":0,"nansen_credits":0,"stage":"preregistered"}
```

`REPORT.md`, `raw/`, selected-token data, model artifacts, and outcome artifacts remain absent. The host process environment and local environment files were checked without printing values: the existing project credential file names only `NANSEN_API_KEY`; no configured `OPENAI_API_KEY` was found. The Codex ChatGPT OAuth token was not repurposed because the preregistration requires the OpenAI Responses API and immutable API evidence.

Resume condition: make a valid OpenAI API key available as `OPENAI_API_KEY` to the reviewed `pilot-start` process. Preserve the current preregistered bundle and rerun the same fixed command; do not initialize a replacement bundle or substitute a model/provider.

## 2026-08-17T22:13:21Z — terminal preflight outcome

Both environment variables were present and the reviewed command was resumed. The public Nansen OpenAPI bytes matched the pinned hash. The OpenAI model-access preflight was then transmitted once and returned HTTP 401 with provider code `invalid_api_key`.

The runner archived the exact response evidence and sealed the original bundle terminally `unscorable` with zero Nansen calls and zero Nansen credits. Post-result audit found that the configured value was not shaped like an OpenAI API key and should have been rejected before transmission. The immutable observation is therefore marked unusable in `2026-08-17-gpt-prospective-pilot-erratum.md`; it must not be rerun with a corrected key.
