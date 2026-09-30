from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParameterRule:
    risk_class: str
    auto_trial_allowed: bool
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None


PARAMETER_RULES: dict[str, ParameterRule] = {
    "cpu.epp": ParameterRule(
        "B", True, choices=("performance", "balance_performance", "balance_power", "power")
    ),
    "cpu.max_freq_khz": ParameterRule("B", True, minimum=400_000, maximum=5_000_000),
    "cpu.turbo_disabled": ParameterRule("B", True, choices=(True, False, 0, 1)),
    "display.brightness_percent": ParameterRule("B", True, minimum=5, maximum=100),
    "wifi.power_save": ParameterRule("C", False, choices=(True, False, 0, 1)),
    "radio.bluetooth_enabled": ParameterRule("C", False, choices=(True, False, 0, 1)),
}

SENSITIVE_PREFIXES = (
    "kernel.", "suspend.", "pci.", "usb.", "i2c.", "firmware.",
    "linux_surface.", "audio.", "gpu.", "aspm.",
)


def classify_parameter(parameter: str) -> dict[str, Any]:
    if parameter == "profile.id":
        return {
            "parameter": parameter,
            "risk_class": "B",
            "known": True,
            "auto_trial_allowed": False,
        }
    rule = PARAMETER_RULES.get(parameter)
    if rule:
        return {
            "parameter": parameter,
            "risk_class": rule.risk_class,
            "known": True,
            "auto_trial_allowed": rule.auto_trial_allowed,
        }
    if parameter.startswith(SENSITIVE_PREFIXES):
        return {
            "parameter": parameter,
            "risk_class": "D",
            "known": True,
            "auto_trial_allowed": False,
        }
    return {
        "parameter": parameter,
        "risk_class": "UNKNOWN",
        "known": False,
        "auto_trial_allowed": False,
    }


def validate_parameter(parameter: str, value: Any, *, for_auto_trial: bool = False) -> list[str]:
    errors: list[str] = []
    if parameter == "profile.id":
        if not isinstance(value, str) or not value.strip():
            errors.append("profile.id must be a non-empty profile id")
        if for_auto_trial:
            errors.append(
                "profile.id requires an explicitly auto-trial-enabled candidate profile"
            )
        return errors
    rule = PARAMETER_RULES.get(parameter)
    if rule is None:
        cls = classify_parameter(parameter)
        if cls["risk_class"] == "D":
            errors.append(f"{parameter} is human-approval-only")
        else:
            errors.append(f"{parameter} is not in the PowerLab parameter registry")
        return errors
    if for_auto_trial and not rule.auto_trial_allowed:
        errors.append(f"{parameter} is not allowed for unattended trials")
    if rule.choices is not None and value not in rule.choices:
        errors.append(f"{parameter} must be one of {rule.choices}")
    if isinstance(value, bool):
        numeric = None
    elif isinstance(value, (int, float)):
        numeric = float(value)
    else:
        numeric = None
    if rule.minimum is not None and (numeric is None or numeric < rule.minimum):
        errors.append(f"{parameter} must be >= {rule.minimum}")
    if rule.maximum is not None and (numeric is None or numeric > rule.maximum):
        errors.append(f"{parameter} must be <= {rule.maximum}")
    return errors
