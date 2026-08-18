from __future__ import annotations


def flow_market_cap_ratio(netflow_usd: float | int | None, market_cap_usd: float | int | None) -> float | None:
    if netflow_usd is None or market_cap_usd in (None, 0):
        return None
    return float(netflow_usd) / float(market_cap_usd)


def accumulation_class(price_change_pct: float | int | None) -> str:
    """Initial hypothesis buckets; thresholds are intentionally explicit and editable."""
    if price_change_pct is None:
        return "unknown"
    x = float(price_change_pct)
    # Nansen's price_change may be returned either as percent points (e.g. 33)
    # or a fraction in some contexts. Normalization is handled by the caller.
    if x <= 5.0:
        return "early"
    if x > 15.0:
        return "momentum"
    return "middle"
