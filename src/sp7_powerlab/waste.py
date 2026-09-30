from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .storage import Database


def brightness_bucket(value: float | None) -> int:
    if value is None:
        return -1
    return int(max(0, min(100, value)) // 10 * 10)


def remote_bucket(remote_hint: float | None) -> int:
    return 1 if float(remote_hint or 0.0) >= 0.5 else 0


@dataclass
class WasteDetector:
    config: Config
    db: Database

    def detect(self, rollup: dict[str, Any]) -> dict[str, Any] | None:
        if rollup.get("avg_power_w") is None:
            return None
        if rollup.get("local_compute_pressure") not in {None, "LOW"}:
            return None
        if rollup.get("thermal_start") in {"THERMAL_PRESSURE", "THROTTLING"}:
            return None

        min_baselines = int(self.config.get("controller.waste_min_baselines", 8))
        baselines = self.db.comparable_rollups(
            battery_epoch=int(rollup.get("battery_epoch") or 0),
            brightness_bucket=int(rollup.get("brightness_bucket") or -1),
            demand_region=str(rollup.get("demand_region") or "unknown"),
            media_playing=bool(rollup.get("media_playing")),
            remote_bucket=int(rollup.get("remote_bucket") or 0),
            system_fingerprint=rollup.get("system_fingerprint"),
            since_ts=float(rollup["bucket_ts"]) - 30 * 86400,
            limit=200,
        )
        all_comparable = [
            item
            for item in baselines
            if item.get("bucket_ts") != rollup.get("bucket_ts")
            and isinstance(item.get("avg_power_w"), (int, float))
        ]
        current_ts = float(rollup["bucket_ts"])
        window_seconds = max(
            60.0,
            float(self.config.get("controller.waste_window_minutes", 5)) * 60.0,
        )
        baseline_cutoff = current_ts - window_seconds
        baselines = [
            item for item in all_comparable if float(item.get("bucket_ts") or 0.0) < baseline_cutoff
        ]
        if len(baselines) < min_baselines:
            return None

        powers = sorted(float(item["avg_power_w"]) for item in baselines)
        p90 = powers[min(len(powers) - 1, int((len(powers) - 1) * 0.90))]
        relative = float(self.config.get("controller.waste_relative_threshold", 0.20))
        absolute = float(self.config.get("controller.waste_absolute_threshold_w", 0.8))
        threshold = max(p90 * (1.0 + relative), p90 + absolute)
        current = float(rollup["avg_power_w"])
        if current <= threshold:
            return None

        recent = [
            item
            for item in all_comparable
            if float(item.get("bucket_ts") or 0.0) >= current_ts - window_seconds
        ] + [rollup]
        recent = sorted(recent, key=lambda item: float(item["bucket_ts"]))
        valid_seconds = sum(float(item.get("valid_seconds") or 0.0) for item in recent)
        high_seconds = sum(
            float(item.get("valid_seconds") or 0.0)
            for item in recent
            if float(item.get("avg_power_w") or 0.0) > threshold
        )
        if valid_seconds < window_seconds * 0.8:
            return None
        if high_seconds / max(valid_seconds, 1.0) < 0.8:
            return None

        severity = "high" if current >= threshold + 1.5 else "medium"
        return {
            "start_ts": float(rollup["bucket_ts"]),
            "end_ts": float(rollup["bucket_ts"])
            + float(self.config.get("collector.rollup_seconds", 60.0)),
            "severity": severity,
            "reason": "low-demand power above personal baseline",
            "current_power_w": current,
            "baseline_p90_w": p90,
            "threshold_w": threshold,
            "window_seconds": window_seconds,
            "window_valid_seconds": valid_seconds,
            "window_high_fraction": high_seconds / max(valid_seconds, 1.0),
            "demand_region": rollup.get("demand_region"),
            "brightness_bucket": rollup.get("brightness_bucket"),
            "battery_epoch": rollup.get("battery_epoch"),
            "system_fingerprint": rollup.get("system_fingerprint"),
            "avg_rapl_w": rollup.get("avg_rapl_w"),
            "max_thermal_pressure": rollup.get("max_thermal_pressure"),
            "avg_network_mbps": rollup.get("avg_network_mbps"),
            "avg_deep_idle_fraction": rollup.get("avg_deep_idle_fraction"),
            "avg_interrupts_per_sec": rollup.get("avg_interrupts_per_sec"),
            "gpu": rollup.get("last_gpu"),
            "devices": rollup.get("last_devices"),
            "top_processes": self.db.recent_process_attribution(
                float(rollup["bucket_ts"]) - 120,
                limit=20,
            ),
            "detected_ts": time.time(),
        }
