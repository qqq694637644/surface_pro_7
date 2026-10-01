from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .envelopes import EnvelopeRegistry
from .storage import Database


@dataclass
class ControllerDecision:
    ts: float
    desired_envelope: str | None
    applied_envelope: str | None
    read_only: bool
    reason: str
    action: str = "NO_CHANGE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "desired_envelope": self.desired_envelope,
            "applied_envelope": self.applied_envelope,
            "read_only": self.read_only,
            "reason": self.reason,
            "action": self.action,
        }


class BatteryLifeController:
    def __init__(
        self,
        config: Config,
        db: Database,
        registry: EnvelopeRegistry,
        actuator: Any,
        *,
        hardware_writable: bool,
        calibration_valid: bool,
        clock=time.time,
    ):
        self.config = config
        self.db = db
        self.registry = registry
        self.actuator = actuator
        self.hardware_writable = hardware_writable
        self.calibration_valid = calibration_valid
        self.clock = clock

    def current_envelope(self) -> str | None:
        return self.db.get_meta("current_envelope")

    @staticmethod
    def _same_hwp_state(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (
            left.get("max_perf_pct") == right.get("max_perf_pct")
            and left.get("turbo") == right.get("turbo")
            and (left.get("epp") or {}) == (right.get("epp") or {})
        )

    def reconcile_actual_state(self, reason: str) -> str | None:
        previous = self.current_envelope()
        try:
            snapshot = self.actuator.snapshot()
        except Exception as exc:
            self.db.set_meta("current_envelope", None)
            self.db.add_control_action(
                action="RECONCILE_HWP",
                envelope=None,
                success=False,
                reason=f"{reason}: {exc}",
            )
            return None
        matched = self.registry.match_verified_snapshot(snapshot, preferred=previous)
        self.db.set_meta("current_envelope", matched)
        self.db.set_meta("last_hwp_reconcile_ts", self.clock())
        if matched != previous:
            self.db.add_control_action(
                action="RECONCILE_HWP",
                envelope=matched,
                success=True,
                reason=(
                    f"{reason}: matched verified envelope"
                    if matched
                    else f"{reason}: actual HWP state is unmanaged"
                ),
                before=snapshot,
            )
        return matched

    def set_override(self, name: str) -> None:
        env = self.registry.get(name)
        if not env:
            raise KeyError(name)
        self.db.set_meta("manual_override", name)

    def clear_override(self) -> None:
        self.db.set_meta("manual_override", None)

    def override(self) -> str | None:
        return self.db.get_meta("manual_override")

    def _desired(self, demand: dict[str, Any], thermal: dict[str, Any]) -> tuple[str, str]:
        state = thermal.get("state")
        if state in {"THROTTLING", "THERMAL_PRESSURE"}:
            return "THERMAL_SAFE", f"thermal state {state}"

        override = self.override()
        if override:
            return override, "manual override"

        if not demand.get("user_active"):
            return "ECO_IDLE", "user idle"

        if float(demand.get("media_continuity") or 0.0) >= 0.5:
            return "MEDIA_EFFICIENT", "media continuity"

        if (
            float(demand.get("remote_hint") or 0.0) >= 0.5
            and demand.get("local_compute_pressure") == "LOW"
        ):
            return "REMOTE_EFFICIENT", "remote interactive demand"

        if state == "HEAT_SOAKED":
            return "INTERACTIVE_EFFICIENT", "heat soaked: no aggressive escalation"

        return "INTERACTIVE_EFFICIENT", "default active demand"

    def _read_only_reason(
        self,
        sample: dict[str, Any],
        desired: str,
    ) -> str | None:
        control_state = self.db.latest_runtime_state("control")
        if control_state:
            state = str(control_state.get("state") or "")
            emergency_thermal_safe = state == "EMERGENCY" and desired == "THERMAL_SAFE"
            if state != "CONTROL_ALLOWED" and not emergency_thermal_safe:
                return (
                    f"control safety state {state}: "
                    f"{control_state.get('reason') or 'control disabled'}"
                )
        required = {
            "battery_power_w": sample.get("battery_power_w"),
            "package_temp_c": sample.get("package_temp_c"),
            "rapl_power_60s_w": sample.get("rapl_power_60s_w"),
            "epp": sample.get("epp"),
            "max_perf_pct": sample.get("max_perf_pct"),
        }
        missing = [key for key, value in required.items() if value is None]
        if missing:
            return "core telemetry missing: " + ", ".join(missing)
        if int(self.config.get("automation.level", 0)) < 1:
            return "automation level is read-only"
        if not self.hardware_writable:
            return "hardware contract is not writable"
        if not self.calibration_valid:
            return "machine calibration is not valid"
        if not sample.get("thermald_active"):
            return "thermald is not active"
        if sample.get("resume_grace"):
            return "resume grace period"
        active_trial = self.db.active_trial()
        if active_trial and active_trial.get("state") not in {
            "WAITING_FOR_COMPARABLE_WINDOW",
            "REVALIDATING",
        }:
            return "trial owns the actuator"
        env = self.registry.get(desired)
        if not env:
            return f"desired envelope missing: {desired}"
        if env.get("status") != "VERIFIED":
            return f"desired envelope is not VERIFIED: {desired}"
        return None

    def step(
        self,
        sample: dict[str, Any],
        demand: dict[str, Any],
        thermal: dict[str, Any],
    ) -> ControllerDecision:
        now = float(sample["ts"])
        desired, reason = self._desired(demand, thermal)
        current = self.current_envelope()
        readonly = self._read_only_reason(sample, desired)
        if readonly:
            decision = ControllerDecision(
                ts=now,
                desired_envelope=desired,
                applied_envelope=current,
                read_only=True,
                reason=readonly,
            )
            self.db.add_controller_state(decision.as_dict())
            return decision

        last_apply = float(self.db.get_meta("last_envelope_apply_ts", 0.0) or 0.0)
        minimum_dwell = float(self.config.get("controller.minimum_dwell_seconds", 60.0))
        if current == desired:
            decision = ControllerDecision(
                ts=now,
                desired_envelope=desired,
                applied_envelope=current,
                read_only=False,
                reason=reason,
            )
            self.db.add_controller_state(decision.as_dict())
            return decision

        thermal_preempt = thermal.get("state") in {"THROTTLING", "THERMAL_PRESSURE"}
        if current and not thermal_preempt and now - last_apply < minimum_dwell:
            decision = ControllerDecision(
                ts=now,
                desired_envelope=desired,
                applied_envelope=current,
                read_only=False,
                reason=f"dwell lock: {reason}",
            )
            self.db.add_controller_state(decision.as_dict())
            return decision

        env = self.registry.get(desired)
        assert env is not None
        before: dict[str, Any] | None = None
        try:
            before = self.actuator.snapshot()
            result = self.actuator.apply_envelope(env)
        except Exception as exc:
            recovery_error: str | None = None
            if before is not None:
                try:
                    actual = self.actuator.snapshot()
                    if not self._same_hwp_state(actual, before):
                        self.actuator.restore(before)
                    verify = self.actuator.snapshot()
                    if not self._same_hwp_state(verify, before):
                        raise RuntimeError("restored HWP state does not match pre-apply snapshot")
                except Exception as recovery_exc:
                    recovery_error = str(recovery_exc)
            matched = self.reconcile_actual_state("controller apply failure")
            failure_reason = str(exc)
            if recovery_error:
                failure_reason += f"; rollback integrity failure: {recovery_error}"
                self.hardware_writable = False
                self.db.set_meta("current_envelope", None)
                self.db.add_runtime_state(
                    "control",
                    "EMERGENCY",
                    "HWP apply failed and exact rollback could not be verified",
                    {
                        "error": str(exc),
                        "rollback_error": recovery_error,
                    },
                )
            self.db.add_control_action(
                action="APPLY_ENVELOPE",
                envelope=desired,
                success=False,
                reason=failure_reason,
                before=before,
            )
            decision = ControllerDecision(
                ts=now,
                desired_envelope=desired,
                applied_envelope=matched,
                read_only=bool(recovery_error),
                reason=f"apply failed: {failure_reason}",
                action="FAILED",
            )
            self.db.add_controller_state(decision.as_dict())
            return decision

        self.db.set_meta("current_envelope", desired)
        self.db.set_meta("last_envelope_apply_ts", now)
        self.db.add_control_action(
            action="APPLY_ENVELOPE",
            envelope=desired,
            success=True,
            reason=reason,
            before=before,
            after=result.get("after"),
        )
        decision = ControllerDecision(
            ts=now,
            desired_envelope=desired,
            applied_envelope=desired,
            read_only=False,
            reason=reason,
            action="APPLIED",
        )
        self.db.add_controller_state(decision.as_dict())
        return decision
