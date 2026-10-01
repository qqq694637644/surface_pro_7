from pathlib import Path

import sp7_powerlab.hardware as hardware


def build_fake_hardware(tmp_path: Path):
    sys = tmp_path / "sys"
    proc = tmp_path / "proc"
    dmi = sys / "class/dmi/id"
    dmi.mkdir(parents=True)
    (dmi / "product_name").write_text("Surface Pro 7", encoding="utf-8")
    (dmi / "bios_version").write_text("test-bios", encoding="utf-8")

    cpuinfo = proc / "cpuinfo"
    proc.mkdir(parents=True)
    cpuinfo.write_text(
        "model name : Intel(R) Core(TM) i5-1035G4 CPU @ 1.10GHz\n",
        encoding="utf-8",
    )

    battery = sys / "class/power_supply/BAT0"
    battery.mkdir(parents=True)
    (battery / "status").write_text("Discharging", encoding="utf-8")

    thermal = sys / "class/thermal/thermal_zone0"
    thermal.mkdir(parents=True)
    (thermal / "type").write_text("x86_pkg_temp", encoding="utf-8")
    (thermal / "temp").write_text("45000", encoding="utf-8")

    rapl = sys / "class/powercap/intel-rapl_0"
    rapl.mkdir(parents=True)
    (rapl / "name").write_text("package-0", encoding="utf-8")
    (rapl / "energy_uj").write_text("1000000", encoding="utf-8")

    pstate = sys / "devices/system/cpu/intel_pstate"
    pstate.mkdir(parents=True)
    (pstate / "status").write_text("active", encoding="utf-8")
    (pstate / "max_perf_pct").write_text("60", encoding="utf-8")
    (pstate / "no_turbo").write_text("0", encoding="utf-8")

    policy = sys / "devices/system/cpu/cpufreq/policy0"
    policy.mkdir(parents=True)
    (policy / "energy_performance_preference").write_text("balance_power", encoding="utf-8")
    return sys, proc


def test_surface_contract_accepts_expected_machine(tmp_path, monkeypatch):
    sys, proc = build_fake_hardware(tmp_path)
    monkeypatch.setattr(hardware, "systemd_available", lambda: True)
    monkeypatch.setattr(
        hardware,
        "thermald_status",
        lambda **_kwargs: {"available": True, "active": True, "version": "2"},
    )
    monkeypatch.setattr(hardware, "ownership_conflicts", lambda: [])
    report = hardware.inspect_hardware(sys_root=sys, proc_root=proc)
    assert report.supported_machine is True
    assert report.writable is True
    assert report.capabilities["rapl"] is True


def test_wrong_cpu_blocks_writes(tmp_path, monkeypatch):
    sys, proc = build_fake_hardware(tmp_path)
    (proc / "cpuinfo").write_text("model name : Some Other CPU\n", encoding="utf-8")
    monkeypatch.setattr(hardware, "systemd_available", lambda: True)
    monkeypatch.setattr(
        hardware,
        "thermald_status",
        lambda **_kwargs: {"available": True, "active": True, "version": "2"},
    )
    monkeypatch.setattr(hardware, "ownership_conflicts", lambda: [])
    report = hardware.inspect_hardware(sys_root=sys, proc_root=proc)
    assert report.writable is False
    assert any("hardware contract mismatch" in error for error in report.errors)


def test_conflicting_writer_blocks_writes(tmp_path, monkeypatch):
    sys, proc = build_fake_hardware(tmp_path)
    monkeypatch.setattr(hardware, "systemd_available", lambda: True)
    monkeypatch.setattr(
        hardware,
        "thermald_status",
        lambda **_kwargs: {"available": True, "active": True, "version": "2"},
    )
    monkeypatch.setattr(hardware, "ownership_conflicts", lambda: ["auto-cpufreq.service"])
    report = hardware.inspect_hardware(sys_root=sys, proc_root=proc)
    assert report.writable is False
    assert "auto-cpufreq.service" in report.errors[-1]


def test_arbitrary_acpi_thermal_zone_is_not_cpu_control_sensor(tmp_path, monkeypatch):
    sys, proc = build_fake_hardware(tmp_path)
    zone = sys / "class/thermal/thermal_zone0"
    (zone / "type").write_text("acpitz", encoding="utf-8")
    monkeypatch.setattr(hardware, "systemd_available", lambda: True)
    monkeypatch.setattr(
        hardware,
        "thermald_status",
        lambda **_kwargs: {"available": True, "active": True, "version": "2"},
    )
    monkeypatch.setattr(hardware, "ownership_conflicts", lambda: [])
    report = hardware.inspect_hardware(sys_root=sys, proc_root=proc)
    assert report.capabilities["thermal"] is False
    assert report.thermal_sensor is None
    assert report.writable is False


def test_coretemp_package_sensor_beats_arbitrary_thermal_zone(tmp_path):
    sys, _proc = build_fake_hardware(tmp_path)
    zone = sys / "class/thermal/thermal_zone0"
    (zone / "type").write_text("surface_thermal", encoding="utf-8")

    hwmon = sys / "class/hwmon/hwmon0"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text("coretemp", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Core 0", encoding="utf-8")
    (hwmon / "temp1_input").write_text("43000", encoding="utf-8")
    (hwmon / "temp2_label").write_text("Package id 0", encoding="utf-8")
    package = hwmon / "temp2_input"
    package.write_text("47000", encoding="utf-8")

    assert hardware.thermal_sensor_path(sys) == package


def test_explicit_pinned_sensor_is_last_safe_fallback(tmp_path):
    sys, _proc = build_fake_hardware(tmp_path)
    zone = sys / "class/thermal/thermal_zone0"
    (zone / "type").write_text("surface_thermal", encoding="utf-8")
    pinned = zone / "temp"
    assert hardware.thermal_sensor_path(sys) is None
    assert (
        hardware.thermal_sensor_path(
            sys,
            "/sys/class/thermal/thermal_zone0/temp",
        )
        == pinned
    )
    assert hardware.thermal_sensor_path(sys, str(tmp_path / "outside")) is None
