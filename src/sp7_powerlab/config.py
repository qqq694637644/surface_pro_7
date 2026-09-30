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
        "rollup_seconds": 60.0,
        "max_gap_seconds": 45.0,
        "top_processes": 10,
    },
    "controller": {
        "minimum_dwell_seconds": 60.0,
        "resume_grace_seconds": 45.0,
        "low_battery_percent": 15.0,
        "waste_window_minutes": 5,
        "waste_min_baselines": 8,
        "waste_relative_threshold": 0.20,
        "waste_absolute_threshold_w": 0.8,
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
