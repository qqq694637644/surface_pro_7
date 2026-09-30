import time

import pytest

from sp7_powerlab.config import load_config
from sp7_powerlab.experiments import TrialError, TrialManager
from sp7_powerlab.profiles import ProfileRegistry
from sp7_powerlab.proposals import normalize_proposal, proposal_template
from sp7_powerlab.storage import Database


class FakeActuators:
    def __init__(self):
        self.value = "balance_power"
        self.restore_calls = 0

    def snapshot_parameter(self, parameter):
        return self.value

    def apply_parameter(self, parameter, value):
        before = self.value
        self.value = value
        return {"parameter": parameter, "before": before, "after": value}

    def restore_parameter(self, parameter, value):
        self.restore_calls += 1
        before = self.value
        self.value = value
        return {"parameter": parameter, "before": before, "after": value}


def make_manager(tmp_path):
    config = load_config(tmp_path)
    config.data["experiment"]["interactive_min_valid_minutes"] = 0.01
    config.data["experiment"]["interactive_min_sessions"] = 1
    db = Database(tmp_path / "runtime" / "powerlab.sqlite3")
    registry = ProfileRegistry(tmp_path, db)
    return config, db, TrialManager(config, db, FakeActuators(), registry)


def proposal(scene="reading", parameter="cpu.epp", before="balance_power", after="power"):
    p = proposal_template("", scene)
    p["title"] = "test"
    p["rationale"] = "test one bounded parameter"
    p["change"] = {"parameter": parameter, "from": before, "to": after}
    p["expected_effect"]["confidence"] = "medium"
    p["validation"]["min_valid_minutes"] = 0.01
    return normalize_proposal(p)


def test_unattended_trial_rejects_class_c_parameter(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        errors = manager.validate_proposal(
            proposal(parameter="wifi.power_save", before=False, after=True),
            current_context={"scene": "reading", "confidence": 0.9},
            unattended=True,
        )
        assert errors
        assert any("not allowed for unattended" in error for error in errors)
    finally:
        db.close()


def test_trial_start_and_manual_rollback(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        trial = manager.start(
            proposal(),
            current_context={"scene": "reading", "confidence": 0.9},
        )
        assert trial["state"] == "SETTLING"
        assert manager.actuators.value == "power"
        rolled = manager.rollback(trial["trial_id"], reason="test")
        assert rolled["state"] == "ROLLED_BACK"
        assert manager.actuators.value == "balance_power"
        assert manager.actuators.restore_calls == 1
    finally:
        db.close()


def test_direct_parameter_trial_captures_current_baseline_profile(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        trial = manager.start(
            proposal(),
            current_context={
                "scene": "reading",
                "confidence": 0.9,
                "battery_status": "Discharging",
                "battery_pct": 70,
                "profile_id": "reading-baseline",
            },
        )
        assert trial["baseline_profile"] == "reading-baseline"
        assert db.get_trial(trial["trial_id"])["baseline_profile"] == "reading-baseline"
        manager.rollback(trial["trial_id"], reason="cleanup")
    finally:
        db.close()


def test_trial_requires_multiple_evidence_not_two_points(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        trial = manager.start(
            proposal(),
            current_context={"scene": "reading", "confidence": 0.9},
        )
        start = db.get_trial(trial["trial_id"])["start_ts"]
        # Even well-spaced-looking samples are not enough without completed sessions/baseline.
        for ts in (start + 130, start + 140):
            db.add_sample(
                {
                    "ts": ts,
                    "wall_ts": "x",
                    "battery_status": "Discharging",
                    "power_w": 5.0,
                    "energy_wh": 10.0,
                    "battery_pct": 50,
                    "cpu_usage": 5,
                    "load1": 0.1,
                    "freq_khz": 1000000,
                    "epp": "power",
                    "temp_c": 40,
                    "brightness_pct": 30,
                    "wifi_rx_bytes": 0,
                    "wifi_tx_bytes": 0,
                    "active_app": "reader",
                    "window_title": "",
                    "afk": False,
                    "context_scene": "reading",
                    "context_confidence": 0.9,
                    "profile_id": None,
                }
            )
        result = manager.evaluate(trial["trial_id"])
        assert result["verdict"] == "INSUFFICIENT_DATA"
    finally:
        db.close()


def test_candidate_winner_requires_fresh_revalidation_evidence(tmp_path, monkeypatch):
    config, db, manager = make_manager(tmp_path)
    config.data["experiment"]["settle_seconds"] = 0
    config.data["experiment"]["revalidation_sessions"] = 1
    config.data["experiment"]["revalidation_min_valid_minutes"] = 0.01
    clock = {"now": 1000.0}
    monkeypatch.setattr("sp7_powerlab.experiments.time.time", lambda: clock["now"])

    def add_sample(ts, power):
        db.add_sample(
            {
                "ts": ts,
                "wall_ts": str(ts),
                "battery_status": "Discharging",
                "power_w": power,
                "energy_wh": 10.0,
                "battery_pct": 50,
                "cpu_usage": 5,
                "load1": 0.1,
                "freq_khz": 1000000,
                "epp": "power",
                "temp_c": 40,
                "brightness_pct": 30,
                "wifi_rx_bytes": 0,
                "wifi_tx_bytes": 0,
                "active_app": "reader",
                "window_title": "",
                "afk": False,
                "context_scene": "reading",
                "context_confidence": 0.9,
                "profile_id": None,
                "process_summary": {"background_cpu_percent": 5},
                "kernel": "test-kernel",
            }
        )

    def add_session(session_id, start, end, avg):
        db.add_session(
            {
                "session_id": session_id,
                "start_ts": start,
                "end_ts": end,
                "context_id": "ctx",
                "scene": "reading",
                "valid_duration_s": end - start,
                "energy_wh": avg * (end - start) / 3600,
                "avg_power_w": avg,
                "sample_count": 2,
                "quality": {"valid": True},
            }
        )

    try:
        add_sample(800, 6.0)
        add_sample(810, 6.0)
        add_sample(820, 6.0)
        add_session("baseline", 800, 820, 6.0)

        p = proposal()
        p["validation"]["min_valid_minutes"] = 0.01
        p["validation"]["acceptance"]["max_average_power_delta_w"] = -0.05
        trial = manager.start(
            p, current_context={"scene": "reading", "confidence": 0.9}
        )
        add_sample(1001, 5.0)
        add_sample(1011, 5.0)
        add_sample(1021, 5.0)
        add_session("candidate-1", 1001, 1021, 5.0)

        clock["now"] = 1900.0
        first = manager.evaluate(trial["trial_id"])
        assert first["verdict"] == "REVALIDATION"
        assert db.get_trial(trial["trial_id"])["state"] == "REVALIDATION"

        waiting = manager.evaluate(trial["trial_id"])
        assert waiting["verdict"] == "INSUFFICIENT_DATA"
        assert db.get_trial(trial["trial_id"])["state"] == "REVALIDATION"

        add_sample(1901, 5.1)
        add_sample(1911, 5.1)
        add_sample(1921, 5.1)
        add_session("candidate-2", 1901, 1921, 5.1)
        clock["now"] = 1930.0
        second = manager.evaluate(trial["trial_id"])
        assert second["verdict"] == "CANDIDATE_WINNER"
        assert second["revalidation_passed"] is True
        assert db.get_trial(trial["trial_id"])["state"] == "CANDIDATE_WINNER"
    finally:
        db.close()


def test_waiting_trial_starts_only_when_context_matches(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        queued = manager.queue(proposal(), unattended=False)
        assert queued["state"] == "WAITING_FOR_CONTEXT"
        assert manager.maybe_start_waiting(
            {"scene": "compile", "confidence": 0.95},
            unattended=False,
        ) is None
        started = manager.maybe_start_waiting(
            {"scene": "reading", "confidence": 0.95},
            unattended=False,
        )
        assert started["state"] == "SETTLING"
        assert manager.actuators.value == "power"
        manager.rollback(started["trial_id"], reason="test cleanup")
        assert manager.actuators.value == "balance_power"
    finally:
        db.close()


def test_unattended_trial_blocks_sensitive_usage_scenes(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        p = proposal(scene="video_call")
        errors = manager.validate_proposal(
            p,
            current_context={
                "scene": "video_call",
                "confidence": 0.95,
                "battery_status": "Discharging",
                "battery_pct": 70,
            },
            unattended=True,
        )
        assert any("blocked for scene video_call" in error for error in errors)
    finally:
        db.close()


def test_trial_waits_when_battery_is_too_low(tmp_path):
    config, db, manager = make_manager(tmp_path)
    try:
        p = proposal(scene="reading")
        errors = manager.validate_proposal(
            p,
            current_context={
                "scene": "reading",
                "confidence": 0.95,
                "battery_status": "Discharging",
                "battery_pct": 10,
            },
            unattended=False,
        )
        assert any("battery below trial threshold" in error for error in errors)
    finally:
        db.close()
