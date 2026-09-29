from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


PROPOSAL_SCHEMA_VERSION = 1

SAFE_PARAMETERS = {
    "cpu.epp",
    "cpu.max_freq_khz",
    "cpu.turbo_disabled",
    "display.brightness_percent",
    "radio.bluetooth_enabled",
    "wifi.power_save",
    "browser.hardware_acceleration",
    "browser.video_decode_policy",
}

SENSITIVE_PREFIXES = (
    "kernel.",
    "suspend.",
    "pci.",
    "usb.",
    "i2c.",
    "firmware.",
    "linux_surface.",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return clean[:48] or "proposal"


def make_proposal_id(title: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"p-{stamp}-{slug(title)}"


def classify_parameter(parameter: str) -> dict:
    if parameter in SAFE_PARAMETERS:
        return {
            "classification": "allowlisted-reversible",
            "autonomous_apply_allowed": False,
            "human_review_required": True,
            "reason": "Safe enough to experiment with after review; PowerLab never applies settings automatically.",
        }
    if parameter.startswith(SENSITIVE_PREFIXES):
        return {
            "classification": "sensitive-human-review-only",
            "autonomous_apply_allowed": False,
            "human_review_required": True,
            "reason": "Surface stability or boot/suspend behavior may be affected.",
        }
    return {
        "classification": "unknown-blocked",
        "autonomous_apply_allowed": False,
        "human_review_required": True,
        "reason": "Unknown parameters are blocked until explicitly classified.",
    }


def proposal_template(experiment_id: str, workload: str = "unknown") -> dict:
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "id": "",
        "based_on_experiment": experiment_id,
        "created_at": now_iso(),
        "source": "ai",
        "title": "CHANGE_ME",
        "rationale": "Explain what the previous experiment suggests and why this single change is worth testing.",
        "change": {
            "parameter": "CHANGE_ME",
            "from": "CHANGE_ME",
            "to": "CHANGE_ME",
        },
        "expected_effect": {
            "average_power_delta_w": {"min": None, "max": None},
            "usability": "Describe the expected usability tradeoff.",
            "confidence": "low",
        },
        "risk": {
            "level": "low",
            "notes": [],
        },
        "rollback": {
            "instruction": "Describe how to restore the previous value/profile.",
        },
        "validation": {
            "workload": workload,
            "duration_seconds": 900,
            "acceptance": {
                "max_average_power_delta_w": None,
                "max_temperature_increase_c": None,
            },
        },
    }


def _is_number_or_none(value: object) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))


def validate_proposal(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != PROPOSAL_SCHEMA_VERSION:
        errors.append(f"schema_version must be {PROPOSAL_SCHEMA_VERSION}")

    for key in ("based_on_experiment", "source", "title", "rationale"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            errors.append(f"{key} must be a non-empty string")

    change = data.get("change")
    if not isinstance(change, dict):
        errors.append("change must be an object")
    else:
        parameter = change.get("parameter")
        if not isinstance(parameter, str) or not parameter.strip() or parameter == "CHANGE_ME":
            errors.append("change.parameter must be a concrete parameter")
        else:
            policy = classify_parameter(parameter)
            if policy["classification"] == "unknown-blocked":
                errors.append(f"change.parameter is not classified: {parameter}")
        if change.get("from") == "CHANGE_ME" or change.get("to") == "CHANGE_ME":
            errors.append("change.from and change.to must be concrete values")
        if "from" not in change or "to" not in change:
            errors.append("change must contain from and to")
        elif change.get("from") == change.get("to"):
            errors.append("change.from and change.to must differ")

    expected = data.get("expected_effect")
    if not isinstance(expected, dict):
        errors.append("expected_effect must be an object")
    else:
        power_delta = expected.get("average_power_delta_w")
        if not isinstance(power_delta, dict):
            errors.append("expected_effect.average_power_delta_w must be an object")
        else:
            low = power_delta.get("min")
            high = power_delta.get("max")
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

    validation = data.get("validation")
    if not isinstance(validation, dict):
        errors.append("validation must be an object")
    else:
        duration = validation.get("duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 60:
            errors.append("validation.duration_seconds must be an integer >= 60")
        if not isinstance(validation.get("workload"), str) or not validation.get("workload", "").strip():
            errors.append("validation.workload must be a non-empty string")
        acceptance = validation.get("acceptance")
        if not isinstance(acceptance, dict):
            errors.append("validation.acceptance must be an object")
        else:
            for key in ("max_average_power_delta_w", "max_temperature_increase_c"):
                if not _is_number_or_none(acceptance.get(key)):
                    errors.append(f"validation.acceptance.{key} must be a number or null")

    return errors


def normalize_proposal(data: dict) -> dict:
    copied = json.loads(json.dumps(data))
    if not copied.get("id"):
        copied["id"] = make_proposal_id(copied.get("title", "proposal"))
    copied.setdefault("created_at", now_iso())
    parameter = (copied.get("change") or {}).get("parameter", "")
    copied["powerlab_policy"] = classify_parameter(parameter)
    return copied


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read proposal JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Proposal JSON root must be an object")
    return data
