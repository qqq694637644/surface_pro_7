from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

ACTIVE_TRIAL_STATES = {
    "PROPOSED",
    "WAITING_FOR_COMPARABLE_WINDOW",
    "SNAPSHOTTED",
    "APPLIED",
    "SETTLING",
    "MEASURING",
    "EVALUATING",
    "REVALIDATING",
}


class LegacyDatabaseError(RuntimeError):
    pass


DDL = [
    """CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        wall_ts TEXT NOT NULL,
        battery_status TEXT,
        battery_pct REAL,
        battery_power_w REAL,
        battery_energy_wh REAL,
        battery_epoch INTEGER,
        brightness_pct REAL,
        cpu_usage REAL,
        cpu_psi REAL,
        io_psi REAL,
        memory_psi REAL,
        load1 REAL,
        avg_freq_khz REAL,
        epp TEXT,
        max_perf_pct REAL,
        turbo INTEGER,
        package_temp_c REAL,
        temp_slope_c_per_min REAL,
        rapl_power_10s_w REAL,
        rapl_power_60s_w REAL,
        rapl_power_300s_w REAL,
        user_active INTEGER,
        media_playing INTEGER,
        network_rx_mbps REAL,
        network_tx_mbps REAL,
        demand_region TEXT,
        latency_need TEXT,
        local_compute_pressure TEXT,
        network_intensity TEXT,
        remote_hint REAL,
        thermal_state TEXT,
        thermal_pressure REAL,
        current_envelope TEXT,
        thermal_override INTEGER,
        trial_id TEXT,
        trial_arm TEXT,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)",
    "CREATE INDEX IF NOT EXISTS idx_samples_conditions ON samples(battery_epoch, demand_region, thermal_state, ts)",
    "CREATE INDEX IF NOT EXISTS idx_samples_trial ON samples(trial_id, trial_arm, ts)",
    """CREATE TABLE IF NOT EXISTS power_rollups (
        bucket_ts REAL PRIMARY KEY,
        battery_epoch INTEGER,
        brightness_bucket INTEGER,
        demand_region TEXT,
        media_playing INTEGER,
        remote_bucket INTEGER,
        thermal_start TEXT,
        system_fingerprint TEXT,
        valid_seconds REAL NOT NULL,
        avg_power_w REAL,
        median_power_w REAL,
        p90_power_w REAL,
        p95_power_w REAL,
        avg_rapl_w REAL,
        avg_cpu_psi REAL,
        avg_io_psi REAL,
        max_thermal_pressure REAL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS demand_windows (
        ts REAL PRIMARY KEY,
        region TEXT NOT NULL,
        user_active INTEGER NOT NULL,
        latency_need TEXT NOT NULL,
        local_compute_pressure TEXT NOT NULL,
        media_continuity REAL NOT NULL,
        remote_hint REAL NOT NULL,
        network_intensity TEXT NOT NULL,
        io_pressure TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS thermal_windows (
        ts REAL PRIMARY KEY,
        state TEXT NOT NULL,
        pressure REAL NOT NULL,
        temp_c REAL,
        slope_c_per_min REAL,
        rapl_60s_w REAL,
        rapl_300s_w REAL,
        throttle_evidence INTEGER NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS controller_states (
        ts REAL PRIMARY KEY,
        desired_envelope TEXT,
        applied_envelope TEXT,
        read_only INTEGER NOT NULL,
        reason TEXT,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS control_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        action TEXT NOT NULL,
        envelope TEXT,
        success INTEGER NOT NULL,
        reason TEXT,
        before_json TEXT,
        after_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS waste_incidents (
        incident_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        severity TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        resolved_ts REAL
    )""",
    """CREATE TABLE IF NOT EXISTS thermal_incidents (
        incident_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        resolved_ts REAL
    )""",
    """CREATE TABLE IF NOT EXISTS envelopes (
        name TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        status TEXT NOT NULL,
        epp TEXT NOT NULL,
        max_perf_pct INTEGER NOT NULL,
        turbo INTEGER NOT NULL,
        content_hash TEXT,
        source TEXT NOT NULL,
        updated_ts REAL NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS envelope_validations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        envelope_name TEXT NOT NULL,
        revision INTEGER NOT NULL,
        ts REAL NOT NULL,
        battery_epoch INTEGER,
        system_fingerprint TEXT,
        calibration_version INTEGER,
        result_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trials (
        trial_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        kind TEXT NOT NULL,
        baseline_envelope TEXT,
        candidate_json TEXT NOT NULL,
        target_json TEXT NOT NULL,
        validation_json TEXT NOT NULL,
        snapshot_json TEXT,
        current_arm TEXT,
        arm_start_ts REAL,
        created_ts REAL NOT NULL,
        updated_ts REAL NOT NULL,
        result_json TEXT,
        last_error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS trial_blocks (
        block_id TEXT PRIMARY KEY,
        trial_id TEXT NOT NULL,
        arm TEXT NOT NULL,
        start_ts REAL NOT NULL,
        end_ts REAL NOT NULL,
        valid_seconds REAL NOT NULL,
        avg_power_w REAL,
        median_power_w REAL,
        avg_cpu_psi REAL,
        avg_io_psi REAL,
        max_thermal_pressure REAL,
        brightness_bucket INTEGER,
        demand_region TEXT,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trial_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trial_id TEXT NOT NULL,
        ts REAL NOT NULL,
        stage TEXT NOT NULL,
        verdict TEXT NOT NULL,
        result_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        trial_id TEXT,
        envelope TEXT,
        rating TEXT NOT NULL,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS process_attribution (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        pid INTEGER NOT NULL,
        ppid INTEGER,
        start_time REAL,
        name TEXT,
        executable TEXT,
        cpu_percent REAL,
        rss_bytes INTEGER,
        read_bytes INTEGER,
        write_bytes INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_process_attribution_ts ON process_attribution(ts)",
    """CREATE TABLE IF NOT EXISTS system_fingerprints (
        fingerprint TEXT PRIMARY KEY,
        ts REAL NOT NULL,
        payload_json TEXT NOT NULL,
        active INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS battery_epochs (
        epoch INTEGER PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        identity_hash TEXT NOT NULL,
        energy_full_wh REAL,
        payload_json TEXT NOT NULL,
        active INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS calibration_runs (
        run_id TEXT PRIMARY KEY,
        phase TEXT NOT NULL,
        start_ts REAL NOT NULL,
        end_ts REAL,
        status TEXT NOT NULL,
        result_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS llm_runs (
        run_id TEXT PRIMARY KEY,
        ts REAL NOT NULL,
        pack_json TEXT NOT NULL,
        decision_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS llm_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        action TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        executed INTEGER NOT NULL,
        result_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS rejections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        key TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._initialize()

    def close(self) -> None:
        self.conn.close()

    def _table_names(self) -> set[str]:
        return {
            row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def _initialize(self) -> None:
        tables = self._table_names()
        if tables and "metadata" not in tables:
            legacy_markers = {"contexts", "profiles", "sessions", "task_runs"}
            if tables & legacy_markers:
                raise LegacyDatabaseError(
                    "v1 database detected. PowerLab v2 does not migrate v1 runtime data; "
                    "run 'sp7-powerlab reset-runtime --yes' after backing it up if desired."
                )

        if "metadata" in tables:
            row = self.conn.execute(
                "SELECT value_json FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if row:
                version = int(_loads(row[0]))
                if version != SCHEMA_VERSION:
                    raise LegacyDatabaseError(
                        f"database schema {version} is not supported by v2 schema {SCHEMA_VERSION}; "
                        "reset runtime explicitly"
                    )

        with self.conn:
            for stmt in DDL:
                self.conn.execute(stmt)
            self.set_meta("schema_version", SCHEMA_VERSION)

    def set_meta(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO metadata(key,value_json) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (key, _json(value)),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value_json FROM metadata WHERE key=?", (key,)).fetchone()
        return _loads(row[0], default) if row else default

    def health(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "schema_version": self.get_meta("schema_version"),
            "samples": self.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
            "active_trial": self.active_trial(),
            "active_battery_epoch": self.active_battery_epoch(),
        }

    def add_sample(self, sample: dict[str, Any]) -> None:
        fields = (
            "ts wall_ts battery_status battery_pct battery_power_w battery_energy_wh "
            "battery_epoch brightness_pct cpu_usage cpu_psi io_psi memory_psi load1 "
            "avg_freq_khz epp max_perf_pct turbo package_temp_c temp_slope_c_per_min "
            "rapl_power_10s_w rapl_power_60s_w rapl_power_300s_w user_active media_playing "
            "network_rx_mbps network_tx_mbps demand_region latency_need local_compute_pressure "
            "network_intensity remote_hint thermal_state thermal_pressure current_envelope "
            "thermal_override trial_id trial_arm"
        ).split()
        values = [sample.get(field) for field in fields]
        values += [_json(sample)]
        placeholders = ",".join("?" for _ in values)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO samples({','.join(fields)},payload_json) VALUES({placeholders})",
                values,
            )

    def latest_sample(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM samples ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return _loads(row[0]) if row else None

    def samples_between(
        self,
        start_ts: float,
        end_ts: float,
        *,
        trial_id: str | None = None,
        trial_arm: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM samples WHERE ts>=? AND ts<=?"
        args: list[Any] = [start_ts, end_ts]
        if trial_id is not None:
            sql += " AND trial_id=?"
            args.append(trial_id)
        if trial_arm is not None:
            sql += " AND trial_arm=?"
            args.append(trial_arm)
        sql += " ORDER BY ts"
        return [_loads(row[0]) for row in self.conn.execute(sql, args)]

    def recent_samples(self, since_ts: float) -> list[dict[str, Any]]:
        return self.samples_between(since_ts, time.time() + 1)

    def add_demand_window(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO demand_windows(
                    ts,region,user_active,latency_need,local_compute_pressure,
                    media_continuity,remote_hint,network_intensity,io_pressure,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    value["ts"],
                    value["region"],
                    int(bool(value["user_active"])),
                    value["latency_need"],
                    value["local_compute_pressure"],
                    float(value["media_continuity"]),
                    float(value["remote_hint"]),
                    value["network_intensity"],
                    value["io_pressure"],
                    _json(value),
                ),
            )

    def add_thermal_window(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO thermal_windows(
                    ts,state,pressure,temp_c,slope_c_per_min,rapl_60s_w,rapl_300s_w,
                    throttle_evidence,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    value["ts"],
                    value["state"],
                    value["pressure"],
                    value.get("temp_c"),
                    value.get("slope_c_per_min"),
                    value.get("rapl_60s_w"),
                    value.get("rapl_300s_w"),
                    int(bool(value.get("throttle_evidence"))),
                    _json(value),
                ),
            )

    def add_controller_state(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO controller_states(
                    ts,desired_envelope,applied_envelope,read_only,reason,payload_json
                ) VALUES(?,?,?,?,?,?)""",
                (
                    value["ts"],
                    value.get("desired_envelope"),
                    value.get("applied_envelope"),
                    int(bool(value.get("read_only"))),
                    value.get("reason"),
                    _json(value),
                ),
            )

    def add_control_action(
        self,
        *,
        action: str,
        envelope: str | None,
        success: bool,
        reason: str | None,
        before: Any = None,
        after: Any = None,
        ts: float | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO control_actions(
                    ts,action,envelope,success,reason,before_json,after_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    ts or time.time(),
                    action,
                    envelope,
                    int(success),
                    reason,
                    _json(before) if before is not None else None,
                    _json(after) if after is not None else None,
                ),
            )

    def add_process_attribution(self, ts: float, rows: Iterable[dict[str, Any]]) -> None:
        values = []
        for row in rows:
            values.append(
                (
                    ts,
                    int(row["pid"]),
                    row.get("ppid"),
                    row.get("start_time"),
                    row.get("name"),
                    row.get("executable"),
                    row.get("cpu_percent"),
                    row.get("rss_bytes"),
                    row.get("read_bytes"),
                    row.get("write_bytes"),
                )
            )
        if not values:
            return
        with self.conn:
            self.conn.executemany(
                """INSERT INTO process_attribution(
                    ts,pid,ppid,start_time,name,executable,cpu_percent,rss_bytes,
                    read_bytes,write_bytes
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                values,
            )

    def recent_process_attribution(self, since_ts: float, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM process_attribution
            WHERE ts>=? ORDER BY ts DESC,cpu_percent DESC LIMIT ?""",
            (since_ts, limit),
        )
        return [dict(row) for row in rows]

    def add_rollup(self, rollup: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO power_rollups(
                    bucket_ts,battery_epoch,brightness_bucket,demand_region,media_playing,
                    remote_bucket,thermal_start,system_fingerprint,valid_seconds,avg_power_w,
                    median_power_w,p90_power_w,p95_power_w,avg_rapl_w,avg_cpu_psi,avg_io_psi,
                    max_thermal_pressure,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rollup["bucket_ts"],
                    rollup.get("battery_epoch"),
                    rollup.get("brightness_bucket"),
                    rollup.get("demand_region"),
                    int(bool(rollup.get("media_playing"))),
                    rollup.get("remote_bucket"),
                    rollup.get("thermal_start"),
                    rollup.get("system_fingerprint"),
                    rollup.get("valid_seconds", 0.0),
                    rollup.get("avg_power_w"),
                    rollup.get("median_power_w"),
                    rollup.get("p90_power_w"),
                    rollup.get("p95_power_w"),
                    rollup.get("avg_rapl_w"),
                    rollup.get("avg_cpu_psi"),
                    rollup.get("avg_io_psi"),
                    rollup.get("max_thermal_pressure"),
                    _json(rollup),
                ),
            )

    def comparable_rollups(
        self,
        *,
        battery_epoch: int,
        brightness_bucket: int,
        demand_region: str,
        media_playing: bool,
        remote_bucket: int,
        system_fingerprint: str | None,
        since_ts: float,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = """SELECT payload_json FROM power_rollups
            WHERE bucket_ts>=? AND battery_epoch=? AND brightness_bucket=?
            AND demand_region=? AND media_playing=? AND remote_bucket=?"""
        args: list[Any] = [
            since_ts,
            battery_epoch,
            brightness_bucket,
            demand_region,
            int(media_playing),
            remote_bucket,
        ]
        if system_fingerprint:
            sql += " AND system_fingerprint=?"
            args.append(system_fingerprint)
        sql += " ORDER BY bucket_ts DESC LIMIT ?"
        args.append(limit)
        return [_loads(row[0]) for row in self.conn.execute(sql, args)]

    def add_incident(self, kind: str, value: dict[str, Any]) -> str:
        incident_id = value.get("incident_id") or f"{kind[:1]}-{uuid.uuid4().hex[:12]}"
        now = float(value.get("start_ts") or time.time())
        with self.conn:
            if kind == "waste":
                self.conn.execute(
                    """INSERT OR REPLACE INTO waste_incidents(
                        incident_id,start_ts,end_ts,severity,reason,payload_json,resolved_ts
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        incident_id,
                        now,
                        value.get("end_ts"),
                        value.get("severity", "medium"),
                        value.get("reason", "unspecified"),
                        _json({**value, "incident_id": incident_id}),
                        value.get("resolved_ts"),
                    ),
                )
            else:
                self.conn.execute(
                    """INSERT OR REPLACE INTO thermal_incidents(
                        incident_id,start_ts,end_ts,state,payload_json,resolved_ts
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        incident_id,
                        now,
                        value.get("end_ts"),
                        value.get("state", "THERMAL_PRESSURE"),
                        _json({**value, "incident_id": incident_id}),
                        value.get("resolved_ts"),
                    ),
                )
        return incident_id

    def recent_incidents(self, since_ts: float, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for table, kind in (("waste_incidents", "waste"), ("thermal_incidents", "thermal")):
            for row in self.conn.execute(
                f"SELECT payload_json FROM {table} WHERE start_ts>=? ORDER BY start_ts DESC LIMIT ?",
                (since_ts, limit),
            ):
                item = _loads(row[0])
                item["kind"] = kind
                result.append(item)
        return sorted(result, key=lambda item: item.get("start_ts", 0), reverse=True)[:limit]

    def upsert_envelope(self, envelope: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO envelopes(
                    name,revision,status,epp,max_perf_pct,turbo,content_hash,source,updated_ts,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    revision=excluded.revision,status=excluded.status,epp=excluded.epp,
                    max_perf_pct=excluded.max_perf_pct,turbo=excluded.turbo,
                    content_hash=excluded.content_hash,source=excluded.source,
                    updated_ts=excluded.updated_ts,payload_json=excluded.payload_json""",
                (
                    envelope["name"],
                    int(envelope.get("revision", 1)),
                    envelope["status"],
                    envelope["epp"],
                    int(envelope["max_perf_pct"]),
                    int(bool(envelope["turbo"])),
                    envelope.get("content_hash"),
                    envelope.get("source", "config"),
                    float(envelope.get("updated_ts") or time.time()),
                    _json(envelope),
                ),
            )

    def envelopes(self) -> list[dict[str, Any]]:
        return [
            _loads(row[0])
            for row in self.conn.execute("SELECT payload_json FROM envelopes ORDER BY name")
        ]

    def envelope(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM envelopes WHERE name=?", (name,)
        ).fetchone()
        return _loads(row[0]) if row else None

    def add_envelope_validation(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO envelope_validations(
                    envelope_name,revision,ts,battery_epoch,system_fingerprint,
                    calibration_version,result_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    value["envelope_name"],
                    value["revision"],
                    value.get("ts", time.time()),
                    value.get("battery_epoch"),
                    value.get("system_fingerprint"),
                    value.get("calibration_version"),
                    _json(value.get("result") or {}),
                ),
            )

    def validations(self, envelope_name: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM envelope_validations
            WHERE envelope_name=? ORDER BY ts DESC""",
            (envelope_name,),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["result"] = _loads(item.pop("result_json"))
            result.append(item)
        return result

    def create_trial(self, trial: dict[str, Any]) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute(
                """INSERT INTO trials(
                    trial_id,state,kind,baseline_envelope,candidate_json,target_json,
                    validation_json,snapshot_json,current_arm,arm_start_ts,created_ts,
                    updated_ts,result_json,last_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trial["trial_id"],
                    trial["state"],
                    trial.get("kind", "envelope"),
                    trial.get("baseline_envelope"),
                    _json(trial["candidate"]),
                    _json(trial.get("target") or {}),
                    _json(trial.get("validation") or {}),
                    _json(trial.get("snapshot")) if trial.get("snapshot") is not None else None,
                    trial.get("current_arm"),
                    trial.get("arm_start_ts"),
                    trial.get("created_ts", now),
                    now,
                    _json(trial.get("result")) if trial.get("result") is not None else None,
                    trial.get("last_error"),
                ),
            )

    def update_trial(self, trial_id: str, **changes: Any) -> None:
        mapping = {
            "state": "state",
            "baseline_envelope": "baseline_envelope",
            "snapshot": "snapshot_json",
            "current_arm": "current_arm",
            "arm_start_ts": "arm_start_ts",
            "result": "result_json",
            "last_error": "last_error",
        }
        assignments = ["updated_ts=?"]
        args: list[Any] = [time.time()]
        for key, value in changes.items():
            if key not in mapping:
                raise KeyError(key)
            assignments.append(f"{mapping[key]}=?")
            if key in {"snapshot", "result"}:
                args.append(_json(value) if value is not None else None)
            else:
                args.append(value)
        args.append(trial_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE trials SET {','.join(assignments)} WHERE trial_id=?",
                args,
            )

    def get_trial(self, trial_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM trials WHERE trial_id=?", (trial_id,)).fetchone()
        return self._trial_row(row) if row else None

    def _trial_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("candidate_json", "candidate"),
            ("target_json", "target"),
            ("validation_json", "validation"),
            ("snapshot_json", "snapshot"),
            ("result_json", "result"),
        ):
            item[target] = _loads(item.pop(source), None)
        return item

    def active_trial(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_TRIAL_STATES)
        row = self.conn.execute(
            f"""SELECT * FROM trials WHERE state IN ({placeholders})
            ORDER BY created_ts DESC LIMIT 1""",
            tuple(ACTIVE_TRIAL_STATES),
        ).fetchone()
        return self._trial_row(row) if row else None

    def recent_trials(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            self._trial_row(row)
            for row in self.conn.execute(
                "SELECT * FROM trials ORDER BY created_ts DESC LIMIT ?", (limit,)
            )
        ]

    def add_trial_block(self, block: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO trial_blocks(
                    block_id,trial_id,arm,start_ts,end_ts,valid_seconds,avg_power_w,
                    median_power_w,avg_cpu_psi,avg_io_psi,max_thermal_pressure,
                    brightness_bucket,demand_region,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    block["block_id"],
                    block["trial_id"],
                    block["arm"],
                    block["start_ts"],
                    block["end_ts"],
                    block["valid_seconds"],
                    block.get("avg_power_w"),
                    block.get("median_power_w"),
                    block.get("avg_cpu_psi"),
                    block.get("avg_io_psi"),
                    block.get("max_thermal_pressure"),
                    block.get("brightness_bucket"),
                    block.get("demand_region"),
                    _json(block),
                ),
            )

    def trial_blocks(self, trial_id: str) -> list[dict[str, Any]]:
        return [
            _loads(row[0])
            for row in self.conn.execute(
                "SELECT payload_json FROM trial_blocks WHERE trial_id=? ORDER BY start_ts",
                (trial_id,),
            )
        ]

    def add_trial_result(
        self, trial_id: str, stage: str, verdict: str, result: dict[str, Any]
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO trial_results(trial_id,ts,stage,verdict,result_json)
                VALUES(?,?,?,?,?)""",
                (trial_id, time.time(), stage, verdict, _json(result)),
            )

    def add_feedback(
        self,
        rating: str,
        *,
        trial_id: str | None = None,
        envelope: str | None = None,
        notes: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO user_feedback(ts,trial_id,envelope,rating,notes)
                VALUES(?,?,?,?,?)""",
                (time.time(), trial_id, envelope, rating, notes),
            )

    def recent_feedback(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM user_feedback ORDER BY ts DESC LIMIT ?", (limit,)
            )
        ]

    def add_rejection(self, key: str, reason: str, payload: dict[str, Any] | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO rejections(ts,key,reason,payload_json) VALUES(?,?,?,?)",
                (time.time(), key, reason, _json(payload or {})),
            )

    def recent_rejections(self, limit: int = 50) -> list[dict[str, Any]]:
        result = []
        for row in self.conn.execute("SELECT * FROM rejections ORDER BY ts DESC LIMIT ?", (limit,)):
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"))
            result.append(item)
        return result

    def set_system_fingerprint(self, fingerprint: str, payload: dict[str, Any]) -> bool:
        current = self.conn.execute(
            "SELECT fingerprint FROM system_fingerprints WHERE active=1"
        ).fetchone()
        changed = bool(current and current[0] != fingerprint)
        with self.conn:
            self.conn.execute("UPDATE system_fingerprints SET active=0")
            self.conn.execute(
                """INSERT INTO system_fingerprints(fingerprint,ts,payload_json,active)
                VALUES(?,?,?,1)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    ts=excluded.ts,payload_json=excluded.payload_json,active=1""",
                (fingerprint, time.time(), _json(payload)),
            )
        return changed

    def active_system_fingerprint(self) -> str | None:
        row = self.conn.execute(
            "SELECT fingerprint FROM system_fingerprints WHERE active=1"
        ).fetchone()
        return row[0] if row else None

    def ensure_battery_epoch(
        self,
        *,
        identity_hash: str,
        energy_full_wh: float | None,
        payload: dict[str, Any],
        force_new: bool = False,
    ) -> int:
        row = self.conn.execute(
            "SELECT * FROM battery_epochs WHERE active=1 ORDER BY epoch DESC LIMIT 1"
        ).fetchone()
        new_epoch = force_new or row is None
        if row is not None and not new_epoch:
            if row["identity_hash"] != identity_hash:
                new_epoch = True
            elif (
                energy_full_wh
                and row["energy_full_wh"]
                and abs(energy_full_wh - row["energy_full_wh"]) / max(row["energy_full_wh"], 0.1)
                > 0.20
            ):
                new_epoch = True

        if new_epoch:
            epoch = (int(row["epoch"]) + 1) if row is not None else 1
            now = time.time()
            with self.conn:
                self.conn.execute(
                    "UPDATE battery_epochs SET active=0,end_ts=? WHERE active=1",
                    (now,),
                )
                self.conn.execute(
                    """INSERT INTO battery_epochs(
                        epoch,start_ts,identity_hash,energy_full_wh,payload_json,active
                    ) VALUES(?,?,?,?,?,1)""",
                    (epoch, now, identity_hash, energy_full_wh, _json(payload)),
                )
            return epoch

        return int(row["epoch"])

    def active_battery_epoch(self) -> int | None:
        row = self.conn.execute("SELECT epoch FROM battery_epochs WHERE active=1").fetchone()
        return int(row[0]) if row else None

    def start_calibration(self, phase: str) -> dict[str, Any]:
        active = self.conn.execute(
            "SELECT * FROM calibration_runs WHERE status='RUNNING'"
        ).fetchone()
        if active:
            raise RuntimeError(f"calibration already running: {active['phase']}")
        run_id = f"cal-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self.conn:
            self.conn.execute(
                """INSERT INTO calibration_runs(run_id,phase,start_ts,status)
                VALUES(?,?,?,'RUNNING')""",
                (run_id, phase, now),
            )
        return {"run_id": run_id, "phase": phase, "start_ts": now, "status": "RUNNING"}

    def active_calibration(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM calibration_runs WHERE status='RUNNING' ORDER BY start_ts DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def finish_calibration(self, run_id: str, result: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE calibration_runs
                SET end_ts=?,status='COMPLETE',result_json=? WHERE run_id=?""",
                (time.time(), _json(result), run_id),
            )

    def calibration_results(self) -> list[dict[str, Any]]:
        result = []
        for row in self.conn.execute(
            "SELECT * FROM calibration_runs WHERE status='COMPLETE' ORDER BY start_ts"
        ):
            item = dict(row)
            item["result"] = _loads(item.pop("result_json"), {})
            result.append(item)
        return result

    def abort_calibration(self, run_id: str, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE calibration_runs
                SET end_ts=?,status='ABORTED',result_json=? WHERE run_id=?""",
                (time.time(), _json({"aborted": True, "reason": reason}), run_id),
            )

    def recent_rollups(self, since_ts: float, limit: int = 500) -> list[dict[str, Any]]:
        return [
            _loads(row[0])
            for row in self.conn.execute(
                """SELECT payload_json FROM power_rollups
                WHERE bucket_ts>=? ORDER BY bucket_ts DESC LIMIT ?""",
                (since_ts, limit),
            )
        ]

    def recent_control_actions(self, since_ts: float, limit: int = 100) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.conn.execute(
            """SELECT * FROM control_actions
            WHERE ts>=? ORDER BY ts DESC LIMIT ?""",
            (since_ts, limit),
        ):
            item = dict(row)
            item["before"] = _loads(item.pop("before_json"), None)
            item["after"] = _loads(item.pop("after_json"), None)
            result.append(item)
        return result

    def add_llm_run(self, pack: dict[str, Any]) -> str:
        run_id = f"llm-{uuid.uuid4().hex[:12]}"
        with self.conn:
            self.conn.execute(
                "INSERT INTO llm_runs(run_id,ts,pack_json) VALUES(?,?,?)",
                (run_id, time.time(), _json(pack)),
            )
        return run_id

    def add_llm_decision(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        executed: bool,
        result: dict[str, Any] | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO llm_decisions(ts,action,payload_json,executed,result_json)
                VALUES(?,?,?,?,?)""",
                (
                    time.time(),
                    action,
                    _json(payload),
                    int(executed),
                    _json(result) if result is not None else None,
                ),
            )

    def prune_raw(self, older_than_ts: float) -> int:
        with self.conn:
            deleted = self.conn.execute("DELETE FROM samples WHERE ts<?", (older_than_ts,)).rowcount
            self.conn.execute("DELETE FROM process_attribution WHERE ts<?", (older_than_ts,))
        return deleted

    def summarize_samples(
        self,
        rows: list[dict[str, Any]],
        *,
        max_gap_seconds: float = 45.0,
    ) -> dict[str, Any]:
        powers = [
            float(row["battery_power_w"])
            for row in rows
            if isinstance(row.get("battery_power_w"), (int, float))
            and row.get("battery_status") == "Discharging"
        ]
        thermal = [
            float(row["thermal_pressure"])
            for row in rows
            if isinstance(row.get("thermal_pressure"), (int, float))
        ]
        network = [
            max(
                float(row.get("network_rx_mbps") or 0.0),
                float(row.get("network_tx_mbps") or 0.0),
            )
            for row in rows
        ]
        interrupts = [
            float(row["interrupts_per_sec"])
            for row in rows
            if isinstance(row.get("interrupts_per_sec"), (int, float))
        ]

        def weighted(key: str, *, discharge_only: bool = False) -> float | None:
            total = 0.0
            seconds = 0.0
            for previous, current in zip(rows, rows[1:], strict=False):
                dt = float(current["ts"]) - float(previous["ts"])
                if not (0 < dt <= max_gap_seconds):
                    continue
                if discharge_only and not (
                    previous.get("battery_status") == "Discharging"
                    and current.get("battery_status") == "Discharging"
                ):
                    continue
                left = previous.get(key)
                right = current.get(key)
                if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                    continue
                total += (float(left) + float(right)) * 0.5 * dt
                seconds += dt
            return total / seconds if seconds > 0 else None

        return {
            "sample_count": len(rows),
            "avg_power_w": weighted("battery_power_w", discharge_only=True),
            "median_power_w": _percentile(powers, 0.50),
            "p90_power_w": _percentile(powers, 0.90),
            "p95_power_w": _percentile(powers, 0.95),
            "avg_rapl_w": weighted("rapl_power_60s_w"),
            "avg_cpu_psi": weighted("cpu_psi"),
            "avg_io_psi": weighted("io_psi"),
            "max_thermal_pressure": max(thermal) if thermal else None,
            "avg_network_mbps": sum(network) / len(network) if network else None,
            "avg_deep_idle_fraction": weighted("deep_idle_fraction"),
            "avg_interrupts_per_sec": (sum(interrupts) / len(interrupts) if interrupts else None),
            "last_gpu": rows[-1].get("gpu") if rows else None,
            "last_devices": rows[-1].get("devices") if rows else None,
        }
