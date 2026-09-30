from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from .config import Config
from .demand import remote_process_tags
from .hardware import (
    PROC,
    SYSFS,
    battery_directory,
    epp_paths,
    intel_pstate_root,
    rapl_energy_path,
    thermal_sensor_path,
)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _number(path: Path, scale: float = 1.0) -> float | None:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return float(raw) / scale
    except ValueError:
        return None


def _command(args: list[str], timeout: float = 1.5) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _battery_snapshot(sys_root: Path = SYSFS) -> dict[str, Any]:
    bat = battery_directory(sys_root)
    if bat is None:
        return {
            "status": None,
            "percent": None,
            "power_w": None,
            "energy_wh": None,
            "energy_full_wh": None,
            "energy_full_design_wh": None,
            "voltage_v": None,
            "current_a": None,
            "identity_hash": "missing",
            "identity": {},
        }

    power_w = _number(bat / "power_now", 1_000_000.0)
    current_a = _number(bat / "current_now", 1_000_000.0)
    voltage_v = _number(bat / "voltage_now", 1_000_000.0)
    if power_w is None and current_a is not None and voltage_v is not None:
        power_w = abs(current_a * voltage_v)
    elif power_w is not None:
        power_w = abs(power_w)

    identity = {
        "model_name": _read(bat / "model_name"),
        "manufacturer": _read(bat / "manufacturer"),
        "serial_number": _read(bat / "serial_number"),
        "technology": _read(bat / "technology"),
        "energy_full_design_wh": _number(bat / "energy_full_design", 1_000_000.0),
    }
    identity_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]

    return {
        "status": _read(bat / "status"),
        "percent": _number(bat / "capacity"),
        "power_w": power_w,
        "energy_wh": _number(bat / "energy_now", 1_000_000.0),
        "energy_full_wh": _number(bat / "energy_full", 1_000_000.0),
        "energy_full_design_wh": _number(bat / "energy_full_design", 1_000_000.0),
        "cycle_count": _number(bat / "cycle_count"),
        "voltage_v": voltage_v,
        "current_a": current_a,
        "identity_hash": identity_hash,
        "identity": identity,
    }


def _psi(resource: str, proc_root: Path = PROC) -> float | None:
    text = _read(proc_root / "pressure" / resource)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("some "):
            for field in line.split()[1:]:
                if field.startswith("avg10="):
                    try:
                        return float(field.split("=", 1)[1])
                    except ValueError:
                        return None
    return None


def _load1(proc_root: Path = PROC) -> float | None:
    raw = _read(proc_root / "loadavg")
    if not raw:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def _cpu_state(sys_root: Path = SYSFS) -> dict[str, Any]:
    root = sys_root / "devices" / "system" / "cpu" / "cpufreq"
    freqs = [
        value
        for path in sorted(root.glob("policy*/scaling_cur_freq"))
        if (value := _number(path)) is not None
    ]
    epps = [value for path in epp_paths(sys_root) if (value := _read(path)) is not None]
    pstate = intel_pstate_root(sys_root)
    no_turbo = _read(pstate / "no_turbo")
    turbo: bool | None = None
    if no_turbo in {"0", "1"}:
        turbo = no_turbo == "0"
    return {
        "avg_freq_khz": _avg(freqs),
        "epp": statistics.mode(epps) if epps else None,
        "max_perf_pct": _number(pstate / "max_perf_pct"),
        "min_perf_pct": _number(pstate / "min_perf_pct"),
        "turbo": turbo,
        "hwp_dynamic_boost": _number(pstate / "hwp_dynamic_boost"),
    }


def _temperature_c(
    sys_root: Path = SYSFS,
    sensor_path: Path | None = None,
) -> float | None:
    path = sensor_path or thermal_sensor_path(sys_root)
    if path is None:
        return None
    value = _number(path)
    if value is None:
        return None
    return value / 1000.0 if value > 500 else value


def _throttle_count(sys_root: Path = SYSFS) -> int | None:
    root = sys_root / "devices" / "system" / "cpu"
    values: list[int] = []
    for path in root.glob("cpu*/thermal_throttle/package_throttle_count"):
        raw = _read(path)
        if raw is None:
            continue
        try:
            values.append(int(raw))
        except ValueError:
            continue
    return sum(values) if values else None


def _brightness(sys_root: Path = SYSFS) -> float | None:
    root = sys_root / "class" / "backlight"
    if not root.exists():
        return None
    for node in sorted(root.iterdir()):
        current = _number(node / "brightness")
        maximum = _number(node / "max_brightness")
        if current is not None and maximum and maximum > 0:
            return max(0.0, min(100.0, current / maximum * 100.0))
    return None


def _network_bytes(proc_root: Path = PROC) -> tuple[int, int]:
    text = _read(proc_root / "net" / "dev") or ""
    rx = tx = 0
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        if name.strip() == "lo":
            continue
        fields = rest.split()
        if len(fields) >= 9:
            try:
                rx += int(fields[0])
                tx += int(fields[8])
            except ValueError:
                continue
    return rx, tx


def _proc_activity_counters(proc_root: Path = PROC) -> tuple[int | None, int | None]:
    text = _read(proc_root / "stat") or ""
    interrupts = context_switches = None
    for line in text.splitlines():
        if line.startswith("intr "):
            try:
                interrupts = int(line.split()[1])
            except (ValueError, IndexError):
                pass
        elif line.startswith("ctxt "):
            try:
                context_switches = int(line.split()[1])
            except (ValueError, IndexError):
                pass
    return interrupts, context_switches


def _cpuidle_deep_time_us(sys_root: Path = SYSFS) -> int | None:
    root = sys_root / "devices" / "system" / "cpu"
    total = 0
    found = False
    for state in root.glob("cpu[0-9]*/cpuidle/state*"):
        name = (_read(state / "name") or "").upper()
        match = re.search(r"C(\d+)", name)
        if not match or int(match.group(1)) < 3:
            continue
        raw = _read(state / "time")
        if raw is None:
            continue
        try:
            total += int(raw)
            found = True
        except ValueError:
            continue
    return total if found else None


def _runtime_pm_snapshot(sys_root: Path = SYSFS) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for bus in ("usb", "pci"):
        counts: dict[str, int] = {}
        root = sys_root / "bus" / bus / "devices"
        if root.exists():
            for status_path in root.glob("*/power/runtime_status"):
                status = (_read(status_path) or "unknown").lower()
                counts[status] = counts.get(status, 0) + 1
        result[bus] = counts
    return result


def _rfkill_snapshot(sys_root: Path = SYSFS) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {"wifi": None, "bluetooth": None}
    root = sys_root / "class" / "rfkill"
    if not root.exists():
        return result
    states: dict[str, list[bool]] = {"wifi": [], "bluetooth": []}
    for node in root.glob("rfkill*"):
        kind = (_read(node / "type") or "").lower()
        if kind == "wlan":
            key = "wifi"
        elif kind == "bluetooth":
            key = "bluetooth"
        else:
            continue
        state = _read(node / "state")
        if state in {"0", "1"}:
            states[key].append(state == "1")
    for key, values in states.items():
        if values:
            result[key] = any(values)
    return result


def _media_playing() -> bool:
    if not shutil.which("playerctl"):
        return False
    code, output = _command(["playerctl", "-a", "status"])
    return code == 0 and any(line.strip() == "Playing" for line in output.splitlines())


def _user_active() -> bool:
    if not shutil.which("loginctl"):
        return True
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if not user:
        return True
    code, output = _command(["loginctl", "show-user", user, "-p", "IdleHint", "--value"])
    if code != 0:
        return True
    return output.strip().lower() != "yes"


def _thermald_active() -> bool:
    if not shutil.which("systemctl"):
        return False
    code, output = _command(["systemctl", "is-active", "thermald"])
    return code == 0 and output == "active"


def _gpu_snapshot(sys_root: Path = SYSFS) -> dict[str, Any]:
    drm = sys_root / "class" / "drm"
    if not drm.exists():
        return {}
    for card in sorted(drm.glob("card[0-9]")):
        device = card / "device"
        freq = _first_existing(
            [
                device / "gt_cur_freq_mhz",
                device / "gt" / "gt0" / "rps_cur_freq_mhz",
            ]
        )
        rc6 = _first_existing(
            [
                device / "power" / "rc6_residency_ms",
                device / "gt" / "gt0" / "rc6_residency_ms",
            ]
        )
        if freq or rc6:
            return {
                "frequency_mhz": _number(freq) if freq else None,
                "rc6_residency_ms": _number(rc6) if rc6 else None,
            }
    return {}


def _activitywatch(config: Config) -> dict[str, Any]:
    if not bool(config.get("activity.enabled", True)):
        return {}
    base = str(config.get("activity.server_url", "http://127.0.0.1:5600")).rstrip("/")
    timeout = float(config.get("activity.timeout_seconds", 1.0))
    try:
        with urllib.request.urlopen(base + "/api/0/buckets", timeout=timeout) as response:
            buckets = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}

    window_buckets = [
        key for key in buckets if key.startswith("aw-watcher-window") or key.startswith("awatcher")
    ]
    if not window_buckets:
        return {}

    bucket = sorted(window_buckets)[-1]
    try:
        with urllib.request.urlopen(
            f"{base}/api/0/buckets/{bucket}/events?limit=1",
            timeout=timeout,
        ) as response:
            events = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}
    if not events:
        return {}
    data = events[0].get("data") or {}
    result = {
        "app": data.get("app"),
        "title": data.get("title")
        if bool(config.get("activity.store_window_title", True))
        else None,
    }
    return result


def _wifi_snapshot() -> dict[str, Any]:
    if not shutil.which("iw"):
        return {"available": False, "interfaces": []}
    code, output = _command(["iw", "dev"])
    if code != 0:
        return {"available": True, "interfaces": []}
    interfaces: list[dict[str, Any]] = []
    names = [
        line.strip().split(maxsplit=1)[1]
        for line in output.splitlines()
        if line.strip().startswith("Interface ") and len(line.strip().split(maxsplit=1)) == 2
    ]
    for name in names:
        ps_code, ps_output = _command(["iw", "dev", name, "get", "power_save"])
        power_save = None
        if ps_code == 0 and ":" in ps_output:
            power_save = ps_output.split(":", 1)[1].strip().lower()
        interfaces.append({"name": name, "power_save": power_save})
    return {"available": True, "interfaces": interfaces}


def _wakeup_sources_snapshot(sys_root: Path = SYSFS) -> list[dict[str, Any]]:
    path = sys_root / "kernel" / "debug" / "wakeup_sources"
    text = _read(path)
    if not text:
        return []
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    header = lines[0]
    wanted = {
        key: header.index(key)
        for key in ("name", "active_count", "event_count", "wakeup_count")
        if key in header
    }
    if "name" not in wanted or "event_count" not in wanted:
        return []
    rows: list[dict[str, Any]] = []
    for fields in lines[1:]:
        if len(fields) <= max(wanted.values()):
            continue
        item: dict[str, Any] = {"name": fields[wanted["name"]]}
        for key in ("active_count", "event_count", "wakeup_count"):
            if key not in wanted:
                continue
            try:
                item[key] = int(fields[wanted[key]])
            except ValueError:
                item[key] = None
        rows.append(item)
    rows.sort(key=lambda item: int(item.get("event_count") or 0), reverse=True)
    return rows[:10]


class ProcessSampler:
    def __init__(self, top_n: int = 10):
        self.top_n = top_n
        self._primed = False
        self.remote_processes: list[str] = []

    def sample(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        remote_processes: set[str] = set()
        for proc in psutil.process_iter(
            [
                "pid",
                "ppid",
                "name",
                "exe",
                "cmdline",
                "create_time",
                "memory_info",
                "io_counters",
            ]
        ):
            try:
                cpu = proc.cpu_percent(None)
                info = proc.info
                memory = info.get("memory_info")
                io = info.get("io_counters")
                rows.append(
                    {
                        "pid": int(info["pid"]),
                        "ppid": int(info.get("ppid") or 0),
                        "name": info.get("name"),
                        "executable": info.get("exe"),
                        "start_time": float(info.get("create_time") or 0.0),
                        "cpu_percent": float(cpu),
                        "rss_bytes": getattr(memory, "rss", None),
                        "read_bytes": getattr(io, "read_bytes", None),
                        "write_bytes": getattr(io, "write_bytes", None),
                    }
                )
                remote_processes.update(
                    remote_process_tags(
                        info.get("name"),
                        info.get("exe"),
                        info.get("cmdline"),
                    )
                )
            except (psutil.Error, OSError):
                continue
        rows.sort(key=lambda item: item.get("cpu_percent") or 0.0, reverse=True)
        self._primed = True
        self.remote_processes = sorted(remote_processes)
        return rows[: self.top_n]


class TelemetryCollector:
    def __init__(
        self,
        config: Config,
        *,
        sys_root: Path = SYSFS,
        proc_root: Path = PROC,
        thermal_sensor_override: str | None = None,
        clock=time.time,
    ):
        self.config = config
        self.sys_root = sys_root
        self.proc_root = proc_root
        self.clock = clock
        self.rapl_path = rapl_energy_path(sys_root)
        self.thermal_path = thermal_sensor_path(sys_root, thermal_sensor_override)
        self.rapl_max_path = (
            self.rapl_path.parent / "max_energy_range_uj" if self.rapl_path else None
        )
        self._last_rapl: tuple[float, float] | None = None
        self._rapl_power: deque[tuple[float, float]] = deque(maxlen=2048)
        self._temps: deque[tuple[float, float]] = deque(maxlen=2048)
        self._last_network: tuple[float, int, int] | None = None
        self._last_throttle: int | None = None
        self._last_proc_activity: tuple[float, int | None, int | None] | None = None
        self._last_cpuidle: tuple[float, int] | None = None
        self._last_ts: float | None = None
        self._resume_until = 0.0
        self.process_sampler = ProcessSampler(int(config.get("collector.top_processes", 10)))
        self._last_process_ts = 0.0
        self._last_process_rows: list[dict[str, Any]] = []
        self._thermald_cache: tuple[float, bool] = (0.0, False)
        self._device_cache: tuple[float, dict[str, Any]] = (0.0, {})

    def _rapl(self, ts: float) -> tuple[float | None, dict[str, float | None]]:
        if self.rapl_path is None:
            return None, {"10s": None, "60s": None, "300s": None}
        energy = _number(self.rapl_path)
        if energy is None:
            return None, {"10s": None, "60s": None, "300s": None}
        power: float | None = None
        if self._last_rapl:
            prev_ts, prev_energy = self._last_rapl
            delta = energy - prev_energy
            if delta < 0 and self.rapl_max_path:
                max_energy = _number(self.rapl_max_path)
                if max_energy:
                    delta = max_energy - prev_energy + energy
            dt = ts - prev_ts
            if dt > 0 and delta >= 0:
                power = (delta / 1_000_000.0) / dt
                if math.isfinite(power) and power < 200:
                    self._rapl_power.append((ts, power))
        self._last_rapl = (ts, energy)

        def rolling(seconds: float) -> float | None:
            values = [value for stamp, value in self._rapl_power if stamp >= ts - seconds]
            return _avg(values)

        return power, {"10s": rolling(10), "60s": rolling(60), "300s": rolling(300)}

    def _temp(self, ts: float) -> tuple[float | None, float | None]:
        temp = _temperature_c(self.sys_root, self.thermal_path)
        if temp is not None:
            self._temps.append((ts, temp))
        cutoff = ts - 300
        points = [(stamp, value) for stamp, value in self._temps if stamp >= cutoff]
        slope = None
        if len(points) >= 2 and points[-1][0] - points[0][0] >= 20:
            dt_min = (points[-1][0] - points[0][0]) / 60.0
            slope = (points[-1][1] - points[0][1]) / dt_min if dt_min else None
        return temp, slope

    def _network(self, ts: float) -> tuple[float, float]:
        rx, tx = _network_bytes(self.proc_root)
        result = (0.0, 0.0)
        if self._last_network:
            prev_ts, prev_rx, prev_tx = self._last_network
            dt = ts - prev_ts
            if dt > 0:
                result = (
                    max(0, rx - prev_rx) * 8 / dt / 1_000_000,
                    max(0, tx - prev_tx) * 8 / dt / 1_000_000,
                )
        self._last_network = (ts, rx, tx)
        return result

    def sample(self) -> dict[str, Any]:
        ts = float(self.clock())
        max_gap = float(self.config.get("collector.max_gap_seconds", 45.0))
        gap = None if self._last_ts is None else ts - self._last_ts
        if gap is not None and gap > max_gap:
            self._resume_until = ts + float(
                self.config.get("controller.resume_grace_seconds", 45.0)
            )
            self._rapl_power.clear()
            self._temps.clear()
            self._last_rapl = None
            self._last_network = None
            self._last_proc_activity = None
            self._last_cpuidle = None
        self._last_ts = ts

        battery = _battery_snapshot(self.sys_root)
        cpu = _cpu_state(self.sys_root)
        instant_rapl, rolling = self._rapl(ts)
        temp, temp_slope = self._temp(ts)
        rx_mbps, tx_mbps = self._network(ts)
        interrupts, context_switches = _proc_activity_counters(self.proc_root)
        interrupts_per_sec = context_switches_per_sec = None
        if self._last_proc_activity:
            prev_ts, prev_interrupts, prev_context = self._last_proc_activity
            dt = ts - prev_ts
            if dt > 0:
                if interrupts is not None and prev_interrupts is not None:
                    interrupts_per_sec = max(0, interrupts - prev_interrupts) / dt
                if context_switches is not None and prev_context is not None:
                    context_switches_per_sec = max(0, context_switches - prev_context) / dt
        self._last_proc_activity = (ts, interrupts, context_switches)

        deep_time = _cpuidle_deep_time_us(self.sys_root)
        deep_idle_fraction = None
        if deep_time is not None and self._last_cpuidle:
            prev_ts, prev_deep = self._last_cpuidle
            dt = ts - prev_ts
            cpu_count = max(1, psutil.cpu_count(logical=True) or 1)
            if dt > 0:
                deep_idle_fraction = max(
                    0.0,
                    min(1.0, (deep_time - prev_deep) / (dt * cpu_count * 1_000_000.0)),
                )
        if deep_time is not None:
            self._last_cpuidle = (ts, deep_time)
        throttle = _throttle_count(self.sys_root)
        throttle_delta = (
            max(0, throttle - self._last_throttle)
            if throttle is not None and self._last_throttle is not None
            else 0
        )
        if throttle is not None:
            self._last_throttle = throttle

        cpu_usage = psutil.cpu_percent(interval=None)
        gpu = _gpu_snapshot(self.sys_root)
        media = _media_playing()
        active = _user_active()
        activity = _activitywatch(self.config)

        processes_fresh = False
        process_interval = float(self.config.get("collector.process_seconds", 30.0))
        if ts - self._last_process_ts >= process_interval:
            self._last_process_rows = self.process_sampler.sample()
            self._last_process_ts = ts
            processes_fresh = True

        cached_at, thermald = self._thermald_cache
        if ts - cached_at >= 60.0:
            thermald = _thermald_active()
            self._thermald_cache = (ts, thermald)

        device_cached_at, device_snapshot = self._device_cache
        if ts - device_cached_at >= 60.0:
            device_snapshot = {
                "runtime_pm": _runtime_pm_snapshot(self.sys_root),
                "rfkill": _rfkill_snapshot(self.sys_root),
                "wifi": _wifi_snapshot(),
                "wakeup_sources": _wakeup_sources_snapshot(self.sys_root),
            }
            self._device_cache = (ts, device_snapshot)

        media_decode_hint = "not_playing"
        if media:
            gpu_freq = gpu.get("frequency_mhz")
            rapl_60 = rolling["60s"]
            if cpu_usage >= 35.0 and isinstance(rapl_60, (int, float)) and rapl_60 >= 5.0:
                media_decode_hint = "suspected_software_decode"
            elif isinstance(gpu_freq, (int, float)) and gpu_freq > 0 and cpu_usage < 25.0:
                media_decode_hint = "likely_hardware_accelerated"
            else:
                media_decode_hint = "unknown"

        avg_freq = cpu.get("avg_freq_khz")
        frequency_collapse = bool(
            cpu_usage >= 30 and isinstance(avg_freq, (int, float)) and avg_freq < 500_000
        )

        return {
            "ts": ts,
            "wall_ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "gap_seconds": gap,
            "resume_grace": ts < self._resume_until,
            "battery": battery,
            "battery_status": battery.get("status"),
            "battery_pct": battery.get("percent"),
            "battery_power_w": battery.get("power_w"),
            "battery_energy_wh": battery.get("energy_wh"),
            "brightness_pct": _brightness(self.sys_root),
            "cpu_usage": cpu_usage,
            "cpu_psi": _psi("cpu", self.proc_root),
            "io_psi": _psi("io", self.proc_root),
            "memory_psi": _psi("memory", self.proc_root),
            "load1": _load1(self.proc_root),
            **cpu,
            "package_temp_c": temp,
            "thermal_sensor_path": str(self.thermal_path) if self.thermal_path else None,
            "temp_slope_c_per_min": temp_slope,
            "rapl_power_instant_w": instant_rapl,
            "rapl_power_10s_w": rolling["10s"],
            "rapl_power_60s_w": rolling["60s"],
            "rapl_power_300s_w": rolling["300s"],
            "throttle_count": throttle,
            "throttle_delta": throttle_delta,
            "frequency_collapse": frequency_collapse,
            "user_active": active,
            "media_playing": media,
            "network_rx_mbps": rx_mbps,
            "network_tx_mbps": tx_mbps,
            "interrupts_per_sec": interrupts_per_sec,
            "context_switches_per_sec": context_switches_per_sec,
            "deep_idle_fraction": deep_idle_fraction,
            "thermald_active": thermald,
            "gpu": gpu,
            "devices": device_snapshot,
            "media_decode_hint": media_decode_hint,
            "activity": activity,
            "processes": self._last_process_rows,
            "processes_fresh": processes_fresh,
            "remote_process_present": bool(self.process_sampler.remote_processes),
            "remote_processes": self.process_sampler.remote_processes,
        }
