import pytest

from sp7_powerlab.config import load_config
from sp7_powerlab.orchestrator import HourlyOrchestrator, OrchestratorError, validate_decision
from sp7_powerlab.proposals import proposal_template
from sp7_powerlab.storage import Database


class Knowledge:
    def build_pack(self):
        return {"pack_sha256": "abc", "hello": "world"}


class Trials:
    def rollback(self, trial_id=None, reason=""):
        return {"trial_id": trial_id, "state": "ROLLED_BACK"}

    def promote(self, trial_id, profile_id=None):
        return {"profile_id": profile_id or "p"}

    def validate_proposal(self, proposal, current_context=None, unattended=False):
        return []

    def start(self, proposal, current_context=None, unattended=False):
        return {"trial_id": "t1", "state": "MEASURING"}

    def queue(self, proposal, unattended=False):
        return {"trial_id": "tq", "state": "WAITING_FOR_CONTEXT"}


class LowBatteryTrials(Trials):
    def validate_proposal(self, proposal, current_context=None, unattended=False):
        if current_context and current_context.get("battery_pct", 100) <= 15:
            return ["battery below trial threshold (10% <= 15%)"]
        return []


def test_decision_schema_rejects_unknown_action():
    errors = validate_decision({"action": "DO_MAGIC", "reason": "x", "payload": {}})
    assert errors


def test_no_change_is_recorded(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    try:
        orchestrator = HourlyOrchestrator(config, db, Knowledge(), Trials())
        result = orchestrator.apply_decision(
            {"action": "NO_CHANGE", "reason": "no evidence", "payload": {}}
        )
        assert result["executed"] is True
    finally:
        db.close()


def valid_trial_proposal():
    proposal = proposal_template("", "reading")
    proposal["title"] = "Try EPP power"
    proposal["rationale"] = "Test one bounded EPP change."
    proposal["change"] = {
        "parameter": "cpu.epp",
        "from": "balance_power",
        "to": "power",
    }
    proposal["expected_effect"]["confidence"] = "medium"
    return proposal


def test_proposal_is_saved_but_not_started_without_approval(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    try:
        orchestrator = HourlyOrchestrator(config, db, Knowledge(), Trials())
        result = orchestrator.apply_decision(
            {
                "action": "PROPOSE_TRIAL",
                "reason": "enough reading data",
                "payload": {"proposal": valid_trial_proposal()},
            },
            current_context={"scene": "reading", "confidence": 0.9},
        )
        assert result["executed"] is False
        assert result["result"]["proposal_status"] == "validated_waiting_for_approval"
        assert list((tmp_path / "proposals").glob("*.json"))
    finally:
        db.close()


def test_human_approved_proposal_can_start(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    try:
        orchestrator = HourlyOrchestrator(config, db, Knowledge(), Trials())
        result = orchestrator.apply_decision(
            {
                "action": "PROPOSE_TRIAL",
                "reason": "approved bounded experiment",
                "payload": {
                    "proposal": valid_trial_proposal(),
                    "human_approved": True,
                },
            },
            current_context={"scene": "reading", "confidence": 0.9},
        )
        assert result["executed"] is True
        assert result["result"]["state"] == "MEASURING"
    finally:
        db.close()


def test_human_approved_proposal_queues_when_battery_is_too_low(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "db.sqlite3")
    try:
        orchestrator = HourlyOrchestrator(
            config, db, Knowledge(), LowBatteryTrials()
        )
        result = orchestrator.apply_decision(
            {
                "action": "PROPOSE_TRIAL",
                "reason": "approved but wait for battery conditions",
                "payload": {
                    "proposal": valid_trial_proposal(),
                    "human_approved": True,
                },
            },
            current_context={
                "scene": "reading",
                "confidence": 0.9,
                "battery_status": "Discharging",
                "battery_pct": 10,
            },
        )
        assert result["executed"] is True
        assert result["result"]["state"] == "WAITING_FOR_CONTEXT"
        assert "battery below trial threshold" in result["result"]["waiting_reasons"][0]
    finally:
        db.close()
