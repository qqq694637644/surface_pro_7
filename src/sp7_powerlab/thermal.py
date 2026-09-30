from __future__ import annotations

from dataclasses import dataclass
from typing import Any

THERMAL_STATES = (
    "COOL",
    "WARMING",
    "HEAT_SOAKED",
    "THERMAL_PRESSURE",
    "THROTTLING",
)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _norm(value: float | None, low: float, high: float) -> float:
    if value is None or high <= low:
        return 0.0
    return _clip((float(value) - low) / (high - low))


@dataclass
class ThermalObserver:
    machine: dict[str, Any]
    thermal_config: dict[str, Any]
    state: str = "COOL"

    def calibrated(self) -> bool:
        return bool((self.machine.get("calibration") or {}).get("valid", False))

    def _pressure(self, sample: dict[str, Any]) -> tuple[float, dict[str, float]]:
        model = self.thermal_config.get("model") or {}
        calibration = self.machine.get("thermal") or {}
        baselines = self.machine.get("baselines") or {}

        temp = sample.get("package_temp_c")
        slope = sample.get("temp_slope_c_per_min")
        rapl_300 = sample.get("rapl_power_300s_w")
        throttle = bool(sample.get("throttle_delta")) or bool(sample.get("frequency_collapse"))

        idle_temp = float(baselines.get("idle_temp_c") or 0.0)
        soft_temp = float(calibration.get("soft_temp_c") or 0.0)
        pressure_temp = float(calibration.get("pressure_temp_c") or 0.0)
        slope_ref = float(calibration.get("slope_reference_c_per_min") or 0.0)
        rapl_ref = float(calibration.get("rapl_reference_w") or 0.0)
        normal_rapl = float(baselines.get("normal_rapl_p90_w") or 0.0)

        if not self.calibrated():
            components = {
                "temperature": 0.0,
                "slope": 0.0,
                "sustained_power": 0.0,
                "throttle": 1.0 if throttle else 0.0,
            }
        else:
            temp_high = pressure_temp if pressure_temp > soft_temp else soft_temp + 5.0
            slope_high = max(0.5, slope_ref)
            rapl_high = max(normal_rapl + 0.5, rapl_ref)
            components = {
                "temperature": _norm(temp, idle_temp, temp_high),
                "slope": _norm(slope, 0.0, slope_high),
                "sustained_power": _norm(
                    rapl_300,
                    max(0.0, normal_rapl * 0.5),
                    rapl_high,
                ),
                "throttle": 1.0 if throttle else 0.0,
            }

        pressure = _clip(
            components["temperature"] * float(model.get("temperature_weight", 0.40))
            + components["slope"] * float(model.get("slope_weight", 0.20))
            + components["sustained_power"] * float(model.get("sustained_power_weight", 0.25))
            + components["throttle"] * float(model.get("throttle_weight", 0.15))
        )
        if throttle:
            pressure = max(pressure, 0.98)
        return pressure, components

    def observe(self, sample: dict[str, Any]) -> dict[str, Any]:
        pressure, components = self._pressure(sample)
        thresholds = self.thermal_config.get("state") or {}
        current = self.state

        throttle = bool(sample.get("throttle_delta")) or bool(sample.get("frequency_collapse"))
        if throttle or pressure >= float(thresholds.get("throttling_enter", 0.95)):
            next_state = "THROTTLING"
        elif current == "THROTTLING":
            next_state = (
                "THERMAL_PRESSURE"
                if pressure >= float(thresholds.get("pressure_exit", 0.60))
                else "HEAT_SOAKED"
            )
        elif pressure >= float(thresholds.get("pressure_enter", 0.75)):
            next_state = "THERMAL_PRESSURE"
        elif current == "THERMAL_PRESSURE":
            next_state = (
                "THERMAL_PRESSURE"
                if pressure >= float(thresholds.get("pressure_exit", 0.60))
                else "HEAT_SOAKED"
            )
        elif pressure >= float(thresholds.get("heat_soaked_enter", 0.55)):
            next_state = "HEAT_SOAKED"
        elif current == "HEAT_SOAKED":
            next_state = (
                "HEAT_SOAKED"
                if pressure >= float(thresholds.get("heat_soaked_exit", 0.40))
                else "WARMING"
            )
        elif pressure >= float(thresholds.get("warming_enter", 0.35)):
            next_state = "WARMING"
        elif current == "WARMING" and pressure >= float(thresholds.get("warming_exit", 0.25)):
            next_state = "WARMING"
        else:
            next_state = "COOL"

        self.state = next_state
        return {
            "ts": float(sample["ts"]),
            "state": next_state,
            "pressure": pressure,
            "temp_c": sample.get("package_temp_c"),
            "slope_c_per_min": sample.get("temp_slope_c_per_min"),
            "rapl_60s_w": sample.get("rapl_power_60s_w"),
            "rapl_300s_w": sample.get("rapl_power_300s_w"),
            "throttle_evidence": throttle,
            "calibrated": self.calibrated(),
            "components": components,
        }
