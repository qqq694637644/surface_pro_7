from __future__ import annotations

from types import SimpleNamespace

import pytest

from sp7_powerlab.config import load_config, load_machine, load_thermal_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.hardware import hard_control_identity
from sp7_powerlab.runtime_audit import (
    FixedContextStaleError,
    audit_fixed_runtime,
    validate_live_fixed_context_before_write,
)
from sp7_powerlab.storage import Database


class FakeActuator:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


def fake_report():
    return SimpleNamespace(
        product="Surface Pro 7",
        cpu="Intel(R) Core(TM) i5-1035G4",
        bios="test-bios",
        kernel="test-kernel",
        supported_machine=True,
        capabilities={"intel_pstate": True, "hwp_epp": True},
        thermal_sensor="/sys/class/hwmon/hwmon0/temp1_input",
        thermald={"active": True, "version": "thermald-test"},
        ownership_conflicts=[],
    )


def test_fixed_runtime_audit_proves_live_physical_state(project_root, monkeypatch):
    config = load_config(project_root)
    db = Database(project_root / "runtime/fixed-audit.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        envelope = db.envelope("INTERACTIVE_EFFICIENT")
        assert envelope is not None
        envelope["status"] = "VERIFIED"
        db.upsert_envelope(envelope)

        report = fake_report()
        machine = load_machine(project_root)
        hard_hash, _payload = hard_control_identity(
            report,
            thermal_config=load_thermal_config(project_root),
            calibration_version=int((machine.get("calibration") or {}).get("version") or 0),
        )
        battery_state = {"identity_hash": "battery-a", "energy_full_wh": 40.0}
        battery_epoch = db.ensure_battery_epoch(
            identity_hash="battery-a",
            energy_full_wh=40.0,
            payload=dict(battery_state),
        )
        epoch_id = db.ensure_evidence_epoch(
            hard_identity_hash=hard_hash,
            battery_epoch=battery_epoch,
            calibration_version=int((machine.get("calibration") or {}).get("version") or 0),
            evidence_semantics_version=int(config.get("evidence.semantics_version", 1)),
            payload={},
        )
        epoch = db.active_evidence_epoch()
        assert epoch and epoch["epoch_id"] == epoch_id

        monkeypatch.setattr("sp7_powerlab.runtime_audit.inspect_hardware", lambda **_kwargs: report)
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.battery_snapshot",
            lambda _root: dict(battery_state),
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_state",
            lambda unit: (
                "inactive"
                if unit == "sp7-powerlab.service"
                else ("active" if unit == "sp7-powerlab-fixed.service" else "unavailable")
            ),
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.HWPActuator",
            lambda _sys_root: FakeActuator(
                {
                    "epp": {"policy0": envelope["epp"]},
                    "max_perf_pct": envelope["max_perf_pct"],
                    "turbo": envelope["turbo"],
                }
            ),
        )

        audit = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert audit["ready"] is True
        assert audit["hwp_matches_fixed_baseline"] is True

        db.set_meta(
            "fixed_good_selection",
            {
                "evidence_epoch_id": epoch_id,
                "envelope": envelope["name"],
                "content_hash": envelope["content_hash"],
            },
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_enabled",
            lambda unit: "disabled" if unit == "sp7-powerlab.service" else "enabled",
        )
        persistent = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
            require_persistent_selection=True,
        )
        assert persistent["ready"] is True

        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_enabled",
            lambda _unit: "disabled",
        )
        missing_boot_apply = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
            require_persistent_selection=True,
        )
        assert "fixed_oneshot_not_enabled" in missing_boot_apply["reasons"]

        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_enabled",
            lambda unit: "disabled" if unit == "sp7-powerlab.service" else "enabled",
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_state",
            lambda unit: "inactive" if unit == "sp7-powerlab.service" else "failed",
        )
        failed_oneshot = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
            require_persistent_selection=True,
        )
        assert "fixed_oneshot_not_active" in failed_oneshot["reasons"]

        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_state",
            lambda unit: "active" if unit == "sp7-powerlab.service" else "unavailable",
        )
        active_service = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert active_service["ready"] is False
        assert "main_service_not_inactive" in active_service["reasons"]
    finally:
        db.close()


def test_live_fixed_context_rejects_stale_hard_or_battery_epoch(project_root, monkeypatch):
    config = load_config(project_root)
    db = Database(project_root / "runtime/live-fixed-context.sqlite3")
    try:
        report = fake_report()
        machine = load_machine(project_root)
        calibration_version = int((machine.get("calibration") or {}).get("version") or 0)
        hard_hash, _payload = hard_control_identity(
            report,
            thermal_config=load_thermal_config(project_root),
            calibration_version=calibration_version,
        )
        battery_epoch = db.ensure_battery_epoch(
            identity_hash="battery-a",
            energy_full_wh=40.0,
            payload={"identity_hash": "battery-a", "energy_full_wh": 40.0},
        )
        db.ensure_evidence_epoch(
            hard_identity_hash=hard_hash,
            battery_epoch=battery_epoch,
            calibration_version=calibration_version,
            evidence_semantics_version=int(config.get("evidence.semantics_version", 0)),
            payload={},
        )
        monkeypatch.setattr("sp7_powerlab.runtime_audit.inspect_hardware", lambda **_kwargs: report)
        battery = {"identity_hash": "battery-a", "energy_full_wh": 40.0}
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.battery_snapshot", lambda _root: dict(battery)
        )

        context = validate_live_fixed_context_before_write(config, db)
        assert context["battery_epoch"] == battery_epoch

        battery["identity_hash"] = "battery-b"
        with pytest.raises(FixedContextStaleError, match="battery identity changed"):
            validate_live_fixed_context_before_write(config, db)

        battery["identity_hash"] = "battery-a"
        battery["energy_full_wh"] = 30.0
        with pytest.raises(FixedContextStaleError, match="energy_full changed"):
            validate_live_fixed_context_before_write(config, db)

        battery["energy_full_wh"] = 40.0
        changed = fake_report()
        changed.kernel = "new-kernel"
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.inspect_hardware", lambda **_kwargs: changed
        )
        with pytest.raises(FixedContextStaleError, match="hard identity"):
            validate_live_fixed_context_before_write(config, db)
    finally:
        db.close()


def test_fixed_runtime_audit_fails_closed_on_live_physical_drift(project_root, monkeypatch):
    config = load_config(project_root)
    db = Database(project_root / "runtime/fixed-audit-drift.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        envelope = db.envelope("INTERACTIVE_EFFICIENT")
        assert envelope is not None
        envelope["status"] = "VERIFIED"
        db.upsert_envelope(envelope)

        baseline_report = fake_report()
        machine = load_machine(project_root)
        hard_hash, _payload = hard_control_identity(
            baseline_report,
            thermal_config=load_thermal_config(project_root),
            calibration_version=int((machine.get("calibration") or {}).get("version") or 0),
        )
        battery_state = {"identity_hash": "battery-a", "energy_full_wh": 40.0}
        battery_epoch = db.ensure_battery_epoch(
            identity_hash="battery-a",
            energy_full_wh=40.0,
            payload=dict(battery_state),
        )
        db.ensure_evidence_epoch(
            hard_identity_hash=hard_hash,
            battery_epoch=battery_epoch,
            calibration_version=int((machine.get("calibration") or {}).get("version") or 0),
            evidence_semantics_version=int(config.get("evidence.semantics_version", 1)),
            payload={},
        )
        epoch = db.active_evidence_epoch()
        assert epoch is not None

        report_state = {"value": baseline_report}
        hwp_state = {
            "value": {
                "epp": {"policy0": envelope["epp"]},
                "max_perf_pct": envelope["max_perf_pct"],
                "turbo": envelope["turbo"],
            }
        }
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.inspect_hardware",
            lambda **_kwargs: report_state["value"],
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.battery_snapshot",
            lambda _root: dict(battery_state),
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.systemd_user_unit_state",
            lambda unit: "inactive" if unit == "sp7-powerlab.service" else "unavailable",
        )
        monkeypatch.setattr(
            "sp7_powerlab.runtime_audit.HWPActuator",
            lambda _sys_root: FakeActuator(hwp_state["value"]),
        )

        report_state["value"] = SimpleNamespace(**vars(baseline_report))
        report_state["value"].kernel = "different-kernel"
        hard_drift = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert "live_hard_identity_mismatch" in hard_drift["reasons"]

        report_state["value"] = SimpleNamespace(**vars(baseline_report))
        report_state["value"].thermald = {"active": False, "version": "thermald-test"}
        thermald_down = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert "thermald_not_active" in thermald_down["reasons"]

        report_state["value"] = SimpleNamespace(**vars(baseline_report))
        report_state["value"].ownership_conflicts = ["tlp.service"]
        conflicting_writer = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert "conflicting_power_writer_active" in conflicting_writer["reasons"]

        report_state["value"] = baseline_report
        hwp_state["value"] = {
            "epp": {"policy0": envelope["epp"]},
            "max_perf_pct": int(envelope["max_perf_pct"]) - 5,
            "turbo": envelope["turbo"],
        }
        hwp_drift = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert "fixed_hwp_state_mismatch" in hwp_drift["reasons"]

        hwp_state["value"] = {
            "epp": {"policy0": envelope["epp"]},
            "max_perf_pct": envelope["max_perf_pct"],
            "turbo": envelope["turbo"],
        }
        battery_state["identity_hash"] = "battery-b"
        battery_drift = audit_fixed_runtime(
            config,
            db,
            evidence_epoch=epoch,
            fixed_baseline_envelope=envelope["name"],
            fixed_baseline_content_hash=envelope["content_hash"],
        )
        assert "live_battery_identity_mismatch" in battery_drift["reasons"]
    finally:
        db.close()
