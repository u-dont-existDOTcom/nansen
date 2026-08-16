# Research evidence graph

Stable node IDs preserve the lineage from the frozen hypothesis to observations, limitations, and the next test.

```mermaid
flowchart LR
    hyp_smart_money_timing["Hypothesis: accumulation timing may matter"]
    exp_20260816_seven_token_pilot["Experiment: 2026-08-16 seven-token pilot"]
    obs_cdxr_flat_accumulation["Observation: CDXR bucket available at 22:00 after flat-price accumulation; late 24h label immature"]
    obs_weakness_continues["Observation: AI-HEDGE-FUND and MONGO accumulation into continued weakness"]
    obs_momentum_participation["Observation: CHEAT.SH and PRISMA momentum participation"]
    obs_toad_no_reversal["Observation: TOAD accumulation without observed reversal"]
    obs_cate_mixed["Observation: CATE mixed, modest momentum participation"]
    lim_selected_endpoint_window["Limitations: selected cohort, endpoint scope, bucket-end availability, short window, right censoring"]
    next_cdxr_fixed_window["Next test: fixed-window CDXR 24h follow-up"]

    hyp_smart_money_timing --> exp_20260816_seven_token_pilot
    exp_20260816_seven_token_pilot --> obs_cdxr_flat_accumulation
    exp_20260816_seven_token_pilot --> obs_weakness_continues
    exp_20260816_seven_token_pilot --> obs_momentum_participation
    exp_20260816_seven_token_pilot --> obs_toad_no_reversal
    exp_20260816_seven_token_pilot --> obs_cate_mixed
    exp_20260816_seven_token_pilot --> lim_selected_endpoint_window
    obs_cdxr_flat_accumulation --> next_cdxr_fixed_window
    obs_weakness_continues --> lim_selected_endpoint_window
    obs_momentum_participation --> lim_selected_endpoint_window
    obs_toad_no_reversal --> lim_selected_endpoint_window
    obs_cate_mixed --> lim_selected_endpoint_window
```

Observation evidence:

- [`obs_cdxr_flat_accumulation`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#cdxr-label-maturity)
- [`obs_weakness_continues`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`obs_momentum_participation`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`obs_toad_no_reversal`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`obs_cate_mixed`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)

[Powered by Nansen API](https://nansen.ai/). Public evidence follows Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide).
