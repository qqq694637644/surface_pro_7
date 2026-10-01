from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .envelopes import EnvelopeRegistry
from .evidence import EvidenceEngine, hard_strata_key
from .lifecycle import COARSE_OPTIMIZATION, CONTROL_ALLOWED, REOPENED, LifecycleManager
from .storage import Database

EPP_ORDER = (
    "performance",
    "balance_performance",
    "balance_power",
    "power",
)


@dataclass
class CandidateScheduler:
    config: Config
    db: Database
    registry: EnvelopeRegistry

    def __post_init__(self) -> None:
        self.lifecycle = LifecycleManager(self.db)
        self.evidence = EvidenceEngine(self.config, self.db)

    def paused(self) -> bool:
        return bool(self.db.get_meta("scheduler_paused", False))

    def pause(self, reason: str) -> None:
        self.db.set_meta(
            "scheduler_paused",
            {"paused": True, "reason": reason, "ts": time.time()},
        )

    def resume(self) -> None:
        self.db.set_meta("scheduler_paused", False)

    def _pause_payload(self) -> dict[str, Any] | None:
        value = self.db.get_meta("scheduler_paused", False)
        return value if isinstance(value, dict) and value.get("paused") else None

    def _latest_rollup(self) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT payload_json FROM power_rollups ORDER BY bucket_ts DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        import json

        return json.loads(row[0])

    def _eligibility(
        self,
        *,
        baseline_name: str,
        rollup: dict[str, Any] | None,
    ) -> tuple[bool, list[str], dict[str, Any]]:
        reasons: list[str] = []
        details: dict[str, Any] = {}

        if self._pause_payload():
            reasons.append("scheduler_paused")

        automation_level = int(self.config.get("automation.level", 0))
        details["automation_level"] = automation_level
        if automation_level < 2:
            reasons.append("automation_level_does_not_allow_assisted_trials")

        learning = self.lifecycle.learning_state()
        details["learning_lifecycle"] = learning
        if learning not in {COARSE_OPTIMIZATION, REOPENED}:
            reasons.append("learning_lifecycle_does_not_allow_exploration")

        if self.db.active_trial():
            reasons.append("trial_already_active")
        if self.db.active_calibration():
            reasons.append("calibration_active")
        if self.db.active_investigation():
            reasons.append("investigation_active")
        control_state = self.lifecycle.control_state()
        details["control_safety_state"] = control_state
        if control_state != CONTROL_ALLOWED:
            reasons.append("control_safety_state_does_not_allow_trials")

        weekly_trial_count = int(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM trials WHERE created_ts>=?",
                (time.time() - 7 * 86400.0,),
            ).fetchone()[0]
        )
        weekly_trial_budget = int(self.config.get("scheduler.max_trials_per_week", 4))
        details["weekly_trial_count"] = weekly_trial_count
        details["weekly_trial_budget"] = weekly_trial_budget
        if weekly_trial_count >= weekly_trial_budget:
            reasons.append("weekly_trial_budget_exhausted")

        candidate_seconds = float(
            self.db.conn.execute(
                """SELECT COALESCE(SUM(valid_seconds),0) FROM arm_measurements
                WHERE role='candidate' AND end_ts>=?""",
                (time.time() - 86400.0,),
            ).fetchone()[0]
            or 0.0
        )
        candidate_minutes = candidate_seconds / 60.0
        daily_candidate_budget = float(
            self.config.get("scheduler.max_candidate_minutes_per_day", 30.0)
        )
        details["candidate_minutes_last_24h"] = candidate_minutes
        details["candidate_minutes_budget"] = daily_candidate_budget
        if candidate_minutes >= daily_candidate_budget:
            reasons.append("daily_candidate_exposure_budget_exhausted")

        negative_cooldown = float(
            self.config.get("scheduler.negative_feedback_cooldown_seconds", 86400.0)
        )
        if negative_cooldown > 0:
            recent_negative = [
                item
                for item in self.db.recent_feedback(100)
                if item.get("rating") in {"sluggish", "bad", "unstable"}
                and float(item.get("ts") or 0.0) >= time.time() - negative_cooldown
            ]
            if recent_negative:
                reasons.append("negative_feedback_cooldown_active")

        thermal_cooldown = float(
            self.config.get("scheduler.thermal_event_cooldown_seconds", 21600.0)
        )
        if thermal_cooldown > 0:
            recent_thermal = self.db.conn.execute(
                """SELECT start_ts FROM thermal_incidents
                WHERE start_ts>=? ORDER BY start_ts DESC LIMIT 1""",
                (time.time() - thermal_cooldown,),
            ).fetchone()
            if recent_thermal:
                reasons.append("thermal_event_cooldown_active")

        latest_sample = self.db.latest_sample()
        if latest_sample and isinstance(latest_sample.get("battery_pct"), (int, float)):
            low_battery = float(self.config.get("controller.low_battery_percent", 15.0))
            details["battery_pct"] = float(latest_sample["battery_pct"])
            if float(latest_sample["battery_pct"]) <= low_battery:
                reasons.append("battery_below_exploration_threshold")

        measurement_trust = self.db.get_meta("measurement_trust", {})
        details["measurement_trust"] = measurement_trust
        if not isinstance(measurement_trust, dict) or measurement_trust.get("status") != "READY":
            reasons.append("measurement_trust_not_ready")

        baseline = self.registry.get(baseline_name)
        if not baseline or baseline.get("status") != "VERIFIED":
            reasons.append("baseline_not_verified")

        epoch = self.db.active_evidence_epoch()
        details["evidence_epoch"] = epoch
        if not epoch:
            reasons.append("missing_evidence_epoch")

        if not rollup:
            reasons.append("missing_recent_rollup")
            return not reasons, reasons, details

        strata = hard_strata_key({**rollup, "current_envelope": baseline_name})
        details["strata_key"] = strata
        useful = self.evidence.minimum_useful_effect(
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
            strata_key=strata,
        )
        details["minimum_useful_effect"] = useful

        arm = self.evidence.recommended_arm_seconds(
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
            strata_key=strata,
            configured_min_seconds=float(self.config.get("experiments.min_block_seconds", 300.0)),
            gauge_min_seconds=(
                float(measurement_trust.get("recommended_min_arm_seconds"))
                if isinstance(measurement_trust, dict)
                and isinstance(
                    measurement_trust.get("recommended_min_arm_seconds"),
                    (int, float),
                )
                else None
            ),
        )
        details["recommended_arm"] = arm
        if float(arm["recommended_min_arm_seconds"]) > daily_candidate_budget * 60.0:
            reasons.append("minimum_arm_duration_exceeds_daily_candidate_budget")

        noise = useful.get("noise_model")
        minimum_noise_windows = int(self.config.get("scheduler.min_noise_windows", 8))
        if not noise or int(noise.get("sample_count") or 0) < minimum_noise_windows:
            reasons.append("insufficient_noise_baseline")

        rapl = rollup.get("avg_rapl_w")
        details["avg_rapl_w"] = rapl
        minimum_cpu_rapl = float(self.config.get("scheduler.min_cpu_rapl_w", 0.5))
        if not isinstance(rapl, (int, float)):
            reasons.append("missing_cpu_headroom_signal")
        elif float(rapl) < max(
            minimum_cpu_rapl,
            float(useful["minimum_useful_effect_w"]),
        ):
            reasons.append("cpu_headroom_below_minimum_useful_effect")

        return not reasons, reasons, details

    def _candidate_changes(
        self,
        baseline: dict[str, Any],
        *,
        ux_regression: bool,
    ) -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        step = int(self.config.get("scheduler.max_perf_step_pct", 5))
        min_perf = int(self.config.get("scheduler.min_max_perf_pct", 30))
        max_perf = int(self.config.get("scheduler.max_max_perf_pct", 100))
        current_perf = int(baseline["max_perf_pct"])
        current_epp = str(baseline["epp"])

        if ux_regression:
            if current_perf + step <= max_perf:
                result.append(("ux_rescue_max_perf", {"max_perf_pct": current_perf + step}))
            try:
                index = EPP_ORDER.index(current_epp)
            except ValueError:
                index = -1
            if index > 0:
                result.append(("ux_rescue_epp", {"epp": EPP_ORDER[index - 1]}))
            return result

        if current_perf - step >= min_perf:
            result.append(("lower_max_perf", {"max_perf_pct": current_perf - step}))

        try:
            index = EPP_ORDER.index(current_epp)
        except ValueError:
            index = -1
        if 0 <= index < len(EPP_ORDER) - 1:
            result.append(("more_efficient_epp", {"epp": EPP_ORDER[index + 1]}))

        if bool(baseline["turbo"]) and bool(self.config.get("scheduler.allow_turbo_off", True)):
            result.append(("disable_turbo", {"turbo": False}))
        return result

    def candidates(
        self,
        *,
        baseline_name: str,
        ux_regression: bool = False,
        rollup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rollup = rollup or self._latest_rollup()
        eligible, reasons, details = self._eligibility(
            baseline_name=baseline_name,
            rollup=rollup,
        )
        if not eligible:
            return {
                "eligible": False,
                "reasons": reasons,
                "details": details,
                "candidates": [],
            }

        baseline = self.registry.get(baseline_name)
        assert baseline is not None
        epoch_id = str((self.db.active_evidence_epoch() or {})["epoch_id"])
        existing = {
            item["candidate_key"]: item
            for item in self.db.candidate_frontier(
                evidence_epoch_id=epoch_id,
                baseline_envelope=baseline_name,
            )
        }

        candidates: list[dict[str, Any]] = []
        for order, (reason, changes) in enumerate(
            self._candidate_changes(baseline, ux_regression=ux_regression)
        ):
            candidate = self.registry.candidate_from_change(baseline_name, changes)
            key = str(candidate["content_hash"])
            prior = existing.get(key)
            attempts = int((prior or {}).get("attempts") or 0)
            max_attempts = int(self.config.get("scheduler.max_candidate_trials", 2))
            if prior:
                status = prior.get("status")
                if status in {
                    "PROPOSED",
                    "TESTING",
                    "WIN",
                    "LOSE",
                    "PRACTICALLY_EQUIVALENT",
                    "BLOCKED",
                }:
                    continue
                if status == "INCONCLUSIVE" and attempts >= max_attempts:
                    continue
            candidates.append(
                {
                    "candidate_key": key,
                    "baseline_envelope": baseline_name,
                    "evidence_epoch_id": epoch_id,
                    "order": order,
                    "reason": reason,
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                    "retrying_inconclusive": bool(prior and prior.get("status") == "INCONCLUSIVE"),
                    "changes": changes,
                    "candidate": candidate,
                    "proposal": {
                        "kind": "envelope",
                        "baseline_envelope": baseline_name,
                        "changes": changes,
                    },
                }
            )
        return {
            "eligible": True,
            "reasons": [],
            "details": details,
            "candidates": candidates,
            "stop_reason": ("no_untried_eligible_neighbors" if not candidates else None),
        }

    def propose_next(
        self,
        *,
        baseline_name: str,
        ux_regression: bool = False,
        rollup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.candidates(
            baseline_name=baseline_name,
            ux_regression=ux_regression,
            rollup=rollup,
        )
        candidates = result.get("candidates") or []
        if not candidates:
            return {**result, "proposal": None}
        selected = candidates[0]
        prior = self.db.candidate_frontier_entry(selected["candidate_key"]) or {}
        self.db.upsert_candidate_frontier(
            {
                **selected,
                "status": "PROPOSED",
                "attempts": int(prior.get("attempts") or selected.get("attempts") or 0),
                "updated_ts": time.time(),
            }
        )
        return {
            **result,
            "proposal": selected["proposal"],
            "selected": selected,
        }

    def mark_candidate(
        self,
        *,
        candidate_key: str,
        baseline_envelope: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        epoch = self.db.active_evidence_epoch()
        self.db.upsert_candidate_frontier(
            {
                "candidate_key": candidate_key,
                "evidence_epoch_id": (epoch or {}).get("epoch_id"),
                "baseline_envelope": baseline_envelope,
                "status": status,
                "updated_ts": time.time(),
                "result": payload or {},
            }
        )

    def status(self) -> dict[str, Any]:
        epoch = self.db.active_evidence_epoch()
        return {
            "paused": self._pause_payload(),
            "learning_lifecycle": self.lifecycle.learning_state(),
            "investigation_status": self.lifecycle.investigation_state(),
            "evidence_epoch": epoch,
            "frontier": self.db.candidate_frontier(evidence_epoch_id=(epoch or {}).get("epoch_id")),
        }
