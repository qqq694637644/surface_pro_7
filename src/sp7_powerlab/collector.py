from __future__ import annotations

import hashlib
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

from .activity import ActivityWatchClient, mpris_state
from .config import PowerLabConfig
from .context import ContextEngine
from .metrics import battery_snapshot, system_snapshot, telemetry_snapshot
from .processes import ProcessSampler, summarize_processes
from .sessions import SessionTracker
from .storage import Database
from .versioning import AppVersionCache


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def version_fingerprint(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    versions = {
        "kernel": snapshot.get("kernel"),
        "platform": snapshot.get("platform"),
        "bios_version": (snapshot.get("dmi") or {}).get("bios_version"),
        "product_name": (snapshot.get("dmi") or {}).get("product_name"),
        "tools": snapshot.get("tools"),
    }
    raw = json.dumps(versions, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest(), versions


class Collector:
    def __init__(
        self,
        config: PowerLabConfig,
        db: Database,
        *,
        profile_provider: Callable[[], str | None] | None = None,
        policy_callback: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ):
        self.config = config
        self.db = db
        self.profile_provider = profile_provider
        self.policy_callback = policy_callback
        self.system_interval = float(config.get("collector.system_interval_seconds", 10.0))
        self.process_interval = float(config.get("collector.process_interval_seconds", 20.0))
        self.rollup_seconds = float(config.get("collector.rollup_seconds", 60.0))
        self.max_gap = float(config.get("collector.max_gap_seconds", 45.0))
        self.process_sampler = ProcessSampler(
            int(config.get("collector.top_processes", 12)),
            include_tree=bool(config.get("activity.store_process_tree", True)),
            store_executable=bool(config.get("activity.store_executable", True)),
        )
        self.activity = ActivityWatchClient(
            str(config.get("activity.server_url")),
            float(config.get("activity.timeout_seconds", 1.0)),
        )
        self.context_engine = ContextEngine(config.root / "config" / "contexts.toml")
        self.sessions = SessionTracker(db, max_gap_seconds=self.max_gap)
        self.app_versions = AppVersionCache()
        self.stop_requested = False
        self.last_process_sample_at = 0.0
        self.last_activity_key: tuple[Any, ...] | None = None
        self.last_activity_event_id: int | None = None
        self.cached_processes: list[dict[str, Any]] = []
        self.cached_process_summary: dict[str, Any] = {
            "class_cpu_percent": {},
            "top": [],
            "background_cpu_percent": 0.0,
        }
        self.last_ts: float | None = None
        self.machine_snapshot: dict[str, Any] = {}
        self.current_minute_bucket: float | None = None
        self.last_network_bytes: int | None = None
        self.last_network_ts: float | None = None

    def request_stop(self, *_args: Any) -> None:
        self.stop_requested = True

    def _install_signals(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                pass

    def startup_snapshot(self) -> None:
        snap = system_snapshot()
        self.machine_snapshot = snap
        fingerprint, versions = version_fingerprint(snap)
        self.db.add_version_snapshot(fingerprint, versions)
        self.db.add_battery_health(snap.get("battery") or {})
        self.db.add_system_event("collector_started", {"versions": versions})

    def collect_once(self) -> dict[str, Any]:
        ts = time.time()
        bucket = float(int(ts // self.rollup_seconds) * self.rollup_seconds)
        if self.current_minute_bucket is not None and bucket != self.current_minute_bucket:
            rows = self.db.samples_between(
                self.current_minute_bucket,
                self.current_minute_bucket + self.rollup_seconds - 0.001,
            )
            self.db.upsert_minute_rollups(
                self.current_minute_bucket, rows, self.max_gap
            )
        self.current_minute_bucket = bucket
        mono = time.monotonic()
        if self.last_ts is not None and ts - self.last_ts > self.max_gap:
            self.sessions.flush(extra_reason="resume_or_gap")
            self.db.add_system_event(
                "sample_gap",
                {"gap_seconds": ts - self.last_ts},
                ts=ts,
            )
        self.last_ts = ts

        if mono - self.last_process_sample_at >= self.process_interval:
            self.cached_processes = self.process_sampler.sample()
            self.cached_process_summary = summarize_processes(self.cached_processes)
            self.db.add_process_samples(ts, self.cached_processes)
            self.last_process_sample_at = mono

        activity_state = (
            self.activity.current().as_dict()
            if bool(self.config.get("activity.enabled", True))
            else {"source": "disabled", "afk": None}
        )
        if not bool(self.config.get("activity.store_executable", True)):
            activity_state["executable"] = None
        media = mpris_state()
        app_version = self.app_versions.get(
            activity_state.get("app"),
            activity_state.get("executable"),
        )
        activity_state["app_version"] = app_version
        telemetry = telemetry_snapshot()
        cpu_usage = psutil.cpu_percent(interval=None)
        network_mbps = None
        rx, tx = telemetry.get("wifi_rx_bytes"), telemetry.get("wifi_tx_bytes")
        if isinstance(rx, int) and isinstance(tx, int):
            total = rx + tx
            if self.last_network_bytes is not None and self.last_network_ts is not None:
                dt = ts - self.last_network_ts
                delta = total - self.last_network_bytes
                if dt > 0 and delta >= 0:
                    network_mbps = delta * 8 / dt / 1_000_000
            self.last_network_bytes = total
            self.last_network_ts = ts
        system_for_context = {
            "cpu_usage": cpu_usage,
            "load1": telemetry.get("load1"),
            "battery_status": telemetry.get("battery_status"),
            "network_mbps": network_mbps,
        }
        context = self.context_engine.infer(
            activity=activity_state,
            process_summary=self.cached_process_summary,
            media=media,
            system=system_for_context,
        ).as_dict()
        self.db.upsert_context(context, ts)

        profile_id = self.profile_provider() if self.profile_provider else None
        sample = {
            "ts": ts,
            "wall_ts": iso_now(),
            "battery_status": telemetry.get("battery_status"),
            "power_w": telemetry.get("power_w"),
            "energy_wh": telemetry.get("energy_wh"),
            "battery_pct": telemetry.get("battery_percent"),
            "cpu_usage": cpu_usage,
            "load1": telemetry.get("load1"),
            "freq_khz": telemetry.get("current_freq_khz"),
            "epp": telemetry.get("epp"),
            "temp_c": telemetry.get("max_temp_c"),
            "brightness_pct": telemetry.get("brightness_percent"),
            "wifi_rx_bytes": telemetry.get("wifi_rx_bytes"),
            "wifi_tx_bytes": telemetry.get("wifi_tx_bytes"),
            "network_mbps": network_mbps,
            "active_app": activity_state.get("app"),
            "app_version": app_version,
            "window_title": activity_state.get("title")
            if bool(self.config.get("activity.store_window_title", True))
            else None,
            "afk": activity_state.get("afk"),
            "context_scene": context["scene"],
            "context_confidence": context["confidence"],
            "profile_id": profile_id,
            "kernel": self.machine_snapshot.get("kernel"),
            "battery_health_pct": (self.machine_snapshot.get("battery") or {}).get("health_percent"),
            "ac_online": telemetry.get("ac_online"),
            "pressure": telemetry.get("pressure"),
            "rapl": telemetry.get("rapl"),
            "gpu": telemetry.get("gpu"),
            "activity": activity_state,
            "media": media,
            "process_summary": self.cached_process_summary,
            "context": context,
        }
        self.db.add_sample(sample)
        self.sessions.add(sample, context)

        activity_key = (
            activity_state.get("app"),
            activity_state.get("title"),
            activity_state.get("afk"),
        )
        if activity_key != self.last_activity_key:
            if self.last_activity_event_id is not None:
                self.db.end_app_event(self.last_activity_event_id, ts)
            self.last_activity_event_id = self.db.add_app_event(
                {
                    "start_ts": ts,
                    "app": activity_state.get("app"),
                    "executable": activity_state.get("executable"),
                    "window_title": activity_state.get("title"),
                    "active": activity_state.get("afk") is not True,
                    "afk": activity_state.get("afk") is True,
                    "source": activity_state.get("source"),
                }
            )
            self.last_activity_key = activity_key

        if self.policy_callback:
            self.policy_callback(sample, context)
        return sample

    def run(self) -> None:
        self._install_signals()
        self.startup_snapshot()
        next_tick = time.monotonic()
        try:
            while not self.stop_requested:
                self.collect_once()
                next_tick += self.system_interval
                sleep_for = max(0.0, next_tick - time.monotonic())
                if sleep_for:
                    time.sleep(sleep_for)
        finally:
            self.sessions.flush(extra_reason="collector_stopped")
            if self.last_activity_event_id is not None:
                self.db.end_app_event(self.last_activity_event_id, time.time())
            if self.current_minute_bucket is not None:
                rows = self.db.samples_between(
                    self.current_minute_bucket,
                    self.current_minute_bucket + self.rollup_seconds - 0.001,
                )
                self.db.upsert_minute_rollups(
                    self.current_minute_bucket, rows, self.max_gap
                )
            self.db.add_system_event("collector_stopped")
            retention_days = int(self.config.get("storage.raw_retention_days", 30))
            if retention_days > 0:
                self.db.prune_raw(
                    time.time() - retention_days * 86400,
                    keep_rollups_forever=bool(
                        self.config.get("storage.keep_rollups_forever", True)
                    ),
                )


def benchmark_collector(config: PowerLabConfig, db: Database, samples: int = 12) -> dict[str, Any]:
    collector = Collector(config, db)
    durations = []
    for _ in range(samples):
        start = time.perf_counter()
        collector.collect_once()
        durations.append(time.perf_counter() - start)
        time.sleep(0.05)
    collector.sessions.flush(extra_reason="benchmark")
    return {
        "samples": len(durations),
        "mean_collection_seconds": sum(durations) / len(durations) if durations else None,
        "max_collection_seconds": max(durations) if durations else None,
        "configured_interval_seconds": collector.system_interval,
        "duty_cycle_estimate": (
            (sum(durations) / len(durations)) / collector.system_interval
            if durations and collector.system_interval > 0
            else None
        ),
    }
