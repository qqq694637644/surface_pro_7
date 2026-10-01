from __future__ import annotations

from sp7_powerlab.measurement import (
    assess_measurement_trust,
    characterize_battery_gauge,
    measurement_energy_summary,
)


def row(ts, power, energy, *, status="Discharging", resume=False):
    return {
        "ts": float(ts),
        "battery_status": status,
        "battery_power_w": float(power),
        "battery_energy_wh": float(energy),
        "resume_grace": resume,
    }


def test_gap_aware_energy_and_endpoint_delta_are_consistent():
    rows = [
        row(0, 5.0, 30.0),
        row(30, 5.0, 30.0 - 5.0 * 30 / 3600.0),
        row(60, 5.0, 30.0 - 5.0 * 60 / 3600.0),
    ]
    summary = measurement_energy_summary(
        rows,
        max_gap_seconds=45,
        max_consistency_ratio=0.10,
        max_consistency_abs_wh=0.01,
    )
    assert abs(summary["integrated_energy_wh"] - (5.0 / 60.0)) < 1e-9
    assert abs(summary["battery_energy_delta_wh"] - (5.0 / 60.0)) < 1e-9
    assert summary["consistency_status"] == "CONSISTENT"
    assert summary["data_quality"] == "OK"


def test_charging_gap_cannot_be_hidden_by_endpoint_delta():
    rows = [
        row(0, 5.0, 30.0),
        row(10, 20.0, 30.1, status="Charging"),
        row(20, 5.0, 30.0),
    ]
    summary = measurement_energy_summary(rows, max_gap_seconds=45)
    assert summary["integrated_energy_wh"] is None
    assert summary["battery_energy_delta_wh"] is None
    assert summary["data_quality"] == "DATA_QUALITY_FAILURE"


def test_static_energy_gauge_is_recorded_as_quantized_not_fake_mismatch():
    rows = [
        row(0, 5.0, 30.0),
        row(10, 5.0, 30.0),
        row(20, 5.0, 30.0),
    ]
    summary = measurement_energy_summary(rows, max_gap_seconds=45)
    assert summary["data_quality"] == "OK"
    assert summary["consistency_status"] == "UNAVAILABLE_QUANTIZED"


def test_gauge_characterization_derives_minimum_arm_duration():
    rows = [
        row(0, 5.0, 30.0),
        row(60, 5.0, 29.9),
        row(120, 5.0, 29.9),
        row(180, 5.0, 29.8),
    ]
    result = characterize_battery_gauge(
        rows,
        expected_power_w=5.0,
        energy_quantum_multiplier=8.0,
    )
    assert abs(result["energy_quantum_wh"] - 0.1) < 1e-9
    assert result["energy_update_cadence_seconds"] == 90.0
    assert abs(result["minimum_arm_seconds_from_quantum"] - 576.0) < 1e-9


def test_gauge_characterization_records_power_quantization_and_cadence():
    rows = [
        row(0, 5.0, 30.0),
        row(10, 5.1, 29.99),
        row(20, 5.1, 29.98),
        row(30, 5.2, 29.97),
    ]
    result = characterize_battery_gauge(rows, expected_power_w=5.1)
    assert abs(result["power_quantum_w"] - 0.1) < 1e-9
    assert result["power_update_cadence_seconds"] == 15.0


def test_measurement_trust_derives_go_no_go_and_recommended_arm_duration():
    rows = [row(ts, 5.0, 30.0 - 5.0 * ts / 3600.0) for ts in range(0, 1201, 60)]
    result = assess_measurement_trust(
        rows,
        min_samples=3,
        min_observation_seconds=120,
        configured_min_arm_seconds=300,
        energy_quantum_multiplier=8.0,
        max_gap_seconds=90,
    )
    assert result["status"] == "READY"
    assert abs(result["recommended_min_arm_seconds"] - 480.0) < 1e-9
    assert len(result["consistency_windows"]) >= 2
    assert all(
        item["consistency_status"] == "CONSISTENT" for item in result["consistency_windows"]
    )

    blocked = assess_measurement_trust(
        rows[:2],
        min_samples=4,
        min_observation_seconds=600,
        configured_min_arm_seconds=300,
        energy_quantum_multiplier=8.0,
        max_gap_seconds=90,
    )
    assert blocked["status"] == "BLOCKED"
    assert "insufficient_discharging_samples" in blocked["reasons"]


def test_measurement_trust_does_not_reconnect_across_charging_gap():
    rows = [
        row(0, 5.0, 30.0),
        row(10, 20.0, 30.1, status="Charging"),
        row(20, 5.0, 30.0),
    ]
    result = assess_measurement_trust(
        rows,
        min_samples=2,
        min_observation_seconds=10,
        configured_min_arm_seconds=20,
        energy_quantum_multiplier=8.0,
        max_gap_seconds=45,
    )
    assert result["status"] == "BLOCKED"
    assert result["observation_seconds"] == 0.0
    assert result["energy_quality"]["data_quality"] == "DATA_QUALITY_FAILURE"
    assert result["gauge"]["energy_quantum_wh"] is None


def test_measurement_trust_does_not_reconnect_across_resume_grace():
    rows = [
        row(0, 5.0, 30.0),
        row(10, 5.0, 29.99, resume=True),
        row(20, 5.0, 29.98),
    ]
    result = assess_measurement_trust(
        rows,
        min_samples=2,
        min_observation_seconds=10,
        configured_min_arm_seconds=20,
        energy_quantum_multiplier=8.0,
        max_gap_seconds=45,
    )
    assert result["status"] == "BLOCKED"
    assert result["observation_seconds"] == 0.0


def test_measurement_trust_requires_segment_level_battery_consistency():
    rows = [
        row(0, 5.0, 30.00),
        row(60, 5.0, 29.99),
        row(120, 5.0, 29.99, status="Charging"),
        row(180, 5.0, 29.98),
        row(240, 5.0, 29.97),
    ]
    result = assess_measurement_trust(
        rows,
        min_samples=4,
        min_observation_seconds=120,
        configured_min_arm_seconds=60,
        energy_quantum_multiplier=1.0,
        max_gap_seconds=90,
        max_consistency_ratio=0.35,
        max_consistency_abs_wh=0.01,
        min_consistency_windows=2,
    )
    assert result["status"] == "BLOCKED"
    assert "battery_energy_consistency_failed" in result["reasons"]
    assert result["energy_quality"]["consistency_status"] == "DISCONTINUOUS_OBSERVATION"
    assert len(result["consistency_windows"]) == 2
    assert all(item["consistency_status"] == "MISMATCH" for item in result["consistency_windows"])


def test_gauge_quantum_uses_robust_repeated_step_not_single_jitter():
    rows = [
        row(0, 5.0, 30.000),
        row(60, 5.0, 29.999),
        row(120, 5.0, 29.899),
        row(180, 5.0, 29.799),
    ]
    result = characterize_battery_gauge(
        rows,
        expected_power_w=5.0,
        max_gap_seconds=90,
    )
    assert abs(result["energy_quantum_wh"] - 0.1) < 1e-9


def test_long_trusted_arm_requires_energy_endpoint_delta():
    rows = [
        row(0, 5.0, 30.0),
        row(60, 5.0, 30.0),
        row(120, 5.0, 30.0),
    ]
    summary = measurement_energy_summary(
        rows,
        max_gap_seconds=90,
        require_energy_delta=True,
    )
    assert summary["consistency_status"] == "UNAVAILABLE_QUANTIZED"
    assert summary["data_quality"] == "DATA_QUALITY_FAILURE"
