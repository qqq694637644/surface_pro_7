from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SYSFS = Path("/sys")
PROC = Path("/proc")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _command(args: list[str], timeout: float = 2.0) -> tuple[int, str]:
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


def cpu_model(proc_root: Path = PROC) -> str:
    text = _read(proc_root / "cpuinfo") or ""
    for line in text.splitlines():
        if line.lower().startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def dmi_value(name: str, sys_root: Path = SYSFS) -> str | None:
    return _read(sys_root / "class" / "dmi" / "id" / name)


def battery_directory(sys_root: Path = SYSFS) -> Path | None:
    root = sys_root / "class" / "power_supply"
    if not root.exists():
        return None
    for path in sorted(root.glob("BAT*")):
        if path.is_dir():
            return path
    return None


def rapl_energy_path(sys_root: Path = SYSFS) -> Path | None:
    powercap = sys_root / "class" / "powercap"
    if not powercap.exists():
        return None
    for path in sorted(powercap.glob("intel-rapl*")):
        name = (_read(path / "name") or "").lower()
        energy = path / "energy_uj"
        if energy.exists() and ("package" in name or path.name == "intel-rapl:0"):
            return energy
    direct = powercap / "intel-rapl:0" / "energy_uj"
    return direct if direct.exists() else None


def _configured_sensor_path(sys_root: Path, configured: str | None) -> Path | None:
    if not configured:
        return None
    if configured == "/sys" or configured.startswith("/sys/"):
        try:
            relative = PurePosixPath(configured).relative_to("/sys")
        except ValueError:
            return None
        candidate = sys_root.joinpath(*relative.parts)
    else:
        raw = Path(configured)
        if raw.is_absolute():
            return None
        candidate = sys_root / raw
    try:
        relative = candidate.relative_to(sys_root)
    except ValueError:
        return None
    parts = relative.parts
    if (
        len(parts) < 3
        or parts[:2] != ("class", "thermal")
        and parts[:2]
        != (
            "class",
            "hwmon",
        )
    ):
        return None
    if candidate.name != "temp" and not (
        candidate.name.startswith("temp") and candidate.name.endswith("_input")
    ):
        return None
    return candidate if candidate.is_file() else None


def thermal_sensor_path(
    sys_root: Path = SYSFS,
    configured: str | None = None,
) -> Path | None:
    thermal = sys_root / "class" / "thermal"
    if thermal.exists():
        preferred: list[Path] = []
        for zone in sorted(thermal.glob("thermal_zone*")):
            temp = zone / "temp"
            if not temp.exists():
                continue
            kind = (_read(zone / "type") or "").lower()
            if any(token in kind for token in ("x86_pkg_temp", "package", "pkg_temp")):
                preferred.append(temp)
        if preferred:
            return preferred[0]

    hwmon = sys_root / "class" / "hwmon"
    if hwmon.exists():
        for node in sorted(hwmon.glob("hwmon*")):
            name = (_read(node / "name") or "").lower()
            if name != "coretemp":
                continue
            for label_path in sorted(node.glob("temp*_label")):
                label = (_read(label_path) or "").lower()
                if "package id 0" not in label and "package" not in label:
                    continue
                input_path = label_path.with_name(label_path.name.replace("_label", "_input"))
                if input_path.is_file():
                    return input_path
    return _configured_sensor_path(sys_root, configured)


def intel_pstate_root(sys_root: Path = SYSFS) -> Path:
    return sys_root / "devices" / "system" / "cpu" / "intel_pstate"


def epp_paths(sys_root: Path = SYSFS) -> list[Path]:
    root = sys_root / "devices" / "system" / "cpu" / "cpufreq"
    return sorted(root.glob("policy*/energy_performance_preference"))


def hwp_available(sys_root: Path = SYSFS) -> bool:
    pstate = intel_pstate_root(sys_root)
    status = _read(pstate / "status")
    return bool(status == "active" and epp_paths(sys_root))


def systemd_available() -> bool:
    return shutil.which("systemctl") is not None


def thermald_status() -> dict[str, Any]:
    if not systemd_available():
        return {"available": False, "active": False, "version": None}
    code, active = _command(["systemctl", "is-active", "thermald"])
    version = None
    if shutil.which("thermald"):
        _, version = _command(["thermald", "--version"])
    return {
        "available": shutil.which("thermald") is not None,
        "active": code == 0 and active == "active",
        "version": version or None,
    }


def ownership_conflicts() -> list[str]:
    conflicts: list[str] = []
    for service in (
        "tlp.service",
        "auto-cpufreq.service",
        "power-profiles-daemon.service",
        "power-options.service",
    ):
        if not systemd_available():
            break
        code, active = _command(["systemctl", "is-active", service])
        if code == 0 and active == "active":
            conflicts.append(service)
    return conflicts


@dataclass(frozen=True)
class HardwareReport:
    product: str
    cpu: str
    bios: str | None
    kernel: str
    supported_machine: bool
    capabilities: dict[str, bool]
    thermald: dict[str, Any]
    ownership_conflicts: list[str]
    errors: list[str]
    warnings: list[str]
    thermal_sensor: str | None

    @property
    def writable(self) -> bool:
        return not self.errors and self.thermald.get("active") is True

    @property
    def control_capable(self) -> bool:
        return not [error for error in self.errors if error != "thermald is not active"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "cpu": self.cpu,
            "bios": self.bios,
            "kernel": self.kernel,
            "supported_machine": self.supported_machine,
            "capabilities": self.capabilities,
            "thermald": self.thermald,
            "ownership_conflicts": self.ownership_conflicts,
            "errors": self.errors,
            "warnings": self.warnings,
            "thermal_sensor": self.thermal_sensor,
            "writable": self.writable,
            "control_capable": self.control_capable,
        }


def inspect_hardware(
    *,
    sys_root: Path = SYSFS,
    proc_root: Path = PROC,
    expected_product: str = "Surface Pro 7",
    expected_cpu_substring: str = "i5-1035G4",
    configured_thermal_sensor: str | None = None,
) -> HardwareReport:
    product = dmi_value("product_name", sys_root) or "unknown"
    cpu = cpu_model(proc_root)
    bios = dmi_value("bios_version", sys_root)
    thermal_path = thermal_sensor_path(sys_root, configured_thermal_sensor)
    caps = {
        "intel_pstate": intel_pstate_root(sys_root).exists(),
        "hwp_epp": hwp_available(sys_root),
        "turbo_control": (intel_pstate_root(sys_root) / "no_turbo").exists(),
        "battery": battery_directory(sys_root) is not None,
        "thermal": thermal_path is not None,
        "rapl": rapl_energy_path(sys_root) is not None,
        "systemd": systemd_available(),
    }
    supported = (
        expected_product.lower() in product.lower()
        and expected_cpu_substring.lower() in cpu.lower()
    )
    errors: list[str] = []
    warnings: list[str] = []

    if not supported:
        errors.append(
            f"hardware contract mismatch: expected {expected_product} / {expected_cpu_substring}, "
            f"got {product} / {cpu}"
        )
    for key in (
        "intel_pstate",
        "hwp_epp",
        "turbo_control",
        "battery",
        "thermal",
        "rapl",
        "systemd",
    ):
        if not caps[key]:
            errors.append(f"required capability missing: {key}")

    td = thermald_status()
    if not td["active"]:
        errors.append("thermald is not active")

    conflicts = ownership_conflicts()
    if conflicts:
        errors.append("conflicting power writers active: " + ", ".join(conflicts))

    if os.name != "posix":
        warnings.append("non-Linux development host: write control is disabled")

    return HardwareReport(
        product=product,
        cpu=cpu,
        bios=bios,
        kernel=platform.release(),
        supported_machine=supported,
        capabilities=caps,
        thermald=td,
        ownership_conflicts=conflicts,
        errors=errors,
        warnings=warnings,
        thermal_sensor=str(thermal_path) if thermal_path else None,
    )


def system_fingerprint(report: HardwareReport) -> dict[str, Any]:
    versions: dict[str, str | None] = {}
    for binary in ("firefox", "chromium", "google-chrome", "playerctl"):
        if shutil.which(binary):
            _, output = _command([binary, "--version"])
            versions[binary] = output or None
    if shutil.which("glxinfo"):
        code, output = _command(["glxinfo", "-B"])
        if code == 0:
            mesa_line = next(
                (
                    line.strip()
                    for line in output.splitlines()
                    if "Mesa" in line
                    and (
                        "OpenGL version string" in line
                        or "OpenGL core profile version string" in line
                    )
                ),
                None,
            )
            versions["mesa"] = mesa_line
    versions["desktop"] = (
        os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or None
    )
    versions["thermald"] = report.thermald.get("version")
    return {
        "product": report.product,
        "cpu": report.cpu,
        "bios": report.bios,
        "kernel": report.kernel,
        "intel_pstate": report.capabilities.get("intel_pstate"),
        "hwp_epp": report.capabilities.get("hwp_epp"),
        "thermal_sensor": report.thermal_sensor,
        "versions": versions,
    }
