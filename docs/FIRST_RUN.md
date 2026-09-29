# First run on the Surface Pro 7

This checklist establishes a clean battery-life baseline before any tuning.

## Install

```bash
sudo apt install -y python3 python3-venv git
git clone https://github.com/qqq694637644/surface_pro_7.git
cd surface_pro_7
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Do not install several competing power-management daemons at once. Pick one actuator later and keep the experiment layer independent from it.

## Verify telemetry

Unplug AC power and run:

```bash
sp7-powerlab doctor
```

For a useful baseline, confirm Surface Pro 7 DMI detection, a numeric whole-device battery `power_w`, fixed brightness telemetry, thermal data, and Wi-Fi state.

## Freeze test conditions

Keep brightness, browser/version, tabs/workload, Wi-Fi network, kernel, and package versions unchanged across comparable A/B runs. Prefer a discharging battery between roughly 30% and 80%. Use at least 15 minutes for the first useful run.

## Record the baseline

```bash
sp7-powerlab start \
  --name baseline-web \
  --profile baseline \
  --workload web \
  --hypothesis "Untuned reference point at fixed brightness"

sp7-powerlab collect --interval 2 --duration 900
sp7-powerlab finish
```

Each completed experiment contains `meta.json`, `telemetry.csv`, `result.json`, and `ai-summary.json`.

## Snapshot a reviewed power profile

Once Power Options or another actuator is configured, pass the exact config file explicitly:

```bash
sp7-powerlab start \
  --name web-epp-power \
  --profile web-epp-power \
  --workload web \
  --hypothesis "EPP=power lowers average draw without harming browsing" \
  --config-file /path/to/the/reviewed/profile.toml
```

PowerLab copies the file and records its SHA-256. It does not edit or apply the profile.

## Compare

```bash
sp7-powerlab list
sp7-powerlab compare <baseline-id> <candidate-id>
```

Do not accept a candidate from wattage alone. Also check temperature, responsiveness, stability, suspend/wake behavior, and test comparability.

## AI iteration rule

Each experiment should change at most one primary variable. Kernel command line, suspend internals, PCI runtime PM, USB autosuspend, I2C devices, firmware, and Surface-kernel changes remain human-reviewed.
