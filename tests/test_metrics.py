import sp7_powerlab.metrics as metrics
from sp7_powerlab.metrics import battery_snapshot, deep_idle_time_us


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


def test_battery_snapshot_normalizes_negative_power_now(tmp_path, monkeypatch):
    battery = tmp_path / "BAT0"
    battery.mkdir()
    (battery / "power_now").write_text("-6500000", encoding="utf-8")
    (battery / "energy_now").write_text("20000000", encoding="utf-8")
    (battery / "energy_full").write_text("40000000", encoding="utf-8")
    (battery / "energy_full_design").write_text("43200000", encoding="utf-8")
    (battery / "status").write_text("Discharging", encoding="utf-8")
    (battery / "capacity").write_text("50", encoding="utf-8")

    monkeypatch.setattr(metrics, "battery_dir", lambda: battery)
    snapshot = battery_snapshot()
    assert snapshot["power_w"] == 6.5
    assert snapshot["energy_wh"] == 20.0
    assert snapshot["status"] == "Discharging"
