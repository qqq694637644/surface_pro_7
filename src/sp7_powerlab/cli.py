from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .analytics import battery_usage_summary
from .calibration import CalibrationManager
from .config import load_config, load_machine, load_thermal_config
from .demand import DemandObserver
from .envelopes import EnvelopeRegistry
from .hardware import inspect_hardware
from .helper import RootHelperServer
from .llm import build_knowledge_pack
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
    except LegacyDatabaseError as exc:
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
        path = directory / "knowledge-v2.json"
        path.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        emit({"path": str(path), "run_id": pack["run_id"]})
    finally:
        db.close()
    return 0


def cmd_service_run(args: argparse.Namespace) -> int:
    service = PowerLabService(
        ROOT,
        Path(args.config).expanduser() if args.config else None,
    )
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
        description="Surface Pro 7 battery-life optimization lab v2",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config")
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

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
