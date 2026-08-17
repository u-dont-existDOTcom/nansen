from datetime import datetime, timedelta, timezone

import pytest

from src.nansen_signal_lab.signals import SignalError, build_signal_features


def _fixture():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = [100, 99, 98, 100, 101, 100, 99]
    holdings = [100, 110, 120, 115, 125, 140, 150]
    holders = [2, 3, 4, 4, 5, 6, 7]
    return tuple(
        {"timestamp": start + timedelta(hours=i), "price_usd": p,
         "token_amount": amount, "holders_count": breadth, "asset_id": "x"}
        for i, (p, amount, breadth) in enumerate(zip(prices, holdings, holders))
    )


def test_build_signal_features_uses_disjoint_trailing_windows():
    rows = build_signal_features(
        _fixture(), horizons=(2,), source_experiment_id="exp-1",
        feature_set_version="community-signals-v1",
    )
    row = rows[6]
    assert row["holdings_change_2h_pct"] == pytest.approx(20.0)
    assert row["price_return_2h_pct"] == pytest.approx(-1.9801980198019802)
    assert row["positive_holdings_delta_hours_2h"] == 2
    assert row["negative_holdings_delta_hours_2h"] == 0
    assert row["accumulation_persistence_2h"] == 1.0
    assert row["distribution_persistence_2h"] == 0.0
    assert row["holdings_velocity_2h_pct_per_hour"] == pytest.approx(10.0)
    assert row["holdings_acceleration_2h_pct_per_hour"] == pytest.approx(
        10.0 - ((125 / 120 - 1) * 100 / 2)
    )
    assert row["holder_count_change_2h"] == 2
    assert row["accumulation_retention_2h"] == 1.0
    assert row["flow_price_divergence_2h_pct"] == pytest.approx(
        20.0 - (-1.9801980198019802)
    )
    assert row["market_phase_2h"] == "accumulation_divergence"


def test_missing_intermediate_hour_blanks_horizon():
    rows = list(_fixture())
    rows[5:] = [{**row, "timestamp": row["timestamp"] + timedelta(hours=1)} for row in rows[5:]]
    row = build_signal_features(tuple(rows), horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[6]
    assert row["holdings_change_2h_pct"] is None
    assert row["market_phase_2h"] == "unavailable"


def test_acceleration_needs_two_disjoint_windows():
    row = build_signal_features(_fixture(), horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[3]
    assert row["holdings_velocity_2h_pct_per_hour"] is not None
    assert row["holdings_acceleration_2h_pct_per_hour"] is None


def test_zero_gross_positive_deltas_make_retention_unavailable():
    rows = tuple({**row, "token_amount": 100 - i} for i, row in enumerate(_fixture()))
    row = build_signal_features(rows, horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[6]
    assert row["accumulation_retention_2h"] is None


def test_missing_holder_count_only_blanks_holder_breadth():
    rows = list(_fixture())
    rows[5] = {**rows[5], "holders_count": None}
    row = build_signal_features(tuple(rows), horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[6]
    assert row["holder_count_change_2h"] is None
    assert row["holdings_change_2h_pct"] == pytest.approx(20)


@pytest.mark.parametrize(
    "holders_count",
    [float("nan"), float("inf"), -1, True, "not-a-number"],
    ids=["nan", "infinity", "negative", "boolean", "malformed"],
)
def test_signal_boundary_rejects_invalid_holder_counts(holders_count):
    """Fails if invalid breadth input emits non-finite output or reaches subtraction."""
    rows = tuple({**row, "holders_count": holders_count} for row in _fixture())

    with pytest.raises(SignalError, match="invalid holders_count"):
        build_signal_features(
            rows,
            horizons=(2,),
            source_experiment_id="e",
            feature_set_version="community-signals-v1",
        )


def test_flat_holdings_have_flat_phase():
    rows = tuple({**row, "token_amount": 100, "price_usd": 100 + i} for i, row in enumerate(_fixture()))
    row = build_signal_features(rows, horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[6]
    assert row["market_phase_2h"] == "flat"


def test_output_is_trailing_whitelist_only():
    row = build_signal_features(tuple({**item, "selection_score": 1, "buyer": "x", "forward_label": 2} for item in _fixture()), horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[6]
    assert not any(any(marker in key for marker in ("selection_", "buyer", "forward_", "mfe_", "mae_")) for key in row)


def test_output_excludes_selection_and_unknown_availability_fields():
    item = {**_fixture()[6], "selection_available": True, "mystery_available": True}
    row = build_signal_features((item,), horizons=(2,), source_experiment_id="e", feature_set_version="community-signals-v1")[0]
    assert "selection_available" not in row
    assert "mystery_available" not in row


@pytest.mark.parametrize("kwargs", [
    {"feature_set_version": "community-signals-v2", "horizons": (2,)},
    {"feature_set_version": "community-signals-v1", "horizons": (0,)},
    {"feature_set_version": "community-signals-v1", "horizons": (2, 2)},
])
def test_rejects_unsupported_feature_set_and_horizons(kwargs):
    with pytest.raises(SignalError):
        build_signal_features(_fixture(), source_experiment_id="e", **kwargs)
