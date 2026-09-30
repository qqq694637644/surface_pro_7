import time
from pathlib import Path

import pytest

from sp7_powerlab.calibration import CalibrationManager
from sp7_powerlab.config import load_config, load_machine
from sp7_powerlab.storage import Database


def add_phase_samples(db, start_ts, *, phase, power, temp, rapl, slope=0.5):
    for index in range(4):
        ts = start_ts
        db.add_sample(
            {
                "ts": ts,
                "wall_ts": str(ts),
                "battery_status": "Discharging",
                "battery_pct": 80,
                "battery_power_w": power,
                "battery_energy_wh": 30,
                "battery_epoch": 1,
                "brightness_pct": 40,
                "cpu_usage": 10,
                "cpu_psi": 0.1,
                "io_psi": 0.1,
                "memory_psi": 0.0,
                "load1": 0.2,
                "avg_freq_khz": 1000000,
                "epp": "balance_power",
                "max_perf_pct": 60,
                "turbo": True,
                "package_temp_c": temp + index,
                "temp_slope_c_per_min": slope,
                "rapl_power_10s_w": rapl,
                "rapl_power_60s_w": rapl,
                "rapl_power_300s_w": rapl,
                "user_active": phase != "cold_idle",
                "media_playing": phase == "media",
                "network_rx_mbps": 0.1,
                "network_tx_mbps": 0.1,
                "demand_region": "ACTIVE",
                "latency_need": "MEDIUM",
                "local_compute_pressure": "LOW",
                "network_intensity": "LOW",
                "remote_hint": 0.0,
                "thermal_state": "COOL",
                "thermal_pressure": 0.1,
                "current_envelope": None,
                "thermal_override": False,
                "trial_id": None,
                "trial_arm": None,
            }
        )


def test_all_calibration_phases_make_machine_valid(project_root: Path):
    machine_path = project_root / "config/machine.toml"
    text = machine_path.read_text(encoding="utf-8")
    text = text.replace("valid = true", "valid = false").replace(
        'completed_phases = ["cold_idle", "normal_interactive", "media", "bounded_burst"]',
        "completed_phases = []",
    )
    machine_path.write_text(text, encoding="utf-8")

    db = Database(project_root / "runtime/db.sqlite3")
    manager = CalibrationManager(project_root, db, load_config(project_root))
    try:
        phases = [
            ("cold_idle", 3.5, 35.0, 1.5, -0.2),
            ("normal_interactive", 5.5, 50.0, 5.0, 0.5),
            ("media", 6.0, 52.0, 4.5, 0.4),
            ("bounded_burst", 9.0, 60.0, 10.0, 3.0),
        ]
        for phase, power, temp, rapl, slope in phases:
            active = manager.start(phase)
            add_phase_samples(
                db,
                active["start_ts"],
                phase=phase,
                power=power,
                temp=temp,
                rapl=rapl,
                slope=slope,
            )
            manager.finish()

        machine = load_machine(project_root)
        assert machine["calibration"]["valid"] is True
        assert machine["calibration"]["version"] >= 2
        assert machine["thermal"]["pressure_temp_c"] > machine["thermal"]["soft_temp_c"]
        assert machine["baselines"]["idle_battery_w"] > 0
    finally:
        db.close()


def test_invalidation_resets_phase_order_even_with_old_db_history(project_root: Path):
    db = Database(project_root / "runtime/db.sqlite3")
    manager = CalibrationManager(project_root, db, load_config(project_root))
    try:
        for phase in ("cold_idle", "normal_interactive"):
            run = db.start_calibration(phase)
            db.finish_calibration(run["run_id"], {"phase": phase})
        manager.invalidate("new battery")
        try:
            manager.start("normal_interactive")
        except RuntimeError as exc:
            assert "cold_idle" in str(exc)
        else:
            raise AssertionError("invalidated calibration must restart phase order")
    finally:
        db.close()


def add_calibration_row(db, ts, *, status, power, sensor="/sys/package"):
    db.add_sample(
        {
            "ts": ts,
            "wall_ts": str(ts),
            "battery_status": status,
            "battery_pct": 80,
            "battery_power_w": power,
            "battery_energy_wh": 30,
            "battery_epoch": 1,
            "package_temp_c": 35.0,
            "thermal_sensor_path": sensor,
            "temp_slope_c_per_min": -0.1,
            "rapl_power_60s_w": 1.5,
            "user_active": False,
            "media_playing": False,
            "resume_grace": False,
        }
    )


def calibration_with_minimum(project_root: Path, seconds: int):
    path = project_root / "config/powerlab.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "cold_idle_min_seconds = 0",
            f"cold_idle_min_seconds = {seconds}",
        ),
        encoding="utf-8",
    )
    db = Database(project_root / "runtime/calibration-duration.sqlite3")
    return db, CalibrationManager(project_root, db, load_config(project_root))


def test_calibration_requires_valid_discharging_duration(project_root: Path):
    db, manager = calibration_with_minimum(project_root, 20)
    try:
        active = manager.start("cold_idle")
        start = time.time() - 100
        db.conn.execute(
            "UPDATE calibration_runs SET start_ts=? WHERE run_id=?",
            (start, active["run_id"]),
        )
        db.conn.commit()
        for offset in (0, 30, 60):
            add_calibration_row(db, start + offset, status="Charging", power=35.0)
        for offset in (80, 85, 90):
            add_calibration_row(db, start + offset, status="Discharging", power=3.5)

        with pytest.raises(RuntimeError, match="valid Discharging"):
            manager.finish()
    finally:
        db.close()


def test_calibration_statistics_exclude_charging_power(project_root: Path):
    db, manager = calibration_with_minimum(project_root, 20)
    try:
        active = manager.start("cold_idle")
        start = time.time() - 100
        db.conn.execute(
            "UPDATE calibration_runs SET start_ts=? WHERE run_id=?",
            (start, active["run_id"]),
        )
        db.conn.commit()
        for offset in (0, 10, 20):
            add_calibration_row(db, start + offset, status="Charging", power=40.0)
        for offset in (40, 55, 70):
            add_calibration_row(db, start + offset, status="Discharging", power=3.5)

        result = manager.finish()["result"]
        assert result["sample_count"] == 3
        assert result["valid_discharge_seconds"] == 30
        assert result["battery_power_median_w"] == 3.5
        machine = load_machine(project_root)
        assert machine["thermal"]["sensor_path"] == "/sys/package"
    finally:
        db.close()


def test_calibration_does_not_count_ac_interval_between_discharging_samples(
    project_root: Path,
):
    db, manager = calibration_with_minimum(project_root, 20)
    try:
        active = manager.start("cold_idle")
        start = time.time() - 100
        db.conn.execute(
            "UPDATE calibration_runs SET start_ts=? WHERE run_id=?",
            (start, active["run_id"]),
        )
        db.conn.commit()
        add_calibration_row(db, start, status="Discharging", power=3.5)
        add_calibration_row(db, start + 10, status="Charging", power=40.0)
        add_calibration_row(db, start + 20, status="Charging", power=40.0)
        add_calibration_row(db, start + 30, status="Discharging", power=3.5)
        add_calibration_row(db, start + 40, status="Discharging", power=3.5)

        with pytest.raises(RuntimeError, match="valid Discharging"):
            manager.finish()
    finally:
        db.close()
