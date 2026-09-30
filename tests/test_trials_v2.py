from pathlib import Path

import pytest

from sp7_powerlab.config import load_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.experiments import TrialManager
from sp7_powerlab.storage import Database
from sp7_powerlab.waste import brightness_bucket


class FakeActuator:
    def __init__(self):
        self.state = {
            "epp": {"policy0": "balance_power"},
            "max_perf_pct": 60,
            "turbo": True,
        }
        self.applies = 0
        self.restores = 0

    def snapshot(self):
        return {
            "epp": dict(self.state["epp"]),
            "max_perf_pct": self.state["max_perf_pct"],
            "turbo": self.state["turbo"],
        }

    def apply_envelope(self, env):
        before = self.snapshot()
        self.state = {
            "epp": {"policy0": env["epp"]},
            "max_perf_pct": env["max_perf_pct"],
            "turbo": env["turbo"],
        }
        self.applies += 1
        return {"before": before, "after": self.snapshot()}

    def restore(self, snapshot):
        self.state = {
            "epp": dict(snapshot["epp"]),
            "max_perf_pct": snapshot["max_perf_pct"],
            "turbo": snapshot["turbo"],
        }
        self.restores += 1
        return {"after": self.snapshot()}


def base_sample(ts, *, power=5.5, trial_id=None, trial_arm=None):
    return {
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
        "package_temp_c": 45,
        "temp_slope_c_per_min": 0.1,
        "rapl_power_10s_w": 2,
        "rapl_power_60s_w": 2,
        "rapl_power_300s_w": 2,
        "user_active": True,
        "media_playing": False,
        "network_rx_mbps": 0.1,
        "network_tx_mbps": 0.1,
        "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
        "latency_need": "MEDIUM",
        "local_compute_pressure": "LOW",
        "network_intensity": "LOW",
        "remote_hint": 0.0,
        "thermal_state": "COOL",
        "thermal_pressure": 0.1,
        "current_envelope": "INTERACTIVE_EFFICIENT",
        "thermal_override": False,
        "thermald_active": True,
        "resume_grace": False,
        "trial_id": trial_id,
        "trial_arm": trial_arm,
    }


def proposal():
    return {
        "kind": "envelope",
        "baseline_envelope": "INTERACTIVE_EFFICIENT",
        "changes": {"max_perf_pct": 50},
    }


def named_proposal():
    return {
        "kind": "envelope",
        "baseline_envelope": "INTERACTIVE_EFFICIENT",
        "candidate_envelope": "REMOTE_EFFICIENT",
    }


def make_manager(project_root: Path):
    config_path = project_root / "config/powerlab.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("level = 1", "level = 2"),
        encoding="utf-8",
    )
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    actuator = FakeActuator()
    return db, registry, actuator, TrialManager(config, db, registry, actuator)


def add_arm(db, trial_id, arm, start, power, **changes):
    for offset in (0, 10, 20):
        row = base_sample(
            start + offset,
            power=power,
            trial_id=trial_id,
            trial_arm=arm,
        )
        row.update(changes)
        db.add_sample(row)


def test_trial_requires_verified_current_baseline(project_root):
    db, registry, _actuator, manager = make_manager(project_root)
    try:
        current = base_sample(100)
        current["current_envelope"] = "ECO_IDLE"
        try:
            manager.start(proposal(), current)
        except Exception as exc:
            assert "current envelope" in str(exc)
        else:
            raise AssertionError("trial should require actual baseline envelope")
    finally:
        db.close()


def test_trial_refuses_low_battery(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        current = base_sample(100)
        current["battery_pct"] = 10
        try:
            manager.start(proposal(), current)
        except Exception as exc:
            assert "battery" in str(exc)
        else:
            raise AssertionError("trial should not start on low battery")
    finally:
        db.close()


def test_trial_fails_if_actual_hwp_state_is_not_verified_baseline(project_root):
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        actuator.state["max_perf_pct"] = 40
        with pytest.raises(Exception, match="actual HWP state"):
            manager.start(proposal(), base_sample(100))
    finally:
        db.close()


def test_named_envelope_trial_uses_stricter_minimum_block(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(named_proposal(), base_sample(100))
        assert trial["candidate"]["name"] == "REMOTE_EFFICIENT"
        assert trial["validation"]["min_block_seconds"] == 40.0
    finally:
        db.close()


def test_full_a_b_a_revalidation_and_promotion(project_root):
    db, registry, actuator, manager = make_manager(project_root)
    try:
        current = base_sample(100)
        trial = manager.start(proposal(), current)
        trial_id = trial["trial_id"]

        manager.tick(base_sample(100))
        assert db.get_trial(trial_id)["current_arm"] == "A1"
        add_arm(db, trial_id, "A1", 100, 5.5)
        manager.tick(base_sample(121))
        assert actuator.applies == 1

        manager.tick(base_sample(122))
        add_arm(db, trial_id, "B1", 122, 5.0)
        manager.tick(base_sample(143))
        assert actuator.restores == 1

        manager.tick(base_sample(144))
        add_arm(db, trial_id, "A2", 144, 5.5)
        manager.tick(base_sample(165))
        assert db.get_trial(trial_id)["state"] == "REVALIDATING"

        manager.tick(base_sample(166))
        assert db.get_trial(trial_id)["current_arm"] == "A3"
        add_arm(db, trial_id, "A3", 166, 5.5)
        manager.tick(base_sample(187))
        manager.tick(base_sample(188))
        add_arm(db, trial_id, "B2", 188, 5.0)
        manager.tick(base_sample(209))
        final = db.get_trial(trial_id)
        assert final["state"] == "VERIFIED_WINNER"

        promoted = manager.promote(trial_id)
        assert promoted["status"] == "VERIFIED"
        assert promoted["max_perf_pct"] == 50
        assert promoted["revision"] == 2
        assert db.get_trial(trial_id)["state"] == "PROMOTED"
        assert db.get_meta("current_envelope") is None
    finally:
        db.close()


def test_negative_feedback_rolls_back_active_trial(project_root):
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        manager.tick(base_sample(100))
        manager.feedback(
            "sluggish",
            trial_id=trial["trial_id"],
            notes="scrolling feels slow",
        )
        assert db.get_trial(trial["trial_id"])["state"] == "ROLLED_BACK"
        assert actuator.restores == 1
    finally:
        db.close()


def test_thermal_pressure_preempts_trial(project_root):
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        manager.tick(base_sample(100))
        hot = base_sample(110)
        hot["thermal_state"] = "THERMAL_PRESSURE"
        manager.tick(hot)
        assert db.get_trial(trial["trial_id"])["state"] == "ROLLED_BACK"
        assert actuator.restores == 1
    finally:
        db.close()


def test_passively_waiting_trial_rolls_back_without_restoring_old_snapshot(project_root):
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        rolled = manager.rollback(trial["trial_id"], "manual override")
        assert rolled["state"] == "ROLLED_BACK"
        assert actuator.restores == 0
    finally:
        db.close()


def test_candidate_caused_regressions_are_kept_as_outcomes(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        trial_id = trial["trial_id"]
        manager.tick(base_sample(100))
        add_arm(db, trial_id, "A1", 100, 5.5)
        manager.tick(base_sample(121))
        manager.tick(base_sample(122))

        add_arm(
            db,
            trial_id,
            "B1",
            122,
            5.0,
            cpu_psi=6.0,
            thermal_state="WARMING",
            thermal_pressure=0.45,
            local_compute_pressure="SUSTAINED",
            demand_region="ACTIVE|LAT_HIGH|CPU_MODERATE|NO_MEDIA|NET_LOW|LOCAL",
        )
        manager.tick(base_sample(143))
        manager.tick(base_sample(144))
        add_arm(db, trial_id, "A2", 144, 5.5)
        manager.tick(base_sample(165))

        final = db.get_trial(trial_id)
        assert final["state"] == "REJECTED"
        assert "cpu_psi_regression" in final["result"]["reasons"]
        assert "thermal_regression" in final["result"]["reasons"]
        assert "demand_backlog_regression" in final["result"]["reasons"]
        b1 = next(block for block in db.trial_blocks(trial_id) if block["arm"] == "B1")
        assert b1["avg_cpu_psi"] == 6.0
        assert b1["max_thermal_pressure"] == 0.45
    finally:
        db.close()


def test_external_window_change_during_candidate_restores_baseline(project_root):
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        trial_id = trial["trial_id"]
        manager.tick(base_sample(100))
        add_arm(db, trial_id, "A1", 100, 5.5)
        manager.tick(base_sample(121))
        manager.tick(base_sample(122))
        assert db.get_trial(trial_id)["current_arm"] == "B1"

        changed = base_sample(123)
        changed["brightness_pct"] = 70
        paused = manager.tick(changed)

        assert paused["state"] == "WAITING_FOR_COMPARABLE_WINDOW"
        assert paused["current_arm"] is None
        assert actuator.restores == 1
        assert actuator.state["max_perf_pct"] == 60
        assert db.get_meta("current_envelope") == "INTERACTIVE_EFFICIENT"
        assert db.trial_blocks(trial_id) == []
    finally:
        db.close()


def test_remote_session_change_during_candidate_restores_baseline(project_root):
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        trial_id = trial["trial_id"]
        manager.tick(base_sample(100))
        add_arm(db, trial_id, "A1", 100, 5.5)
        manager.tick(base_sample(121))
        manager.tick(base_sample(122))
        assert db.get_trial(trial_id)["current_arm"] == "B1"

        changed = base_sample(123)
        changed["remote_hint"] = 0.8
        paused = manager.tick(changed)

        assert paused["state"] == "WAITING_FOR_COMPARABLE_WINDOW"
        assert actuator.restores == 1
        assert db.get_meta("current_envelope") == "INTERACTIVE_EFFICIENT"
    finally:
        db.close()


def test_brightness_delta_is_enforced_inside_same_bucket(project_root):
    config_path = project_root / "config/powerlab.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "max_brightness_delta = 10",
            "max_brightness_delta = 5",
        ),
        encoding="utf-8",
    )
    db, _registry, actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        trial_id = trial["trial_id"]
        manager.tick(base_sample(100))
        add_arm(db, trial_id, "A1", 100, 5.5)
        manager.tick(base_sample(121))
        manager.tick(base_sample(122))

        changed = base_sample(123)
        changed["brightness_pct"] = 49
        assert brightness_bucket(changed["brightness_pct"]) == brightness_bucket(40)
        paused = manager.tick(changed)
        assert paused["state"] == "WAITING_FOR_COMPARABLE_WINDOW"
        assert actuator.restores == 1
    finally:
        db.close()


def test_revalidation_must_win_independently_of_good_b1(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(proposal(), base_sample(100))
        trial_id = trial["trial_id"]
        manager.tick(base_sample(100))
        add_arm(db, trial_id, "A1", 100, 5.5)
        manager.tick(base_sample(121))
        manager.tick(base_sample(122))
        add_arm(db, trial_id, "B1", 122, 4.5)
        manager.tick(base_sample(143))
        manager.tick(base_sample(144))
        add_arm(db, trial_id, "A2", 144, 6.5)
        manager.tick(base_sample(165))
        after_initial = db.get_trial(trial_id)
        assert after_initial["state"] == "REVALIDATING"
        assert after_initial["result"]["initial_result"]["verdict"] == "CANDIDATE_WINNER"

        manager.tick(base_sample(166))
        add_arm(db, trial_id, "A3", 166, 5.5)
        manager.tick(base_sample(187))
        manager.tick(base_sample(188))
        add_arm(db, trial_id, "B2", 188, 5.6)
        manager.tick(base_sample(209))

        final = db.get_trial(trial_id)
        assert final["state"] == "REJECTED"
        assert final["result"]["initial_result"]["verdict"] == "CANDIDATE_WINNER"
        assert final["result"]["revalidation_result"]["verdict"] == "REJECT"
        assert final["result"]["revalidation_result"]["baseline_avg_power_w"] == 5.5
        assert "power_saving_too_small" in final["result"]["revalidation_result"]["reasons"]
    finally:
        db.close()


def test_trial_schema_rejects_llm_target_override(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        forged = {
            **proposal(),
            "target": {
                "battery_epoch": 999,
                "brightness_bucket": 0,
            },
        }
        errors = manager.validate_proposal(forged)
        assert errors
        assert any("Additional properties" in error for error in errors)
    finally:
        db.close()


def test_trial_schema_rejects_validation_override(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        requested = {
            **proposal(),
            "validation": {
                "min_block_seconds": 60,
            },
        }
        errors = manager.validate_proposal(requested)
        assert errors
        assert any("Additional properties" in error for error in errors)
    finally:
        db.close()


def test_promoted_trial_negative_feedback_restores_previous_verified_revision(
    project_root,
):
    db, registry, actuator, manager = make_manager(project_root)
    try:
        baseline = registry.get("INTERACTIVE_EFFICIENT")
        assert baseline is not None
        candidate = registry.candidate_from_change(
            "INTERACTIVE_EFFICIENT",
            {"max_perf_pct": 50},
        )
        snapshot = actuator.snapshot()
        trial_id = "trial-promoted-feedback"
        db.create_trial(
            {
                "trial_id": trial_id,
                "state": "VERIFIED_WINNER",
                "kind": "envelope",
                "baseline_envelope": "INTERACTIVE_EFFICIENT",
                "candidate": candidate,
                "target": {},
                "validation": {},
                "snapshot": snapshot,
                "current_arm": None,
                "arm_start_ts": None,
                "result": {"verdict": "CANDIDATE_WINNER"},
            }
        )
        db.set_meta("current_envelope", "INTERACTIVE_EFFICIENT")
        manager.promote(trial_id)
        actuator.state["max_perf_pct"] = 50
        db.set_meta("current_envelope", "INTERACTIVE_EFFICIENT")

        manager.feedback(
            "sluggish",
            trial_id=trial_id,
            notes="regression discovered after promotion",
        )

        rejected = db.get_trial(trial_id)
        restored = registry.get("INTERACTIVE_EFFICIENT")
        assert rejected["state"] == "REJECTED"
        assert "negative_user_feedback_after_promotion" in rejected["result"]["reasons"]
        assert restored["status"] == "VERIFIED"
        assert restored["source"] == "rollback"
        assert restored["max_perf_pct"] == 60
        assert restored["revision"] >= 3
        assert actuator.state["max_perf_pct"] == 60
    finally:
        db.close()
