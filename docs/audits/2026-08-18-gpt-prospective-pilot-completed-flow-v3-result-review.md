# GPT prospective pilot completed-flow v3 result review

Date: 2026-08-18  
Bundle: `research/experiments/2026-08-18-gpt-prospective-pilot-completed-flow-v3/`  
Terminal manifest SHA-256: `17632c3697f04d7ce2db7b5100f738918b5456c25764552ec46a1dec946a2fc3`  
Terminal report SHA-256: `45d5a54d6eddb71ece1b2cafec7ff459e602c080af95508e49e951e1497022e4`

## Verdict

The terminal `unscorable` bundle passes offline replay and hash-chain checking.
It is not evidence about GPT signal quality because the run stopped during
snapshot normalization before either structured GPT pass. The observation is
immutable and must not be retried under the same v3 protocol.

## What v3 established

- The live OpenAPI checksum, exact-model preflight, account-baseline fallback,
  and per-call credit accounting all passed again.
- The completed-hour request correction worked. Smart-Money and exchange each
  returned 24 ordered rows; their final bucket ended at
  `2026-08-18T00:00:00Z` and every returned row declared
  `is_complete=true`. Their archived response SHA-256 values are
  `2aebeec1707caa5622540660271f6100759a12102b9f80cecd845954a85b7f15`
  and
  `914a7c5b320c3f778911b81b7bd2894148a0c4e8f8033b28aa74c13b8d725dbe`.
- Five paid Nansen calls used five credits, moving the provider balance from 85
  to 80. No DEX, OHLCV, structured GPT, order, wallet, or venue action followed
  the invalid snapshot.

## Terminal mismatch

The matched live OpenAPI defines `TGMFlowIntelligenceResponse.data` as an array
of `TGMFlowIntelligence` records. The live response contained exactly one such
record, while the local normalizer required `data` to be an object. Its exact
response SHA-256 is
`3c70af9026092323104eff9b3ed26557ee2bd4350612688abf46b0efafc0c469`.
The runner stopped with `flow_intelligence response data must be an object`.

Read-only in-memory replay of the archived bytes then found two related contract
interpretation gaps before any additional spend:

- token-information and exchange-flow responses used `warnings: null` to mean no
  warnings, while the normalizer accepted only an array; and
- token-information metrics are nested under `spot_metrics` and `token_details`,
  while flow-intelligence uses the documented segment-specific field names. The
  original flat whitelist would discard those valid metrics and give GPT empty
  context even after a singleton unwrap.

## Future-run correction

A separately versioned future protocol may normalize the matched contract as
follows, with exact tests against the archived shapes before another live call:

- require flow-intelligence `data` to be a list containing exactly one object;
- treat absent or null warnings as an empty warning list, preserve non-empty
  string lists, and reject every other warnings shape;
- map an explicit identity-safe numeric whitelist from token-information
  `spot_metrics` and `token_details` into normalized metrics;
- admit the explicit documented numeric flow-intelligence fields and no unknown
  fields; and
- retain the v3 completed-hour range and every strict temporal/completeness rule.

The terminal seal was recorded at `2026-08-18T00:36:57.299653Z`, binding budget
journal head
`aca1f837dde42297144e89fb2a70527b38886552a0dbfaeed9df0882a1a45a1f`
and snapshot
`9c7d8a72356340b6471fd1534bea48c56924b5fb0d614468104b131d16e79ca8`.
