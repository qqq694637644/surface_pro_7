from pathlib import Path

from sp7_powerlab.config import load_config
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.knowledge import build_review_pack
from sp7_powerlab.storage import Database


def test_review_pack_is_read_only_agent_context(project_root: Path):
    config = load_config(project_root)
    db = Database(project_root / "runtime/review-pack.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        pack = build_review_pack(config, db, registry)
        assert pack["rules"]["battery_power_is_primary_reward"] is True
        assert pack["rules"]["agent_not_in_realtime_control"] is True
        assert "net_benefit" in pack
        assert "net_benefit_results" in pack
    finally:
        db.close()
