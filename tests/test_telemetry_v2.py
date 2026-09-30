from pathlib import Path

import sp7_powerlab.telemetry as telemetry
from sp7_powerlab.config import load_config


def build_fs(tmp_path: Path):
    sys = tmp_path / "sys"
    proc = tmp_path / "proc"
    bat = sys / "class/power_supply/BAT0"
    bat.mkdir(parents=True)
    for name, value in {
        "status": "Discharging",
        "capacity": "50",
        "power_now": "-6000000",
        "energy_now": "20000000",
        "energy_full": "40000000",
        "energy_full_design": "43200000",
        "voltage_now": "7600000",
        "current_now": "800000",
        "model_name": "TEST",
        "manufacturer": "TEST",
        "serial_number": "123",
    }.items():
        (bat / name).write_text(value, encoding="utf-8")

    thermal = sys / "class/thermal/thermal_zone0"
    thermal.mkdir(parents=True)
    (thermal / "type").write_text("x86_pkg_temp", encoding="utf-8")
    (thermal / "temp").write_text("45000", encoding="utf-8")

    rapl = sys / "class/powercap/intel-rapl_0"
    rapl.mkdir(parents=True)
    (rapl / "name").write_text("package-0", encoding="utf-8")
    (rapl / "energy_uj").write_text("1000000", encoding="utf-8")
    (rapl / "max_energy_range_uj").write_text("100000000", encoding="utf-8")

    pstate = sys / "devices/system/cpu/intel_pstate"
    pstate.mkdir(parents=True)
    (pstate / "max_perf_pct").write_text("60", encoding="utf-8")
    (pstate / "min_perf_pct").write_text("10", encoding="utf-8")
    (pstate / "no_turbo").write_text("0", encoding="utf-8")

    policy = sys / "devices/system/cpu/cpufreq/policy0"
    policy.mkdir(parents=True)
    (policy / "scaling_cur_freq").write_text("1200000", encoding="utf-8")
    (policy / "energy_performance_preference").write_text("balance_power", encoding="utf-8")

    backlight = sys / "class/backlight/test"
    backlight.mkdir(parents=True)
    (backlight / "brightness").write_text("50", encoding="utf-8")
    (backlight / "max_brightness").write_text("100", encoding="utf-8")

    (proc / "pressure").mkdir(parents=True)
    for name in ("cpu", "io", "memory"):
        (proc / "pressure" / name).write_text(
            "some avg10=0.10 avg60=0.05 avg300=0.01 total=1\n",
            encoding="utf-8",
        )
    (proc / "loadavg").write_text("0.20 0.10 0.05 1/100 1\n", encoding="utf-8")
    (proc / "net").mkdir()
    (proc / "net/dev").write_text(
        "Inter-| Receive | Transmit\n face |bytes |bytes\n"
        "eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n",
        encoding="utf-8",
    )
    return sys, proc, rapl, thermal


def test_battery_negative_power_is_normalized(tmp_path):
    sys, _proc, _rapl, _thermal = build_fs(tmp_path)
    result = telemetry._battery_snapshot(sys)
    assert result["power_w"] == 6.0
    assert result["energy_wh"] == 20.0


def test_collector_derives_rapl_and_temperature_slope(project_root, tmp_path, monkeypatch):
    sys, proc, rapl, thermal_zone = build_fs(tmp_path)
    config = load_config(project_root)
    now = {"value": 100.0}
    monkeypatch.setattr(telemetry, "_media_playing", lambda: False)
    monkeypatch.setattr(telemetry, "_user_active", lambda: True)
    monkeypatch.setattr(telemetry, "_thermald_active", lambda: True)
    monkeypatch.setattr(telemetry, "_activitywatch", lambda _config: {})
    monkeypatch.setattr(telemetry, "_gpu_snapshot", lambda _root: {})

    collector = telemetry.TelemetryCollector(
        config,
        sys_root=sys,
        proc_root=proc,
        clock=lambda: now["value"],
    )
    first = collector.sample()
    assert first["battery_power_w"] == 6.0

    now["value"] = 130.0
    (rapl / "energy_uj").write_text("151000000", encoding="utf-8")
    (thermal_zone / "temp").write_text("47000", encoding="utf-8")
    second = collector.sample()
    assert second["rapl_power_60s_w"] is not None
    assert second["rapl_power_60s_w"] > 0
    assert second["temp_slope_c_per_min"] is not None
    assert second["brightness_pct"] == 50.0


def test_suspend_gap_enters_resume_grace(project_root, tmp_path, monkeypatch):
    sys, proc, _rapl, _thermal = build_fs(tmp_path)
    config = load_config(project_root)
    now = {"value": 100.0}
    monkeypatch.setattr(telemetry, "_media_playing", lambda: False)
    monkeypatch.setattr(telemetry, "_user_active", lambda: True)
    monkeypatch.setattr(telemetry, "_thermald_active", lambda: True)
    monkeypatch.setattr(telemetry, "_activitywatch", lambda _config: {})
    monkeypatch.setattr(telemetry, "_gpu_snapshot", lambda _root: {})
    collector = telemetry.TelemetryCollector(
        config, sys_root=sys, proc_root=proc, clock=lambda: now["value"]
    )
    collector.sample()
    now["value"] = 200.0
    second = collector.sample()
    assert second["resume_grace"] is True
