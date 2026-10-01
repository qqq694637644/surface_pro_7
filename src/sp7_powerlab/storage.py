from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .measurement import valid_discharge_interval_seconds

SCHEMA_VERSION = 8
NET_BENEFIT_CAMPAIGN_MODES = {
    "MONITORING_OVERHEAD",
    "DYNAMIC_CONTROLLER",
    "FULL_POWERLAB",
}

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
        evidence_epoch_id TEXT,
        battery_epoch INTEGER,
        brightness_bucket INTEGER,
        demand_region TEXT,
        media_playing INTEGER,
        remote_bucket INTEGER,
        thermal_start TEXT,
        system_fingerprint TEXT,
        current_envelope TEXT,
        reference_eligible INTEGER NOT NULL,
        reference_ineligible_reasons_json TEXT NOT NULL,
        valid_seconds REAL NOT NULL,
        valid_fraction REAL NOT NULL,
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
    "CREATE INDEX IF NOT EXISTS idx_power_rollups_epoch ON power_rollups(evidence_epoch_id,bucket_ts)",
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
    """CREATE TABLE IF NOT EXISTS evidence_epochs (
        epoch_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        active INTEGER NOT NULL,
        hard_identity_hash TEXT NOT NULL,
        battery_epoch INTEGER,
        calibration_version INTEGER,
        evidence_semantics_version INTEGER NOT NULL,
        invalidation_reason TEXT,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_evidence_epochs_active ON evidence_epochs(active)",
    """CREATE TABLE IF NOT EXISTS compatibility_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        scope TEXT NOT NULL,
        tag_key TEXT NOT NULL,
        tag_value TEXT,
        active INTEGER NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_compatibility_tags_active ON compatibility_tags(active,scope,tag_key)",
    """CREATE TABLE IF NOT EXISTS reference_baselines (
        reference_id TEXT PRIMARY KEY,
        created_ts REAL NOT NULL,
        evidence_epoch_id TEXT NOT NULL,
        strata_key TEXT NOT NULL,
        envelope TEXT,
        median_power_w REAL,
        mad_power_w REAL,
        p25_power_w REAL,
        p75_power_w REAL,
        sample_count INTEGER NOT NULL,
        frozen INTEGER NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_reference_baselines_key ON reference_baselines(evidence_epoch_id,strata_key,created_ts)",
    """CREATE TABLE IF NOT EXISTS recent_noise_distributions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        updated_ts REAL NOT NULL,
        evidence_epoch_id TEXT NOT NULL,
        strata_key TEXT NOT NULL,
        window_seconds REAL NOT NULL,
        median_power_w REAL,
        mad_power_w REAL,
        p25_power_w REAL,
        p75_power_w REAL,
        p10_power_w REAL,
        p90_power_w REAL,
        noise_floor_w REAL,
        sample_count INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        UNIQUE(evidence_epoch_id,strata_key,window_seconds)
    )""",
    """CREATE TABLE IF NOT EXISTS arm_measurements (
        arm_id TEXT PRIMARY KEY,
        trial_id TEXT NOT NULL,
        arm TEXT NOT NULL,
        role TEXT NOT NULL,
        start_ts REAL NOT NULL,
        end_ts REAL NOT NULL,
        valid_seconds REAL NOT NULL,
        avg_power_w REAL,
        integrated_energy_wh REAL,
        battery_energy_delta_wh REAL,
        consistency_error_wh REAL,
        consistency_error_ratio REAL,
        data_quality TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_arm_measurements_trial ON arm_measurements(trial_id,start_ts)",
    """CREATE TABLE IF NOT EXISTS crossover_episodes (
        episode_id TEXT PRIMARY KEY,
        trial_id TEXT NOT NULL,
        candidate_key TEXT,
        evidence_scope_key TEXT NOT NULL,
        baseline_envelope TEXT NOT NULL,
        baseline_content_hash TEXT NOT NULL,
        reference_strata_key TEXT NOT NULL,
        compatibility_generation TEXT NOT NULL,
        stage TEXT NOT NULL,
        evidence_epoch_id TEXT,
        paired_effect_w REAL,
        paired_effect_wh REAL,
        valid INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        created_ts REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_crossover_trial ON crossover_episodes(trial_id,created_ts)",
    "CREATE INDEX IF NOT EXISTS idx_crossover_scope ON crossover_episodes(evidence_scope_key,created_ts)",
    "CREATE INDEX IF NOT EXISTS idx_crossover_candidate ON crossover_episodes(candidate_key,evidence_epoch_id,created_ts)",
    """CREATE TABLE IF NOT EXISTS evidence_decisions (
        decision_id TEXT PRIMARY KEY,
        trial_id TEXT,
        candidate_key TEXT,
        evidence_scope_key TEXT NOT NULL,
        evidence_epoch_id TEXT,
        verdict TEXT NOT NULL,
        minimum_useful_effect_w REAL,
        median_effect_w REAL,
        direction_consistency REAL,
        evidence_count INTEGER NOT NULL,
        created_ts REAL NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_evidence_decisions_trial ON evidence_decisions(trial_id,created_ts)",
    """CREATE TABLE IF NOT EXISTS candidate_frontier (
        evidence_scope_key TEXT PRIMARY KEY,
        candidate_key TEXT NOT NULL,
        evidence_epoch_id TEXT,
        baseline_envelope TEXT NOT NULL,
        baseline_content_hash TEXT NOT NULL,
        reference_strata_key TEXT NOT NULL,
        compatibility_generation TEXT NOT NULL,
        status TEXT NOT NULL,
        updated_ts REAL NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_candidate_frontier_epoch ON candidate_frontier(evidence_epoch_id,baseline_envelope,status)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_frontier_candidate ON candidate_frontier(candidate_key,evidence_epoch_id)",
    """CREATE TABLE IF NOT EXISTS control_safety_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        state TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS learning_lifecycle_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        state TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS investigations (
        investigation_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        status TEXT NOT NULL,
        event_id TEXT,
        classification TEXT,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS unexpected_power_events (
        event_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        severity TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS monitoring_overhead_runs (
        run_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        mode TEXT NOT NULL,
        result_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS net_benefit_campaigns (
        campaign_id TEXT PRIMARY KEY,
        created_ts REAL NOT NULL,
        updated_ts REAL NOT NULL,
        closed_ts REAL,
        status TEXT NOT NULL,
        evidence_epoch_id TEXT NOT NULL,
        battery_epoch INTEGER NOT NULL,
        hard_identity_hash TEXT NOT NULL,
        calibration_version INTEGER NOT NULL,
        evidence_semantics_version INTEGER NOT NULL,
        fixed_baseline_envelope TEXT NOT NULL,
        fixed_baseline_content_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_net_benefit_campaign_status ON net_benefit_campaigns(status,created_ts)",
    """CREATE TABLE IF NOT EXISTS minimal_meter_runs (
        run_id TEXT PRIMARY KEY,
        start_ts REAL NOT NULL,
        end_ts REAL,
        status TEXT NOT NULL,
        capture_mode TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        evidence_epoch_id TEXT NOT NULL,
        battery_epoch INTEGER NOT NULL,
        battery_identity_hash TEXT NOT NULL,
        hard_identity_hash TEXT NOT NULL,
        calibration_version INTEGER NOT NULL,
        evidence_semantics_version INTEGER NOT NULL,
        envelope TEXT,
        envelope_content_hash TEXT NOT NULL,
        runtime_policy_fingerprint TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS minimal_meter_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        ts REAL NOT NULL,
        battery_status TEXT,
        battery_power_w REAL,
        battery_energy_wh REAL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_minimal_meter_samples_run ON minimal_meter_samples(run_id,ts)",
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
                    "v1 database detected. PowerLab does not migrate legacy runtime data; "
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
                        f"database schema {version} is not supported by PowerLab schema {SCHEMA_VERSION}; "
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
                    bucket_ts,evidence_epoch_id,battery_epoch,brightness_bucket,demand_region,
                    media_playing,remote_bucket,thermal_start,system_fingerprint,current_envelope,
                    reference_eligible,reference_ineligible_reasons_json,valid_seconds,valid_fraction,
                    avg_power_w,median_power_w,p90_power_w,p95_power_w,avg_rapl_w,avg_cpu_psi,
                    avg_io_psi,max_thermal_pressure,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rollup["bucket_ts"],
                    rollup.get("evidence_epoch_id"),
                    rollup.get("battery_epoch"),
                    rollup.get("brightness_bucket"),
                    rollup.get("demand_region"),
                    int(bool(rollup.get("media_playing"))),
                    rollup.get("remote_bucket"),
                    rollup.get("thermal_start"),
                    rollup.get("system_fingerprint"),
                    rollup.get("current_envelope"),
                    int(bool(rollup.get("reference_eligible", False))),
                    _json(rollup.get("reference_ineligible_reasons") or []),
                    rollup.get("valid_seconds", 0.0),
                    rollup.get("valid_fraction", 0.0),
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
        if kind != "thermal":
            raise ValueError(f"unsupported incident kind: {kind}")
        incident_id = value.get("incident_id") or f"{kind[:1]}-{uuid.uuid4().hex[:12]}"
        now = float(value.get("start_ts") or time.time())
        with self.conn:
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
        for table, kind in (
            ("unexpected_power_events", "unexpected_power"),
            ("thermal_incidents", "thermal"),
        ):
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

    def ensure_evidence_epoch(
        self,
        *,
        hard_identity_hash: str,
        battery_epoch: int | None,
        calibration_version: int,
        evidence_semantics_version: int,
        payload: dict[str, Any],
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM evidence_epochs WHERE active=1 ORDER BY start_ts DESC LIMIT 1"
        ).fetchone()
        if (
            row
            and row["hard_identity_hash"] == hard_identity_hash
            and row["battery_epoch"] == battery_epoch
            and row["calibration_version"] == calibration_version
            and row["evidence_semantics_version"] == evidence_semantics_version
        ):
            return str(row["epoch_id"])

        now = time.time()
        epoch_id = f"ee-{uuid.uuid4().hex[:12]}"
        reason = "initial evidence epoch"
        if row:
            reason = "hard evidence identity changed"
        with self.conn:
            self.conn.execute(
                """UPDATE evidence_epochs
                SET active=0,end_ts=?,invalidation_reason=?
                WHERE active=1""",
                (now, reason),
            )
            self.conn.execute(
                """INSERT INTO evidence_epochs(
                    epoch_id,start_ts,active,hard_identity_hash,battery_epoch,
                    calibration_version,evidence_semantics_version,payload_json
                ) VALUES(?,?,1,?,?,?,?,?)""",
                (
                    epoch_id,
                    now,
                    hard_identity_hash,
                    battery_epoch,
                    calibration_version,
                    evidence_semantics_version,
                    _json(payload),
                ),
            )
        return epoch_id

    def active_evidence_epoch(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_epochs WHERE active=1 ORDER BY start_ts DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

    def set_compatibility_tags(self, scope: str, tags: dict[str, Any]) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute(
                "UPDATE compatibility_tags SET active=0 WHERE scope=? AND active=1",
                (scope,),
            )
            for key, value in sorted(tags.items()):
                payload = {
                    "scope": scope,
                    "key": key,
                    "value": value,
                }
                self.conn.execute(
                    """INSERT INTO compatibility_tags(
                        ts,scope,tag_key,tag_value,active,payload_json
                    ) VALUES(?,?,?,?,1,?)""",
                    (now, scope, key, None if value is None else str(value), _json(payload)),
                )

    def active_compatibility_tags(self, scope: str | None = None) -> dict[str, str | None]:
        if scope is None:
            rows = self.conn.execute(
                """SELECT scope,tag_key,tag_value FROM compatibility_tags
                WHERE active=1 ORDER BY scope,tag_key"""
            )
            return {f"{row['scope']}:{row['tag_key']}": row["tag_value"] for row in rows}
        rows = self.conn.execute(
            """SELECT tag_key,tag_value FROM compatibility_tags
            WHERE active=1 AND scope=? ORDER BY tag_key""",
            (scope,),
        )
        return {str(row["tag_key"]): row["tag_value"] for row in rows}

    def upsert_reference_baseline(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO reference_baselines(
                    reference_id,created_ts,evidence_epoch_id,strata_key,envelope,
                    median_power_w,mad_power_w,p25_power_w,p75_power_w,sample_count,
                    frozen,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value["reference_id"],
                    float(value.get("created_ts") or time.time()),
                    value["evidence_epoch_id"],
                    value["strata_key"],
                    value.get("envelope"),
                    value.get("median_power_w"),
                    value.get("mad_power_w"),
                    value.get("p25_power_w"),
                    value.get("p75_power_w"),
                    int(value.get("sample_count") or 0),
                    int(bool(value.get("frozen", True))),
                    _json(value),
                ),
            )

    def reference_baseline(
        self,
        evidence_epoch_id: str,
        strata_key: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT payload_json FROM reference_baselines
            WHERE evidence_epoch_id=? AND strata_key=? AND frozen=1
            ORDER BY created_ts DESC LIMIT 1""",
            (evidence_epoch_id, strata_key),
        ).fetchone()
        return _loads(row[0]) if row else None

    def upsert_noise_distribution(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO recent_noise_distributions(
                    updated_ts,evidence_epoch_id,strata_key,window_seconds,median_power_w,
                    mad_power_w,p25_power_w,p75_power_w,p10_power_w,p90_power_w,
                    noise_floor_w,sample_count,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(evidence_epoch_id,strata_key,window_seconds) DO UPDATE SET
                    updated_ts=excluded.updated_ts,
                    median_power_w=excluded.median_power_w,
                    mad_power_w=excluded.mad_power_w,
                    p25_power_w=excluded.p25_power_w,
                    p75_power_w=excluded.p75_power_w,
                    p10_power_w=excluded.p10_power_w,
                    p90_power_w=excluded.p90_power_w,
                    noise_floor_w=excluded.noise_floor_w,
                    sample_count=excluded.sample_count,
                    payload_json=excluded.payload_json""",
                (
                    float(value.get("updated_ts") or time.time()),
                    value["evidence_epoch_id"],
                    value["strata_key"],
                    float(value["window_seconds"]),
                    value.get("median_power_w"),
                    value.get("mad_power_w"),
                    value.get("p25_power_w"),
                    value.get("p75_power_w"),
                    value.get("p10_power_w"),
                    value.get("p90_power_w"),
                    value.get("noise_floor_w"),
                    int(value.get("sample_count") or 0),
                    _json(value),
                ),
            )

    def noise_distribution(
        self,
        evidence_epoch_id: str,
        strata_key: str,
        *,
        window_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        sql = """SELECT payload_json FROM recent_noise_distributions
            WHERE evidence_epoch_id=? AND strata_key=?"""
        args: list[Any] = [evidence_epoch_id, strata_key]
        if window_seconds is not None:
            sql += " AND window_seconds=?"
            args.append(float(window_seconds))
        sql += " ORDER BY window_seconds DESC,updated_ts DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        return _loads(row[0]) if row else None

    def add_arm_measurement(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO arm_measurements(
                    arm_id,trial_id,arm,role,start_ts,end_ts,valid_seconds,avg_power_w,
                    integrated_energy_wh,battery_energy_delta_wh,consistency_error_wh,
                    consistency_error_ratio,data_quality,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value["arm_id"],
                    value["trial_id"],
                    value["arm"],
                    value["role"],
                    value["start_ts"],
                    value["end_ts"],
                    value["valid_seconds"],
                    value.get("avg_power_w"),
                    value.get("integrated_energy_wh"),
                    value.get("battery_energy_delta_wh"),
                    value.get("consistency_error_wh"),
                    value.get("consistency_error_ratio"),
                    value.get("data_quality", "UNKNOWN"),
                    _json(value),
                ),
            )

    def arm_measurements(self, trial_id: str) -> list[dict[str, Any]]:
        return [
            _loads(row[0])
            for row in self.conn.execute(
                """SELECT payload_json FROM arm_measurements
                WHERE trial_id=? ORDER BY start_ts""",
                (trial_id,),
            )
        ]

    def delete_arm_measurements(self, trial_id: str, arms: set[str]) -> None:
        if not arms:
            return
        placeholders = ",".join("?" for _ in arms)
        with self.conn:
            self.conn.execute(
                f"DELETE FROM arm_measurements WHERE trial_id=? AND arm IN ({placeholders})",
                (trial_id, *sorted(arms)),
            )

    def add_crossover_episode(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO crossover_episodes(
                    episode_id,trial_id,candidate_key,evidence_scope_key,baseline_envelope,
                    baseline_content_hash,reference_strata_key,compatibility_generation,
                    stage,evidence_epoch_id,paired_effect_w,paired_effect_wh,valid,payload_json,created_ts
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value["episode_id"],
                    value["trial_id"],
                    value.get("candidate_key"),
                    value["evidence_scope_key"],
                    value["baseline_envelope"],
                    value["baseline_content_hash"],
                    value["reference_strata_key"],
                    value["compatibility_generation"],
                    value["stage"],
                    value.get("evidence_epoch_id"),
                    value.get("paired_effect_w"),
                    value.get("paired_effect_wh"),
                    int(bool(value.get("valid", True))),
                    _json(value),
                    float(value.get("created_ts") or time.time()),
                ),
            )

    def crossover_episodes(self, trial_id: str) -> list[dict[str, Any]]:
        return [
            _loads(row[0])
            for row in self.conn.execute(
                """SELECT payload_json FROM crossover_episodes
                WHERE trial_id=? ORDER BY created_ts""",
                (trial_id,),
            )
        ]

    def evidence_scope_crossover_episodes(
        self,
        evidence_scope_key: str,
    ) -> list[dict[str, Any]]:
        sql = """SELECT payload_json FROM crossover_episodes
            WHERE evidence_scope_key=?"""
        args: list[Any] = [evidence_scope_key]
        sql += " ORDER BY created_ts"
        return [_loads(row[0]) for row in self.conn.execute(sql, args)]

    def add_evidence_decision(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO evidence_decisions(
                    decision_id,trial_id,candidate_key,evidence_scope_key,evidence_epoch_id,verdict,
                    minimum_useful_effect_w,median_effect_w,direction_consistency,
                    evidence_count,created_ts,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value["decision_id"],
                    value.get("trial_id"),
                    value.get("candidate_key"),
                    value["evidence_scope_key"],
                    value.get("evidence_epoch_id"),
                    value["verdict"],
                    value.get("minimum_useful_effect_w"),
                    value.get("median_effect_w"),
                    value.get("direction_consistency"),
                    int(value.get("evidence_count") or 0),
                    float(value.get("created_ts") or time.time()),
                    _json(value),
                ),
            )

    def evidence_decisions(
        self,
        *,
        trial_id: str | None = None,
        evidence_epoch_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if trial_id and evidence_epoch_id:
            rows = self.conn.execute(
                """SELECT payload_json FROM evidence_decisions
                WHERE trial_id=? AND evidence_epoch_id=?
                ORDER BY created_ts DESC LIMIT ?""",
                (trial_id, evidence_epoch_id, limit),
            )
        elif trial_id:
            rows = self.conn.execute(
                """SELECT payload_json FROM evidence_decisions
                WHERE trial_id=? ORDER BY created_ts DESC LIMIT ?""",
                (trial_id, limit),
            )
        elif evidence_epoch_id:
            rows = self.conn.execute(
                """SELECT payload_json FROM evidence_decisions
                WHERE evidence_epoch_id=? ORDER BY created_ts DESC LIMIT ?""",
                (evidence_epoch_id, limit),
            )
        else:
            rows = self.conn.execute(
                """SELECT payload_json FROM evidence_decisions
                ORDER BY created_ts DESC LIMIT ?""",
                (limit,),
            )
        return [_loads(row[0]) for row in rows]

    def upsert_candidate_frontier(self, value: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO candidate_frontier(
                    evidence_scope_key,candidate_key,evidence_epoch_id,baseline_envelope,
                    baseline_content_hash,reference_strata_key,compatibility_generation,
                    status,updated_ts,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(evidence_scope_key) DO UPDATE SET
                    candidate_key=excluded.candidate_key,
                    evidence_epoch_id=excluded.evidence_epoch_id,
                    baseline_envelope=excluded.baseline_envelope,
                    baseline_content_hash=excluded.baseline_content_hash,
                    reference_strata_key=excluded.reference_strata_key,
                    compatibility_generation=excluded.compatibility_generation,
                    status=excluded.status,
                    updated_ts=excluded.updated_ts,
                    payload_json=excluded.payload_json""",
                (
                    value["evidence_scope_key"],
                    value["candidate_key"],
                    value.get("evidence_epoch_id"),
                    value["baseline_envelope"],
                    value["baseline_content_hash"],
                    value["reference_strata_key"],
                    value["compatibility_generation"],
                    value["status"],
                    float(value.get("updated_ts") or time.time()),
                    _json(value),
                ),
            )

    def candidate_frontier(
        self,
        *,
        evidence_epoch_id: str | None = None,
        baseline_envelope: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM candidate_frontier WHERE 1=1"
        args: list[Any] = []
        if evidence_epoch_id is not None:
            sql += " AND evidence_epoch_id=?"
            args.append(evidence_epoch_id)
        if baseline_envelope is not None:
            sql += " AND baseline_envelope=?"
            args.append(baseline_envelope)
        sql += " ORDER BY updated_ts DESC"
        return [_loads(row[0]) for row in self.conn.execute(sql, args)]

    def candidate_frontier_entry(self, evidence_scope_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM candidate_frontier WHERE evidence_scope_key=?",
            (evidence_scope_key,),
        ).fetchone()
        return _loads(row[0]) if row else None

    def add_runtime_state(
        self,
        kind: str,
        state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        table = {
            "control": "control_safety_history",
            "learning": "learning_lifecycle_history",
        }.get(kind)
        if not table:
            raise ValueError(f"unknown runtime state kind: {kind}")
        with self.conn:
            self.conn.execute(
                f"INSERT INTO {table}(ts,state,reason,payload_json) VALUES(?,?,?,?)",
                (time.time(), state, reason, _json(payload or {})),
            )

    def latest_runtime_state(self, kind: str) -> dict[str, Any] | None:
        table = {
            "control": "control_safety_history",
            "learning": "learning_lifecycle_history",
        }.get(kind)
        if not table:
            raise ValueError(f"unknown runtime state kind: {kind}")
        row = self.conn.execute(f"SELECT * FROM {table} ORDER BY ts DESC LIMIT 1").fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

    def add_unexpected_power_event(self, value: dict[str, Any]) -> str:
        event_id = value.get("event_id") or f"up-{uuid.uuid4().hex[:12]}"
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO unexpected_power_events(
                    event_id,start_ts,end_ts,severity,status,reason,payload_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    event_id,
                    float(value.get("start_ts") or time.time()),
                    value.get("end_ts"),
                    value.get("severity", "medium"),
                    value.get("status", "OPEN"),
                    value.get("reason", "unexpected power"),
                    _json({**value, "event_id": event_id}),
                ),
            )
        return str(event_id)

    def recent_unexpected_power_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            _loads(row[0])
            for row in self.conn.execute(
                """SELECT payload_json FROM unexpected_power_events
                ORDER BY start_ts DESC LIMIT ?""",
                (limit,),
            )
        ]

    def unexpected_power_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM unexpected_power_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        return _loads(row[0]) if row else None

    def close_unexpected_power_event(
        self,
        event_id: str,
        *,
        classification: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = self.unexpected_power_event(event_id) or {}
        merged = {
            **current,
            **(payload or {}),
            "event_id": event_id,
            "status": "CLOSED",
            "classification": classification,
            "resolved_ts": time.time(),
        }
        with self.conn:
            self.conn.execute(
                """UPDATE unexpected_power_events
                SET end_ts=COALESCE(end_ts,?),status='CLOSED',payload_json=?
                WHERE event_id=?""",
                (time.time(), _json(merged), event_id),
            )

    def start_investigation(
        self,
        *,
        event_id: str | None,
        payload: dict[str, Any],
    ) -> str:
        existing = self.conn.execute(
            """SELECT investigation_id FROM investigations
            WHERE status='INVESTIGATING' ORDER BY start_ts DESC LIMIT 1"""
        ).fetchone()
        if existing:
            return str(existing[0])
        investigation_id = f"inv-{uuid.uuid4().hex[:12]}"
        with self.conn:
            self.conn.execute(
                """INSERT INTO investigations(
                    investigation_id,start_ts,status,event_id,payload_json
                ) VALUES(?,?,'INVESTIGATING',?,?)""",
                (investigation_id, time.time(), event_id, _json(payload)),
            )
        return investigation_id

    def finish_investigation(
        self,
        investigation_id: str,
        *,
        classification: str,
        payload: dict[str, Any],
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE investigations
                SET end_ts=?,status='CLOSED',classification=?,payload_json=?
                WHERE investigation_id=?""",
                (time.time(), classification, _json(payload), investigation_id),
            )

    def investigation(self, investigation_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM investigations WHERE investigation_id=?",
            (investigation_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

    def active_investigation(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM investigations
            WHERE status='INVESTIGATING' ORDER BY start_ts DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

    def recent_investigations(self, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.conn.execute(
            "SELECT * FROM investigations ORDER BY start_ts DESC LIMIT ?",
            (limit,),
        ):
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"), {})
            result.append(item)
        return result

    def create_net_benefit_campaign(
        self,
        *,
        campaign_id: str,
        evidence_epoch_id: str,
        battery_epoch: int,
        hard_identity_hash: str,
        calibration_version: int,
        evidence_semantics_version: int,
        fixed_baseline_envelope: str,
        fixed_baseline_content_hash: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self.conn:
            self.conn.execute(
                """INSERT INTO net_benefit_campaigns(
                    campaign_id,created_ts,updated_ts,status,evidence_epoch_id,battery_epoch,
                    hard_identity_hash,calibration_version,evidence_semantics_version,
                    fixed_baseline_envelope,fixed_baseline_content_hash,payload_json
                ) VALUES(?,?,?,'OPEN',?,?,?,?,?,?,?,?)""",
                (
                    campaign_id,
                    now,
                    now,
                    evidence_epoch_id,
                    battery_epoch,
                    hard_identity_hash,
                    calibration_version,
                    evidence_semantics_version,
                    fixed_baseline_envelope,
                    fixed_baseline_content_hash,
                    _json(payload or {"comparisons": {}}),
                ),
            )
        campaign = self.net_benefit_campaign(campaign_id)
        assert campaign is not None
        return campaign

    def net_benefit_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM net_benefit_campaigns WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

    def net_benefit_campaigns(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM net_benefit_campaigns"
        args: list[Any] = []
        if status is not None:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_ts DESC LIMIT ?"
        args.append(limit)
        result: list[dict[str, Any]] = []
        for row in self.conn.execute(sql, args):
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"), {})
            result.append(item)
        return result

    def invalidate_net_benefit_campaign(self, campaign_id: str, reason: str) -> None:
        campaign = self.net_benefit_campaign(campaign_id)
        if not campaign:
            raise KeyError(campaign_id)
        payload = dict(campaign.get("payload") or {})
        payload["invalid_reason"] = reason
        now = time.time()
        with self.conn:
            self.conn.execute(
                """UPDATE net_benefit_campaigns
                SET status='INVALID',updated_ts=?,closed_ts=?,payload_json=? WHERE campaign_id=?""",
                (now, now, _json(payload), campaign_id),
            )

    def record_net_benefit_campaign_comparison(
        self,
        campaign_id: str,
        *,
        mode: str,
        overhead_run_id: str,
        runtime_policy_fingerprint: str,
    ) -> dict[str, Any]:
        if mode not in NET_BENEFIT_CAMPAIGN_MODES:
            raise ValueError(f"invalid Net Benefit mode: {mode}")
        campaign = self.net_benefit_campaign(campaign_id)
        if not campaign:
            raise KeyError(campaign_id)
        if campaign.get("status") != "OPEN":
            raise ValueError(f"Net Benefit campaign is not OPEN: {campaign.get('status')}")
        payload = dict(campaign.get("payload") or {})
        comparisons = dict(payload.get("comparisons") or {})
        if mode in comparisons:
            raise ValueError(f"Net Benefit campaign already has a {mode} comparison")
        comparisons[mode] = {
            "overhead_run_id": overhead_run_id,
            "runtime_policy_fingerprint": runtime_policy_fingerprint,
        }
        payload["comparisons"] = comparisons
        complete = NET_BENEFIT_CAMPAIGN_MODES <= set(comparisons)
        now = time.time()
        with self.conn:
            self.conn.execute(
                """UPDATE net_benefit_campaigns
                SET status=?,updated_ts=?,closed_ts=?,payload_json=? WHERE campaign_id=?""",
                (
                    "COMPLETE" if complete else "OPEN",
                    now,
                    now if complete else None,
                    _json(payload),
                    campaign_id,
                ),
            )
        updated = self.net_benefit_campaign(campaign_id)
        assert updated is not None
        return updated

    def start_minimal_meter_run(
        self,
        *,
        capture_mode: str,
        campaign_id: str,
        evidence_epoch_id: str,
        battery_epoch: int,
        battery_identity_hash: str,
        hard_identity_hash: str,
        calibration_version: int,
        evidence_semantics_version: int,
        envelope: str | None,
        envelope_content_hash: str,
        runtime_policy_fingerprint: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        run_id = f"meter-{uuid.uuid4().hex[:12]}"
        with self.conn:
            self.conn.execute(
                """INSERT INTO minimal_meter_runs(
                    run_id,start_ts,status,capture_mode,campaign_id,evidence_epoch_id,
                    battery_epoch,battery_identity_hash,hard_identity_hash,calibration_version,
                    evidence_semantics_version,envelope,envelope_content_hash,
                    runtime_policy_fingerprint,payload_json
                ) VALUES(?,?,'RUNNING',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    time.time(),
                    capture_mode,
                    campaign_id,
                    evidence_epoch_id,
                    battery_epoch,
                    battery_identity_hash,
                    hard_identity_hash,
                    calibration_version,
                    evidence_semantics_version,
                    envelope,
                    envelope_content_hash,
                    runtime_policy_fingerprint,
                    _json(payload or {}),
                ),
            )
        return run_id

    def add_minimal_meter_sample(self, run_id: str, sample: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO minimal_meter_samples(
                    run_id,ts,battery_status,battery_power_w,battery_energy_wh,payload_json
                ) VALUES(?,?,?,?,?,?)""",
                (
                    run_id,
                    sample["ts"],
                    sample.get("battery_status"),
                    sample.get("battery_power_w"),
                    sample.get("battery_energy_wh"),
                    _json(sample),
                ),
            )

    def finish_minimal_meter_run(
        self,
        run_id: str,
        payload: dict[str, Any],
        *,
        status: str = "COMPLETE",
    ) -> None:
        if status not in {"COMPLETE", "INVALID"}:
            raise ValueError(f"invalid minimal meter terminal status: {status}")
        row = self.conn.execute(
            "SELECT payload_json FROM minimal_meter_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not row:
            raise KeyError(run_id)
        merged_payload = {**_loads(row[0], {}), **payload}
        with self.conn:
            self.conn.execute(
                """UPDATE minimal_meter_runs
                SET end_ts=?,status=?,payload_json=? WHERE run_id=?""",
                (time.time(), status, _json(merged_payload), run_id),
            )

    def minimal_meter_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM minimal_meter_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        item["samples"] = [
            _loads(sample[0])
            for sample in self.conn.execute(
                """SELECT payload_json FROM minimal_meter_samples
                WHERE run_id=? ORDER BY ts""",
                (run_id,),
            )
        ]
        return item

    def recent_minimal_meter_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.conn.execute(
            "SELECT * FROM minimal_meter_runs ORDER BY start_ts DESC LIMIT ?",
            (limit,),
        ):
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"), {})
            result.append(item)
        return result

    def start_monitoring_overhead_run(
        self,
        *,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        run_id = f"overhead-{uuid.uuid4().hex[:12]}"
        with self.conn:
            self.conn.execute(
                """INSERT INTO monitoring_overhead_runs(
                    run_id,start_ts,mode,result_json
                ) VALUES(?,?,?,?)""",
                (run_id, time.time(), mode, _json(payload or {})),
            )
        return run_id

    def finish_monitoring_overhead_run(
        self,
        run_id: str,
        result: dict[str, Any],
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE monitoring_overhead_runs
                SET end_ts=?,result_json=? WHERE run_id=?""",
                (time.time(), _json(result), run_id),
            )

    def monitoring_overhead_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.conn.execute(
            """SELECT * FROM monitoring_overhead_runs
            ORDER BY start_ts DESC LIMIT ?""",
            (limit,),
        ):
            item = dict(row)
            item["result"] = _loads(item.pop("result_json"), {})
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

    def active_battery_epoch_record(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM battery_epochs WHERE active=1 ORDER BY epoch DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

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

    def recent_rollups(
        self,
        since_ts: float,
        limit: int = 500,
        *,
        evidence_epoch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM power_rollups WHERE bucket_ts>=?"
        args: list[Any] = [since_ts]
        if evidence_epoch_id is not None:
            sql += " AND evidence_epoch_id=?"
            args.append(evidence_epoch_id)
        sql += " ORDER BY bucket_ts DESC LIMIT ?"
        args.append(limit)
        return [_loads(row[0]) for row in self.conn.execute(sql, args)]

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
            and not row.get("resume_grace")
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
                if discharge_only:
                    valid_dt = valid_discharge_interval_seconds(
                        previous,
                        current,
                        max_gap_seconds=max_gap_seconds,
                    )
                    if valid_dt is None:
                        continue
                    dt = valid_dt
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
