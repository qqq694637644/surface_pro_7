from __future__ import annotations

from pathlib import Path

from sp7_powerlab.lifecycle import (
    BASELINE_OBSERVATION,
    CALIBRATING,
    CONTROL_ALLOWED,
    EMERGENCY,
    INVESTIGATING,
    READ_ONLY,
    STABLE,
    LifecycleManager,
)
from sp7_powerlab.storage import Database


def test_control_and_learning_states_are_independent(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite3")
    try:
        manager = LifecycleManager(db)
        assert manager.control_state() == READ_ONLY
        assert manager.learning_state() == CALIBRATING

        manager.synchronize_learning(calibration_valid=True)
        assert manager.learning_state() == BASELINE_OBSERVATION

        manager.freeze("enough verified usage coverage")
        assert manager.learning_state() == STABLE

        manager.synchronize_control(
            calibration_valid=True,
            hardware_writable=False,
            thermal_provider_healthy=True,
            core_telemetry_valid=True,
        )
        assert manager.control_state() == READ_ONLY
        assert manager.learning_state() == STABLE

        manager.synchronize_control(
            calibration_valid=True,
            hardware_writable=True,
            thermal_provider_healthy=True,
            core_telemetry_valid=True,
        )
        assert manager.control_state() == CONTROL_ALLOWED
        assert manager.learning_state() == STABLE
    finally:
        db.close()


def test_thermal_emergency_does_not_reset_learning_lifecycle(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite3")
    try:
        manager = LifecycleManager(db)
        manager.synchronize_learning(calibration_valid=True)
        manager.freeze("stable")
        manager.synchronize_control(
            calibration_valid=True,
            hardware_writable=True,
            thermal_provider_healthy=True,
            core_telemetry_valid=True,
            thermal_emergency=True,
        )
        assert manager.control_state() == EMERGENCY
        assert manager.learning_state() == STABLE
    finally:
        db.close()


def test_unexpected_power_investigation_does_not_reopen_learning(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite3")
    try:
        manager = LifecycleManager(db)
        manager.synchronize_learning(calibration_valid=True)
        manager.freeze("stable")
        investigation_id = manager.start_investigation(
            event_id="up-1",
            payload={"trigger": "UNEXPECTED_POWER"},
        )
        assert manager.investigation_state() == INVESTIGATING
        assert manager.learning_state() == STABLE

        manager.finish_investigation(
            investigation_id,
            classification="EXPECTED_WORKLOAD_CHANGE",
            payload={"reason": "large download"},
        )
        assert manager.investigation_state() != INVESTIGATING
        assert manager.learning_state() == STABLE
    finally:
        db.close()
