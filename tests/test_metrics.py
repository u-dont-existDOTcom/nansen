from src.nansen_signal_lab.metrics import accumulation_class, flow_market_cap_ratio


def test_ratio():
    assert round(flow_market_cap_ratio(485_900, 17_900_000), 4) == 0.0271


def test_buckets():
    assert accumulation_class(5) == "early"
    assert accumulation_class(15) == "middle"
    assert accumulation_class(15.01) == "momentum"
