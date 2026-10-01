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


def test_rollup_with_mixed_envelope_and_brightness_is_not_reference_eligible(
    project_root: Path,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/mixed-rollup.sqlite3")
    lifecycle = LifecycleManager(db)
    epoch = db.ensure_evidence_epoch(
        hard_identity_hash="fp",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
    service = object.__new__(PowerLabService)
    service.root = project_root
    service.config = config
    service.db = db
    service.stack = {
        "fingerprint": "fp",
        "noise": SimpleNamespace(observe_rollup=lambda *_args, **_kwargs: None),
        "drift": SimpleNamespace(detect=lambda *_args, **_kwargs: None),
        "unexpected_power": SimpleNamespace(detect=lambda *_args, **_kwargs: None),
        "lifecycle": lifecycle,
        "collector": SimpleNamespace(trigger_diagnostic_burst=lambda: None),
    }
    db.set_meta(
        "measurement_trust",
        {
            "status": "READY",
            "evidence_epoch_id": epoch,
            "battery_epoch": 1,
            "calibration_version": 1,
            "evidence_semantics_version": 1,
        },
    )
    try:
        common = {
            "wall_ts": "x",
            "battery_status": "Discharging",
            "battery_pct": 80,
            "battery_power_w": 5.0,
            "battery_energy_wh": 30.0,
            "battery_epoch": 1,
            "cpu_psi": 0.1,
            "io_psi": 0.1,
            "rapl_power_60s_w": 2.0,
            "thermal_pressure": 0.1,
            "thermal_state": "COOL",
            "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
            "local_compute_pressure": "LOW",
            "media_playing": False,
            "user_active": True,
            "remote_hint": 0.0,
            "network_rx_mbps": 0.0,
            "network_tx_mbps": 0.0,
            "evidence_epoch": epoch,
            "resume_grace": False,
        }
        db.add_sample(
            {
                **common,
                "ts": 100.0,
                "brightness_pct": 40,
                "current_envelope": "INTERACTIVE_EFFICIENT",
            }
        )
        db.add_sample(
            {
                **common,
                "ts": 120.0,
                "brightness_pct": 80,
                "current_envelope": "REMOTE_EFFICIENT",
            }
        )
        result = service._rollup(100.0, 120.0)
        assert result is not None
        assert result["reference_eligible"] is False
        assert result["current_envelope"] == "MIXED"
        assert result["brightness_bucket"] == -1
        assert "envelope" in result["mixed_dimensions"]
        assert "brightness" in result["mixed_dimensions"]
    finally:
        db.close()


def test_rollup_with_same_envelope_name_but_mixed_content_hash_is_not_reference_eligible(
    project_root: Path,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/mixed-envelope-hash-rollup.sqlite3")
    lifecycle = LifecycleManager(db)
    epoch = db.ensure_evidence_epoch(
        hard_identity_hash="fp",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
    service = object.__new__(PowerLabService)
    service.root = project_root
    service.config = config
    service.db = db
    service.stack = {
        "fingerprint": "fp",
        "noise": SimpleNamespace(observe_rollup=lambda *_args, **_kwargs: None),
        "drift": SimpleNamespace(detect=lambda *_args, **_kwargs: None),
        "unexpected_power": SimpleNamespace(detect=lambda *_args, **_kwargs: None),
        "lifecycle": lifecycle,
        "collector": SimpleNamespace(trigger_diagnostic_burst=lambda: None),
    }
    try:
        common = {
            "wall_ts": "x",
            "battery_status": "Discharging",
            "battery_pct": 80,
            "battery_power_w": 5.0,
            "battery_energy_wh": 30.0,
            "battery_epoch": 1,
            "brightness_pct": 40,
            "cpu_psi": 0.1,
            "io_psi": 0.1,
            "rapl_power_60s_w": 2.0,
            "thermal_pressure": 0.1,
            "thermal_state": "COOL",
            "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
            "local_compute_pressure": "LOW",
            "media_playing": False,
            "user_active": True,
            "remote_hint": 0.0,
            "network_rx_mbps": 0.0,
            "network_tx_mbps": 0.0,
            "evidence_epoch": epoch,
            "resume_grace": False,
            "current_envelope": "INTERACTIVE_EFFICIENT",
        }
        db.add_sample(
            {
                **common,
                "ts": 100.0,
                "current_envelope_content_hash": "revision-a",
            }
        )
        db.add_sample(
            {
                **common,
                "ts": 120.0,
                "current_envelope_content_hash": "revision-b",
            }
        )
        result = service._rollup(100.0, 120.0)
        assert result is not None
        assert result["reference_eligible"] is False
        assert result["current_envelope"] == "INTERACTIVE_EFFICIENT"
        assert result["current_envelope_content_hash"] is None
        assert "envelope_content_hash" in result["mixed_dimensions"]
        assert "mixed_envelope_content_hash" in result["reference_ineligible_reasons"]
    finally:
        db.close()


def test_rollup_with_charging_transition_is_not_reference_eligible(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/charging-rollup.sqlite3")
    lifecycle = LifecycleManager(db)
    epoch = db.ensure_evidence_epoch(
        hard_identity_hash="fp",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
    service = object.__new__(PowerLabService)
    service.root = project_root
    service.config = config
    service.db = db
    service.stack = {
        "fingerprint": "fp",
        "noise": SimpleNamespace(observe_rollup=lambda *_args, **_kwargs: None),
        "drift": SimpleNamespace(detect=lambda *_args, **_kwargs: None),
        "unexpected_power": SimpleNamespace(detect=lambda *_args, **_kwargs: None),
        "lifecycle": lifecycle,
        "collector": SimpleNamespace(trigger_diagnostic_burst=lambda: None),
    }
    try:
        common = {
            "wall_ts": "x",
            "battery_pct": 80,
            "battery_power_w": 5.0,
            "battery_energy_wh": 30.0,
            "battery_epoch": 1,
            "brightness_pct": 40,
            "cpu_psi": 0.1,
            "io_psi": 0.1,
            "rapl_power_60s_w": 2.0,
            "thermal_pressure": 0.1,
            "thermal_state": "COOL",
            "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
            "local_compute_pressure": "LOW",
            "media_playing": False,
            "user_active": True,
            "remote_hint": 0.0,
            "network_rx_mbps": 0.0,
            "network_tx_mbps": 0.0,
            "evidence_epoch": epoch,
            "resume_grace": False,
            "current_envelope": "INTERACTIVE_EFFICIENT",
        }
        for ts, status in (
            (0, "Discharging"),
            (10, "Discharging"),
            (20, "Charging"),
            (30, "Charging"),
            (40, "Discharging"),
            (50, "Discharging"),
        ):
            db.add_sample({**common, "ts": float(ts), "battery_status": status})

        result = service._rollup(0.0, 50.0)
        assert result is not None
        assert result["reference_eligible"] is False
        assert result["valid_seconds"] == 20.0
        assert abs(result["valid_fraction"] - (20.0 / 60.0)) < 1e-9
        assert "power_source_not_all_discharging" in result["reference_ineligible_reasons"]
        assert "insufficient_valid_discharge_fraction" in result["reference_ineligible_reasons"]
    finally:
        db.close()


def test_hard_epoch_change_invalidates_trust_and_exits_stable(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/epoch-transition.sqlite3")
    lifecycle = LifecycleManager(db)
    old_epoch = db.ensure_evidence_epoch(
        hard_identity_hash="old-fp",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=int(config.get("evidence.semantics_version", 1)),
        payload={},
    )
    db.set_meta(
        "measurement_trust",
        {
            "status": "READY",
            "evidence_epoch_id": old_epoch,
            "battery_epoch": 1,
            "calibration_version": 1,
            "evidence_semantics_version": int(config.get("evidence.semantics_version", 1)),
        },
    )
    lifecycle.synchronize_learning(calibration_valid=True)
    lifecycle.freeze("test stable")
    service = object.__new__(PowerLabService)
    service.root = project_root
    service.config = config
    service.db = db
    service.stack = {
        "machine": load_machine(project_root),
        "fingerprint": "new-fp",
        "lifecycle": lifecycle,
    }
    try:
        new_epoch = service._sync_evidence_epoch(1)
        assert new_epoch != old_epoch
        assert db.get_meta("measurement_trust")["status"] == "BLOCKED"
        assert db.get_meta("measurement_trust")["reasons"] == ["evidence_epoch_changed"]
        assert lifecycle.learning_state() == "BASELINE_OBSERVATION"
    finally:
        db.close()


def test_kernel_is_hard_fingerprint_and_media_versions_get_compatibility_generation(
    project_root: Path,
    monkeypatch,
):
    db = Database(project_root / "runtime/fingerprint-compat.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    payload = {
        "product_name": "Surface Pro 7",
        "cpu_model": "Intel(R) Core(TM) i5-1035G4",
        "kernel": "6.10.1-surface",
        "versions": {
            "thermald": "2.5",
            "firefox": "130",
            "mesa": "24.1",
        },
    }
    monkeypatch.setattr(service_module, "system_fingerprint", lambda _report: dict(payload))
    machine = {"calibration": {"version": 1}}
    report = SimpleNamespace(
        product="Surface Pro 7",
        cpu="Intel(R) Core(TM) i5-1035G4",
        bios="test-bios",
        kernel="6.10.1-surface",
        capabilities={"intel_pstate": True, "hwp_epp": True},
        thermal_sensor="/sys/class/thermal/thermal_zone0/temp",
        thermald={"version": "2.5"},
    )
    try:
        first = service_module._refresh_fingerprint_state(
            db=db,
            registry=registry,
            report=report,
            machine=machine,
            thermal_config={},
        )
        first_media = db.get_meta("media_compatibility_generation")

        payload["versions"] = {**payload["versions"], "firefox": "131"}
        browser_only = service_module._refresh_fingerprint_state(
            db=db,
            registry=registry,
            report=report,
            machine=machine,
            thermal_config={},
        )
        assert browser_only == first
        assert db.get_meta("media_compatibility_generation") != first_media

        payload["kernel"] = "6.10.2-surface"
        report.kernel = "6.10.2-surface"
        kernel_changed = service_module._refresh_fingerprint_state(
            db=db,
            registry=registry,
            report=report,
            machine=machine,
            thermal_config={},
        )
        assert kernel_changed != first
    finally:
        db.close()


def test_hardware_refresh_rebinds_actuator_when_helper_becomes_available(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/actuator-rebind.sqlite3")
    service = object.__new__(PowerLabService)
    service.config = config
    service.db = db
    service._last_hardware_refresh_ts = 0.0
    new_actuator = SimpleNamespace(name="helper")
    controller = SimpleNamespace(hardware_writable=False, actuator=None)
    trials = SimpleNamespace(actuator=None)
    service.stack = {
        "machine": {"identity": {}, "calibration": {"version": 1}},
        "actuator": SimpleNamespace(name="unavailable"),
        "actuator_available": False,
        "actuator_mode": "read-only",
        "controller": controller,
        "trials": trials,
        "lifecycle": SimpleNamespace(set_control=lambda *_args, **_kwargs: None),
        "registry": SimpleNamespace(),
        "thermal_config": {},
        "fingerprint": "old",
    }
    report = SimpleNamespace(control_capable=True, errors=[])
    monkeypatch.setattr(service_module, "inspect_hardware", lambda **_kwargs: report)
    monkeypatch.setattr(
        service_module,
        "build_actuator",
        lambda _config: (new_actuator, True, "root-helper"),
    )
    monkeypatch.setattr(service_module, "_refresh_fingerprint_state", lambda **_kwargs: "new-fp")
    try:
        service._refresh_hardware_contract(400.0)
        assert service.stack["actuator"] is new_actuator
        assert service.stack["actuator_available"] is True
        assert service.stack["actuator_mode"] == "root-helper"
        assert controller.actuator is new_actuator
        assert trials.actuator is new_actuator
        assert controller.hardware_writable is True
        assert db.get_meta("actuator_rebind")["previous_mode"] == "read-only"
    finally:
        db.close()


def test_hardware_refresh_revokes_write_on_failed_probe_but_keeps_reconnecting_client(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/actuator-transient.sqlite3")
    service = object.__new__(PowerLabService)
    service.config = config
    service.db = db
    service._last_hardware_refresh_ts = 0.0
    old_actuator = SimpleNamespace(name="helper-client")
    unavailable = SimpleNamespace(name="unavailable")
    controller = SimpleNamespace(hardware_writable=True, actuator=old_actuator)
    trials = SimpleNamespace(actuator=old_actuator)
    control_states = []
    service.stack = {
        "machine": {"identity": {}, "calibration": {"version": 1}},
        "actuator": old_actuator,
        "actuator_available": True,
        "actuator_mode": "root-helper",
        "controller": controller,
        "trials": trials,
        "lifecycle": SimpleNamespace(
            control_state=lambda: "CONTROL_ALLOWED",
            set_control=lambda state, *_args, **_kwargs: control_states.append(state),
        ),
        "registry": SimpleNamespace(),
        "thermal_config": {},
        "fingerprint": "old",
    }
    report = SimpleNamespace(control_capable=True, errors=[])
    monkeypatch.setattr(service_module, "inspect_hardware", lambda **_kwargs: report)
    monkeypatch.setattr(
        service_module,
        "build_actuator",
        lambda _config: (unavailable, False, "read-only"),
    )
    monkeypatch.setattr(service_module, "_refresh_fingerprint_state", lambda **_kwargs: "new-fp")
    try:
        service._refresh_hardware_contract(400.0)
        assert service.stack["actuator"] is old_actuator
        assert service.stack["actuator_available"] is False
        assert service.stack["actuator_mode"] == "root-helper"
        assert controller.actuator is old_actuator
        assert trials.actuator is old_actuator
        assert controller.hardware_writable is False
        assert control_states[-1] == "READ_ONLY"
        assert db.get_meta("actuator_probe_degraded")["probed_available"] is False
        assert db.get_meta("actuator_probe_degraded")["hardware_writable"] is False
    finally:
        db.close()


def test_hardware_refresh_freezes_actuator_backend_during_active_trial(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/actuator-trial-freeze.sqlite3")
    service = object.__new__(PowerLabService)
    service.config = config
    service.db = db
    service._last_hardware_refresh_ts = 0.0
    old_actuator = SimpleNamespace(name="helper-client")
    new_actuator = SimpleNamespace(name="direct")
    controller = SimpleNamespace(hardware_writable=True, actuator=old_actuator)
    trials = SimpleNamespace(actuator=old_actuator)
    service.stack = {
        "machine": {"identity": {}, "calibration": {"version": 1}},
        "actuator": old_actuator,
        "actuator_available": True,
        "actuator_mode": "root-helper",
        "controller": controller,
        "trials": trials,
        "lifecycle": SimpleNamespace(set_control=lambda *_args, **_kwargs: None),
        "registry": SimpleNamespace(),
        "thermal_config": {},
        "fingerprint": "old",
    }
    report = SimpleNamespace(control_capable=True, errors=[])
    monkeypatch.setattr(db, "active_trial", lambda: {"trial_id": "trial-active"})
    monkeypatch.setattr(service_module, "inspect_hardware", lambda **_kwargs: report)
    monkeypatch.setattr(
        service_module,
        "build_actuator",
        lambda _config: (new_actuator, True, "direct"),
    )
    monkeypatch.setattr(service_module, "_refresh_fingerprint_state", lambda **_kwargs: "new-fp")
    try:
        service._refresh_hardware_contract(400.0)
        assert service.stack["actuator"] is old_actuator
        assert service.stack["actuator_available"] is False
        assert service.stack["actuator_mode"] == "root-helper"
        assert controller.actuator is old_actuator
        assert trials.actuator is old_actuator
        assert controller.hardware_writable is False
        assert db.get_meta("actuator_probe_degraded")["active_trial"] is True
    finally:
        db.close()


def test_fast_hardware_refresh_skips_versions_and_compatibility_scan_is_slow(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/refresh-cadence.sqlite3")
    service = object.__new__(PowerLabService)
    service.config = config
    service.db = db
    service._last_hardware_refresh_ts = 0.0
    service._last_compatibility_refresh_ts = 100.0
    controller = SimpleNamespace(hardware_writable=False, actuator=None)
    service.stack = {
        "machine": {"identity": {}, "calibration": {"version": 1}},
        "actuator": SimpleNamespace(name="unavailable"),
        "actuator_available": False,
        "actuator_mode": "read-only",
        "controller": controller,
        "trials": SimpleNamespace(actuator=None),
        "lifecycle": SimpleNamespace(set_control=lambda *_args, **_kwargs: None),
        "registry": SimpleNamespace(),
        "thermal_config": {},
        "fingerprint": "old",
    }
    include_versions_calls = []

    def fake_inspect(**kwargs):
        include_versions_calls.append(kwargs.get("include_versions"))
        return SimpleNamespace(control_capable=True, errors=[])

    monkeypatch.setattr(service_module, "inspect_hardware", fake_inspect)
    monkeypatch.setattr(
        service_module,
        "build_actuator",
        lambda _config: (SimpleNamespace(name="unavailable"), False, "read-only"),
    )
    monkeypatch.setattr(service_module, "_refresh_fingerprint_state", lambda **_kwargs: "new-fp")
    try:
        service._refresh_hardware_contract(400.0)
        assert include_versions_calls == [False]

        service._refresh_compatibility_contract(100.0 + 6 * 3600.0 - 1.0)
        assert include_versions_calls == [False]

        service._refresh_compatibility_contract(100.0 + 6 * 3600.0)
        assert include_versions_calls == [False, True]
    finally:
        db.close()
