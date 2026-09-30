import time
import sqlite3

import pytest

from sp7_powerlab.storage import Database, SCHEMA_VERSION


def test_database_migrates_and_stores_context_policy(tmp_path):
    db = Database(tmp_path / "powerlab.sqlite3")
    try:
        assert db.health()["schema_version"] == SCHEMA_VERSION
        db.upsert_profile(
            {
                "profile_id": "p1",
                "backend": "noop",
                "backend_profile": None,
                "parameters": {},
                "status": "verified",
                "evidence": {},
            }
        )
        db.set_context_policy("reading", "p1", source="test", evidence={"ok": True})
        policy = db.context_policy("reading")
        assert policy["profile_id"] == "p1"
        assert policy["evidence"]["ok"] is True
    finally:
        db.close()


def test_task_runs_roundtrip(tmp_path):
    db = Database(tmp_path / "powerlab.sqlite3")
    try:
        now = time.time()
        db.add_task_run(
            {
                "task_run_id": "job-1",
                "scene": "compile",
                "label": "build",
                "start_ts": now,
                "end_ts": now + 30,
                "duration_s": 30,
                "energy_wh": 0.08,
                "avg_power_w": 9.6,
                "profile_id": "p",
                "trial_id": None,
                "exit_code": 0,
                "quality": {"valid": True},
                "metadata": {"command": ["make"]},
            }
        )
        rows = db.recent_task_runs(now - 1, scene="compile", label="build")
        assert len(rows) == 1
        assert rows[0]["energy_wh"] == 0.08
        assert rows[0]["quality"]["valid"] is True
    finally:
        db.close()


def test_minute_rollup_persists_energy_summary(tmp_path):
    db = Database(tmp_path / "powerlab.sqlite3")
    try:
        samples = [
            {
                "ts": 60.0,
                "battery_status": "Discharging",
                "power_w": 4.0,
                "context_scene": "reading",
                "cpu_usage": 5.0,
                "temp_c": 40.0,
                "brightness_pct": 30.0,
            },
            {
                "ts": 70.0,
                "battery_status": "Discharging",
                "power_w": 6.0,
                "context_scene": "reading",
                "cpu_usage": 7.0,
                "temp_c": 42.0,
                "brightness_pct": 30.0,
            },
        ]
        db.upsert_minute_rollups(60.0, samples, max_gap_seconds=45)
        rows = db.recent_rollups(0, scene="reading")
        assert len(rows) == 1
        assert rows[0]["avg_power_w"] == pytest.approx(5.0)
        assert rows[0]["valid_duration_s"] == 10.0
    finally:
        db.close()


def test_sessions_and_task_runs_can_be_filtered_by_profile(tmp_path):
    db = Database(tmp_path / "powerlab.sqlite3")
    try:
        db.add_session(
            {
                "session_id": "s-a",
                "start_ts": 10.0,
                "end_ts": 20.0,
                "context_id": "ctx",
                "scene": "reading",
                "profile_id": "profile-a",
                "valid_duration_s": 10.0,
                "energy_wh": 0.02,
                "avg_power_w": 7.2,
                "sample_count": 3,
                "quality": {"valid": True},
            }
        )
        db.add_session(
            {
                "session_id": "s-b",
                "start_ts": 30.0,
                "end_ts": 40.0,
                "context_id": "ctx",
                "scene": "reading",
                "profile_id": "profile-b",
                "valid_duration_s": 10.0,
                "energy_wh": 0.01,
                "avg_power_w": 3.6,
                "sample_count": 3,
                "quality": {"valid": True},
            }
        )
        assert [
            row["session_id"]
            for row in db.recent_sessions(0, scene="reading", profile_id="profile-a")
        ] == ["s-a"]

        for run_id, profile_id, trial_id in (
            ("job-a", "profile-a", None),
            ("job-b", "profile-b", "trial-b"),
        ):
            db.add_task_run(
                {
                    "task_run_id": run_id,
                    "scene": "compile",
                    "label": "build",
                    "start_ts": 50.0,
                    "end_ts": 60.0,
                    "duration_s": 10.0,
                    "energy_wh": 0.05,
                    "avg_power_w": 18.0,
                    "profile_id": profile_id,
                    "trial_id": trial_id,
                    "exit_code": 0,
                    "quality": {"valid": True},
                    "metadata": {},
                }
            )
        assert [
            row["task_run_id"]
            for row in db.recent_task_runs(
                0, scene="compile", label="build", profile_id="profile-a"
            )
        ] == ["job-a"]
        assert [
            row["task_run_id"]
            for row in db.recent_task_runs(
                0, scene="compile", label="build", trial_id="trial-b"
            )
        ] == ["job-b"]
    finally:
        db.close()


def test_database_migrates_pre_profile_aware_tables(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """CREATE TABLE process_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                pid INTEGER NOT NULL,
                start_time REAL NOT NULL,
                name TEXT,
                executable TEXT,
                cpu_percent REAL,
                rss_bytes INTEGER,
                read_bytes INTEGER,
                write_bytes INTEGER,
                class TEXT,
                UNIQUE(ts, pid, start_time)
            )"""
        )
        conn.execute(
            """CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                context_id TEXT,
                scene TEXT NOT NULL,
                valid_duration_s REAL NOT NULL,
                energy_wh REAL,
                avg_power_w REAL,
                sample_count INTEGER NOT NULL,
                quality_json TEXT NOT NULL
            )"""
        )
        conn.commit()
    finally:
        conn.close()

    db = Database(path)
    try:
        process_columns = {
            row[1] for row in db.conn.execute("PRAGMA table_info(process_samples)")
        }
        session_columns = {
            row[1] for row in db.conn.execute("PRAGMA table_info(sessions)")
        }
        assert "ppid" in process_columns
        assert "profile_id" in session_columns
        assert db.health()["schema_version"] == SCHEMA_VERSION
    finally:
        db.close()
