from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 7

DDL = [
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        wall_ts TEXT NOT NULL,
        battery_status TEXT,
        power_w REAL,
        energy_wh REAL,
        battery_pct REAL,
        cpu_usage REAL,
        load1 REAL,
        freq_khz INTEGER,
        epp TEXT,
        temp_c REAL,
        brightness_pct REAL,
        wifi_rx_bytes INTEGER,
        wifi_tx_bytes INTEGER,
        active_app TEXT,
        window_title TEXT,
        afk INTEGER,
        context_scene TEXT,
        context_confidence REAL,
        profile_id TEXT,
        raw_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)",
    "CREATE INDEX IF NOT EXISTS idx_samples_context_ts ON samples(context_scene, ts)",
    """CREATE TABLE IF NOT EXISTS app_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts REAL NOT NULL,
        end_ts REAL,
        app TEXT,
        executable TEXT,
        window_title TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        afk INTEGER NOT NULL DEFAULT 0,
        source TEXT,
        raw_json TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_app_events_ts ON app_events(start_ts)",
    """CREATE TABLE IF NOT EXISTS process_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        pid INTEGER NOT NULL,
        ppid INTEGER,
        start_time REAL NOT NULL,
        name TEXT,
        executable TEXT,
        cpu_percent REAL,
        rss_bytes INTEGER,
        read_bytes INTEGER,
        write_bytes INTEGER,
        class TEXT,
        UNIQUE(ts, pid, start_time)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_process_samples_ts ON process_samples(ts)",
    """CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        event TEXT NOT NULL,
        details_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS contexts (
        context_id TEXT PRIMARY KEY,
        scene TEXT NOT NULL,
        features_json TEXT NOT NULL,
        confidence REAL NOT NULL,
        rule_version TEXT NOT NULL,
        first_seen_ts REAL NOT NULL,
        last_seen_ts REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL NOT NULL,
        context_id TEXT,
        scene TEXT NOT NULL,
        profile_id TEXT,
        valid_duration_s REAL NOT NULL,
        energy_wh REAL,
        avg_power_w REAL,
        sample_count INTEGER NOT NULL,
        quality_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_scene_ts ON sessions(scene, start_ts)",
    """CREATE TABLE IF NOT EXISTS minute_rollups (
        bucket_ts REAL NOT NULL,
        scene TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        valid_duration_s REAL NOT NULL,
        energy_wh REAL,
        avg_power_w REAL,
        avg_cpu REAL,
        avg_temp REAL,
        avg_brightness REAL,
        quality_json TEXT NOT NULL,
        PRIMARY KEY(bucket_ts, scene)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_rollups_scene_ts ON minute_rollups(scene,bucket_ts)",
    """CREATE TABLE IF NOT EXISTS profiles (
        profile_id TEXT PRIMARY KEY,
        backend TEXT NOT NULL,
        backend_profile TEXT,
        content_hash TEXT,
        parameters_json TEXT NOT NULL,
        status TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        last_validated_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS profile_applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        profile_id TEXT,
        context_id TEXT,
        reason TEXT,
        before_json TEXT,
        after_json TEXT,
        success INTEGER NOT NULL,
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS context_policies (
        scene TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        updated_ts REAL NOT NULL,
        source TEXT NOT NULL,
        evidence_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trials (
        trial_id TEXT PRIMARY KEY,
        proposal_id TEXT,
        context_scene TEXT,
        baseline_profile TEXT,
        candidate_profile TEXT,
        parameter TEXT,
        state TEXT NOT NULL,
        start_ts REAL,
        end_ts REAL,
        snapshot_json TEXT,
        proposal_json TEXT,
        result_json TEXT,
        last_error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS trial_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trial_id TEXT NOT NULL,
        ts REAL NOT NULL,
        verdict TEXT NOT NULL,
        objective_json TEXT NOT NULL,
        quality_json TEXT NOT NULL,
        FOREIGN KEY(trial_id) REFERENCES trials(trial_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        trial_id TEXT,
        session_id TEXT,
        decision TEXT,
        responsiveness INTEGER,
        stability TEXT,
        suspend_wake TEXT,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS task_runs (
        task_run_id TEXT PRIMARY KEY,
        scene TEXT NOT NULL,
        label TEXT NOT NULL,
        start_ts REAL NOT NULL,
        end_ts REAL NOT NULL,
        duration_s REAL NOT NULL,
        energy_wh REAL,
        avg_power_w REAL,
        profile_id TEXT,
        trial_id TEXT,
        exit_code INTEGER,
        quality_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_task_runs_scene_ts ON task_runs(scene,start_ts)",
    """CREATE TABLE IF NOT EXISTS llm_runs (
        run_id TEXT PRIMARY KEY,
        ts REAL NOT NULL,
        input_hash TEXT,
        input_json TEXT NOT NULL,
        output_json TEXT,
        action TEXT,
        status TEXT NOT NULL,
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        ts REAL NOT NULL,
        source TEXT NOT NULL,
        action TEXT NOT NULL,
        reason TEXT,
        payload_json TEXT NOT NULL,
        applied INTEGER NOT NULL DEFAULT 0,
        result_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS system_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        fingerprint TEXT NOT NULL,
        versions_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS battery_health (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        health_percent REAL,
        energy_full_wh REAL,
        energy_full_design_wh REAL,
        cycle_count INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS rejections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        context_scene TEXT,
        parameter TEXT,
        value_json TEXT,
        reason TEXT NOT NULL,
        source TEXT NOT NULL,
        UNIQUE(context_scene, parameter, value_json, reason)
    )""",
]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        with self.conn:
            for stmt in DDL:
                self.conn.execute(stmt)
            columns = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(process_samples)")
            }
            if "ppid" not in columns:
                self.conn.execute(
                    "ALTER TABLE process_samples ADD COLUMN ppid INTEGER"
                )
            session_columns = {
                row[1] for row in self.conn.execute("PRAGMA table_info(sessions)")
            }
            if "profile_id" not in session_columns:
                self.conn.execute(
                    "ALTER TABLE sessions ADD COLUMN profile_id TEXT"
                )
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def add_sample(self, sample: dict[str, Any]) -> int:
        raw = dict(sample)
        cur = self.conn.execute(
            """INSERT INTO samples(
                ts,wall_ts,battery_status,power_w,energy_wh,battery_pct,
                cpu_usage,load1,freq_khz,epp,temp_c,brightness_pct,
                wifi_rx_bytes,wifi_tx_bytes,active_app,window_title,afk,
                context_scene,context_confidence,profile_id,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sample["ts"], sample["wall_ts"], sample.get("battery_status"),
                sample.get("power_w"), sample.get("energy_wh"), sample.get("battery_pct"),
                sample.get("cpu_usage"), sample.get("load1"), sample.get("freq_khz"),
                sample.get("epp"), sample.get("temp_c"), sample.get("brightness_pct"),
                sample.get("wifi_rx_bytes"), sample.get("wifi_tx_bytes"),
                sample.get("active_app"), sample.get("window_title"),
                int(bool(sample.get("afk"))) if sample.get("afk") is not None else None,
                sample.get("context_scene"), sample.get("context_confidence"),
                sample.get("profile_id"), _json(raw),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_process_samples(self, ts: float, processes: Iterable[dict[str, Any]]) -> None:
        rows = [
            (
                ts, p["pid"], p.get("ppid"), p["start_time"], p.get("name"), p.get("executable"),
                p.get("cpu_percent"), p.get("rss_bytes"), p.get("read_bytes"),
                p.get("write_bytes"), p.get("class"),
            )
            for p in processes
        ]
        if not rows:
            return
        with self.conn:
            self.conn.executemany(
                """INSERT OR IGNORE INTO process_samples(
                    ts,pid,ppid,start_time,name,executable,cpu_percent,rss_bytes,
                    read_bytes,write_bytes,class
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def add_app_event(self, event: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """INSERT INTO app_events(
                start_ts,end_ts,app,executable,window_title,active,afk,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                event["start_ts"], event.get("end_ts"), event.get("app"),
                event.get("executable"), event.get("window_title"),
                int(bool(event.get("active", True))), int(bool(event.get("afk", False))),
                event.get("source"), _json(event),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def end_app_event(self, event_id: int, end_ts: float) -> None:
        self.conn.execute(
            "UPDATE app_events SET end_ts=? WHERE id=? AND end_ts IS NULL",
            (end_ts, event_id),
        )
        self.conn.commit()

    def recent_app_events(self, since_ts: float) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM app_events
            WHERE start_ts>=? OR (end_ts IS NOT NULL AND end_ts>=?)
            ORDER BY start_ts""",
            (since_ts, since_ts),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["raw"] = json.loads(item.pop("raw_json")) if item.get("raw_json") else {}
            result.append(item)
        return result

    def add_system_event(self, event: str, details: dict[str, Any] | None = None, ts: float | None = None) -> None:
        self.conn.execute(
            "INSERT INTO system_events(ts,event,details_json) VALUES(?,?,?)",
            (ts or time.time(), event, _json(details or {})),
        )
        self.conn.commit()

    def recent_system_events(self, since_ts: float, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM system_events
            WHERE ts>=? ORDER BY ts DESC LIMIT ?""",
            (since_ts, limit),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json")) if item.get("details_json") else {}
            result.append(item)
        return result

    def upsert_context(self, context: dict[str, Any], ts: float) -> str:
        context_id = str(context["context_id"])
        self.conn.execute(
            """INSERT INTO contexts(
                context_id,scene,features_json,confidence,rule_version,first_seen_ts,last_seen_ts
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(context_id) DO UPDATE SET
                last_seen_ts=excluded.last_seen_ts,
                confidence=excluded.confidence,
                features_json=excluded.features_json""",
            (
                context_id, context["scene"], _json(context.get("features", {})),
                float(context.get("confidence", 0.0)), context.get("rule_version", "unknown"),
                ts, ts,
            ),
        )
        self.conn.commit()
        return context_id

    def add_session(self, session: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO sessions(
                session_id,start_ts,end_ts,context_id,scene,profile_id,valid_duration_s,
                energy_wh,avg_power_w,sample_count,quality_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session["session_id"], session["start_ts"], session["end_ts"],
                session.get("context_id"), session["scene"], session.get("profile_id"),
                session["valid_duration_s"], session.get("energy_wh"),
                session.get("avg_power_w"), session["sample_count"],
                _json(session.get("quality", {})),
            ),
        )
        self.conn.commit()

    def recent_samples(self, since_ts: float, scene: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT raw_json FROM samples WHERE ts>=?"
        args: list[Any] = [since_ts]
        if scene:
            sql += " AND context_scene=?"
            args.append(scene)
        sql += " ORDER BY ts"
        return [json.loads(row["raw_json"]) for row in self.conn.execute(sql, args)]

    def samples_between(
        self,
        start_ts: float,
        end_ts: float,
        scene: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT raw_json FROM samples WHERE ts>=? AND ts<=?"
        args: list[Any] = [start_ts, end_ts]
        if scene:
            sql += " AND context_scene=?"
            args.append(scene)
        sql += " ORDER BY ts"
        return [json.loads(row["raw_json"]) for row in self.conn.execute(sql, args)]

    def latest_sample(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT raw_json FROM samples ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["raw_json"]) if row else None

    def recent_sessions(
        self,
        since_ts: float,
        scene: str | None = None,
        profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions WHERE start_ts>=?"
        args: list[Any] = [since_ts]
        if scene:
            sql += " AND scene=?"
            args.append(scene)
        if profile_id:
            sql += " AND profile_id=?"
            args.append(profile_id)
        sql += " ORDER BY start_ts"
        return [dict(row) for row in self.conn.execute(sql, args)]

    def upsert_minute_rollups(self, bucket_ts: float, samples: list[dict[str, Any]], max_gap_seconds: float) -> None:
        from .quality import data_quality

        grouped: dict[str, list[dict[str, Any]]] = {}
        for sample in samples:
            scene = str(sample.get("context_scene") or "unknown")
            grouped.setdefault(scene, []).append(sample)
        with self.conn:
            for scene, rows in grouped.items():
                quality = data_quality(
                    rows,
                    max_gap_seconds=max_gap_seconds,
                    min_samples=2,
                    min_valid_seconds=0.0,
                    require_single_scene=True,
                )
                def average(key: str) -> float | None:
                    values = [
                        float(row[key])
                        for row in rows
                        if isinstance(row.get(key), (int, float))
                    ]
                    return sum(values) / len(values) if values else None
                self.conn.execute(
                    """INSERT OR REPLACE INTO minute_rollups(
                        bucket_ts,scene,sample_count,valid_duration_s,energy_wh,
                        avg_power_w,avg_cpu,avg_temp,avg_brightness,quality_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        bucket_ts, scene, len(rows), quality["valid_duration_s"],
                        quality["energy_wh"], quality["average_power_w"],
                        average("cpu_usage"), average("temp_c"),
                        average("brightness_pct"), _json(quality),
                    ),
                )

    def recent_rollups(self, since_ts: float, scene: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM minute_rollups WHERE bucket_ts>=?"
        args: list[Any] = [since_ts]
        if scene:
            sql += " AND scene=?"
            args.append(scene)
        sql += " ORDER BY bucket_ts"
        result = []
        for row in self.conn.execute(sql, args):
            item = dict(row)
            item["quality"] = json.loads(item.pop("quality_json"))
            result.append(item)
        return result

    def upsert_profile(self, profile: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO profiles(
                profile_id,backend,backend_profile,content_hash,parameters_json,status,
                evidence_json,last_validated_json
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
                backend=excluded.backend,
                backend_profile=excluded.backend_profile,
                content_hash=excluded.content_hash,
                parameters_json=excluded.parameters_json,
                status=excluded.status,
                evidence_json=excluded.evidence_json,
                last_validated_json=COALESCE(
                    excluded.last_validated_json,
                    profiles.last_validated_json
                )""",
            (
                profile["profile_id"], profile["backend"], profile.get("backend_profile"),
                profile.get("content_hash"), _json(profile.get("parameters", {})),
                profile.get("status", "experimental"), _json(profile.get("evidence", {})),
                _json(profile.get("last_validated")) if profile.get("last_validated") else None,
            ),
        )
        self.conn.commit()

    def profiles(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM profiles"
        args: list[Any] = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY profile_id"
        rows = []
        for row in self.conn.execute(sql, args):
            item = dict(row)
            item["parameters"] = json.loads(item.pop("parameters_json"))
            item["evidence"] = json.loads(item.pop("evidence_json"))
            if item.get("last_validated_json"):
                item["last_validated"] = json.loads(item.pop("last_validated_json"))
            else:
                item.pop("last_validated_json", None)
                item["last_validated"] = None
            rows.append(item)
        return rows

    def set_profile_status(self, profile_id: str, status: str) -> None:
        self.conn.execute("UPDATE profiles SET status=? WHERE profile_id=?", (status, profile_id))
        self.conn.commit()

    def add_profile_application(self, record: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO profile_applications(
                ts,profile_id,context_id,reason,before_json,after_json,success,error
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                record.get("ts", time.time()), record.get("profile_id"), record.get("context_id"),
                record.get("reason"), _json(record.get("before", {})),
                _json(record.get("after", {})), int(bool(record.get("success"))),
                record.get("error"),
            ),
        )
        self.conn.commit()

    def set_context_policy(
        self,
        scene: str,
        profile_id: str,
        *,
        source: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO context_policies(scene,profile_id,updated_ts,source,evidence_json)
            VALUES(?,?,?,?,?)
            ON CONFLICT(scene) DO UPDATE SET
                profile_id=excluded.profile_id,
                updated_ts=excluded.updated_ts,
                source=excluded.source,
                evidence_json=excluded.evidence_json""",
            (scene, profile_id, time.time(), source, _json(evidence or {})),
        )
        self.conn.commit()

    def context_policy(self, scene: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM context_policies WHERE scene=?", (scene,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        return item

    def context_policies(self) -> list[dict[str, Any]]:
        result = []
        for row in self.conn.execute("SELECT * FROM context_policies ORDER BY scene"):
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            result.append(item)
        return result

    def create_trial(self, trial: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO trials(
                trial_id,proposal_id,context_scene,baseline_profile,candidate_profile,
                parameter,state,start_ts,end_ts,snapshot_json,proposal_json,result_json,last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trial["trial_id"], trial.get("proposal_id"), trial.get("context_scene"),
                trial.get("baseline_profile"), trial.get("candidate_profile"),
                trial.get("parameter"), trial["state"], trial.get("start_ts"),
                trial.get("end_ts"), _json(trial.get("snapshot", {})),
                _json(trial.get("proposal", {})), _json(trial.get("result", {})),
                trial.get("last_error"),
            ),
        )
        self.conn.commit()

    def update_trial(self, trial_id: str, **fields: Any) -> None:
        allowed = {
            "state",
            "start_ts",
            "end_ts",
            "baseline_profile",
            "candidate_profile",
            "snapshot_json",
            "result_json",
            "last_error",
        }
        pairs = []
        values: list[Any] = []
        for key, value in fields.items():
            db_key = key
            if key == "snapshot":
                db_key, value = "snapshot_json", _json(value)
            elif key == "result":
                db_key, value = "result_json", _json(value)
            if db_key not in allowed:
                continue
            pairs.append(f"{db_key}=?")
            values.append(value)
        if not pairs:
            return
        values.append(trial_id)
        self.conn.execute(f"UPDATE trials SET {','.join(pairs)} WHERE trial_id=?", values)
        self.conn.commit()

    def get_trial(self, trial_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM trials WHERE trial_id=?", (trial_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        for key in ("snapshot_json", "proposal_json", "result_json"):
            raw = item.pop(key)
            item[key.removesuffix("_json")] = json.loads(raw) if raw else {}
        return item

    def active_trial(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT trial_id FROM trials
            WHERE state NOT IN ('PROMOTED','REJECTED','ROLLED_BACK','FAILED','INSUFFICIENT_DATA')
            ORDER BY COALESCE(start_ts,0) DESC LIMIT 1"""
        ).fetchone()
        return self.get_trial(row["trial_id"]) if row else None

    def add_trial_result(self, trial_id: str, verdict: str, objective: dict[str, Any], quality: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO trial_results(trial_id,ts,verdict,objective_json,quality_json) VALUES(?,?,?,?,?)",
            (trial_id, time.time(), verdict, _json(objective), _json(quality)),
        )
        self.conn.commit()

    def add_feedback(self, feedback: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO user_feedback(
                ts,trial_id,session_id,decision,responsiveness,stability,suspend_wake,notes
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                feedback.get("ts", time.time()), feedback.get("trial_id"), feedback.get("session_id"),
                feedback.get("decision"), feedback.get("responsiveness"),
                feedback.get("stability"), feedback.get("suspend_wake"), feedback.get("notes"),
            ),
        )
        self.conn.commit()

    def recent_feedback(self, limit: int = 200) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM user_feedback ORDER BY ts DESC LIMIT ?", (limit,)
            )
        ]

    def add_task_run(self, run: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO task_runs(
                task_run_id,scene,label,start_ts,end_ts,duration_s,energy_wh,
                avg_power_w,profile_id,trial_id,exit_code,quality_json,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run["task_run_id"], run["scene"], run["label"], run["start_ts"],
                run["end_ts"], run["duration_s"], run.get("energy_wh"),
                run.get("avg_power_w"), run.get("profile_id"), run.get("trial_id"),
                run.get("exit_code"), _json(run.get("quality", {})),
                _json(run.get("metadata", {})),
            ),
        )
        self.conn.commit()

    def recent_task_runs(
        self,
        since_ts: float,
        scene: str | None = None,
        label: str | None = None,
        profile_id: str | None = None,
        trial_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM task_runs WHERE start_ts>=?"
        args: list[Any] = [since_ts]
        if scene:
            sql += " AND scene=?"
            args.append(scene)
        if label:
            sql += " AND label=?"
            args.append(label)
        if profile_id:
            sql += " AND profile_id=?"
            args.append(profile_id)
        if trial_id:
            sql += " AND trial_id=?"
            args.append(trial_id)
        sql += " ORDER BY start_ts"
        result = []
        for row in self.conn.execute(sql, args):
            item = dict(row)
            item["quality"] = json.loads(item.pop("quality_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def add_rejection(self, context_scene: str | None, parameter: str | None, value: Any, reason: str, source: str) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO rejections(
                ts,context_scene,parameter,value_json,reason,source
            ) VALUES(?,?,?,?,?,?)""",
            (time.time(), context_scene, parameter, _json(value), reason, source),
        )
        self.conn.commit()

    def rejections(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM rejections ORDER BY ts DESC LIMIT ?", (limit,)
        )
        result = []
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json")) if item.get("value_json") else None
            result.append(item)
        return result

    def add_llm_run(self, run: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO llm_runs(
                run_id,ts,input_hash,input_json,output_json,action,status,error
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                run["run_id"], run.get("ts", time.time()), run.get("input_hash"),
                _json(run.get("input", {})),
                _json(run.get("output")) if run.get("output") is not None else None,
                run.get("action"), run.get("status", "created"), run.get("error"),
            ),
        )
        self.conn.commit()

    def add_decision(self, decision: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO decisions(
                decision_id,ts,source,action,reason,payload_json,applied,result_json
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                decision["decision_id"], decision.get("ts", time.time()),
                decision.get("source", "unknown"), decision["action"], decision.get("reason"),
                _json(decision.get("payload", {})), int(bool(decision.get("applied"))),
                _json(decision.get("result", {})),
            ),
        )
        self.conn.commit()

    def recent_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        result = []
        for row in self.conn.execute(
            "SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,)
        ):
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else {}
            result.append(item)
        return result

    def add_version_snapshot(self, fingerprint: str, versions: dict[str, Any]) -> None:
        last = self.conn.execute(
            "SELECT fingerprint FROM system_versions ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if last and last["fingerprint"] == fingerprint:
            return
        self.conn.execute(
            "INSERT INTO system_versions(ts,fingerprint,versions_json) VALUES(?,?,?)",
            (time.time(), fingerprint, _json(versions)),
        )
        self.conn.commit()

    def latest_version_snapshot(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM system_versions ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["versions"] = json.loads(item.pop("versions_json"))
        return item

    def add_battery_health(self, battery: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO battery_health(
                ts,health_percent,energy_full_wh,energy_full_design_wh,cycle_count
            ) VALUES(?,?,?,?,?)""",
            (
                time.time(), battery.get("health_percent"), battery.get("energy_full_wh"),
                battery.get("energy_full_design_wh"), battery.get("cycle_count"),
            ),
        )
        self.conn.commit()

    def latest_battery_health(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM battery_health ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def prune_raw(
        self,
        older_than_ts: float,
        *,
        keep_rollups_forever: bool = True,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.conn:
            for table, column in (
                ("samples", "ts"),
                ("process_samples", "ts"),
                ("app_events", "start_ts"),
                ("system_events", "ts"),
            ):
                cur = self.conn.execute(f"DELETE FROM {table} WHERE {column}<?", (older_than_ts,))
                counts[table] = cur.rowcount
            if not keep_rollups_forever:
                cur = self.conn.execute(
                    "DELETE FROM minute_rollups WHERE bucket_ts<?",
                    (older_than_ts,),
                )
                counts["minute_rollups"] = cur.rowcount
        return counts

    def health(self) -> dict[str, Any]:
        counts = {}
        for table in (
            "samples", "sessions", "contexts", "profiles", "trials",
            "llm_runs", "rejections",
        ):
            counts[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        latest = self.conn.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        return {"path": str(self.path), "schema_version": SCHEMA_VERSION, "counts": counts, "latest_sample_ts": latest}
