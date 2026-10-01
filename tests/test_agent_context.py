from __future__ import annotations

from pathlib import Path

from sp7_powerlab.agent_context import (
    build_agent_context,
    build_agent_context_without_runtime,
)
from sp7_powerlab.config import load_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.lifecycle import LifecycleManager
from sp7_powerlab.storage import Database


class FakeHardwareReport:
    def as_dict(self):
        return {
            "product": "Surface Pro 7",
            "cpu": "Intel(R) Core(TM) i5-1035G4",
            "bios": "test",
            "kernel": "test",
            "supported_machine": True,
            "capabilities": {
                "intel_pstate": True,
                "hwp_epp": True,
                "turbo_control": True,
                "battery": True,
                "thermal": True,
                "rapl": True,
                "systemd": True,
            },
            "thermald": {"active": True},
            "ownership_conflicts": [],
            "errors": [],
            "warnings": [],
            "thermal_sensor": "/sys/class/hwmon/hwmon0/temp1_input",
            "writable": True,
            "control_capable": True,
        }


def test_agent_context_is_compact_runtime_truth_entrypoint(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/powerlab.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    monkeypatch.setattr(
        "sp7_powerlab.agent_context.inspect_hardware",
        lambda **_kwargs: FakeHardwareReport(),
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context._git_context",
        lambda _root: {
            "commit": "abc123",
            "branch": "test",
            "dirty": False,
        },
    )
    try:
        before_runs = db.conn.execute("SELECT COUNT(*) FROM llm_runs").fetchone()[0]
        context = build_agent_context(config, db, registry)
        after_runs = db.conn.execute("SELECT COUNT(*) FROM llm_runs").fetchone()[0]

        assert before_runs == after_runs
        assert context["project"]["runtime_schema"] == 5
        assert context["project"]["git"]["commit"] == "abc123"
        assert context["documentation"]["ai_entry"] == "AGENTS.md"
        assert context["documentation"]["design_contract"] == "PLAN2.md"
        assert context["hardware"]["supported_machine"] is True
        assert context["current_stage"]["name"] == "STAGE_A_MEASUREMENT_TRUST"
        assert context["current_stage"]["status"] == "BLOCKED"
        assert context["recommended_next_actions"]
        assert "Runtime state in this output" in context["truth_note"]
        assert "latest_runs" not in context["net_benefit"]
        assert "measurement_trust" not in context["stable_readiness"]
        assert context["scheduler"]["potential_neighbor_count"] == 0
    finally:
        db.close()


def test_agent_context_exposes_machine_workload_assumption(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/powerlab.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    monkeypatch.setattr(
        "sp7_powerlab.agent_context.inspect_hardware",
        lambda **_kwargs: FakeHardwareReport(),
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context._git_context",
        lambda _root: {"commit": None, "branch": None, "dirty": None},
    )
    try:
        context = build_agent_context(config, db, registry)
        assumption = context["project"]["hardware_target"]["workload_assumption"]
        assert "heavy jobs" in assumption
        assert "remote server" in assumption
    finally:
        db.close()


def test_agent_context_still_navigates_when_runtime_database_is_unavailable(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    monkeypatch.setattr(
        "sp7_powerlab.agent_context.inspect_hardware",
        lambda **_kwargs: FakeHardwareReport(),
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context._git_context",
        lambda _root: {"commit": "abc123", "branch": "test", "dirty": False},
    )

    context = build_agent_context_without_runtime(
        config,
        error="legacy runtime schema",
    )

    assert context["runtime_database"]["available"] is False
    assert context["current_stage"]["name"] == "RUNTIME_DATABASE"
    assert context["current_stage"]["status"] == "BLOCKED"
    assert context["runtime"]["control_safety_state"] == "UNKNOWN"
    assert context["battery"]["active_epoch"] is None
    assert context["scheduler"]["eligible"] is False
    assert context["recommended_next_actions"][0]["action"] == ("inspect_or_reset_runtime_database")
    assert "authorized" in context["recommended_next_actions"][0]["reason"]


def test_agent_context_does_not_sync_config_envelopes_into_runtime(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/read-only-context.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    monkeypatch.setattr(
        "sp7_powerlab.agent_context.inspect_hardware",
        lambda **_kwargs: FakeHardwareReport(),
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context._git_context",
        lambda _root: {"commit": "abc123", "branch": "test", "dirty": False},
    )
    try:
        assert db.envelopes() == []
        build_agent_context(config, db, registry)
        assert db.envelopes() == []
    finally:
        db.close()


def test_agent_context_marks_stale_measurement_trust_blocked(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/stale-trust.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    db.ensure_battery_epoch(
        identity_hash="battery",
        energy_full_wh=40.0,
        payload={},
    )
    first = db.ensure_evidence_epoch(
        hard_identity_hash="old",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
    db.set_meta(
        "measurement_trust",
        {
            "status": "READY",
            "evidence_epoch_id": first,
            "battery_epoch": 1,
            "calibration_version": 1,
            "evidence_semantics_version": 1,
        },
    )
    db.ensure_evidence_epoch(
        hard_identity_hash="new",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context.inspect_hardware",
        lambda **_kwargs: FakeHardwareReport(),
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context._git_context",
        lambda _root: {"commit": "abc123", "branch": "test", "dirty": False},
    )
    try:
        context = build_agent_context(config, db, registry)
        assert context["battery"]["measurement_trust"]["status"] == "BLOCKED"
        assert context["battery"]["measurement_trust"]["valid_for_active_evidence_epoch"] is False
        assert context["current_stage"]["name"] == "STAGE_A_MEASUREMENT_TRUST"
    finally:
        db.close()


def test_agent_context_does_not_call_stale_stable_converged(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/stale-stable.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    db.ensure_battery_epoch(
        identity_hash="battery",
        energy_full_wh=40.0,
        payload={},
    )
    epoch = db.ensure_evidence_epoch(
        hard_identity_hash="hard",
        battery_epoch=1,
        calibration_version=1,
        evidence_semantics_version=1,
        payload={},
    )
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
    lifecycle = LifecycleManager(db)
    lifecycle.synchronize_learning(calibration_valid=True)
    lifecycle.freeze("test stale stable")
    monkeypatch.setattr(
        "sp7_powerlab.agent_context.inspect_hardware",
        lambda **_kwargs: FakeHardwareReport(),
    )
    monkeypatch.setattr(
        "sp7_powerlab.agent_context._git_context",
        lambda _root: {"commit": "abc123", "branch": "test", "dirty": False},
    )
    try:
        context = build_agent_context(config, db, registry)
        assert context["runtime"]["learning_lifecycle"] == "STABLE"
        assert context["stable_readiness"]["ready"] is False
        assert context["current_stage"]["name"] == "STABLE_STALE"
        assert context["current_stage"]["status"] == "BLOCKED"
    finally:
        db.close()
