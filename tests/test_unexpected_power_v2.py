from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.evidence import reference_strata_key
from sp7_powerlab.storage import Database
from sp7_powerlab.unexpected_power import UnexpectedPowerDetector


def rollup(ts, power, *, epoch=None):
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
        "current_envelope": "INTERACTIVE_EFFICIENT",
        "evidence_epoch_id": epoch,
        "reference_eligible": True,
    }


def make_epoch(db):
    return db.ensure_evidence_epoch(
        hard_identity_hash="fp",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )


def test_low_demand_high_power_creates_unexpected_power_event(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        epoch = make_epoch(db)
        for i, power in enumerate((5.0, 5.1, 4.9, 5.0)):
            db.add_rollup(rollup(100 + i * 60, power, epoch=epoch))
        for i in range(4):
            db.add_rollup(rollup(700 + i * 60, 6.5, epoch=epoch))
        current = rollup(940, 6.5, epoch=epoch)
        db.add_rollup(current)
        incident = UnexpectedPowerDetector(config, db).detect(current)
        assert incident is not None
        assert incident["current_power_w"] == 6.5
        assert incident["baseline_p90_w"] < 5.2
    finally:
        db.close()


def test_normal_power_does_not_create_waste(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        epoch = make_epoch(db)
        for i, power in enumerate((5.0, 5.1, 4.9, 5.0)):
            db.add_rollup(rollup(100 + i * 60, power, epoch=epoch))
        for i in range(4):
            db.add_rollup(rollup(700 + i * 60, 5.2, epoch=epoch))
        current = rollup(940, 5.2, epoch=epoch)
        db.add_rollup(current)
        assert UnexpectedPowerDetector(config, db).detect(current) is None
    finally:
        db.close()


def test_frozen_reference_uses_real_p90_and_noise_floor(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/frozen.sqlite3")
    try:
        epoch = make_epoch(db)
        current = rollup(940, 6.5, epoch=epoch)
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-frozen",
                "created_ts": 1.0,
                "evidence_epoch_id": epoch,
                "strata_key": reference_strata_key(current),
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 5.0,
                "mad_power_w": 0.02,
                "p25_power_w": 4.9,
                "p75_power_w": 5.1,
                "p90_power_w": 5.4,
                "noise_floor_w": 0.3,
                "sample_count": 20,
                "frozen": True,
            }
        )
        baseline = UnexpectedPowerDetector(config, db)._baseline(current)
        assert baseline["p90_power_w"] == 5.4
        assert baseline["noise_floor_w"] == 0.3
    finally:
        db.close()
