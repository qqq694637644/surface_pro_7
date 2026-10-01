from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import sp7_powerlab.cli as cli_module
from sp7_powerlab.cli import _meter_campaign
from sp7_powerlab.config import load_config
from sp7_powerlab.minimal_meter_cli import (
    _capture_context_change_reason,
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
    order: int,
) -> str:
    run_id = db.start_minimal_meter_run(
        capture_mode=mode,
        campaign_id=campaign,
        evidence_epoch_id=epoch,
        battery_epoch=1,
        battery_identity_hash="battery",
        hard_identity_hash="hard",
        calibration_version=1,
        evidence_semantics_version=5,
        envelope=envelope,
        envelope_content_hash=envelope_hash,
        payload={"capture_contract_version": 2},
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
            evidence_semantics_version=5,
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


def test_meter_campaign_rejects_runs_from_stale_epoch(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        old_epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=5,
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
            evidence_semantics_version=5,
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
            evidence_semantics_version=5,
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
            evidence_semantics_version=5,
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


def test_dynamic_capture_allows_verified_envelope_changes_but_fixed_capture_does_not():
    start = {
        "evidence_epoch_id": "epoch",
        "battery_epoch": 1,
        "battery_identity_hash": "battery",
        "hard_identity_hash": "hard",
        "calibration_version": 1,
        "evidence_semantics_version": 5,
        "envelope": "INTERACTIVE_EFFICIENT",
        "envelope_content_hash": "fixed",
    }
    final = {**start, "envelope": "REMOTE_EFFICIENT"}
    assert _capture_context_change_reason(start, final, mode="DYNAMIC_CONTROLLER") is None
    assert _capture_context_change_reason(start, final, mode="FULL_POWERLAB") is None
    assert (
        _capture_context_change_reason(start, final, mode="FIXED_GOOD")
        == "fixed_capture_envelope_changed"
    )
    assert (
        _capture_context_change_reason(start, final, mode="MONITORING")
        == "fixed_capture_envelope_changed"
    )


def test_service_mode_status_verifies_runtime_mode(project_root: Path):
    config = load_config(project_root)
    config.data["automation"]["level"] = 0
    db = Database(project_root / "runtime/meter-mode.sqlite3")
    try:
        fixed = _service_mode_status(config, db, "FIXED_GOOD")
        assert fixed["service_heartbeat_fresh"] is False

        db.set_meta(
            "service_heartbeat",
            {
                "ts": time.time(),
                "automation_level": 0,
                "control_state": "READ_ONLY",
                "current_envelope": "INTERACTIVE_EFFICIENT",
            },
        )
        with pytest.raises(RuntimeError, match="service to be stopped"):
            _service_mode_status(config, db, "FIXED_GOOD")
        assert _service_mode_status(config, db, "MONITORING")["runtime_automation_level"] == 0

        config.data["automation"]["level"] = 1
        db.set_meta(
            "service_heartbeat",
            {
                "ts": time.time(),
                "automation_level": 1,
                "control_state": "CONTROL_ALLOWED",
                "current_envelope": "INTERACTIVE_EFFICIENT",
            },
        )
        assert (
            _service_mode_status(config, db, "DYNAMIC_CONTROLLER")["runtime_automation_level"] == 1
        )
        with pytest.raises(RuntimeError, match="automation.level>=2"):
            _service_mode_status(config, db, "FULL_POWERLAB")
    finally:
        db.close()


def test_service_run_returns_non_restartable_exit_for_legacy_schema(monkeypatch, capsys):
    def fail_service(*_args, **_kwargs):
        raise LegacyDatabaseError("database schema 6 is not supported by schema 7")

    monkeypatch.setattr(cli_module, "PowerLabService", fail_service)
    result = cli_module.cmd_service_run(SimpleNamespace(config=None, iterations=None))
    assert result == 78
    assert "reset-runtime --yes" in capsys.readouterr().err
