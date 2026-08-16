from src.nansen_signal_lab.metrics import accumulation_class, flow_market_cap_ratio
from src.nansen_signal_lab.cli import normalize_price_change


def test_ratio():
    assert round(flow_market_cap_ratio(485_900, 17_900_000), 4) == 0.0271


def test_buckets():
    assert accumulation_class(5) == "early"
    assert accumulation_class(15) == "middle"
    assert accumulation_class(15.01) == "momentum"


def test_price_change_decimal_return_is_converted_to_percent():
    assert round(normalize_price_change(0.16713988082593417), 6) == 16.713988
    assert round(normalize_price_change(2.1637279928926847), 6) == 216.372799
