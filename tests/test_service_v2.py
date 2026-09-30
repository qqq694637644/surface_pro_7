from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import sp7_powerlab.service as service_module
from sp7_powerlab.config import load_config, load_machine, load_thermal_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.experiments import TrialManager
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
        "waste": SimpleNamespace(),
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
        "waste": SimpleNamespace(),
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
