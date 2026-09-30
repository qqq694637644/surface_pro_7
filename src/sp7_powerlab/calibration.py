from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import Config, load_machine, machine_path
from .storage import Database

PHASES = ("cold_idle", "normal_interactive", "media", "bounded_burst")
REQUIRED_PHASES = set(PHASES)


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


def _numbers(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _list_strings(values: list[str]) -> str:
    return "[" + ", ".join(_quote(v) for v in values) + "]"


def write_machine(root: Path, machine: dict[str, Any]) -> None:
    identity = machine.get("identity") or {}
    calibration = machine.get("calibration") or {}
    battery = machine.get("battery") or {}
    baselines = machine.get("baselines") or {}
    thermal = machine.get("thermal") or {}

    lines = [
        "[identity]",
        f"expected_product = {_quote(str(identity.get('expected_product', 'Surface Pro 7')))}",
        f"expected_cpu_substring = {_quote(str(identity.get('expected_cpu_substring', 'i5-1035G4')))}",
        "",
        "[calibration]",
        f"valid = {'true' if calibration.get('valid') else 'false'}",
        f"version = {int(calibration.get('version') or 0)}",
        f"completed_phases = {_list_strings(list(calibration.get('completed_phases') or []))}",
        f"invalid_reason = {_quote(str(calibration.get('invalid_reason') or ''))}",
        "",
        "[battery]",
        f"active_epoch = {int(battery.get('active_epoch') or 0)}",
        "",
        "[baselines]",
    ]
    for key in (
        "idle_battery_w",
        "interactive_battery_w",
        "media_battery_w",
        "idle_temp_c",
        "interactive_temp_p90_c",
        "normal_rapl_p90_w",
    ):
        lines.append(f"{key} = {float(baselines.get(key) or 0.0):.6f}")
    lines.extend(["", "[thermal]"])
    for key in (
        "soft_temp_c",
        "pressure_temp_c",
        "slope_reference_c_per_min",
        "rapl_reference_w",
        "cooldown_reference_c_per_min",
    ):
        lines.append(f"{key} = {float(thermal.get(key) or 0.0):.6f}")
    machine_path(root).write_text("\n".join(lines) + "\n", encoding="utf-8")


class CalibrationManager:
    def __init__(self, root: Path, db: Database, config: Config | None = None):
        self.root = root
        self.db = db
        self.config = config

    def status(self) -> dict[str, Any]:
        machine = load_machine(self.root)
        return {
            "machine": machine,
            "active": self.db.active_calibration(),
            "completed": self.db.calibration_results(),
        }

    def start(self, phase: str) -> dict[str, Any]:
        if phase not in PHASES:
            raise ValueError(f"unsupported calibration phase: {phase}")
        machine = load_machine(self.root)
        completed = set((machine.get("calibration") or {}).get("completed_phases") or [])
        index = PHASES.index(phase)
        missing = sorted(set(PHASES[:index]) - completed)
        if missing:
            raise RuntimeError(
                f"calibration phase {phase} requires previous phases: {', '.join(missing)}"
            )
        return self.db.start_calibration(phase)

    def finish(self) -> dict[str, Any]:
        active = self.db.active_calibration()
        if not active:
            raise RuntimeError("no calibration is running")

        rows = self.db.samples_between(active["start_ts"], time.time())
        if len(rows) < 3:
            raise RuntimeError("not enough telemetry samples to finish calibration")

        phase = active["phase"]
        min_seconds = 0.0
        if self.config is not None:
            min_seconds = float(self.config.get(f"calibration.{phase}_min_seconds", 0.0))
        elapsed = time.time() - float(active["start_ts"])
        if elapsed < min_seconds:
            raise RuntimeError(
                f"calibration phase {phase} requires at least {min_seconds:.0f}s; "
                f"only {elapsed:.0f}s elapsed"
            )
        powers = _numbers(rows, "battery_power_w")
        temps = _numbers(rows, "package_temp_c")
        rapl = _numbers(rows, "rapl_power_60s_w")
        slopes = _numbers(rows, "temp_slope_c_per_min")
        epochs = {
            int(row["battery_epoch"])
            for row in rows
            if isinstance(row.get("battery_epoch"), (int, float))
        }
        if len(epochs) > 1:
            raise RuntimeError("battery epoch changed during calibration")
        if len(powers) < 3 or len(temps) < 3 or len(rapl) < 3:
            raise RuntimeError("calibration requires stable BAT, thermal, and RAPL telemetry")
        valid_discharge = [row for row in rows if row.get("battery_status") == "Discharging"]
        if phase != "bounded_burst" and len(valid_discharge) < 3:
            raise RuntimeError("calibration requires battery Discharging samples")
        active_fraction = sum(bool(row.get("user_active")) for row in rows) / len(rows)
        media_fraction = sum(bool(row.get("media_playing")) for row in rows) / len(rows)
        if phase == "cold_idle" and active_fraction > 0.20:
            raise RuntimeError("cold_idle calibration requires the user to remain idle")
        if phase == "normal_interactive" and active_fraction < 0.80:
            raise RuntimeError("normal_interactive calibration requires active use")
        if phase == "media" and media_fraction < 0.80:
            raise RuntimeError("media calibration requires continuous media playback")
        if phase == "bounded_burst":
            machine = load_machine(self.root)
            normal_rapl = float((machine.get("baselines") or {}).get("normal_rapl_p90_w") or 0.0)
            rapl_p90 = float(_percentile(rapl, 0.9) or 0.0)
            slope_p90 = float(_percentile(slopes, 0.9) or 0.0)
            if rapl_p90 < normal_rapl + 0.5 and slope_p90 < 0.5:
                raise RuntimeError(
                    "bounded_burst did not create a measurable thermal/power response"
                )

        result = {
            "phase": phase,
            "sample_count": len(rows),
            "start_ts": active["start_ts"],
            "end_ts": time.time(),
            "battery_power_median_w": _percentile(powers, 0.5),
            "battery_power_p90_w": _percentile(powers, 0.9),
            "temp_median_c": _percentile(temps, 0.5),
            "temp_p90_c": _percentile(temps, 0.9),
            "temp_max_c": max(temps) if temps else None,
            "rapl_median_w": _percentile(rapl, 0.5),
            "rapl_p90_w": _percentile(rapl, 0.9),
            "slope_p90_c_per_min": _percentile(slopes, 0.9),
            "slope_min_c_per_min": min(slopes) if slopes else None,
        }
        self.db.finish_calibration(active["run_id"], result)

        machine = load_machine(self.root)
        calibration = machine.setdefault("calibration", {})
        completed = set(calibration.get("completed_phases") or [])
        completed.add(phase)
        calibration["completed_phases"] = sorted(completed)
        baselines = machine.setdefault("baselines", {})
        thermal = machine.setdefault("thermal", {})

        if phase == "cold_idle":
            baselines["idle_battery_w"] = result["battery_power_median_w"] or 0.0
            baselines["idle_temp_c"] = result["temp_median_c"] or 0.0
            if result["slope_min_c_per_min"] is not None:
                thermal["cooldown_reference_c_per_min"] = abs(
                    min(0.0, float(result["slope_min_c_per_min"]))
                )
        elif phase == "normal_interactive":
            baselines["interactive_battery_w"] = result["battery_power_median_w"] or 0.0
            baselines["interactive_temp_p90_c"] = result["temp_p90_c"] or 0.0
            baselines["normal_rapl_p90_w"] = result["rapl_p90_w"] or 0.0
        elif phase == "media":
            baselines["media_battery_w"] = result["battery_power_median_w"] or 0.0
        elif phase == "bounded_burst":
            normal_temp = float(baselines.get("interactive_temp_p90_c") or 0.0)
            burst_p90 = float(result["temp_p90_c"] or normal_temp)
            burst_max = float(result["temp_max_c"] or burst_p90)
            thermal["soft_temp_c"] = max(normal_temp + 3.0, burst_p90)
            thermal["pressure_temp_c"] = max(
                thermal["soft_temp_c"] + 3.0,
                burst_max,
            )
            thermal["slope_reference_c_per_min"] = max(
                0.5, float(result["slope_p90_c_per_min"] or 0.0)
            )
            thermal["rapl_reference_w"] = max(
                float(baselines.get("normal_rapl_p90_w") or 0.0) + 0.5,
                float(result["rapl_p90_w"] or 0.0),
            )

        if REQUIRED_PHASES.issubset(completed):
            calibration["valid"] = True
            calibration["version"] = int(calibration.get("version") or 0) + 1
            calibration["invalid_reason"] = ""
        else:
            calibration["valid"] = False

        write_machine(self.root, machine)
        return {"result": result, "machine": machine}

    def set_active_battery_epoch(self, epoch: int) -> None:
        machine = load_machine(self.root)
        machine.setdefault("battery", {})["active_epoch"] = int(epoch)
        write_machine(self.root, machine)

    def invalidate(self, reason: str) -> dict[str, Any]:
        machine = load_machine(self.root)
        calibration = machine.setdefault("calibration", {})
        calibration["valid"] = False
        calibration["completed_phases"] = []
        calibration["invalid_reason"] = reason
        write_machine(self.root, machine)
        return machine


def calibration_safety_violation(
    sample: dict[str, Any],
    thermal_config: dict[str, Any],
    active_calibration: dict[str, Any] | None,
) -> str | None:
    if not active_calibration or active_calibration.get("phase") != "bounded_burst":
        return None
    bootstrap = thermal_config.get("bootstrap") or {}
    abort_temp = float(bootstrap.get("abort_temp_c", 80.0))
    max_seconds = float(bootstrap.get("max_burst_seconds", 90.0))
    temp = sample.get("package_temp_c")
    if isinstance(temp, (int, float)) and float(temp) >= abort_temp:
        return f"bounded burst reached bootstrap abort temperature {temp:.1f}C"
    elapsed = float(sample["ts"]) - float(active_calibration["start_ts"])
    if elapsed >= max_seconds:
        return f"bounded burst reached maximum duration {elapsed:.0f}s"
    if sample.get("throttle_delta") or sample.get("frequency_collapse"):
        return "bounded burst detected throttling"
    return None
