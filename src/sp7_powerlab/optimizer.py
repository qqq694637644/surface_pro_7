from __future__ import annotations

from typing import Any

from .safety import PARAMETER_RULES


def bounded_candidates(parameter: str, current: Any) -> list[Any]:
    rule = PARAMETER_RULES.get(parameter)
    if not rule:
        return []
    if rule.choices:
        return [value for value in rule.choices if value != current]
    if isinstance(current, (int, float)) and rule.minimum is not None and rule.maximum is not None:
        span = rule.maximum - rule.minimum
        steps = [
            max(rule.minimum, min(rule.maximum, float(current) + span * fraction))
            for fraction in (-0.20, -0.10, 0.10, 0.20)
        ]
        if isinstance(current, int):
            steps = [int(round(value)) for value in steps]
        return sorted(set(value for value in steps if value != current))
    return []


def recommend_next_parameter(history: list[dict[str, Any]]) -> dict[str, Any]:
    attempted = {
        str((item.get("proposal") or {}).get("change", {}).get("parameter"))
        for item in history
    }
    order = ["cpu.epp", "cpu.max_freq_khz", "cpu.turbo_disabled"]
    candidate = next((item for item in order if item not in attempted), order[0])
    return {
        "parameter": candidate,
        "reason": "bounded deterministic fallback when no external LLM optimizer is available",
    }
