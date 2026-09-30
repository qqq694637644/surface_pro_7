from __future__ import annotations

from pathlib import Path

import pytest

POWERLAB = """[collector]
sample_seconds = 10
process_seconds = 30
rollup_seconds = 60
max_gap_seconds = 45
top_processes = 5

[controller]
minimum_dwell_seconds = 0
resume_grace_seconds = 45
low_battery_percent = 15
waste_min_baselines = 3
waste_relative_threshold = 0.20
waste_absolute_threshold_w = 0.5

[calibration]
cold_idle_min_seconds = 0
normal_interactive_min_seconds = 0
media_min_seconds = 0
bounded_burst_min_seconds = 0

[experiments]
min_block_seconds = 20
named_min_block_seconds = 40
settle_seconds = 0
max_brightness_delta = 10
min_power_saving_w = 0.10
max_cpu_psi_delta = 2.0
max_io_psi_delta = 2.0
max_thermal_pressure_delta = 0.10
max_media_drop = 0.05
max_sustained_compute_delta = 0.10

[automation]
level = 1
auto_promote = false

[storage]
database = "runtime/powerlab.sqlite3"

[activity]
enabled = false

[helper]
enabled = false
socket = "runtime/helper.sock"
"""

MACHINE = """[identity]
expected_product = "Surface Pro 7"
expected_cpu_substring = "i5-1035G4"

[calibration]
valid = true
version = 1
completed_phases = ["cold_idle", "normal_interactive", "media", "bounded_burst"]

[battery]
active_epoch = 1

[baselines]
idle_battery_w = 3.5
interactive_battery_w = 5.5
media_battery_w = 6.0
idle_temp_c = 35.0
interactive_temp_p90_c = 55.0
normal_rapl_p90_w = 5.0

[thermal]
soft_temp_c = 65.0
pressure_temp_c = 72.0
slope_reference_c_per_min = 4.0
rapl_reference_w = 10.0
cooldown_reference_c_per_min = 2.0
"""

THERMAL = """[model]
temperature_weight = 0.40
slope_weight = 0.20
sustained_power_weight = 0.25
throttle_weight = 0.15

[state]
warming_enter = 0.35
warming_exit = 0.25
heat_soaked_enter = 0.55
heat_soaked_exit = 0.40
pressure_enter = 0.75
pressure_exit = 0.60
throttling_enter = 0.95

[bootstrap]
abort_temp_c = 80
max_burst_seconds = 90
"""

ENVELOPES = """[envelopes.ECO_IDLE]
status = "VERIFIED"
epp = "power"
max_perf_pct = 30
turbo = false

[envelopes.INTERACTIVE_EFFICIENT]
status = "VERIFIED"
epp = "balance_power"
max_perf_pct = 60
turbo = true

[envelopes.REMOTE_EFFICIENT]
status = "VERIFIED"
epp = "balance_power"
max_perf_pct = 50
turbo = true

[envelopes.MEDIA_EFFICIENT]
status = "VERIFIED"
epp = "power"
max_perf_pct = 45
turbo = true

[envelopes.THERMAL_SAFE]
status = "VERIFIED"
epp = "power"
max_perf_pct = 30
turbo = false
"""


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "powerlab.toml").write_text(POWERLAB, encoding="utf-8")
    (config / "machine.toml").write_text(MACHINE, encoding="utf-8")
    (config / "thermal.toml").write_text(THERMAL, encoding="utf-8")
    (config / "envelopes.toml").write_text(ENVELOPES, encoding="utf-8")
    return tmp_path
