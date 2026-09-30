from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .actuators.base import ActuatorError
from .profiles import ProfileRegistry

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class PolicyEngine:
    def __init__(self, config, db, registry: ProfileRegistry, actuators):
        self.config = config
        self.db = db
        self.registry = registry
        self.actuators = actuators
        self.rules = self._load_rules(config.root / "config" / "policy.toml")
        self.override_path = config.root / "runtime" / "manual-override"
        self.state_path = config.root / "runtime" / "profile-state.json"
        self.current_profile_id: str | None = None
        self.current_restore_state: dict[str, Any] | None = None
        self.last_switch_ts = 0.0
        self.minimum_switch_interval = 60.0
        self._load_runtime_state()

    @staticmethod
    def _boot_id() -> str | None:
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return None

    @staticmethod
    def _load_rules(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        mapping = data.get("scene") or {}
        return {str(key): str(value) for key, value in mapping.items()}

    def current_profile(self) -> str | None:
        return self.current_profile_id

    def _load_runtime_state(self) -> None:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        saved_boot = payload.get("boot_id")
        current_boot = self._boot_id()
        if saved_boot and current_boot and saved_boot != current_boot:
            self.state_path.unlink(missing_ok=True)
            return
        profile_id = payload.get("profile_id")
        restore = payload.get("restore_state")
        if isinstance(profile_id, str) and profile_id:
            self.current_profile_id = profile_id
        if isinstance(restore, dict):
            self.current_restore_state = restore

    def _persist_runtime_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profile_id": self.current_profile_id,
            "restore_state": self.current_restore_state,
            "boot_id": self._boot_id(),
            "updated_at": time.time(),
        }
        self.state_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def restore_current_profile(self, reason: str = "profile switch") -> dict[str, Any] | None:
        if not self.current_restore_state:
            return None
        restored: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for parameter, previous in reversed(list(self.current_restore_state.items())):
            try:
                restored[parameter] = self.actuators.restore_parameter(parameter, previous)
            except Exception as exc:
                errors[parameter] = str(exc)
        self.current_restore_state = None
        self.current_profile_id = None
        self._persist_runtime_state()
        record = {
            "reason": reason,
            "restored": restored,
            "errors": errors,
            "success": not errors,
        }
        self.db.add_system_event("profile_state_restored", record)
        if errors:
            raise ActuatorError(f"Failed restoring prior sysfs profile: {errors}")
        return record

    def manual_override(self) -> str | None:
        try:
            value = self.override_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def desired_profile(self, sample: dict[str, Any], context: dict[str, Any]) -> str:
        safe = str(self.config.get("policy.safe_profile", "safe-baseline"))
        if self.db.active_trial():
            return self.current_profile_id or safe

        override = self.manual_override()
        if override and self.registry.get(override):
            return override

        temp = sample.get("temp_c")
        if isinstance(temp, (int, float)) and temp >= float(
            self.config.get("policy.thermal_emergency_c", 90.0)
        ):
            return safe

        scene = str(context.get("scene") or "unknown")
        confidence = float(context.get("confidence") or 0.0)
        if confidence < 0.5 or scene in {"unknown", "mixed"}:
            return safe

        promoted = self.db.context_policy(scene)
        candidate = (
            str(promoted["profile_id"])
            if promoted
            else self.rules.get(scene, safe)
        )
        profile = self.registry.get(candidate)
        if not profile or profile.get("status") != "verified":
            return safe
        validated = profile.get("last_validated")
        if isinstance(validated, dict):
            reasons: list[str] = []
            validated_kernel = validated.get("kernel")
            current_kernel = sample.get("kernel")
            if validated_kernel and current_kernel and validated_kernel != current_kernel:
                reasons.append("kernel_changed")
            validated_major = validated.get("app_major_version")
            current_major = (context.get("features") or {}).get(
                "foreground_app_major_version"
            )
            if (
                validated_major is not None
                and current_major is not None
                and validated_major != current_major
            ):
                reasons.append("foreground_app_major_version_changed")
            validated_health = validated.get("battery_health_pct")
            current_health = sample.get("battery_health_pct")
            if (
                isinstance(validated_health, (int, float))
                and isinstance(current_health, (int, float))
                and abs(float(validated_health) - float(current_health)) >= 8.0
            ):
                reasons.append("battery_health_drift")
            if reasons:
                self.registry.set_status(candidate, "needs_revalidation")
                self.db.add_system_event(
                    "profile_needs_revalidation",
                    {
                        "profile_id": candidate,
                        "scene": scene,
                        "reasons": reasons,
                    },
                )
                return safe
        return candidate

    def apply_profile_id(
        self,
        profile_id: str,
        *,
        context_id: str | None = None,
        reason: str,
        force: bool = False,
    ) -> dict[str, Any]:
        profile = self.registry.get(profile_id)
        if not profile:
            raise ActuatorError(f"Profile not found: {profile_id}")
        if not force and profile.get("status") != "verified":
            raise ActuatorError(f"Profile is not verified: {profile_id}")
        before = self.actuators.inspect()
        previous_profile = self.current_profile_id
        if previous_profile and previous_profile != profile_id and self.current_restore_state:
            self.restore_current_profile(reason=f"before switching to {profile_id}")
        try:
            result = (
                self.actuators.apply_safe_baseline()
                if profile_id == str(self.config.get("policy.safe_profile", "safe-baseline"))
                else self.actuators.apply_profile(profile)
            )
            self.current_profile_id = profile_id
            self.current_restore_state = (
                dict(result.get("before") or {})
                if profile.get("backend") == "sysfs"
                else None
            )
            self.last_switch_ts = time.time()
            self._persist_runtime_state()
            record = {
                "profile_id": profile_id,
                "context_id": context_id,
                "reason": reason,
                "before": before,
                "after": result,
                "success": True,
            }
            self.db.add_profile_application(record)
            return record
        except Exception as exc:
            self.current_profile_id = None
            self.current_restore_state = None
            self._persist_runtime_state()
            record = {
                "profile_id": profile_id,
                "context_id": context_id,
                "reason": reason,
                "before": before,
                "after": {},
                "success": False,
                "error": str(exc),
            }
            self.db.add_profile_application(record)
            self.db.add_system_event("profile_apply_failed", record)
            if isinstance(exc, ActuatorError):
                raise
            raise ActuatorError(str(exc)) from exc

    def consider(self, sample: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
        if not bool(self.config.get("automation.auto_switch_verified_profiles", True)):
            return None
        if self.db.active_trial():
            return None
        desired = self.desired_profile(sample, context)
        if desired == self.current_profile_id:
            return None
        if time.time() - self.last_switch_ts < self.minimum_switch_interval:
            return None

        try:
            return self.apply_profile_id(
                desired,
                context_id=context.get("context_id"),
                reason=f"verified policy for {context.get('scene')}",
            )
        except ActuatorError as exc:
            return {
                "profile_id": desired,
                "context_id": context.get("context_id"),
                "reason": "automatic profile switch failed",
                "success": False,
                "error": str(exc),
            }
