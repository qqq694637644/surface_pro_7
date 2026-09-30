from __future__ import annotations

from typing import Any

from .quality import data_quality


INTERACTIVE_SCENES = {
    "reading", "web_interactive", "coding_interactive", "office_interactive",
    "remote_interactive", "mixed", "unknown",
}
JOB_SCENES = {"compile", "background_compute", "batch_job", "file_transfer"}
MEDIA_SCENES = {"media_playback", "video_call"}


def scene_objective(scene: str) -> str:
    if scene in JOB_SCENES:
        return "energy_per_task"
    if scene in MEDIA_SCENES:
        return "energy_rate_with_quality"
    return "average_power_with_responsiveness"


def evaluate_window(
    samples: list[dict[str, Any]],
    *,
    scene: str,
    max_gap_seconds: float,
    min_valid_seconds: float,
    task_duration_s: float | None = None,
    task_completed: bool | None = None,
    quality_constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = data_quality(
        samples,
        max_gap_seconds=max_gap_seconds,
        min_valid_seconds=min_valid_seconds,
        require_single_scene=True,
    )
    objective_type = scene_objective(scene)
    objective: dict[str, Any] = {
        "type": objective_type,
        "scene": scene,
        "valid": quality["valid"],
    }
    if objective_type == "energy_per_task":
        objective.update(
            {
                "energy_wh": quality.get("energy_wh"),
                "task_duration_s": task_duration_s,
                "task_completed": task_completed,
            }
        )
        if task_completed is not True:
            quality["valid"] = False
            if "task_not_confirmed_complete" not in quality["reasons"]:
                quality["reasons"].append("task_not_confirmed_complete")
    else:
        objective.update(
            {
                "average_power_w": quality.get("average_power_w"),
                "energy_wh": quality.get("energy_wh"),
                "valid_duration_s": quality.get("valid_duration_s"),
            }
        )
    constraints = dict(quality_constraints or {})
    if scene == "media_playback":
        constraints.setdefault("min_media_playing_fraction", 0.90)

    quality_metrics: dict[str, Any] = {}
    quality_checks: dict[str, bool | None] = {}

    media_states = [
        bool((sample.get("media") or {}).get("playing"))
        for sample in samples
        if isinstance(sample.get("media"), dict)
        and "playing" in (sample.get("media") or {})
    ]
    if media_states:
        quality_metrics["media_playing_fraction"] = sum(media_states) / len(media_states)

    temperatures = [
        float(sample["temp_c"])
        for sample in samples
        if isinstance(sample.get("temp_c"), (int, float))
    ]
    if temperatures:
        quality_metrics["max_temperature_c"] = max(temperatures)

    cpu_values = [
        float(sample["cpu_usage"])
        for sample in samples
        if isinstance(sample.get("cpu_usage"), (int, float))
    ]
    if cpu_values:
        quality_metrics["average_cpu_usage_percent"] = sum(cpu_values) / len(cpu_values)

    def enforce(
        key: str,
        metric_key: str,
        *,
        minimum: bool,
    ) -> None:
        if key not in constraints:
            return
        requested = constraints.get(key)
        metric = quality_metrics.get(metric_key)
        if not isinstance(requested, (int, float)):
            quality_checks[key] = None
            quality["valid"] = False
            quality["reasons"].append(f"invalid_quality_constraint:{key}")
            return
        if not isinstance(metric, (int, float)):
            quality_checks[key] = None
            quality["valid"] = False
            quality["reasons"].append(f"missing_quality_metric:{metric_key}")
            return
        passed = (
            float(metric) >= float(requested)
            if minimum
            else float(metric) <= float(requested)
        )
        quality_checks[key] = passed
        if not passed:
            quality["valid"] = False
            quality["reasons"].append(f"quality_constraint_failed:{key}")

    enforce(
        "min_media_playing_fraction",
        "media_playing_fraction",
        minimum=True,
    )
    enforce("max_temperature_c", "max_temperature_c", minimum=False)
    enforce(
        "max_average_cpu_usage_percent",
        "average_cpu_usage_percent",
        minimum=False,
    )

    if constraints:
        objective["quality_constraints"] = constraints
        objective["quality_metrics"] = quality_metrics
        objective["quality_checks"] = quality_checks
    objective["valid"] = quality["valid"]
    return {"objective": objective, "quality": quality}


def compare_objectives(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_power_delta_w: float | None = None,
    max_task_duration_ratio: float | None = None,
) -> dict[str, Any]:
    if not baseline.get("valid") or not candidate.get("valid"):
        return {"verdict": "INSUFFICIENT_DATA", "reason": "invalid_objective"}

    typ = baseline.get("type")
    if typ != candidate.get("type"):
        return {"verdict": "INSUFFICIENT_DATA", "reason": "objective_type_mismatch"}

    checks: dict[str, bool | None] = {}
    observed: dict[str, Any] = {}
    if typ == "energy_per_task":
        be, ce = baseline.get("energy_wh"), candidate.get("energy_wh")
        if not isinstance(be, (int, float)) or not isinstance(ce, (int, float)):
            return {"verdict": "INSUFFICIENT_DATA", "reason": "missing_task_energy"}
        observed["energy_delta_wh"] = ce - be
        observed["energy_delta_percent"] = (ce - be) / be * 100.0 if be else None
        bd, cd = baseline.get("task_duration_s"), candidate.get("task_duration_s")
        if isinstance(bd, (int, float)) and isinstance(cd, (int, float)) and bd > 0:
            observed["duration_ratio"] = cd / bd
            checks["duration"] = (
                True if max_task_duration_ratio is None else cd / bd <= max_task_duration_ratio
            )
        checks["energy"] = ce < be
    else:
        bp, cp = baseline.get("average_power_w"), candidate.get("average_power_w")
        if not isinstance(bp, (int, float)) or not isinstance(cp, (int, float)):
            return {"verdict": "INSUFFICIENT_DATA", "reason": "missing_average_power"}
        delta = cp - bp
        observed["average_power_delta_w"] = delta
        observed["average_power_delta_percent"] = delta / bp * 100.0 if bp else None
        checks["power"] = delta < 0 if max_power_delta_w is None else delta <= max_power_delta_w

    decisive = [v for v in checks.values() if v is not None]
    verdict = "CANDIDATE_WINNER" if decisive and all(decisive) else "REJECTED"
    return {"verdict": verdict, "observed": observed, "checks": checks}
