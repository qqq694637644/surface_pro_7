from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .experiments import TrialError
from .proposals import normalize_proposal, validate_proposal


ALLOWED_ACTIONS = {
    "NO_CHANGE",
    "NEED_MORE_DATA",
    "PROPOSE_TRIAL",
    "ROLLBACK_TRIAL",
    "PROMOTE_PROFILE",
    "INVESTIGATE_REGRESSION",
    "UPDATE_CONTEXT_RULE",
}


class OrchestratorError(RuntimeError):
    pass


def validate_decision(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    action = decision.get("action")
    if action not in ALLOWED_ACTIONS:
        errors.append(f"action must be one of {sorted(ALLOWED_ACTIONS)}")
    if not isinstance(decision.get("reason"), str) or not decision.get("reason", "").strip():
        errors.append("reason must be a non-empty string")
    payload = decision.get("payload")
    if payload is None:
        decision["payload"] = {}
    elif not isinstance(payload, dict):
        errors.append("payload must be an object")
    if action == "PROPOSE_TRIAL":
        proposal = (decision.get("payload") or {}).get("proposal")
        if not isinstance(proposal, dict):
            errors.append("PROPOSE_TRIAL requires payload.proposal")
    if action == "ROLLBACK_TRIAL":
        trial_id = (decision.get("payload") or {}).get("trial_id")
        if trial_id is not None and not isinstance(trial_id, str):
            errors.append("payload.trial_id must be a string")
    if action == "PROMOTE_PROFILE":
        if not isinstance((decision.get("payload") or {}).get("trial_id"), str):
            errors.append("PROMOTE_PROFILE requires payload.trial_id")
    return errors


class HourlyOrchestrator:
    def __init__(self, config, db, knowledge, trials):
        self.config = config
        self.db = db
        self.knowledge = knowledge
        self.trials = trials
        self.runtime = config.root / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)

    def emit_pack(self, output: Path | None = None) -> dict[str, Any]:
        pack = self.knowledge.build_pack()
        output = output or self.runtime / "hourly-pack.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(pack, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        run_id = f"llm-{uuid.uuid4().hex[:16]}"
        self.db.add_llm_run(
            {
                "run_id": run_id,
                "input_hash": pack["pack_sha256"],
                "input": pack,
                "status": "awaiting_external_decision",
            }
        )
        return {"run_id": run_id, "pack": pack, "output": str(output)}

    def apply_decision(
        self,
        decision: dict[str, Any],
        *,
        run_id: str | None = None,
        current_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        errors = validate_decision(decision)
        if errors:
            raise OrchestratorError("; ".join(errors))
        action = decision["action"]
        payload = decision.get("payload") or {}
        result: dict[str, Any] = {"action": action, "executed": False}

        if action in {"NO_CHANGE", "NEED_MORE_DATA", "INVESTIGATE_REGRESSION"}:
            result.update({"executed": True, "result": "recorded"})
        elif action == "UPDATE_CONTEXT_RULE":
            rule_dir = self.config.root / "proposals" / "context-rules"
            rule_dir.mkdir(parents=True, exist_ok=True)
            rule_file = rule_dir / f"context-rule-{uuid.uuid4().hex[:16]}.json"
            rule_file.write_text(
                json.dumps(
                    {
                        "created_at": time.time(),
                        "reason": decision["reason"],
                        "payload": payload,
                        "source": decision.get("source", "llm"),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            result.update(
                {
                    "executed": False,
                    "result": {
                        "status": "proposal-recorded",
                        "file": str(rule_file),
                        "note": "context rule config is never rewritten automatically",
                    },
                }
            )
        elif action == "ROLLBACK_TRIAL":
            result.update(
                {
                    "executed": True,
                    "result": self.trials.rollback(payload.get("trial_id"), reason=decision["reason"]),
                }
            )
        elif action == "PROMOTE_PROFILE":
            if not bool(self.config.get("automation.auto_promote_profiles", False)) and not bool(
                payload.get("human_approved")
            ):
                result["result"] = "promotion requires human_approved or auto_promote_profiles=true"
            else:
                result.update(
                    {
                        "executed": True,
                        "result": self.trials.promote(
                            payload["trial_id"], payload.get("profile_id")
                        ),
                    }
                )
        elif action == "PROPOSE_TRIAL":
            proposal = normalize_proposal(payload["proposal"])
            proposal_errors = validate_proposal(proposal)
            if proposal_errors:
                result["result"] = {
                    "proposal_status": "invalid",
                    "errors": proposal_errors,
                }
                proposal = None
            if proposal is None:
                pass
            else:
                proposal_dir = self.config.root / "proposals"
                proposal_dir.mkdir(parents=True, exist_ok=True)
                (proposal_dir / f"{proposal['id']}.json").write_text(
                    json.dumps(proposal, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        if action == "PROPOSE_TRIAL" and proposal is not None:
            auto = bool(self.config.get("automation.auto_run_low_risk_trials", False))
            errors = self.trials.validate_proposal(
                proposal, current_context=None, unattended=auto
            )
            if errors:
                result["result"] = {"proposal_status": "invalid_or_not_ready", "errors": errors}
            elif not auto and not bool(payload.get("human_approved")):
                result["result"] = {
                    "proposal_status": "validated_waiting_for_approval",
                    "proposal": proposal,
                }
            else:
                unattended = auto and not bool(payload.get("human_approved"))
                readiness_errors = (
                    self.trials.validate_proposal(
                        proposal,
                        current_context=current_context,
                        unattended=unattended,
                    )
                    if current_context
                    else ["current context unavailable"]
                )
                deferrable_markers = (
                    "current scene",
                    "context confidence",
                    "battery is not discharging",
                    "battery below trial threshold",
                    "current context unavailable",
                )
                hard_errors = [
                    error
                    for error in readiness_errors
                    if not any(marker in error for marker in deferrable_markers)
                ]
                if hard_errors:
                    result["result"] = {
                        "proposal_status": "invalid_or_not_ready",
                        "errors": hard_errors,
                    }
                elif not readiness_errors:
                    started = self.trials.start(
                        proposal,
                        current_context=current_context,
                        unattended=unattended,
                    )
                    result.update({"executed": True, "result": started})
                else:
                    queued = self.trials.queue(proposal, unattended=unattended)
                    result.update(
                        {
                            "executed": True,
                            "result": {
                                **queued,
                                "note": (
                                    "queued until matching scene, confidence and "
                                    "battery-discharge conditions are observed"
                                ),
                                "waiting_reasons": readiness_errors,
                            },
                        }
                    )

        decision_id = str(decision.get("decision_id") or f"d-{uuid.uuid4().hex[:16]}")
        self.db.add_decision(
            {
                "decision_id": decision_id,
                "source": decision.get("source", "llm"),
                "action": action,
                "reason": decision["reason"],
                "payload": payload,
                "applied": result["executed"],
                "result": result,
            }
        )
        if run_id:
            pack_row = self.db.conn.execute(
                "SELECT input_json,input_hash FROM llm_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            self.db.add_llm_run(
                {
                    "run_id": run_id,
                    "input_hash": pack_row["input_hash"] if pack_row else None,
                    "input": json.loads(pack_row["input_json"]) if pack_row else {},
                    "output": decision,
                    "action": action,
                    "status": "applied" if result["executed"] else "recorded",
                }
            )
        return result
