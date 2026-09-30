from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .analytics import battery_usage_summary
from .config import Config, load_machine
from .envelopes import EnvelopeRegistry
from .experiments import TrialManager
from .storage import Database

ALLOWED_ACTIONS = {
    "NO_CHANGE",
    "NEED_MORE_DATA",
    "INVESTIGATE_POWER_SPIKE",
    "INVESTIGATE_THERMAL_EVENT",
    "PROPOSE_WASTE_FIX",
    "PROPOSE_ENVELOPE_TRIAL",
    "ROLLBACK_TRIAL",
    "PROMOTE_ENVELOPE",
    "PROPOSE_MANUAL_RECALIBRATION",
}


class LLMDecisionError(RuntimeError):
    pass


def build_knowledge_pack(
    config: Config,
    db: Database,
    registry: EnvelopeRegistry,
) -> dict[str, Any]:
    now = time.time()
    history_hours = float(config.get("llm.history_hours", 24))
    since = now - history_hours * 3600
    samples = db.recent_samples(since)
    rollups = db.recent_rollups(since, limit=500)
    incidents = db.recent_incidents(
        since,
        limit=int(config.get("llm.max_incidents", 20)),
    )

    battery_summary = battery_usage_summary(
        samples,
        max_gap_seconds=float(config.get("collector.max_gap_seconds", 45.0)),
    )
    active = [row for row in samples if row.get("user_active")]
    latest = samples[-1] if samples else None
    latest_battery = (latest or {}).get("battery") or {}
    full_energy = latest_battery.get("energy_full_wh")
    design_energy = latest_battery.get("energy_full_design_wh")
    battery_health_pct = (
        float(full_energy) / float(design_energy) * 100.0
        if isinstance(full_energy, (int, float))
        and isinstance(design_energy, (int, float))
        and design_energy > 0
        else None
    )
    thermal_states: dict[str, int] = {}
    envelopes: dict[str, int] = {}
    for row in samples:
        state = str(row.get("thermal_state") or "unknown")
        thermal_states[state] = thermal_states.get(state, 0) + 1
        envelope = str(row.get("current_envelope") or "none")
        envelopes[envelope] = envelopes.get(envelope, 0) + 1

    machine = load_machine(config.root)
    pack = {
        "generated_ts": now,
        "objective": (
            "Minimize whole-device battery discharge while preserving acceptable "
            "user experience, stability, and sustainable thermals."
        ),
        "rules": {
            "battery_power_is_primary_reward": True,
            "llm_not_in_realtime_control": True,
            "trial_success_requires_measurement": True,
            "negative_user_feedback_rejects_candidate": True,
            "thermal_safety_can_preempt_any_trial": True,
            "prefer_waste_elimination_before_performance_restriction": True,
        },
        "battery": {
            "epoch": db.active_battery_epoch(),
            **battery_summary,
            "active_fraction": len(active) / len(samples) if samples else None,
            "full_energy_wh": full_energy,
            "design_energy_wh": design_energy,
            "health_pct": battery_health_pct,
            "recent_rollups": rollups[:120],
        },
        "demand": {
            "latest": samples[-1].get("demand_region") if samples else None,
            "recent_local_compute_pressure": (
                samples[-1].get("local_compute_pressure") if samples else None
            ),
        },
        "thermal": {
            "states": thermal_states,
            "latest_pressure": (samples[-1].get("thermal_pressure") if samples else None),
            "latest_temp_c": (samples[-1].get("package_temp_c") if samples else None),
        },
        "control": {
            "envelope_distribution": envelopes,
            "actions": db.recent_control_actions(since, limit=100),
            "envelopes": registry.list(),
        },
        "incidents": incidents,
        "trials": db.recent_trials(int(config.get("llm.max_trials", 20))),
        "feedback": db.recent_feedback(50),
        "rejections": db.recent_rejections(50),
        "system_fingerprint": db.active_system_fingerprint(),
        "software_version_drift": db.get_meta("software_version_drift"),
        "calibration": machine.get("calibration"),
    }
    run_id = db.add_llm_run(pack)
    pack["run_id"] = run_id
    return pack


def validate_decision(decision: dict[str, Any]) -> None:
    unknown = set(decision) - {"action", "reason", "payload"}
    if unknown:
        raise LLMDecisionError(f"unsupported decision fields: {sorted(unknown)}")
    action = decision.get("action")
    if action not in ALLOWED_ACTIONS:
        raise LLMDecisionError(f"unsupported LLM action: {action}")
    if not isinstance(decision.get("reason", ""), str):
        raise LLMDecisionError("reason must be a string")
    payload = decision.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise LLMDecisionError("payload must be an object")


def _save_manual_proposal(
    root: Path,
    action: str,
    payload: dict[str, Any],
) -> str:
    directory = root / "proposals"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{int(time.time())}-{action.lower()}.json"
    path.write_text(
        json.dumps(
            {
                "action": action,
                "created_ts": time.time(),
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return str(path)


def apply_decision(
    decision: dict[str, Any],
    *,
    config: Config,
    db: Database,
    registry: EnvelopeRegistry,
    trials: TrialManager,
    approved: bool = False,
) -> dict[str, Any]:
    validate_decision(decision)
    action = str(decision["action"])
    payload = decision.get("payload") or {}
    level = int(config.get("automation.level", 0))
    executed = False
    result: dict[str, Any] = {"action": action}

    if action in {
        "NO_CHANGE",
        "NEED_MORE_DATA",
        "INVESTIGATE_POWER_SPIKE",
        "INVESTIGATE_THERMAL_EVENT",
    }:
        result["status"] = "recorded"

    elif action in {"PROPOSE_WASTE_FIX", "PROPOSE_MANUAL_RECALIBRATION"}:
        path = _save_manual_proposal(config.root, action, payload)
        result.update({"status": "manual_review_required", "proposal_file": path})

    elif action == "PROPOSE_ENVELOPE_TRIAL":
        proposal = payload.get("proposal")
        if not isinstance(proposal, dict):
            raise LLMDecisionError("PROPOSE_ENVELOPE_TRIAL requires payload.proposal")
        errors = trials.validate_proposal(proposal)
        if errors:
            raise LLMDecisionError("; ".join(errors))
        if approved and level < 2:
            result.update(
                {
                    "status": "automation_level_too_low",
                    "required_level": 2,
                }
            )
        elif not approved and level < 3:
            path = _save_manual_proposal(config.root, action, payload)
            result.update(
                {
                    "status": "awaiting_human_approval",
                    "proposal_file": path,
                }
            )
        else:
            latest = db.latest_sample()
            if not latest:
                raise LLMDecisionError("no telemetry sample available for trial")
            result["trial"] = trials.start(proposal, latest)
            result["status"] = "started"
            executed = True

    elif action == "ROLLBACK_TRIAL":
        trial_id = payload.get("trial_id")
        if not isinstance(trial_id, str):
            raise LLMDecisionError("ROLLBACK_TRIAL requires payload.trial_id")
        result["trial"] = trials.rollback(
            trial_id,
            str(decision.get("reason") or "LLM requested rollback"),
        )
        result["status"] = "rolled_back"
        executed = True

    elif action == "PROMOTE_ENVELOPE":
        trial_id = payload.get("trial_id")
        if not isinstance(trial_id, str):
            raise LLMDecisionError("PROMOTE_ENVELOPE requires payload.trial_id")
        if approved and level < 2:
            result.update(
                {
                    "status": "automation_level_too_low",
                    "required_level": 2,
                }
            )
        elif not approved and not (
            level >= 4 and bool(config.get("automation.auto_promote", False))
        ):
            result["status"] = "awaiting_human_approval"
        else:
            result["envelope"] = trials.promote(trial_id)
            result["status"] = "promoted"
            executed = True

    db.add_llm_decision(
        action,
        decision,
        executed=executed,
        result=result,
    )
    return {"executed": executed, **result}
