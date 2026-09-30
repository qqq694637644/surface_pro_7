import json
import time

from sp7_powerlab.config import load_config
from sp7_powerlab.knowledge import KnowledgeManager
from sp7_powerlab.profiles import ProfileRegistry
from sp7_powerlab.storage import Database


def test_export_git_knowledge_writes_compact_state(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "powerlab.sqlite3")
    registry = ProfileRegistry(tmp_path, db)
    try:
        db.upsert_profile(
            {
                "profile_id": "safe-baseline",
                "backend": "noop",
                "backend_profile": None,
                "parameters": {},
                "status": "verified",
                "evidence": {"kind": "safe"},
            }
        )
        db.set_context_policy(
            "reading",
            "safe-baseline",
            source="test",
            evidence={"verified": True},
        )
        db.add_rejection(
            "reading", "cpu.epp", "performance", "too much power", "test"
        )
        db.add_feedback(
            {
                "decision": "accepted",
                "responsiveness": 5,
                "stability": "good",
                "notes": "fine",
            }
        )
        knowledge = KnowledgeManager(config, db, registry)
        exported = knowledge.export_git_knowledge()
        knowledge_path = tmp_path / "history" / "continuous" / "knowledge.json"
        assert knowledge_path.exists()
        payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
        assert payload["profiles"][0]["profile_id"] == "safe-baseline"
        assert payload["context_policies"][0]["scene"] == "reading"
        assert payload["rejections"][0]["parameter"] == "cpu.epp"
        assert payload["feedback"][0]["decision"] == "accepted"
        assert exported["knowledge_file"] == str(knowledge_path)
        daily = tmp_path / "history" / "continuous" / "daily"
        assert list(daily.glob("*.json"))
    finally:
        db.close()


def test_knowledge_pack_contains_current_proposal_template(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "runtime" / "powerlab.sqlite3")
    registry = ProfileRegistry(tmp_path, db)
    try:
        db.add_sample(
            {
                "ts": time.time(),
                "wall_ts": "now",
                "battery_status": "Discharging",
                "power_w": 5.0,
                "energy_wh": 20.0,
                "battery_pct": 70,
                "cpu_usage": 5,
                "load1": 0.1,
                "freq_khz": 900000,
                "epp": "balance_power",
                "temp_c": 40,
                "brightness_pct": 30,
                "wifi_rx_bytes": 0,
                "wifi_tx_bytes": 0,
                "active_app": "reader",
                "window_title": "",
                "afk": False,
                "context_scene": "reading",
                "context_confidence": 0.9,
                "profile_id": "safe-baseline",
            }
        )
        pack = KnowledgeManager(config, db, registry).build_pack(1)
        assert pack["next_proposal_template"]["context"]["scene"] == "reading"
        assert pack["rules"]["llm_is_not_reward_function"] is True
    finally:
        db.close()
