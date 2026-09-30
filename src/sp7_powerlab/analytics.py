from __future__ import annotations

import statistics
from typing import Any

from .evaluation import percentile, time_weighted_average, valid_duration


def _linear_slope_per_hour(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return None
    slope_per_second = (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False)) / denominator
    )
    return slope_per_second * 3600.0


def battery_usage_summary(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float = 45.0,
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda item: float(item["ts"]))
    discharge = [
        row
        for row in rows
        if row.get("battery_status") == "Discharging"
        and isinstance(row.get("battery_power_w"), (int, float))
        and not row.get("resume_grace")
    ]
    powers = [float(row["battery_power_w"]) for row in discharge]
    valid_seconds = valid_duration(discharge, max_gap_seconds)
    avg_power = time_weighted_average(
        discharge,
        "battery_power_w",
        max_gap_seconds=max_gap_seconds,
    )

    energy_used_wh = 0.0
    active_energy_wh = 0.0
    active_seconds = 0.0
    for previous, current in zip(discharge, discharge[1:], strict=False):
        dt = float(current["ts"]) - float(previous["ts"])
        if not (0 < dt <= max_gap_seconds):
            continue
        p0 = float(previous["battery_power_w"])
        p1 = float(current["battery_power_w"])
        interval_wh = (p0 + p1) * 0.5 * dt / 3600.0
        energy_used_wh += interval_wh
        if previous.get("user_active") and current.get("user_active"):
            active_energy_wh += interval_wh
            active_seconds += dt

    energy_points = [
        (float(row["ts"]), float(row["battery_energy_wh"]))
        for row in discharge
        if isinstance(row.get("battery_energy_wh"), (int, float))
    ]
    percent_points = [
        (float(row["ts"]), float(row["battery_pct"]))
        for row in discharge
        if isinstance(row.get("battery_pct"), (int, float))
    ]
    energy_slope = _linear_slope_per_hour(energy_points)
    percent_slope = _linear_slope_per_hour(percent_points)

    latest = discharge[-1] if discharge else (rows[-1] if rows else None)
    remaining_energy = (
        float(latest["battery_energy_wh"])
        if latest and isinstance(latest.get("battery_energy_wh"), (int, float))
        else None
    )
    battery = (latest or {}).get("battery") or {}
    full_energy = battery.get("energy_full_wh")
    if not isinstance(full_energy, (int, float)):
        full_energy = None

    return {
        "sample_count": len(discharge),
        "valid_discharge_seconds": valid_seconds,
        "avg_power_w": avg_power,
        "median_power_w": percentile(powers, 0.50),
        "p90_power_w": percentile(powers, 0.90),
        "p95_power_w": percentile(powers, 0.95),
        "energy_used_wh": energy_used_wh,
        "active_seconds": active_seconds,
        "active_energy_used_wh": active_energy_wh,
        "wh_per_active_hour": (
            active_energy_wh / (active_seconds / 3600.0) if active_seconds > 0 else None
        ),
        "energy_slope_wh_per_hour": energy_slope,
        "drain_w_from_energy_slope": (
            -energy_slope if energy_slope is not None and energy_slope < 0 else None
        ),
        "percent_slope_per_hour": percent_slope,
        "remaining_energy_wh": remaining_energy,
        "projected_remaining_hours": (
            remaining_energy / avg_power
            if remaining_energy is not None and avg_power and avg_power > 0
            else None
        ),
        "projected_full_hours": (
            float(full_energy) / avg_power
            if full_energy is not None and avg_power and avg_power > 0
            else None
        ),
    }
