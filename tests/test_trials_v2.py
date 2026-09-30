from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.experiments import TrialManager
from sp7_powerlab.storage import Database


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
        "validation": {
            "min_block_seconds": 20,
            "settle_seconds": 0,
            "min_power_saving_w": 0.1,
        },
    }


def named_proposal():
    return {
        "kind": "envelope",
        "baseline_envelope": "INTERACTIVE_EFFICIENT",
        "candidate_envelope": "REMOTE_EFFICIENT",
        "validation": {
            "min_block_seconds": 120,
            "settle_seconds": 0,
        },
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


def add_arm(db, trial_id, arm, start, power):
    for offset in (0, 10, 20):
        db.add_sample(
            base_sample(
                start + offset,
                power=power,
                trial_id=trial_id,
                trial_arm=arm,
            )
        )


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
        trial = manager.start(proposal(), base_sample(100))
        actuator.state["max_perf_pct"] = 40
        manager.tick(base_sample(100))
        failed = db.get_trial(trial["trial_id"])
        assert failed["state"] == "FAILED"
        assert "baseline" in failed["last_error"]
    finally:
        db.close()


def test_named_envelope_trial_uses_stricter_minimum_block(project_root):
    db, _registry, _actuator, manager = make_manager(project_root)
    try:
        trial = manager.start(named_proposal(), base_sample(100))
        assert trial["candidate"]["name"] == "REMOTE_EFFICIENT"
        assert trial["validation"]["min_block_seconds"] == 600.0
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
        manager.tick(base_sample(167))
        add_arm(db, trial_id, "B2", 167, 5.0)
        manager.tick(base_sample(188))
        final = db.get_trial(trial_id)
        assert final["state"] == "VERIFIED_WINNER"

        promoted = manager.promote(trial_id)
        assert promoted["status"] == "VERIFIED"
        assert promoted["max_perf_pct"] == 50
        assert promoted["revision"] == 2
        assert db.get_trial(trial_id)["state"] == "PROMOTED"
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
