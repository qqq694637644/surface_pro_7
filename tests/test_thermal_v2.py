from sp7_powerlab.thermal import ThermalObserver

MACHINE = {
    "calibration": {"valid": True},
    "baselines": {
        "idle_temp_c": 35.0,
        "normal_rapl_p90_w": 5.0,
    },
    "thermal": {
        "soft_temp_c": 65.0,
        "pressure_temp_c": 72.0,
        "slope_reference_c_per_min": 4.0,
        "rapl_reference_w": 10.0,
    },
}

CONFIG = {
    "model": {
        "temperature_weight": 0.40,
        "slope_weight": 0.20,
        "sustained_power_weight": 0.25,
        "throttle_weight": 0.15,
    },
    "state": {
        "warming_enter": 0.35,
        "warming_exit": 0.25,
        "heat_soaked_enter": 0.55,
        "heat_soaked_exit": 0.40,
        "pressure_enter": 0.75,
        "pressure_exit": 0.60,
        "throttling_enter": 0.95,
    },
}


def sample(**changes):
    base = {
        "ts": 1.0,
        "package_temp_c": 40.0,
        "temp_slope_c_per_min": 0.0,
        "rapl_power_60s_w": 2.0,
        "rapl_power_300s_w": 2.0,
        "throttle_delta": 0,
        "frequency_collapse": False,
    }
    base.update(changes)
    return base


def test_cool_state():
    obs = ThermalObserver(MACHINE, CONFIG)
    result = obs.observe(sample())
    assert result["state"] == "COOL"
    assert result["pressure"] < 0.35


def test_rising_hot_sustained_power_enters_pressure():
    obs = ThermalObserver(MACHINE, CONFIG)
    result = obs.observe(
        sample(
            package_temp_c=72.0,
            temp_slope_c_per_min=4.0,
            rapl_power_300s_w=10.0,
        )
    )
    assert result["state"] in {"THERMAL_PRESSURE", "THROTTLING"}


def test_throttle_evidence_always_wins():
    obs = ThermalObserver(MACHINE, CONFIG)
    result = obs.observe(sample(throttle_delta=1))
    assert result["state"] == "THROTTLING"
    assert result["pressure"] >= 0.98


def test_hysteresis_prevents_immediate_cool_from_pressure():
    obs = ThermalObserver(MACHINE, CONFIG)
    obs.state = "THERMAL_PRESSURE"
    result = obs.observe(
        sample(
            package_temp_c=64.0,
            temp_slope_c_per_min=1.0,
            rapl_power_300s_w=7.0,
        )
    )
    assert result["state"] in {"THERMAL_PRESSURE", "HEAT_SOAKED"}


def test_uncalibrated_only_uses_throttle_evidence():
    obs = ThermalObserver({"calibration": {"valid": False}}, CONFIG)
    result = obs.observe(
        sample(
            package_temp_c=90.0,
            temp_slope_c_per_min=10.0,
            rapl_power_300s_w=20.0,
        )
    )
    assert result["pressure"] == 0.0
    assert result["state"] == "COOL"
