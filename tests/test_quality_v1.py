from sp7_powerlab.quality import comparability_score, data_quality, integrate_energy


def sample(ts, power=6.0, status="Discharging", scene="reading"):
    return {
        "ts": ts,
        "power_w": power,
        "battery_status": status,
        "context_scene": scene,
    }


def test_time_weighted_energy_ignores_charge_and_large_gaps():
    samples = [
        sample(0, 4.0),
        sample(10, 6.0),
        sample(20, 8.0, status="Charging"),
        sample(100, 5.0),
        sample(110, 5.0),
    ]
    result = integrate_energy(samples, max_gap_seconds=30)
    assert result["valid_duration_s"] == 20
    assert round(result["energy_wh"], 6) == round((5 * 10 + 5 * 10) / 3600, 6)
    assert result["invalid_status_segments"] == 1
    assert result["gaps"] == 1


def test_quality_never_accepts_missing_core_evidence():
    result = data_quality(
        [sample(0, power=None), sample(10, power=None), sample(20, power=None)],
        min_valid_seconds=10,
    )
    assert result["valid"] is False
    assert "whole_device_power_unavailable" in result["reasons"]


def test_comparability_rejects_different_scene():
    result = comparability_score(
        {"scene": "reading", "brightness_pct": 30, "kernel": "a"},
        {"scene": "compile", "brightness_pct": 30, "kernel": "a"},
    )
    assert result["comparable"] is False
