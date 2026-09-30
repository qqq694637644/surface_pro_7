from __future__ import annotations

import hashlib
import json
import os
import signal
import statistics
import time
from pathlib import Path
from typing import Any

from .actuators.hwp import HWPActuator
from .calibration import (
    CalibrationManager,
    calibration_safety_violation,
)
from .config import (
    Config,
    load_config,
    load_machine,
    load_thermal_config,
)
from .controller import BatteryLifeController
from .demand import DemandObserver
from .envelopes import EnvelopeRegistry
from .evaluation import valid_duration
from .experiments import TrialManager
from .hardware import inspect_hardware, system_fingerprint
from .helper import RootHelperClient
from .storage import Database
from .telemetry import TelemetryCollector
from .thermal import ThermalObserver
from .waste import WasteDetector, brightness_bucket, remote_bucket


def fingerprint_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _refresh_fingerprint_state(
    *,
    db: Database,
    registry: EnvelopeRegistry,
    report: Any,
    machine: dict[str, Any],
    thermal_config: dict[str, Any],
) -> str:
    fingerprint_payload = system_fingerprint(report)
    versions = fingerprint_payload.get("versions") or {}
    control_fingerprint = {
        key: value for key, value in fingerprint_payload.items() if key != "versions"
    }
    control_fingerprint["thermald_version"] = versions.get("thermald")
    control_fingerprint["thermal_config_hash"] = fingerprint_hash(thermal_config)
    control_fingerprint["calibration_version"] = int(
        (machine.get("calibration") or {}).get("version") or 0
    )
    fp_hash = fingerprint_hash(control_fingerprint)
    changed = db.set_system_fingerprint(fp_hash, fingerprint_payload)
    if changed:
        registry.mark_verified_needs_revalidation(
            "kernel/BIOS/HWP/thermald/calibration control fingerprint changed"
        )

    software_versions = {key: value for key, value in versions.items() if key != "thermald"}
    previous_versions = db.get_meta("software_versions", {})
    if previous_versions and previous_versions != software_versions:
        registry.mark_needs_revalidation(
            {"MEDIA_EFFICIENT"},
            "browser/media software fingerprint changed",
        )
        db.set_meta(
            "software_version_drift",
            {
                "ts": time.time(),
                "previous": previous_versions,
                "current": software_versions,
            },
        )
    db.set_meta("software_versions", software_versions)
    return fp_hash


class UnavailableActuator:
    def snapshot(self) -> dict[str, Any]:
        raise RuntimeError("no privileged HWP actuator is available")

    def apply_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("no privileged HWP actuator is available")

    def restore(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("no privileged HWP actuator is available")


def build_actuator(config: Config) -> tuple[Any, bool, str]:
    helper = RootHelperClient(config.path("helper.socket"))
    if bool(config.get("helper.enabled", True)) and helper.available():
        try:
            state = helper.inspect()
            return helper, bool(state.get("available")), "root-helper"
        except Exception:
            pass

    direct = HWPActuator()
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if is_root and direct.available():
        return direct, True, "direct-root"

    return UnavailableActuator(), False, "read-only"


def prepare_stack(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    config = load_config(root, config_path)
    db = Database(config.path("storage.database"))
    machine = load_machine(root)
    thermal_config = load_thermal_config(root)
    identity = machine.get("identity") or {}
    report = inspect_hardware(
        expected_product=str(identity.get("expected_product", "Surface Pro 7")),
        expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
    )
    registry = EnvelopeRegistry(root, db)
    registry.load()
    actuator, actuator_available, actuator_mode = build_actuator(config)

    fp_hash = _refresh_fingerprint_state(
        db=db,
        registry=registry,
        report=report,
        machine=machine,
        thermal_config=thermal_config,
    )

    collector = TelemetryCollector(config)
    demand = DemandObserver(machine=machine)
    thermal = ThermalObserver(machine, thermal_config)
    calibration = CalibrationManager(root, db, config)
    controller = BatteryLifeController(
        config,
        db,
        registry,
        actuator,
        hardware_writable=bool(report.control_capable and actuator_available),
        calibration_valid=bool((machine.get("calibration") or {}).get("valid", False)),
    )
    trials = TrialManager(config, db, registry, actuator)
    waste = WasteDetector(config, db)

    return {
        "config": config,
        "db": db,
        "machine": machine,
        "thermal_config": thermal_config,
        "report": report,
        "registry": registry,
        "actuator": actuator,
        "actuator_available": actuator_available,
        "actuator_mode": actuator_mode,
        "fingerprint": fp_hash,
        "collector": collector,
        "demand": demand,
        "thermal": thermal,
        "calibration": calibration,
        "controller": controller,
        "trials": trials,
        "waste": waste,
    }


class PowerLabService:
    def __init__(self, root: Path, config_path: Path | None = None):
        self.root = root
        self.stack = prepare_stack(root, config_path)
        self.config: Config = self.stack["config"]
        self.db: Database = self.stack["db"]
        self._stop = False
        self._last_rollup_bucket: float | None = None
        self._last_thermal_state: str | None = None
        self._last_prune_ts = 0.0
        self._last_hardware_refresh_ts = time.time()
        self._machine_mtime = self._mtime(self.root / "config" / "machine.toml")
        self._thermal_mtime = self._mtime(self.root / "config" / "thermal.toml")
        self._recover_interrupted_trial()

    def close(self) -> None:
        self.db.close()

    def stop(self, *_args: Any) -> None:
        self._stop = True

    @staticmethod
    def _mtime(path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def _sync_machine(self, machine: dict[str, Any]) -> None:
        self.stack["machine"] = machine
        self.stack["demand"].machine = machine
        self.stack["thermal"].machine = machine
        self.stack["controller"].calibration_valid = bool(
            (machine.get("calibration") or {}).get("valid", False)
        )

    def _recover_interrupted_trial(self) -> None:
        active = self.db.active_trial()
        if not active:
            return
        recovered = self.stack["trials"].rollback(
            active["trial_id"],
            "service restart safety recovery",
        )
        if recovered.get("state") == "FAILED":
            self.db.add_incident(
                "waste",
                {
                    "start_ts": time.time(),
                    "severity": "high",
                    "reason": "interrupted trial could not restore baseline",
                    "trial_id": active["trial_id"],
                    "last_error": recovered.get("last_error"),
                },
            )

    def _refresh_runtime_files(self) -> None:
        machine_path = self.root / "config" / "machine.toml"
        machine_mtime = self._mtime(machine_path)
        if machine_mtime != self._machine_mtime:
            old_version = int((self.stack["machine"].get("calibration") or {}).get("version") or 0)
            machine = load_machine(self.root)
            new_version = int((machine.get("calibration") or {}).get("version") or 0)
            self._sync_machine(machine)
            self._machine_mtime = machine_mtime
            if old_version and new_version != old_version:
                self.stack["registry"].mark_verified_needs_revalidation(
                    "calibration version changed"
                )

        thermal_path = self.root / "config" / "thermal.toml"
        thermal_mtime = self._mtime(thermal_path)
        if thermal_mtime != self._thermal_mtime:
            thermal_config = load_thermal_config(self.root)
            if thermal_config != self.stack["thermal_config"]:
                self.stack["thermal_config"] = thermal_config
                self.stack["thermal"].thermal_config = thermal_config
                self.stack["registry"].mark_verified_needs_revalidation(
                    "thermal model configuration changed"
                )
            self._thermal_mtime = thermal_mtime

    def _refresh_hardware_contract(self, ts: float) -> None:
        if ts - self._last_hardware_refresh_ts < 300.0:
            return
        identity = self.stack["machine"].get("identity") or {}
        report = inspect_hardware(
            expected_product=str(identity.get("expected_product", "Surface Pro 7")),
            expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
        )
        actuator_available = False
        if hasattr(self.stack["actuator"], "inspect"):
            try:
                actuator_available = bool(self.stack["actuator"].inspect().get("available", True))
            except Exception:
                actuator_available = False
        self.stack["report"] = report
        self.stack["actuator_available"] = actuator_available
        self.stack["controller"].hardware_writable = bool(
            report.control_capable and actuator_available
        )
        previous_errors = self.db.get_meta("hardware_runtime_errors", [])
        current_errors = report.errors
        if current_errors != previous_errors:
            self.db.set_meta("hardware_runtime_errors", current_errors)
            if current_errors:
                self.db.add_incident(
                    "waste",
                    {
                        "start_ts": ts,
                        "severity": "high",
                        "reason": "runtime hardware/control ownership degraded",
                        "errors": current_errors,
                    },
                )
        self.stack["fingerprint"] = _refresh_fingerprint_state(
            db=self.db,
            registry=self.stack["registry"],
            report=report,
            machine=self.stack["machine"],
            thermal_config=self.stack["thermal_config"],
        )
        self._last_hardware_refresh_ts = ts

    def _battery_epoch(self, sample: dict[str, Any]) -> int:
        battery = sample.get("battery") or {}
        epoch = self.db.ensure_battery_epoch(
            identity_hash=str(battery.get("identity_hash") or "missing"),
            energy_full_wh=battery.get("energy_full_wh"),
            payload=battery,
        )
        machine_epoch = int((self.stack["machine"].get("battery") or {}).get("active_epoch") or 0)
        if machine_epoch != epoch:
            if machine_epoch != 0:
                self.stack["calibration"].invalidate("battery epoch changed")
                self.stack["registry"].mark_verified_needs_revalidation("battery epoch changed")
            self.stack["calibration"].set_active_battery_epoch(epoch)
            self._sync_machine(load_machine(self.root))
            self._machine_mtime = self._mtime(self.root / "config" / "machine.toml")
        return epoch

    def _rollup(self, bucket: float, end_ts: float) -> dict[str, Any] | None:
        rows = self.db.samples_between(bucket, end_ts)
        if len(rows) < 2:
            return None
        summary = self.db.summarize_samples(
            rows,
            max_gap_seconds=float(self.config.get("collector.max_gap_seconds", 45.0)),
        )
        demand_regions = {str(row.get("demand_region")) for row in rows if row.get("demand_region")}
        local_compute = {
            str(row.get("local_compute_pressure"))
            for row in rows
            if row.get("local_compute_pressure")
        }
        brightness = [
            float(row["brightness_pct"])
            for row in rows
            if isinstance(row.get("brightness_pct"), (int, float))
        ]
        first = rows[0]
        last = rows[-1]
        rollup = {
            "bucket_ts": bucket,
            "battery_epoch": last.get("battery_epoch"),
            "brightness_bucket": brightness_bucket(_median(brightness)),
            "demand_region": (next(iter(demand_regions)) if len(demand_regions) == 1 else "MIXED"),
            "media_playing": bool(last.get("media_playing")),
            "remote_bucket": remote_bucket(last.get("remote_hint")),
            "thermal_start": first.get("thermal_state"),
            "system_fingerprint": self.stack["fingerprint"],
            "valid_seconds": valid_duration(
                rows,
                float(self.config.get("collector.max_gap_seconds", 45.0)),
            ),
            "local_compute_pressure": (
                next(iter(local_compute)) if len(local_compute) == 1 else "MIXED"
            ),
            **summary,
        }
        self.db.add_rollup(rollup)
        incident = self.stack["waste"].detect(rollup)
        if incident:
            self.db.add_incident("waste", incident)
        return rollup

    def _maybe_rollup(self, sample: dict[str, Any]) -> None:
        seconds = float(self.config.get("collector.rollup_seconds", 60.0))
        bucket = float(int(float(sample["ts"]) // seconds) * seconds)
        if self._last_rollup_bucket is None:
            self._last_rollup_bucket = bucket
            return
        if bucket == self._last_rollup_bucket:
            return
        self._rollup(self._last_rollup_bucket, bucket - 0.001)
        self._last_rollup_bucket = bucket

    def _thermal_incident(self, thermal: dict[str, Any]) -> None:
        state = str(thermal["state"])
        if state != self._last_thermal_state and state in {
            "THERMAL_PRESSURE",
            "THROTTLING",
        }:
            self.db.add_incident(
                "thermal",
                {
                    "start_ts": thermal["ts"],
                    "state": state,
                    "pressure": thermal.get("pressure"),
                    "temp_c": thermal.get("temp_c"),
                    "slope_c_per_min": thermal.get("slope_c_per_min"),
                    "rapl_60s_w": thermal.get("rapl_60s_w"),
                    "rapl_300s_w": thermal.get("rapl_300s_w"),
                },
            )
        self._last_thermal_state = state

    def step(self) -> dict[str, Any]:
        self._refresh_runtime_files()
        sample = self.stack["collector"].sample()
        self._refresh_hardware_contract(float(sample["ts"]))
        epoch = self._battery_epoch(sample)
        demand = self.stack["demand"].observe(sample)
        thermal = self.stack["thermal"].observe(sample)

        trial_before = self.db.active_trial()
        trial_arm = (
            trial_before.get("current_arm")
            if trial_before and trial_before.get("state") == "MEASURING"
            else None
        )
        sample.update(
            {
                "battery_epoch": epoch,
                "demand_region": demand["region"],
                "latency_need": demand["latency_need"],
                "local_compute_pressure": demand["local_compute_pressure"],
                "network_intensity": demand["network_intensity"],
                "remote_hint": demand["remote_hint"],
                "thermal_state": thermal["state"],
                "thermal_pressure": thermal["pressure"],
                "current_envelope": self.stack["controller"].current_envelope(),
                "thermal_override": thermal["state"] in {"THERMAL_PRESSURE", "THROTTLING"},
                "trial_id": trial_before.get("trial_id") if trial_before else None,
                "trial_arm": trial_arm,
            }
        )
        self.db.add_sample(sample)
        self.db.add_demand_window(demand)
        self.db.add_thermal_window(thermal)
        if sample.get("processes_fresh"):
            self.db.add_process_attribution(sample["ts"], sample.get("processes") or [])
        self._thermal_incident(thermal)
        self._maybe_rollup(sample)

        if sample.get("media_decode_hint") == "suspected_software_decode":
            last_media_incident = float(
                self.db.get_meta("last_media_decode_incident_ts", 0.0) or 0.0
            )
            if sample["ts"] - last_media_incident >= 900:
                self.db.add_incident(
                    "waste",
                    {
                        "start_ts": sample["ts"],
                        "severity": "medium",
                        "reason": "suspected media software decode / media CPU regression",
                        "battery_power_w": sample.get("battery_power_w"),
                        "rapl_power_60s_w": sample.get("rapl_power_60s_w"),
                        "cpu_usage": sample.get("cpu_usage"),
                        "gpu": sample.get("gpu"),
                        "top_processes": sample.get("processes") or [],
                    },
                )
                self.db.set_meta("last_media_decode_incident_ts", sample["ts"])

        active_cal = self.db.active_calibration()
        violation = calibration_safety_violation(sample, self.stack["thermal_config"], active_cal)
        if violation and active_cal:
            self.db.abort_calibration(active_cal["run_id"], violation)
            self.db.add_incident(
                "thermal",
                {
                    "start_ts": sample["ts"],
                    "state": "CALIBRATION_ABORT",
                    "reason": violation,
                },
            )

        trial_after = self.stack["trials"].tick(sample)
        decision = self.stack["controller"].step(sample, demand, thermal)

        if sample["ts"] - self._last_prune_ts > 3600:
            retention = int(self.config.get("storage.raw_retention_days", 30))
            if retention > 0:
                self.db.prune_raw(sample["ts"] - retention * 86400)
            self._last_prune_ts = sample["ts"]

        return {
            "sample": sample,
            "demand": demand,
            "thermal": thermal,
            "trial": trial_after,
            "controller": decision.as_dict(),
        }

    def run(self, *, iterations: int | None = None) -> None:
        try:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        except (ValueError, AttributeError):
            pass
        count = 0
        interval = float(self.config.get("collector.sample_seconds", 10.0))
        while not self._stop:
            started = time.monotonic()
            self.step()
            count += 1
            if iterations is not None and count >= iterations:
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, interval - elapsed))


def service_status(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    stack = prepare_stack(root, config_path)
    try:
        return {
            "hardware": stack["report"].as_dict(),
            "actuator_mode": stack["actuator_mode"],
            "actuator_available": stack["actuator_available"],
            "calibration": stack["machine"].get("calibration"),
            "database": stack["db"].health(),
            "current_envelope": stack["controller"].current_envelope(),
            "override": stack["controller"].override(),
        }
    finally:
        stack["db"].close()
