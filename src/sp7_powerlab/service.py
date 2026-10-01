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
from .evidence import NoiseTracker
from .experiments import TrialError, TrialManager
from .hardware import inspect_hardware, system_fingerprint, thermal_sensor_path
from .helper import RootHelperClient
from .lifecycle import CONTROL_ALLOWED, EMERGENCY, READ_ONLY, STABLE, LifecycleManager
from .longterm import DriftDetector
from .measurement import measurement_trust_matches_epoch
from .scheduler import CandidateScheduler
from .storage import Database
from .telemetry import TelemetryCollector
from .thermal import ThermalObserver
from .unexpected_power import UnexpectedPowerDetector, brightness_bucket, remote_bucket


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
            "kernel/BIOS/HWP/thermal-safety/calibration hard control fingerprint changed"
        )

    software_versions = {
        "kernel": fingerprint_payload.get("kernel"),
        **{key: value for key, value in versions.items() if key != "thermald"},
    }
    media_keys = {"firefox", "chromium", "google-chrome", "playerctl", "mesa"}
    media_versions = {key: software_versions.get(key) for key in sorted(media_keys)}
    media_generation = f"media-{fingerprint_hash(media_versions)}"
    db.set_meta("media_compatibility_generation", media_generation)
    db.set_compatibility_tags("media", media_versions)
    previous_versions = db.get_meta("software_versions", {})
    if previous_versions and previous_versions != software_versions:
        media_changed = any(
            previous_versions.get(key) != software_versions.get(key) for key in media_keys
        )
        if media_changed:
            registry.mark_needs_revalidation(
                {"MEDIA_EFFICIENT"},
                "browser/media compatibility tags changed",
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
    db.set_compatibility_tags("global", software_versions)
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
        configured_thermal_sensor=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
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

    collector = TelemetryCollector(
        config,
        thermal_sensor_override=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
    )
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
    lifecycle = LifecycleManager(db)
    lifecycle.synchronize_learning(
        calibration_valid=bool((machine.get("calibration") or {}).get("valid", False))
    )
    lifecycle.synchronize_control(
        calibration_valid=bool((machine.get("calibration") or {}).get("valid", False)),
        hardware_writable=bool(report.control_capable and actuator_available),
        thermal_provider_healthy=bool(report.thermald.get("active")),
        core_telemetry_valid=False,
        rollback_integrity_ok=controller.rollback_integrity_ok(),
    )
    noise = NoiseTracker(config, db)
    unexpected_power = UnexpectedPowerDetector(config, db)
    drift = DriftDetector(
        db,
        minimum_recent_windows=int(config.get("drift.minimum_recent_windows", 8)),
        absolute_threshold_w=float(config.get("drift.absolute_threshold_w", 0.30)),
        relative_threshold=float(config.get("drift.relative_threshold", 0.08)),
        noise_multiplier=float(config.get("drift.noise_multiplier", 2.0)),
        cooldown_seconds=float(config.get("drift.cooldown_seconds", 21600.0)),
    )
    scheduler = CandidateScheduler(config, db, registry)

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
        "lifecycle": lifecycle,
        "noise": noise,
        "unexpected_power": unexpected_power,
        "drift": drift,
        "scheduler": scheduler,
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
        self._last_hwp_reconcile_ts = 0.0
        self._last_scheduler_check_ts = 0.0
        self._machine_mtime = self._mtime(self.root / "config" / "machine.toml")
        self._thermal_mtime = self._mtime(self.root / "config" / "thermal.toml")
        self._recover_interrupted_trial()
        self._reconcile_hwp(time.time(), "service startup", force=True)

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
        collector = self.stack.get("collector")
        if collector is not None and hasattr(collector, "sys_root"):
            configured = str((machine.get("thermal") or {}).get("sensor_path") or "") or None
            collector.thermal_path = thermal_sensor_path(collector.sys_root, configured)
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
            self.stack["lifecycle"].set_control(
                EMERGENCY,
                "interrupted trial could not restore baseline",
                {
                    "trial_id": active["trial_id"],
                    "last_error": recovered.get("last_error"),
                },
            )

    def _reconcile_hwp(self, ts: float, reason: str, *, force: bool = False) -> None:
        if self.db.active_trial():
            return
        interval = float(self.config.get("controller.reconcile_seconds", 60.0))
        if not force and ts - self._last_hwp_reconcile_ts < interval:
            return
        self.stack["controller"].reconcile_actual_state(reason)
        self._last_hwp_reconcile_ts = ts

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
            configured_thermal_sensor=str(
                (self.stack["machine"].get("thermal") or {}).get("sensor_path") or ""
            )
            or None,
        )
        actuator, actuator_available, actuator_mode = build_actuator(self.config)
        probed_actuator = actuator
        probed_available = actuator_available
        probed_mode = actuator_mode
        previous_actuator = self.stack.get("actuator")
        previous_actuator_mode = str(self.stack.get("actuator_mode") or "read-only")
        active_trial = self.db.active_trial() is not None
        preserve_backend = False
        if previous_actuator is not None and active_trial:
            preserve_backend = True
            actuator_available = bool(probed_available and probed_mode == previous_actuator_mode)
        elif (
            previous_actuator is not None
            and previous_actuator_mode != "read-only"
            and not probed_available
        ):
            preserve_backend = True
            actuator_available = False

        if preserve_backend:
            actuator = previous_actuator
            actuator_mode = previous_actuator_mode
            self.db.set_meta(
                "actuator_probe_degraded",
                {
                    "ts": ts,
                    "active_trial": active_trial,
                    "probed_mode": probed_mode,
                    "probed_available": probed_available,
                    "preserved_mode": previous_actuator_mode,
                    "hardware_writable": actuator_available,
                },
            )
        else:
            actuator = probed_actuator
            actuator_available = probed_available
            actuator_mode = probed_mode
            self.stack["actuator"] = actuator
            self.stack["actuator_available"] = actuator_available
            self.stack["actuator_mode"] = actuator_mode
            self.stack["controller"].actuator = actuator
            self.stack["trials"].actuator = actuator
        if not preserve_backend and actuator_mode != previous_actuator_mode:
            self.db.set_meta(
                "actuator_rebind",
                {
                    "ts": ts,
                    "previous_mode": previous_actuator_mode,
                    "current_mode": actuator_mode,
                    "available": actuator_available,
                },
            )
        if preserve_backend:
            self.stack["actuator_available"] = actuator_available
        self.stack["report"] = report
        self.stack["controller"].hardware_writable = bool(
            report.control_capable and actuator_available
        )
        previous_errors = self.db.get_meta("hardware_runtime_errors", [])
        current_errors = report.errors
        if current_errors != previous_errors:
            self.db.set_meta("hardware_runtime_errors", current_errors)
            if current_errors:
                self.stack["lifecycle"].set_control(
                    "DEGRADED",
                    "runtime hardware/control ownership degraded",
                    {"errors": current_errors},
                )
        lifecycle = self.stack["lifecycle"]
        current_control = lifecycle.control_state() if hasattr(lifecycle, "control_state") else None
        if not actuator_available and current_control != EMERGENCY:
            lifecycle.set_control(
                READ_ONLY,
                "actuator probe is unavailable; normal writes disabled",
                {
                    "actuator_mode": actuator_mode,
                    "probed_mode": probed_mode,
                    "active_trial": active_trial,
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

    def _sync_evidence_epoch(self, battery_epoch: int) -> str:
        previous = self.db.active_evidence_epoch()
        calibration_version = int(
            (self.stack["machine"].get("calibration") or {}).get("version") or 0
        )
        semantics_version = int(self.config.get("evidence.semantics_version", 1))
        payload = {
            "hard_control_fingerprint": self.stack["fingerprint"],
            "battery_epoch": battery_epoch,
            "calibration_version": calibration_version,
            "evidence_semantics_version": semantics_version,
            "compatibility_tags": self.db.active_compatibility_tags("global"),
        }
        epoch_id = self.db.ensure_evidence_epoch(
            hard_identity_hash=str(self.stack["fingerprint"]),
            battery_epoch=battery_epoch,
            calibration_version=calibration_version,
            evidence_semantics_version=semantics_version,
            payload=payload,
        )
        if previous and str(previous.get("epoch_id")) != str(epoch_id):
            previous_trust = self.db.get_meta("measurement_trust", {})
            self.db.set_meta(
                "measurement_trust",
                {
                    **(previous_trust if isinstance(previous_trust, dict) else {}),
                    "status": "BLOCKED",
                    "reasons": ["evidence_epoch_changed"],
                    "invalidated_ts": time.time(),
                    "invalidated_by_evidence_epoch": epoch_id,
                },
            )
            calibration_valid = bool(
                (self.stack["machine"].get("calibration") or {}).get("valid", False)
            )
            self.stack["lifecycle"].evidence_epoch_changed(
                calibration_valid=calibration_valid,
                previous_epoch_id=str(previous.get("epoch_id") or "") or None,
                current_epoch_id=epoch_id,
            )
        self.stack["evidence_epoch"] = epoch_id
        return epoch_id

    def _rollup(self, bucket: float, end_ts: float) -> dict[str, Any] | None:
        rows = self.db.samples_between(bucket, end_ts)
        if len(rows) < 2:
            return None
        max_gap_seconds = float(self.config.get("collector.max_gap_seconds", 45.0))
        rollup_seconds = float(self.config.get("collector.rollup_seconds", 60.0))
        summary = self.db.summarize_samples(
            rows,
            max_gap_seconds=max_gap_seconds,
        )
        demand_regions = {str(row.get("demand_region")) for row in rows if row.get("demand_region")}
        local_compute = {
            str(row.get("local_compute_pressure"))
            for row in rows
            if row.get("local_compute_pressure")
        }
        brightness_buckets = {
            (
                brightness_bucket(row.get("brightness_pct"))
                if isinstance(row.get("brightness_pct"), (int, float))
                else None
            )
            for row in rows
        }
        envelopes = {
            str(row.get("current_envelope")) if row.get("current_envelope") else None
            for row in rows
        }
        media_states = {
            bool(row.get("media_playing"))
            if isinstance(row.get("media_playing"), (bool, int))
            else None
            for row in rows
        }
        active_states = {
            bool(row.get("user_active"))
            if isinstance(row.get("user_active"), (bool, int))
            else None
            for row in rows
        }
        remote_buckets = {
            (
                remote_bucket(row.get("remote_hint"))
                if isinstance(row.get("remote_hint"), (int, float))
                else None
            )
            for row in rows
        }
        evidence_epochs = {
            str(row.get("evidence_epoch")) if row.get("evidence_epoch") else None for row in rows
        }
        envelope_content_hashes = {
            str(row.get("current_envelope_content_hash"))
            if row.get("current_envelope_content_hash")
            else None
            for row in rows
        }
        compatibility_generations = {
            str(row.get("compatibility_generation"))
            if row.get("compatibility_generation")
            else None
            for row in rows
        }
        battery_epochs = {row.get("battery_epoch") for row in rows}
        battery_statuses = {row.get("battery_status") for row in rows}
        mixed_dimensions: list[str] = []
        for name, values in (
            ("brightness", brightness_buckets),
            ("envelope", envelopes),
            ("media", media_states),
            ("user_active", active_states),
            ("remote", remote_buckets),
            ("evidence_epoch", evidence_epochs),
            ("envelope_content_hash", envelope_content_hashes),
            ("compatibility_generation", compatibility_generations),
        ):
            if len(values) != 1 or None in values:
                mixed_dimensions.append(name)
        valid_seconds = valid_duration(rows, max_gap_seconds)
        valid_fraction = valid_seconds / rollup_seconds if rollup_seconds > 0 else 0.0
        reference_ineligible_reasons: list[str] = []
        if mixed_dimensions:
            reference_ineligible_reasons.extend(f"mixed_{name}" for name in mixed_dimensions)
        if battery_statuses != {"Discharging"}:
            reference_ineligible_reasons.append("power_source_not_all_discharging")
        if any(bool(row.get("resume_grace")) for row in rows):
            reference_ineligible_reasons.append("resume_grace_present")
        if len(battery_epochs) != 1 or None in battery_epochs:
            reference_ineligible_reasons.append("mixed_battery_epoch")
        if any(
            not (0 < float(current["ts"]) - float(previous["ts"]) <= max_gap_seconds)
            for previous, current in zip(rows, rows[1:], strict=False)
        ):
            reference_ineligible_reasons.append("sample_gap")
        minimum_valid_fraction = float(
            self.config.get("evidence.reference_min_valid_fraction", 0.80)
        )
        if valid_fraction + 1e-9 < minimum_valid_fraction:
            reference_ineligible_reasons.append("insufficient_valid_discharge_fraction")
        first = rows[0]
        last = rows[-1]
        rollup = {
            "bucket_ts": bucket,
            "battery_epoch": last.get("battery_epoch"),
            "brightness_bucket": (
                next(iter(brightness_buckets))
                if len(brightness_buckets) == 1 and None not in brightness_buckets
                else -1
            ),
            "demand_region": (next(iter(demand_regions)) if len(demand_regions) == 1 else "MIXED"),
            "media_playing": (
                next(iter(media_states))
                if len(media_states) == 1 and None not in media_states
                else None
            ),
            "user_active": (
                next(iter(active_states))
                if len(active_states) == 1 and None not in active_states
                else None
            ),
            "remote_bucket": (
                next(iter(remote_buckets))
                if len(remote_buckets) == 1 and None not in remote_buckets
                else -1
            ),
            "thermal_start": first.get("thermal_state"),
            "system_fingerprint": self.stack["fingerprint"],
            "current_envelope": (
                next(iter(envelopes)) if len(envelopes) == 1 and None not in envelopes else "MIXED"
            ),
            "current_envelope_content_hash": (
                next(iter(envelope_content_hashes))
                if len(envelope_content_hashes) == 1 and None not in envelope_content_hashes
                else None
            ),
            "evidence_epoch_id": (
                next(iter(evidence_epochs))
                if len(evidence_epochs) == 1 and None not in evidence_epochs
                else None
            ),
            "compatibility_generation": (
                next(iter(compatibility_generations))
                if len(compatibility_generations) == 1 and None not in compatibility_generations
                else None
            ),
            "reference_eligible": not reference_ineligible_reasons,
            "reference_ineligible_reasons": reference_ineligible_reasons,
            "mixed_dimensions": mixed_dimensions,
            "trial_id": next(
                (row.get("trial_id") for row in rows if row.get("trial_id")),
                None,
            ),
            "valid_seconds": valid_seconds,
            "valid_fraction": valid_fraction,
            "local_compute_pressure": (
                next(iter(local_compute)) if len(local_compute) == 1 else "MIXED"
            ),
            **summary,
        }
        self.db.add_rollup(rollup)
        evidence_epoch = self.db.active_evidence_epoch()
        measurement_trust = self.db.get_meta("measurement_trust", {})
        if evidence_epoch and measurement_trust_matches_epoch(
            measurement_trust,
            evidence_epoch,
        ):
            self.stack["noise"].observe_rollup(
                rollup,
                evidence_epoch_id=str(evidence_epoch["epoch_id"]),
            )
            drift_event = self.stack["drift"].detect(
                rollup,
                evidence_epoch_id=str(evidence_epoch["epoch_id"]),
            )
            if drift_event:
                event_id = self.db.add_unexpected_power_event(drift_event)
                self.stack["lifecycle"].start_investigation(
                    event_id=event_id,
                    payload={
                        "trigger": "SUSTAINED_DRIFT",
                        "event": drift_event,
                    },
                )
                self.stack["collector"].trigger_diagnostic_burst()
        incident = self.stack["unexpected_power"].detect(rollup)
        if incident:
            event_id = self.db.add_unexpected_power_event(incident)
            self.stack["lifecycle"].start_investigation(
                event_id=event_id,
                payload={
                    "trigger": "UNEXPECTED_POWER",
                    "event": incident,
                },
            )
            self.stack["collector"].trigger_diagnostic_burst()
        return rollup

    def _configure_telemetry_mode(self) -> None:
        if self.db.active_trial() or self.db.active_calibration():
            self.stack["collector"].set_runtime_mode("TRIAL")
        elif self.stack["lifecycle"].learning_state() == STABLE:
            self.stack["collector"].set_runtime_mode("STABLE")
        else:
            self.stack["collector"].set_runtime_mode("NORMAL")
        if self.db.active_investigation():
            self.stack["collector"].trigger_diagnostic_burst()

    def _maybe_start_autonomous_trial(
        self,
        sample: dict[str, Any],
        controller_decision: Any,
        *,
        had_active_trial: bool,
    ) -> dict[str, Any] | None:
        if had_active_trial or self.db.active_trial() or self.db.active_calibration():
            return None
        if int(self.config.get("automation.level", 0)) < 3:
            return None
        if self.stack["lifecycle"].control_state() != CONTROL_ALLOWED:
            return None
        if bool(getattr(controller_decision, "read_only", True)):
            return None
        if getattr(controller_decision, "action", None) != "NO_CHANGE":
            return None
        baseline = getattr(controller_decision, "applied_envelope", None)
        desired = getattr(controller_decision, "desired_envelope", None)
        if not baseline or baseline != desired:
            return None

        now = float(sample["ts"])
        check_seconds = float(self.config.get("scheduler.check_seconds", 60.0))
        if now - self._last_scheduler_check_ts < check_seconds:
            return None
        self._last_scheduler_check_ts = now

        result = self.stack["scheduler"].candidates(
            baseline_name=str(baseline),
        )
        candidates = result.get("candidates") or []
        if not result.get("eligible") or not candidates:
            return None

        trial_sample = {**sample, "current_envelope": baseline}
        try:
            trial = self.stack["trials"].start(candidates[0]["proposal"], trial_sample)
        except TrialError as exc:
            self.db.set_meta(
                "last_autonomous_trial_error",
                {
                    "ts": now,
                    "baseline": baseline,
                    "candidate": candidates[0].get("candidate_key"),
                    "error": str(exc),
                },
            )
            return None
        self.db.set_meta(
            "last_autonomous_trial_start",
            {
                "ts": now,
                "trial_id": trial.get("trial_id"),
                "baseline": baseline,
                "candidate": candidates[0].get("candidate_key"),
            },
        )
        return trial

    def _maybe_auto_promote(self, trial: dict[str, Any] | None) -> dict[str, Any] | None:
        if not trial or trial.get("state") != "VERIFIED_WINNER":
            return trial
        if int(self.config.get("automation.level", 0)) < 4:
            return trial
        if not bool(self.config.get("automation.auto_promote", False)):
            return trial
        try:
            self.stack["trials"].promote(str(trial["trial_id"]))
        except TrialError as exc:
            self.db.set_meta(
                "last_auto_promotion_error",
                {
                    "ts": time.time(),
                    "trial_id": trial["trial_id"],
                    "error": str(exc),
                },
            )
            return trial
        return self.db.get_trial(str(trial["trial_id"]))

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
        self._configure_telemetry_mode()
        sample = self.stack["collector"].sample()
        self._refresh_hardware_contract(float(sample["ts"]))
        self._reconcile_hwp(float(sample["ts"]), "periodic runtime reconcile")
        epoch = self._battery_epoch(sample)
        evidence_epoch = self._sync_evidence_epoch(epoch)
        demand = self.stack["demand"].observe(sample)
        thermal = self.stack["thermal"].observe(sample)

        trial_before = self.db.active_trial()
        trial_arm = trial_before.get("current_arm") if trial_before else None
        current_envelope = self.stack["controller"].current_envelope()
        current_envelope_record = (
            self.db.envelope(str(current_envelope)) if current_envelope else None
        )
        current_envelope_content_hash = (
            str(current_envelope_record.get("content_hash") or "")
            if current_envelope_record
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
                "current_envelope": current_envelope,
                "current_envelope_content_hash": current_envelope_content_hash,
                "thermal_override": thermal["state"] in {"THERMAL_PRESSURE", "THROTTLING"},
                "trial_id": trial_before.get("trial_id") if trial_before else None,
                "trial_arm": trial_arm,
                "evidence_epoch": evidence_epoch,
                "compatibility_generation": self.db.get_meta(
                    "media_compatibility_generation",
                    "media-missing",
                ),
            }
        )
        self.stack["lifecycle"].synchronize_learning(
            calibration_valid=bool(
                (self.stack["machine"].get("calibration") or {}).get("valid", False)
            )
        )
        self.stack["lifecycle"].synchronize_control(
            calibration_valid=bool(
                (self.stack["machine"].get("calibration") or {}).get("valid", False)
            ),
            hardware_writable=bool(self.stack["controller"].hardware_writable),
            thermal_provider_healthy=bool(sample.get("thermald_active")),
            core_telemetry_valid=self.stack["trials"]._core_telemetry_valid(sample),
            rollback_integrity_ok=self.stack["controller"].rollback_integrity_ok(),
            thermal_emergency=thermal["state"] in {"THERMAL_PRESSURE", "THROTTLING"},
        )
        self.db.set_meta(
            "service_heartbeat",
            {
                "ts": float(sample["ts"]),
                "automation_level": int(self.config.get("automation.level", 0)),
                "learning_state": self.stack["lifecycle"].learning_state(),
                "control_state": self.stack["lifecycle"].control_state(),
                "current_envelope": self.stack["controller"].current_envelope(),
            },
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
                event = {
                    "start_ts": sample["ts"],
                    "severity": "medium",
                    "status": "OPEN",
                    "classification": "UNEXPECTED_POWER",
                    "reason": "suspected media software decode / media CPU regression",
                    "battery_power_w": sample.get("battery_power_w"),
                    "rapl_power_60s_w": sample.get("rapl_power_60s_w"),
                    "cpu_usage": sample.get("cpu_usage"),
                    "gpu": sample.get("gpu"),
                    "top_processes": sample.get("processes") or [],
                }
                event_id = self.db.add_unexpected_power_event(event)
                self.stack["lifecycle"].start_investigation(
                    event_id=event_id,
                    payload={
                        "trigger": "SUSPECTED_MEDIA_DECODE_REGRESSION",
                        "event": event,
                    },
                )
                self.stack["collector"].trigger_diagnostic_burst()
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
        trial_after = self._maybe_auto_promote(trial_after)
        decision = self.stack["controller"].step(sample, demand, thermal)
        autonomous_trial = self._maybe_start_autonomous_trial(
            sample,
            decision,
            had_active_trial=trial_before is not None,
        )
        if autonomous_trial is not None:
            trial_after = autonomous_trial

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
            "lifecycle": self.stack["lifecycle"].status(),
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
            "lifecycle": stack["lifecycle"].status(),
        }
    finally:
        stack["db"].close()
