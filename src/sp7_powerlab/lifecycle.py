from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Database

CONTROL_ALLOWED = "CONTROL_ALLOWED"
READ_ONLY = "READ_ONLY"
DEGRADED = "DEGRADED"
EMERGENCY = "EMERGENCY"

CALIBRATING = "CALIBRATING"
BASELINE_OBSERVATION = "BASELINE_OBSERVATION"
COARSE_OPTIMIZATION = "COARSE_OPTIMIZATION"
VALIDATING = "VALIDATING"
STABLE = "STABLE"
REOPENED = "REOPENED"

IDLE = "IDLE"
INVESTIGATING = "INVESTIGATING"


@dataclass
class LifecycleManager:
    db: Database

    def control_state(self) -> str:
        row = self.db.latest_runtime_state("control")
        return str(row["state"]) if row else READ_ONLY

    def learning_state(self) -> str:
        row = self.db.latest_runtime_state("learning")
        return str(row["state"]) if row else CALIBRATING

    def investigation_state(self) -> str:
        return INVESTIGATING if self.db.active_investigation() else IDLE

    def set_control(
        self,
        state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        if state not in {CONTROL_ALLOWED, READ_ONLY, DEGRADED, EMERGENCY}:
            raise ValueError(f"invalid control safety state: {state}")
        if self.control_state() != state:
            self.db.add_runtime_state("control", state, reason, payload)
        return state

    def set_learning(
        self,
        state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        if state not in {
            CALIBRATING,
            BASELINE_OBSERVATION,
            COARSE_OPTIMIZATION,
            VALIDATING,
            STABLE,
            REOPENED,
        }:
            raise ValueError(f"invalid learning lifecycle state: {state}")
        if self.learning_state() != state:
            self.db.add_runtime_state("learning", state, reason, payload)
        return state

    def derive_control_state(
        self,
        *,
        calibration_valid: bool,
        hardware_writable: bool,
        thermal_provider_healthy: bool,
        core_telemetry_valid: bool,
        rollback_integrity_ok: bool = True,
        thermal_emergency: bool = False,
    ) -> tuple[str, str]:
        if thermal_emergency:
            return EMERGENCY, "thermal emergency"
        if not rollback_integrity_ok:
            return EMERGENCY, "controller rollback integrity is not trusted"
        if not thermal_provider_healthy:
            return READ_ONLY, "validated thermal safety provider is unhealthy"
        if not core_telemetry_valid:
            return DEGRADED, "core telemetry is invalid"
        if not calibration_valid:
            return READ_ONLY, "machine calibration is invalid"
        if not hardware_writable:
            return READ_ONLY, "hardware/control ownership is not writable"
        return CONTROL_ALLOWED, "control safety contract satisfied"

    def synchronize_control(self, **conditions: Any) -> str:
        state, reason = self.derive_control_state(**conditions)
        return self.set_control(state, reason, conditions)

    def synchronize_learning(self, *, calibration_valid: bool) -> str:
        current = self.learning_state()
        if not calibration_valid:
            return self.set_learning(CALIBRATING, "calibration invalid")
        if current == CALIBRATING:
            return self.set_learning(
                BASELINE_OBSERVATION,
                "calibration valid; collect measurement/noise baseline",
            )
        return current

    def freeze(self, reason: str = "manual freeze") -> str:
        return self.set_learning(STABLE, reason)

    def reopen(self, reason: str) -> str:
        return self.set_learning(REOPENED, reason)

    def begin_optimization(self, reason: str) -> str:
        return self.set_learning(COARSE_OPTIMIZATION, reason)

    def begin_validation(self, reason: str) -> str:
        return self.set_learning(VALIDATING, reason)

    def start_investigation(
        self,
        *,
        event_id: str | None,
        payload: dict[str, Any],
    ) -> str:
        return self.db.start_investigation(event_id=event_id, payload=payload)

    def finish_investigation(
        self,
        investigation_id: str,
        *,
        classification: str,
        payload: dict[str, Any],
    ) -> None:
        investigation = self.db.investigation(investigation_id)
        if not investigation:
            raise KeyError(investigation_id)
        self.db.finish_investigation(
            investigation_id,
            classification=classification,
            payload=payload,
        )
        event_id = investigation.get("event_id")
        if event_id:
            self.db.close_unexpected_power_event(
                str(event_id),
                classification=classification,
                payload={"investigation_id": investigation_id},
            )
        if classification == "CONFIRMED_CONFIG_REGRESSION":
            self.reopen(f"investigation {investigation_id} confirmed configuration regression")

    def status(self) -> dict[str, Any]:
        return {
            "control_safety_state": self.control_state(),
            "learning_lifecycle": self.learning_state(),
            "investigation_status": self.investigation_state(),
            "active_investigation": self.db.active_investigation(),
        }
