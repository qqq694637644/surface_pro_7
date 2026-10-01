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
