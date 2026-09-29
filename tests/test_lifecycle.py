import argparse
import csv
import json

import sp7_powerlab.cli as cli
from sp7_powerlab.proposals import normalize_proposal, proposal_template


def test_finish_archives_proposal_evaluation(tmp_path, monkeypatch):
    experiments = tmp_path / "experiments"
    proposals = tmp_path / "proposals"
    history = tmp_path / "history"
    experiments.mkdir()
    proposals.mkdir()
    history.mkdir()

    monkeypatch.setattr(cli, "EXPERIMENTS", experiments)
    monkeypatch.setattr(cli, "PROPOSALS", proposals)
    monkeypatch.setattr(cli, "HISTORY", history)
    monkeypatch.setattr(cli, "ACTIVE_FILE", tmp_path / ".powerlab-active")
    monkeypatch.setattr(cli, "DB_PATH", tmp_path / "powerlab.sqlite3")

    baseline = {
        "id": "baseline",
        "name": "baseline",
        "profile": "baseline",
        "workload": "web",
        "started_at": "2026-09-29T00:00:00+00:00",
        "finished_at": "2026-09-29T00:15:00+00:00",
        "duration_seconds": 900,
        "samples": 450,
        "battery_statuses": ["Discharging"],
        "power_w": {"average": 6.0, "median": 6.0, "p95": 6.5, "minimum": 5.5, "maximum": 7.0},
        "conditions": {"average_max_temp_c": 44.0},
    }
    (history / "baseline.json").write_text(
        json.dumps({"schema_version": 1, "result": baseline}),
        encoding="utf-8",
    )

    proposal = proposal_template("baseline", "web")
    proposal["title"] = "Try EPP power"
    proposal["rationale"] = "Reduce short-burst CPU energy."
    proposal["change"] = {"parameter": "cpu.epp", "from": "balance_power", "to": "power"}
    proposal["expected_effect"]["average_power_delta_w"] = {"min": -0.6, "max": -0.1}
    proposal["expected_effect"]["confidence"] = "medium"
    proposal["rollback"]["instruction"] = "Restore EPP to balance_power."
    proposal["validation"]["duration_seconds"] = 600
    proposal["validation"]["acceptance"] = {
        "max_average_power_delta_w": -0.05,
        "max_temperature_increase_c": 2.0,
    }
    proposal = normalize_proposal(proposal)
    proposal_path = proposals / f"{proposal['id']}.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    exp_dir = experiments / "candidate"
    exp_dir.mkdir()
    meta = {
        "schema_version": 1,
        "id": "candidate",
        "name": "candidate",
        "profile": "epp-power",
        "workload": "web",
        "hypothesis": proposal["rationale"],
        "notes": "",
        "started_at": "2026-09-29T01:00:00+00:00",
        "config_files": [],
        "proposal_id": proposal["id"],
        "proposal_sha256": "test",
        "system": {
            "kernel": "6.0-test",
            "dmi": {"is_surface_pro_7": True},
            "battery": {"present": True},
            "cpu": {"epp": "power", "cpu0_idle_states": []},
            "display": {"percent": 30.0},
            "network": {"wifi": {"name": "wlan0"}},
        },
    }
    (exp_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with (exp_dir / "telemetry.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cli.TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-09-29T01:00:00+00:00",
                "battery_status": "Discharging",
                "power_w": 5.4,
                "max_temp_c": 43.0,
            }
        )
        writer.writerow(
            {
                "timestamp": "2026-09-29T01:10:00+00:00",
                "battery_status": "Discharging",
                "power_w": 5.6,
                "max_temp_c": 44.0,
            }
        )
    cli.ACTIVE_FILE.write_text(str(exp_dir), encoding="utf-8")

    assert cli.cmd_finish(argparse.Namespace()) == 0

    archived = json.loads((history / "candidate.json").read_text(encoding="utf-8"))
    evaluation = archived["proposal_evaluation"]
    assert evaluation["observed"]["average_power_delta_w"] == -0.5
    assert evaluation["checks"]["average_power_delta"] is True
    assert evaluation["checks"]["temperature_delta"] is True
    assert evaluation["checks"]["duration"] is True
    assert evaluation["criteria_status"] == "criteria-met"
    assert evaluation["review_status"] == "pending-human-review"
