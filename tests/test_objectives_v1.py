from sp7_powerlab.objectives import compare_objectives, evaluate_window


def media_sample(ts, power, playing=True, temp=42.0, cpu=12.0):
    return {
        "ts": ts,
        "battery_status": "Discharging",
        "power_w": power,
        "context_scene": "media_playback",
        "media": {"playing": playing},
        "temp_c": temp,
        "cpu_usage": cpu,
    }


def test_media_objective_rejects_fake_savings_caused_by_paused_playback():
    baseline = [
        media_sample(0, 7.0, True),
        media_sample(10, 7.0, True),
        media_sample(20, 7.0, True),
    ]
    candidate = [
        media_sample(0, 4.0, False),
        media_sample(10, 4.0, False),
        media_sample(20, 4.0, True),
    ]

    baseline_eval = evaluate_window(
        baseline,
        scene="media_playback",
        max_gap_seconds=30,
        min_valid_seconds=10,
    )
    candidate_eval = evaluate_window(
        candidate,
        scene="media_playback",
        max_gap_seconds=30,
        min_valid_seconds=10,
    )

    assert baseline_eval["objective"]["valid"] is True
    assert candidate_eval["objective"]["valid"] is False
    assert (
        candidate_eval["objective"]["quality_checks"][
            "min_media_playing_fraction"
        ]
        is False
    )

    result = compare_objectives(
        baseline_eval["objective"],
        candidate_eval["objective"],
    )
    assert result["verdict"] == "INSUFFICIENT_DATA"


def test_media_objective_can_enforce_temperature_and_cpu_limits():
    samples = [
        media_sample(0, 6.0, True, temp=44.0, cpu=20.0),
        media_sample(10, 6.0, True, temp=46.0, cpu=22.0),
        media_sample(20, 6.0, True, temp=48.0, cpu=24.0),
    ]
    result = evaluate_window(
        samples,
        scene="media_playback",
        max_gap_seconds=30,
        min_valid_seconds=10,
        quality_constraints={
            "max_temperature_c": 47.0,
            "max_average_cpu_usage_percent": 30.0,
        },
    )
    assert result["objective"]["valid"] is False
    assert result["objective"]["quality_checks"]["max_temperature_c"] is False
    assert (
        result["objective"]["quality_checks"]["max_average_cpu_usage_percent"]
        is True
    )
