from __future__ import annotations

import hashlib
import os
import platform
import shutil
from pathlib import Path


SYS = Path("/sys")


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def micro_to_unit(value: str | None, scale: float = 1_000_000.0) -> float | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    return parsed / scale


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dmi_snapshot() -> dict:
    base = SYS / "class/dmi/id"
    values = {
        "sys_vendor": read_text(base / "sys_vendor"),
        "product_name": read_text(base / "product_name"),
        "product_version": read_text(base / "product_version"),
        "bios_vendor": read_text(base / "bios_vendor"),
        "bios_version": read_text(base / "bios_version"),
        "board_name": read_text(base / "board_name"),
    }
    vendor = (values["sys_vendor"] or "").lower()
    product = (values["product_name"] or "").strip().lower()
    values["is_surface_pro_7"] = "microsoft" in vendor and product == "surface pro 7"
    return values


def battery_dir() -> Path | None:
    base = SYS / "class/power_supply"
    if not base.exists():
        return None
    for path in sorted(base.glob("BAT*")):
        if path.is_dir():
            return path
    return None


def battery_snapshot() -> dict:
    bat = battery_dir()
    if bat is None:
        return {"present": False}

    power_w = micro_to_unit(read_text(bat / "power_now"))
    energy_wh = micro_to_unit(read_text(bat / "energy_now"))
    energy_full_wh = micro_to_unit(read_text(bat / "energy_full"))
    energy_full_design_wh = micro_to_unit(read_text(bat / "energy_full_design"))

    if power_w is None:
        current_a = micro_to_unit(read_text(bat / "current_now"))
        voltage_v = micro_to_unit(read_text(bat / "voltage_now"))
        if current_a is not None and voltage_v is not None:
            power_w = abs(current_a * voltage_v)

    health = None
    if energy_full_wh is not None and energy_full_design_wh:
        health = 100.0 * energy_full_wh / energy_full_design_wh

    return {
        "present": True,
        "path": str(bat),
        "status": read_text(bat / "status"),
        "capacity_percent": safe_float(read_text(bat / "capacity")),
        "power_w": power_w,
        "energy_wh": energy_wh,
        "energy_full_wh": energy_full_wh,
        "energy_full_design_wh": energy_full_design_wh,
        "health_percent": health,
        "voltage_v": micro_to_unit(read_text(bat / "voltage_now")),
        "cycle_count": safe_int(read_text(bat / "cycle_count")),
    }


def cpu_idle_states() -> list[dict]:
    base = SYS / "devices/system/cpu/cpu0/cpuidle"
    states: list[dict] = []
    if not base.exists():
        return states
    for path in sorted(base.glob("state*")):
        states.append(
            {
                "state": path.name,
                "name": read_text(path / "name"),
                "desc": read_text(path / "desc"),
                "latency_us": safe_int(read_text(path / "latency")),
                "residency_us": safe_int(read_text(path / "residency")),
                "usage": safe_int(read_text(path / "usage")),
                "time_us": safe_int(read_text(path / "time")),
            }
        )
    return states


def deep_idle_time_us(states: list[dict] | None = None) -> int | None:
    states = cpu_idle_states() if states is None else states
    total = 0
    found = False
    for state in states:
        name = (state.get("name") or "").upper()
        digits = "".join(ch for ch in name if ch.isdigit())
        level = int(digits) if digits else None
        if level is not None and level >= 6 and state.get("time_us") is not None:
            total += int(state["time_us"])
            found = True
    return total if found else None


def cpu_snapshot() -> dict:
    base = SYS / "devices/system/cpu/cpu0/cpufreq"
    governor = read_text(base / "scaling_governor")
    epp = read_text(base / "energy_performance_preference")
    max_freq = safe_int(read_text(base / "scaling_max_freq"))
    min_freq = safe_int(read_text(base / "scaling_min_freq"))
    current_freq = safe_int(read_text(base / "scaling_cur_freq"))
    turbo = read_text(SYS / "devices/system/cpu/intel_pstate/no_turbo")
    driver = read_text(base / "scaling_driver")
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = None
    idle_states = cpu_idle_states()
    return {
        "driver": driver,
        "governor": governor,
        "epp": epp,
        "min_freq_khz": min_freq,
        "max_freq_khz": max_freq,
        "current_freq_khz": current_freq,
        "turbo_disabled": turbo == "1" if turbo is not None else None,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "cpu0_idle_states": idle_states,
        "cpu0_deep_idle_time_us": deep_idle_time_us(idle_states),
    }


def backlight_snapshot() -> dict:
    base = SYS / "class/backlight"
    if not base.exists():
        return {"present": False}
    devices = [path for path in sorted(base.iterdir()) if path.is_dir()]
    if not devices:
        return {"present": False}

    path = devices[0]
    brightness = safe_int(read_text(path / "brightness"))
    actual = safe_int(read_text(path / "actual_brightness"))
    maximum = safe_int(read_text(path / "max_brightness"))
    current = actual if actual is not None else brightness
    percent = 100.0 * current / maximum if current is not None and maximum else None
    return {
        "present": True,
        "device": path.name,
        "brightness": brightness,
        "actual_brightness": actual,
        "max_brightness": maximum,
        "percent": percent,
    }


def _temperature_c(raw: str | None) -> float | None:
    value = safe_float(raw)
    if value is None:
        return None
    return value / 1000.0 if abs(value) > 200 else value


def thermal_snapshot() -> dict:
    base = SYS / "class/thermal"
    zones: list[dict] = []
    if base.exists():
        for path in sorted(base.glob("thermal_zone*")):
            temp = _temperature_c(read_text(path / "temp"))
            zones.append(
                {
                    "zone": path.name,
                    "type": read_text(path / "type"),
                    "temp_c": temp,
                }
            )
    temps = [zone["temp_c"] for zone in zones if zone["temp_c"] is not None]
    return {
        "zones": zones,
        "max_temp_c": max(temps) if temps else None,
    }


def network_snapshot() -> dict:
    base = SYS / "class/net"
    interfaces: list[dict] = []
    wifi: dict | None = None
    if not base.exists():
        return {"interfaces": interfaces, "wifi": wifi}

    for path in sorted(base.iterdir()):
        if not path.is_dir() or path.name == "lo":
            continue
        is_wireless = (path / "wireless").exists() or path.name.startswith(("wl", "wlan"))
        info = {
            "name": path.name,
            "wireless": is_wireless,
            "operstate": read_text(path / "operstate"),
            "rx_bytes": safe_int(read_text(path / "statistics/rx_bytes")),
            "tx_bytes": safe_int(read_text(path / "statistics/tx_bytes")),
            "power_control": read_text(path / "device/power/control"),
        }
        interfaces.append(info)
        if is_wireless and wifi is None:
            wifi = info.copy()
    return {"interfaces": interfaces, "wifi": wifi}


def system_snapshot() -> dict:
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "dmi": dmi_snapshot(),
        "battery": battery_snapshot(),
        "cpu": cpu_snapshot(),
        "display": backlight_snapshot(),
        "thermal": thermal_snapshot(),
        "network": network_snapshot(),
        "tools": {
            name: shutil.which(name)
            for name in (
                "powerstat",
                "powerjoular",
                "powertop",
                "power-options",
                "power-daemon-mgr",
                "brightnessctl",
            )
        },
    }


def telemetry_snapshot() -> dict:
    battery = battery_snapshot()
    cpu = cpu_snapshot()
    display = backlight_snapshot()
    thermal = thermal_snapshot()
    network = network_snapshot()
    wifi = network.get("wifi") or {}
    return {
        "battery_percent": battery.get("capacity_percent"),
        "battery_status": battery.get("status"),
        "power_w": battery.get("power_w"),
        "energy_wh": battery.get("energy_wh"),
        "load1": cpu.get("load1"),
        "load5": cpu.get("load5"),
        "load15": cpu.get("load15"),
        "current_freq_khz": cpu.get("current_freq_khz"),
        "max_freq_khz": cpu.get("max_freq_khz"),
        "governor": cpu.get("governor"),
        "epp": cpu.get("epp"),
        "turbo_disabled": cpu.get("turbo_disabled"),
        "cpu0_deep_idle_time_us": cpu.get("cpu0_deep_idle_time_us"),
        "brightness_percent": display.get("percent"),
        "max_temp_c": thermal.get("max_temp_c"),
        "wifi_interface": wifi.get("name"),
        "wifi_operstate": wifi.get("operstate"),
        "wifi_rx_bytes": wifi.get("rx_bytes"),
        "wifi_tx_bytes": wifi.get("tx_bytes"),
    }
