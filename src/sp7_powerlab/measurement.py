from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hardware import battery_directory


def valid_discharge_interval_seconds(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    max_gap_seconds: float,
) -> float | None:
    dt = float(current["ts"]) - float(previous["ts"])
    if not (0 < dt <= max_gap_seconds):
        return None
    if previous.get("battery_status") != "Discharging":
        return None
    if current.get("battery_status") != "Discharging":
        return None
    if previous.get("resume_grace") or current.get("resume_grace"):
        return None
    previous_battery_epoch = previous.get("battery_epoch")
    current_battery_epoch = current.get("battery_epoch")
    if (
        previous_battery_epoch is not None
        and current_battery_epoch is not None
        and previous_battery_epoch != current_battery_epoch
    ):
        return None
    previous_evidence_epoch = previous.get("evidence_epoch")
    current_evidence_epoch = current.get("evidence_epoch")
    if (
        previous_evidence_epoch is not None
        and current_evidence_epoch is not None
        and previous_evidence_epoch != current_evidence_epoch
    ):
        return None
    return dt


def contiguous_discharge_segments(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float,
) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    current_segment: list[dict[str, Any]] = []
    for previous, current in zip(rows, rows[1:], strict=False):
        valid = valid_discharge_interval_seconds(
            previous,
            current,
            max_gap_seconds=max_gap_seconds,
        )
        if valid is None:
            if len(current_segment) >= 2:
                segments.append(current_segment)
            current_segment = []
            continue
        if not current_segment:
            current_segment = [previous, current]
        elif current_segment[-1] is previous:
            current_segment.append(current)
        else:
            segments.append(current_segment)
            current_segment = [previous, current]
    if len(current_segment) >= 2:
        segments.append(current_segment)
    return segments


def consistency_windows(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float,
    minimum_seconds: float,
) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    required = max(float(minimum_seconds), 0.0)
    for segment in contiguous_discharge_segments(rows, max_gap_seconds=max_gap_seconds):
        start = 0
        for index in range(1, len(segment)):
            elapsed = float(segment[index]["ts"]) - float(segment[start]["ts"])
            if elapsed + 1e-9 < required:
                continue
            windows.append(segment[start : index + 1])
            start = index
    return windows


def integrate_battery_energy_wh(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float,
) -> tuple[float | None, float]:
    energy_ws = 0.0
    valid_seconds = 0.0
    for previous, current in zip(rows, rows[1:], strict=False):
        dt = valid_discharge_interval_seconds(
            previous,
            current,
            max_gap_seconds=max_gap_seconds,
        )
        if dt is None:
            continue
        left = previous.get("battery_power_w")
        right = current.get("battery_power_w")
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        energy_ws += (float(left) + float(right)) * 0.5 * dt
        valid_seconds += dt
    if valid_seconds <= 0:
        return None, 0.0
    return energy_ws / 3600.0, valid_seconds


def battery_energy_delta_wh(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float,
) -> float | None:
    # Only use a contiguous all-discharging segment. A charging/suspend/gap event
    # makes the endpoint delta untrustworthy for this arm.
    if len(rows) < 2:
        return None
    for previous, current in zip(rows, rows[1:], strict=False):
        if (
            valid_discharge_interval_seconds(
                previous,
                current,
                max_gap_seconds=max_gap_seconds,
            )
            is None
        ):
            return None
    first = rows[0].get("battery_energy_wh")
    last = rows[-1].get("battery_energy_wh")
    if not isinstance(first, (int, float)) or not isinstance(last, (int, float)):
        return None
    delta = float(first) - float(last)
    return delta if delta >= 0 else None


def measurement_energy_summary(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float,
    max_consistency_ratio: float = 0.35,
    max_consistency_abs_wh: float = 0.05,
    require_energy_delta: bool = False,
) -> dict[str, Any]:
    integrated, valid_seconds = integrate_battery_energy_wh(
        rows,
        max_gap_seconds=max_gap_seconds,
    )
    delta = battery_energy_delta_wh(rows, max_gap_seconds=max_gap_seconds)
    error_wh = None
    error_ratio = None
    consistency_status = "UNAVAILABLE"
    quality = "OK"

    discontinuous = any(
        valid_discharge_interval_seconds(
            previous,
            current,
            max_gap_seconds=max_gap_seconds,
        )
        is None
        for previous, current in zip(rows, rows[1:], strict=False)
    )

    if integrated is None:
        quality = "DATA_QUALITY_FAILURE"
        consistency_status = "NO_INTEGRATED_ENERGY"
    elif delta is None:
        consistency_status = (
            "DISCONTINUOUS_OBSERVATION" if discontinuous else "ENERGY_DELTA_UNAVAILABLE"
        )
        if require_energy_delta:
            quality = "DATA_QUALITY_FAILURE"
    elif delta == 0:
        # A coarse fuel gauge can legitimately stay flat during a short arm.
        consistency_status = "UNAVAILABLE_QUANTIZED"
        if require_energy_delta:
            quality = "DATA_QUALITY_FAILURE"
    else:
        error_wh = abs(integrated - delta)
        error_ratio = error_wh / max(integrated, delta, 1e-9)
        if error_wh > max_consistency_abs_wh and error_ratio > max_consistency_ratio:
            quality = "DATA_QUALITY_FAILURE"
            consistency_status = "MISMATCH"
        else:
            consistency_status = "CONSISTENT"

    return {
        "integrated_energy_wh": integrated,
        "battery_energy_delta_wh": delta,
        "consistency_error_wh": error_wh,
        "consistency_error_ratio": error_ratio,
        "consistency_status": consistency_status,
        "energy_valid_seconds": valid_seconds,
        "data_quality": quality,
    }


def _robust_positive_step(steps: list[float]) -> float | None:
    return statistics.median(steps) if steps else None


def _positive_steps(
    rows: list[dict[str, Any]],
    key: str,
    *,
    max_gap_seconds: float | None,
) -> list[float]:
    steps: list[float] = []
    for previous, current in zip(rows, rows[1:], strict=False):
        if (
            max_gap_seconds is not None
            and valid_discharge_interval_seconds(
                previous,
                current,
                max_gap_seconds=max_gap_seconds,
            )
            is None
        ):
            continue
        left = previous.get(key)
        right = current.get(key)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        step = abs(float(right) - float(left))
        if step > 0:
            steps.append(step)
    return steps


def _median_change_cadence(
    rows: list[dict[str, Any]],
    key: str,
    *,
    max_gap_seconds: float | None = None,
) -> float | None:
    intervals: list[float] = []
    previous_value: float | None = None
    last_change_ts: float | None = None
    previous_row: dict[str, Any] | None = None
    for row in rows:
        value = row.get(key)
        if not isinstance(value, (int, float)):
            previous_row = row
            previous_value = None
            last_change_ts = None
            continue
        if (
            max_gap_seconds is not None
            and previous_row is not None
            and valid_discharge_interval_seconds(
                previous_row,
                row,
                max_gap_seconds=max_gap_seconds,
            )
            is None
        ):
            previous_value = float(value)
            last_change_ts = float(row["ts"])
            previous_row = row
            continue
        if previous_value is None:
            previous_value = float(value)
            last_change_ts = float(row["ts"])
        elif float(value) != previous_value:
            current_ts = float(row["ts"])
            if last_change_ts is not None and current_ts > last_change_ts:
                intervals.append(current_ts - last_change_ts)
            previous_value = float(value)
            last_change_ts = current_ts
        previous_row = row
    return statistics.median(intervals) if intervals else None


def characterize_battery_gauge(
    rows: list[dict[str, Any]],
    *,
    expected_power_w: float | None = None,
    energy_quantum_multiplier: float = 8.0,
    max_gap_seconds: float | None = None,
) -> dict[str, Any]:
    energy_steps = _positive_steps(
        rows,
        "battery_energy_wh",
        max_gap_seconds=max_gap_seconds,
    )
    power_steps = _positive_steps(
        rows,
        "battery_power_w",
        max_gap_seconds=max_gap_seconds,
    )
    quantum = _robust_positive_step(energy_steps)
    power_quantum = _robust_positive_step(power_steps)
    energy_cadence = _median_change_cadence(
        rows,
        "battery_energy_wh",
        max_gap_seconds=max_gap_seconds,
    )
    power_cadence = _median_change_cadence(
        rows,
        "battery_power_w",
        max_gap_seconds=max_gap_seconds,
    )
    minimum_arm_seconds = None
    if (
        quantum is not None
        and isinstance(expected_power_w, (int, float))
        and float(expected_power_w) > 0
    ):
        minimum_arm_seconds = quantum * energy_quantum_multiplier / float(expected_power_w) * 3600.0
    return {
        "energy_quantum_wh": quantum,
        "power_quantum_w": power_quantum,
        "energy_update_cadence_seconds": energy_cadence,
        "power_update_cadence_seconds": power_cadence,
        "observed_energy_points": sum(
            isinstance(row.get("battery_energy_wh"), (int, float))
            and row.get("battery_status") == "Discharging"
            and not row.get("resume_grace")
            for row in rows
        ),
        "observed_power_points": sum(
            isinstance(row.get("battery_power_w"), (int, float))
            and row.get("battery_status") == "Discharging"
            and not row.get("resume_grace")
            for row in rows
        ),
        "energy_quantum_multiplier": energy_quantum_multiplier,
        "minimum_arm_seconds_from_quantum": minimum_arm_seconds,
    }


def assess_measurement_trust(
    rows: list[dict[str, Any]],
    *,
    min_samples: int,
    min_observation_seconds: float,
    configured_min_arm_seconds: float,
    energy_quantum_multiplier: float,
    max_gap_seconds: float,
    max_consistency_ratio: float = 0.35,
    max_consistency_abs_wh: float = 0.05,
    min_consistency_windows: int = 2,
) -> dict[str, Any]:
    discharge = [
        row
        for row in rows
        if row.get("battery_status") == "Discharging"
        and isinstance(row.get("battery_power_w"), (int, float))
        and isinstance(row.get("battery_energy_wh"), (int, float))
        and not row.get("resume_grace")
    ]
    integrated, observed_seconds = integrate_battery_energy_wh(
        rows,
        max_gap_seconds=max_gap_seconds,
    )
    expected_power = (
        float(integrated) * 3600.0 / observed_seconds
        if isinstance(integrated, (int, float)) and observed_seconds > 0
        else None
    )
    gauge = characterize_battery_gauge(
        rows,
        expected_power_w=expected_power,
        energy_quantum_multiplier=energy_quantum_multiplier,
        max_gap_seconds=max_gap_seconds,
    )
    energy_summary = measurement_energy_summary(
        rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    quantum_arm = gauge.get("minimum_arm_seconds_from_quantum")
    recommended_min_arm_seconds = max(
        configured_min_arm_seconds,
        float(quantum_arm) if isinstance(quantum_arm, (int, float)) else 0.0,
    )
    windows = consistency_windows(
        rows,
        max_gap_seconds=max_gap_seconds,
        minimum_seconds=recommended_min_arm_seconds,
    )
    consistency_checks: list[dict[str, Any]] = []
    for window in windows:
        summary = measurement_energy_summary(
            window,
            max_gap_seconds=max_gap_seconds,
            max_consistency_ratio=max_consistency_ratio,
            max_consistency_abs_wh=max_consistency_abs_wh,
            require_energy_delta=True,
        )
        consistency_checks.append(
            {
                "start_ts": float(window[0]["ts"]),
                "end_ts": float(window[-1]["ts"]),
                **summary,
            }
        )

    reasons: list[str] = []
    if len(discharge) < min_samples:
        reasons.append("insufficient_discharging_samples")
    if observed_seconds < min_observation_seconds:
        reasons.append("insufficient_observation_duration")
    if not isinstance(expected_power, (int, float)) or expected_power <= 0:
        reasons.append("missing_battery_power")
    if gauge.get("energy_quantum_wh") is None:
        reasons.append("battery_energy_quantum_not_observed")
    if gauge.get("minimum_arm_seconds_from_quantum") is None:
        reasons.append("minimum_arm_duration_not_resolved")
    if len(consistency_checks) < max(int(min_consistency_windows), 1):
        reasons.append("insufficient_battery_consistency_windows")
    if any(check.get("data_quality") != "OK" for check in consistency_checks):
        reasons.append("battery_energy_consistency_failed")

    return {
        "status": "READY" if not reasons else "BLOCKED",
        "reasons": reasons,
        "sample_count": len(discharge),
        "observation_seconds": observed_seconds,
        "expected_power_w": expected_power,
        "configured_min_arm_seconds": configured_min_arm_seconds,
        "recommended_min_arm_seconds": recommended_min_arm_seconds,
        "gauge": gauge,
        "energy_quality": energy_summary,
        "consistency_window_seconds": recommended_min_arm_seconds,
        "required_consistency_windows": max(int(min_consistency_windows), 1),
        "consistency_windows": consistency_checks,
    }


def measurement_trust_matches_epoch(
    record: dict[str, Any] | None,
    epoch: dict[str, Any] | None,
) -> bool:
    if not isinstance(record, dict) or record.get("status") != "READY":
        return False
    if not isinstance(epoch, dict):
        return False
    return (
        record.get("evidence_epoch_id") == epoch.get("epoch_id")
        and record.get("battery_epoch") == epoch.get("battery_epoch")
        and record.get("calibration_version") == epoch.get("calibration_version")
        and record.get("evidence_semantics_version") == epoch.get("evidence_semantics_version")
    )


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _read_number(path: Path, divisor: float = 1.0) -> float | None:
    value = _read_text(path)
    if value is None:
        return None
    try:
        return float(value) / divisor
    except ValueError:
        return None


@dataclass
class MinimalMeter:
    sys_root: Path = Path("/sys")

    def sample(self) -> dict[str, Any]:
        battery = battery_directory(self.sys_root)
        if battery is None:
            raise RuntimeError("battery sysfs directory is unavailable")
        power = _read_number(battery / "power_now", 1_000_000.0)
        if power is not None:
            power = abs(power)
        return {
            "ts": time.time(),
            "battery_status": _read_text(battery / "status"),
            "battery_power_w": power,
            "battery_energy_wh": _read_number(battery / "energy_now", 1_000_000.0),
        }
