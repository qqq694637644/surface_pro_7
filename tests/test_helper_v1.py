import pytest

import sp7_powerlab.actuators.sysfs as sysfs_module
from sp7_powerlab.actuators.base import ActuatorError
from sp7_powerlab.actuators.sysfs import SysfsParameterActuator
from sp7_powerlab.helper import RootHelperProtocolError, RootHelperServer


class FakeParameterActuator:
    def __init__(self):
        self.value = "balance_power"

    def snapshot(self, parameter):
        return self.value

    def apply(self, parameter, value):
        before = self.value
        self.value = value
        return {"before": before, "after": value}

    def restore(self, parameter, value):
        self.value = value
        return {"after": value}


def test_root_helper_blocks_unknown_parameter(tmp_path):
    server = RootHelperServer(tmp_path / "helper.sock", "nobody")
    server.actuator = FakeParameterActuator()
    with pytest.raises(RootHelperProtocolError):
        server.handle({"action": "apply", "parameter": "kernel.magic", "value": 1})


def test_root_helper_only_applies_valid_bounded_value(tmp_path):
    server = RootHelperServer(tmp_path / "helper.sock", "nobody")
    server.actuator = FakeParameterActuator()
    response = server.handle(
        {"action": "apply", "parameter": "cpu.epp", "value": "power"}
    )
    assert response["ok"] is True
    assert response["result"]["after"] == "power"


def test_sysfs_restore_rejects_forged_paths(tmp_path, monkeypatch):
    policy = tmp_path / "cpufreq" / "policy0"
    policy.mkdir(parents=True)
    epp = policy / "energy_performance_preference"
    epp.write_text("balance_power", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("do-not-touch", encoding="utf-8")

    monkeypatch.setattr(sysfs_module, "CPU_POLICY_ROOT", tmp_path / "cpufreq")
    actuator = SysfsParameterActuator()

    with pytest.raises(ActuatorError, match="unauthorized paths"):
        actuator.restore(
            "cpu.epp",
            {
                str(epp): "power",
                str(outside): "pwned",
            },
        )

    assert epp.read_text(encoding="utf-8") == "balance_power"
    assert outside.read_text(encoding="utf-8") == "do-not-touch"


def test_sysfs_restore_accepts_only_enumerated_cpufreq_nodes(tmp_path, monkeypatch):
    policy = tmp_path / "cpufreq" / "policy0"
    policy.mkdir(parents=True)
    epp = policy / "energy_performance_preference"
    epp.write_text("power", encoding="utf-8")

    monkeypatch.setattr(sysfs_module, "CPU_POLICY_ROOT", tmp_path / "cpufreq")
    actuator = SysfsParameterActuator()
    result = actuator.restore(
        "cpu.epp",
        {str(epp): "balance_power"},
    )
    assert result["after"][str(epp)] == "balance_power"
