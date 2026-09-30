from __future__ import annotations

import math
from typing import Any


def integrate_energy(
    samples: list[dict[str, Any]],
    max_gap_seconds: float = 45.0,
    require_discharging: bool = True,
) -> dict[str, Any]:
    ordered = sorted(samples, key=lambda s: float(s.get("ts", 0.0)))
    energy_wh = 0.0
    valid_s = 0.0
    valid_segments = 0
    gaps = 0
    invalid_status = 0
    missing_power = 0

    for left, right in zip(ordered, ordered[1:]):
        try:
            t0, t1 = float(left["ts"]), float(right["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        dt = t1 - t0
        if dt <= 0:
            continue
        if dt > max_gap_seconds:
            gaps += 1
            continue
        if require_discharging and (
            left.get("battery_status") != "Discharging"
            or right.get("battery_status") != "Discharging"
        ):
            invalid_status += 1
            continue
        p0, p1 = left.get("power_w"), right.get("power_w")
        if not isinstance(p0, (int, float)) or not isinstance(p1, (int, float)):
            missing_power += 1
            continue
        if not math.isfinite(float(p0)) or not math.isfinite(float(p1)):
            missing_power += 1
            continue
        power = (float(p0) + float(p1)) / 2.0
        if power < 0:
            missing_power += 1
            continue
        energy_wh += power * dt / 3600.0
        valid_s += dt
        valid_segments += 1

    avg_power = energy_wh / (valid_s / 3600.0) if valid_s > 0 else None
    return {
        "energy_wh": energy_wh if valid_s > 0 else None,
        "average_power_w": avg_power,
        "valid_duration_s": valid_s,
        "valid_segments": valid_segments,
        "gaps": gaps,
        "invalid_status_segments": invalid_status,
        "missing_power_segments": missing_power,
        "sample_count": len(ordered),
    }


def data_quality(
    samples: list[dict[str, Any]],
    *,
    max_gap_seconds: float = 45.0,
    min_samples: int = 3,
    min_valid_seconds: float = 300.0,
    require_single_scene: bool = True,
) -> dict[str, Any]:
    integration = integrate_energy(samples, max_gap_seconds=max_gap_seconds)
    scenes = {s.get("context_scene") for s in samples if s.get("context_scene")}
    reasons: list[str] = []
    if len(samples) < min_samples:
        reasons.append("too_few_samples")
    if integration["valid_duration_s"] < min_valid_seconds:
        reasons.append("insufficient_valid_duration")
    if integration["average_power_w"] is None:
        reasons.append("whole_device_power_unavailable")
    if require_single_scene and len(scenes) > 1:
        reasons.append("mixed_scene")
    if integration["invalid_status_segments"]:
        reasons.append("contains_non_discharging_segments")
    quality_score = 1.0
    quality_score -= min(0.35, 0.08 * integration["gaps"])
    quality_score -= min(0.35, 0.08 * integration["missing_power_segments"])
    if "mixed_scene" in reasons:
        quality_score -= 0.2
    if "insufficient_valid_duration" in reasons:
        quality_score -= 0.3
    quality_score = max(0.0, min(1.0, quality_score))
    return {
        "valid": not reasons,
        "reasons": reasons,
        "score": quality_score,
        "scenes": sorted(scenes),
        **integration,
    }


def comparability_score(
    a: dict[str, Any],
    b: dict[str, Any],
) -> dict[str, Any]:
    weights = {
        "scene": 0.30,
        "brightness": 0.15,
        "kernel": 0.10,
        "app_version": 0.10,
        "battery_health": 0.10,
        "network_intensity": 0.10,
        "background_load": 0.10,
        "thermal": 0.05,
    }
    details: dict[str, float] = {}

    details["scene"] = 1.0 if a.get("scene") == b.get("scene") and a.get("scene") else 0.0

    def near(key: str, tolerance: float) -> float:
        av, bv = a.get(key), b.get(key)
        if not isinstance(av, (int, float)) or not isinstance(bv, (int, float)):
            return 0.5
        delta = abs(float(av) - float(bv))
        return max(0.0, 1.0 - delta / tolerance)

    details["brightness"] = near("brightness_pct", 15.0)
    details["battery_health"] = near("battery_health_pct", 8.0)
    details["network_intensity"] = near("network_mbps", 10.0)
    details["background_load"] = near("background_cpu_pct", 40.0)
    details["thermal"] = near("temperature_c", 15.0)
    details["kernel"] = 1.0 if a.get("kernel") == b.get("kernel") and a.get("kernel") else 0.4
    details["app_version"] = (
        1.0 if a.get("app_major_version") == b.get("app_major_version") and a.get("app_major_version")
        else 0.5
    )
    score = sum(details[key] * weight for key, weight in weights.items())
    return {"score": round(score, 4), "details": details, "comparable": score >= 0.72}
