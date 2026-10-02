from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import sp7_powerlab.cli as cli_module
import sp7_powerlab.minimal_meter_cli as meter_cli_module
from sp7_powerlab.cli import _meter_campaign
from sp7_powerlab.config import load_config
from sp7_powerlab.minimal_meter_cli import (
    _capture_context_change_reason,
    _control_safety_activity,
    _prepare_campaign,
    _service_mode_status,
)
from sp7_powerlab.storage import Database, LegacyDatabaseError


def _meter_run(
    db: Database,
    *,
    mode: str,
    campaign: str,
    epoch: str,
    envelope: str = "INTERACTIVE_EFFICIENT",
    envelope_hash: str = "fixed-hash",
    policy_fingerprint: str = "policy-a",
    stage_e_contract_identity: str = "stage-e-contract-a",
    media_generation: str = "media-a",
    order: int,
) -> str:
    if mode == "FIXED_GOOD" and db.net_benefit_campaign(campaign) is None:
        db.create_net_benefit_campaign(
            campaign_id=campaign,
            evidence_epoch_id=epoch,
            battery_epoch=1,
            hard_identity_hash="hard",
            calibration_version=1,
            evidence_semantics_version=8,
            fixed_baseline_envelope=envelope,
            fixed_baseline_content_hash=envelope_hash,
            payload={
                "comparisons": {},
                "stage_e_contract_identity": stage_e_contract_identity,
                "media_compatibility_generation": media_generation,
            },
        )
    run_id = db.start_minimal_meter_run(
        capture_mode=mode,
        campaign_id=campaign,
        evidence_epoch_id=epoch,
        battery_epoch=1,
        battery_identity_hash="battery",
        hard_identity_hash="hard",
        calibration_version=1,
        evidence_semantics_version=8,
        envelope=envelope,
        envelope_content_hash=envelope_hash,
        runtime_policy_fingerprint=policy_fingerprint,
        payload={
            "capture_contract_version": 4,
            "stage_e_contract_identity": stage_e_contract_identity,
            "media_compatibility_generation": media_generation,
            "interval_seconds": 60.0,
        },
    )
    db.finish_minimal_meter_run(run_id, {"sample_count": 2})
    start_ts = float(order * 100)
    with db.conn:
        db.conn.execute(
            "UPDATE minimal_meter_runs SET start_ts=?,end_ts=? WHERE run_id=?",
            (start_ts, start_ts + 50.0, run_id),
        )
    return run_id


def test_meter_campaign_requires_paired_capture_time_provenance(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        before = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            order=1,
        )
        candidate_first = _meter_run(
            db,
            mode="MONITORING",
            campaign="campaign-a",
            epoch=epoch,
            order=2,
        )
        candidate_second = _meter_run(
            db,
            mode="MONITORING",
            campaign="campaign-a",
            epoch=epoch,
            order=3,
        )
        after = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            order=4,
        )
        before_run, first_run, second_run, after_run, result_mode = _meter_campaign(
            db,
            before,
            candidate_first,
            candidate_second,
            after,
        )
        assert before_run["run_id"] == before
        assert first_run["run_id"] == candidate_first
        assert second_run["run_id"] == candidate_second
        assert after_run["run_id"] == after
        assert result_mode == "MONITORING_OVERHEAD"

        wrong_campaign = _meter_run(
            db,
            mode="MONITORING",
            campaign="campaign-b",
            epoch=epoch,
            order=5,
        )
        with pytest.raises(SystemExit, match="campaign_id"):
            _meter_campaign(db, before, candidate_first, wrong_campaign, after)
    finally:
        db.close()


def test_meter_campaign_rejects_media_generation_change(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        before = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-media",
            epoch=epoch,
            order=1,
        )
        first = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-media",
            epoch=epoch,
            order=2,
        )
        second = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-media",
            epoch=epoch,
            media_generation="media-b",
            order=3,
        )
        after = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-media",
            epoch=epoch,
            order=4,
        )
        with pytest.raises(SystemExit, match="media_compatibility_generation"):
            _meter_campaign(db, before, first, second, after)
    finally:
        db.close()


def test_meter_campaign_rejects_runs_from_stale_epoch(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        old_epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        before = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=old_epoch,
            order=1,
        )
        candidate_first = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=old_epoch,
            order=2,
        )
        candidate_second = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=old_epoch,
            order=3,
        )
        after = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=old_epoch,
            order=4,
        )
        db.ensure_evidence_epoch(
            hard_identity_hash="hard-new",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        with pytest.raises(SystemExit, match="current evidence epoch"):
            _meter_campaign(db, before, candidate_first, candidate_second, after)
    finally:
        db.close()


def test_meter_campaign_rejects_changed_fixed_baseline(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        before = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            envelope_hash="fixed-a",
            order=1,
        )
        candidate_first = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=epoch,
            order=2,
        )
        candidate_second = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=epoch,
            order=3,
        )
        after = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            envelope_hash="fixed-b",
            order=4,
        )
        with pytest.raises(SystemExit, match="fixed reference definition changed"):
            _meter_campaign(db, before, candidate_first, candidate_second, after)
    finally:
        db.close()


def test_meter_campaign_rejects_large_interblock_gap(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        before = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            order=1,
        )
        first = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=epoch,
            order=2,
        )
        second = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=epoch,
            order=30,
        )
        after = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            order=31,
        )
        with pytest.raises(SystemExit, match="inter-block gap"):
            _meter_campaign(
                db,
                before,
                first,
                second,
                after,
                max_interblock_gap_seconds=900.0,
            )
    finally:
        db.close()


def test_meter_campaign_rejects_runtime_policy_change_between_candidate_blocks(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=8,
            payload={},
        )
        before = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            policy_fingerprint="policy-a",
            order=1,
        )
        first = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=epoch,
            policy_fingerprint="policy-a",
            order=2,
        )
        second = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=epoch,
            policy_fingerprint="policy-b",
            order=3,
        )
        after = _meter_run(
            db,
            mode="FIXED_GOOD",
            campaign="campaign-a",
            epoch=epoch,
            policy_fingerprint="policy-a",
            order=4,
        )
        with pytest.raises(SystemExit, match="runtime policy fingerprint changed"):
            _meter_campaign(db, before, first, second, after)
    finally:
        db.close()


def test_net_benefit_campaign_rejects_stage_e_contract_change_between_blocks(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        context = {
            "evidence_epoch_id": "epoch-current",
            "battery_epoch": 1,
            "hard_identity_hash": "hard",
            "calibration_version": 1,
            "evidence_semantics_version": 8,
            "envelope": "INTERACTIVE_EFFICIENT",
            "envelope_content_hash": "fixed-hash",
            "runtime_policy_fingerprint": "dynamic-policy",
            "stage_e_contract_identity": "contract-a",
            "media_compatibility_generation": "media-a",
        }
        campaign = _prepare_campaign(
            db,
            campaign_id="campaign-code",
            mode="FIXED_GOOD",
            context=context,
            max_campaign_span_seconds=86400.0,
        )
        assert campaign["payload"]["stage_e_contract_identity"] == "contract-a"

        with pytest.raises(RuntimeError, match="Stage E contract identity changed"):
            _prepare_campaign(
                db,
                campaign_id="campaign-code",
                mode="DYNAMIC_CONTROLLER",
                context={**context, "stage_e_contract_identity": "contract-b"},
                max_campaign_span_seconds=86400.0,
            )
        assert db.net_benefit_campaign("campaign-code")["status"] == "INVALID"
    finally:
        db.close()


def test_dynamic_capture_allows_verified_envelope_changes_but_fixed_capture_does_not():
    start = {
        "evidence_epoch_id": "epoch",
        "battery_epoch": 1,
        "battery_identity_hash": "battery",
        "hard_identity_hash": "hard",
        "calibration_version": 1,
        "evidence_semantics_version": 8,
        "runtime_policy_fingerprint": "policy-a",
        "stage_e_contract_identity": "contract-a",
        "media_compatibility_generation": "media-a",
        "envelope": "INTERACTIVE_EFFICIENT",
        "envelope_content_hash": "fixed",
    }
    final = {**start, "envelope": "REMOTE_EFFICIENT"}
    assert _capture_context_change_reason(start, final, mode="DYNAMIC_CONTROLLER") is None
    assert (
        _capture_context_change_reason(start, final, mode="FIXED_GOOD")
        == "fixed_capture_envelope_changed"
    )
    assert (
        _capture_context_change_reason(start, final, mode="MONITORING")
        == "fixed_capture_envelope_changed"
    )


def test_service_mode_status_verifies_runtime_mode(project_root: Path, monkeypatch):
    config = load_config(project_root)
    config.data["automation"]["level"] = 0
    db = Database(project_root / "runtime/meter-mode.sqlite3")
    service_state = {"value": "inactive"}
    monkeypatch.setattr(
        meter_cli_module,
        "systemd_user_unit_state",
        lambda unit: service_state["value"] if unit == "sp7-powerlab.service" else "unavailable",
    )
    monkeypatch.setattr(
        meter_cli_module,
        "_hourly_units_status",
        lambda: {
            "sp7-powerlab-hourly.timer": "inactive",
            "sp7-powerlab-hourly.service": "inactive",
        },
    )
    try:
        fixed = _service_mode_status(config, db, "FIXED_GOOD")
        assert fixed["service_heartbeat_fresh"] is False
        assert fixed["service_unit_state"] == "inactive"

        service_state["value"] = "active"
        runtime_code = meter_cli_module.dynamic_runtime_code_identity(config)["aggregate_sha256"]
        runtime_config = meter_cli_module.dynamic_runtime_config_identity(config)["identity"]
        db.set_meta(
            "service_heartbeat",
            {
                "ts": time.time(),
                "automation_level": 0,
                "control_state": "READ_ONLY",
                "current_envelope": "INTERACTIVE_EFFICIENT",
                "runtime_code_identity": runtime_code,
                "runtime_config_identity": runtime_config,
            },
        )
        with pytest.raises(RuntimeError, match="service to be stopped"):
            _service_mode_status(config, db, "FIXED_GOOD")
        assert _service_mode_status(config, db, "MONITORING")["runtime_automation_level"] == 0

        db.set_meta(
            "service_heartbeat",
            {
                "ts": time.time(),
                "automation_level": 0,
                "control_state": "READ_ONLY",
                "runtime_code_identity": "stale-code",
                "runtime_config_identity": runtime_config,
            },
        )
        with pytest.raises(RuntimeError, match="runtime implementation stale"):
            _service_mode_status(config, db, "MONITORING")

        db.set_meta(
            "service_heartbeat",
            {
                "ts": time.time(),
                "automation_level": 0,
                "control_state": "READ_ONLY",
                "telemetry_mode": "DIAGNOSTIC_BURST",
                "runtime_code_identity": runtime_code,
                "runtime_config_identity": runtime_config,
            },
        )
        with pytest.raises(RuntimeError, match="diagnostic burst"):
            _service_mode_status(config, db, "MONITORING")

        config.data["automation"]["level"] = 1
        runtime_config = meter_cli_module.dynamic_runtime_config_identity(config)["identity"]
        db.set_meta(
            "service_heartbeat",
            {
                "ts": time.time(),
                "automation_level": 1,
                "control_state": "CONTROL_ALLOWED",
                "current_envelope": "INTERACTIVE_EFFICIENT",
                "runtime_code_identity": runtime_code,
                "runtime_config_identity": runtime_config,
            },
        )
        assert (
            _service_mode_status(config, db, "DYNAMIC_CONTROLLER")["runtime_automation_level"] == 1
        )
    finally:
        db.close()


def test_fixed_apply_uses_verified_envelope_without_setting_override(
    project_root: Path, monkeypatch
):
    monkeypatch.setattr(cli_module, "ROOT", project_root)
    db = Database(project_root / "runtime/powerlab.sqlite3")
    registry = cli_module.EnvelopeRegistry(project_root, db)
    registry.load()
    envelope = db.envelope("INTERACTIVE_EFFICIENT")
    assert envelope is not None
    envelope["status"] = "VERIFIED"
    db.upsert_envelope(envelope)
    db.close()

    snapshot = {
        "epp": {"policy0": envelope["epp"]},
        "max_perf_pct": envelope["max_perf_pct"],
        "turbo": envelope["turbo"],
    }

    class Actuator:
        def apply_envelope(self, _envelope):
            return {"after": snapshot}

        def snapshot(self):
            return snapshot

    monkeypatch.setattr(cli_module, "systemd_user_unit_state", lambda _unit: "inactive")
    monkeypatch.setattr(
        cli_module,
        "validate_live_fixed_context_before_write",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        cli_module,
        "inspect_hardware",
        lambda **_kwargs: SimpleNamespace(writable=True),
    )
    monkeypatch.setattr(
        cli_module,
        "build_actuator",
        lambda _config: (Actuator(), True, "root-helper"),
    )

    assert cli_module.cmd_fixed_apply(SimpleNamespace(config=None, name=envelope["name"])) == 0
    verify = Database(project_root / "runtime/powerlab.sqlite3")
    try:
        assert verify.get_meta("current_envelope") == envelope["name"]
        assert verify.get_meta("manual_override") is None
    finally:
        verify.close()


def test_control_safety_activity_keeps_thermal_as_outcome_but_rejects_other_interruptions(
    tmp_path: Path,
):
    db = Database(tmp_path / "db.sqlite3")
    try:
        start_ts = time.time()
        db.add_runtime_state("control", "EMERGENCY", "thermal emergency", {})
        db.add_runtime_state("control", "READ_ONLY", "actuator probe is unavailable", {})
        activity = _control_safety_activity(db, start_ts)
        assert [row["state"] for row in activity["thermal"]] == ["EMERGENCY"]
        assert [row["state"] for row in activity["invalid"]] == ["READ_ONLY"]
    finally:
        db.close()


def test_service_mode_status_rejects_active_hourly_background_unit(project_root: Path, monkeypatch):
    config = load_config(project_root)
    db = Database(project_root / "runtime/meter-hourly.sqlite3")
    monkeypatch.setattr(
        meter_cli_module,
        "systemd_user_unit_state",
        lambda unit: "inactive" if unit == "sp7-powerlab.service" else "unavailable",
    )
    monkeypatch.setattr(
        meter_cli_module,
        "_hourly_units_status",
        lambda: {
            "sp7-powerlab-hourly.timer": "active",
            "sp7-powerlab-hourly.service": "inactive",
        },
    )
    try:
        with pytest.raises(RuntimeError, match="hourly background units"):
            _service_mode_status(config, db, "FIXED_GOOD")
    finally:
        db.close()


def test_fixed_good_rejects_stale_heartbeat_if_service_is_actually_active(
    project_root: Path,
    monkeypatch,
):
    config = load_config(project_root)
    db = Database(project_root / "runtime/meter-fixed-physical.sqlite3")
    monkeypatch.setattr(
        meter_cli_module,
        "systemd_user_unit_state",
        lambda unit: "active" if unit == "sp7-powerlab.service" else "unavailable",
    )
    monkeypatch.setattr(
        meter_cli_module,
        "_hourly_units_status",
        lambda: {
            "sp7-powerlab-hourly.timer": "unavailable",
            "sp7-powerlab-hourly.service": "unavailable",
        },
    )
    try:
        db.set_meta("service_heartbeat", {"ts": 0.0})
        with pytest.raises(RuntimeError, match="service to be stopped"):
            _service_mode_status(config, db, "FIXED_GOOD")
    finally:
        db.close()


def test_dynamic_activation_requires_heartbeat_from_this_start():
    status = {
        "mode": "DYNAMIC_CONTROLLER",
        "service_heartbeat_ts": 100.0,
        "runtime_code_identity": "code-a",
        "runtime_config_identity": "config-a",
    }
    assert not cli_module._dynamic_activation_ready(
        status,
        activation_start_ts=101.0,
        expected_code="code-a",
        expected_config="config-a",
    )
    status["service_heartbeat_ts"] = 101.0
    assert cli_module._dynamic_activation_ready(
        status,
        activation_start_ts=101.0,
        expected_code="code-a",
        expected_config="config-a",
    )


def test_production_meter_does_not_expose_evidence_relaxation_or_fake_root_flags():
    parser = meter_cli_module.parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--campaign",
                "formal",
                "--mode",
                "FIXED_GOOD",
                "--interval",
                "300",
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--campaign",
                "formal",
                "--mode",
                "FIXED_GOOD",
                "--sys-root",
                "/tmp/fake-sys",
            ]
        )
    with pytest.raises(SystemExit):
        cli_module.parser().parse_args(
            [
                "net-benefit",
                "compare",
                "a1",
                "b1",
                "b2",
                "a2",
                "--max-gap-seconds",
                "301",
            ]
        )


def test_service_run_returns_non_restartable_exit_for_legacy_schema(monkeypatch, capsys):
    def fail_service(*_args, **_kwargs):
        raise LegacyDatabaseError("database schema 9 is not supported by schema 10")

    monkeypatch.setattr(cli_module, "PowerLabService", fail_service)
    result = cli_module.cmd_service_run(SimpleNamespace(config=None, iterations=None))
    assert result == 78
    assert "reset-runtime --yes" in capsys.readouterr().err
