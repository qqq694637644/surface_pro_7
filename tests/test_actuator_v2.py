from pathlib import Path

import pytest

from sp7_powerlab.actuators.base import ActuatorError
from sp7_powerlab.actuators.hwp import HWPActuator


def make_sysfs(tmp_path: Path):
    pstate = tmp_path / "devices/system/cpu/intel_pstate"
    policy = tmp_path / "devices/system/cpu/cpufreq/policy0"
    pstate.mkdir(parents=True)
    policy.mkdir(parents=True)
    (pstate / "max_perf_pct").write_text("60", encoding="utf-8")
    (pstate / "no_turbo").write_text("0", encoding="utf-8")
    (policy / "energy_performance_preference").write_text("balance_power", encoding="utf-8")
    return pstate, policy


def test_hwp_apply_and_readback(tmp_path):
    pstate, policy = make_sysfs(tmp_path)
    actuator = HWPActuator(tmp_path)
    result = actuator.apply_values(epp="power", max_perf_pct=40, turbo=False)
    assert result["after"]["max_perf_pct"] == 40
    assert result["after"]["turbo"] is False
    assert (policy / "energy_performance_preference").read_text() == "power"
    assert (pstate / "no_turbo").read_text() == "1"


def test_hwp_restore_rejects_forged_path(tmp_path):
    _pstate, policy = make_sysfs(tmp_path)
    actuator = HWPActuator(tmp_path)
    snap = actuator.snapshot()
    snap["epp"][str(tmp_path / "evil")] = "power"
    with pytest.raises(ActuatorError, match="unauthorized"):
        actuator.restore(snap)
    assert (policy / "energy_performance_preference").read_text() == "balance_power"


@pytest.mark.parametrize("max_perf", [0, 9, 101, 500])
def test_hwp_bounds(max_perf):
    with pytest.raises(ActuatorError):
        HWPActuator.validate_values("power", max_perf, True)
