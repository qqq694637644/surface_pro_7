from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import sp7_powerlab.service as service_module
from sp7_powerlab.config import load_config, load_machine, load_thermal_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.experiments import TrialManager
from sp7_powerlab.lifecycle import LifecycleManager
from sp7_powerlab.service import PowerLabService
from sp7_powerlab.storage import Database


class FakeActuator:
    def __init__(self):
        self.restored = 0

    def snapshot(self):
        return {
            "epp": {"policy0": "balance_power"},
            "max_perf_pct": 60,
            "turbo": True,
        }

    def restore(self, snapshot):
        self.restored += 1
        return {"after": snapshot}

    def apply_envelope(self, envelope):
        return {"after": envelope}


class FakeController:
    def __init__(self):
        self.calibration_valid = True
        self.hardware_writable = True
        self.reconciles = 0

    def reconcile_actual_state(self, _reason):
        self.reconciles += 1
        return "INTERACTIVE_EFFICIENT"


class FakeScheduler:
    def __init__(self):
        self.calls = 0

    def candidates(self, *, baseline_name):
        self.calls += 1
        return {
            "eligible": True,
            "candidates": [
                {
                    "candidate_key": "candidate-1",
                    "proposal": {
                        "kind": "envelope",
                        "baseline_envelope": baseline_name,
                        "changes": {"max_perf_pct": 55},
                    },
                }
            ],
        }


class FakeAutonomousTrials:
    def __init__(self, db: Database):
        self.db = db
        self.starts: list[tuple[dict, dict]] = []
        self.promotions: list[str] = []

    def start(self, proposal, sample):
        self.starts.append((proposal, sample))
        return {
            "trial_id": "auto-trial",
            "state": "WAITING_FOR_COMPARABLE_WINDOW",
        }

    def promote(self, trial_id):
        self.promotions.append(trial_id)
        self.db.update_trial(trial_id, state="PROMOTED")
        return {"name": "INTERACTIVE_EFFICIENT", "status": "VERIFIED"}


def make_stack(project_root: Path, *, active_state: str):
    config = load_config(project_root)
    db = Database(project_root / "runtime/service.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    actuator = FakeActuator()
    trials = TrialManager(config, db, registry, actuator)
    snapshot = (
        {
            "epp": {"policy0": "balance_power"},
            "max_perf_pct": 60,
            "turbo": True,
        }
        if active_state == "MEASURING"
        else None
    )
    db.create_trial(
        {
            "trial_id": "restart-trial",
            "state": active_state,
            "kind": "envelope",
            "baseline_envelope": "INTERACTIVE_EFFICIENT",
            "candidate": {
                "name": "INTERACTIVE_EFFICIENT",
                "epp": "balance_power",
                "max_perf_pct": 50,
                "turbo": True,
            },
            "target": {},
            "validation": {},
            "snapshot": snapshot,
            "current_arm": "B1" if active_state == "MEASURING" else None,
            "arm_start_ts": 1.0 if active_state == "MEASURING" else None,
        }
    )
    machine = load_machine(project_root)
    thermal_config = load_thermal_config(project_root)
    stack = {
        "config": config,
        "db": db,
        "machine": machine,
        "thermal_config": thermal_config,
        "registry": registry,
        "actuator": actuator,
        "actuator_available": True,
        "actuator_mode": "fake",
        "fingerprint": "fp",
        "collector": SimpleNamespace(),
        "demand": SimpleNamespace(machine=machine),
        "thermal": SimpleNamespace(machine=machine, thermal_config=thermal_config),
        "calibration": SimpleNamespace(),
        "controller": FakeController(),
        "trials": trials,
        "unexpected_power": SimpleNamespace(),
        "report": SimpleNamespace(),
    }
    return stack, actuator


def test_service_restart_rolls_back_measuring_trial(project_root: Path, monkeypatch):
    stack, actuator = make_stack(project_root, active_state="MEASURING")
    monkeypatch.setattr(service_module, "prepare_stack", lambda *_args, **_kwargs: stack)
    service = PowerLabService(project_root)
    try:
        trial = stack["db"].get_trial("restart-trial")
        assert trial["state"] == "ROLLED_BACK"
        assert actuator.restored == 1
    finally:
        service.close()


def test_service_restart_cancels_passive_wait_without_sysfs_restore(
    project_root: Path, monkeypatch
):
    stack, actuator = make_stack(
        project_root,
        active_state="WAITING_FOR_COMPARABLE_WINDOW",
    )
    monkeypatch.setattr(service_module, "prepare_stack", lambda *_args, **_kwargs: stack)
    service = PowerLabService(project_root)
    try:
        trial = stack["db"].get_trial("restart-trial")
        assert trial["state"] == "ROLLED_BACK"
        assert actuator.restored == 0
    finally:
        service.close()


def test_runtime_machine_refresh_updates_observers(project_root: Path, monkeypatch):
    config = load_config(project_root)
    db = Database(project_root / "runtime/service.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    machine = load_machine(project_root)
    thermal_config = load_thermal_config(project_root)
    stack = {
        "config": config,
        "db": db,
        "machine": machine,
        "thermal_config": thermal_config,
        "registry": registry,
        "actuator": FakeActuator(),
        "actuator_available": True,
        "actuator_mode": "fake",
        "fingerprint": "fp",
        "collector": SimpleNamespace(),
        "demand": SimpleNamespace(machine=machine),
        "thermal": SimpleNamespace(machine=machine, thermal_config=thermal_config),
        "calibration": SimpleNamespace(),
        "controller": FakeController(),
        "trials": TrialManager(config, db, registry, FakeActuator()),
        "unexpected_power": SimpleNamespace(),
        "report": SimpleNamespace(),
    }
    monkeypatch.setattr(service_module, "prepare_stack", lambda *_args, **_kwargs: stack)
    service = PowerLabService(project_root)
    try:
        path = project_root / "config/machine.toml"
        text = path.read_text(encoding="utf-8").replace("valid = true", "valid = false")
        path.write_text(text, encoding="utf-8")
        current = path.stat().st_mtime
        os.utime(path, (current + 2, current + 2))
        service._refresh_runtime_files()
        assert service.stack["controller"].calibration_valid is False
        assert service.stack["demand"].machine["calibration"]["valid"] is False
        assert service.stack["thermal"].machine["calibration"]["valid"] is False
    finally:
        service.close()


def _automation_service(project_root: Path, *, level: int):
    config = load_config(project_root)
    config.data["automation"]["level"] = level
    db = Database(project_root / f"runtime/automation-{level}.sqlite3")
    lifecycle = LifecycleManager(db)
    lifecycle.synchronize_control(
        calibration_valid=True,
        hardware_writable=True,
        thermal_provider_healthy=True,
        core_telemetry_valid=True,
    )
    service = object.__new__(PowerLabService)
    service.root = project_root
    service.config = config
    service.db = db
    service._last_scheduler_check_ts = 0.0
    service.stack = {
        "lifecycle": lifecycle,
        "scheduler": FakeScheduler(),
        "trials": FakeAutonomousTrials(db),
    }
    return service


def test_level_three_can_start_one_gated_autonomous_trial(project_root: Path):
    service = _automation_service(project_root, level=3)
    try:
        decision = SimpleNamespace(
            read_only=False,
            action="NO_CHANGE",
            desired_envelope="INTERACTIVE_EFFICIENT",
            applied_envelope="INTERACTIVE_EFFICIENT",
        )
        trial = service._maybe_start_autonomous_trial(
            {
                "ts": 100.0,
                "current_envelope": "INTERACTIVE_EFFICIENT",
            },
            decision,
            had_active_trial=False,
        )
        assert trial["trial_id"] == "auto-trial"
        assert len(service.stack["trials"].starts) == 1
        assert service.stack["scheduler"].calls == 1
    finally:
        service.db.close()


def test_level_two_never_starts_autonomous_trial(project_root: Path):
    service = _automation_service(project_root, level=2)
    try:
        decision = SimpleNamespace(
            read_only=False,
            action="NO_CHANGE",
            desired_envelope="INTERACTIVE_EFFICIENT",
            applied_envelope="INTERACTIVE_EFFICIENT",
        )
        trial = service._maybe_start_autonomous_trial(
            {"ts": 100.0},
            decision,
            had_active_trial=False,
        )
        assert trial is None
        assert service.stack["trials"].starts == []
    finally:
        service.db.close()


def test_level_four_auto_promotion_requires_explicit_flag(project_root: Path):
    service = _automation_service(project_root, level=4)
    try:
        service.db.create_trial(
            {
                "trial_id": "verified-auto",
                "state": "VERIFIED_WINNER",
                "kind": "envelope",
                "baseline_envelope": "INTERACTIVE_EFFICIENT",
                "candidate": {"name": "INTERACTIVE_EFFICIENT"},
                "target": {},
                "validation": {},
            }
        )
        trial = service.db.get_trial("verified-auto")
        assert service._maybe_auto_promote(trial)["state"] == "VERIFIED_WINNER"
        assert service.stack["trials"].promotions == []

        service.config.data["automation"]["auto_promote"] = True
        promoted = service._maybe_auto_promote(trial)
        assert promoted["state"] == "PROMOTED"
        assert service.stack["trials"].promotions == ["verified-auto"]
    finally:
        service.db.close()
