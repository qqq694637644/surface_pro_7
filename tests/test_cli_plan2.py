from __future__ import annotations

from pathlib import Path

import pytest

from sp7_powerlab.cli import _meter_pair
from sp7_powerlab.minimal_meter_cli import _capture_context_change_reason
from sp7_powerlab.storage import Database


def _meter_run(
    db: Database,
    *,
    mode: str,
    campaign: str,
    epoch: str,
    envelope: str = "INTERACTIVE_EFFICIENT",
) -> str:
    run_id = db.start_minimal_meter_run(
        capture_mode=mode,
        campaign_id=campaign,
        evidence_epoch_id=epoch,
        battery_epoch=1,
        battery_identity_hash="battery",
        hard_identity_hash="hard",
        calibration_version=1,
        evidence_semantics_version=4,
        envelope=envelope,
        payload={"capture_contract_version": 1},
    )
    db.finish_minimal_meter_run(run_id, {"sample_count": 2})
    return run_id


def test_meter_pair_requires_capture_time_provenance(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=4,
            payload={},
        )
        reference = _meter_run(db, mode="FIXED_GOOD", campaign="campaign-a", epoch=epoch)
        candidate = _meter_run(db, mode="MONITORING", campaign="campaign-a", epoch=epoch)
        ref_run, cand_run, result_mode = _meter_pair(db, reference, candidate)
        assert ref_run["run_id"] == reference
        assert cand_run["run_id"] == candidate
        assert result_mode == "MONITORING_OVERHEAD"

        wrong_campaign = _meter_run(
            db,
            mode="MONITORING",
            campaign="campaign-b",
            epoch=epoch,
        )
        with pytest.raises(SystemExit, match="campaign_id"):
            _meter_pair(db, reference, wrong_campaign)
    finally:
        db.close()


def test_meter_pair_rejects_runs_from_stale_epoch(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        old_epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=4,
            payload={},
        )
        reference = _meter_run(db, mode="FIXED_GOOD", campaign="campaign-a", epoch=old_epoch)
        candidate = _meter_run(
            db,
            mode="DYNAMIC_CONTROLLER",
            campaign="campaign-a",
            epoch=old_epoch,
        )
        db.ensure_evidence_epoch(
            hard_identity_hash="hard-new",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=4,
            payload={},
        )
        with pytest.raises(SystemExit, match="current evidence epoch"):
            _meter_pair(db, reference, candidate)
    finally:
        db.close()


def test_dynamic_capture_allows_verified_envelope_changes_but_fixed_capture_does_not():
    start = {
        "evidence_epoch_id": "epoch",
        "battery_epoch": 1,
        "battery_identity_hash": "battery",
        "hard_identity_hash": "hard",
        "calibration_version": 1,
        "evidence_semantics_version": 4,
        "envelope": "INTERACTIVE_EFFICIENT",
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
