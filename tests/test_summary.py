import csv
import json

from sp7_powerlab.cli import TELEMETRY_FIELDS, build_ai_summary, summarize


def write_experiment(tmp_path):
    meta = {
        "schema_version": 1,
        "id": "exp-1",
        "name": "baseline",
        "profile": "baseline",
        "workload": "web",
        "hypothesis": "baseline measurement",
        "started_at": "2026-09-29T00:00:00+00:00",
        "config_files": [],
        "system": {
            "kernel": "6.0-test",
            "dmi": {"is_surface_pro_7": True, "product_name": "Surface Pro 7"},
            "battery": {"present": True, "health_percent": 95.0},
            "cpu": {"epp": "power", "cpu0_idle_states": []},
            "display": {"percent": 30.0},
            "network": {"wifi": {"name": "wlan0"}},
        },
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with (tmp_path / "telemetry.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "timestamp": "2026-09-29T00:00:00+00:00",
                    "battery_percent": 80,
                    "battery_status": "Discharging",
                    "power_w": 5.0,
                    "energy_wh": 30,
                    "load1": 0.1,
                    "cpu0_deep_idle_time_us": 1000000,
                    "brightness_percent": 30,
                    "max_temp_c": 42,
                    "wifi_rx_bytes": 1000000,
                    "wifi_tx_bytes": 500000,
                },
                {
                    "timestamp": "2026-09-29T00:10:00+00:00",
                    "battery_percent": 79,
                    "battery_status": "Discharging",
                    "power_w": 7.0,
                    "energy_wh": 29,
                    "load1": 0.2,
                    "cpu0_deep_idle_time_us": 301000000,
                    "brightness_percent": 30,
                    "max_temp_c": 44,
                    "wifi_rx_bytes": 11000000,
                    "wifi_tx_bytes": 2500000,
                },
            ]
        )


def test_summarize_power(tmp_path):
    write_experiment(tmp_path)
    result = summarize(tmp_path)
    assert result["samples"] == 2
    assert result["duration_seconds"] == 600.0
    assert result["power_w"]["average"] is None
    assert result["power_w"]["median"] == 6.0
    assert result["power_w"]["minimum"] == 5.0
    assert result["power_w"]["maximum"] == 7.0
    assert result["conditions"]["gap_count"] == 1
    assert result["conditions"]["valid_discharge_duration_seconds"] == 0.0
    assert result["conditions"]["average_brightness_percent"] == 30.0
    assert result["conditions"]["average_max_temp_c"] == 43.0
    assert result["conditions"]["wifi_rx_mb_delta"] == 10.0
    assert result["conditions"]["wifi_tx_mb_delta"] == 2.0
    assert result["conditions"]["cpu0_deep_idle_fraction"] == 0.5


def test_summarize_uses_time_weighted_valid_discharge(tmp_path):
    write_experiment(tmp_path)
    path = tmp_path / "telemetry.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-09-29T00:00:00+00:00",
                "battery_status": "Discharging",
                "power_w": 4.0,
            }
        )
        writer.writerow(
            {
                "timestamp": "2026-09-29T00:00:10+00:00",
                "battery_status": "Discharging",
                "power_w": 6.0,
            }
        )
        writer.writerow(
            {
                "timestamp": "2026-09-29T00:00:20+00:00",
                "battery_status": "Discharging",
                "power_w": 8.0,
            }
        )
    result = summarize(tmp_path)
    assert result["power_w"]["average"] == 6.0
    assert result["conditions"]["valid_discharge_duration_seconds"] == 20.0


def test_ai_summary_contains_guardrails(tmp_path, monkeypatch):
    write_experiment(tmp_path)
    result = summarize(tmp_path)
    monkeypatch.setattr("sp7_powerlab.cli.previous_comparable", lambda *_: None)

    summary = build_ai_summary(tmp_path, result)
    assert summary["objective"].startswith("minimize whole-device")
    assert summary["guardrails"]["change_one_primary_variable_per_experiment"] is True
    assert "kernel_command_line" in summary["guardrails"]["do_not_autonomously_change"]
    assert summary["hardware_context"]["dmi"]["is_surface_pro_7"] is True
    assert "cpu0_idle_states" not in summary["hardware_context"]["cpu"]
