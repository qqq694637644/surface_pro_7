from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import (
    PARAMETER_RULES,
    SENSITIVE_PREFIXES,
    classify_parameter as classify_safety_parameter,
    validate_parameter,
)


PROPOSAL_SCHEMA_VERSION = 2
SAFE_PARAMETERS = set(PARAMETER_RULES)


def classify_parameter(parameter: str) -> dict[str, Any]:
    policy = classify_safety_parameter(parameter)
    risk = policy["risk_class"]
    if risk == "B":
        classification = "allowlisted-reversible"
    elif risk in {"C", "D"}:
        classification = "sensitive-human-review-only"
    else:
        classification = "unknown-blocked"
    return {
        **policy,
        "classification": classification,
        "autonomous_apply_allowed": False,
        "human_review_required": True,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return clean[:48] or "proposal"


def make_proposal_id(title: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"p-{stamp}-{slug(title)}"


def proposal_template(experiment_id: str = "", workload: str = "unknown") -> dict[str, Any]:
    scene = workload or "unknown"
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "id": "",
        "based_on_experiment": experiment_id,
        "created_at": now_iso(),
        "source": "ai",
        "title": "CHANGE_ME",
        "rationale": "Explain the measured evidence and why this single change is worth testing.",
        "context": {"scene": scene, "min_confidence": 0.70},
        "change": {"parameter": "CHANGE_ME", "from": "CHANGE_ME", "to": "CHANGE_ME"},
        "objective": {"type": "auto"},
        "expected_effect": {
            "average_power_delta_w": {"min": None, "max": None},
            "usability": "Describe the expected responsiveness/task-quality tradeoff.",
            "confidence": "low",
        },
        "risk": {"level": "low", "notes": []},
        "rollback": {
            "instruction": "PowerLab restores the pre-trial snapshot automatically; add any manual caveat here."
        },
        "validation": {
            "workload": scene,
            "duration_seconds": 900,
            "min_valid_minutes": 60,
            "baseline_lookback_hours": 168,
            "acceptance": {
                "max_average_power_delta_w": -0.05,
                "max_temperature_increase_c": 2.0,
                "max_task_duration_ratio": 1.10,
            },
        },
    }


def _is_number_or_none(value: object) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))


def _validate_common(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("source", "title", "rationale"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            errors.append(f"{key} must be a non-empty string")

    change = data.get("change")
    if not isinstance(change, dict):
        errors.append("change must be an object")
        return errors

    parameter = change.get("parameter")
    if not isinstance(parameter, str) or not parameter.strip() or parameter == "CHANGE_ME":
        errors.append("change.parameter must be a concrete parameter")
    else:
        if classify_parameter(parameter)["risk_class"] == "UNKNOWN":
            errors.append(f"change.parameter is not classified: {parameter}")
        if "to" in change and change.get("to") != "CHANGE_ME":
            errors.extend(validate_parameter(parameter, change.get("to"), for_auto_trial=False))

    if "from" not in change or "to" not in change:
        errors.append("change must contain from and to")
    elif change.get("from") == "CHANGE_ME" or change.get("to") == "CHANGE_ME":
        errors.append("change.from and change.to must be concrete values")
    elif change.get("from") == change.get("to"):
        errors.append("change.from and change.to must differ")

    expected = data.get("expected_effect")
    if not isinstance(expected, dict):
        errors.append("expected_effect must be an object")
    else:
        delta = expected.get("average_power_delta_w")
        if not isinstance(delta, dict):
            errors.append("expected_effect.average_power_delta_w must be an object")
        else:
            low, high = delta.get("min"), delta.get("max")
            if not _is_number_or_none(low) or not _is_number_or_none(high):
                errors.append("expected power delta min/max must be numbers or null")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low > high:
                errors.append("expected power delta min cannot exceed max")
        if expected.get("confidence") not in {"low", "medium", "high"}:
            errors.append("expected_effect.confidence must be low, medium, or high")

    risk = data.get("risk")
    if not isinstance(risk, dict) or risk.get("level") not in {"low", "medium", "high"}:
        errors.append("risk.level must be low, medium, or high")

    rollback = data.get("rollback")
    if not isinstance(rollback, dict) or not str(rollback.get("instruction", "")).strip():
        errors.append("rollback.instruction must be non-empty")
    return errors


def validate_proposal(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = data.get("schema_version")
    if version not in {1, 2}:
        return ["schema_version must be 1 or 2"]

    if version == 1:
        based = data.get("based_on_experiment")
        if not isinstance(based, str) or not based.strip():
            errors.append("based_on_experiment must be a non-empty string")
        errors.extend(_validate_common(data))
        validation = data.get("validation")
        if not isinstance(validation, dict):
            errors.append("validation must be an object")
        else:
            duration = validation.get("duration_seconds")
            if not isinstance(duration, int) or isinstance(duration, bool) or duration < 60:
                errors.append("validation.duration_seconds must be an integer >= 60")
            workload = validation.get("workload")
            if not isinstance(workload, str) or not workload.strip():
                errors.append("validation.workload must be a non-empty string")
        return errors

    errors.extend(_validate_common(data))
    context = data.get("context")
    if not isinstance(context, dict):
        errors.append("context must be an object")
    else:
        scene = context.get("scene")
        if not isinstance(scene, str) or not scene.strip():
            errors.append("context.scene must be a non-empty string")
        confidence = context.get("min_confidence", 0.0)
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            errors.append("context.min_confidence must be between 0 and 1")

    validation = data.get("validation")
    if not isinstance(validation, dict):
        errors.append("validation must be an object")
    else:
        duration = validation.get("duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 60:
            errors.append("validation.duration_seconds must be an integer >= 60")
        minutes = validation.get("min_valid_minutes")
        if not isinstance(minutes, (int, float)) or float(minutes) <= 0:
            errors.append("validation.min_valid_minutes must be > 0")
        lookback = validation.get("baseline_lookback_hours")
        if not isinstance(lookback, (int, float)) or float(lookback) <= 0:
            errors.append("validation.baseline_lookback_hours must be > 0")
        acceptance = validation.get("acceptance")
        if not isinstance(acceptance, dict):
            errors.append("validation.acceptance must be an object")
        else:
            for key in (
                "max_average_power_delta_w",
                "max_temperature_increase_c",
                "max_task_duration_ratio",
            ):
                if not _is_number_or_none(acceptance.get(key)):
                    errors.append(f"validation.acceptance.{key} must be a number or null")
    return errors


def normalize_proposal(data: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(data))
    if copied.get("schema_version") == 1:
        workload = (copied.get("validation") or {}).get("workload") or "unknown"
        copied["schema_version"] = 2
        copied.setdefault("context", {"scene": workload, "min_confidence": 0.70})
        copied.setdefault("objective", {"type": "auto"})
        validation = copied.setdefault("validation", {})
        validation.setdefault("min_valid_minutes", 60)
        validation.setdefault("baseline_lookback_hours", 168)
        acceptance = validation.setdefault("acceptance", {})
        acceptance.setdefault("max_task_duration_ratio", 1.10)
    if not copied.get("id"):
        copied["id"] = make_proposal_id(copied.get("title", "proposal"))
    copied.setdefault("created_at", now_iso())
    parameter = (copied.get("change") or {}).get("parameter", "")
    copied["powerlab_policy"] = classify_parameter(str(parameter))
    return copied


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read proposal JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Proposal JSON root must be an object")
    return data
