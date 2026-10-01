from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .evidence import hard_strata_key, robust_distribution
from .storage import Database


def brightness_bucket(value: float | None) -> int:
    if value is None:
        return -1
    return int(max(0, min(100, value)) // 10 * 10)


def remote_bucket(remote_hint: float | None) -> int:
    return 1 if float(remote_hint or 0.0) >= 0.5 else 0


@dataclass
class UnexpectedPowerDetector:
    config: Config
    db: Database

    def _baseline(self, rollup: dict[str, Any]) -> dict[str, Any] | None:
        active_epoch = self.db.active_evidence_epoch()
        strata = hard_strata_key(rollup)
        if active_epoch:
            frozen = self.db.reference_baseline(str(active_epoch["epoch_id"]), strata)
            if frozen:
                return {
                    "source": "frozen_reference",
                    "median_power_w": frozen.get("median_power_w"),
                    "p90_power_w": frozen.get("p75_power_w"),
                    "noise_floor_w": frozen.get("mad_power_w"),
                    "sample_count": frozen.get("sample_count"),
                }
        min_baselines = int(self.config.get("unexpected_power.min_baselines", 8))
        now = float(rollup["bucket_ts"])
        window_seconds = max(
            60.0,
            float(self.config.get("unexpected_power.window_minutes", 5)) * 60.0,
        )
        current_brightness = int(rollup.get("brightness_bucket") or -1)
        candidates = [
            item
            for item in self.db.recent_rollups(now - 30 * 86400, limit=5000)
            if float(item.get("bucket_ts") or 0.0) < now - window_seconds
            and hard_strata_key(item) == strata
            and isinstance(item.get("avg_power_w"), (int, float))
            and (
                current_brightness < 0
                or int(item.get("brightness_bucket") or -1) < 0
                or abs(int(item.get("brightness_bucket") or -1) - current_brightness) <= 10
            )
        ]
        if len(candidates) < min_baselines:
            return None
        stats = robust_distribution([float(item["avg_power_w"]) for item in candidates])
        return {"source": "recent_compatible_history", **stats}

    def detect(self, rollup: dict[str, Any]) -> dict[str, Any] | None:
        if rollup.get("avg_power_w") is None:
            return None
        if rollup.get("local_compute_pressure") not in {None, "LOW"}:
            return None
        if rollup.get("thermal_start") in {"THERMAL_PRESSURE", "THROTTLING"}:
            return None
        if rollup.get("trial_id"):
            return None

        baseline = self._baseline(rollup)
        if not baseline:
            return None
        center = baseline.get("median_power_w")
        if not isinstance(center, (int, float)):
            return None
        relative = float(self.config.get("unexpected_power.relative_threshold", 0.20))
        absolute = float(self.config.get("unexpected_power.absolute_threshold_w", 0.8))
        noise = float(baseline.get("noise_floor_w") or 0.0)
        threshold = max(
            float(center) * (1.0 + relative),
            float(center) + absolute,
            float(center) + 2.0 * noise,
        )
        current = float(rollup["avg_power_w"])
        if current <= threshold:
            return None

        current_ts = float(rollup["bucket_ts"])
        window_seconds = max(
            60.0,
            float(self.config.get("unexpected_power.window_minutes", 5)) * 60.0,
        )
        strata = hard_strata_key(rollup)
        recent = [
            item
            for item in self.db.recent_rollups(current_ts - window_seconds, limit=100)
            if float(item.get("bucket_ts") or 0.0) >= current_ts - window_seconds
            and hard_strata_key(item) == strata
            and isinstance(item.get("avg_power_w"), (int, float))
        ]
        if not any(item.get("bucket_ts") == rollup.get("bucket_ts") for item in recent):
            recent.append(rollup)
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
            "status": "OPEN",
            "classification": "UNEXPECTED_POWER",
            "reason": "power above frozen/recent compatible personal reference",
            "current_power_w": current,
            "baseline_source": baseline.get("source"),
            "baseline_median_w": center,
            "baseline_p90_w": baseline.get("p90_power_w"),
            "noise_floor_w": noise,
            "threshold_w": threshold,
            "window_seconds": window_seconds,
            "window_valid_seconds": valid_seconds,
            "window_high_fraction": high_seconds / max(valid_seconds, 1.0),
            "demand_region": rollup.get("demand_region"),
            "brightness_bucket": rollup.get("brightness_bucket"),
            "battery_epoch": rollup.get("battery_epoch"),
            "evidence_epoch": (self.db.active_evidence_epoch() or {}).get("epoch_id"),
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
