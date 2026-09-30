from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import ActuatorError, ParameterActuator


CPU_POLICY_ROOT = Path("/sys/devices/system/cpu/cpufreq")
INTEL_PSTATE = Path("/sys/devices/system/cpu/intel_pstate")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, PermissionError):
        return None


def _write(path: Path, value: Any) -> None:
    try:
        path.write_text(str(value), encoding="utf-8")
    except (OSError, PermissionError) as exc:
        raise ActuatorError(f"Cannot write {path}: {exc}") from exc


class SysfsParameterActuator(ParameterActuator):
    def _policy_paths(self, leaf: str) -> list[Path]:
        return [p / leaf for p in sorted(CPU_POLICY_ROOT.glob("policy*")) if (p / leaf).exists()]

    def snapshot(self, parameter: str) -> Any:
        if parameter == "cpu.epp":
            return {str(path): _read(path) for path in self._policy_paths("energy_performance_preference")}
        if parameter == "cpu.max_freq_khz":
            return {str(path): _read(path) for path in self._policy_paths("scaling_max_freq")}
        if parameter == "cpu.turbo_disabled":
            path = INTEL_PSTATE / "no_turbo"
            return _read(path)
        if parameter == "display.brightness_percent":
            if shutil.which("brightnessctl"):
                proc = subprocess.run(
                    ["brightnessctl", "-m"], capture_output=True, text=True, timeout=3, check=False
                )
                return proc.stdout.strip()
            raise ActuatorError("brightnessctl is required for display.brightness_percent")
        if parameter == "wifi.power_save":
            if not shutil.which("iw"):
                raise ActuatorError("iw not found")
            interfaces = list(Path("/sys/class/net").glob("wl*"))
            if not interfaces:
                raise ActuatorError("No wireless interface detected")
            iface = interfaces[0].name
            proc = subprocess.run(
                ["iw", "dev", iface, "get", "power_save"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if proc.returncode != 0:
                raise ActuatorError((proc.stderr or proc.stdout).strip())
            raw = proc.stdout.strip().lower()
            enabled = "on" in raw
            return {"interface": iface, "enabled": enabled, "raw": proc.stdout.strip()}
        if parameter == "radio.bluetooth_enabled":
            if not shutil.which("rfkill"):
                raise ActuatorError("rfkill not found")
            proc = subprocess.run(
                ["rfkill", "-J"], capture_output=True, text=True, timeout=3, check=False
            )
            try:
                payload = json.loads(proc.stdout or "{}")
                devices = payload.get("rfkilldevices") or payload.get("rfkill") or []
                for device in devices:
                    if str(device.get("type", "")).lower() == "bluetooth":
                        soft = str(device.get("soft", "")).lower()
                        blocked = soft in {"blocked", "yes", "true", "1"}
                        return {"enabled": not blocked, "raw": device}
            except json.JSONDecodeError:
                pass
            fallback = subprocess.run(
                ["rfkill", "list", "bluetooth"],
                capture_output=True, text=True, timeout=3, check=False,
            ).stdout
            blocked = "Soft blocked: yes" in fallback
            return {"enabled": not blocked, "raw": fallback.strip()}
        raise ActuatorError(f"Unsupported parameter: {parameter}")

    def apply(self, parameter: str, value: Any) -> dict[str, Any]:
        before = self.snapshot(parameter)
        if parameter == "cpu.epp":
            paths = self._policy_paths("energy_performance_preference")
            if not paths:
                raise ActuatorError("CPU EPP is not exposed by this kernel/driver")
            for path in paths:
                _write(path, value)
            after = self.snapshot(parameter)
            if any(v != str(value) for v in after.values()):
                raise ActuatorError(f"EPP read-back mismatch: {after}")
        elif parameter == "cpu.max_freq_khz":
            paths = self._policy_paths("scaling_max_freq")
            if not paths:
                raise ActuatorError("scaling_max_freq is not exposed")
            for path in paths:
                _write(path, int(value))
            after = self.snapshot(parameter)
            if any(v != str(int(value)) for v in after.values()):
                raise ActuatorError(f"max-freq read-back mismatch: {after}")
        elif parameter == "cpu.turbo_disabled":
            path = INTEL_PSTATE / "no_turbo"
            if not path.exists():
                raise ActuatorError("intel_pstate/no_turbo is unavailable")
            desired = "1" if bool(value) else "0"
            _write(path, desired)
            after = self.snapshot(parameter)
            if after != desired:
                raise ActuatorError(f"Turbo read-back mismatch: {after}")
        elif parameter == "display.brightness_percent":
            if not shutil.which("brightnessctl"):
                raise ActuatorError("brightnessctl not found")
            proc = subprocess.run(
                ["brightnessctl", "set", f"{int(value)}%"],
                capture_output=True, text=True, timeout=4, check=False,
            )
            if proc.returncode != 0:
                raise ActuatorError((proc.stderr or proc.stdout).strip())
            after = self.snapshot(parameter)
        elif parameter == "wifi.power_save":
            if not shutil.which("iw"):
                raise ActuatorError("iw not found")
            interfaces = list(Path("/sys/class/net").glob("wl*"))
            if not interfaces:
                raise ActuatorError("No wireless interface detected")
            iface = interfaces[0].name
            state = "on" if bool(value) else "off"
            proc = subprocess.run(
                ["iw", "dev", iface, "set", "power_save", state],
                capture_output=True, text=True, timeout=4, check=False,
            )
            if proc.returncode != 0:
                raise ActuatorError((proc.stderr or proc.stdout).strip())
            after = self.snapshot(parameter)
        elif parameter == "radio.bluetooth_enabled":
            if not shutil.which("rfkill"):
                raise ActuatorError("rfkill not found")
            verb = "unblock" if bool(value) else "block"
            proc = subprocess.run(
                ["rfkill", verb, "bluetooth"],
                capture_output=True, text=True, timeout=4, check=False,
            )
            if proc.returncode != 0:
                raise ActuatorError((proc.stderr or proc.stdout).strip())
            after = self.snapshot(parameter)
        else:
            raise ActuatorError(f"Unsupported parameter: {parameter}")
        return {"parameter": parameter, "before": before, "after": after, "requested": value}

    def restore(self, parameter: str, value: Any) -> dict[str, Any]:
        if parameter in {"cpu.epp", "cpu.max_freq_khz"} and isinstance(value, dict):
            before = self.snapshot(parameter)
            leaf = (
                "energy_performance_preference"
                if parameter == "cpu.epp"
                else "scaling_max_freq"
            )
            allowed_paths = {
                str(path): path for path in self._policy_paths(leaf)
            }
            unexpected = sorted(set(value) - set(allowed_paths))
            if unexpected:
                raise ActuatorError(
                    f"Restore snapshot contains unauthorized paths: {unexpected}"
                )
            for raw_path, raw_value in value.items():
                path = allowed_paths.get(raw_path)
                if path is None or raw_value is None:
                    continue
                if parameter == "cpu.epp":
                    allowed_epp = {
                        "performance",
                        "balance_performance",
                        "balance_power",
                        "power",
                    }
                    if str(raw_value) not in allowed_epp:
                        raise ActuatorError(
                            f"Refusing invalid EPP restore value: {raw_value}"
                        )
                    restored_value: Any = str(raw_value)
                else:
                    try:
                        restored_value = int(raw_value)
                    except (TypeError, ValueError) as exc:
                        raise ActuatorError(
                            f"Refusing invalid max-frequency restore value: {raw_value}"
                        ) from exc
                    if not 400_000 <= restored_value <= 5_000_000:
                        raise ActuatorError(
                            f"Refusing out-of-range max-frequency restore value: {restored_value}"
                        )
                _write(path, restored_value)
            return {"parameter": parameter, "before": before, "after": self.snapshot(parameter)}
        if parameter == "cpu.turbo_disabled":
            desired = str(value)
            path = INTEL_PSTATE / "no_turbo"
            _write(path, desired)
            return {"parameter": parameter, "after": self.snapshot(parameter)}
        if parameter == "display.brightness_percent" and isinstance(value, str):
            parts = value.split(",")
            percent = next((part.rstrip("%") for part in parts if part.endswith("%")), None)
            if percent:
                restored_percent = float(percent)
                if not 0 <= restored_percent <= 100:
                    raise ActuatorError(
                        f"Refusing invalid brightness restore value: {restored_percent}"
                    )
                return self.apply(parameter, restored_percent)
        if parameter == "wifi.power_save" and isinstance(value, dict) and "enabled" in value:
            return self.apply(parameter, bool(value["enabled"]))
        if parameter == "radio.bluetooth_enabled" and isinstance(value, dict) and "enabled" in value:
            return self.apply(parameter, bool(value["enabled"]))
        raise ActuatorError(f"Automatic restore format unsupported for {parameter}")
