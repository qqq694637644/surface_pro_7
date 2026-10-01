from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sp7_powerlab.storage import SCHEMA_VERSION, Database, LegacyDatabaseError


def test_fresh_database_has_current_schema(tmp_path: Path):
    db = Database(tmp_path / "powerlab.sqlite3")
    try:
        assert db.health()["schema_version"] == SCHEMA_VERSION
        assert db.health()["samples"] == 0
    finally:
        db.close()


def test_v2_database_fails_fast_after_plan2_breaking_schema(tmp_path: Path):
    path = tmp_path / "powerlab.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
    conn.execute("INSERT INTO metadata(key,value_json) VALUES('schema_version','2')")
    conn.commit()
    conn.close()
    with pytest.raises(LegacyDatabaseError, match="schema 2"):
        Database(path)


def test_v3_database_fails_fast_after_arm_measurement_schema_cleanup(tmp_path: Path):
    path = tmp_path / "powerlab.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
    conn.execute("INSERT INTO metadata(key,value_json) VALUES('schema_version','3')")
    conn.commit()
    conn.close()
    with pytest.raises(LegacyDatabaseError, match="schema 3"):
        Database(path)


def test_v5_database_fails_fast_after_evidence_integrity_schema_break(tmp_path: Path):
    path = tmp_path / "powerlab.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
    conn.execute("INSERT INTO metadata(key,value_json) VALUES('schema_version','5')")
    conn.commit()
    conn.close()
    with pytest.raises(LegacyDatabaseError, match="schema 5"):
        Database(path)


def test_v1_database_fails_fast(tmp_path: Path):
    path = tmp_path / "powerlab.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE contexts(id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with pytest.raises(LegacyDatabaseError):
        Database(path)


def test_battery_epoch_changes_on_identity(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        first = db.ensure_battery_epoch(
            identity_hash="a",
            energy_full_wh=40.0,
            payload={"model": "one"},
        )
        same = db.ensure_battery_epoch(
            identity_hash="a",
            energy_full_wh=39.5,
            payload={"model": "one"},
        )
        second = db.ensure_battery_epoch(
            identity_hash="b",
            energy_full_wh=39.5,
            payload={"model": "two"},
        )
        assert first == same == 1
        assert second == 2
    finally:
        db.close()


def test_battery_epoch_changes_on_large_capacity_jump(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        assert db.ensure_battery_epoch(identity_hash="same", energy_full_wh=20.0, payload={}) == 1
        assert db.ensure_battery_epoch(identity_hash="same", energy_full_wh=40.0, payload={}) == 2
    finally:
        db.close()


def test_minimal_meter_run_persists_capture_provenance(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    try:
        run_id = db.start_minimal_meter_run(
            capture_mode="FIXED_GOOD",
            campaign_id="campaign-a",
            evidence_epoch_id="epoch-a",
            battery_epoch=2,
            battery_identity_hash="battery-a",
            hard_identity_hash="hard-a",
            calibration_version=3,
            evidence_semantics_version=4,
            envelope="INTERACTIVE_EFFICIENT",
            payload={"capture_contract_version": 1},
        )
        db.add_minimal_meter_sample(
            run_id,
            {
                "ts": 10.0,
                "battery_status": "Discharging",
                "battery_power_w": 5.0,
                "battery_energy_wh": 30.0,
            },
        )
        db.finish_minimal_meter_run(run_id, {"sample_count": 1}, status="INVALID")
        run = db.minimal_meter_run(run_id)
        assert run is not None
        assert run["status"] == "INVALID"
        assert run["capture_mode"] == "FIXED_GOOD"
        assert run["campaign_id"] == "campaign-a"
        assert run["evidence_epoch_id"] == "epoch-a"
        assert run["battery_epoch"] == 2
        assert run["battery_identity_hash"] == "battery-a"
        assert run["hard_identity_hash"] == "hard-a"
        assert run["calibration_version"] == 3
        assert run["evidence_semantics_version"] == 4
        assert run["envelope"] == "INTERACTIVE_EFFICIENT"
        assert len(run["samples"]) == 1
    finally:
        db.close()
