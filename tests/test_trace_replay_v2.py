from __future__ import annotations

from pathlib import Path

from sp7_powerlab.config import load_config, load_machine, load_thermal_config
from sp7_powerlab.controller import BatteryLifeController
from sp7_powerlab.demand import DemandObserver
from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.storage import Database
from sp7_powerlab.thermal import ThermalObserver


class FakeActuator:
    def __init__(self):
        self.applied: list[str] = []
        self.state = {
            "epp": {"policy0": "balance_power"},
            "max_perf_pct": 60,
            "turbo": True,
        }

    def snapshot(self):
        return {
            "epp": dict(self.state["epp"]),
            "max_perf_pct": self.state["max_perf_pct"],
            "turbo": self.state["turbo"],
        }

    def apply_envelope(self, env):
        before = self.snapshot()
        self.state = {
            "epp": {"policy0": env["epp"]},
            "max_perf_pct": env["max_perf_pct"],
            "turbo": env["turbo"],
        }
        self.applied.append(env["name"])
        return {"before": before, "after": self.snapshot()}

    def restore(self, snapshot):
        self.state = snapshot
        return {"after": snapshot}


def raw_sample(ts: float, **changes):
    base = {
        "ts": ts,
        "battery_status": "Discharging",
        "battery_pct": 80.0,
        "battery_power_w": 5.0,
        "package_temp_c": 42.0,
        "rapl_power_60s_w": 2.0,
        "rapl_power_300s_w": 2.0,
        "temp_slope_c_per_min": 0.0,
        "epp": "balance_power",
        "max_perf_pct": 60.0,
        "thermald_active": True,
        "resume_grace": False,
        "user_active": True,
        "cpu_usage": 5.0,
        "cpu_psi": 0.0,
        "io_psi": 0.0,
        "load1": 0.2,
        "network_rx_mbps": 0.0,
        "network_tx_mbps": 0.0,
        "media_playing": False,
        "processes": [],
        "throttle_delta": 0,
        "frequency_collapse": False,
    }
    base.update(changes)
    return base


def build(project_root: Path, *, writable: bool = True):
    config = load_config(project_root)
    db = Database(project_root / "runtime/trace.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    actuator = FakeActuator()
    controller = BatteryLifeController(
        config,
        db,
        registry,
        actuator,
        hardware_writable=writable,
        calibration_valid=True,
    )
    demand = DemandObserver(machine=load_machine(project_root))
    thermal = ThermalObserver(
        load_machine(project_root),
        load_thermal_config(project_root),
    )
    return db, actuator, controller, demand, thermal


def decide(controller, demand_observer, thermal_observer, sample):
    demand = demand_observer.observe(sample)
    thermal = thermal_observer.observe(sample)
    return controller.step(sample, demand, thermal), demand, thermal


def test_replay_idle_interactive_remote_media_and_thermal_override(project_root):
    db, actuator, controller, demand, thermal = build(project_root)
    try:
        result, _, _ = decide(
            controller,
            demand,
            thermal,
            raw_sample(10, user_active=False),
        )
        assert result.desired_envelope == "ECO_IDLE"

        result, _, _ = decide(
            controller,
            demand,
            thermal,
            raw_sample(20, user_active=True),
        )
        assert result.desired_envelope == "INTERACTIVE_EFFICIENT"

        result, observed, _ = decide(
            controller,
            demand,
            thermal,
            raw_sample(
                30,
                network_rx_mbps=2.0,
                processes=[{"name": "ssh"}],
            ),
        )
        assert observed["remote_hint"] >= 0.5
        assert result.desired_envelope == "REMOTE_EFFICIENT"

        result, _, _ = decide(
            controller,
            demand,
            thermal,
            raw_sample(40, media_playing=True),
        )
        assert result.desired_envelope == "MEDIA_EFFICIENT"

        controller.set_override("ECO_IDLE")
        result, _, observed_thermal = decide(
            controller,
            demand,
            thermal,
            raw_sample(
                50,
                package_temp_c=78.0,
                rapl_power_60s_w=12.0,
                rapl_power_300s_w=12.0,
                temp_slope_c_per_min=5.0,
                throttle_delta=1,
            ),
        )
        assert observed_thermal["state"] == "THROTTLING"
        assert result.desired_envelope == "THERMAL_SAFE"
        assert actuator.applied[-1] == "THERMAL_SAFE"
    finally:
        db.close()


def test_trace_sensor_failure_forces_read_only(project_root):
    db, actuator, controller, demand, thermal = build(project_root)
    try:
        sample = raw_sample(10, battery_power_w=None)
        result, _, _ = decide(controller, demand, thermal, sample)
        assert result.read_only is True
        assert "core telemetry missing" in result.reason
        assert actuator.applied == []
    finally:
        db.close()


def test_trace_thermald_failure_forces_read_only(project_root):
    db, actuator, controller, demand, thermal = build(project_root)
    try:
        sample = raw_sample(10, thermald_active=False)
        result, _, _ = decide(controller, demand, thermal, sample)
        assert result.read_only is True
        assert result.reason == "thermald is not active"
        assert actuator.applied == []
    finally:
        db.close()


def test_trace_wrong_hardware_contract_forces_read_only(project_root):
    db, actuator, controller, demand, thermal = build(
        project_root,
        writable=False,
    )
    try:
        result, _, _ = decide(controller, demand, thermal, raw_sample(10))
        assert result.read_only is True
        assert result.reason == "hardware contract is not writable"
        assert actuator.applied == []
    finally:
        db.close()


def test_trace_frequency_collapse_is_throttling(project_root):
    db, _actuator, _controller, _demand, thermal = build(project_root)
    try:
        observed = thermal.observe(
            raw_sample(
                10,
                frequency_collapse=True,
                cpu_usage=60.0,
            )
        )
        assert observed["state"] == "THROTTLING"
        assert observed["pressure"] >= 0.98
    finally:
        db.close()


def test_trace_short_burst_and_runaway_stay_with_interactive_intent(project_root):
    db, _actuator, controller, demand, thermal = build(project_root)
    try:
        burst = raw_sample(
            10,
            cpu_usage=65.0,
            load1=2.0,
            rapl_power_60s_w=7.0,
            rapl_power_300s_w=4.0,
            package_temp_c=48.0,
            temp_slope_c_per_min=1.0,
        )
        result, observed_demand, observed_thermal = decide(controller, demand, thermal, burst)
        assert observed_demand["latency_need"] == "HIGH"
        assert observed_thermal["state"] in {"COOL", "WARMING"}
        assert result.desired_envelope == "INTERACTIVE_EFFICIENT"

        runaway = raw_sample(
            20,
            cpu_usage=92.0,
            cpu_psi=8.0,
            load1=6.0,
            rapl_power_60s_w=11.0,
            rapl_power_300s_w=8.5,
            package_temp_c=58.0,
            temp_slope_c_per_min=2.0,
            processes=[{"name": "firefox"}],
        )
        result, observed_demand, _ = decide(controller, demand, thermal, runaway)
        assert observed_demand["local_compute_pressure"] == "SUSTAINED"
        assert result.desired_envelope == "INTERACTIVE_EFFICIENT"
    finally:
        db.close()


def test_trace_slow_heat_soak_rapid_heat_and_cooldown(project_root):
    db, _actuator, controller, demand, thermal = build(project_root)
    try:
        soaked = raw_sample(
            10,
            package_temp_c=66.0,
            temp_slope_c_per_min=1.0,
            rapl_power_60s_w=8.5,
            rapl_power_300s_w=9.0,
        )
        result, _, observed = decide(controller, demand, thermal, soaked)
        assert observed["state"] == "HEAT_SOAKED"
        assert result.desired_envelope == "INTERACTIVE_EFFICIENT"

        rapid = raw_sample(
            20,
            package_temp_c=72.0,
            temp_slope_c_per_min=4.0,
            rapl_power_60s_w=10.0,
            rapl_power_300s_w=10.0,
        )
        result, _, observed = decide(controller, demand, thermal, rapid)
        assert observed["state"] == "THERMAL_PRESSURE"
        assert result.desired_envelope == "THERMAL_SAFE"

        cooldown = raw_sample(
            30,
            package_temp_c=54.0,
            temp_slope_c_per_min=-2.0,
            rapl_power_60s_w=4.0,
            rapl_power_300s_w=4.0,
        )
        result, _, observed = decide(controller, demand, thermal, cooldown)
        assert observed["state"] in {"HEAT_SOAKED", "WARMING"}
        assert result.desired_envelope == "INTERACTIVE_EFFICIENT"
    finally:
        db.close()


def test_trace_suspend_resume_grace_blocks_writes(project_root):
    db, actuator, controller, demand, thermal = build(project_root)
    try:
        result, _, _ = decide(
            controller,
            demand,
            thermal,
            raw_sample(10, resume_grace=True),
        )
        assert result.read_only is True
        assert result.reason == "resume grace period"
        assert actuator.applied == []
    finally:
        db.close()
