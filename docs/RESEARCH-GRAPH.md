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

    src_nansen_cli_builds["Source: Nansen CLI Builds community catalog"]
    src_nansen_divergence["Source lead: Nansen Divergence"]
    src_smrr["Source lead: Smart Money Rotation Radar (README labels MIT; no separate license file detected)"]
    lead_supply_control["Lead: exchange withdrawal / supply control"]
    lead_four_hour_rotation["Lead: four-hour rotation cadence"]
    hyp_persistence_acceleration["Hypothesis: persistence and acceleration may describe regimes"]
    hyp_buyer_breadth["Hypothesis: wallet-level buyer breadth may add evidence"]
    hyp_exchange_outflow_confirmation["Hypothesis: labelled exchange outflow may confirm co-movement"]
    req_point_in_time_state["Requirement: point-in-time state, liquidity, and execution evidence"]
    exp_20260816_community_signal_shadow["Experiment: 2026-08-16 community-signal shadow (discovery)"]

    src_nansen_cli_builds -->|inspired_by| lead_supply_control
    src_nansen_cli_builds -->|inspired_by| lead_four_hour_rotation
    src_nansen_divergence -->|inspired_by| hyp_persistence_acceleration
    src_smrr -->|inspired_by| hyp_buyer_breadth
    src_smrr -->|inspired_by| hyp_exchange_outflow_confirmation
    lead_supply_control -->|not_yet_replicated| hyp_exchange_outflow_confirmation
    lead_four_hour_rotation -->|not_yet_replicated| hyp_persistence_acceleration
    hyp_persistence_acceleration -->|tests| exp_20260816_community_signal_shadow
    exp_20260816_community_signal_shadow -->|blocked_by| req_point_in_time_state
    hyp_buyer_breadth -->|requires| req_point_in_time_state
    hyp_exchange_outflow_confirmation -->|requires| req_point_in_time_state
    hyp_buyer_breadth -->|blocked_by| req_point_in_time_state
    hyp_exchange_outflow_confirmation -->|blocked_by| req_point_in_time_state
```

Observation evidence:

- [`obs_cdxr_flat_accumulation`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#cdxr-label-maturity)
- [`obs_weakness_continues`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`obs_momentum_participation`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`obs_toad_no_reversal`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`obs_cate_mixed`](../research/experiments/2026-08-16-seven-token-pilot/REPORT.md#event-weighted-timing-analysis)
- [`src_nansen_cli_builds`](https://release.nansen.ai/en/help/articles/6399546-nansen-cli-builds) — community claims are leads only.
- [`src_nansen_divergence`](https://github.com/Ridwannurudeen/nansen-divergence) — direct source repository, which declares MIT; no community claim is treated as validated.
- [`src_smrr`](https://github.com/Iziedking/Narrative-pulse) — direct Smart Money Rotation Radar source repository; its README labels the project MIT, while no separate license file was detected. No code or expression is copied.

[Powered by Nansen API](https://nansen.ai/). Public evidence follows Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide).
