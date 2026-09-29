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

    decisive = [value for value in checks.values() if value is not None]
    if not decisive:
        criteria = "insufficient-data"
    elif all(decisive):
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

    with (exp_dir / "telemetry.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = parse_time(row.get("timestamp"))
            if ts is not None:
                timestamps.append(ts)
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
            "average": statistics.fmean(powers) if powers else None,
            "median": statistics.median(powers) if powers else None,
            "p95": percentile95(powers),
            "minimum": min(powers) if powers else None,
            "maximum": max(powers) if powers else None,
        },
        "conditions": {
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
        _, proposal = load_proposal(proposal_id)
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

    return p


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
