from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


DEFAULTS: dict[str, Any] = {
    "collector": {
        "sample_seconds": 10.0,
        "process_seconds": 30.0,
        "process_seconds_stable": 120.0,
        "process_seconds_trial": 10.0,
        "gpu_seconds": 30.0,
        "gpu_seconds_stable": 120.0,
        "gpu_seconds_trial": 10.0,
        "device_seconds": 60.0,
        "device_seconds_stable": 300.0,
        "device_seconds_trial": 30.0,
        "activity_seconds": 30.0,
        "activity_seconds_stable": 120.0,
        "activity_seconds_trial": 10.0,
        "diagnostic_burst_seconds": 300.0,
        "rollup_seconds": 60.0,
        "max_gap_seconds": 45.0,
        "top_processes": 10,
    },
    "controller": {
        "minimum_dwell_seconds": 60.0,
        "resume_grace_seconds": 45.0,
        "reconcile_seconds": 60.0,
        "low_battery_percent": 15.0,
    },
    "unexpected_power": {
        "window_minutes": 5,
        "min_baselines": 8,
        "relative_threshold": 0.20,
        "absolute_threshold_w": 0.8,
    },
    "experiments": {
        "min_block_seconds": 300.0,
        "named_min_block_seconds": 600.0,
        "settle_min_seconds": 30.0,
        "settle_max_seconds": 300.0,
        "settle_min_samples": 3,
        "settle_window_seconds": 30.0,
        "settle_max_temp_slope_c_per_min": 1.0,
        "settle_max_rapl_range_w": 1.5,
        "settle_max_cpu_psi": 5.0,
        "max_brightness_delta": 10.0,
        "min_power_saving_w": 0.10,
        "max_cpu_psi_delta": 2.0,
        "max_io_psi_delta": 2.0,
        "max_thermal_pressure_delta": 0.10,
        "max_media_drop": 0.05,
        "max_sustained_compute_delta": 0.10,
    },
    "evidence": {
        "semantics_version": 4,
        "practical_threshold_w": 0.10,
        "min_crossover_episodes": 2,
        "medium_effect_min_crossover_episodes": 3,
        "large_effect_multiplier": 2.0,
        "direction_consistency": 0.75,
        "reference_min_windows": 8,
        "reference_min_valid_fraction": 0.80,
        "max_energy_consistency_ratio": 0.35,
        "max_energy_consistency_abs_wh": 0.05,
        "gauge_quantum_multiplier": 8.0,
        "measurement_min_samples": 30,
        "measurement_min_observation_seconds": 900.0,
        "measurement_min_consistency_windows": 2,
        "noise_arm_confidence_multiplier": 2.0,
    },
    "calibration": {
        "cold_idle_min_seconds": 900.0,
        "normal_interactive_min_seconds": 1800.0,
        "media_min_seconds": 600.0,
        "bounded_burst_min_seconds": 30.0,
    },
    "automation": {
        "level": 0,
        "auto_promote": False,
    },
    "storage": {
        "database": "runtime/powerlab.sqlite3",
        "raw_retention_days": 30,
    },
    "llm": {
        "analysis_interval_minutes": 60,
        "history_hours": 24,
        "max_incidents": 20,
        "max_trials": 20,
    },
    "activity": {
        "enabled": True,
        "server_url": "http://127.0.0.1:5600",
        "timeout_seconds": 1.0,
        "store_window_title": True,
    },
    "helper": {
        "socket": "/run/sp7-powerlab/helper.sock",
        "enabled": True,
    },
    "minimal_meter": {
        "sample_seconds": 60.0,
    },
    "scheduler": {
        "min_noise_windows": 8,
        "min_cpu_rapl_w": 0.5,
        "max_perf_step_pct": 5,
        "min_max_perf_pct": 30,
        "max_max_perf_pct": 100,
        "allow_turbo_off": True,
        "max_candidate_trials": 2,
        "max_trials_per_week": 4,
        "max_candidate_minutes_per_day": 30.0,
        "negative_feedback_cooldown_seconds": 86400.0,
        "thermal_event_cooldown_seconds": 21600.0,
        "check_seconds": 60.0,
    },
    "stable": {
        "coverage_days": 30,
        "target_trusted_fraction": 0.90,
        "feedback_lookback_days": 7,
    },
    "drift": {
        "minimum_recent_windows": 8,
        "absolute_threshold_w": 0.30,
        "relative_threshold": 0.08,
        "noise_multiplier": 2.0,
        "cooldown_seconds": 21600.0,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in base.items():
        result[key] = dict(value) if isinstance(value, dict) else value
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass(frozen=True)
class Config:
    root: Path
    data: dict[str, Any]

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def path(self, dotted: str) -> Path:
        raw = str(self.get(dotted))
        path = Path(raw).expanduser()
        return path if path.is_absolute() else self.root / path


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(root: Path, path: Path | None = None) -> Config:
    path = path or root / "config" / "powerlab.toml"
    return Config(root=root, data=_merge(DEFAULTS, load_toml(path)))


def machine_path(root: Path) -> Path:
    return root / "config" / "machine.toml"


def envelope_path(root: Path) -> Path:
    return root / "config" / "envelopes.toml"


def thermal_path(root: Path) -> Path:
    return root / "config" / "thermal.toml"


def load_machine(root: Path) -> dict[str, Any]:
    return load_toml(machine_path(root))


def load_envelope_config(root: Path) -> dict[str, Any]:
    return load_toml(envelope_path(root))


def load_thermal_config(root: Path) -> dict[str, Any]:
    return load_toml(thermal_path(root))
