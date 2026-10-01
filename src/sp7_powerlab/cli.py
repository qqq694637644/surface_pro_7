from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .agent_context import build_agent_context, build_agent_context_without_runtime
from .analytics import battery_usage_summary
from .attribution import AttributionEngine
from .calibration import CalibrationManager
from .config import load_config, load_machine, load_thermal_config
from .demand import DemandObserver
from .envelopes import EnvelopeRegistry
from .hardware import inspect_hardware
from .helper import RootHelperServer
from .lifecycle import LifecycleManager
from .llm import build_knowledge_pack
from .longterm import (
    StableReadiness,
    UsageCoverage,
    assess_net_benefit,
    compare_paired_meter_runs,
)
from .measurement import assess_measurement_trust, characterize_battery_gauge
from .scheduler import CandidateScheduler
from .service import PowerLabService, build_actuator, prepare_stack, service_status
from .storage import Database, LegacyDatabaseError
from .telemetry import TelemetryCollector
from .thermal import ThermalObserver


def _default_root() -> Path:
    configured = os.environ.get("SP7_POWERLAB_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "config" / "powerlab.toml").exists():
        return cwd
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "config" / "powerlab.toml").exists():
        return source_root
    return cwd


ROOT = _default_root()


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def config_from_args(args: argparse.Namespace):
    config_path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    return load_config(ROOT, config_path)


def db_from_args(args: argparse.Namespace) -> tuple[Any, Database]:
    config = config_from_args(args)
    return config, Database(config.path("storage.database"))


def cmd_doctor(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    machine = load_machine(ROOT)
    identity = machine.get("identity") or {}
    report = inspect_hardware(
        expected_product=str(identity.get("expected_product", "Surface Pro 7")),
        expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
        configured_thermal_sensor=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
    )
    database: dict[str, Any]
    try:
        db = Database(config.path("storage.database"))
        try:
            database = db.health()
        finally:
            db.close()
    except (LegacyDatabaseError, sqlite3.DatabaseError) as exc:
        database = {"error": str(exc), "legacy_database": True}
    actuator, available, mode = build_actuator(config)
    _ = actuator
    emit(
        {
            "version": __version__,
            "hardware": report.as_dict(),
            "calibration": machine.get("calibration"),
            "database": database,
            "actuator": {"mode": mode, "available": available},
            "automation_level": int(config.get("automation.level", 0)),
        }
    )
    return 0


def cmd_agent_context(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    try:
        db = Database(config.path("storage.database"))
    except (LegacyDatabaseError, sqlite3.DatabaseError) as exc:
        emit(build_agent_context_without_runtime(config, error=str(exc)))
        return 0
    registry = EnvelopeRegistry(ROOT, db)
    try:
        emit(build_agent_context(config, db, registry))
    finally:
        db.close()
    return 0


def cmd_reset_runtime(args: argparse.Namespace) -> int:
    if not args.yes:
        raise SystemExit("reset-runtime is destructive; pass --yes")
    config = config_from_args(args)
    path = config.path("storage.database")
    removed: list[str] = []
    for candidate in (
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
    ):
        if candidate.exists():
            candidate.unlink()
            removed.append(str(candidate))
    emit({"removed": removed})
    return 0


def cmd_calibrate_status(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        emit(CalibrationManager(ROOT, db, config).status())
    finally:
        db.close()
    return 0


def cmd_calibrate_start(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        result = CalibrationManager(ROOT, db, config).start(args.phase)
        emit(result)
    finally:
        db.close()
    return 0


def cmd_calibrate_finish(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        emit(CalibrationManager(ROOT, db, config).finish())
    finally:
        db.close()
    return 0


def cmd_calibrate_new_battery(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        sample = db.latest_sample()
        if not sample or not isinstance(sample.get("battery"), dict):
            raise SystemExit("no battery telemetry is available")
        battery = sample["battery"]
        epoch = db.ensure_battery_epoch(
            identity_hash=str(battery.get("identity_hash") or "manual"),
            energy_full_wh=battery.get("energy_full_wh"),
            payload={**battery, "manual_new_battery": True},
            force_new=True,
        )
        manager = CalibrationManager(ROOT, db, config)
        manager.set_active_battery_epoch(epoch)
        manager.invalidate("manual new battery epoch")
        previous_trust = db.get_meta("measurement_trust", {})
        db.set_meta(
            "measurement_trust",
            {
                **(previous_trust if isinstance(previous_trust, dict) else {}),
                "status": "BLOCKED",
                "reasons": ["battery_epoch_changed"],
                "invalidated_ts": time.time(),
                "invalidated_by_battery_epoch": epoch,
            },
        )
        emit({"battery_epoch": epoch, "calibration_valid": False})
    finally:
        db.close()
    return 0


def _recent_summary(db: Database, hours: float) -> dict[str, Any]:
    since = time.time() - hours * 3600
    rows = db.recent_samples(since)
    return {
        "hours": hours,
        **battery_usage_summary(rows),
        "latest": rows[-1] if rows else None,
    }


def cmd_observe_now(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        latest = db.latest_sample()
        if latest:
            emit(latest)
            return 0
    finally:
        db.close()

    machine = load_machine(ROOT)
    collector = TelemetryCollector(
        config,
        thermal_sensor_override=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
    )
    sample = collector.sample()
    demand = DemandObserver().observe(sample)
    thermal = ThermalObserver(load_machine(ROOT), load_thermal_config(ROOT)).observe(sample)
    sample.update({"demand": demand, "thermal": thermal})
    emit(sample)
    return 0


def cmd_observe_power(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        emit(_recent_summary(db, args.hours))
    finally:
        db.close()
    return 0


def cmd_observe_demand(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    since = time.time() - args.hours * 3600
    try:
        rows = [
            dict(row)
            for row in db.conn.execute(
                "SELECT * FROM demand_windows WHERE ts>=? ORDER BY ts DESC LIMIT 500",
                (since,),
            )
        ]
        emit({"hours": args.hours, "windows": rows})
    finally:
        db.close()
    return 0


def cmd_observe_thermal(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    since = time.time() - args.hours * 3600
    try:
        rows = [
            dict(row)
            for row in db.conn.execute(
                "SELECT * FROM thermal_windows WHERE ts>=? ORDER BY ts DESC LIMIT 500",
                (since,),
            )
        ]
        emit({"hours": args.hours, "windows": rows})
    finally:
        db.close()
    return 0


def cmd_incidents(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        emit(
            {
                "incidents": db.recent_incidents(
                    time.time() - args.hours * 3600,
                    limit=args.limit,
                )
            }
        )
    finally:
        db.close()
    return 0


def cmd_lifecycle_status(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        manager = LifecycleManager(db)
        epoch = db.active_evidence_epoch()
        coverage_days = int(config.get("stable.coverage_days", 30))
        coverage = UsageCoverage(db).summarize(
            since_ts=time.time() - coverage_days * 86400.0,
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
        )
        target = float(config.get("stable.target_trusted_fraction", 0.90))
        emit(
            {
                **manager.status(),
                "usage_coverage": coverage,
                "stable_target_trusted_fraction": target,
                "stable_coverage_ready": (
                    isinstance(coverage.get("trusted_fraction"), (int, float))
                    and float(coverage["trusted_fraction"]) >= target
                ),
            }
        )
    finally:
        db.close()
    return 0


def cmd_lifecycle_freeze(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        manager = LifecycleManager(db)
        readiness = StableReadiness(config, db).assess()
        if not readiness["ready"] and not args.force:
            emit({"frozen": False, "readiness": readiness})
            return 2
        manager.freeze(args.reason)
        net_benefit = readiness.get("net_benefit") or {}
        epoch = readiness.get("evidence_epoch") or {}
        db.set_meta(
            "stable_entry_readiness",
            {
                "ts": time.time(),
                "qualified": bool(readiness.get("ready")),
                "forced": bool(args.force),
                "evidence_epoch_id": epoch.get("epoch_id"),
                "selected_policy_mode": net_benefit.get("selected_policy_mode"),
                "selected_policy_fingerprint": net_benefit.get("selected_policy_fingerprint"),
                "usage_coverage": readiness.get("usage_coverage"),
            },
        )
        emit({"frozen": True, "forced": bool(args.force), **manager.status()})
    finally:
        db.close()
    return 0


def cmd_lifecycle_reopen(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        manager = LifecycleManager(db)
        manager.reopen(args.reason)
        db.set_meta("stable_entry_readiness", {})
        emit(manager.status())
    finally:
        db.close()
    return 0


def cmd_lifecycle_coverage(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        epoch = db.active_evidence_epoch()
        days = int(args.days or config.get("stable.coverage_days", 30))
        result = UsageCoverage(db).summarize(
            since_ts=time.time() - days * 86400.0,
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
        )
        target = float(config.get("stable.target_trusted_fraction", 0.90))
        emit(
            {
                "days": days,
                "target_trusted_fraction": target,
                "ready_for_stable_by_coverage": (
                    isinstance(result.get("trusted_fraction"), (int, float))
                    and float(result["trusted_fraction"]) >= target
                ),
                **result,
            }
        )
    finally:
        db.close()
    return 0


def cmd_lifecycle_readiness(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        emit(StableReadiness(config, db).assess())
    finally:
        db.close()
    return 0


def cmd_lifecycle_optimize(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        manager = LifecycleManager(db)
        manager.begin_optimization(args.reason)
        emit(manager.status())
    finally:
        db.close()
    return 0


def cmd_lifecycle_validate(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        manager = LifecycleManager(db)
        manager.begin_validation(args.reason)
        emit(manager.status())
    finally:
        db.close()
    return 0


def cmd_safety_status(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        manager = LifecycleManager(db)
        emit(
            {
                "control_safety_state": manager.control_state(),
                "history": [
                    dict(row)
                    for row in db.conn.execute(
                        "SELECT * FROM control_safety_history ORDER BY ts DESC LIMIT 20"
                    )
                ],
            }
        )
    finally:
        db.close()
    return 0


def cmd_safety_recover_rollback(args: argparse.Namespace) -> int:
    stack = prepare_stack(
        ROOT,
        Path(args.config).expanduser() if args.config else None,
    )
    try:
        matched = stack["controller"].recover_rollback_integrity_fault(args.reason)
        stack["lifecycle"].set_control(
            "READ_ONLY",
            "rollback integrity fault cleared after verified HWP reconcile; "
            "awaiting normal runtime safety synchronization",
            {"verified_envelope": matched, "reason": args.reason},
        )
        emit(
            {
                "recovered": True,
                "verified_envelope": matched,
                "rollback_integrity_fault": stack["db"].get_meta(
                    "rollback_integrity_fault",
                    {},
                ),
                "control_safety_state": stack["lifecycle"].control_state(),
            }
        )
    finally:
        stack["db"].close()
    return 0


def cmd_investigation_list(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        emit({"investigations": db.recent_investigations(args.limit)})
    finally:
        db.close()
    return 0


def cmd_investigation_inspect(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        item = db.investigation(args.investigation_id)
        if not item:
            raise SystemExit(f"investigation not found: {args.investigation_id}")
        event = db.unexpected_power_event(str(item["event_id"])) if item.get("event_id") else None
        emit({**item, "event": event})
    finally:
        db.close()
    return 0


def cmd_investigation_attribute(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        emit(AttributionEngine(db).attribute(args.investigation_id))
    finally:
        db.close()
    return 0


def cmd_investigation_close(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        attribution = AttributionEngine(db)
        classification = attribution.validate_classification(args.classification)
        payload = {
            "reason": args.reason,
            "local_evidence": args.evidence or [],
            "verification_plan": args.verification or [],
        }
        manager = LifecycleManager(db)
        manager.finish_investigation(
            args.investigation_id,
            classification=classification,
            payload=payload,
        )
        emit(
            {
                "investigation": db.investigation(args.investigation_id),
                "lifecycle": manager.status(),
            }
        )
    finally:
        db.close()
    return 0


def cmd_unexpected_power_list(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        emit({"events": db.recent_unexpected_power_events(args.limit)})
    finally:
        db.close()
    return 0


def cmd_unexpected_power_inspect(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        event = db.unexpected_power_event(args.event_id)
        if not event:
            raise SystemExit(f"unexpected-power event not found: {args.event_id}")
        emit(event)
    finally:
        db.close()
    return 0


def cmd_evidence_status(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        epoch = db.active_evidence_epoch()
        epoch_id = (epoch or {}).get("epoch_id")
        emit(
            {
                "active_evidence_epoch": epoch,
                "compatibility_tags": db.active_compatibility_tags(),
                "recent_decisions": db.evidence_decisions(
                    evidence_epoch_id=epoch_id,
                    limit=args.limit,
                ),
                "arm_measurements_total": db.conn.execute(
                    "SELECT COUNT(*) FROM arm_measurements"
                ).fetchone()[0],
                "crossover_episodes": (
                    db.conn.execute(
                        "SELECT COUNT(*) FROM crossover_episodes WHERE evidence_epoch_id=?",
                        (epoch_id,),
                    ).fetchone()[0]
                    if epoch_id
                    else 0
                ),
                "frozen_references": (
                    db.conn.execute(
                        """SELECT COUNT(*) FROM reference_baselines
                        WHERE frozen=1 AND evidence_epoch_id=?""",
                        (epoch_id,),
                    ).fetchone()[0]
                    if epoch_id
                    else 0
                ),
            }
        )
    finally:
        db.close()
    return 0


def cmd_evidence_noise(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        epoch = db.active_evidence_epoch()
        epoch_id = (epoch or {}).get("epoch_id")
        rows = (
            [
                dict(row)
                for row in db.conn.execute(
                    """SELECT updated_ts,evidence_epoch_id,strata_key,window_seconds,
                    median_power_w,mad_power_w,noise_floor_w,sample_count
                    FROM recent_noise_distributions
                    WHERE evidence_epoch_id=?
                    ORDER BY updated_ts DESC LIMIT ?""",
                    (epoch_id, args.limit),
                )
            ]
            if epoch_id
            else []
        )
        emit({"active_evidence_epoch": epoch, "noise": rows})
    finally:
        db.close()
    return 0


def cmd_evidence_gauge(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        epoch = db.active_evidence_epoch()
        since = time.time() - float(args.hours) * 3600.0
        if epoch:
            since = max(since, float(epoch.get("start_ts") or since))
        rows = db.recent_samples(since)
        discharge_power = [
            float(row["battery_power_w"])
            for row in rows
            if row.get("battery_status") == "Discharging"
            and isinstance(row.get("battery_power_w"), (int, float))
            and not row.get("resume_grace")
        ]
        expected_power = sum(discharge_power) / len(discharge_power) if discharge_power else None
        emit(
            {
                "hours": float(args.hours),
                "expected_power_w": expected_power,
                **characterize_battery_gauge(
                    rows,
                    expected_power_w=expected_power,
                    energy_quantum_multiplier=float(
                        config.get("evidence.gauge_quantum_multiplier", 8.0)
                    ),
                    max_gap_seconds=float(config.get("collector.max_gap_seconds", 45.0)),
                ),
            }
        )
    finally:
        db.close()
    return 0


def cmd_evidence_trust(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        epoch = db.active_evidence_epoch()
        since = time.time() - float(args.hours) * 3600.0
        if epoch:
            since = max(since, float(epoch.get("start_ts") or since))
        rows = db.recent_samples(since)
        result = assess_measurement_trust(
            rows,
            min_samples=int(config.get("evidence.measurement_min_samples", 30)),
            min_observation_seconds=float(
                config.get("evidence.measurement_min_observation_seconds", 900.0)
            ),
            configured_min_arm_seconds=float(config.get("experiments.min_block_seconds", 300.0)),
            energy_quantum_multiplier=float(config.get("evidence.gauge_quantum_multiplier", 8.0)),
            max_gap_seconds=float(config.get("collector.max_gap_seconds", 45.0)),
            max_consistency_ratio=float(config.get("evidence.max_energy_consistency_ratio", 0.35)),
            max_consistency_abs_wh=float(
                config.get("evidence.max_energy_consistency_abs_wh", 0.05)
            ),
            min_consistency_windows=int(
                config.get("evidence.measurement_min_consistency_windows", 2)
            ),
        )
        machine = load_machine(ROOT)
        calibration = machine.get("calibration") or {}
        battery = machine.get("battery") or {}
        if not epoch:
            result = {
                **result,
                "status": "BLOCKED",
                "reasons": [
                    *(result.get("reasons") or []),
                    "missing_active_evidence_epoch",
                ],
            }
        elif int(epoch.get("calibration_version") or 0) != int(
            calibration.get("version") or 0
        ) or int(epoch.get("battery_epoch") or 0) != int(battery.get("active_epoch") or 0):
            result = {
                **result,
                "status": "BLOCKED",
                "reasons": [
                    *(result.get("reasons") or []),
                    "active_evidence_epoch_context_stale",
                ],
            }
        record = {
            **result,
            "assessed_ts": time.time(),
            "history_hours": float(args.hours),
            "evidence_epoch_id": (epoch or {}).get("epoch_id"),
            "battery_epoch": (epoch or {}).get("battery_epoch"),
            "calibration_version": (epoch or {}).get("calibration_version"),
            "evidence_semantics_version": (epoch or {}).get("evidence_semantics_version"),
        }
        db.set_meta("measurement_trust", record)
        emit(record)
    finally:
        db.close()
    return 0


NET_BENEFIT_CAPTURE_MODES = {
    "MONITORING": "MONITORING_OVERHEAD",
    "DYNAMIC_CONTROLLER": "DYNAMIC_CONTROLLER",
}


def _meter_campaign(
    db: Database,
    reference_before_id: str,
    candidate_first_id: str,
    candidate_second_id: str,
    reference_after_id: str,
    *,
    max_interblock_gap_seconds: float = 900.0,
    max_campaign_span_seconds: float = 86400.0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str]:
    reference_before = db.minimal_meter_run(reference_before_id)
    candidate_first = db.minimal_meter_run(candidate_first_id)
    candidate_second = db.minimal_meter_run(candidate_second_id)
    reference_after = db.minimal_meter_run(reference_after_id)
    if not reference_before:
        raise SystemExit(f"unknown MinimalMeter reference-before run: {reference_before_id}")
    if not candidate_first:
        raise SystemExit(f"unknown MinimalMeter first candidate run: {candidate_first_id}")
    if not candidate_second:
        raise SystemExit(f"unknown MinimalMeter second candidate run: {candidate_second_id}")
    if not reference_after:
        raise SystemExit(f"unknown MinimalMeter reference-after run: {reference_after_id}")
    if len({reference_before_id, candidate_first_id, candidate_second_id, reference_after_id}) != 4:
        raise SystemExit("Net Benefit A-B-B-A comparison requires four distinct MinimalMeter runs")
    runs = (reference_before, candidate_first, candidate_second, reference_after)
    if any(run.get("status") != "COMPLETE" for run in runs):
        raise SystemExit("Net Benefit A-B-B-A comparison requires four COMPLETE MinimalMeter runs")
    if (
        reference_before.get("capture_mode") != "FIXED_GOOD"
        or reference_after.get("capture_mode") != "FIXED_GOOD"
    ):
        raise SystemExit("Net Benefit A-B-B-A references must use capture mode FIXED_GOOD")
    candidate_mode = str(candidate_first.get("capture_mode") or "")
    if str(candidate_second.get("capture_mode") or "") != candidate_mode:
        raise SystemExit("both Net Benefit candidate blocks must use the same capture mode")
    result_mode = NET_BENEFIT_CAPTURE_MODES.get(candidate_mode)
    if result_mode is None:
        raise SystemExit("candidate MinimalMeter run must use MONITORING or DYNAMIC_CONTROLLER")

    provenance_fields = (
        "campaign_id",
        "evidence_epoch_id",
        "battery_epoch",
        "battery_identity_hash",
        "hard_identity_hash",
        "calibration_version",
        "evidence_semantics_version",
    )
    mismatched = [
        field for field in provenance_fields if len({str(run.get(field)) for run in runs}) != 1
    ]
    if mismatched:
        raise SystemExit("MinimalMeter provenance mismatch: " + ", ".join(sorted(mismatched)))
    policy_fingerprints = {str(run.get("runtime_policy_fingerprint") or "") for run in runs}
    if len(policy_fingerprints) != 1 or "" in policy_fingerprints:
        raise SystemExit("MinimalMeter runtime policy fingerprint changed within A-B-B-A")
    if reference_before.get("envelope") != reference_after.get("envelope") or reference_before.get(
        "envelope_content_hash"
    ) != reference_after.get("envelope_content_hash"):
        raise SystemExit("A-B-B-A fixed reference definition changed between reference blocks")
    if candidate_mode == "MONITORING" and (
        any(
            reference_before.get("envelope") != run.get("envelope")
            for run in (candidate_first, candidate_second)
        )
        or any(
            reference_before.get("envelope_content_hash") != run.get("envelope_content_hash")
            for run in (candidate_first, candidate_second)
        )
    ):
        raise SystemExit("MONITORING comparison requires the same fixed VERIFIED envelope")

    for run in runs:
        payload = run.get("payload") or {}
        if int(payload.get("capture_contract_version") or 0) < 2:
            raise SystemExit("Net Benefit comparison requires capture contract v2 provenance")
    before_end = float(reference_before.get("end_ts") or 0.0)
    first_start = float(candidate_first.get("start_ts") or 0.0)
    first_end = float(candidate_first.get("end_ts") or 0.0)
    second_start = float(candidate_second.get("start_ts") or 0.0)
    second_end = float(candidate_second.get("end_ts") or 0.0)
    after_start = float(reference_after.get("start_ts") or 0.0)
    if not (before_end <= first_start and first_end <= second_start and second_end <= after_start):
        raise SystemExit(
            "Net Benefit A-B-B-A runs are not in reference-candidate-candidate-reference order"
        )
    interblock_gaps = (
        first_start - before_end,
        second_start - first_end,
        after_start - second_end,
    )
    if any(gap > float(max_interblock_gap_seconds) for gap in interblock_gaps):
        raise SystemExit("Net Benefit A-B-B-A inter-block gap exceeds the paired-campaign limit")

    campaign_id = str(candidate_first.get("campaign_id") or "")
    campaign = db.net_benefit_campaign(campaign_id)
    if not campaign:
        raise SystemExit(f"Net Benefit campaign entity does not exist: {campaign_id}")
    if campaign.get("status") != "OPEN":
        raise SystemExit(f"Net Benefit campaign is not OPEN: {campaign.get('status')}")
    if time.time() - float(campaign.get("created_ts") or 0.0) > float(max_campaign_span_seconds):
        db.invalidate_net_benefit_campaign(campaign_id, "campaign_span_exceeded")
        raise SystemExit("Net Benefit campaign exceeded max_campaign_span_seconds")
    campaign_context = {
        "evidence_epoch_id": candidate_first.get("evidence_epoch_id"),
        "battery_epoch": candidate_first.get("battery_epoch"),
        "hard_identity_hash": candidate_first.get("hard_identity_hash"),
        "calibration_version": candidate_first.get("calibration_version"),
        "evidence_semantics_version": candidate_first.get("evidence_semantics_version"),
    }
    campaign_mismatch = [
        field for field, value in campaign_context.items() if str(campaign.get(field)) != str(value)
    ]
    if campaign_mismatch:
        db.invalidate_net_benefit_campaign(
            campaign_id,
            "campaign_context_changed:" + ",".join(sorted(campaign_mismatch)),
        )
        raise SystemExit(
            "Net Benefit campaign context mismatch: " + ", ".join(sorted(campaign_mismatch))
        )
    if str(campaign.get("fixed_baseline_envelope") or "") != str(
        reference_before.get("envelope") or ""
    ) or str(campaign.get("fixed_baseline_content_hash") or "") != str(
        reference_before.get("envelope_content_hash") or ""
    ):
        db.invalidate_net_benefit_campaign(campaign_id, "fixed_baseline_changed")
        raise SystemExit("Net Benefit campaign fixed baseline no longer matches its contract")
    comparisons = dict((campaign.get("payload") or {}).get("comparisons") or {})
    if result_mode in comparisons:
        raise SystemExit(f"Net Benefit campaign already contains {result_mode}")

    active_epoch = db.active_evidence_epoch()
    if not active_epoch or str(active_epoch.get("epoch_id") or "") != str(
        candidate_first.get("evidence_epoch_id") or ""
    ):
        raise SystemExit("MinimalMeter runs are not from the current evidence epoch")
    return reference_before, candidate_first, candidate_second, reference_after, result_mode


def cmd_overhead_compare(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        reference_before, candidate_first, candidate_second, reference_after, result_mode = (
            _meter_campaign(
                db,
                args.reference_before_run,
                args.candidate_first_run,
                args.candidate_second_run,
                args.reference_after_run,
                max_interblock_gap_seconds=float(
                    config.get("net_benefit.max_interblock_gap_seconds", 900.0)
                ),
                max_campaign_span_seconds=float(
                    config.get("net_benefit.max_campaign_span_seconds", 86400.0)
                ),
            )
        )
        trust = db.get_meta("measurement_trust", {})
        minimum_block_seconds = max(
            float(config.get("experiments.min_block_seconds", 300.0)),
            float((trust or {}).get("recommended_min_arm_seconds") or 0.0),
        )
        result = compare_paired_meter_runs(
            reference_before.get("samples") or [],
            candidate_first.get("samples") or [],
            candidate_second.get("samples") or [],
            reference_after.get("samples") or [],
            usable_battery_wh=args.usable_battery_wh,
            max_gap_seconds=float(args.max_gap_seconds),
            max_consistency_ratio=float(config.get("evidence.max_energy_consistency_ratio", 0.35)),
            max_consistency_abs_wh=float(
                config.get("evidence.max_energy_consistency_abs_wh", 0.05)
            ),
            minimum_block_seconds=minimum_block_seconds,
            max_brightness_delta_pct=float(
                config.get("net_benefit.max_brightness_delta_pct", 10.0)
            ),
            max_active_fraction_delta=float(
                config.get("net_benefit.max_active_fraction_delta", 0.15)
            ),
            max_media_fraction_delta=float(
                config.get("net_benefit.max_media_fraction_delta", 0.10)
            ),
            max_remote_fraction_delta=float(
                config.get("net_benefit.max_remote_fraction_delta", 0.10)
            ),
            max_network_mbps_delta=float(config.get("net_benefit.max_network_mbps_delta", 5.0)),
            max_mean_temp_delta_c=float(config.get("net_benefit.max_mean_temp_delta_c", 5.0)),
            max_reference_drift_w=float(config.get("net_benefit.max_reference_drift_w", 0.30)),
            max_candidate_delta_spread_w=float(
                config.get("net_benefit.max_candidate_delta_spread_w", 0.30)
            ),
            require_candidate_fixed_hwp=(candidate_first.get("capture_mode") == "MONITORING"),
        )
        result = {
            **result,
            "reference_before_run_id": reference_before["run_id"],
            "candidate_first_run_id": candidate_first["run_id"],
            "candidate_second_run_id": candidate_second["run_id"],
            "reference_after_run_id": reference_after["run_id"],
            "campaign_id": candidate_first["campaign_id"],
            "evidence_epoch_id": candidate_first["evidence_epoch_id"],
            "battery_epoch": candidate_first["battery_epoch"],
            "battery_identity_hash": candidate_first["battery_identity_hash"],
            "hard_identity_hash": candidate_first["hard_identity_hash"],
            "calibration_version": candidate_first["calibration_version"],
            "evidence_semantics_version": candidate_first["evidence_semantics_version"],
            "fixed_baseline_envelope": reference_before["envelope"],
            "fixed_baseline_content_hash": reference_before["envelope_content_hash"],
            "runtime_policy_fingerprint": candidate_first["runtime_policy_fingerprint"],
        }
        run_id = db.start_monitoring_overhead_run(
            mode=result_mode,
            payload={
                "comparison_design": "A_B_B_A",
                "reference_before_run_id": reference_before["run_id"],
                "candidate_first_run_id": candidate_first["run_id"],
                "candidate_second_run_id": candidate_second["run_id"],
                "reference_after_run_id": reference_after["run_id"],
                "usable_battery_wh": args.usable_battery_wh,
                "mode": result_mode,
                "campaign_id": candidate_first["campaign_id"],
            },
        )
        db.finish_monitoring_overhead_run(run_id, result)
        campaign = None
        if result.get("comparison_quality") == "OK":
            campaign = db.record_net_benefit_campaign_comparison(
                str(candidate_first["campaign_id"]),
                mode=result_mode,
                overhead_run_id=run_id,
                runtime_policy_fingerprint=str(candidate_first["runtime_policy_fingerprint"]),
            )
        emit({"run_id": run_id, "campaign": campaign, **result})
    finally:
        db.close()
    return 0


def cmd_overhead_history(args: argparse.Namespace) -> int:
    _config, db = db_from_args(args)
    try:
        emit({"runs": db.monitoring_overhead_runs(args.limit)})
    finally:
        db.close()
    return 0


def cmd_overhead_summary(args: argparse.Namespace) -> int:
    config, db = db_from_args(args)
    try:
        epoch = db.active_evidence_epoch()
        complete_campaign_ids = {
            str(item["campaign_id"])
            for item in db.net_benefit_campaigns(status="COMPLETE", limit=args.limit)
        }
        emit(
            assess_net_benefit(
                db.monitoring_overhead_runs(args.limit),
                practical_threshold_w=float(config.get("evidence.practical_threshold_w", 0.10)),
                evidence_epoch_id=(epoch or {}).get("epoch_id"),
                complete_campaign_ids=complete_campaign_ids,
                max_campaign_span_seconds=float(
                    config.get("net_benefit.max_campaign_span_seconds", 86400.0)
                ),
            )
        )
    finally:
        db.close()
    return 0


def _scheduler_stack(args: argparse.Namespace):
    config, db, registry = _registry(args)
    registry.load()
    return config, db, registry, CandidateScheduler(config, db, registry)


def cmd_scheduler_status(args: argparse.Namespace) -> int:
    _config, db, _registry_value, scheduler = _scheduler_stack(args)
    try:
        emit(scheduler.status())
    finally:
        db.close()
    return 0


def cmd_scheduler_candidates(args: argparse.Namespace) -> int:
    _config, db, _registry_value, scheduler = _scheduler_stack(args)
    try:
        emit(
            scheduler.candidates(
                baseline_name=args.baseline,
                ux_regression=bool(args.ux_regression),
            )
        )
    finally:
        db.close()
    return 0


def cmd_scheduler_propose(args: argparse.Namespace) -> int:
    _config, db, _registry_value, scheduler = _scheduler_stack(args)
    try:
        emit(
            scheduler.propose_next(
                baseline_name=args.baseline,
                ux_regression=bool(args.ux_regression),
            )
        )
    finally:
        db.close()
    return 0


def cmd_scheduler_pause(args: argparse.Namespace) -> int:
    _config, db, _registry_value, scheduler = _scheduler_stack(args)
    try:
        scheduler.pause(args.reason)
        emit(scheduler.status())
    finally:
        db.close()
    return 0


def cmd_scheduler_resume(args: argparse.Namespace) -> int:
    _config, db, _registry_value, scheduler = _scheduler_stack(args)
    try:
        scheduler.resume()
        emit(scheduler.status())
    finally:
        db.close()
    return 0


def _registry(args: argparse.Namespace):
    config, db = db_from_args(args)
    return config, db, EnvelopeRegistry(ROOT, db)


def cmd_envelope_list(args: argparse.Namespace) -> int:
    _config, db, registry = _registry(args)
    try:
        emit({"envelopes": registry.list()})
    finally:
        db.close()
    return 0


def cmd_envelope_inspect(args: argparse.Namespace) -> int:
    _config, db, registry = _registry(args)
    try:
        env = registry.get(args.name)
        if not env:
            raise SystemExit(f"envelope not found: {args.name}")
        emit({"envelope": env, "validations": db.validations(args.name)})
    finally:
        db.close()
    return 0


def cmd_envelope_adopt_current(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        machine = load_machine(ROOT)
        if not bool((machine.get("calibration") or {}).get("valid", False)):
            raise SystemExit("machine calibration must be valid before adopting an envelope")
        if not stack["report"].writable:
            raise SystemExit("hardware/thermald/ownership checks must pass before adoption")
        if not stack["actuator_available"]:
            raise SystemExit("root helper or direct root HWP actuator is required")
        if stack["db"].active_trial():
            raise SystemExit("cannot adopt current HWP state while a trial is active")
        snapshot = stack["actuator"].snapshot()
        env = stack["registry"].adopt_current(
            args.name,
            snapshot,
            battery_epoch=stack["db"].active_battery_epoch(),
            system_fingerprint=stack["db"].active_system_fingerprint(),
            calibration_version=int((machine.get("calibration") or {}).get("version") or 0),
            note=args.note,
        )
        stack["db"].set_meta("current_envelope", args.name)
        emit(env)
    finally:
        stack["db"].close()
    return 0


def cmd_envelope_block(args: argparse.Namespace) -> int:
    _config, db, registry = _registry(args)
    try:
        emit(registry.set_status(args.name, "BLOCKED"))
    finally:
        db.close()
    return 0


def cmd_envelope_override(args: argparse.Namespace) -> int:
    stack = prepare_stack(
        ROOT,
        Path(args.config).expanduser() if args.config else None,
    )
    try:
        env = stack["registry"].get(args.name)
        if not env:
            raise SystemExit(f"envelope not found: {args.name}")
        if env.get("status") != "VERIFIED":
            raise SystemExit("manual override only accepts VERIFIED envelopes")
        active = stack["db"].active_trial()
        cancelled = None
        if active:
            cancelled = stack["trials"].rollback(
                active["trial_id"],
                "manual envelope override",
            )
        stack["controller"].set_override(args.name)
        emit({"override": args.name, "cancelled_trial": cancelled})
    finally:
        stack["db"].close()
    return 0


def cmd_envelope_clear_override(args: argparse.Namespace) -> int:
    stack = prepare_stack(
        ROOT,
        Path(args.config).expanduser() if args.config else None,
    )
    try:
        stack["controller"].clear_override()
        emit({"override": None})
    finally:
        stack["db"].close()
    return 0


def _trial_stack(args: argparse.Namespace):
    stack = prepare_stack(
        ROOT,
        Path(args.config).expanduser() if args.config else None,
    )
    return stack


def cmd_trial_status(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        trial = (
            stack["db"].get_trial(args.trial_id) if args.trial_id else stack["db"].active_trial()
        )
        emit({"trial": trial})
    finally:
        stack["db"].close()
    return 0


def cmd_trial_start(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        if int(stack["config"].get("automation.level", 0)) < 2:
            raise SystemExit("trial start requires automation.level >= 2")
        proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        latest = stack["db"].latest_sample()
        if not latest:
            raise SystemExit("no telemetry sample available")
        emit(stack["trials"].start(proposal, latest))
    finally:
        stack["db"].close()
    return 0


def cmd_trial_evaluate(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        latest = stack["db"].latest_sample()
        if not latest:
            raise SystemExit("no telemetry sample available")
        emit({"trial": stack["trials"].tick(latest)})
    finally:
        stack["db"].close()
    return 0


def cmd_trial_rollback(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        trial_id = args.trial_id
        if not trial_id:
            active = stack["db"].active_trial()
            if not active:
                raise SystemExit("no active trial")
            trial_id = active["trial_id"]
        emit(stack["trials"].rollback(trial_id, args.reason))
    finally:
        stack["db"].close()
    return 0


def cmd_trial_promote(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        if int(stack["config"].get("automation.level", 0)) < 2:
            raise SystemExit("trial promote requires automation.level >= 2")
        emit(stack["trials"].promote(args.trial_id))
    finally:
        stack["db"].close()
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    stack = _trial_stack(args)
    try:
        stack["trials"].feedback(
            args.rating,
            trial_id=args.trial_id,
            envelope=args.envelope,
            notes=args.notes,
        )
        emit({"recorded": True})
    finally:
        stack["db"].close()
    return 0


def cmd_hourly(args: argparse.Namespace) -> int:
    config, db, registry = _registry(args)
    try:
        pack = build_knowledge_pack(config, db, registry)
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
        emit(pack)
    finally:
        db.close()
    return 0


def cmd_knowledge_export(args: argparse.Namespace) -> int:
    config, db, registry = _registry(args)
    try:
        pack = build_knowledge_pack(config, db, registry)
        directory = ROOT / "history" / "continuous"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "knowledge.json"
        path.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        emit({"path": str(path), "run_id": pack["run_id"]})
    finally:
        db.close()
    return 0


def cmd_service_run(args: argparse.Namespace) -> int:
    try:
        service = PowerLabService(
            ROOT,
            Path(args.config).expanduser() if args.config else None,
        )
    except LegacyDatabaseError as exc:
        print(f"PowerLab runtime schema is incompatible: {exc}", file=sys.stderr)
        print(
            "Run 'sp7-powerlab reset-runtime --yes' explicitly before restarting.", file=sys.stderr
        )
        return 78
    try:
        service.run(iterations=args.iterations)
    finally:
        service.close()
    return 0


def cmd_service_status(args: argparse.Namespace) -> int:
    emit(
        service_status(
            ROOT,
            Path(args.config).expanduser() if args.config else None,
        )
    )
    return 0


def cmd_root_helper(args: argparse.Namespace) -> int:
    server = RootHelperServer(
        Path(args.socket),
        allow_uid=int(args.allow_uid),
    )
    server.serve_forever()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sp7-powerlab",
        description="Surface Pro 7 battery-life optimization lab",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config")
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    agent_context = sub.add_parser("agent-context")
    agent_context.set_defaults(func=cmd_agent_context)

    reset = sub.add_parser("reset-runtime")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset_runtime)

    calibrate = sub.add_parser("calibrate")
    cal_sub = calibrate.add_subparsers(dest="calibrate_command", required=True)
    cal_status = cal_sub.add_parser("status")
    cal_status.set_defaults(func=cmd_calibrate_status)
    cal_start = cal_sub.add_parser("start")
    cal_start.add_argument(
        "phase",
        choices=("cold_idle", "normal_interactive", "media", "bounded_burst"),
    )
    cal_start.set_defaults(func=cmd_calibrate_start)
    cal_finish = cal_sub.add_parser("finish")
    cal_finish.set_defaults(func=cmd_calibrate_finish)
    cal_battery = cal_sub.add_parser("new-battery")
    cal_battery.set_defaults(func=cmd_calibrate_new_battery)

    observe = sub.add_parser("observe")
    obs_sub = observe.add_subparsers(dest="observe_command", required=True)
    obs_now = obs_sub.add_parser("now")
    obs_now.set_defaults(func=cmd_observe_now)
    for name, func in (
        ("power", cmd_observe_power),
        ("demand", cmd_observe_demand),
        ("thermal", cmd_observe_thermal),
    ):
        item = obs_sub.add_parser(name)
        item.add_argument("--hours", type=float, default=6.0)
        item.set_defaults(func=func)

    incidents = sub.add_parser("incidents")
    incidents.add_argument("--hours", type=float, default=24.0)
    incidents.add_argument("--limit", type=int, default=50)
    incidents.set_defaults(func=cmd_incidents)

    lifecycle = sub.add_parser("lifecycle")
    lifecycle_sub = lifecycle.add_subparsers(dest="lifecycle_command", required=True)
    lifecycle_status = lifecycle_sub.add_parser("status")
    lifecycle_status.set_defaults(func=cmd_lifecycle_status)
    lifecycle_freeze = lifecycle_sub.add_parser("freeze")
    lifecycle_freeze.add_argument("--reason", default="manual freeze")
    lifecycle_freeze.add_argument("--force", action="store_true")
    lifecycle_freeze.set_defaults(func=cmd_lifecycle_freeze)
    lifecycle_reopen = lifecycle_sub.add_parser("reopen")
    lifecycle_reopen.add_argument("--reason", default="manual reopen")
    lifecycle_reopen.set_defaults(func=cmd_lifecycle_reopen)
    lifecycle_coverage = lifecycle_sub.add_parser("coverage")
    lifecycle_coverage.add_argument("--days", type=int)
    lifecycle_coverage.set_defaults(func=cmd_lifecycle_coverage)
    lifecycle_readiness = lifecycle_sub.add_parser("readiness")
    lifecycle_readiness.set_defaults(func=cmd_lifecycle_readiness)
    lifecycle_optimize = lifecycle_sub.add_parser("optimize")
    lifecycle_optimize.add_argument("--reason", default="manual optimization start")
    lifecycle_optimize.set_defaults(func=cmd_lifecycle_optimize)
    lifecycle_validate = lifecycle_sub.add_parser("validate")
    lifecycle_validate.add_argument("--reason", default="manual validation start")
    lifecycle_validate.set_defaults(func=cmd_lifecycle_validate)

    safety = sub.add_parser("safety")
    safety_sub = safety.add_subparsers(dest="safety_command", required=True)
    safety_status = safety_sub.add_parser("status")
    safety_status.set_defaults(func=cmd_safety_status)
    safety_recover = safety_sub.add_parser("recover-rollback")
    safety_recover.add_argument("--reason", required=True)
    safety_recover.set_defaults(func=cmd_safety_recover_rollback)

    investigation = sub.add_parser("investigation")
    investigation_sub = investigation.add_subparsers(
        dest="investigation_command",
        required=True,
    )
    investigation_list = investigation_sub.add_parser("list")
    investigation_list.add_argument("--limit", type=int, default=50)
    investigation_list.set_defaults(func=cmd_investigation_list)
    investigation_inspect = investigation_sub.add_parser("inspect")
    investigation_inspect.add_argument("investigation_id")
    investigation_inspect.set_defaults(func=cmd_investigation_inspect)
    investigation_attribute = investigation_sub.add_parser("attribute")
    investigation_attribute.add_argument("investigation_id")
    investigation_attribute.set_defaults(func=cmd_investigation_attribute)
    investigation_close = investigation_sub.add_parser("close")
    investigation_close.add_argument("investigation_id")
    investigation_close.add_argument(
        "classification",
        choices=(
            "EXPECTED_WORKLOAD_CHANGE",
            "INSUFFICIENT_EVIDENCE",
            "SUSPECTED_REGRESSION",
            "ACTIONABLE_WASTE",
            "CONFIRMED_CONFIG_REGRESSION",
        ),
    )
    investigation_close.add_argument("--reason", default="")
    investigation_close.add_argument("--evidence", action="append")
    investigation_close.add_argument("--verification", action="append")
    investigation_close.set_defaults(func=cmd_investigation_close)

    unexpected_power = sub.add_parser("unexpected-power")
    unexpected_power_sub = unexpected_power.add_subparsers(
        dest="unexpected_power_command",
        required=True,
    )
    unexpected_power_list = unexpected_power_sub.add_parser("list")
    unexpected_power_list.add_argument("--limit", type=int, default=50)
    unexpected_power_list.set_defaults(func=cmd_unexpected_power_list)
    unexpected_power_inspect = unexpected_power_sub.add_parser("inspect")
    unexpected_power_inspect.add_argument("event_id")
    unexpected_power_inspect.set_defaults(func=cmd_unexpected_power_inspect)

    evidence = sub.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_status = evidence_sub.add_parser("status")
    evidence_status.add_argument("--limit", type=int, default=20)
    evidence_status.set_defaults(func=cmd_evidence_status)
    evidence_noise = evidence_sub.add_parser("noise")
    evidence_noise.add_argument("--limit", type=int, default=50)
    evidence_noise.set_defaults(func=cmd_evidence_noise)
    evidence_gauge = evidence_sub.add_parser("gauge")
    evidence_gauge.add_argument("--hours", type=float, default=6.0)
    evidence_gauge.set_defaults(func=cmd_evidence_gauge)
    evidence_trust = evidence_sub.add_parser("trust")
    evidence_trust.add_argument("--hours", type=float, default=6.0)
    evidence_trust.set_defaults(func=cmd_evidence_trust)

    overhead = sub.add_parser("overhead")
    overhead_sub = overhead.add_subparsers(dest="overhead_command", required=True)
    overhead_compare = overhead_sub.add_parser("compare")
    overhead_compare.add_argument("reference_before_run")
    overhead_compare.add_argument("candidate_first_run")
    overhead_compare.add_argument("candidate_second_run")
    overhead_compare.add_argument("reference_after_run")
    overhead_compare.add_argument("--usable-battery-wh", type=float)
    overhead_compare.add_argument("--max-gap-seconds", type=float, default=90.0)
    overhead_compare.set_defaults(func=cmd_overhead_compare)
    overhead_history = overhead_sub.add_parser("history")
    overhead_history.add_argument("--limit", type=int, default=20)
    overhead_history.set_defaults(func=cmd_overhead_history)
    overhead_summary = overhead_sub.add_parser("summary")
    overhead_summary.add_argument("--limit", type=int, default=50)
    overhead_summary.set_defaults(func=cmd_overhead_summary)

    scheduler = sub.add_parser("scheduler")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    scheduler_status = scheduler_sub.add_parser("status")
    scheduler_status.set_defaults(func=cmd_scheduler_status)
    scheduler_candidates = scheduler_sub.add_parser("candidates")
    scheduler_candidates.add_argument("baseline")
    scheduler_candidates.add_argument("--ux-regression", action="store_true")
    scheduler_candidates.set_defaults(func=cmd_scheduler_candidates)
    scheduler_propose = scheduler_sub.add_parser("propose")
    scheduler_propose.add_argument("baseline")
    scheduler_propose.add_argument("--ux-regression", action="store_true")
    scheduler_propose.set_defaults(func=cmd_scheduler_propose)
    scheduler_pause = scheduler_sub.add_parser("pause")
    scheduler_pause.add_argument("--reason", default="manual pause")
    scheduler_pause.set_defaults(func=cmd_scheduler_pause)
    scheduler_resume = scheduler_sub.add_parser("resume")
    scheduler_resume.set_defaults(func=cmd_scheduler_resume)

    envelope = sub.add_parser("envelope")
    env_sub = envelope.add_subparsers(dest="envelope_command", required=True)
    env_list = env_sub.add_parser("list")
    env_list.set_defaults(func=cmd_envelope_list)
    env_inspect = env_sub.add_parser("inspect")
    env_inspect.add_argument("name")
    env_inspect.set_defaults(func=cmd_envelope_inspect)
    env_adopt = env_sub.add_parser("adopt-current")
    env_adopt.add_argument("name")
    env_adopt.add_argument("--note", default="")
    env_adopt.set_defaults(func=cmd_envelope_adopt_current)
    env_block = env_sub.add_parser("block")
    env_block.add_argument("name")
    env_block.set_defaults(func=cmd_envelope_block)
    env_override = env_sub.add_parser("override")
    env_override.add_argument("name")
    env_override.set_defaults(func=cmd_envelope_override)
    env_clear = env_sub.add_parser("clear-override")
    env_clear.set_defaults(func=cmd_envelope_clear_override)

    trial = sub.add_parser("trial")
    trial_sub = trial.add_subparsers(dest="trial_command", required=True)
    trial_status = trial_sub.add_parser("status")
    trial_status.add_argument("--trial-id")
    trial_status.set_defaults(func=cmd_trial_status)
    trial_start = trial_sub.add_parser("start")
    trial_start.add_argument("proposal")
    trial_start.set_defaults(func=cmd_trial_start)
    trial_eval = trial_sub.add_parser("evaluate")
    trial_eval.set_defaults(func=cmd_trial_evaluate)
    trial_rollback = trial_sub.add_parser("rollback")
    trial_rollback.add_argument("--trial-id")
    trial_rollback.add_argument("--reason", default="manual rollback")
    trial_rollback.set_defaults(func=cmd_trial_rollback)
    trial_promote = trial_sub.add_parser("promote")
    trial_promote.add_argument("trial_id")
    trial_promote.set_defaults(func=cmd_trial_promote)

    feedback = sub.add_parser("feedback")
    feedback.add_argument("rating", choices=("good", "sluggish", "bad", "unstable"))
    feedback.add_argument("--trial-id")
    feedback.add_argument("--envelope")
    feedback.add_argument("--notes")
    feedback.set_defaults(func=cmd_feedback)

    hourly = sub.add_parser("hourly")
    hourly.add_argument("--output")
    hourly.set_defaults(func=cmd_hourly)

    knowledge = sub.add_parser("knowledge-export")
    knowledge.set_defaults(func=cmd_knowledge_export)

    service = sub.add_parser("service")
    service_sub = service.add_subparsers(dest="service_command", required=True)
    service_run = service_sub.add_parser("run")
    service_run.add_argument("--iterations", type=int)
    service_run.set_defaults(func=cmd_service_run)
    service_stat = service_sub.add_parser("status")
    service_stat.set_defaults(func=cmd_service_status)

    helper = sub.add_parser("root-helper", help=argparse.SUPPRESS)
    helper_sub = helper.add_subparsers(dest="helper_command", required=True)
    helper_serve = helper_sub.add_parser("serve")
    helper_serve.add_argument("--socket", required=True)
    helper_serve.add_argument("--allow-uid", required=True, type=int)
    helper_serve.set_defaults(func=cmd_root_helper)

    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except LegacyDatabaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
