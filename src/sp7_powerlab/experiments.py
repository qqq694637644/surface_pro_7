from __future__ import annotations

import json
import time
import uuid
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from .config import Config, load_machine
from .envelopes import EnvelopeRegistry, snapshot_matches_envelope
from .evaluation import compare_candidate, summarize_block
from .storage import Database
from .waste import brightness_bucket, remote_bucket

NEGATIVE_FEEDBACK = {"sluggish", "bad", "unstable"}


class TrialError(RuntimeError):
    pass


class TrialManager:
    def __init__(
        self,
        config: Config,
        db: Database,
        registry: EnvelopeRegistry,
        actuator: Any,
    ):
        self.config = config
        self.db = db
        self.registry = registry
        self.actuator = actuator

    def _default_validation(self) -> dict[str, Any]:
        return {
            "min_block_seconds": float(self.config.get("experiments.min_block_seconds", 300.0)),
            "settle_seconds": float(self.config.get("experiments.settle_seconds", 60.0)),
            "max_gap_seconds": float(self.config.get("collector.max_gap_seconds", 45.0)),
            "max_brightness_delta": float(
                self.config.get("experiments.max_brightness_delta", 10.0)
            ),
            "min_power_saving_w": float(self.config.get("experiments.min_power_saving_w", 0.10)),
            "max_cpu_psi_delta": float(self.config.get("experiments.max_cpu_psi_delta", 2.0)),
            "max_io_psi_delta": float(self.config.get("experiments.max_io_psi_delta", 2.0)),
            "max_thermal_pressure_delta": float(
                self.config.get("experiments.max_thermal_pressure_delta", 0.10)
            ),
            "max_media_drop": float(self.config.get("experiments.max_media_drop", 0.05)),
            "max_sustained_compute_delta": float(
                self.config.get("experiments.max_sustained_compute_delta", 0.10)
            ),
        }

    def _schema_errors(self, proposal: dict[str, Any]) -> list[str]:
        try:
            raw = (
                files("sp7_powerlab.schemas")
                .joinpath("envelope-trial-v2.schema.json")
                .read_text(encoding="utf-8")
            )
            schema = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            return [f"trial schema unavailable: {exc}"]
        validator = Draft202012Validator(schema)
        return [
            error.message
            for error in sorted(
                validator.iter_errors(proposal),
                key=lambda item: list(item.path),
            )
        ]

    def _validation_for_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        effective = self._default_validation()
        if proposal.get("candidate_envelope"):
            effective["min_block_seconds"] = max(
                float(effective["min_block_seconds"]),
                float(self.config.get("experiments.named_min_block_seconds", 600.0)),
            )
        return effective

    @staticmethod
    def _core_telemetry_valid(sample: dict[str, Any]) -> bool:
        return all(
            sample.get(key) is not None
            for key in (
                "battery_power_w",
                "package_temp_c",
                "rapl_power_60s_w",
                "epp",
                "max_perf_pct",
            )
        )

    def validate_proposal(self, proposal: dict[str, Any]) -> list[str]:
        errors = self._schema_errors(proposal)
        if errors:
            return errors
        if proposal.get("kind", "envelope") != "envelope":
            errors.append("only envelope trials are executable in v2.0")
        baseline = proposal.get("baseline_envelope")
        baseline_env = self.registry.get(baseline) if isinstance(baseline, str) else None
        if not isinstance(baseline, str) or not baseline_env:
            errors.append("baseline_envelope must name an existing envelope")
        elif baseline_env.get("status") != "VERIFIED":
            errors.append("baseline_envelope must be VERIFIED")
        changes = proposal.get("changes")
        named = proposal.get("candidate_envelope")
        if bool(changes) == bool(named):
            errors.append("provide exactly one of changes or candidate_envelope")
        elif changes:
            if not isinstance(changes, dict):
                errors.append("changes must be an object")
            elif len(changes) != 1:
                errors.append("parameter tuning changes exactly one primary field")
            elif baseline and self.registry.get(baseline):
                try:
                    self.registry.candidate_from_change(baseline, changes)
                except Exception as exc:
                    errors.append(str(exc))
        elif not isinstance(named, str):
            errors.append("candidate_envelope must be a string")
        elif named == baseline:
            errors.append("candidate_envelope must differ from baseline_envelope")
        else:
            try:
                self.registry.candidate_from_named(named)
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def start(
        self,
        proposal: dict[str, Any],
        current_sample: dict[str, Any],
    ) -> dict[str, Any]:
        if int(self.config.get("automation.level", 0)) < 2:
            raise TrialError("automation level must be >= 2 to start a trial")
        if self.db.active_trial():
            raise TrialError("another trial is already active")
        machine = load_machine(self.config.root)
        if not bool((machine.get("calibration") or {}).get("valid", False)):
            raise TrialError("machine calibration must be valid before starting a trial")
        errors = self.validate_proposal(proposal)
        if errors:
            raise TrialError("; ".join(errors))
        if current_sample.get("battery_status") != "Discharging":
            raise TrialError("trial requires battery Discharging")
        if not self._core_telemetry_valid(current_sample):
            raise TrialError("trial requires valid core telemetry")
        low_battery = float(self.config.get("controller.low_battery_percent", 15.0))
        if (
            isinstance(current_sample.get("battery_pct"), (int, float))
            and float(current_sample["battery_pct"]) <= low_battery
        ):
            raise TrialError("battery is below the experiment threshold")
        if current_sample.get("resume_grace"):
            raise TrialError("trial cannot start during resume grace period")
        if not current_sample.get("thermald_active"):
            raise TrialError("thermald must be active")

        baseline = str(proposal["baseline_envelope"])
        if current_sample.get("current_envelope") != baseline:
            raise TrialError(
                "current envelope must match baseline_envelope before starting a trial"
            )
        baseline_env = self.registry.get(baseline)
        assert baseline_env is not None
        try:
            actual_snapshot = self.actuator.snapshot()
        except Exception as exc:
            raise TrialError(f"cannot verify actual HWP baseline state: {exc}") from exc
        if not snapshot_matches_envelope(actual_snapshot, baseline_env):
            raise TrialError(
                "actual HWP state does not match baseline_envelope before starting a trial"
            )
        if proposal.get("candidate_envelope"):
            candidate = self.registry.candidate_from_named(str(proposal["candidate_envelope"]))
        else:
            candidate = self.registry.candidate_from_change(baseline, dict(proposal["changes"]))
        validation = self._validation_for_proposal(proposal)
        target = {
            "battery_epoch": current_sample.get("battery_epoch"),
            "brightness_bucket": brightness_bucket(current_sample.get("brightness_pct")),
            "brightness_pct": current_sample.get("brightness_pct"),
            "demand_region": current_sample.get("demand_region"),
            "user_active": bool(current_sample.get("user_active")),
            "media_playing": bool(current_sample.get("media_playing")),
            "remote_bucket": remote_bucket(current_sample.get("remote_hint")),
        }
        trial = {
            "trial_id": f"trial-{uuid.uuid4().hex[:12]}",
            "state": "WAITING_FOR_COMPARABLE_WINDOW",
            "kind": "envelope",
            "baseline_envelope": baseline,
            "candidate": candidate,
            "target": target,
            "validation": validation,
            "snapshot": None,
            "current_arm": None,
            "arm_start_ts": None,
            "created_ts": time.time(),
        }
        self.db.create_trial(trial)
        return self.db.get_trial(trial["trial_id"]) or trial

    def _entry_comparable(
        self,
        sample: dict[str, Any],
        target: dict[str, Any],
        validation: dict[str, Any],
    ) -> bool:
        if sample.get("battery_status") != "Discharging":
            return False
        if not self._core_telemetry_valid(sample):
            return False
        if sample.get("resume_grace"):
            return False
        if not sample.get("thermald_active"):
            return False
        if sample.get("thermal_state") != "COOL":
            return False
        if sample.get("local_compute_pressure") == "SUSTAINED":
            return False
        if target.get("battery_epoch") is not None and sample.get("battery_epoch") != target.get(
            "battery_epoch"
        ):
            return False
        if target.get("demand_region") and sample.get("demand_region") != target.get(
            "demand_region"
        ):
            return False
        if brightness_bucket(sample.get("brightness_pct")) != int(
            target.get("brightness_bucket", -1)
        ):
            return False
        reference_brightness = target.get("brightness_pct")
        current_brightness = sample.get("brightness_pct")
        if (
            isinstance(reference_brightness, (int, float))
            and isinstance(current_brightness, (int, float))
            and abs(float(current_brightness) - float(reference_brightness))
            > float(validation.get("max_brightness_delta", 10.0))
        ):
            return False
        if bool(sample.get("media_playing")) != bool(target.get("media_playing")):
            return False
        if remote_bucket(sample.get("remote_hint")) != int(target.get("remote_bucket", 0)):
            return False
        return True

    def _external_window_change(
        self,
        sample: dict[str, Any],
        trial: dict[str, Any],
    ) -> str | None:
        target = trial.get("target") or {}
        validation = trial.get("validation") or {}
        arm = str(trial.get("current_arm") or "")
        if sample.get("battery_status") != "Discharging":
            return "power source changed"
        if sample.get("resume_grace"):
            return "suspend/resume interrupted experiment window"
        if target.get("battery_epoch") is not None and sample.get("battery_epoch") != target.get(
            "battery_epoch"
        ):
            return "battery epoch changed"
        if brightness_bucket(sample.get("brightness_pct")) != int(
            target.get("brightness_bucket", -1)
        ):
            return "brightness bucket changed"
        reference_brightness = target.get("brightness_pct")
        current_brightness = sample.get("brightness_pct")
        if (
            isinstance(reference_brightness, (int, float))
            and isinstance(current_brightness, (int, float))
            and abs(float(current_brightness) - float(reference_brightness))
            > float(validation.get("max_brightness_delta", 10.0))
        ):
            return "brightness changed beyond experiment tolerance"
        if bool(sample.get("user_active")) != bool(target.get("user_active")):
            return "user active/idle state changed"
        if remote_bucket(sample.get("remote_hint")) != int(target.get("remote_bucket", 0)):
            return "remote/local workload changed"
        if arm.startswith("A"):
            if target.get("demand_region") and sample.get("demand_region") != target.get(
                "demand_region"
            ):
                return "baseline workload region changed"
            if bool(sample.get("media_playing")) != bool(target.get("media_playing")):
                return "baseline media state changed"
        elif arm.startswith("B"):
            if not bool(target.get("media_playing")) and bool(sample.get("media_playing")):
                return "new media workload started during candidate arm"
        return None

    def _baseline_is_active(
        self,
        sample: dict[str, Any],
        trial: dict[str, Any],
    ) -> bool:
        return sample.get("current_envelope") == trial.get(
            "baseline_envelope"
        ) and self._entry_comparable(
            sample,
            trial.get("target") or {},
            trial.get("validation") or {},
        )

    def _capture_verified_baseline(self, trial: dict[str, Any]) -> bool:
        envelope = self.registry.get(str(trial["baseline_envelope"]))
        if not envelope or envelope.get("status") != "VERIFIED":
            return False
        try:
            snapshot = self.actuator.snapshot()
        except Exception as exc:
            self.db.add_control_action(
                action="TRIAL_SNAPSHOT_BASELINE",
                envelope=trial.get("baseline_envelope"),
                success=False,
                reason=str(exc),
            )
            return False
        if not snapshot_matches_envelope(snapshot, envelope):
            return False
        self.db.update_trial(trial["trial_id"], snapshot=snapshot)
        return True

    def _fail_trial(self, trial: dict[str, Any], reason: str) -> dict[str, Any]:
        self.db.update_trial(
            trial["trial_id"],
            state="FAILED",
            current_arm=None,
            arm_start_ts=None,
            last_error=reason,
            result={"verdict": "FAILED", "reason": reason},
        )
        return self.db.get_trial(trial["trial_id"]) or trial

    def _apply_candidate(self, trial: dict[str, Any]) -> bool:
        before: dict[str, Any] | None = None
        try:
            before = self.actuator.snapshot()
            result = self.actuator.apply_envelope(trial["candidate"])
        except Exception as exc:
            recovery_error = None
            if before is not None:
                try:
                    self.actuator.restore(before)
                    self.db.set_meta("current_envelope", trial.get("baseline_envelope"))
                except Exception as recovery_exc:
                    recovery_error = str(recovery_exc)
            reason = f"candidate apply failed: {exc}"
            if recovery_error:
                reason += f"; recovery failed: {recovery_error}"
            self.db.add_control_action(
                action="TRIAL_APPLY_CANDIDATE",
                envelope=trial["candidate"].get("name"),
                success=False,
                reason=reason,
                before=before,
            )
            self._fail_trial(trial, reason)
            return False
        self.db.add_control_action(
            action="TRIAL_APPLY_CANDIDATE",
            envelope=trial["candidate"].get("name"),
            success=True,
            reason=trial["trial_id"],
            before=before,
            after=result.get("after"),
        )
        self.db.set_meta(
            "current_envelope",
            f"TRIAL:{trial['trial_id']}:{trial['candidate'].get('content_hash')}",
        )
        return True

    def _restore_baseline(self, trial: dict[str, Any]) -> bool:
        snapshot = trial.get("snapshot")
        if not isinstance(snapshot, dict):
            self.db.add_control_action(
                action="TRIAL_RESTORE_BASELINE",
                envelope=trial.get("baseline_envelope"),
                success=False,
                reason="trial snapshot missing",
            )
            return False
        before = None
        try:
            before = self.actuator.snapshot()
            result = self.actuator.restore(snapshot)
        except Exception as exc:
            self.db.add_control_action(
                action="TRIAL_RESTORE_BASELINE",
                envelope=trial.get("baseline_envelope"),
                success=False,
                reason=str(exc),
                before=before,
            )
            return False
        self.db.add_control_action(
            action="TRIAL_RESTORE_BASELINE",
            envelope=trial.get("baseline_envelope"),
            success=True,
            reason=trial["trial_id"],
            before=before,
            after=result.get("after"),
        )
        self.db.set_meta("current_envelope", trial.get("baseline_envelope"))
        return True

    def _begin_arm(self, trial: dict[str, Any], arm: str, ts: float, *, settle: bool) -> None:
        self.db.update_trial(
            trial["trial_id"],
            state="SETTLING" if settle else "MEASURING",
            current_arm=arm,
            arm_start_ts=ts,
        )

    def _block_rows(self, trial: dict[str, Any], sample_ts: float) -> list[dict[str, Any]]:
        arm = str(trial.get("current_arm") or "")
        start = float(trial.get("arm_start_ts") or sample_ts)
        rows = self.db.samples_between(
            start,
            sample_ts,
            trial_id=trial["trial_id"],
            trial_arm=arm,
        )
        return rows

    def _close_block(self, trial: dict[str, Any], sample_ts: float) -> dict[str, Any] | None:
        validation = trial.get("validation") or {}
        rows = self._block_rows(trial, sample_ts)
        summary = summarize_block(
            rows,
            max_gap_seconds=float(validation.get("max_gap_seconds", 45.0)),
        )
        if float(summary.get("valid_seconds") or 0.0) < float(
            validation.get("min_block_seconds", 300.0)
        ):
            return None

        block = {
            "block_id": f"block-{uuid.uuid4().hex[:12]}",
            "trial_id": trial["trial_id"],
            "arm": trial["current_arm"],
            "start_ts": float(trial["arm_start_ts"]),
            "end_ts": sample_ts,
            "brightness_bucket": int((trial.get("target") or {}).get("brightness_bucket", -1)),
            "demand_region": (trial.get("target") or {}).get("demand_region"),
            **summary,
        }
        self.db.add_trial_block(block)
        return block

    def _evaluate(self, trial: dict[str, Any], *, stage: str) -> dict[str, Any]:
        blocks = self.db.trial_blocks(trial["trial_id"])
        if stage == "initial":
            baseline_arms = {"A1", "A2"}
            candidate_arms = {"B1"}
        elif stage == "revalidation":
            baseline_arms = {"A3"}
            candidate_arms = {"B2"}
        else:
            raise TrialError(f"unknown evaluation stage: {stage}")
        baseline = [block for block in blocks if str(block["arm"]) in baseline_arms]
        candidate = [block for block in blocks if str(block["arm"]) in candidate_arms]
        validation = trial.get("validation") or {}
        result = compare_candidate(
            baseline,
            candidate,
            min_power_saving_w=float(validation.get("min_power_saving_w", 0.10)),
            max_cpu_psi_delta=float(validation.get("max_cpu_psi_delta", 2.0)),
            max_io_psi_delta=float(validation.get("max_io_psi_delta", 2.0)),
            max_thermal_pressure_delta=float(validation.get("max_thermal_pressure_delta", 0.10)),
            max_media_drop=float(validation.get("max_media_drop", 0.05)),
            max_sustained_compute_delta=float(validation.get("max_sustained_compute_delta", 0.10)),
        )
        feedback = [
            item
            for item in self.db.recent_feedback(100)
            if item.get("trial_id") == trial["trial_id"]
        ]
        negative = [item for item in feedback if item.get("rating") in NEGATIVE_FEEDBACK]
        if negative:
            result = {
                **result,
                "verdict": "REJECT",
                "reasons": [*(result.get("reasons") or []), "negative_user_feedback"],
                "negative_feedback": negative,
            }

        self.db.add_trial_result(trial["trial_id"], stage, result["verdict"], result)
        return result

    def _pause_for_window(self, trial: dict[str, Any], reason: str) -> dict[str, Any]:
        arm = str(trial.get("current_arm") or "")
        if arm.startswith("B") and not self._restore_baseline(trial):
            return self._fail_trial(trial, f"{reason}; failed to restore baseline")
        if arm in {"A3", "B2"}:
            next_state = "REVALIDATING"
            self.db.delete_trial_blocks(trial["trial_id"], {"A3", "B2"})
        else:
            next_state = "WAITING_FOR_COMPARABLE_WINDOW"
            self.db.delete_trial_blocks(trial["trial_id"], {"A1", "B1", "A2"})
        self.db.update_trial(
            trial["trial_id"],
            state=next_state,
            current_arm=None,
            arm_start_ts=None,
            last_error=f"paused: {reason}",
        )
        return self.db.get_trial(trial["trial_id"]) or trial

    def rollback(self, trial_id: str, reason: str) -> dict[str, Any]:
        trial = self.db.get_trial(trial_id)
        if not trial:
            raise TrialError(f"trial not found: {trial_id}")
        passive_wait = trial.get("state") in {
            "WAITING_FOR_COMPARABLE_WINDOW",
            "REVALIDATING",
        } and not trial.get("current_arm")
        if not passive_wait and not self._restore_baseline(trial):
            return self._fail_trial(trial, f"{reason}; failed to restore baseline")
        self.db.update_trial(
            trial_id,
            state="ROLLED_BACK",
            current_arm=None,
            arm_start_ts=None,
            result={"verdict": "ROLLED_BACK", "reason": reason},
        )
        return self.db.get_trial(trial_id) or trial

    def reject(self, trial: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if not self._restore_baseline(trial):
            return self._fail_trial(trial, "candidate rejected but baseline restore failed")
        self.db.update_trial(
            trial["trial_id"],
            state="REJECTED",
            current_arm=None,
            arm_start_ts=None,
            result=result,
        )
        self.db.add_rejection(
            f"trial:{trial['baseline_envelope']}:{trial['candidate'].get('content_hash')}",
            "; ".join(result.get("reasons") or ["candidate rejected"]),
            result,
        )
        return self.db.get_trial(trial["trial_id"]) or trial

    def tick(self, sample: dict[str, Any]) -> dict[str, Any] | None:
        trial = self.db.active_trial()
        if not trial:
            return None

        passive_wait = trial["state"] in {
            "WAITING_FOR_COMPARABLE_WINDOW",
            "REVALIDATING",
        } and not trial.get("current_arm")
        if not self._core_telemetry_valid(sample):
            if passive_wait:
                return trial
            return self.rollback(trial["trial_id"], "core telemetry became invalid")
        if sample.get("thermal_state") in {"THERMAL_PRESSURE", "THROTTLING"}:
            if passive_wait:
                return trial
            return self.rollback(trial["trial_id"], "thermal safety preemption")
        if not sample.get("thermald_active"):
            if passive_wait:
                return trial
            return self.rollback(trial["trial_id"], "thermald became unhealthy")
        low_battery = float(self.config.get("controller.low_battery_percent", 15.0))
        if (
            isinstance(sample.get("battery_pct"), (int, float))
            and float(sample["battery_pct"]) <= low_battery
        ):
            if trial["state"] == "WAITING_FOR_COMPARABLE_WINDOW":
                return trial
            return self.rollback(trial["trial_id"], "battery fell below experiment threshold")

        if not passive_wait:
            external_change = self._external_window_change(sample, trial)
            if external_change:
                return self._pause_for_window(trial, external_change)

        if trial["state"] == "WAITING_FOR_COMPARABLE_WINDOW":
            if self._baseline_is_active(sample, trial):
                if not self._capture_verified_baseline(trial):
                    self.db.update_trial(
                        trial["trial_id"],
                        state="FAILED",
                        last_error="actual HWP state does not match verified baseline",
                        result={
                            "verdict": "FAILED",
                            "reason": "actual HWP state does not match verified baseline",
                        },
                    )
                    return self.db.get_trial(trial["trial_id"])
                self._begin_arm(trial, "A1", float(sample["ts"]), settle=False)
                return self.db.get_trial(trial["trial_id"])
            return trial

        if trial["state"] == "REVALIDATING":
            if self._baseline_is_active(sample, trial):
                if not self._capture_verified_baseline(trial):
                    return self._fail_trial(
                        trial,
                        "actual HWP state does not match verified baseline before revalidation",
                    )
                self._begin_arm(trial, "A3", float(sample["ts"]), settle=False)
                return self.db.get_trial(trial["trial_id"])
            return trial

        if trial["state"] == "SETTLING":
            settle = float((trial.get("validation") or {}).get("settle_seconds", 60.0))
            if float(sample["ts"]) - float(trial.get("arm_start_ts") or sample["ts"]) >= settle:
                self.db.update_trial(
                    trial["trial_id"],
                    state="MEASURING",
                    arm_start_ts=float(sample["ts"]),
                )
                return self.db.get_trial(trial["trial_id"])
            return trial

        if trial["state"] != "MEASURING":
            return trial

        block = self._close_block(trial, float(sample["ts"]))
        if not block:
            return trial

        arm = str(trial.get("current_arm"))
        if arm == "A1":
            if not self._apply_candidate(trial):
                return self.db.get_trial(trial["trial_id"])
            self._begin_arm(trial, "B1", float(sample["ts"]), settle=True)
            return self.db.get_trial(trial["trial_id"])

        if arm == "B1":
            if not self._restore_baseline(trial):
                return self._fail_trial(trial, "failed to restore baseline after B1")
            self._begin_arm(trial, "A2", float(sample["ts"]), settle=True)
            return self.db.get_trial(trial["trial_id"])

        if arm == "A2":
            result = self._evaluate(trial, stage="initial")
            if result["verdict"] != "CANDIDATE_WINNER":
                return self.reject(trial, result)
            self.db.update_trial(
                trial["trial_id"],
                state="REVALIDATING",
                current_arm=None,
                arm_start_ts=None,
                result={"initial_result": result},
            )
            return self.db.get_trial(trial["trial_id"])

        if arm == "A3":
            if not self._apply_candidate(trial):
                return self.db.get_trial(trial["trial_id"])
            self._begin_arm(trial, "B2", float(sample["ts"]), settle=True)
            return self.db.get_trial(trial["trial_id"])

        if arm == "B2":
            if not self._restore_baseline(trial):
                return self._fail_trial(trial, "failed to restore baseline after B2")
            result = self._evaluate(trial, stage="revalidation")
            if result["verdict"] != "CANDIDATE_WINNER":
                return self.reject(
                    trial,
                    {
                        "verdict": "REJECT",
                        "reasons": result.get("reasons") or [],
                        "initial_result": (trial.get("result") or {}).get("initial_result"),
                        "revalidation_result": result,
                    },
                )
            combined = {
                "verdict": "CANDIDATE_WINNER",
                "initial_result": (trial.get("result") or {}).get("initial_result"),
                "revalidation_result": result,
            }
            self.db.update_trial(
                trial["trial_id"],
                state="VERIFIED_WINNER",
                current_arm=None,
                arm_start_ts=None,
                result=combined,
            )
            return self.db.get_trial(trial["trial_id"])

        raise TrialError(f"unknown trial arm: {arm}")

    def promote(self, trial_id: str) -> dict[str, Any]:
        trial = self.db.get_trial(trial_id)
        if not trial:
            raise TrialError(f"trial not found: {trial_id}")
        if trial["state"] != "VERIFIED_WINNER":
            raise TrialError("only VERIFIED_WINNER trials can be promoted")
        if int(self.config.get("automation.level", 0)) < 2:
            raise TrialError("automation level must be >= 2 to promote a trial")
        negative = [
            item
            for item in self.db.recent_feedback(200)
            if item.get("trial_id") == trial_id and item.get("rating") in NEGATIVE_FEEDBACK
        ]
        if negative:
            raise TrialError("trial has negative user feedback and cannot be promoted")
        machine = load_machine(self.config.root)
        if not bool((machine.get("calibration") or {}).get("valid", False)):
            raise TrialError("machine calibration must be valid before promotion")
        calibration_version = int((machine.get("calibration") or {}).get("version") or 0)
        previous_verified = self.registry.get(str(trial["baseline_envelope"]))
        if not previous_verified or previous_verified.get("status") != "VERIFIED":
            raise TrialError("baseline envelope is no longer VERIFIED at promotion time")
        promotion_evidence = {
            **(trial.get("result") or {}),
            "previous_verified_envelope": previous_verified,
        }
        promoted = self.registry.promote_candidate(
            trial["candidate"],
            battery_epoch=self.db.active_battery_epoch(),
            system_fingerprint=self.db.active_system_fingerprint(),
            calibration_version=calibration_version,
            result=promotion_evidence,
        )
        self.db.update_trial(
            trial_id,
            state="PROMOTED",
            result={**promotion_evidence, "promoted": promoted},
        )
        self.db.set_meta("current_envelope", None)
        return promoted

    def feedback(
        self,
        rating: str,
        *,
        trial_id: str | None = None,
        envelope: str | None = None,
        notes: str | None = None,
    ) -> None:
        if rating not in {"good", "sluggish", "bad", "unstable"}:
            raise TrialError("feedback must be good/sluggish/bad/unstable")
        self.db.add_feedback(
            rating,
            trial_id=trial_id,
            envelope=envelope,
            notes=notes,
        )
        if envelope and rating in NEGATIVE_FEEDBACK:
            try:
                self.registry.set_status(envelope, "BLOCKED")
            except KeyError:
                pass
            self.db.add_rejection(
                f"envelope:{envelope}",
                f"user feedback: {rating}",
                {"notes": notes},
            )
        if trial_id and rating in NEGATIVE_FEEDBACK:
            trial = self.db.get_trial(trial_id)
            active = self.db.active_trial()
            if active and active["trial_id"] == trial_id:
                self.rollback(trial_id, f"user feedback: {rating}")
            elif trial and trial.get("state") == "VERIFIED_WINNER":
                previous = trial.get("result") or {}
                self.db.update_trial(
                    trial_id,
                    state="REJECTED",
                    result={
                        **previous,
                        "verdict": "REJECT",
                        "reasons": [
                            *(previous.get("reasons") or []),
                            "negative_user_feedback_after_revalidation",
                        ],
                    },
                )
                self.db.add_rejection(
                    f"trial:{trial_id}",
                    f"user feedback after revalidation: {rating}",
                    {"notes": notes},
                )
            elif trial and trial.get("state") == "PROMOTED":
                previous = trial.get("result") or {}
                promoted = previous.get("promoted") or {}
                promoted_name = promoted.get("name")
                prior_verified = previous.get("previous_verified_envelope")
                if promoted_name:
                    try:
                        self.registry.set_status(str(promoted_name), "BLOCKED")
                    except KeyError:
                        pass
                current = self.db.get_meta("current_envelope")
                if promoted_name and current == promoted_name:
                    if not self._restore_baseline(trial):
                        self._fail_trial(
                            trial,
                            "negative feedback after promotion; failed to restore prior baseline",
                        )
                        return
                if isinstance(prior_verified, dict) and prior_verified.get("name") == promoted_name:
                    machine = load_machine(self.config.root)
                    restored = self.registry.restore_previous_verified(
                        prior_verified,
                        battery_epoch=self.db.active_battery_epoch(),
                        system_fingerprint=self.db.active_system_fingerprint(),
                        calibration_version=int(
                            (machine.get("calibration") or {}).get("version") or 0
                        ),
                        reason=f"user feedback after promotion: {rating}",
                    )
                    if current == promoted_name:
                        self.db.set_meta("current_envelope", restored["name"])
                self.db.update_trial(
                    trial_id,
                    state="REJECTED",
                    result={
                        **previous,
                        "verdict": "REJECT",
                        "reasons": [
                            *(previous.get("reasons") or []),
                            "negative_user_feedback_after_promotion",
                        ],
                    },
                )
                self.db.add_rejection(
                    f"trial:{trial_id}",
                    f"user feedback after promotion: {rating}",
                    {"notes": notes, "promoted": promoted},
                )
