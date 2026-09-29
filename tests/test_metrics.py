from sp7_powerlab.metrics import deep_idle_time_us


def test_deep_idle_time_us_counts_c6_and_deeper():
    states = [
        {"name": "POLL", "time_us": 10},
        {"name": "C1", "time_us": 100},
        {"name": "C6", "time_us": 300},
        {"name": "C10", "time_us": 700},
    ]
    assert deep_idle_time_us(states) == 1000


def test_deep_idle_time_us_returns_none_without_deep_state():
    assert deep_idle_time_us([{"name": "C1", "time_us": 50}]) is None
