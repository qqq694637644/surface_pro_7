from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path


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


def test_root_helper_has_no_sys_admin_capability():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "systemd/sp7-powerlab-root-helper.service.in").read_text(encoding="utf-8")
    assert "CAP_SYS_ADMIN" not in unit
    assert "CapabilityBoundingSet=" in unit
    assert "AmbientCapabilities=" in unit


def test_user_service_does_not_restart_forever_on_breaking_schema_mismatch():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "systemd/sp7-powerlab.service.in").read_text(encoding="utf-8")
    assert "RestartPreventExitStatus=78" in unit


def test_fixed_good_runtime_is_a_boot_oneshot_not_a_daemon():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "systemd/sp7-powerlab-fixed.service.in").read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "RemainAfterExit=yes" in unit
    assert "envelope apply-fixed" in unit
    assert "Restart=" not in unit


def test_user_units_do_not_order_after_the_target_that_wants_them():
    root = Path(__file__).resolve().parents[1]
    for name in ("sp7-powerlab.service.in", "sp7-powerlab-fixed.service.in"):
        unit = (root / "systemd" / name).read_text(encoding="utf-8")
        assert "WantedBy=default.target" in unit
        assert "After=default.target" not in unit


def test_user_service_installer_restarts_preserved_runtime_mode():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/install-user-services.sh").read_text(encoding="utf-8")
    assert "restart sp7-powerlab-fixed.service" in script
    assert "is-active --quiet sp7-powerlab-fixed.service" in script
    assert "restart sp7-powerlab.service" in script
    assert "is-active --quiet sp7-powerlab.service" in script
