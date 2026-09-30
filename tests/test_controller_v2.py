from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.controller import BatteryLifeController
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.storage import Database


class FakeActuator:
    def __init__(self):
        self.applied = []
        self.state = {"epp": {}, "max_perf_pct": 60, "turbo": True}

    def snapshot(self):
        return dict(self.state)

    def apply_envelope(self, env):
        before = dict(self.state)
        self.state = {
            "epp": {"policy0": env["epp"]},
            "max_perf_pct": env["max_perf_pct"],
            "turbo": env["turbo"],
        }
        self.applied.append(env["name"])
        return {"before": before, "after": dict(self.state)}

    def restore(self, snapshot):
        self.state = dict(snapshot)
        return {"after": dict(self.state)}


def make_controller(project_root: Path, *, writable=True, calibrated=True):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    actuator = FakeActuator()
    controller = BatteryLifeController(
        config,
        db,
        registry,
        actuator,
        hardware_writable=writable,
        calibration_valid=calibrated,
        clock=lambda: 100.0,
    )
    return config, db, registry, actuator, controller


def sample(**changes):
    base = {
        "ts": 100.0,
        "thermald_active": True,
        "resume_grace": False,
        "battery_power_w": 5.2,
        "package_temp_c": 45.0,
        "rapl_power_60s_w": 2.0,
        "epp": "balance_power",
        "max_perf_pct": 60,
    }
    base.update(changes)
    return base


def demand(**changes):
    base = {
        "user_active": True,
        "media_continuity": 0.0,
        "remote_hint": 0.0,
        "local_compute_pressure": "LOW",
    }
    base.update(changes)
    return base


def thermal(state="COOL"):
    return {"state": state, "pressure": 0.1}


def test_default_active_uses_interactive_envelope(project_root):
    _config, db, _registry, actuator, controller = make_controller(project_root)
    try:
        decision = controller.step(sample(), demand(), thermal())
        assert decision.action == "APPLIED"
        assert decision.applied_envelope == "INTERACTIVE_EFFICIENT"
        assert actuator.applied == ["INTERACTIVE_EFFICIENT"]
    finally:
        db.close()


def test_idle_uses_eco(project_root):
    _config, db, _registry, _actuator, controller = make_controller(project_root)
    try:
        result = controller.step(sample(), demand(user_active=False), thermal())
        assert result.desired_envelope == "ECO_IDLE"
    finally:
        db.close()


def test_media_precedes_remote(project_root):
    _config, db, _registry, _actuator, controller = make_controller(project_root)
    try:
        result = controller.step(
            sample(),
            demand(media_continuity=1.0, remote_hint=0.9),
            thermal(),
        )
        assert result.desired_envelope == "MEDIA_EFFICIENT"
    finally:
        db.close()


def test_thermal_pressure_preempts_everything(project_root):
    _config, db, _registry, _actuator, controller = make_controller(project_root)
    try:
        result = controller.step(
            sample(),
            demand(media_continuity=1.0),
            thermal("THERMAL_PRESSURE"),
        )
        assert result.desired_envelope == "THERMAL_SAFE"
    finally:
        db.close()


def test_thermal_pressure_preempts_manual_override(project_root):
    _config, db, _registry, _actuator, controller = make_controller(project_root)
    try:
        controller.set_override("REMOTE_EFFICIENT")
        result = controller.step(
            sample(),
            demand(remote_hint=0.9),
            thermal("THERMAL_PRESSURE"),
        )
        assert result.desired_envelope == "THERMAL_SAFE"
    finally:
        db.close()


def test_uncalibrated_controller_is_read_only(project_root):
    _config, db, _registry, actuator, controller = make_controller(project_root, calibrated=False)
    try:
        result = controller.step(sample(), demand(), thermal())
        assert result.read_only is True
        assert not actuator.applied
    finally:
        db.close()


def test_missing_core_telemetry_blocks_automatic_write(project_root):
    _config, db, _registry, actuator, controller = make_controller(project_root)
    try:
        broken = sample()
        broken["rapl_power_60s_w"] = None
        result = controller.step(broken, demand(), thermal())
        assert result.read_only is True
        assert "core telemetry missing" in result.reason
        assert not actuator.applied
    finally:
        db.close()


def test_active_trial_blocks_normal_controller(project_root):
    _config, db, _registry, actuator, controller = make_controller(project_root)
    try:
        db.create_trial(
            {
                "trial_id": "t1",
                "state": "MEASURING",
                "kind": "envelope",
                "baseline_envelope": "INTERACTIVE_EFFICIENT",
                "candidate": {"x": 1},
                "target": {},
                "validation": {},
            }
        )
        result = controller.step(sample(), demand(), thermal())
        assert result.read_only is True
        assert "trial owns" in result.reason
        assert not actuator.applied
    finally:
        db.close()
