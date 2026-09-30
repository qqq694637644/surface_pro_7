from sp7_powerlab.config import load_config
from sp7_powerlab.actuators.base import ActuatorError
from sp7_powerlab.actuators.manager import ActuatorManager
from sp7_powerlab.policy import PolicyEngine
from sp7_powerlab.storage import Database
from sp7_powerlab.profiles import ProfileRegistry


class Registry:
    def __init__(self):
        self.items = {
            "safe-baseline": {
                "profile_id": "safe-baseline",
                "backend": "noop",
                "status": "verified",
                "parameters": {},
            },
            "reading-power": {
                "profile_id": "reading-power",
                "backend": "sysfs",
                "status": "verified",
                "parameters": {"cpu.epp": "power"},
            },
        }

    def get(self, profile_id):
        return self.items.get(profile_id)


class Actuators:
    def __init__(self):
        self.value = "balance_power"

    def inspect(self):
        return {"value": self.value}

    def apply_profile(self, profile):
        before = {"cpu.epp": self.value}
        self.value = profile["parameters"]["cpu.epp"]
        return {"backend": "sysfs", "before": before, "after": {"cpu.epp": self.value}}

    def apply_safe_baseline(self):
        return {"backend": "noop", "before": {}, "after": {}}

    def restore_parameter(self, parameter, value):
        if isinstance(value, dict):
            # This fake accepts either a direct value or the manager-style path map.
            value = next(iter(value.values()), self.value)
        before = self.value
        self.value = value
        return {"parameter": parameter, "before": before, "after": value}


def test_sysfs_profile_restore_survives_policy_restart(tmp_path):
    config = load_config(tmp_path)
    config.data["policy"]["actuator"] = "sysfs"
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    registry = Registry()
    actuators = Actuators()
    try:
        first = PolicyEngine(config, db, registry, actuators)
        first.apply_profile_id("reading-power", reason="test", force=True)
        assert actuators.value == "power"
        assert first.current_restore_state == {"cpu.epp": "balance_power"}

        restarted = PolicyEngine(config, db, registry, actuators)
        assert restarted.current_profile() == "reading-power"
        restarted.apply_profile_id("safe-baseline", reason="scene changed", force=True)
        assert actuators.value == "balance_power"
        assert restarted.current_restore_state is None
        assert restarted.current_profile() == "safe-baseline"
    finally:
        db.close()


def test_single_writer_blocks_sysfs_profile_when_external_backend_selected(tmp_path):
    config = load_config(tmp_path)
    config.data["policy"]["actuator"] = "power-profiles-daemon"
    manager = ActuatorManager(config)
    try:
        manager.apply_profile(
            {
                "profile_id": "bad-mix",
                "backend": "sysfs",
                "parameters": {"cpu.epp": "power"},
            }
        )
    except ActuatorError as exc:
        assert "conflicts with selected writer" in str(exc)
    else:
        raise AssertionError("mixed power writers must be rejected")


def test_static_profile_verification_status_survives_registry_reload(tmp_path):
    profile_dir = tmp_path / "config" / "profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "candidate.toml").write_text(
        "[profile]\n"
        'id = "candidate"\n'
        'backend = "noop"\n'
        'status = "experimental"\n',
        encoding="utf-8",
    )
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    try:
        first = ProfileRegistry(tmp_path, db)
        first.load()
        first.set_status("candidate", "verified")
        assert first.get("candidate")["status"] == "verified"

        second = ProfileRegistry(tmp_path, db)
        second.load()
        assert second.get("candidate")["status"] == "verified"
        assert db.profiles()[0]["status"] == "verified"
    finally:
        db.close()


def test_verified_static_profile_definition_change_requires_revalidation(tmp_path):
    profile_dir = tmp_path / "config" / "profiles"
    profile_dir.mkdir(parents=True)
    profile_path = profile_dir / "candidate.toml"
    profile_path.write_text(
        "[profile]\n"
        'id = "candidate"\n'
        'backend = "power-profiles-daemon"\n'
        'backend_profile = "balanced"\n'
        'status = "experimental"\n',
        encoding="utf-8",
    )
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    try:
        first = ProfileRegistry(tmp_path, db)
        first.load()
        first.set_status("candidate", "verified")
        assert first.get("candidate")["status"] == "verified"

        profile_path.write_text(
            "[profile]\n"
            'id = "candidate"\n'
            'backend = "power-profiles-daemon"\n'
            'backend_profile = "power-saver"\n'
            'status = "experimental"\n',
            encoding="utf-8",
        )

        second = ProfileRegistry(tmp_path, db)
        second.load()
        assert second.get("candidate")["status"] == "needs_revalidation"
        events = db.recent_system_events(0)
        assert any(event["event"] == "profile_definition_drift" for event in events)
    finally:
        db.close()


def test_policy_does_not_write_while_trial_is_active(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    registry = Registry()
    actuators = Actuators()
    try:
        policy = PolicyEngine(config, db, registry, actuators)
        db.create_trial(
            {
                "trial_id": "t-active",
                "proposal_id": "p",
                "context_scene": "reading",
                "baseline_profile": None,
                "candidate_profile": None,
                "parameter": "cpu.epp",
                "state": "MEASURING",
                "start_ts": 1.0,
                "snapshot": {},
                "proposal": {},
            }
        )
        result = policy.consider(
            {"temp_c": 40.0},
            {
                "context_id": "ctx",
                "scene": "reading",
                "confidence": 0.95,
                "features": {},
            },
        )
        assert result is None
        assert actuators.value == "balance_power"
        assert policy.current_profile() is None
    finally:
        db.close()
