from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from sp7_powerlab import agent_cli, cli
from sp7_powerlab.llm import ALLOWED_ACTIONS


def test_agent_cli_structured_surface_excludes_privileged_commands():
    help_text = agent_cli.parser().format_help()
    assert "observe" in help_text
    assert "hourly" in help_text
    assert "submit-decision" in help_text
    assert "root-helper" not in help_text
    assert "trial start" not in help_text
    assert "trial promote" not in help_text

    with pytest.raises(SystemExit):
        agent_cli.parser().parse_args(["trial", "start", "proposal.json"])
    with pytest.raises(SystemExit):
        agent_cli.parser().parse_args(["root-helper", "serve"])
    with pytest.raises(SystemExit):
        agent_cli.parser().parse_args(["--config", "other.toml", "hourly"])
    with pytest.raises(SystemExit):
        agent_cli.parser().parse_args(["hourly", "--output", "anywhere.json"])


def test_human_cli_has_no_llm_approve_flag():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["llm-apply", "decision.json", "--approve"])


def test_packaged_trial_schema_matches_repository_contract():
    root = Path(__file__).resolve().parents[1]
    repository = json.loads(
        (root / "schemas/envelope-trial.schema.json").read_text(encoding="utf-8")
    )
    packaged = json.loads(
        files("sp7_powerlab.schemas")
        .joinpath("envelope-trial.schema.json")
        .read_text(encoding="utf-8")
    )
    assert packaged == repository


def test_llm_decision_schema_matches_runtime_actions():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas/llm-decision.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]["action"]["enum"]) == ALLOWED_ACTIONS


def test_root_helper_has_no_sys_admin_capability():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "systemd/sp7-powerlab-root-helper.service.in").read_text(encoding="utf-8")
    assert "CAP_SYS_ADMIN" not in unit
    assert "CapabilityBoundingSet=" in unit
    assert "AmbientCapabilities=" in unit


def test_agent_decision_path_is_confined_to_runtime(project_root: Path):
    inside = agent_cli._runtime_decision_path(
        "runtime/llm-decision.json",
        root=project_root,
    )
    assert inside == (project_root / "runtime/llm-decision.json").resolve()

    with pytest.raises(SystemExit, match="runtime directory"):
        agent_cli._runtime_decision_path("../outside.json", root=project_root)
