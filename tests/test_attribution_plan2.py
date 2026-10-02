from pathlib import Path

from sp7_powerlab.attribution import AttributionEngine
from sp7_powerlab.lifecycle import REOPENED, STABLE, LifecycleManager
from sp7_powerlab.storage import Database


def test_attribution_keeps_root_cause_as_hypothesis(tmp_path: Path):
    db = Database(tmp_path / "attribution.sqlite3")
    try:
        event_id = db.add_unexpected_power_event(
            {
                "start_ts": 100.0,
                "severity": "medium",
                "status": "OPEN",
                "classification": "UNEXPECTED_POWER",
                "reason": "high power",
                "avg_rapl_w": 3.2,
                "top_processes": [
                    {"pid": 10, "name": "firefox", "cpu_percent": 35.0},
                ],
            }
        )
        investigation_id = db.start_investigation(
            event_id=event_id,
            payload={"trigger": "UNEXPECTED_POWER"},
        )
        result = AttributionEngine(db).attribute(investigation_id)
        assert result["classification"] == "SUSPECTED_REGRESSION"
        assert result["verification_plan"]
        assert any("firefox" in item for item in result["local_evidence"])
    finally:
        db.close()


def test_only_confirmed_config_regression_reopens_learning(tmp_path: Path):
    db = Database(tmp_path / "lifecycle.sqlite3")
    try:
        manager = LifecycleManager(db)
        manager.set_learning(STABLE, "stable")
        event_id = db.add_unexpected_power_event(
            {
                "start_ts": 100.0,
                "severity": "medium",
                "status": "OPEN",
                "reason": "high power",
            }
        )
        investigation_id = manager.start_investigation(
            event_id=event_id,
            payload={"trigger": "UNEXPECTED_POWER"},
        )
        manager.finish_investigation(
            investigation_id,
            classification="EXPECTED_WORKLOAD_CHANGE",
            payload={"reason": "known transfer"},
        )
        assert manager.learning_state() == STABLE
        assert db.unexpected_power_event(event_id)["status"] == "CLOSED"

        second_event = db.add_unexpected_power_event(
            {
                "start_ts": 200.0,
                "severity": "medium",
                "status": "OPEN",
                "reason": "verified envelope regressed",
            }
        )
        second = manager.start_investigation(
            event_id=second_event,
            payload={"trigger": "UNEXPECTED_POWER"},
        )
        manager.finish_investigation(
            second,
            classification="CONFIRMED_CONFIG_REGRESSION",
            payload={"verification": "reproduced"},
        )
        assert manager.learning_state() == REOPENED
    finally:
        db.close()
