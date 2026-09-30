from sp7_powerlab.analytics import battery_usage_summary


def sample(ts, power, energy, pct, *, active=True):
    return {
        "ts": ts,
        "battery_status": "Discharging",
        "battery_power_w": power,
        "battery_energy_wh": energy,
        "battery_pct": pct,
        "user_active": active,
        "resume_grace": False,
        "battery": {
            "energy_full_wh": 40.0,
            "energy_full_design_wh": 43.2,
        },
    }


def test_battery_summary_integrates_energy_and_projects_runtime():
    rows = [
        sample(0, 5.0, 30.0, 75.0),
        sample(1800, 5.0, 27.5, 68.75),
        sample(3600, 5.0, 25.0, 62.5),
    ]
    result = battery_usage_summary(rows, max_gap_seconds=2000)
    assert result["valid_discharge_seconds"] == 3600
    assert abs(result["avg_power_w"] - 5.0) < 1e-9
    assert abs(result["energy_used_wh"] - 5.0) < 1e-9
    assert abs(result["projected_remaining_hours"] - 5.0) < 1e-9
    assert abs(result["projected_full_hours"] - 8.0) < 1e-9
    assert abs(result["drain_w_from_energy_slope"] - 5.0) < 1e-9
    assert abs(result["percent_slope_per_hour"] + 12.5) < 1e-9


def test_suspend_gap_is_not_counted_as_usage():
    rows = [
        sample(0, 5.0, 30.0, 75.0),
        sample(10, 5.0, 29.99, 74.9),
        sample(1000, 5.0, 29.0, 72.0),
    ]
    result = battery_usage_summary(rows, max_gap_seconds=45)
    assert result["valid_discharge_seconds"] == 10
    assert result["energy_used_wh"] < 0.02
