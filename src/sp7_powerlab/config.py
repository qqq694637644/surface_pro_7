from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


DEFAULTS: dict[str, Any] = {
    "collector": {
        "system_interval_seconds": 10.0,
        "process_interval_seconds": 20.0,
        "rollup_seconds": 60.0,
        "max_gap_seconds": 45.0,
        "top_processes": 12,
    },
    "llm": {
        "analysis_interval_minutes": 60,
        "history_days": 30,
        "recent_hours": 24,
        "max_trials_in_pack": 40,
    },
    "experiment": {
        "settle_seconds": 120,
        "interactive_min_sessions": 3,
        "interactive_min_valid_minutes": 60,
        "job_min_repetitions": 3,
        "max_one_primary_change": True,
        "revalidation_sessions": 2,
        "revalidation_min_valid_minutes": 30,
    },
    "automation": {
        "auto_switch_verified_profiles": True,
        "auto_run_low_risk_trials": False,
        "auto_promote_profiles": False,
        "recover_trial_on_collector_start": True,
    },
    "storage": {
        "database": "runtime/powerlab.sqlite3",
        "raw_retention_days": 30,
        "keep_rollups_forever": True,
    },
    "activity": {
        "enabled": True,
        "server_url": "http://127.0.0.1:5600",
        "timeout_seconds": 1.0,
        "store_window_title": True,
        "store_executable": True,
        "store_process_tree": True,
    },
    "policy": {
        "actuator": "auto",
        "safe_profile": "safe-baseline",
        "thermal_emergency_c": 90.0,
        "low_battery_percent": 15.0,
    },
    "power_options": {
        "binary": "power-daemon-mgr",
    },
    "power_profiles_daemon": {
        "binary": "powerprofilesctl",
    },
    "helper": {
        "enabled": True,
        "socket": "/run/sp7-powerlab/helper.sock",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class PowerLabConfig:
    def __init__(self, data: dict[str, Any], root: Path, source: Path | None = None):
        self.data = data
        self.root = root
        self.source = source

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def path(self, dotted: str, default: str) -> Path:
        raw = str(self.get(dotted, default))
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        return path if path.is_absolute() else self.root / path


def load_config(root: Path | None = None, path: Path | None = None) -> PowerLabConfig:
    root = (root or Path.cwd()).resolve()
    if path is None:
        env = os.environ.get("SP7_POWERLAB_CONFIG")
        path = Path(env).expanduser() if env else root / "config" / "powerlab.toml"
    if not path.is_absolute():
        path = root / path

    loaded: dict[str, Any] = {}
    source: Path | None = None
    if path.exists():
        with path.open("rb") as fh:
            parsed = tomllib.load(fh)
        if not isinstance(parsed, dict):
            raise ValueError("PowerLab config root must be a TOML table")
        loaded = parsed
        source = path
    return PowerLabConfig(_deep_merge(DEFAULTS, loaded), root=root, source=source)
