from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .metrics import (
    sha256_file,
    system_snapshot as extended_system_snapshot,
    telemetry_snapshot,
)
from .actuators import ActuatorManager
from .collector import Collector, benchmark_collector
from .config import load_config
from .experiments import TrialError, TrialManager
from .knowledge import KnowledgeManager
from .jobs import run_measured_task
from .helper import RootHelperServer
from .orchestrator import HourlyOrchestrator, OrchestratorError, validate_decision
from .policy import PolicyEngine
from .profiles import ProfileRegistry
from .quality import integrate_energy
from .storage import Database
from .proposals import (
    SAFE_PARAMETERS,
    SENSITIVE_PREFIXES,
    classify_parameter,
    load_json,
    normalize_proposal,
    proposal_template,
    validate_proposal,
)


ROOT = Path.cwd()
EXPERIMENTS = ROOT / "experiments"
ACTIVE_FILE = ROOT / ".powerlab-active"
DB_PATH = ROOT / "powerlab.sqlite3"
PROPOSALS = ROOT / "proposals"
HISTORY = ROOT / "history"
SCHEMA_VERSION = 1

TELEMETRY_FIELDS = [
    "timestamp",
    "battery_percent",
    "battery_status",
    "power_w",
    "energy_wh",
    "load1",
    "load5",
    "load15",
    "current_freq_khz",
    "max_freq_khz",
    "governor",
    "epp",
    "turbo_disabled",
    "cpu0_deep_idle_time_us",
    "brightness_percent",
    "max_temp_c",
    "wifi_interface",
    "wifi_operstate",
    "wifi_rx_bytes",
    "wifi_tx_bytes",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def system_snapshot() -> dict:
    return {"timestamp": now_iso(), **extended_system_snapshot()}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def bool_text(value: object) -> str:
    if value is None:
        return ""
    return "1" if value is True else "0" if value is False else str(value)


def snapshot_config_files(exp_dir: Path, paths: list[str]) -> list[dict]:
    if not paths:
        return []
    dest = exp_dir / "config-snapshot"
    dest.mkdir(exist_ok=True)
    records: list[dict] = []
    for index, raw in enumerate(paths, start=1):
        source = Path(raw).expanduser()
        record = {"source": str(source)}
        if not source.is_file():
            record["error"] = "not-a-readable-file"
            records.append(record)
            continue
        target = dest / f"{index:02d}-{source.name}"
        try:
            shutil.copy2(source, target)
            record.update(
                {
                    "snapshot": str(target.relative_to(exp_dir)),
                    "sha256": sha256_file(target),
                    "bytes": target.stat().st_size,
                }
            )
        except (OSError, PermissionError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    return records


def load_proposal(proposal_id: str) -> tuple[Path, dict]:
    path = PROPOSALS / f"{proposal_id}.json"
    if not path.exists():
        raise SystemExit(f"Proposal not found: {proposal_id}")
    try:
        proposal = load_json(path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    errors = validate_proposal(proposal)
    if errors:
        raise SystemExit("Stored proposal is invalid:\n- " + "\n- ".join(errors))
    return path, proposal


def load_experiment_result(exp_id: str) -> dict:
    raw = EXPERIMENTS / exp_id / "result.json"
    if raw.exists():
        return json.loads(raw.read_text(encoding="utf-8"))
    archived = HISTORY / f"{exp_id}.json"
    if archived.exists():
        payload = json.loads(archived.read_text(encoding="utf-8"))
        result = payload.get("result")
        if isinstance(result, dict):
            return result
    raise SystemExit(f"Completed experiment not found: {exp_id}")


def evaluate_proposal(proposal: dict, result: dict) -> dict:
    base = load_experiment_result(proposal["based_on_experiment"])
    base_avg = base.get("power_w", {}).get("average")
    candidate_avg = result.get("power_w", {}).get("average")
    delta_w = None
    delta_percent = None
    if base_avg is not None and candidate_avg is not None:
        delta_w = candidate_avg - base_avg
        delta_percent = (delta_w / base_avg * 100.0) if base_avg else None

    base_temp = base.get("conditions", {}).get("average_max_temp_c")
    candidate_temp = result.get("conditions", {}).get("average_max_temp_c")
    temp_delta = None
    if base_temp is not None and candidate_temp is not None:
        temp_delta = candidate_temp - base_temp

    validation = proposal.get("validation") or {}
    acceptance = validation.get("acceptance") or {}
    checks: dict[str, bool | None] = {}

    power_limit = acceptance.get("max_average_power_delta_w")
    checks["average_power_delta"] = (
        None if power_limit is None or delta_w is None else delta_w <= power_limit
    )

    temp_limit = acceptance.get("max_temperature_increase_c")
    checks["temperature_delta"] = (
        None if temp_limit is None or temp_delta is None else temp_delta <= temp_limit
    )

    requested_duration = validation.get("duration_seconds")
    actual_duration = result.get("duration_seconds")
    checks["duration"] = (
        None
        if not isinstance(requested_duration, (int, float)) or actual_duration is None
        else actual_duration >= requested_duration
    )
    expected_workload = validation.get("workload")
    checks["workload_match"] = (
        None if not expected_workload else result.get("workload") == expected_workload
    )
    statuses = set(result.get("battery_statuses") or [])
    checks["battery_discharging_only"] = statuses == {"Discharging"} if statuses else None
    base_statuses = set(base.get("battery_statuses") or [])
    checks["baseline_battery_discharging_only"] = (
        base_statuses == {"Discharging"} if base_statuses else None
    )
    checks["sample_count"] = (
        int(result.get("samples") or 0) >= 30
        and int(base.get("samples") or 0) >= 30
    )
    checks["power_available"] = base_avg is not None and candidate_avg is not None
    required_checks = [
        "duration",
        "workload_match",
        "battery_discharging_only",
        "baseline_battery_discharging_only",
        "sample_count",
        "power_available",
    ]
    if power_limit is not None:
        required_checks.append("average_power_delta")
    if temp_limit is not None:
        required_checks.append("temperature_delta")
    if any(checks.get(key) is None for key in required_checks):
        criteria = "insufficient-data"
    elif all(bool(checks.get(key)) for key in required_checks):
        criteria = "criteria-met"
    else:
        criteria = "criteria-not-met"

    return {
        "proposal_id": proposal["id"],
        "based_on_experiment": proposal["based_on_experiment"],
        "candidate_experiment": result["id"],
        "parameter": proposal["change"]["parameter"],
        "observed": {
            "average_power_delta_w": delta_w,
            "average_power_delta_percent": delta_percent,
            "average_temperature_delta_c": temp_delta,
        },
        "checks": checks,
        "criteria_status": criteria,
        "review_status": "pending-human-review",
    }


def write_history_record(
    exp_dir: Path,
    result: dict,
    ai_summary: dict,
    proposal: dict | None = None,
    proposal_evaluation: dict | None = None,
) -> Path:
    meta = json.loads((exp_dir / "meta.json").read_text(encoding="utf-8"))
    HISTORY.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": SCHEMA_VERSION,
        "archived_at": now_iso(),
        "result": result,
        "ai_summary": ai_summary,
        "experiment_meta": {
            "id": meta.get("id"),
            "name": meta.get("name"),
            "profile": meta.get("profile"),
            "workload": meta.get("workload"),
            "hypothesis": meta.get("hypothesis"),
            "notes": meta.get("notes"),
            "proposal_id": meta.get("proposal_id"),
            "proposal_sha256": meta.get("proposal_sha256"),
            "config_files": meta.get("config_files", []),
        },
        "proposal": proposal,
        "proposal_evaluation": proposal_evaluation,
    }
    path = HISTORY / f"{result['id']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def ensure_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            profile TEXT,
            workload TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            samples INTEGER,
            avg_power_w REAL,
            median_power_w REAL,
            p95_power_w REAL,
            min_power_w REAL,
            max_power_w REAL
        )
        """
    )
    conn.commit()
    return conn


def active_experiment() -> Path:
    if not ACTIVE_FILE.exists():
        raise SystemExit("No active experiment. Run 'sp7-powerlab start' first.")
    path = Path(ACTIVE_FILE.read_text(encoding="utf-8").strip())
    if not path.exists():
        raise SystemExit(f"Active experiment path no longer exists: {path}")
    return path


def cmd_doctor(_: argparse.Namespace) -> int:
    snap = system_snapshot()
    snap["readiness"] = {
        "battery_telemetry": bool(snap["battery"].get("present") and snap["battery"].get("power_w") is not None),
        "surface_pro_7_detected": bool(snap["dmi"].get("is_surface_pro_7")),
        "brightness_telemetry": bool(snap["display"].get("present")),
        "thermal_telemetry": snap["thermal"].get("max_temp_c") is not None,
        "wifi_telemetry": snap["network"].get("wifi") is not None,
    }
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    if not snap["battery"]["present"]:
        print("\nWARNING: no BAT* power-supply device detected.", file=sys.stderr)
        return 2
    if snap["battery"].get("power_w") is None:
        print("\nWARNING: battery exists but whole-device power could not be read.", file=sys.stderr)
        return 3
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    if ACTIVE_FILE.exists():
        raise SystemExit(f"An experiment is already active: {ACTIVE_FILE.read_text().strip()}")

    proposal_path: Path | None = None
    proposal: dict | None = None
    if args.proposal:
        proposal_path, proposal = load_proposal(args.proposal)
        if args.workload == "unknown":
            args.workload = proposal["validation"]["workload"]
        if not args.hypothesis:
            args.hypothesis = proposal["rationale"]

    EXPERIMENTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in args.name).strip("-") or "experiment"
    exp_id = f"{stamp}_{safe_name}"
    exp_dir = EXPERIMENTS / exp_id
    exp_dir.mkdir()

    config_files = snapshot_config_files(exp_dir, args.config_file or [])
    meta = {
        "schema_version": SCHEMA_VERSION,
        "id": exp_id,
        "name": args.name,
        "profile": args.profile,
        "workload": args.workload,
        "started_at": now_iso(),
        "hypothesis": args.hypothesis,
        "notes": args.notes,
        "system": system_snapshot(),
        "config_files": config_files,
        "proposal_id": proposal["id"] if proposal else None,
        "proposal_sha256": sha256_file(proposal_path) if proposal_path else None,
    }
    (exp_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (exp_dir / "telemetry.csv").open("w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=TELEMETRY_FIELDS).writeheader()
    ACTIVE_FILE.write_text(str(exp_dir.resolve()), encoding="utf-8")

    conn = ensure_db()
    conn.execute(
        "INSERT INTO experiments(id,name,profile,workload,started_at) VALUES(?,?,?,?,?)",
        (exp_id, args.name, args.profile, args.workload, meta["started_at"]),
    )
    conn.commit()
    conn.close()

    print(exp_id)
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than 0")
    if args.duration < 0:
        raise SystemExit("--duration cannot be negative")

    exp_dir = active_experiment()
    csv_path = exp_dir / "telemetry.csv"
    deadline = None if args.duration == 0 else time.monotonic() + args.duration

    with csv_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TELEMETRY_FIELDS)
        try:
            while deadline is None or time.monotonic() < deadline:
                row = {"timestamp": now_iso(), **telemetry_snapshot()}
                row["turbo_disabled"] = bool_text(row.get("turbo_disabled"))
                writer.writerow({field: row.get(field, "") for field in TELEMETRY_FIELDS})
                fh.flush()
                if args.once:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    return 0


def percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def summarize(exp_dir: Path) -> dict:
    meta = json.loads((exp_dir / "meta.json").read_text(encoding="utf-8"))
    powers: list[float] = []
    brightness: list[float] = []
    temperatures: list[float] = []
    loads: list[float] = []
    timestamps: list[datetime] = []
    wifi_rx: list[float] = []
    wifi_tx: list[float] = []
    deep_idle: list[float] = []
    battery_statuses: set[str] = set()
    integration_samples: list[dict] = []

    with (exp_dir / "telemetry.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = parse_time(row.get("timestamp"))
            if ts is not None:
                timestamps.append(ts)
                integration_samples.append(
                    {
                        "ts": ts.timestamp(),
                        "power_w": number(row.get("power_w")),
                        "battery_status": row.get("battery_status"),
                    }
                )
            for key, target in (
                ("power_w", powers),
                ("brightness_percent", brightness),
                ("max_temp_c", temperatures),
                ("load1", loads),
                ("wifi_rx_bytes", wifi_rx),
                ("wifi_tx_bytes", wifi_tx),
                ("cpu0_deep_idle_time_us", deep_idle),
            ):
                value = number(row.get(key))
                if value is not None and (key != "power_w" or value >= 0):
                    target.append(value)
            status = row.get("battery_status")
            if status:
                battery_statuses.add(status)

    duration_seconds = None
    if len(timestamps) >= 2:
        duration_seconds = max(0.0, (timestamps[-1] - timestamps[0]).total_seconds())

    deep_idle_fraction = None
    if duration_seconds and len(deep_idle) >= 2:
        delta = deep_idle[-1] - deep_idle[0]
        if delta >= 0:
            deep_idle_fraction = max(0.0, min(1.0, delta / (duration_seconds * 1_000_000.0)))
    integrated = integrate_energy(
        integration_samples,
        max_gap_seconds=30.0,
        require_discharging=True,
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "id": meta["id"],
        "name": meta["name"],
        "profile": meta.get("profile"),
        "workload": meta.get("workload"),
        "hypothesis": meta.get("hypothesis"),
        "started_at": meta["started_at"],
        "finished_at": now_iso(),
        "duration_seconds": duration_seconds,
        "samples": len(powers),
        "battery_statuses": sorted(battery_statuses),
        "power_w": {
            "average": integrated["average_power_w"],
            "median": statistics.median(powers) if powers else None,
            "p95": percentile95(powers),
            "minimum": min(powers) if powers else None,
            "maximum": max(powers) if powers else None,
        },
        "conditions": {
            "valid_discharge_duration_seconds": integrated["valid_duration_s"],
            "integrated_energy_wh": integrated["energy_wh"],
            "gap_count": integrated["gaps"],
            "average_brightness_percent": statistics.fmean(brightness) if brightness else None,
            "average_max_temp_c": statistics.fmean(temperatures) if temperatures else None,
            "average_load1": statistics.fmean(loads) if loads else None,
            "cpu0_deep_idle_fraction": deep_idle_fraction,
            "wifi_rx_mb_delta": (wifi_rx[-1] - wifi_rx[0]) / 1_000_000.0 if len(wifi_rx) >= 2 else None,
            "wifi_tx_mb_delta": (wifi_tx[-1] - wifi_tx[0]) / 1_000_000.0 if len(wifi_tx) >= 2 else None,
        },
    }
    return result


def previous_comparable(current_id: str, workload: str | None) -> dict | None:
    conn = ensure_db()
    row = conn.execute(
        """
        SELECT id,name,profile,workload,samples,avg_power_w,p95_power_w,finished_at
        FROM experiments
        WHERE id <> ? AND finished_at IS NOT NULL AND (? IS NULL OR workload = ?)
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        (current_id, workload, workload),
    ).fetchone()
    conn.close()
    keys = ["id", "name", "profile", "workload", "samples", "avg_power_w", "p95_power_w", "finished_at"]
    if row is not None:
        return dict(zip(keys, row))

    candidates: list[dict] = []
    if HISTORY.exists():
        for path in HISTORY.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            result = payload.get("result")
            if not isinstance(result, dict) or result.get("id") == current_id:
                continue
            if workload is not None and result.get("workload") != workload:
                continue
            candidates.append(
                {
                    "id": result.get("id"),
                    "name": result.get("name"),
                    "profile": result.get("profile"),
                    "workload": result.get("workload"),
                    "samples": result.get("samples"),
                    "avg_power_w": result.get("power_w", {}).get("average"),
                    "p95_power_w": result.get("power_w", {}).get("p95"),
                    "finished_at": result.get("finished_at"),
                }
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.get("finished_at") or "", reverse=True)
    return candidates[0]


def build_ai_summary(exp_dir: Path, result: dict) -> dict:
    meta = json.loads((exp_dir / "meta.json").read_text(encoding="utf-8"))
    system = meta.get("system", {})
    previous = previous_comparable(result["id"], result.get("workload"))
    current_avg = result["power_w"]["average"]
    comparison = None
    if previous and current_avg is not None and previous.get("avg_power_w") is not None:
        baseline = float(previous["avg_power_w"])
        delta = current_avg - baseline
        comparison = {
            "baseline_experiment_id": previous["id"],
            "baseline_average_power_w": baseline,
            "candidate_average_power_w": current_avg,
            "delta_w": delta,
            "delta_percent": delta / baseline * 100.0 if baseline else None,
        }

    caveats: list[str] = []
    if result["samples"] < 30:
        caveats.append("low_sample_count")
    if result["duration_seconds"] is not None and result["duration_seconds"] < 300:
        caveats.append("short_duration_under_5_minutes")
    if current_avg is None:
        caveats.append("whole_device_power_unavailable")
    statuses = set(result.get("battery_statuses") or [])
    if statuses and statuses != {"Discharging"}:
        caveats.append("battery_not_exclusively_discharging")
    if not system.get("dmi", {}).get("is_surface_pro_7"):
        caveats.append("surface_pro_7_not_confirmed_by_dmi")

    cpu = dict(system.get("cpu") or {})
    cpu.pop("cpu0_idle_states", None)
    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "AI-readable battery-life experiment summary",
        "objective": "minimize whole-device battery power while preserving acceptable usability and stability",
        "guardrails": {
            "change_one_primary_variable_per_experiment": True,
            "do_not_autonomously_change": [
                "kernel_command_line",
                "suspend_internals",
                "PCI_runtime_PM",
                "USB_autosuspend",
                "I2C_devices",
                "firmware",
                "linux_surface_kernel",
            ],
        },
        "experiment": {
            "id": result["id"],
            "name": result["name"],
            "profile": result.get("profile"),
            "workload": result.get("workload"),
            "hypothesis": result.get("hypothesis"),
            "proposal_id": meta.get("proposal_id"),
            "config_files": meta.get("config_files", []),
        },
        "hardware_context": {
            "dmi": system.get("dmi"),
            "kernel": system.get("kernel"),
            "battery": system.get("battery"),
            "cpu": cpu,
            "display": system.get("display"),
            "network": {"wifi": (system.get("network") or {}).get("wifi")},
        },
        "metrics": result,
        "comparison_to_previous_same_workload": comparison,
        "caveats": caveats,
        "next_experiment_instruction": "Propose at most one primary reversible change and state the expected power/usability tradeoff.",
    }


def cmd_finish(_: argparse.Namespace) -> int:
    exp_dir = active_experiment()
    result = summarize(exp_dir)
    ai_summary = build_ai_summary(exp_dir, result)
    meta = json.loads((exp_dir / "meta.json").read_text(encoding="utf-8"))
    proposal = None
    proposal_evaluation = None
    proposal_id = meta.get("proposal_id")
    if proposal_id:
        proposal_path, proposal = load_proposal(proposal_id)
        expected_hash = meta.get("proposal_sha256")
        current_hash = sha256_file(proposal_path)
        if expected_hash and expected_hash != current_hash:
            proposal_evaluation = {
                "proposal_id": proposal_id,
                "criteria_status": "insufficient-data",
                "review_status": "blocked",
                "reason": "proposal_hash_mismatch",
                "expected_sha256": expected_hash,
                "current_sha256": current_hash,
            }
        else:
            proposal_evaluation = evaluate_proposal(proposal, result)

    (exp_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (exp_dir / "ai-summary.json").write_text(
        json.dumps(ai_summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if proposal_evaluation is not None:
        (exp_dir / "proposal-evaluation.json").write_text(
            json.dumps(proposal_evaluation, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    p = result["power_w"]
    conn = ensure_db()
    conn.execute(
        """
        UPDATE experiments
        SET finished_at=?, samples=?, avg_power_w=?, median_power_w=?,
            p95_power_w=?, min_power_w=?, max_power_w=?
        WHERE id=?
        """,
        (
            result["finished_at"],
            result["samples"],
            p["average"],
            p["median"],
            p["p95"],
            p["minimum"],
            p["maximum"],
            result["id"],
        ),
    )
    conn.commit()
    conn.close()
    history_path = write_history_record(
        exp_dir,
        result,
        ai_summary,
        proposal=proposal,
        proposal_evaluation=proposal_evaluation,
    )
    ACTIVE_FILE.unlink(missing_ok=True)
    output = dict(result)
    output["history_file"] = str(history_path)
    if proposal_evaluation is not None:
        output["proposal_evaluation"] = proposal_evaluation
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    conn = ensure_db()
    rows = conn.execute(
        "SELECT id,name,profile,workload,samples,avg_power_w,p95_power_w,finished_at FROM experiments ORDER BY started_at"
    ).fetchall()
    conn.close()
    for row in rows:
        print("\t".join("" if value is None else str(value) for value in row))
    return 0


def load_result(exp_id: str) -> dict:
    return load_experiment_result(exp_id)


def cmd_compare(args: argparse.Namespace) -> int:
    a = load_result(args.a)
    b = load_result(args.b)
    avg_a = a["power_w"]["average"]
    avg_b = b["power_w"]["average"]
    delta = None
    pct = None
    if avg_a is not None and avg_b is not None:
        delta = avg_b - avg_a
        pct = (delta / avg_a * 100.0) if avg_a else None
    out = {
        "baseline": args.a,
        "candidate": args.b,
        "average_power_w": {"baseline": avg_a, "candidate": avg_b},
        "delta_w": delta,
        "delta_percent": pct,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_proposal_template(args: argparse.Namespace) -> int:
    result = load_experiment_result(args.experiment)
    template = proposal_template(args.experiment, result.get("workload") or "unknown")
    text = json.dumps(template, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        target = Path(args.output)
        target.write_text(text, encoding="utf-8")
        print(target)
    else:
        print(text, end="")
    return 0


def cmd_proposal_add(args: argparse.Namespace) -> int:
    source = Path(args.file)
    try:
        data = load_json(source)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    errors = validate_proposal(data)
    if errors:
        raise SystemExit("Proposal validation failed:\n- " + "\n- ".join(errors))

    load_experiment_result(data["based_on_experiment"])
    proposal = normalize_proposal(data)
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    target = PROPOSALS / f"{proposal['id']}.json"
    if target.exists():
        raise SystemExit(f"Proposal already exists: {proposal['id']}")
    target.write_text(json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(
        {
            "id": proposal["id"],
            "file": str(target),
            "parameter": proposal["change"]["parameter"],
            "policy": proposal["powerlab_policy"],
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


def cmd_proposal_list(_: argparse.Namespace) -> int:
    if not PROPOSALS.exists():
        return 0
    for path in sorted(PROPOSALS.glob("*.json")):
        try:
            proposal = load_json(path)
        except ValueError:
            continue
        change = proposal.get("change") or {}
        policy = proposal.get("powerlab_policy") or classify_parameter(str(change.get("parameter", "")))
        print(
            "\t".join(
                [
                    str(proposal.get("id", path.stem)),
                    str(proposal.get("based_on_experiment", "")),
                    str(change.get("parameter", "")),
                    str(policy.get("classification", "")),
                    str(proposal.get("title", "")),
                ]
            )
        )
    return 0


def cmd_proposal_show(args: argparse.Namespace) -> int:
    _, proposal = load_proposal(args.proposal)
    print(json.dumps(proposal, indent=2, ensure_ascii=False))
    return 0


def cmd_history_list(_: argparse.Namespace) -> int:
    if not HISTORY.exists():
        return 0
    records: list[dict] = []
    for path in HISTORY.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            records.append(result)
    records.sort(key=lambda item: item.get("started_at") or "")
    for result in records:
        avg = result.get("power_w", {}).get("average")
        print(
            "\t".join(
                [
                    str(result.get("id", "")),
                    str(result.get("name", "")),
                    str(result.get("profile", "")),
                    str(result.get("workload", "")),
                    "" if avg is None else str(avg),
                    str(result.get("finished_at", "")),
                ]
            )
        )
    return 0


def cmd_history_show(args: argparse.Namespace) -> int:
    path = HISTORY / f"{args.experiment}.json"
    if not path.exists():
        raise SystemExit(f"Archived experiment not found: {args.experiment}")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def load_history_record(exp_id: str) -> tuple[Path, dict]:
    path = HISTORY / f"{exp_id}.json"
    if not path.exists():
        raise SystemExit(f"Archived experiment not found: {exp_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read history record: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("History record root must be an object")
    return path, payload


def cmd_decision(args: argparse.Namespace) -> int:
    if args.responsiveness is not None and not 1 <= args.responsiveness <= 5:
        raise SystemExit("--responsiveness must be between 1 and 5")

    path, payload = load_history_record(args.experiment)
    human = {
        "recorded_at": now_iso(),
        "decision": args.status,
        "responsiveness_1_to_5": args.responsiveness,
        "stability": args.stability,
        "suspend_wake": args.suspend_wake,
        "notes": args.notes,
    }
    payload["human_evaluation"] = human
    proposal_evaluation = payload.get("proposal_evaluation")
    if isinstance(proposal_evaluation, dict):
        proposal_evaluation["review_status"] = f"human-{args.status}"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(human, indent=2, ensure_ascii=False))
    return 0


def build_ai_pack(limit: int = 12, workload: str | None = None) -> dict:
    records: list[dict] = []
    if HISTORY.exists():
        for path in HISTORY.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            if workload and result.get("workload") != workload:
                continue
            records.append(payload)

    records.sort(
        key=lambda item: (item.get("result") or {}).get("finished_at") or "",
        reverse=True,
    )
    selected = records[:limit]
    experiments: list[dict] = []
    for payload in selected:
        result = payload.get("result") or {}
        ai_summary = payload.get("ai_summary") or {}
        experiments.append(
            {
                "id": result.get("id"),
                "name": result.get("name"),
                "profile": result.get("profile"),
                "workload": result.get("workload"),
                "hypothesis": result.get("hypothesis"),
                "finished_at": result.get("finished_at"),
                "power_w": result.get("power_w"),
                "conditions": result.get("conditions"),
                "caveats": ai_summary.get("caveats"),
                "config_files": (payload.get("experiment_meta") or {}).get("config_files", []),
                "proposal": payload.get("proposal"),
                "proposal_evaluation": payload.get("proposal_evaluation"),
                "human_evaluation": payload.get("human_evaluation"),
            }
        )

    latest_id = experiments[0]["id"] if experiments else ""
    latest_workload = experiments[0]["workload"] if experiments else (workload or "unknown")
    return {
        "schema_version": 1,
        "purpose": "Context pack for the next SP7 battery tuning proposal",
        "objective": "Reduce whole-device battery power without sacrificing acceptable responsiveness, stability, or suspend/wake behavior.",
        "rules": {
            "propose_exactly_one_primary_change": True,
            "never_claim_success_from_power_alone": True,
            "respect_human_rejections": True,
            "autonomous_setting_application": False,
            "allowlisted_reversible_parameters": sorted(SAFE_PARAMETERS),
            "sensitive_parameter_prefixes": list(SENSITIVE_PREFIXES),
        },
        "history_count": len(records),
        "included_count": len(experiments),
        "experiments_newest_first": experiments,
        "next_proposal_template": proposal_template(latest_id, latest_workload) if latest_id else None,
        "instruction": (
            "Use measured history and human feedback. Return one proposal matching next_proposal_template. "
            "Prefer a reversible allowlisted parameter unless evidence strongly justifies a human-review-only experiment."
        ),
    }


def cmd_ai_pack(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    pack = build_ai_pack(limit=args.limit, workload=args.workload)
    text = json.dumps(pack, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        target = Path(args.output)
        target.write_text(text, encoding="utf-8")
        print(target)
    else:
        print(text, end="")
    return 0


def continuous_stack(config_path: str | None = None):
    path = Path(config_path).expanduser() if config_path else None
    config = load_config(ROOT, path)
    db = Database(config.path("storage.database", "runtime/powerlab.sqlite3"))
    registry = ProfileRegistry(config.root, db)
    registry.load()
    actuators = ActuatorManager(config)
    trials = TrialManager(config, db, actuators, registry)
    knowledge = KnowledgeManager(config, db, registry)
    policy = PolicyEngine(config, db, registry, actuators)
    orchestrator = HourlyOrchestrator(config, db, knowledge, trials)
    return config, db, registry, actuators, trials, knowledge, policy, orchestrator


def current_context_from_sample(sample: dict | None) -> dict | None:
    if not sample:
        return None
    context = sample.get("context")
    if isinstance(context, dict):
        return {
            **context,
            "battery_pct": sample.get("battery_pct"),
            "battery_status": sample.get("battery_status"),
            "profile_id": sample.get("profile_id"),
        }
    scene = sample.get("context_scene")
    if not scene:
        return None
    return {
        "context_id": None,
        "scene": scene,
        "confidence": sample.get("context_confidence"),
        "features": {},
        "battery_pct": sample.get("battery_pct"),
        "battery_status": sample.get("battery_status"),
        "profile_id": sample.get("profile_id"),
    }


def cmd_service_run(args: argparse.Namespace) -> int:
    config, db, registry, actuators, trials, knowledge, policy, _ = continuous_stack(
        args.config
    )
    try:
        if bool(config.get("automation.recover_trial_on_collector_start", True)):
            recovered = trials.recover_stale_trial()
            if recovered:
                print(json.dumps({"trial_recovery": recovered}, ensure_ascii=False))
        knowledge.detect_drift()
        def guarded_policy(sample, context):
            waiting = trials.status()
            if waiting and waiting.get("state") == "WAITING_FOR_CONTEXT":
                try:
                    started = trials.maybe_start_waiting(
                        {
                            **context,
                            "battery_pct": sample.get("battery_pct"),
                            "battery_status": sample.get("battery_status"),
                            "profile_id": sample.get("profile_id"),
                        },
                        unattended=bool(
                            config.get("automation.auto_run_low_risk_trials", False)
                        ),
                    )
                    if started:
                        db.add_system_event(
                            "waiting_trial_context_matched",
                            {
                                "trial_id": started["trial_id"],
                                "scene": context.get("scene"),
                            },
                        )
                except TrialError as exc:
                    db.add_system_event(
                        "waiting_trial_start_failed",
                        {"error": str(exc), "context": context},
                    )
            temp = sample.get("temp_c")
            if (
                trials.status()
                and isinstance(temp, (int, float))
                and temp >= float(config.get("policy.thermal_emergency_c", 90.0))
            ):
                trials.rollback(reason=f"thermal emergency at {temp:.1f}C")
            return policy.consider(sample, context)

        def effective_profile():
            active = trials.status()
            if (
                active
                and active.get("state") not in {"WAITING_FOR_CONTEXT", "ROLLED_BACK", "FAILED"}
                and active.get("candidate_profile")
            ):
                return active.get("candidate_profile")
            return policy.current_profile()

        collector = Collector(
            config,
            db,
            profile_provider=effective_profile,
            policy_callback=guarded_policy,
        )
        collector.run()
        return 0
    finally:
        db.close()


def cmd_service_status(args: argparse.Namespace) -> int:
    config, db, registry, actuators, trials, knowledge, policy, _ = continuous_stack(
        args.config
    )
    try:
        latest = db.latest_sample()
        out = {
            "database": db.health(),
            "active_trial": trials.status(),
            "actuators": actuators.inspect(),
            "profiles": db.profiles(),
            "context_policies": db.context_policies(),
            "latest_sample": latest,
            "config": str(config.source) if config.source else None,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_collect_once_continuous(args: argparse.Namespace) -> int:
    config, db, registry, actuators, trials, knowledge, policy, _ = continuous_stack(
        args.config
    )
    try:
        collector = Collector(
            config,
            db,
            profile_provider=policy.current_profile,
            policy_callback=policy.consider if args.apply_policy else None,
        )
        collector.startup_snapshot()
        sample = collector.collect_once()
        collector.sessions.flush(extra_reason="single_sample")
        print(json.dumps(sample, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_collector_benchmark(args: argparse.Namespace) -> int:
    config, db, *_ = continuous_stack(args.config)
    try:
        result = benchmark_collector(config, db, samples=args.samples)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    finally:
        db.close()


def cmd_observe(args: argparse.Namespace) -> int:
    _, db, _, _, _, knowledge, _, _ = continuous_stack(args.config)
    try:
        out = {
            "summary": knowledge.scene_summary(args.hours),
            "current": db.latest_sample(),
            "active_trial": db.active_trial(),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_contexts_continuous(args: argparse.Namespace) -> int:
    _, db, _, _, _, knowledge, _, _ = continuous_stack(args.config)
    try:
        since = time.time() - args.hours * 3600
        out = {
            "summary": knowledge.scene_summary(args.hours),
            "sessions": db.recent_sessions(since, scene=args.scene),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_current_continuous(args: argparse.Namespace) -> int:
    _, db, _, actuators, trials, _, _, _ = continuous_stack(args.config)
    try:
        print(
            json.dumps(
                {
                    "sample": db.latest_sample(),
                    "active_trial": trials.status(),
                    "actuators": actuators.inspect(),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    finally:
        db.close()


def cmd_profile_list(args: argparse.Namespace) -> int:
    _, db, registry, *_ = continuous_stack(args.config)
    try:
        print(
            json.dumps(
                {
                    "profiles": registry.load(),
                    "context_policies": db.context_policies(),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    finally:
        db.close()


def cmd_profile_inspect(args: argparse.Namespace) -> int:
    _, db, registry, *_ = continuous_stack(args.config)
    try:
        profile = registry.get(args.profile_id)
        if not profile:
            raise SystemExit(f"Profile not found: {args.profile_id}")
        print(json.dumps(profile, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_profile_apply(args: argparse.Namespace) -> int:
    _, db, registry, _, _, _, policy, _ = continuous_stack(args.config)
    try:
        profile = registry.get(args.profile_id)
        if not profile:
            raise SystemExit(f"Profile not found: {args.profile_id}")
        result = policy.apply_profile_id(
            args.profile_id,
            reason=args.reason or "manual profile apply",
            force=True,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_profile_status(args: argparse.Namespace) -> int:
    _, db, registry, *_ = continuous_stack(args.config)
    try:
        registry.set_status(args.profile_id, args.status)
        if args.scene and args.status == "verified":
            profile = registry.get(args.profile_id)
            if not profile:
                raise SystemExit(f"Profile not found: {args.profile_id}")
            latest = db.latest_sample() or {}
            app_version = latest.get("app_version")
            last_validated = {
                "ts": time.time(),
                "scene": args.scene,
                "kernel": latest.get("kernel"),
                "app_major_version": (
                    app_version.get("major")
                    if isinstance(app_version, dict)
                    else None
                ),
                "battery_health_pct": latest.get("battery_health_pct"),
                "brightness_pct": latest.get("brightness_pct"),
                "temperature_c": latest.get("temp_c"),
            }
            evidence = {
                **(profile.get("evidence") or {}),
                "manual_verification_note": args.note,
                "scenes": sorted(
                    set(
                        [
                            *(profile.get("evidence") or {}).get("scenes", []),
                            args.scene,
                        ]
                    )
                ),
            }
            db.upsert_profile(
                {
                    **profile,
                    "status": "verified",
                    "evidence": evidence,
                    "last_validated": last_validated,
                }
            )
            registry.load()
            db.set_context_policy(
                args.scene,
                args.profile_id,
                source="manual_profile_verification",
                evidence={
                    "note": args.note,
                    "last_validated": last_validated,
                },
            )
        print(
            json.dumps(
                {
                    "profile_id": args.profile_id,
                    "status": args.status,
                    "scene": args.scene,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        db.close()


def cmd_trial_start(args: argparse.Namespace) -> int:
    config, db, _, _, trials, _, _, _ = continuous_stack(args.config)
    try:
        proposal = load_json(Path(args.proposal))
        errors = validate_proposal(proposal)
        if errors:
            raise SystemExit("Proposal validation failed:\n- " + "\n- ".join(errors))
        proposal = normalize_proposal(proposal)
        PROPOSALS.mkdir(parents=True, exist_ok=True)
        proposal_path = PROPOSALS / f"{proposal['id']}.json"
        proposal_path.write_text(
            json.dumps(proposal, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        context = current_context_from_sample(db.latest_sample())
        result = trials.start(
            proposal,
            current_context=None if args.ignore_context else context,
            unattended=args.unattended,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_trial_status(args: argparse.Namespace) -> int:
    _, db, _, _, trials, _, _, _ = continuous_stack(args.config)
    try:
        trial = db.get_trial(args.trial_id) if args.trial_id else trials.status()
        print(json.dumps(trial, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_trial_evaluate(args: argparse.Namespace) -> int:
    _, db, _, _, trials, _, _, _ = continuous_stack(args.config)
    try:
        result = trials.evaluate(args.trial_id)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_trial_rollback(args: argparse.Namespace) -> int:
    _, db, _, _, trials, _, _, _ = continuous_stack(args.config)
    try:
        result = trials.rollback(args.trial_id, reason=args.reason)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_trial_promote(args: argparse.Namespace) -> int:
    _, db, _, _, trials, _, _, _ = continuous_stack(args.config)
    try:
        result = trials.promote(args.trial_id, args.profile_id)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_feedback(args: argparse.Namespace) -> int:
    _, db, _, _, trials, _, _, _ = continuous_stack(args.config)
    try:
        feedback = {
            "trial_id": args.trial_id,
            "session_id": args.session_id,
            "decision": args.decision,
            "responsiveness": args.responsiveness,
            "stability": args.stability,
            "suspend_wake": args.suspend_wake,
            "notes": args.notes,
        }
        db.add_feedback(feedback)
        if args.decision == "rejected" and args.trial_id:
            trial = db.get_trial(args.trial_id)
            if trial:
                proposal = trial.get("proposal") or {}
                db.add_rejection(
                    trial.get("context_scene"),
                    trial.get("parameter"),
                    (proposal.get("change") or {}).get("to"),
                    args.notes or "human rejected",
                    "human_feedback",
                )
                active = trials.status()
                if active and active.get("trial_id") == args.trial_id:
                    feedback["rollback"] = trials.rollback(
                        args.trial_id, reason="human feedback rejected trial"
                    )
                for profile in db.profiles():
                    if (profile.get("evidence") or {}).get("source_trial") == args.trial_id:
                        db.set_profile_status(profile["profile_id"], "blocked")
                        feedback.setdefault("blocked_profiles", []).append(
                            profile["profile_id"]
                        )
        print(json.dumps(feedback, indent=2, ensure_ascii=False))
        return 0
    finally:
        db.close()


def cmd_knowledge_pack(args: argparse.Namespace) -> int:
    _, db, _, _, _, knowledge, _, _ = continuous_stack(args.config)
    try:
        pack = knowledge.build_pack(args.hours)
        text_out = json.dumps(pack, indent=2, ensure_ascii=False, default=str) + "\n"
        if args.output:
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text_out, encoding="utf-8")
            print(target)
        else:
            print(text_out, end="")
        return 0
    finally:
        db.close()


def cmd_knowledge_export(args: argparse.Namespace) -> int:
    _, db, _, _, _, knowledge, _, _ = continuous_stack(args.config)
    try:
        result = knowledge.export_git_knowledge()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    finally:
        db.close()


def cmd_actuator_inspect(args: argparse.Namespace) -> int:
    _, db, _, actuators, *_ = continuous_stack(args.config)
    try:
        print(json.dumps(actuators.inspect(), indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def cmd_hourly(args: argparse.Namespace) -> int:
    config, db, registry, actuators, trials, knowledge, policy, orchestrator = continuous_stack(
        args.config
    )
    try:
        drift = knowledge.detect_drift()
        evaluation = None
        active = trials.status()
        if active and active.get("state") in {"MEASURING", "REVALIDATION"}:
            try:
                evaluation = trials.evaluate(active["trial_id"])
            except TrialError as exc:
                evaluation = {"error": str(exc)}
        active = trials.status()
        if (
            active
            and active.get("state") == "CANDIDATE_WINNER"
            and bool(config.get("automation.auto_promote_profiles", False))
        ):
            promotion = trials.promote(active["trial_id"])
            if evaluation is None:
                evaluation = {"verdict": "CANDIDATE_WINNER"}
            evaluation["promotion"] = promotion
        emitted = orchestrator.emit_pack(
            Path(args.output) if args.output else None
        )
        knowledge_export = knowledge.export_git_knowledge()
        print(
            json.dumps(
                {
                    "run_id": emitted["run_id"],
                    "pack_file": emitted["output"],
                    "drift": drift,
                    "trial_evaluation": evaluation,
                    "knowledge_export": knowledge_export,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    finally:
        db.close()


def cmd_llm_apply(args: argparse.Namespace) -> int:
    _, db, _, _, _, _, _, orchestrator = continuous_stack(args.config)
    try:
        decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
        errors = validate_decision(decision)
        if errors:
            raise SystemExit("Decision validation failed:\n- " + "\n- ".join(errors))
        result = orchestrator.apply_decision(
            decision,
            run_id=args.run_id,
            current_context=current_context_from_sample(db.latest_sample()),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    except (json.JSONDecodeError, OrchestratorError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        db.close()


def cmd_task_run(args: argparse.Namespace) -> int:
    config, db, _, _, trials, _, policy, _ = continuous_stack(args.config)
    try:
        active = trials.status()
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise SystemExit("task-run requires a command after --")
        result = run_measured_task(
            db,
            scene=args.scene,
            label=args.label,
            command=command,
            max_gap_seconds=float(config.get("collector.max_gap_seconds", 45)),
            profile_id=(
                active.get("candidate_profile")
                if active and active.get("candidate_profile")
                else policy.current_profile()
            ),
            trial_id=active.get("trial_id") if active else None,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return int(result.get("exit_code") or 0)
    finally:
        db.close()


def cmd_root_helper(args: argparse.Namespace) -> int:
    server = RootHelperServer(Path(args.socket), args.allow_user)
    server.run()
    return 0


def cmd_config_get(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else None
    config = load_config(ROOT, path)
    value = config.get(args.key)
    if value is None:
        raise SystemExit(f"Unknown config key: {args.key}")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)
    return 0


def cmd_override(args: argparse.Namespace) -> int:
    config, db, registry, _, trials, _, policy, _ = continuous_stack(args.config)
    try:
        path = config.root / "runtime" / "manual-override"
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.override_command == "status":
            print(
                json.dumps(
                    {
                        "profile_id": policy.manual_override(),
                        "file": str(path),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.override_command == "clear":
            path.unlink(missing_ok=True)
            if trials.status():
                trials.rollback(reason="manual override cleared")
            result = policy.apply_profile_id(
                str(config.get("policy.safe_profile", "safe-baseline")),
                reason="manual override cleared",
                force=True,
            )
            db.add_system_event("manual_override_cleared", {"result": result})
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            return 0
        profile = registry.get(args.profile_id)
        if not profile:
            raise SystemExit(f"Profile not found: {args.profile_id}")
        if trials.status():
            trials.rollback(reason="manual profile override")
        result = policy.apply_profile_id(
            args.profile_id,
            reason="manual profile override",
            force=True,
        )
        path.write_text(args.profile_id + "\n", encoding="utf-8")
        db.add_system_event(
            "manual_override_set",
            {"profile_id": args.profile_id, "result": result},
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sp7-powerlab")
    sub = p.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="inspect SP7 detection, telemetry readiness, and installed tools")
    doctor.set_defaults(func=cmd_doctor)

    start = sub.add_parser("start", help="start a new experiment and snapshot its initial state")
    start.add_argument("--name", required=True)
    start.add_argument("--profile", default="unknown")
    start.add_argument("--workload", default="unknown")
    start.add_argument("--hypothesis", default="")
    start.add_argument("--notes", default="")
    start.add_argument(
        "--proposal",
        help="link this experiment to a stored proposal; settings are not applied automatically",
    )
    start.add_argument(
        "--config-file",
        action="append",
        default=[],
        help="copy a reviewed power/profile config into this experiment (repeatable)",
    )
    start.set_defaults(func=cmd_start)

    collect = sub.add_parser("collect", help="append battery/system telemetry to the active experiment")
    collect.add_argument("--interval", type=float, default=2.0)
    collect.add_argument("--duration", type=float, default=0.0, help="seconds; 0 means until Ctrl-C")
    collect.add_argument("--once", action="store_true")
    collect.set_defaults(func=cmd_collect)

    finish = sub.add_parser("finish", help="summarize, create AI context, and close the active experiment")
    finish.set_defaults(func=cmd_finish)

    listing = sub.add_parser("list", help="list indexed experiments")
    listing.set_defaults(func=cmd_list)

    compare = sub.add_parser("compare", help="compare average whole-device power between two experiments")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.set_defaults(func=cmd_compare)

    proposal_template_cmd = sub.add_parser(
        "proposal-template",
        help="emit an editable AI proposal template based on a completed experiment",
    )
    proposal_template_cmd.add_argument("experiment")
    proposal_template_cmd.add_argument("--output")
    proposal_template_cmd.set_defaults(func=cmd_proposal_template)

    proposal_add = sub.add_parser(
        "proposal-add",
        help="validate and store an AI/user tuning proposal",
    )
    proposal_add.add_argument("file")
    proposal_add.set_defaults(func=cmd_proposal_add)

    proposal_list = sub.add_parser("proposal-list", help="list stored tuning proposals")
    proposal_list.set_defaults(func=cmd_proposal_list)

    proposal_show = sub.add_parser("proposal-show", help="show a stored tuning proposal")
    proposal_show.add_argument("proposal")
    proposal_show.set_defaults(func=cmd_proposal_show)

    history_list = sub.add_parser("history-list", help="list portable Git-trackable experiment summaries")
    history_list.set_defaults(func=cmd_history_list)

    history_show = sub.add_parser("history-show", help="show one portable experiment history record")
    history_show.add_argument("experiment")
    history_show.set_defaults(func=cmd_history_show)

    decision = sub.add_parser(
        "decision",
        help="attach human usability/stability feedback and accept/reject status to an archived experiment",
    )
    decision.add_argument("experiment")
    decision.add_argument("status", choices=["accepted", "rejected", "inconclusive"])
    decision.add_argument("--responsiveness", type=int)
    decision.add_argument(
        "--stability",
        choices=["good", "degraded", "bad", "unknown"],
        default="unknown",
    )
    decision.add_argument(
        "--suspend-wake",
        choices=["good", "degraded", "bad", "untested"],
        default="untested",
    )
    decision.add_argument("--notes", default="")
    decision.set_defaults(func=cmd_decision)

    ai_pack = sub.add_parser(
        "ai-pack",
        help="build a compact history + feedback context for the next AI tuning proposal",
    )
    ai_pack.add_argument("--limit", type=int, default=12)
    ai_pack.add_argument("--workload")
    ai_pack.add_argument("--output")
    ai_pack.set_defaults(func=cmd_ai_pack)

    service_run = sub.add_parser("service-run", help="run the continuous collector/policy service")
    service_run.add_argument("--config")
    service_run.set_defaults(func=cmd_service_run)

    service_status = sub.add_parser("service-status", help="show continuous PowerLab health")
    service_status.add_argument("--config")
    service_status.set_defaults(func=cmd_service_status)

    collect_once = sub.add_parser("collect-once", help="collect one continuous-mode sample")
    collect_once.add_argument("--config")
    collect_once.add_argument("--apply-policy", action="store_true")
    collect_once.set_defaults(func=cmd_collect_once_continuous)

    benchmark = sub.add_parser("collector-benchmark", help="measure collector overhead")
    benchmark.add_argument("--config")
    benchmark.add_argument("--samples", type=int, default=12)
    benchmark.set_defaults(func=cmd_collector_benchmark)

    observe = sub.add_parser("observe", help="summarize recent real usage")
    observe.add_argument("--config")
    observe.add_argument("--hours", type=float, default=1.0)
    observe.set_defaults(func=cmd_observe)

    contexts = sub.add_parser("contexts", help="show context/session history")
    contexts.add_argument("--config")
    contexts.add_argument("--hours", type=float, default=24.0)
    contexts.add_argument("--scene")
    contexts.set_defaults(func=cmd_contexts_continuous)

    current = sub.add_parser("current", help="show latest sample, trial and actuator state")
    current.add_argument("--config")
    current.set_defaults(func=cmd_current_continuous)

    profile = sub.add_parser("profile", help="manage verified/experimental profiles")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_list_cmd = profile_sub.add_parser("list")
    profile_list_cmd.add_argument("--config")
    profile_list_cmd.set_defaults(func=cmd_profile_list)
    profile_inspect_cmd = profile_sub.add_parser("inspect")
    profile_inspect_cmd.add_argument("profile_id")
    profile_inspect_cmd.add_argument("--config")
    profile_inspect_cmd.set_defaults(func=cmd_profile_inspect)
    profile_apply_cmd = profile_sub.add_parser("apply")
    profile_apply_cmd.add_argument("profile_id")
    profile_apply_cmd.add_argument("--config")
    profile_apply_cmd.add_argument("--reason", default="")
    profile_apply_cmd.set_defaults(func=cmd_profile_apply)
    profile_status_cmd = profile_sub.add_parser("status")
    profile_status_cmd.add_argument("profile_id")
    profile_status_cmd.add_argument(
        "status",
        choices=["experimental", "verified", "needs_revalidation", "deprecated", "blocked"],
    )
    profile_status_cmd.add_argument("--scene")
    profile_status_cmd.add_argument("--note", default="")
    profile_status_cmd.add_argument("--config")
    profile_status_cmd.set_defaults(func=cmd_profile_status)

    trial = sub.add_parser("trial", help="manage bounded tuning trials")
    trial_sub = trial.add_subparsers(dest="trial_command", required=True)
    trial_start = trial_sub.add_parser("start")
    trial_start.add_argument("proposal")
    trial_start.add_argument("--config")
    trial_start.add_argument("--unattended", action="store_true")
    trial_start.add_argument("--ignore-context", action="store_true")
    trial_start.set_defaults(func=cmd_trial_start)
    trial_status = trial_sub.add_parser("status")
    trial_status.add_argument("trial_id", nargs="?")
    trial_status.add_argument("--config")
    trial_status.set_defaults(func=cmd_trial_status)
    trial_evaluate = trial_sub.add_parser("evaluate")
    trial_evaluate.add_argument("trial_id", nargs="?")
    trial_evaluate.add_argument("--config")
    trial_evaluate.set_defaults(func=cmd_trial_evaluate)
    trial_rollback = trial_sub.add_parser("rollback")
    trial_rollback.add_argument("trial_id", nargs="?")
    trial_rollback.add_argument("--reason", default="manual rollback")
    trial_rollback.add_argument("--config")
    trial_rollback.set_defaults(func=cmd_trial_rollback)
    trial_promote = trial_sub.add_parser("promote")
    trial_promote.add_argument("trial_id")
    trial_promote.add_argument("--profile-id")
    trial_promote.add_argument("--config")
    trial_promote.set_defaults(func=cmd_trial_promote)

    feedback = sub.add_parser("feedback", help="record human trial/session feedback")
    feedback.add_argument("decision", choices=["accepted", "rejected", "inconclusive"])
    feedback.add_argument("--trial-id")
    feedback.add_argument("--session-id")
    feedback.add_argument("--responsiveness", type=int)
    feedback.add_argument(
        "--stability", choices=["good", "degraded", "bad", "unknown"], default="unknown"
    )
    feedback.add_argument(
        "--suspend-wake", choices=["good", "degraded", "bad", "untested"], default="untested"
    )
    feedback.add_argument("--notes", default="")
    feedback.add_argument("--config")
    feedback.set_defaults(func=cmd_feedback)

    knowledge_pack = sub.add_parser("knowledge-pack", help="build the hourly LLM context pack")
    knowledge_pack.add_argument("--config")
    knowledge_pack.add_argument("--hours", type=float)
    knowledge_pack.add_argument("--output")
    knowledge_pack.set_defaults(func=cmd_knowledge_pack)

    knowledge_export = sub.add_parser(
        "knowledge-export",
        help="export compact continuous learning history into Git-trackable files",
    )
    knowledge_export.add_argument("--config")
    knowledge_export.set_defaults(func=cmd_knowledge_export)

    actuator = sub.add_parser("actuator-inspect", help="inspect available power backends")
    actuator.add_argument("--config")
    actuator.set_defaults(func=cmd_actuator_inspect)

    hourly = sub.add_parser("hourly", help="evaluate current trial and emit the next MCP/LLM pack")
    hourly.add_argument("--config")
    hourly.add_argument("--output")
    hourly.set_defaults(func=cmd_hourly)

    llm_apply = sub.add_parser("llm-apply", help="validate and apply one structured LLM decision")
    llm_apply.add_argument("decision")
    llm_apply.add_argument("--run-id")
    llm_apply.add_argument("--config")
    llm_apply.set_defaults(func=cmd_llm_apply)

    task_run = sub.add_parser(
        "task-run",
        help="run a fixed-workload command and measure total battery energy/time",
    )
    task_run.add_argument("--scene", required=True)
    task_run.add_argument("--label", required=True)
    task_run.add_argument("--config")
    task_run.add_argument("command", nargs=argparse.REMAINDER)
    task_run.set_defaults(func=cmd_task_run)

    root_helper = sub.add_parser(
        "root-helper",
        help="run the privileged allowlisted parameter helper (normally via systemd)",
    )
    root_helper.add_argument("--socket", default="/run/sp7-powerlab/helper.sock")
    root_helper.add_argument("--allow-user", required=True)
    root_helper.set_defaults(func=cmd_root_helper)

    config_get = sub.add_parser(
        "config-get",
        help="print one resolved PowerLab configuration value",
    )
    config_get.add_argument("key")
    config_get.add_argument("--config")
    config_get.set_defaults(func=cmd_config_get)

    override = sub.add_parser("override", help="set/clear a manual profile override")
    override_sub = override.add_subparsers(dest="override_command", required=True)
    override_set = override_sub.add_parser("set")
    override_set.add_argument("profile_id")
    override_set.add_argument("--config")
    override_set.set_defaults(func=cmd_override)
    override_clear = override_sub.add_parser("clear")
    override_clear.add_argument("--config")
    override_clear.set_defaults(func=cmd_override)
    override_status = override_sub.add_parser("status")
    override_status.add_argument("--config")
    override_status.set_defaults(func=cmd_override)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
