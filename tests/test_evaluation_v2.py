from sp7_powerlab.evaluation import compare_arm_constraints, summarize_block


def row(ts, power, *, psi=0.1, thermal=0.1, playing=False):
    return {
        "ts": ts,
        "battery_status": "Discharging",
        "battery_power_w": power,
        "cpu_psi": psi,
        "io_psi": 0.1,
        "thermal_pressure": thermal,
        "media_playing": playing,
        "brightness_pct": 40.0,
        "demand_region": "ACTIVE",
        "resume_grace": False,
    }


def test_block_summary_is_gap_aware():
    summary = summarize_block([row(0, 5), row(10, 5), row(100, 5)], max_gap_seconds=20)
    assert summary["valid_seconds"] == 10


def test_block_average_is_time_weighted():
    rows = [
        row(0, 4.0),
        row(10, 4.0),
        row(40, 8.0),
    ]
    summary = summarize_block(rows, max_gap_seconds=60)
    # 10 seconds at 4W plus 30 seconds with trapezoidal 4->8W gives 5.5W.
    assert abs(summary["avg_power_w"] - 5.5) < 1e-9


def test_candidate_wins_only_when_power_drops_without_regressions():
    baseline = [
        {"avg_power_w": 5.5, "avg_cpu_psi": 0.2, "avg_io_psi": 0.2, "max_thermal_pressure": 0.2}
    ]
    candidate = [
        {"avg_power_w": 5.1, "avg_cpu_psi": 0.3, "avg_io_psi": 0.2, "max_thermal_pressure": 0.22}
    ]
    result = compare_arm_constraints(baseline, candidate)
    assert result["constraint_status"] == "PASS"
    assert result["power_delta_w"] < 0


def test_thermal_regression_rejects_even_if_power_is_lower():
    baseline = [
        {"avg_power_w": 5.5, "avg_cpu_psi": 0.2, "avg_io_psi": 0.2, "max_thermal_pressure": 0.2}
    ]
    candidate = [
        {"avg_power_w": 4.8, "avg_cpu_psi": 0.2, "avg_io_psi": 0.2, "max_thermal_pressure": 0.5}
    ]
    result = compare_arm_constraints(baseline, candidate)
    assert result["constraint_status"] == "VIOLATION"
    assert "thermal_regression" in result["reasons"]
