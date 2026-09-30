from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.experiments import TrialManager
from sp7_powerlab.llm import apply_decision, build_knowledge_pack
from sp7_powerlab.storage import Database


class FakeActuator:
    def snapshot(self):
        return {"epp": {}, "max_perf_pct": 60, "turbo": True}

    def apply_envelope(self, env):
        return {"after": env}

    def restore(self, snapshot):
        return {"after": snapshot}


def add_sample(db):
    db.add_sample(
        {
            "ts": 100.0,
            "wall_ts": "100",
            "battery_status": "Discharging",
            "battery_pct": 80,
            "battery_power_w": 5.2,
            "battery_energy_wh": 30,
            "battery_epoch": 1,
            "brightness_pct": 40,
            "cpu_usage": 10,
            "cpu_psi": 0.1,
            "io_psi": 0.1,
            "memory_psi": 0.0,
            "load1": 0.2,
            "avg_freq_khz": 1000000,
            "epp": "balance_power",
            "max_perf_pct": 60,
            "turbo": True,
            "package_temp_c": 45,
            "temp_slope_c_per_min": 0.1,
            "rapl_power_10s_w": 2,
            "rapl_power_60s_w": 2,
            "rapl_power_300s_w": 2,
            "user_active": True,
            "media_playing": False,
            "network_rx_mbps": 0.1,
            "network_tx_mbps": 0.1,
            "demand_region": "ACTIVE",
            "latency_need": "MEDIUM",
            "local_compute_pressure": "LOW",
            "network_intensity": "LOW",
            "remote_hint": 0.0,
            "thermal_state": "COOL",
            "thermal_pressure": 0.1,
            "current_envelope": "INTERACTIVE_EFFICIENT",
            "thermal_override": False,
            "trial_id": None,
            "trial_arm": None,
            "thermald_active": True,
            "resume_grace": False,
        }
    )


def test_pack_says_battery_power_is_primary_reward(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        # Use current time so it falls inside the history window.
        import time

        add_sample(db)
        db.conn.execute("UPDATE samples SET ts=?", (time.time(),))
        db.conn.commit()
        pack = build_knowledge_pack(config, db, registry)
        assert pack["rules"]["battery_power_is_primary_reward"] is True
        assert pack["rules"]["prefer_waste_elimination_before_performance_restriction"] is True
    finally:
        db.close()


def test_unapproved_trial_proposal_is_saved_for_review(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    trials = TrialManager(config, db, registry, FakeActuator())
    try:
        decision = {
            "action": "PROPOSE_ENVELOPE_TRIAL",
            "reason": "test lower max perf",
            "payload": {
                "proposal": {
                    "kind": "envelope",
                    "baseline_envelope": "INTERACTIVE_EFFICIENT",
                    "changes": {"max_perf_pct": 50},
                }
            },
        }
        result = apply_decision(
            decision,
            config=config,
            db=db,
            registry=registry,
            trials=trials,
        )
        assert result["executed"] is False
        assert result["status"] == "awaiting_human_approval"
        assert Path(result["proposal_file"]).exists()
    finally:
        db.close()


def test_no_change_is_recorded_without_execution(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    trials = TrialManager(config, db, registry, FakeActuator())
    try:
        result = apply_decision(
            {"action": "NO_CHANGE", "reason": "nothing worth changing"},
            config=config,
            db=db,
            registry=registry,
            trials=trials,
        )
        assert result["executed"] is False
        assert result["status"] == "recorded"
    finally:
        db.close()


def test_human_approved_field_is_not_trusted(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    trials = TrialManager(config, db, registry, FakeActuator())
    try:
        try:
            apply_decision(
                {
                    "action": "PROPOSE_ENVELOPE_TRIAL",
                    "reason": "model cannot approve itself",
                    "human_approved": True,
                    "payload": {},
                },
                config=config,
                db=db,
                registry=registry,
                trials=trials,
            )
        except Exception as exc:
            assert "unsupported decision fields" in str(exc)
        else:
            raise AssertionError("LLM-provided approval must be rejected")
    finally:
        db.close()


def test_level_two_llm_path_can_only_save_for_human_review(project_root: Path):
    config_path = project_root / "config/powerlab.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("level = 1", "level = 2"),
        encoding="utf-8",
    )
    config = load_config(project_root)
    db = Database(project_root / "runtime/db.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    trials = TrialManager(config, db, registry, FakeActuator())
    try:
        result = apply_decision(
            {
                "action": "PROPOSE_ENVELOPE_TRIAL",
                "reason": "approved test",
                "payload": {
                    "proposal": {
                        "kind": "envelope",
                        "baseline_envelope": "INTERACTIVE_EFFICIENT",
                        "changes": {"max_perf_pct": 50},
                    }
                },
            },
            config=config,
            db=db,
            registry=registry,
            trials=trials,
        )
        assert result["executed"] is False
        assert result["status"] == "awaiting_human_approval"
        assert result["required_level_for_autonomous_trial"] == 3
        assert Path(result["proposal_file"]).exists()
    finally:
        db.close()
