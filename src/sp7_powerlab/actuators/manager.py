from __future__ import annotations

from typing import Any
from pathlib import Path

from .base import ActuatorError
from .power_options import PowerOptionsActuator
from .power_profiles_daemon import PowerProfilesDaemonActuator
from .sysfs import SysfsParameterActuator
from ..helper import RootHelperClient


class ActuatorManager:
    def __init__(self, config):
        self.config = config
        self.power_options = PowerOptionsActuator(str(config.get("power_options.binary", "power-daemon-mgr")))
        self.ppd = PowerProfilesDaemonActuator(
            str(config.get("power_profiles_daemon.binary", "powerprofilesctl"))
        )
        self.direct_parameters = SysfsParameterActuator()
        helper_path = Path(str(config.get("helper.socket", "/run/sp7-powerlab/helper.sock")))
        self.helper = RootHelperClient(helper_path)

    def parameter_actuator(self):
        if bool(self.config.get("helper.enabled", True)) and self.helper.available():
            return self.helper
        return self.direct_parameters

    def selected_backend(self) -> str:
        configured = str(self.config.get("policy.actuator", "auto"))
        if configured != "auto":
            if configured not in {"power-options", "power-profiles-daemon", "sysfs"}:
                raise ActuatorError(f"Unsupported policy.actuator: {configured}")
            return configured
        if self.power_options.available():
            return "power-options"
        if self.ppd.available():
            return "power-profiles-daemon"
        return "sysfs"

    def inspect(self) -> dict[str, Any]:
        return {
            "power_options": self.power_options.inspect(),
            "power_profiles_daemon": self.ppd.inspect(),
            "root_helper": {
                "enabled": bool(self.config.get("helper.enabled", True)),
                "socket": str(self.helper.socket_path),
                "available": self.helper.available(),
                "parameter_backend": (
                    "root-helper"
                    if self.parameter_actuator() is self.helper
                    else "direct-sysfs"
                ),
            },
            "selected_backend": self.selected_backend(),
        }

    def backend(self, name: str):
        if name == "power-options":
            return self.power_options
        if name == "power-profiles-daemon":
            return self.ppd
        raise ActuatorError(f"Unknown profile backend: {name}")

    def apply_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        backend_name = str(profile.get("backend") or "")
        if backend_name == "noop":
            return {
                "backend": "noop",
                "profile": profile.get("profile_id"),
                "before": {},
                "after": {},
                "note": "safe no-op profile",
            }
        selected = self.selected_backend()
        if backend_name != selected:
            raise ActuatorError(
                f"Profile backend {backend_name} conflicts with selected writer {selected}"
            )
        if backend_name == "sysfs":
            before: dict[str, Any] = {}
            applied: dict[str, Any] = {}
            parameters = profile.get("parameters") or {}
            try:
                for parameter, value in parameters.items():
                    before[parameter] = self.snapshot_parameter(parameter)
                    applied[parameter] = self.apply_parameter(parameter, value)
            except Exception:
                for parameter, value in reversed(list(before.items())):
                    try:
                        self.restore_parameter(parameter, value)
                    except Exception:
                        pass
                raise
            return {
                "backend": "sysfs",
                "profile": profile.get("profile_id"),
                "before": before,
                "after": applied,
            }
        backend_profile = profile.get("backend_profile")
        if not backend_profile:
            raise ActuatorError(f"Profile {profile.get('profile_id')} has no backend_profile")
        backend = self.backend(backend_name)
        if not backend.available():
            raise ActuatorError(f"Backend unavailable: {backend_name}")
        return backend.apply_profile(str(backend_profile))

    def apply_safe_baseline(self) -> dict[str, Any]:
        selected = self.selected_backend()
        if selected == "power-options" and self.power_options.available():
            return {
                "mode": "safe-baseline",
                "strategy": "reset-power-options-override",
                "result": self.power_options.reset_override(),
            }
        if selected == "power-profiles-daemon" and self.ppd.available():
            return {
                "mode": "safe-baseline",
                "strategy": "ppd-balanced",
                "result": self.ppd.apply_profile("balanced"),
            }
        return {
            "mode": "safe-baseline",
            "strategy": "noop",
            "result": {},
        }

    def parameter_trial_errors(self, parameter: str) -> list[str]:
        if parameter == "profile.id":
            return []
        selected = self.selected_backend()
        if selected != "sysfs":
            return [
                f"Direct parameter trial for {parameter} is blocked while "
                f"policy.actuator={selected}; use external verified profiles or "
                "explicitly select sysfs after disabling competing power writers"
            ]
        return []

    def profile_trial_errors(
        self,
        profile: dict[str, Any],
        *,
        unattended: bool = False,
    ) -> list[str]:
        errors: list[str] = []
        backend_name = str(profile.get("backend") or "")
        selected = self.selected_backend()
        if backend_name not in {selected, "noop"}:
            errors.append(
                f"Candidate profile backend {backend_name} conflicts with selected writer {selected}"
            )
        if profile.get("status") in {"blocked", "deprecated"}:
            errors.append(
                f"Candidate profile {profile.get('profile_id')} is {profile.get('status')}"
            )
        if unattended and not bool(
            (profile.get("evidence") or {}).get("auto_trial_allowed", False)
        ):
            errors.append(
                f"Candidate profile {profile.get('profile_id')} is not enabled for unattended trials"
            )
        return errors

    def snapshot_profile_state(
        self,
        candidate_profile: dict[str, Any],
        *,
        baseline_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        backend_name = str(candidate_profile.get("backend") or "")
        if backend_name == "sysfs":
            parameters = candidate_profile.get("parameters") or {}
            return {
                "kind": "profile",
                "backend": "sysfs",
                "parameters": {
                    parameter: self.snapshot_parameter(parameter)
                    for parameter in parameters
                },
                "baseline_profile_id": (
                    baseline_profile.get("profile_id") if baseline_profile else None
                ),
            }
        if backend_name == "power-profiles-daemon":
            state = self.ppd.inspect()
            return {
                "kind": "profile",
                "backend": backend_name,
                "current_backend_profile": state.get("current_profile"),
                "baseline_profile_id": (
                    baseline_profile.get("profile_id") if baseline_profile else None
                ),
            }
        if backend_name == "power-options":
            return {
                "kind": "profile",
                "backend": backend_name,
                "baseline_profile_id": (
                    baseline_profile.get("profile_id") if baseline_profile else None
                ),
                "note": (
                    "Power Options CLI does not expose the active temporary override; "
                    "rollback reapplies the declared baseline profile or resets the override."
                ),
            }
        if backend_name == "noop":
            return {
                "kind": "profile",
                "backend": "noop",
                "baseline_profile_id": (
                    baseline_profile.get("profile_id") if baseline_profile else None
                ),
            }
        raise ActuatorError(f"Unsupported profile trial backend: {backend_name}")

    def restore_profile_state(
        self,
        snapshot: dict[str, Any],
        *,
        baseline_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        backend_name = str(snapshot.get("backend") or "")
        if backend_name == "sysfs":
            restored: dict[str, Any] = {}
            for parameter, previous in reversed(
                list((snapshot.get("parameters") or {}).items())
            ):
                restored[parameter] = self.restore_parameter(parameter, previous)
            return {"backend": backend_name, "restored": restored}
        if backend_name == "power-profiles-daemon":
            current = snapshot.get("current_backend_profile")
            if current:
                return {
                    "backend": backend_name,
                    "restored": self.ppd.apply_profile(str(current)),
                }
            return self.apply_safe_baseline()
        if backend_name == "power-options":
            if baseline_profile and baseline_profile.get("backend") == "power-options":
                return {
                    "backend": backend_name,
                    "restored": self.apply_profile(baseline_profile),
                }
            return {
                "backend": backend_name,
                "restored": self.power_options.reset_override(),
            }
        if backend_name == "noop":
            return self.apply_safe_baseline()
        raise ActuatorError(f"Unsupported profile snapshot backend: {backend_name}")

    def snapshot_parameter(self, parameter: str) -> Any:
        errors = self.parameter_trial_errors(parameter)
        if errors:
            raise ActuatorError(errors[0])
        return self.parameter_actuator().snapshot(parameter)

    def apply_parameter(self, parameter: str, value: Any) -> dict[str, Any]:
        errors = self.parameter_trial_errors(parameter)
        if errors:
            raise ActuatorError(errors[0])
        return self.parameter_actuator().apply(parameter, value)

    def restore_parameter(self, parameter: str, value: Any) -> dict[str, Any]:
        return self.parameter_actuator().restore(parameter, value)
