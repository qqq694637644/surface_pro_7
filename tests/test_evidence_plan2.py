from __future__ import annotations

from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.evidence import (
    EvidenceEngine,
    NoiseTracker,
    build_crossover_episode,
    hard_strata_key,
)
from sp7_powerlab.storage import Database


def rollup(ts: float, power: float) -> dict:
    return {
        "bucket_ts": ts,
        "battery_epoch": 1,
        "brightness_bucket": 40,
        "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
        "media_playing": False,
        "user_active": True,
        "remote_bucket": 0,
        "thermal_start": "COOL",
        "system_fingerprint": "hard-fp",
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
        "trial_id": None,
    }


def measurement(arm: str, power: float) -> dict:
    return {
        "arm_id": f"id-{arm}",
        "trial_id": "trial-x",
        "arm": arm,
        "role": "candidate" if arm.startswith("B") else "baseline",
        "start_ts": 0.0,
        "end_ts": 120.0,
        "valid_seconds": 120.0,
        "avg_power_w": power,
        "integrated_energy_wh": power * 120.0 / 3600.0,
        "battery_energy_delta_wh": power * 120.0 / 3600.0,
        "data_quality": "OK",
    }


def make_epoch(db: Database) -> str:
    return db.ensure_evidence_epoch(
        hard_identity_hash="hard-fp",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={"test": True},
    )


def test_noise_tracker_freezes_reference_and_keeps_recent_distribution(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/evidence.sqlite3")
    try:
        epoch = make_epoch(db)
        tracker = NoiseTracker(config, db)
        values = (5.0, 5.1, 4.9, 5.0)
        for index, power in enumerate(values):
            item = rollup(100.0 + index * 60.0, power)
            db.add_rollup(item)
            tracker.observe_rollup(item, evidence_epoch_id=epoch)

        strata = hard_strata_key(rollup(999, 5.0))
        frozen = db.reference_baseline(epoch, strata)
        recent = db.noise_distribution(epoch, strata, window_seconds=7 * 86400)
        assert frozen is not None
        assert frozen["frozen"] is True
        assert frozen["sample_count"] == 3
        assert recent is not None
        assert recent["sample_count"] == 4

        frozen_median = frozen["median_power_w"]
        for index in range(5):
            item = rollup(1000.0 + index * 60.0, 6.0)
            db.add_rollup(item)
            tracker.observe_rollup(item, evidence_epoch_id=epoch)
        assert db.reference_baseline(epoch, strata)["median_power_w"] == frozen_median
        assert (
            db.noise_distribution(epoch, strata, window_seconds=7 * 86400)["median_power_w"]
            > frozen_median
        )
    finally:
        db.close()


def test_noise_floor_constrains_recommended_arm_duration(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/noise-arm.sqlite3")
    try:
        epoch = make_epoch(db)
        strata = hard_strata_key(rollup(100, 5.0))
        db.upsert_noise_distribution(
            {
                "evidence_epoch_id": epoch,
                "strata_key": strata,
                "window_seconds": 7 * 86400,
                "median_power_w": 5.0,
                "mad_power_w": 0.1,
                "p25_power_w": 4.8,
                "p75_power_w": 5.2,
                "p10_power_w": 4.7,
                "p90_power_w": 5.3,
                "noise_floor_w": 0.2,
                "sample_count": 10,
            }
        )
        arm = EvidenceEngine(config, db).recommended_arm_seconds(
            evidence_epoch_id=epoch,
            strata_key=strata,
            configured_min_seconds=300.0,
            gauge_min_seconds=600.0,
        )
        assert arm["noise_min_arm_seconds"] == 960.0
        assert arm["recommended_min_arm_seconds"] == 960.0
    finally:
        db.close()


def test_build_crossover_uses_fresh_revalidation_baseline():
    initial = build_crossover_episode(
        trial_id="trial-x",
        stage="initial",
        arm_measurements=[
            measurement("A1", 5.5),
            measurement("B1", 5.0),
            measurement("A2", 5.7),
        ],
        evidence_epoch_id="ee",
    )
    revalidation = build_crossover_episode(
        trial_id="trial-x",
        stage="revalidation",
        arm_measurements=[
            measurement("A1", 5.5),
            measurement("B1", 5.0),
            measurement("A2", 9.0),
            measurement("A3", 5.4),
            measurement("B2", 5.2),
        ],
        evidence_epoch_id="ee",
    )
    assert abs(initial["paired_effect_w"] - (5.0 - 5.6)) < 1e-9
    assert abs(revalidation["paired_effect_w"] - (5.2 - 5.4)) < 1e-9
    assert revalidation["baseline_arms"] == ["A3"]


def test_evidence_engine_contract_win_equivalent_inconclusive_and_lose(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/decisions.sqlite3")
    try:
        engine = EvidenceEngine(config, db)

        win = engine.decide(
            [
                {"episode_id": "e1", "valid": True, "paired_effect_w": -0.4},
                {"episode_id": "e2", "valid": True, "paired_effect_w": -0.3},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert win["verdict"] == "WIN"

        equivalent = engine.decide(
            [
                {"episode_id": "e3", "valid": True, "paired_effect_w": -0.03},
                {"episode_id": "e4", "valid": True, "paired_effect_w": 0.04},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert equivalent["verdict"] == "PRACTICALLY_EQUIVALENT"

        inconclusive = engine.decide(
            [
                {"episode_id": "e5", "valid": True, "paired_effect_w": -0.4},
                {"episode_id": "e6", "valid": True, "paired_effect_w": 0.05},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert inconclusive["verdict"] == "INCONCLUSIVE"

        lose = engine.decide(
            [
                {
                    "episode_id": "e7",
                    "valid": False,
                    "paired_effect_w": -0.6,
                    "constraint_reasons": ["thermal_regression"],
                },
                {"episode_id": "e8", "valid": True, "paired_effect_w": -0.5},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert lose["verdict"] == "LOSE"
        assert "thermal_regression" in lose["reasons"]

        data_quality = engine.decide(
            [
                {
                    "episode_id": "e9",
                    "valid": False,
                    "paired_effect_w": -0.8,
                    "constraint_reasons": ["data_quality_failure"],
                }
            ],
            minimum_useful_effect_w=0.1,
        )
        assert data_quality["verdict"] == "INCONCLUSIVE"
        assert "data_quality_failure" in data_quality["reasons"]

        boundary = engine.provisional(
            {
                "episode_id": "e10",
                "valid": True,
                "paired_effect_w": 0.1 - 1e-12,
                "constraint_reasons": [],
            },
            minimum_useful_effect_w=0.1,
        )
        assert boundary["verdict"] == "LOSE"
    finally:
        db.close()


def test_medium_effect_requires_more_crossover_evidence_than_large_effect(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/effect-budget.sqlite3")
    try:
        engine = EvidenceEngine(config, db)
        medium_two = engine.decide(
            [
                {"episode_id": "m1", "valid": True, "paired_effect_w": -0.15},
                {"episode_id": "m2", "valid": True, "paired_effect_w": -0.16},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert medium_two["verdict"] == "INCONCLUSIVE"
        assert medium_two["required_evidence_count"] == 3
        assert "evidence_budget_not_yet_satisfied" in medium_two["reasons"]

        medium_three = engine.decide(
            [
                {"episode_id": "m1", "valid": True, "paired_effect_w": -0.15},
                {"episode_id": "m2", "valid": True, "paired_effect_w": -0.16},
                {"episode_id": "m3", "valid": True, "paired_effect_w": -0.14},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert medium_three["verdict"] == "WIN"
        assert medium_three["required_evidence_count"] == 3

        large_two = engine.decide(
            [
                {"episode_id": "l1", "valid": True, "paired_effect_w": -0.30},
                {"episode_id": "l2", "valid": True, "paired_effect_w": -0.28},
            ],
            minimum_useful_effect_w=0.1,
        )
        assert large_two["verdict"] == "WIN"
        assert large_two["required_evidence_count"] == 2
    finally:
        db.close()
