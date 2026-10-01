from __future__ import annotations

from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.evidence import hard_strata_key
from sp7_powerlab.lifecycle import LifecycleManager
from sp7_powerlab.scheduler import CandidateScheduler
from sp7_powerlab.storage import Database


def rollup():
    return {
        "bucket_ts": 1000.0,
        "battery_epoch": 1,
        "brightness_bucket": 40,
        "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
        "media_playing": False,
        "user_active": True,
        "remote_bucket": 0,
        "thermal_start": "COOL",
        "valid_seconds": 60,
        "avg_power_w": 5.0,
        "avg_rapl_w": 1.2,
        "local_compute_pressure": "LOW",
        "current_envelope": "INTERACTIVE_EFFICIENT",
        "trial_id": None,
    }


def make_scheduler(project_root: Path):
    config = load_config(project_root)
    config.data["automation"]["level"] = 2
    db = Database(project_root / "runtime/scheduler.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    lifecycle = LifecycleManager(db)
    lifecycle.synchronize_learning(calibration_valid=True)
    lifecycle.begin_optimization("test")
    lifecycle.synchronize_control(
        calibration_valid=True,
        hardware_writable=True,
        thermal_provider_healthy=True,
        core_telemetry_valid=True,
    )
    db.set_meta(
        "measurement_trust",
        {
            "status": "READY",
            "recommended_min_arm_seconds": 20.0,
        },
    )
    epoch = db.ensure_evidence_epoch(
        hard_identity_hash="hard",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
    item = rollup()
    strata = hard_strata_key(item)
    db.upsert_noise_distribution(
        {
            "evidence_epoch_id": epoch,
            "strata_key": strata,
            "window_seconds": 7 * 86400,
            "median_power_w": 5.0,
            "mad_power_w": 0.04,
            "p25_power_w": 4.95,
            "p75_power_w": 5.05,
            "p10_power_w": 4.9,
            "p90_power_w": 5.1,
            "noise_floor_w": 0.1,
            "sample_count": 10,
        }
    )
    return db, lifecycle, CandidateScheduler(config, db, registry), item


def test_scheduler_requires_assisted_trial_automation_level(project_root: Path):
    config = load_config(project_root)
    config.data["automation"]["level"] = 1
    db = Database(project_root / "runtime/scheduler-level.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        lifecycle = LifecycleManager(db)
        lifecycle.synchronize_learning(calibration_valid=True)
        lifecycle.begin_optimization("test")
        lifecycle.synchronize_control(
            calibration_valid=True,
            hardware_writable=True,
            thermal_provider_healthy=True,
            core_telemetry_valid=True,
        )
        db.set_meta("measurement_trust", {"status": "READY"})
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        item = rollup()
        db.upsert_noise_distribution(
            {
                "evidence_epoch_id": epoch,
                "strata_key": hard_strata_key(item),
                "window_seconds": 7 * 86400,
                "median_power_w": 5.0,
                "mad_power_w": 0.04,
                "p25_power_w": 4.95,
                "p75_power_w": 5.05,
                "p10_power_w": 4.9,
                "p90_power_w": 5.1,
                "noise_floor_w": 0.1,
                "sample_count": 10,
            }
        )
        result = CandidateScheduler(config, db, registry).candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is False
        assert "automation_level_does_not_allow_assisted_trials" in result["reasons"]
    finally:
        db.close()


def test_scheduler_energy_first_discrete_neighbors(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is True
        reasons = [candidate["reason"] for candidate in result["candidates"]]
        assert reasons[:3] == [
            "lower_max_perf",
            "more_efficient_epp",
            "disable_turbo",
        ]
        assert result["candidates"][0]["changes"] == {"max_perf_pct": 55}
        assert result["candidates"][1]["changes"] == {"epp": "power"}
    finally:
        db.close()


def test_scheduler_stable_and_investigation_are_hard_stop_rules(project_root: Path):
    db, lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        lifecycle.freeze("test stable")
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is False
        assert "learning_lifecycle_does_not_allow_exploration" in result["reasons"]

        lifecycle.reopen("test reopen")
        lifecycle.start_investigation(
            event_id="up-1",
            payload={"trigger": "UNEXPECTED_POWER"},
        )
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is False
        assert "investigation_active" in result["reasons"]
    finally:
        db.close()


def test_scheduler_ux_rescue_moves_toward_performance(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            ux_regression=True,
            rollup=item,
        )
        assert result["eligible"] is True
        changes = [candidate["changes"] for candidate in result["candidates"]]
        assert changes[0] == {"max_perf_pct": 65}
        assert {"epp": "balance_performance"} in changes
        assert {"turbo": False} not in changes
    finally:
        db.close()


def test_scheduler_frontier_does_not_repeat_proposed_candidate(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        first = scheduler.propose_next(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        second = scheduler.propose_next(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert first["selected"]["reason"] == "lower_max_perf"
        assert second["selected"]["reason"] == "more_efficient_epp"
        assert first["selected"]["candidate_key"] != second["selected"]["candidate_key"]
    finally:
        db.close()


def test_scheduler_refuses_to_explore_before_measurement_trust_is_ready(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        db.set_meta("measurement_trust", {"status": "BLOCKED"})
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is False
        assert "measurement_trust_not_ready" in result["reasons"]
    finally:
        db.close()


def test_scheduler_stops_when_noise_limited_arm_exceeds_daily_budget(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        scheduler.config.data["scheduler"]["max_candidate_minutes_per_day"] = 5
        epoch = str((db.active_evidence_epoch() or {})["epoch_id"])
        db.upsert_noise_distribution(
            {
                "evidence_epoch_id": epoch,
                "strata_key": hard_strata_key(item),
                "window_seconds": 7 * 86400,
                "median_power_w": 5.0,
                "mad_power_w": 0.25,
                "p25_power_w": 4.5,
                "p75_power_w": 5.5,
                "p10_power_w": 4.4,
                "p90_power_w": 5.6,
                "noise_floor_w": 0.5,
                "sample_count": 10,
            }
        )
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is False
        assert "minimum_arm_duration_exceeds_daily_candidate_budget" in result["reasons"]
        assert result["details"]["recommended_arm"]["noise_min_arm_seconds"] == 6000.0
    finally:
        db.close()


def test_scheduler_retries_inconclusive_candidate_only_within_attempt_budget(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        first = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )["candidates"][0]
        key = first["candidate_key"]
        epoch = str((db.active_evidence_epoch() or {})["epoch_id"])
        db.upsert_candidate_frontier(
            {
                "candidate_key": key,
                "evidence_epoch_id": epoch,
                "baseline_envelope": "INTERACTIVE_EFFICIENT",
                "status": "INCONCLUSIVE",
                "attempts": 1,
                "updated_ts": 1.0,
                "result": {},
            }
        )
        retry = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        retry_candidate = next(
            candidate for candidate in retry["candidates"] if candidate["candidate_key"] == key
        )
        assert retry_candidate["retrying_inconclusive"] is True
        assert retry_candidate["attempts"] == 1

        db.upsert_candidate_frontier(
            {
                "candidate_key": key,
                "evidence_epoch_id": epoch,
                "baseline_envelope": "INTERACTIVE_EFFICIENT",
                "status": "INCONCLUSIVE",
                "attempts": 2,
                "updated_ts": 2.0,
                "result": {},
            }
        )
        exhausted = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert all(candidate["candidate_key"] != key for candidate in exhausted["candidates"])
    finally:
        db.close()


def test_scheduler_enforces_weekly_trial_budget(project_root: Path):
    db, _lifecycle, scheduler, item = make_scheduler(project_root)
    try:
        for index in range(20):
            db.create_trial(
                {
                    "trial_id": f"historical-{index}",
                    "state": "REJECTED",
                    "kind": "envelope",
                    "baseline_envelope": "INTERACTIVE_EFFICIENT",
                    "candidate": {"index": index},
                    "target": {},
                    "validation": {},
                }
            )
        result = scheduler.candidates(
            baseline_name="INTERACTIVE_EFFICIENT",
            rollup=item,
        )
        assert result["eligible"] is False
        assert "weekly_trial_budget_exhausted" in result["reasons"]
    finally:
        db.close()
