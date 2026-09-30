from __future__ import annotations

import statistics
from typing import Any


def percentile(values: list[float], p: float) -> float | None:
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


def valid_duration(rows: list[dict[str, Any]], max_gap_seconds: float) -> float:
    if len(rows) < 2:
        return 0.0
    total = 0.0
    for previous, current in zip(rows, rows[1:], strict=False):
        dt = float(current["ts"]) - float(previous["ts"])
        if 0 < dt <= max_gap_seconds:
            total += dt
    return total


def time_weighted_average(
    rows: list[dict[str, Any]],
    key: str,
    *,
    max_gap_seconds: float,
) -> float | None:
    weighted = 0.0
    seconds = 0.0
    for previous, current in zip(rows, rows[1:], strict=False):
        dt = float(current["ts"]) - float(previous["ts"])
        if not (0 < dt <= max_gap_seconds):
            continue
        left = previous.get(key)
        right = current.get(key)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        weighted += (float(left) + float(right)) * 0.5 * dt
        seconds += dt
    return weighted / seconds if seconds > 0 else None


def summarize_block(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float = 45.0,
) -> dict[str, Any]:
    valid_rows = [
        row
        for row in rows
        if row.get("battery_status") == "Discharging"
        and isinstance(row.get("battery_power_w"), (int, float))
        and not row.get("resume_grace")
    ]
    powers = [float(row["battery_power_w"]) for row in valid_rows]
    thermal = [
        float(row["thermal_pressure"])
        for row in valid_rows
        if isinstance(row.get("thermal_pressure"), (int, float))
    ]
    media = [
        1.0 if row.get("media_playing") else 0.0 for row in valid_rows if "media_playing" in row
    ]
    brightness = [
        float(row["brightness_pct"])
        for row in valid_rows
        if isinstance(row.get("brightness_pct"), (int, float))
    ]
    duration = valid_duration(valid_rows, max_gap_seconds)
    return {
        "sample_count": len(valid_rows),
        "valid_seconds": duration,
        "avg_power_w": time_weighted_average(
            valid_rows, "battery_power_w", max_gap_seconds=max_gap_seconds
        ),
        "median_power_w": percentile(powers, 0.5),
        "p90_power_w": percentile(powers, 0.9),
        "p95_power_w": percentile(powers, 0.95),
        "avg_cpu_psi": time_weighted_average(
            valid_rows, "cpu_psi", max_gap_seconds=max_gap_seconds
        ),
        "avg_io_psi": time_weighted_average(valid_rows, "io_psi", max_gap_seconds=max_gap_seconds),
        "max_thermal_pressure": max(thermal) if thermal else None,
        "media_playing_fraction": (
            time_weighted_average(
                [
                    {**row, "_media_numeric": 1.0 if row.get("media_playing") else 0.0}
                    for row in valid_rows
                ],
                "_media_numeric",
                max_gap_seconds=max_gap_seconds,
            )
            if media
            else None
        ),
        "brightness_median": percentile(brightness, 0.5),
        "demand_regions": sorted(
            {str(row.get("demand_region")) for row in valid_rows if row.get("demand_region")}
        ),
    }


def block_is_comparable(
    summary: dict[str, Any],
    *,
    min_seconds: float,
    max_brightness_delta: float | None = None,
    reference_brightness: float | None = None,
    media_required: bool = False,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if float(summary.get("valid_seconds") or 0.0) < min_seconds:
        reasons.append("insufficient_valid_duration")
    if summary.get("avg_power_w") is None:
        reasons.append("missing_battery_power")
    if (
        reference_brightness is not None
        and max_brightness_delta is not None
        and summary.get("brightness_median") is not None
        and abs(float(summary["brightness_median"]) - reference_brightness) > max_brightness_delta
    ):
        reasons.append("brightness_not_comparable")
    if media_required:
        fraction = summary.get("media_playing_fraction")
        if fraction is None or float(fraction) < 0.90:
            reasons.append("media_continuity_failed")
    return not reasons, reasons


def compare_candidate(
    baseline_blocks: list[dict[str, Any]],
    candidate_blocks: list[dict[str, Any]],
    *,
    min_power_saving_w: float = 0.10,
    max_cpu_psi_delta: float = 2.0,
    max_io_psi_delta: float = 2.0,
    max_thermal_pressure_delta: float = 0.10,
) -> dict[str, Any]:
    if not baseline_blocks or not candidate_blocks:
        return {"verdict": "INSUFFICIENT_DATA", "reasons": ["missing_blocks"]}

    def means(key: str, blocks: list[dict[str, Any]]) -> float | None:
        values = [float(block[key]) for block in blocks if isinstance(block.get(key), (int, float))]
        return statistics.fmean(values) if values else None

    base_power = means("avg_power_w", baseline_blocks)
    cand_power = means("avg_power_w", candidate_blocks)
    if base_power is None or cand_power is None:
        return {"verdict": "INSUFFICIENT_DATA", "reasons": ["missing_power"]}

    cpu_base = means("avg_cpu_psi", baseline_blocks)
    cpu_cand = means("avg_cpu_psi", candidate_blocks)
    io_base = means("avg_io_psi", baseline_blocks)
    io_cand = means("avg_io_psi", candidate_blocks)
    thermal_base = means("max_thermal_pressure", baseline_blocks)
    thermal_cand = means("max_thermal_pressure", candidate_blocks)

    power_delta = cand_power - base_power
    reasons: list[str] = []
    if power_delta > -abs(min_power_saving_w):
        reasons.append("power_saving_too_small")
    if cpu_base is not None and cpu_cand is not None and cpu_cand - cpu_base > max_cpu_psi_delta:
        reasons.append("cpu_psi_regression")
    if io_base is not None and io_cand is not None and io_cand - io_base > max_io_psi_delta:
        reasons.append("io_psi_regression")
    if (
        thermal_base is not None
        and thermal_cand is not None
        and thermal_cand - thermal_base > max_thermal_pressure_delta
    ):
        reasons.append("thermal_regression")

    return {
        "verdict": "CANDIDATE_WINNER" if not reasons else "REJECT",
        "reasons": reasons,
        "baseline_avg_power_w": base_power,
        "candidate_avg_power_w": cand_power,
        "power_delta_w": power_delta,
        "power_delta_percent": (power_delta / base_power * 100.0) if base_power else None,
        "cpu_psi_delta": (
            cpu_cand - cpu_base if cpu_base is not None and cpu_cand is not None else None
        ),
        "io_psi_delta": (
            io_cand - io_base if io_base is not None and io_cand is not None else None
        ),
        "thermal_pressure_delta": (
            thermal_cand - thermal_base
            if thermal_base is not None and thermal_cand is not None
            else None
        ),
    }
