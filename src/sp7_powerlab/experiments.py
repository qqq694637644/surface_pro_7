from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .actuators.base import ActuatorError
from .objectives import JOB_SCENES, compare_objectives, evaluate_window
from .profiles import ProfileRegistry
from .quality import comparability_score
from .safety import classify_parameter, validate_parameter


TERMINAL_STATES = {
    "PROMOTED", "REJECTED", "ROLLED_BACK", "FAILED", "INSUFFICIENT_DATA"
}

UNATTENDED_BLOCKED_SCENES = {
    "video_call",
    "remote_interactive",
    "mixed",
    "unknown",
}


class TrialError(RuntimeError):
    pass


class TrialManager:
    def __init__(self, config, db, actuators, registry: ProfileRegistry):
        self.config = config
        self.db = db
        self.actuators = actuators
        self.registry = registry
        self.runtime = config.root / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.runtime / "trial.lock"

    def _write_lock(self, payload: dict[str, Any]) -> None:
        self.lock_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _clear_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    @staticmethod
    def _is_profile_change(proposal: dict[str, Any]) -> bool:
        change = proposal.get("change") or {}
        return str(change.get("parameter") or "") == "profile.id"

    def _proposal_profiles(
        self,
        proposal: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        change = proposal.get("change") or {}
        baseline_id = change.get("from") or proposal.get("baseline_profile")
        candidate_id = change.get("to") or proposal.get("candidate_profile")
        baseline = (
            self.registry.get(str(baseline_id))
            if isinstance(baseline_id, str) and baseline_id
            else None
        )
        candidate = (
            self.registry.get(str(candidate_id))
            if isinstance(candidate_id, str) and candidate_id
            else None
        )
        return baseline, candidate

    def _snapshot_for_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        change = proposal.get("change") or {}
        parameter = str(change.get("parameter") or "")
        if self._is_profile_change(proposal):
            baseline, candidate = self._proposal_profiles(proposal)
            if not candidate:
                raise TrialError(f"candidate profile not found: {change.get('to')}")
            return {
                "parameter": parameter,
                "value": self.actuators.snapshot_profile_state(
                    candidate,
                    baseline_profile=baseline,
                ),
                "captured_at": time.time(),
            }
        return {
            "parameter": parameter,
            "value": self.actuators.snapshot_parameter(parameter),
            "captured_at": time.time(),
        }

    def _apply_proposal_change(self, proposal: dict[str, Any]) -> dict[str, Any]:
        change = proposal.get("change") or {}
        if self._is_profile_change(proposal):
            _, candidate = self._proposal_profiles(proposal)
            if not candidate:
                raise TrialError(f"candidate profile not found: {change.get('to')}")
            return self.actuators.apply_profile(candidate)
        return self.actuators.apply_parameter(
            str(change.get("parameter") or ""),
            change.get("to"),
        )

    def _restore_snapshot(
        self,
        proposal: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        parameter = str(snapshot.get("parameter") or "")
        if parameter == "profile.id":
            baseline, _ = self._proposal_profiles(proposal)
            return self.actuators.restore_profile_state(
                snapshot.get("value") or {},
                baseline_profile=baseline,
            )
        return self.actuators.restore_parameter(parameter, snapshot.get("value"))

    def validate_proposal(
        self,
        proposal: dict[str, Any],
        *,
        current_context: dict[str, Any] | None = None,
        unattended: bool = False,
        ignore_active: bool = False,
    ) -> list[str]:
        errors: list[str] = []
        change = proposal.get("change")
        if not isinstance(change, dict):
            return ["proposal.change must be an object"]
        if bool(self.config.get("experiment.max_one_primary_change", True)):
            changes = proposal.get("changes")
            if isinstance(changes, list) and len(changes) > 1:
                errors.append("only one primary change is allowed per trial")
        parameter = str(change.get("parameter") or "")
        if not parameter:
            errors.append("proposal.change.parameter is required")
        if "to" not in change:
            errors.append("proposal.change.to is required")
        elif parameter == "profile.id":
            candidate_id = change.get("to")
            baseline_id = change.get("from")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                errors.append("profile.id change.to must name a candidate profile")
            else:
                candidate = self.registry.get(candidate_id)
                if not candidate:
                    errors.append(f"candidate profile not found: {candidate_id}")
                elif hasattr(self.actuators, "profile_trial_errors"):
                    errors.extend(
                        self.actuators.profile_trial_errors(
                            candidate,
                            unattended=unattended,
                        )
                    )
            if (
                baseline_id is not None
                and (
                    not isinstance(baseline_id, str)
                    or not baseline_id.strip()
                    or not self.registry.get(str(baseline_id))
                )
            ):
                errors.append(f"baseline profile not found: {baseline_id}")
            if baseline_id == candidate_id:
                errors.append("profile trial baseline and candidate must differ")
        else:
            errors.extend(
                validate_parameter(parameter, change.get("to"), for_auto_trial=unattended)
            )
        if parameter != "profile.id" and hasattr(self.actuators, "parameter_trial_errors"):
            errors.extend(self.actuators.parameter_trial_errors(parameter))

        context = proposal.get("context") or {}
        scene = context.get("scene")
        min_confidence = float(context.get("min_confidence", 0.0) or 0.0)
        if unattended and scene in UNATTENDED_BLOCKED_SCENES:
            errors.append(f"unattended trials are blocked for scene {scene}")
        if current_context and scene:
            if current_context.get("scene") != scene:
                errors.append(
                    f"current scene {current_context.get('scene')} does not match proposal scene {scene}"
                )
            if float(current_context.get("confidence") or 0.0) < min_confidence:
                errors.append("current context confidence is below proposal minimum")
            battery_status = current_context.get("battery_status")
            if battery_status and battery_status != "Discharging":
                errors.append("current battery is not discharging")
            battery_pct = current_context.get("battery_pct")
            low_threshold = float(
                self.config.get("policy.low_battery_percent", 15.0)
            )
            if (
                isinstance(battery_pct, (int, float))
                and float(battery_pct) <= low_threshold
            ):
                errors.append(
                    f"battery below trial threshold ({battery_pct}% <= {low_threshold}%)"
                )

        if self.db.active_trial() and not ignore_active:
            errors.append("another trial is already active")
        return errors

    def queue(
        self,
        proposal: dict[str, Any],
        *,
        unattended: bool = False,
    ) -> dict[str, Any]:
        errors = self.validate_proposal(
            proposal,
            current_context=None,
            unattended=unattended,
        )
        if errors:
            raise TrialError("; ".join(errors))
        change = proposal["change"]
        scene = str((proposal.get("context") or {}).get("scene") or "unknown")
        trial_id = str(proposal.get("trial_id") or f"t-{uuid.uuid4().hex[:16]}")
        trial = {
            "trial_id": trial_id,
            "proposal_id": proposal.get("id"),
            "context_scene": scene,
            "baseline_profile": (
                change.get("from")
                if str(change.get("parameter") or "") == "profile.id"
                else proposal.get("baseline_profile")
            ),
            "candidate_profile": (
                change.get("to")
                if str(change.get("parameter") or "") == "profile.id"
                else proposal.get("candidate_profile")
            ),
            "parameter": str(change["parameter"]),
            "state": "WAITING_FOR_CONTEXT",
            "start_ts": None,
            "snapshot": {},
            "proposal": proposal,
        }
        self.db.create_trial(trial)
        self.db.add_system_event(
            "trial_queued",
            {
                "trial_id": trial_id,
                "scene": scene,
                "min_confidence": (proposal.get("context") or {}).get("min_confidence"),
            },
        )
        return trial

    def maybe_start_waiting(
        self,
        current_context: dict[str, Any],
        *,
        unattended: bool = True,
    ) -> dict[str, Any] | None:
        trial = self.db.active_trial()
        if not trial or trial.get("state") != "WAITING_FOR_CONTEXT":
            return None
        proposal = trial.get("proposal") or {}
        if (
            not self._is_profile_change(proposal)
            and not trial.get("baseline_profile")
            and current_context.get("profile_id")
        ):
            trial["baseline_profile"] = current_context.get("profile_id")
            self.db.update_trial(
                trial["trial_id"],
                baseline_profile=trial["baseline_profile"],
            )
        errors = self.validate_proposal(
            proposal,
            current_context=current_context,
            unattended=unattended,
            ignore_active=True,
        )
        context_errors = [
            error for error in errors
            if (
                "current scene" in error
                or "context confidence" in error
                or "battery is not discharging" in error
                or "battery below trial threshold" in error
            )
        ]
        hard_errors = [error for error in errors if error not in context_errors]
        if hard_errors:
            self.db.update_trial(
                trial["trial_id"],
                state="FAILED",
                end_ts=time.time(),
                last_error="; ".join(hard_errors),
            )
            raise TrialError("; ".join(hard_errors))
        if context_errors:
            return None

        change = proposal["change"]
        parameter = str(change["parameter"])
        desired = change["to"]
        snapshot = self._snapshot_for_proposal(proposal)
        start_ts = time.time()
        trial.update(
            {
                "start_ts": start_ts,
                "snapshot": snapshot,
                "state": "SNAPSHOTTED",
            }
        )
        self.db.update_trial(
            trial["trial_id"],
            state="SNAPSHOTTED",
            start_ts=start_ts,
            snapshot=snapshot,
        )
        self._write_lock(trial)
        try:
            self.db.update_trial(trial["trial_id"], state="APPLIED")
            applied = self._apply_proposal_change(proposal)
            trial["apply_result"] = applied
            trial["state"] = "READBACK_OK"
            self.db.update_trial(
                trial["trial_id"],
                state="READBACK_OK",
                result={"apply_result": applied},
            )
            self.db.update_trial(trial["trial_id"], state="SETTLING")
            trial["state"] = "SETTLING"
            self._write_lock(trial)
            self.db.add_system_event(
                "queued_trial_started",
                {
                    "trial_id": trial["trial_id"],
                    "parameter": parameter,
                    "desired": desired,
                    "context": current_context,
                },
            )
            return trial
        except Exception as exc:
            try:
                self._restore_snapshot(proposal, snapshot)
            except Exception as restore_exc:
                self.db.update_trial(
                    trial["trial_id"],
                    state="FAILED",
                    end_ts=time.time(),
                    last_error=f"{exc}; rollback failed: {restore_exc}",
                )
                self._clear_lock()
                raise TrialError(
                    f"queued trial apply failed and rollback failed: {exc}; {restore_exc}"
                ) from exc
            self.db.update_trial(
                trial["trial_id"],
                state="ROLLED_BACK",
                end_ts=time.time(),
                last_error=str(exc),
            )
            self._clear_lock()
            raise TrialError(f"queued trial failed and was rolled back: {exc}") from exc

    def start(
        self,
        proposal: dict[str, Any],
        *,
        current_context: dict[str, Any] | None = None,
        unattended: bool = False,
    ) -> dict[str, Any]:
        errors = self.validate_proposal(
            proposal, current_context=current_context, unattended=unattended
        )
        if errors:
            raise TrialError("; ".join(errors))

        change = proposal["change"]
        parameter = str(change["parameter"])
        desired = change["to"]
        snapshot = self._snapshot_for_proposal(proposal)
        trial_id = str(proposal.get("trial_id") or f"t-{uuid.uuid4().hex[:16]}")
        scene = (
            (proposal.get("context") or {}).get("scene")
            or (current_context or {}).get("scene")
            or "unknown"
        )
        trial = {
            "trial_id": trial_id,
            "proposal_id": proposal.get("id"),
            "context_scene": scene,
            "baseline_profile": (
                change.get("from")
                if str(change.get("parameter") or "") == "profile.id"
                else (
                    proposal.get("baseline_profile")
                    or (current_context or {}).get("profile_id")
                )
            ),
            "candidate_profile": (
                change.get("to")
                if str(change.get("parameter") or "") == "profile.id"
                else proposal.get("candidate_profile")
            ),
            "parameter": parameter,
            "state": "SNAPSHOTTED",
            "start_ts": time.time(),
            "snapshot": snapshot,
            "proposal": proposal,
        }
        self.db.create_trial(trial)
        self._write_lock(trial)

        try:
            self.db.update_trial(trial_id, state="APPLIED")
            applied = self._apply_proposal_change(proposal)
            trial["apply_result"] = applied
            trial["state"] = "READBACK_OK"
            self.db.update_trial(
                trial_id,
                state="READBACK_OK",
                result={"apply_result": applied},
            )
            self.db.update_trial(trial_id, state="SETTLING")
            trial["state"] = "SETTLING"
            self._write_lock(trial)
            self.db.add_system_event(
                "trial_started",
                {
                    "trial_id": trial_id,
                    "parameter": parameter,
                    "desired": desired,
                    "risk": classify_parameter(parameter),
                },
            )
            return trial
        except Exception as exc:
            try:
                self._restore_snapshot(proposal, snapshot)
            except Exception as restore_exc:
                self.db.update_trial(
                    trial_id,
                    state="FAILED",
                    end_ts=time.time(),
                    last_error=f"{exc}; rollback failed: {restore_exc}",
                )
                self._clear_lock()
                raise TrialError(f"apply failed and rollback failed: {exc}; {restore_exc}") from exc
            self.db.update_trial(
                trial_id, state="ROLLED_BACK", end_ts=time.time(), last_error=str(exc)
            )
            self._clear_lock()
            raise TrialError(f"trial apply failed and was rolled back: {exc}") from exc

    def status(self) -> dict[str, Any] | None:
        trial = self.db.active_trial()
        if not trial:
            return None
        if trial.get("state") == "SETTLING":
            start_ts = float(trial.get("start_ts") or 0.0)
            settle = float(self.config.get("experiment.settle_seconds", 120))
            if time.time() >= start_ts + settle:
                self.db.update_trial(trial["trial_id"], state="MEASURING")
                trial = self.db.get_trial(trial["trial_id"])
        return trial

    def rollback(self, trial_id: str | None = None, reason: str = "requested") -> dict[str, Any]:
        trial = self.db.get_trial(trial_id) if trial_id else self.db.active_trial()
        if not trial:
            raise TrialError("no active trial")
        if trial.get("state") == "WAITING_FOR_CONTEXT":
            self.db.update_trial(
                trial["trial_id"],
                state="ROLLED_BACK",
                end_ts=time.time(),
                result={"reason": reason, "applied": False},
            )
            self.db.add_system_event(
                "queued_trial_cancelled",
                {"trial_id": trial["trial_id"], "reason": reason},
            )
            return {
                "trial_id": trial["trial_id"],
                "state": "ROLLED_BACK",
                "result": {"reason": reason, "applied": False},
            }
        snapshot = trial.get("snapshot") or {}
        parameter = snapshot.get("parameter") or trial.get("parameter")
        proposal = trial.get("proposal") or {}
        try:
            result = self._restore_snapshot(proposal, snapshot)
        except ActuatorError as exc:
            self.db.update_trial(trial["trial_id"], state="FAILED", last_error=str(exc))
            raise TrialError(str(exc)) from exc
        self.db.update_trial(
            trial["trial_id"],
            state="ROLLED_BACK",
            end_ts=time.time(),
            result={"rollback": result, "reason": reason},
        )
        self.db.add_system_event(
            "trial_rolled_back", {"trial_id": trial["trial_id"], "reason": reason}
        )
        self._clear_lock()
        return {"trial_id": trial["trial_id"], "state": "ROLLED_BACK", "result": result}

    def recover_stale_trial(self) -> dict[str, Any] | None:
        if not self.lock_path.exists():
            return None
        try:
            lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
            trial_id = lock.get("trial_id")
        except (OSError, json.JSONDecodeError):
            self._clear_lock()
            return {"recovered": False, "reason": "invalid_trial_lock_removed"}
        if not trial_id:
            self._clear_lock()
            return {"recovered": False, "reason": "trial_lock_missing_id"}
        trial = self.db.get_trial(str(trial_id))
        if not trial or trial.get("state") in TERMINAL_STATES:
            self._clear_lock()
            return {"recovered": False, "reason": "stale_terminal_lock_removed"}
        return self.rollback(str(trial_id), reason="collector_start_crash_recovery")

    @staticmethod
    def _condition_summary(samples: list[dict[str, Any]], scene: str) -> dict[str, Any]:
        values = lambda key: [
            float(s[key]) for s in samples if isinstance(s.get(key), (int, float))
        ]
        brightness = values("brightness_pct")
        temp = values("temp_c")
        network = []
        for left, right in zip(samples, samples[1:]):
            dt = float(right.get("ts", 0)) - float(left.get("ts", 0))
            if 0 < dt <= 60:
                drx = (right.get("wifi_rx_bytes") or 0) - (left.get("wifi_rx_bytes") or 0)
                dtx = (right.get("wifi_tx_bytes") or 0) - (left.get("wifi_tx_bytes") or 0)
                if drx >= 0 and dtx >= 0:
                    network.append((drx + dtx) * 8 / dt / 1_000_000)
        background = [
            float((s.get("process_summary") or {}).get("background_cpu_percent") or 0)
            for s in samples
        ]
        app_versions = [
            (s.get("app_version") or {}).get("major")
            for s in samples
            if isinstance(s.get("app_version"), dict)
            and (s.get("app_version") or {}).get("major") is not None
        ]
        battery_health = values("battery_health_pct")
        return {
            "scene": scene,
            "brightness_pct": sum(brightness) / len(brightness) if brightness else None,
            "temperature_c": sum(temp) / len(temp) if temp else None,
            "network_mbps": sum(network) / len(network) if network else None,
            "background_cpu_pct": sum(background) / len(background) if background else None,
            "kernel": next((s.get("kernel") for s in samples if s.get("kernel")), None),
            "app_major_version": app_versions[0] if app_versions else None,
            "battery_health_pct": (
                sum(battery_health) / len(battery_health) if battery_health else None
            ),
        }

    def evaluate(self, trial_id: str | None = None) -> dict[str, Any]:
        trial = self.db.get_trial(trial_id) if trial_id else self.db.active_trial()
        if not trial:
            raise TrialError("no active trial")
        proposal = trial.get("proposal") or {}
        scene = str(trial.get("context_scene") or "unknown")
        settle = float(self.config.get("experiment.settle_seconds", 120))
        start_ts = float(trial.get("start_ts") or 0.0)
        if trial.get("state") in {"READBACK_OK", "SETTLING"}:
            settle_until = start_ts + settle
            if time.time() < settle_until:
                result = {
                    "verdict": "INSUFFICIENT_DATA",
                    "reason": ["trial_still_settling"],
                    "settle_until": settle_until,
                    "remaining_seconds": max(0.0, settle_until - time.time()),
                }
                self.db.update_trial(
                    trial["trial_id"], state="SETTLING", result=result
                )
                return result
            self.db.update_trial(trial["trial_id"], state="MEASURING")
            trial["state"] = "MEASURING"
        previous_result = trial.get("result") or {}
        is_revalidation = trial.get("state") == "REVALIDATION"
        continue_state = "REVALIDATION" if is_revalidation else "MEASURING"
        revalidation = (
            previous_result.get("revalidation")
            if isinstance(previous_result.get("revalidation"), dict)
            else {}
        )
        candidate_since = (
            float(revalidation.get("started_at") or start_ts + settle)
            if is_revalidation
            else start_ts + settle
        )
        validation = proposal.get("validation") or {}
        quality_constraints = dict(validation.get("quality_constraints") or {})
        baseline_profile_id = trial.get("baseline_profile")
        candidate_profile_id = trial.get("candidate_profile")
        requested_duration = float(validation.get("duration_seconds") or 0.0)
        if not is_revalidation and requested_duration > 0:
            elapsed = time.time() - start_ts
            if elapsed < requested_duration:
                result = {
                    "verdict": "INSUFFICIENT_DATA",
                    "reason": ["trial_duration_not_reached"],
                    "elapsed_seconds": elapsed,
                    "required_seconds": requested_duration,
                }
                self.db.update_trial(
                    trial["trial_id"], state=continue_state, result=result
                )
                return result
        self.db.update_trial(trial["trial_id"], state="EVALUATING")
        baseline_lookback = float(validation.get("baseline_lookback_hours", 168))
        acceptance = validation.get("acceptance") or {}

        if scene in JOB_SCENES:
            label = validation.get("task_label")
            repetitions = (
                int(self.config.get("experiment.revalidation_sessions", 2))
                if is_revalidation
                else int(self.config.get("experiment.job_min_repetitions", 3))
            )
            candidate_runs = self.db.recent_task_runs(
                candidate_since,
                scene=scene,
                label=label,
                trial_id=trial["trial_id"],
            )
            baseline_runs = [
                run
                for run in self.db.recent_task_runs(
                    start_ts - baseline_lookback * 3600,
                    scene=scene,
                    label=label,
                    profile_id=(
                        str(baseline_profile_id) if baseline_profile_id else None
                    ),
                )
                if float(run.get("end_ts") or 0) < start_ts
            ]
            valid_candidate = [
                run for run in candidate_runs
                if run.get("exit_code") == 0 and (run.get("quality") or {}).get("valid")
            ]
            valid_baseline = [
                run for run in baseline_runs
                if run.get("exit_code") == 0 and (run.get("quality") or {}).get("valid")
            ]
            if len(valid_candidate) < repetitions or len(valid_baseline) < repetitions:
                result = {
                    "verdict": "INSUFFICIENT_DATA",
                    "reason": ["not_enough_complete_task_repetitions"],
                    "candidate_runs": len(valid_candidate),
                    "baseline_runs": len(valid_baseline),
                    "required_repetitions": repetitions,
                }
                self.db.update_trial(
                    trial["trial_id"], state=continue_state, result=result
                )
                return result

            def task_objective(runs: list[dict[str, Any]]) -> dict[str, Any]:
                energies = [float(run["energy_wh"]) for run in runs if run.get("energy_wh") is not None]
                durations = [float(run["duration_s"]) for run in runs]
                return {
                    "type": "energy_per_task",
                    "scene": scene,
                    "valid": bool(energies) and len(energies) == len(runs),
                    "energy_wh": sum(energies) / len(energies) if energies else None,
                    "task_duration_s": sum(durations) / len(durations) if durations else None,
                    "task_completed": True,
                    "repetitions": len(runs),
                }

            baseline_obj = task_objective(valid_baseline[-repetitions:])
            candidate_obj = task_objective(valid_candidate[-repetitions:])
            result = compare_objectives(
                baseline_obj,
                candidate_obj,
                max_task_duration_ratio=acceptance.get("max_task_duration_ratio"),
            )
            result.update(
                {
                    "baseline": {"objective": baseline_obj},
                    "candidate": {"objective": candidate_obj},
                    "comparability": {
                        "score": 1.0 if label else 0.8,
                        "comparable": True,
                        "details": {"task_label_match": bool(label)},
                    },
                }
            )
            verdict = result["verdict"]
            if verdict == "CANDIDATE_WINNER" and not is_revalidation:
                result["initial_verdict"] = "CANDIDATE_WINNER"
                result["verdict"] = "REVALIDATION"
                result["revalidation"] = {
                    "started_at": time.time(),
                    "required_repetitions": int(
                        self.config.get("experiment.revalidation_sessions", 2)
                    ),
                }
                self.db.add_trial_result(
                    trial["trial_id"], "REVALIDATION", candidate_obj,
                    {"valid": True, "task_repetitions": len(valid_candidate)},
                )
                self.db.update_trial(
                    trial["trial_id"], state="REVALIDATION", result=result
                )
                return result
            if verdict == "CANDIDATE_WINNER" and is_revalidation:
                result["revalidation_passed"] = True
                self.db.update_trial(
                    trial["trial_id"], state="CANDIDATE_WINNER", result=result
                )
            self.db.add_trial_result(
                trial["trial_id"], verdict, candidate_obj,
                {"valid": candidate_obj["valid"], "task_repetitions": len(valid_candidate)},
            )
            if not (verdict == "CANDIDATE_WINNER" and is_revalidation):
                self.db.update_trial(
                    trial["trial_id"], state=continue_state, result=result
                )
            else:
                self.db.update_trial(trial["trial_id"], result=result)
            if verdict == "REJECTED":
                self.db.add_rejection(
                    scene,
                    trial.get("parameter"),
                    (proposal.get("change") or {}).get("to"),
                    "task_energy_or_duration_constraint_failed",
                    "automatic_evaluation",
                )
                self.rollback(trial["trial_id"], reason="automatic_task_rejection")
            return result

        candidate_samples = self.db.recent_samples(candidate_since, scene=scene)
        if candidate_profile_id:
            candidate_samples = [
                sample
                for sample in candidate_samples
                if sample.get("profile_id") == candidate_profile_id
            ]

        baseline_min_minutes = float(
            self.config.get("experiment.interactive_min_valid_minutes", 60)
        )
        min_minutes = (
            float(self.config.get("experiment.revalidation_min_valid_minutes", 30))
            if is_revalidation
            else baseline_min_minutes
        )
        requested = validation.get("min_valid_minutes")
        if isinstance(requested, (int, float)) and not is_revalidation:
            min_minutes = max(min_minutes, float(requested))
        min_valid_seconds = min_minutes * 60.0
        baseline_min_valid_seconds = baseline_min_minutes * 60.0
        max_gap = float(self.config.get("collector.max_gap_seconds", 45))
        baseline_min_sessions = int(
            self.config.get("experiment.interactive_min_sessions", 3)
        )
        min_sessions = (
            int(self.config.get("experiment.revalidation_sessions", 2))
            if is_revalidation
            else baseline_min_sessions
        )
        candidate_sessions = self.db.recent_sessions(
            candidate_since,
            scene=scene,
            profile_id=(
                str(candidate_profile_id) if candidate_profile_id else None
            ),
        )
        valid_candidate_sessions = [
            item for item in candidate_sessions if float(item.get("valid_duration_s") or 0) > 0
        ]
        if len(valid_candidate_sessions) < min_sessions:
            result = {
                "verdict": "INSUFFICIENT_DATA",
                "reason": ["not_enough_candidate_sessions"],
                "candidate_sessions": len(valid_candidate_sessions),
                "required_sessions": min_sessions,
            }
            self.db.update_trial(
                trial["trial_id"], state=continue_state, result=result
            )
            return result

        candidate_eval = evaluate_window(
            candidate_samples,
            scene=scene,
            max_gap_seconds=max_gap,
            min_valid_seconds=min_valid_seconds,
            quality_constraints=quality_constraints,
        )
        if not candidate_eval["quality"]["valid"]:
            result = {
                "verdict": "INSUFFICIENT_DATA",
                "candidate": candidate_eval,
                "reason": candidate_eval["quality"]["reasons"],
            }
            self.db.add_trial_result(
                trial["trial_id"], "INSUFFICIENT_DATA",
                candidate_eval["objective"], candidate_eval["quality"],
            )
            self.db.update_trial(
                trial["trial_id"], state=continue_state, result=result
            )
            return result

        baseline_samples = self.db.recent_samples(
            start_ts - baseline_lookback * 3600, scene=scene
        )
        baseline_samples = [
            sample for sample in baseline_samples
            if float(sample.get("ts", 0)) < start_ts
            and (
                not baseline_profile_id
                or sample.get("profile_id") == baseline_profile_id
            )
        ]
        baseline_sessions = [
            item for item in self.db.recent_sessions(
                start_ts - baseline_lookback * 3600,
                scene=scene,
                profile_id=(
                    str(baseline_profile_id) if baseline_profile_id else None
                ),
            )
            if float(item.get("end_ts") or 0) < start_ts
            and float(item.get("valid_duration_s") or 0) > 0
        ]
        if len(baseline_sessions) < baseline_min_sessions:
            result = {
                "verdict": "INSUFFICIENT_DATA",
                "reason": ["not_enough_baseline_sessions"],
                "baseline_sessions": len(baseline_sessions),
                "required_sessions": baseline_min_sessions,
            }
            self.db.update_trial(
                trial["trial_id"], state=continue_state, result=result
            )
            return result
        baseline_eval = evaluate_window(
            baseline_samples,
            scene=scene,
            max_gap_seconds=max_gap,
            min_valid_seconds=baseline_min_valid_seconds,
            quality_constraints=quality_constraints,
        )
        if not baseline_eval["quality"]["valid"]:
            result = {
                "verdict": "INSUFFICIENT_DATA",
                "baseline": baseline_eval,
                "candidate": candidate_eval,
                "reason": ["baseline_not_sufficient"],
            }
            self.db.add_trial_result(
                trial["trial_id"], "INSUFFICIENT_DATA",
                candidate_eval["objective"], candidate_eval["quality"],
            )
            self.db.update_trial(
                trial["trial_id"], state=continue_state, result=result
            )
            return result

        baseline_conditions = self._condition_summary(baseline_samples, scene)
        candidate_conditions = self._condition_summary(candidate_samples, scene)
        comp = comparability_score(baseline_conditions, candidate_conditions)
        if not comp["comparable"]:
            result = {
                "verdict": "INSUFFICIENT_DATA",
                "baseline": baseline_eval,
                "candidate": candidate_eval,
                "comparability": comp,
                "reason": ["low_comparability"],
            }
            self.db.update_trial(
                trial["trial_id"], state=continue_state, result=result
            )
            return result

        result = compare_objectives(
            baseline_eval["objective"],
            candidate_eval["objective"],
            max_power_delta_w=acceptance.get("max_average_power_delta_w"),
            max_task_duration_ratio=acceptance.get("max_task_duration_ratio"),
        )
        result.update(
            {
                "baseline": baseline_eval,
                "candidate": candidate_eval,
                "comparability": comp,
                "baseline_conditions": baseline_conditions,
                "candidate_conditions": candidate_conditions,
            }
        )
        verdict = result["verdict"]
        if verdict == "CANDIDATE_WINNER" and not is_revalidation:
            result["initial_verdict"] = "CANDIDATE_WINNER"
            result["verdict"] = "REVALIDATION"
            result["revalidation"] = {
                "started_at": time.time(),
                "required_sessions": int(
                    self.config.get("experiment.revalidation_sessions", 2)
                ),
                "required_valid_minutes": float(
                    self.config.get("experiment.revalidation_min_valid_minutes", 30)
                ),
            }
            self.db.add_trial_result(
                trial["trial_id"], "REVALIDATION",
                candidate_eval["objective"], candidate_eval["quality"],
            )
            self.db.update_trial(
                trial["trial_id"], state="REVALIDATION", result=result
            )
            return result
        if verdict == "CANDIDATE_WINNER" and is_revalidation:
            result["revalidation_passed"] = True
            self.db.update_trial(
                trial["trial_id"], state="CANDIDATE_WINNER", result=result
            )
        self.db.add_trial_result(
            trial["trial_id"], verdict,
            candidate_eval["objective"], candidate_eval["quality"],
        )
        self.db.update_trial(trial["trial_id"], result=result)
        if verdict == "REJECTED":
            self.db.add_rejection(
                scene,
                trial.get("parameter"),
                (proposal.get("change") or {}).get("to"),
                "objective_or_constraint_failed",
                "automatic_evaluation",
            )
            self.rollback(trial["trial_id"], reason="automatic_rejection")
        return result

    def promote(self, trial_id: str, profile_id: str | None = None) -> dict[str, Any]:
        trial = self.db.get_trial(trial_id)
        if not trial:
            raise TrialError(f"trial not found: {trial_id}")
        result = trial.get("result") or {}
        if result.get("verdict") != "CANDIDATE_WINNER":
            raise TrialError("only a CANDIDATE_WINNER can be promoted")
        proposal = trial.get("proposal") or {}
        parameter = str(trial.get("parameter"))
        desired = (proposal.get("change") or {}).get("to")
        validation_record = {
            "ts": time.time(),
            "scene": trial.get("context_scene"),
            **(
                result.get("candidate_conditions")
                if isinstance(result.get("candidate_conditions"), dict)
                else {}
            ),
        }
        if parameter == "profile.id":
            candidate = self.registry.get(str(desired))
            if not candidate:
                raise TrialError(f"candidate profile not found: {desired}")
            profile_id = profile_id or candidate["profile_id"]
            profile = {
                **candidate,
                "profile_id": profile_id,
                "status": "verified",
                "evidence": {
                    **(candidate.get("evidence") or {}),
                    "source_trial": trial_id,
                    "scene": trial.get("context_scene"),
                    "scenes": sorted(
                        set(
                            [
                                *(candidate.get("evidence") or {}).get("scenes", []),
                                trial.get("context_scene"),
                            ]
                        )
                    ),
                    "result": result,
                },
                "last_validated": validation_record,
            }
        else:
            profile_id = profile_id or f"verified-{trial_id}"
            profile = {
                "profile_id": profile_id,
                "backend": "sysfs",
                "backend_profile": None,
                "content_hash": None,
                "parameters": {parameter: desired},
                "status": "verified",
                "evidence": {
                    "source_trial": trial_id,
                    "scene": trial.get("context_scene"),
                    "scenes": [trial.get("context_scene")],
                    "result": result,
                },
                "last_validated": validation_record,
            }
        self.db.upsert_profile(profile)
        self.registry.load()
        if trial.get("context_scene"):
            self.db.set_context_policy(
                str(trial["context_scene"]),
                profile_id,
                source="trial_promotion",
                evidence={"trial_id": trial_id, "result": result},
            )
        self.db.update_trial(trial_id, state="PROMOTED", end_ts=time.time())
        self._clear_lock()
        return profile
