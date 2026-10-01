from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .config import Config
from .storage import Database

NON_EVALUABLE_REASONS = {
    "missing_arm_measurement",
    "data_quality_failure",
    "invalid_comparison",
    "missing_blocks",
    "missing_power",
}
EFFECT_EPSILON_W = 1e-9


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _mad(values: list[float]) -> float | None:
    if not values:
        return None
    median = statistics.median(values)
    return statistics.median(abs(value - median) for value in values)


def hard_strata_key(value: dict[str, Any]) -> str:
    epoch = int(value.get("battery_epoch") or 0)
    envelope = str(value.get("current_envelope") or value.get("envelope") or "UNMANAGED")
    active = 1 if bool(value.get("user_active")) else 0
    media = 1 if bool(value.get("media_playing")) else 0
    remote = int(
        value.get("remote_bucket") or (1 if float(value.get("remote_hint") or 0.0) >= 0.5 else 0)
    )
    return f"bat={epoch}|env={envelope}|active={active}|media={media}|remote={remote}"


def robust_distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "median_power_w": None,
            "mad_power_w": None,
            "p10_power_w": None,
            "p25_power_w": None,
            "p75_power_w": None,
            "p90_power_w": None,
            "noise_floor_w": None,
            "sample_count": 0,
        }
    median = statistics.median(values)
    mad = _mad(values) or 0.0
    p10 = _percentile(values, 0.10)
    p25 = _percentile(values, 0.25)
    p75 = _percentile(values, 0.75)
    p90 = _percentile(values, 0.90)
    iqr_half = ((p75 or median) - (p25 or median)) / 2.0
    # Conservative robust engineering floor. Stage A can tune the multiplier,
    # but this keeps a few lucky windows from manufacturing tiny "wins".
    noise_floor = max(2.0 * mad, iqr_half, 0.0)
    return {
        "median_power_w": median,
        "mad_power_w": mad,
        "p10_power_w": p10,
        "p25_power_w": p25,
        "p75_power_w": p75,
        "p90_power_w": p90,
        "noise_floor_w": noise_floor,
        "sample_count": len(values),
    }


@dataclass
class NoiseTracker:
    config: Config
    db: Database

    def observe_rollup(
        self,
        rollup: dict[str, Any],
        *,
        evidence_epoch_id: str,
    ) -> dict[str, Any] | None:
        power = rollup.get("avg_power_w")
        if not isinstance(power, (int, float)):
            return None
        if rollup.get("trial_id"):
            return None
        envelope = str(rollup.get("current_envelope") or "")
        if not envelope or envelope.startswith("TRIAL:"):
            return None
        if rollup.get("demand_region") == "MIXED":
            return None
        if rollup.get("thermal_start") in {"THERMAL_PRESSURE", "THROTTLING"}:
            return None

        strata = hard_strata_key(rollup)
        now = float(rollup.get("bucket_ts") or time.time())
        windows = (
            24.0 * 3600.0,
            7.0 * 86400.0,
            30.0 * 86400.0,
        )
        recent = self.db.recent_rollups(now - max(windows), limit=5000)
        comparable = [
            item
            for item in recent
            if hard_strata_key(item) == strata
            and not item.get("trial_id")
            and item.get("demand_region") != "MIXED"
            and isinstance(item.get("avg_power_w"), (int, float))
        ]

        latest_distribution: dict[str, Any] | None = None
        for window_seconds in windows:
            powers = [
                float(item["avg_power_w"])
                for item in comparable
                if float(item.get("bucket_ts") or 0.0) >= now - window_seconds
            ]
            stats = robust_distribution(powers)
            distribution = {
                "updated_ts": time.time(),
                "evidence_epoch_id": evidence_epoch_id,
                "strata_key": strata,
                "window_seconds": window_seconds,
                **stats,
            }
            self.db.upsert_noise_distribution(distribution)
            latest_distribution = distribution

        reference_min = int(self.config.get("evidence.reference_min_windows", 8))
        if self.db.reference_baseline(evidence_epoch_id, strata) is None:
            reference_rows = sorted(
                comparable,
                key=lambda item: float(item.get("bucket_ts") or 0.0),
            )[:reference_min]
            if len(reference_rows) >= reference_min:
                reference_stats = robust_distribution(
                    [float(item["avg_power_w"]) for item in reference_rows]
                )
                self.db.upsert_reference_baseline(
                    {
                        "reference_id": f"ref-{uuid.uuid4().hex[:12]}",
                        "created_ts": time.time(),
                        "evidence_epoch_id": evidence_epoch_id,
                        "strata_key": strata,
                        "envelope": envelope,
                        "frozen": True,
                        **reference_stats,
                    }
                )
        return latest_distribution


def build_crossover_episode(
    *,
    trial_id: str,
    stage: str,
    arm_measurements: list[dict[str, Any]],
    evidence_epoch_id: str | None,
    candidate_key: str | None = None,
    constraint_reasons: list[str] | None = None,
) -> dict[str, Any]:
    by_arm = {str(item["arm"]): item for item in arm_measurements}
    if stage == "initial":
        required = ("A1", "B1", "A2")
        baseline_arms = ("A1", "A2")
        candidate_arm = "B1"
    elif stage == "revalidation":
        required = ("A3", "B2")
        baseline_arms = ("A3",)
        candidate_arm = "B2"
    else:
        raise ValueError(f"unknown crossover stage: {stage}")

    missing = [arm for arm in required if arm not in by_arm]
    quality_failures = [
        arm for arm in required if arm in by_arm and by_arm[arm].get("data_quality") != "OK"
    ]
    reasons = list(constraint_reasons or [])
    if missing:
        reasons.append("missing_arm_measurement")
    if quality_failures:
        reasons.append("data_quality_failure")

    baseline_power = [
        float(by_arm[arm]["avg_power_w"])
        for arm in baseline_arms
        if arm in by_arm and isinstance(by_arm[arm].get("avg_power_w"), (int, float))
    ]
    candidate = by_arm.get(candidate_arm)
    candidate_power = (
        float(candidate["avg_power_w"])
        if candidate and isinstance(candidate.get("avg_power_w"), (int, float))
        else None
    )
    baseline_avg = statistics.fmean(baseline_power) if baseline_power else None
    paired_effect_w = (
        candidate_power - baseline_avg
        if candidate_power is not None and baseline_avg is not None
        else None
    )

    paired_effect_wh = None
    if candidate is not None and baseline_arms:
        base_rates = []
        for arm in baseline_arms:
            item = by_arm.get(arm)
            if not item:
                continue
            energy = item.get("integrated_energy_wh")
            seconds = item.get("valid_seconds")
            if (
                isinstance(energy, (int, float))
                and isinstance(seconds, (int, float))
                and seconds > 0
            ):
                base_rates.append(float(energy) / float(seconds))
        c_energy = candidate.get("integrated_energy_wh")
        c_seconds = candidate.get("valid_seconds")
        if (
            base_rates
            and isinstance(c_energy, (int, float))
            and isinstance(c_seconds, (int, float))
            and c_seconds > 0
        ):
            # Wh/s delta normalized to one hour -> Wh for an hour, numerically W.
            paired_effect_wh = (
                float(c_energy) / float(c_seconds) - statistics.fmean(base_rates)
            ) * 3600.0

    return {
        "episode_id": f"xo-{uuid.uuid4().hex[:12]}",
        "trial_id": trial_id,
        "candidate_key": candidate_key,
        "stage": stage,
        "evidence_epoch_id": evidence_epoch_id,
        "arm_ids": [by_arm[arm]["arm_id"] for arm in required if arm in by_arm],
        "baseline_arms": list(baseline_arms),
        "candidate_arm": candidate_arm,
        "baseline_avg_power_w": baseline_avg,
        "candidate_avg_power_w": candidate_power,
        "paired_effect_w": paired_effect_w,
        "paired_effect_wh": paired_effect_wh,
        "constraint_reasons": reasons,
        "valid": not reasons and paired_effect_w is not None,
        "created_ts": time.time(),
    }


@dataclass
class EvidenceEngine:
    config: Config
    db: Database

    def recommended_arm_seconds(
        self,
        *,
        evidence_epoch_id: str | None,
        strata_key: str | None,
        configured_min_seconds: float,
        gauge_min_seconds: float | None,
    ) -> dict[str, Any]:
        practical = float(self.config.get("evidence.practical_threshold_w", 0.10))
        confidence_multiplier = float(
            self.config.get("evidence.noise_arm_confidence_multiplier", 2.0)
        )
        base_window_seconds = float(self.config.get("collector.rollup_seconds", 60.0))
        useful = self.minimum_useful_effect(
            evidence_epoch_id=evidence_epoch_id,
            strata_key=strata_key,
        )
        noise_floor = useful.get("noise_floor_w")
        noise_seconds = None
        if isinstance(noise_floor, (int, float)) and float(noise_floor) > 0 and practical > 0:
            required_windows = max(
                1.0,
                (confidence_multiplier * float(noise_floor) / practical) ** 2,
            )
            noise_seconds = base_window_seconds * required_windows
        recommended = max(
            float(configured_min_seconds),
            float(gauge_min_seconds or 0.0),
            float(noise_seconds or 0.0),
        )
        return {
            "recommended_min_arm_seconds": recommended,
            "configured_min_arm_seconds": float(configured_min_seconds),
            "gauge_min_arm_seconds": (
                float(gauge_min_seconds) if isinstance(gauge_min_seconds, (int, float)) else None
            ),
            "noise_min_arm_seconds": noise_seconds,
            "noise_floor_w": noise_floor,
            "practical_threshold_w": practical,
            "noise_arm_confidence_multiplier": confidence_multiplier,
            "noise_base_window_seconds": base_window_seconds,
        }

    def minimum_useful_effect(
        self,
        *,
        evidence_epoch_id: str | None,
        strata_key: str | None,
    ) -> dict[str, Any]:
        practical = float(self.config.get("evidence.practical_threshold_w", 0.10))
        noise_floor = None
        noise = None
        if evidence_epoch_id and strata_key:
            noise = self.db.noise_distribution(
                evidence_epoch_id,
                strata_key,
                window_seconds=7.0 * 86400.0,
            )
            if noise and isinstance(noise.get("noise_floor_w"), (int, float)):
                noise_floor = float(noise["noise_floor_w"])
        mue = max(practical, noise_floor or 0.0)
        return {
            "minimum_useful_effect_w": mue,
            "practical_threshold_w": practical,
            "noise_floor_w": noise_floor,
            "noise_model": noise,
        }

    def provisional(
        self,
        episode: dict[str, Any],
        *,
        minimum_useful_effect_w: float,
    ) -> dict[str, Any]:
        reasons = list(episode.get("constraint_reasons") or [])
        effect = episode.get("paired_effect_w")
        non_evaluable = [reason for reason in reasons if reason in NON_EVALUABLE_REASONS]
        hard_vetoes = [reason for reason in reasons if reason not in NON_EVALUABLE_REASONS]
        result_reasons = list(reasons)
        if non_evaluable or not isinstance(effect, (int, float)):
            verdict = "INCONCLUSIVE"
            if not result_reasons:
                result_reasons.append("missing_valid_paired_effect")
        elif hard_vetoes:
            verdict = "LOSE"
        elif float(effect) <= -minimum_useful_effect_w + EFFECT_EPSILON_W:
            verdict = "PROVISIONAL_WIN"
        elif float(effect) >= minimum_useful_effect_w - EFFECT_EPSILON_W:
            verdict = "LOSE"
            result_reasons.append("candidate_uses_more_power")
        else:
            verdict = "PRACTICALLY_EQUIVALENT"
            result_reasons.append("effect_within_minimum_useful_effect")
        return {
            "verdict": verdict,
            "reasons": result_reasons,
            "paired_effect_w": effect,
            "minimum_useful_effect_w": minimum_useful_effect_w,
            "evidence_count": 1,
        }

    def decide(
        self,
        episodes: list[dict[str, Any]],
        *,
        minimum_useful_effect_w: float,
        trial_id: str | None = None,
        candidate_key: str | None = None,
        evidence_epoch_id: str | None = None,
    ) -> dict[str, Any]:
        valid = [
            episode
            for episode in episodes
            if episode.get("valid") and isinstance(episode.get("paired_effect_w"), (int, float))
        ]
        all_reasons = [
            reason for episode in episodes for reason in (episode.get("constraint_reasons") or [])
        ]
        non_evaluable_reasons = [
            reason for reason in all_reasons if reason in NON_EVALUABLE_REASONS
        ]
        hard_reasons = [reason for reason in all_reasons if reason not in NON_EVALUABLE_REASONS]
        effects = [float(episode["paired_effect_w"]) for episode in valid]
        min_count = int(self.config.get("evidence.min_crossover_episodes", 2))
        medium_count = max(
            min_count,
            int(self.config.get("evidence.medium_effect_min_crossover_episodes", 3)),
        )
        large_effect_multiplier = float(self.config.get("evidence.large_effect_multiplier", 2.0))
        consistency_required = float(self.config.get("evidence.direction_consistency", 0.75))

        median_effect = statistics.median(effects) if effects else None
        save_fraction = sum(effect < 0 for effect in effects) / len(effects) if effects else 0.0
        within_fraction = (
            sum(abs(effect) < minimum_useful_effect_w - EFFECT_EPSILON_W for effect in effects)
            / len(effects)
            if effects
            else 0.0
        )
        required_count = min_count
        if (
            median_effect is not None
            and median_effect < 0
            and abs(median_effect) < minimum_useful_effect_w * large_effect_multiplier
        ):
            required_count = medium_count

        reasons: list[str] = []
        if hard_reasons:
            verdict = "LOSE"
            reasons.extend(sorted(set(hard_reasons)))
        elif non_evaluable_reasons and len(effects) < required_count:
            verdict = "INCONCLUSIVE"
            reasons.extend(sorted(set(non_evaluable_reasons)))
        elif len(effects) < required_count:
            verdict = "INCONCLUSIVE"
            reasons.append("evidence_budget_not_yet_satisfied")
        elif (
            median_effect is not None
            and median_effect >= minimum_useful_effect_w - EFFECT_EPSILON_W
        ):
            verdict = "LOSE"
            reasons.append("candidate_uses_more_power")
        elif (
            median_effect is not None
            and median_effect <= -minimum_useful_effect_w + EFFECT_EPSILON_W
            and save_fraction >= consistency_required
        ):
            verdict = "WIN"
        elif within_fraction >= consistency_required:
            verdict = "PRACTICALLY_EQUIVALENT"
            reasons.append("effect_within_minimum_useful_effect")
        else:
            verdict = "INCONCLUSIVE"
            reasons.append("effect_direction_inconsistent")

        decision = {
            "decision_id": f"ed-{uuid.uuid4().hex[:12]}",
            "trial_id": trial_id,
            "candidate_key": candidate_key,
            "evidence_epoch_id": evidence_epoch_id,
            "verdict": verdict,
            "minimum_useful_effect_w": minimum_useful_effect_w,
            "median_effect_w": median_effect,
            "direction_consistency": save_fraction,
            "within_mue_fraction": within_fraction,
            "evidence_count": len(effects),
            "required_evidence_count": required_count,
            "episode_ids": [episode.get("episode_id") for episode in valid],
            "reasons": reasons,
            "created_ts": time.time(),
        }
        self.db.add_evidence_decision(decision)
        return decision
