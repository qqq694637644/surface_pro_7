import argparse
import json

import sp7_powerlab.cli as cli


def write_history(path, exp_id, avg_w, finished_at, decision=None):
    payload = {
        "result": {
            "id": exp_id,
            "name": exp_id,
            "profile": "test",
            "workload": "web",
            "hypothesis": "test",
            "finished_at": finished_at,
            "power_w": {"average": avg_w},
            "conditions": {"average_max_temp_c": 42.0},
        },
        "ai_summary": {"caveats": []},
        "experiment_meta": {"config_files": []},
        "proposal": None,
        "proposal_evaluation": None,
    }
    if decision:
        payload["human_evaluation"] = decision
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_ai_pack_keeps_human_feedback_and_orders_newest(tmp_path, monkeypatch):
    history = tmp_path / "history"
    history.mkdir()
    monkeypatch.setattr(cli, "HISTORY", history)

    write_history(
        history / "old.json",
        "old",
        6.2,
        "2026-09-29T01:00:00+00:00",
    )
    write_history(
        history / "new.json",
        "new",
        5.8,
        "2026-09-29T02:00:00+00:00",
        {"decision": "rejected", "responsiveness_1_to_5": 2},
    )

    pack = cli.build_ai_pack(limit=10, workload="web")

    assert pack["included_count"] == 2
    assert pack["experiments_newest_first"][0]["id"] == "new"
    assert pack["experiments_newest_first"][0]["human_evaluation"]["decision"] == "rejected"
    assert pack["rules"]["respect_human_rejections"] is True
    assert pack["next_proposal_template"]["based_on_experiment"] == "new"


def test_decision_updates_history_record(tmp_path, monkeypatch):
    history = tmp_path / "history"
    history.mkdir()
    monkeypatch.setattr(cli, "HISTORY", history)

    path = history / "candidate.json"
    path.write_text(
        json.dumps(
            {
                "result": {"id": "candidate"},
                "proposal_evaluation": {"review_status": "pending-human-review"},
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        experiment="candidate",
        status="rejected",
        responsiveness=2,
        stability="good",
        suspend_wake="untested",
        notes="Power improved but scrolling felt sluggish.",
    )
    assert cli.cmd_decision(args) == 0

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["human_evaluation"]["decision"] == "rejected"
    assert updated["proposal_evaluation"]["review_status"] == "human-rejected"
