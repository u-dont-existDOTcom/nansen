# Prospective GPT Pilot: Contract-Context Protocol v4

Status: preregistration protocol for new observations only.

This document is a narrow normative overlay on the completed-flow v3 design,
whose immutable SHA-256 is
`9e8d0db9434fbc561e0e6ca4990c852e905a968d7a35124ede6bf68d2aaba037`.
The v3 completed-hour range, v2 account fallback, and every inherited base-pilot
requirement remain normative. Earlier sealed observations retain their original
design hashes and must never be rewritten or reinterpreted under v4.

## Motivation

The v3 live evidence matched the pinned OpenAPI but exposed three normalization
assumptions that differed from the documented response models:

- `TGMFlowIntelligenceResponse.data` is a list and contained one record;
- an optional warnings field may be absent or null when there is no warning; and
- token-information metrics are nested under `spot_metrics` and
  `token_details`, while flow-intelligence metrics use segment-specific names.

An object-only/flat normalizer either stops or silently discards the context that
the protocol intended to show GPT. Version 4 binds the exact interpretation.

## Version-4 context normalization

Flow intelligence must be an object whose `data` is a list containing exactly
one object. Empty, multiple, non-list, or non-object records are terminal. Only
these documented finite numeric fields are admitted:

- `public_figure_net_flow_usd`, `public_figure_avg_flow_usd`,
  `public_figure_wallet_count`;
- `top_pnl_net_flow_usd`, `top_pnl_avg_flow_usd`, `top_pnl_wallet_count`;
- `whale_net_flow_usd`, `whale_avg_flow_usd`, `whale_wallet_count`;
- `smart_trader_net_flow_usd`, `smart_trader_avg_flow_usd`,
  `smart_trader_wallet_count`;
- `exchange_net_flow_usd`, `exchange_avg_flow_usd`,
  `exchange_wallet_count`; and
- `fresh_wallets_net_flow_usd`, `fresh_wallets_avg_flow_usd`,
  `fresh_wallets_wallet_count`.

Token information must contain an object `data`. From `spot_metrics`, admit and
rename only `volume_total_usd` to `volume_usd`, `buy_volume_usd`,
`sell_volume_usd`, `total_buys` to `buy_count`, `total_sells` to `sell_count`,
`unique_buyers`, `unique_sellers`, `liquidity_usd`, and `total_holders` to
`holders_count`. From `token_details`, admit only `market_cap_usd`, `fdv_usd`,
`circulating_supply`, and `total_supply`. Present admitted values must be finite
numbers and booleans are forbidden. No name, symbol, address, logo, deployment
date, website, or social field may enter normalized or blinded data.

For every context or flow response, an absent or null `warnings` value means an
empty list. A present warnings value must be a list of strings; all other shapes
are terminal. Normalized availability records preserve whether warnings are
present and their exact count, while raw warning text remains outside the GPT
snapshot. Unknown fields are ignored by the explicit whitelist.

## Unchanged scope

The live OpenAPI must still match exactly. Flow pages and rows remain strict,
the account-baseline proof remains narrow, and the model, prompts, blinding,
comparators, paper execution, four-hour outcome, ceilings, retries, lifecycle,
immutability, reporting, and advancement rule are unchanged. This remains one
paper-only observation and cannot establish advancement.
