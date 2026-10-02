from __future__ import annotations

from pathlib import Path

import pytest

from sp7_powerlab.actuators.base import ActuatorError
from sp7_powerlab.helper import HelperProtocolError, RootHelperServer


class FakeActuator:
    def inspect(self):
        return {"available": True}

    def snapshot(self):
        return {
            "epp": {"/sys/fake/policy0": "balance_power"},
            "max_perf_pct": 60,
            "turbo": True,
        }

    def apply_values(self, *, epp, max_perf_pct, turbo):
        if epp not in {"performance", "balance_performance", "balance_power", "power"}:
            raise ActuatorError("bad epp")
        if not 10 <= max_perf_pct <= 100:
            raise ActuatorError("bad max_perf_pct")
        return {
            "after": {
                "epp": epp,
                "max_perf_pct": max_perf_pct,
                "turbo": turbo,
            }
        }

    def restore(self, snapshot):
        return {"after": snapshot}


def server(tmp_path: Path) -> RootHelperServer:
    return RootHelperServer(
        tmp_path / "helper.sock",
        allow_uid=1000,
        actuator=FakeActuator(),
    )


def test_helper_exposes_only_fixed_actions(tmp_path):
    helper = server(tmp_path)
    with pytest.raises(HelperProtocolError, match="unsupported action"):
        helper.dispatch({"action": "shell", "payload": {"command": "id"}})


def test_helper_apply_rejects_extra_fields(tmp_path):
    helper = server(tmp_path)
    with pytest.raises(HelperProtocolError, match="only"):
        helper.dispatch(
            {
                "action": "apply",
                "payload": {
                    "epp": "power",
                    "max_perf_pct": 40,
                    "turbo": False,
                    "path": "/sys/arbitrary",
                },
            }
        )


def test_helper_apply_uses_typed_hwp_values(tmp_path):
    helper = server(tmp_path)
    result = helper.dispatch(
        {
            "action": "apply",
            "payload": {
                "epp": "power",
                "max_perf_pct": 40,
                "turbo": False,
            },
        }
    )
    assert result["after"]["epp"] == "power"
    assert result["after"]["max_perf_pct"] == 40
    assert result["after"]["turbo"] is False


def test_helper_inspect_exposes_protocol_and_implementation_identity(tmp_path):
    result = server(tmp_path).dispatch({"action": "inspect", "payload": {}})
    assert result["protocol_version"] == 2
    assert len(result["implementation_identity"]) == 64
