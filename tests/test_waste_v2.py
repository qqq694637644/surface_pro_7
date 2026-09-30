from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.storage import Database
from sp7_powerlab.waste import WasteDetector


def rollup(ts, power):
    return {
        "bucket_ts": ts,
        "battery_epoch": 1,
        "brightness_bucket": 40,
        "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
        "media_playing": False,
        "remote_bucket": 0,
        "thermal_start": "COOL",
        "system_fingerprint": "fp",
        "valid_seconds": 60,
        "avg_power_w": power,
        "median_power_w": power,
        "p90_power_w": power,
        "p95_power_w": power,
        "avg_rapl_w": 2.0,
        "avg_cpu_psi": 0.1,
        "avg_io_psi": 0.1,
        "max_thermal_pressure": 0.1,
        "local_compute_pressure": "LOW",
    }


def test_low_demand_high_power_creates_waste_incident(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        for i, power in enumerate((5.0, 5.1, 4.9, 5.0)):
            db.add_rollup(rollup(100 + i * 60, power))
        for i in range(4):
            db.add_rollup(rollup(700 + i * 60, 6.5))
        current = rollup(940, 6.5)
        db.add_rollup(current)
        incident = WasteDetector(config, db).detect(current)
        assert incident is not None
        assert incident["current_power_w"] == 6.5
        assert incident["baseline_p90_w"] < 5.2
    finally:
        db.close()


def test_normal_power_does_not_create_waste(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        for i, power in enumerate((5.0, 5.1, 4.9, 5.0)):
            db.add_rollup(rollup(100 + i * 60, power))
        for i in range(4):
            db.add_rollup(rollup(700 + i * 60, 5.2))
        current = rollup(940, 5.2)
        db.add_rollup(current)
        assert WasteDetector(config, db).detect(current) is None
    finally:
        db.close()
