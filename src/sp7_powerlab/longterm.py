from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .evidence import reference_strata_key
from .measurement import measurement_energy_summary, measurement_trust_matches_epoch
from .storage import Database

NET_BENEFIT_MODES = (
    "MONITORING_OVERHEAD",
    "DYNAMIC_CONTROLLER",
    "FULL_POWERLAB",
)


def minutes_gained_per_charge(
    *,
    usable_battery_wh: float,
    baseline_power_w: float,
    candidate_power_w: float,
) -> float | None:
    if usable_battery_wh <= 0 or baseline_power_w <= 0 or candidate_power_w <= 0:
        return None
    baseline_hours = usable_battery_wh / baseline_power_w
    candidate_hours = usable_battery_wh / candidate_power_w
    return (candidate_hours - baseline_hours) * 60.0


@dataclass
class UsageCoverage:
    db: Database

    def summarize(
        self,
        *,
        since_ts: float,
        evidence_epoch_id: str | None = None,
    ) -> dict[str, Any]:
        rows = self.db.recent_rollups(
            since_ts,
            limit=10000,
            evidence_epoch_id=evidence_epoch_id,
        )
        rows = sorted(rows, key=lambda item: float(item.get("bucket_ts") or 0.0))
        total_seconds = 0.0
        verified_seconds = 0.0
        trusted_seconds = 0.0
        by_envelope: dict[str, float] = {}
        uncovered: dict[str, float] = {}

        for row in rows:
            if evidence_epoch_id and str(row.get("evidence_epoch_id") or "") != str(
                evidence_epoch_id
            ):
                continue
            seconds = float(row.get("valid_seconds") or 0.0)
            if seconds <= 0 or row.get("trial_id"):
                continue
            total_seconds += seconds
            envelope_name = str(row.get("current_envelope") or "UNMANAGED")
            by_envelope[envelope_name] = by_envelope.get(envelope_name, 0.0) + seconds
            envelope = self.db.envelope(envelope_name)
            verified = bool(envelope and envelope.get("status") == "VERIFIED")
            if verified:
                verified_seconds += seconds

            trusted = False
            if verified and evidence_epoch_id and row.get("demand_region") != "MIXED":
                reference = self.db.reference_baseline(
                    evidence_epoch_id,
                    reference_strata_key(row),
                )
                trusted = reference is not None
            if trusted:
                trusted_seconds += seconds
            else:
                key = str(row.get("demand_region") or "UNKNOWN")
                uncovered[key] = uncovered.get(key, 0.0) + seconds

        def fraction(value: float) -> float | None:
            return value / total_seconds if total_seconds > 0 else None

        return {
            "since_ts": since_ts,
            "total_valid_seconds": total_seconds,
            "verified_seconds": verified_seconds,
            "trusted_seconds": trusted_seconds,
            "verified_fraction": fraction(verified_seconds),
            "trusted_fraction": fraction(trusted_seconds),
            "by_envelope_seconds": dict(
                sorted(by_envelope.items(), key=lambda item: item[1], reverse=True)
            ),
            "uncovered_demand_seconds": dict(
                sorted(uncovered.items(), key=lambda item: item[1], reverse=True)
            ),
        }


def assess_net_benefit(
    runs: list[dict[str, Any]],
    *,
    practical_threshold_w: float,
    evidence_epoch_id: str | None = None,
) -> dict[str, Any]:
    selected_campaign: str | None = None
    filtered_runs = [
        run
        for run in runs
        if run.get("end_ts") is not None
        and (
            evidence_epoch_id is None
            or str(((run.get("result") or {}).get("evidence_epoch_id")) or "")
            == str(evidence_epoch_id)
        )
    ]
    if evidence_epoch_id is not None:
        campaigns: dict[str, list[dict[str, Any]]] = {}
        for run in filtered_runs:
            campaign = str(((run.get("result") or {}).get("campaign_id")) or "")
            if campaign:
                campaigns.setdefault(campaign, []).append(run)
        complete_campaigns = [
            (campaign, campaign_runs)
            for campaign, campaign_runs in campaigns.items()
            if set(NET_BENEFIT_MODES) <= {str(run.get("mode") or "") for run in campaign_runs}
        ]
        if complete_campaigns:
            selected_campaign, filtered_runs = max(
                complete_campaigns,
                key=lambda item: max(
                    float(run.get("end_ts") or run.get("start_ts") or 0.0) for run in item[1]
                ),
            )
        else:
            filtered_runs = []

    latest: dict[str, dict[str, Any]] = {}
    for run in sorted(
        filtered_runs,
        key=lambda item: float(item.get("end_ts") or item.get("start_ts") or 0.0),
        reverse=True,
    ):
        mode = str(run.get("mode") or "")
        if mode in NET_BENEFIT_MODES and mode not in latest and run.get("end_ts") is not None:
            latest[mode] = run

    missing = [mode for mode in NET_BENEFIT_MODES if mode not in latest]
    deltas: dict[str, float | None] = {}
    for mode in NET_BENEFIT_MODES:
        result = (latest.get(mode) or {}).get("result") or {}
        value = result.get("candidate_minus_reference_w")
        deltas[mode] = float(value) if isinstance(value, (int, float)) else None

    if missing or any(deltas[mode] is None for mode in NET_BENEFIT_MODES):
        return {
            "complete": False,
            "missing_modes": missing,
            "practical_threshold_w": practical_threshold_w,
            "deltas_w": deltas,
            "recommendation": "NEED_MORE_DATA",
            "reasons": [
                (
                    "complete same-epoch same-campaign fixed-good crossover comparison "
                    "is unavailable"
                    if evidence_epoch_id is not None
                    else "complete fixed-good crossover comparison is unavailable"
                )
            ],
            "campaign_id": selected_campaign,
            "latest_runs": latest,
        }

    monitoring_delta = float(deltas["MONITORING_OVERHEAD"])
    dynamic_delta = float(deltas["DYNAMIC_CONTROLLER"])
    full_delta = float(deltas["FULL_POWERLAB"])
    reasons: list[str] = []

    if full_delta <= -practical_threshold_w:
        recommendation = "KEEP_FULL_POWERLAB"
        reasons.append("full PowerLab has practically meaningful net battery savings")
    elif dynamic_delta <= -practical_threshold_w:
        recommendation = "KEEP_DYNAMIC_REDUCE_MONITORING"
        reasons.append(
            "dynamic control helps but full PowerLab does not clear the net-benefit threshold"
        )
        if monitoring_delta > 0:
            reasons.append("monitoring overhead consumes part of the controller savings")
    else:
        recommendation = "FIXED_GOOD_ENVELOPE"
        reasons.append("dynamic/full PowerLab does not beat fixed-good by a practical margin")

    return {
        "complete": True,
        "missing_modes": [],
        "practical_threshold_w": practical_threshold_w,
        "deltas_w": deltas,
        "monitoring_overhead_w": monitoring_delta,
        "dynamic_net_saving_w": -dynamic_delta,
        "full_net_saving_w": -full_delta,
        "recommendation": recommendation,
        "reasons": reasons,
        "campaign_id": selected_campaign,
        "latest_runs": latest,
    }


@dataclass
class StableReadiness:
    config: Config
    db: Database

    def assess(self, *, now: float | None = None) -> dict[str, Any]:
        now = float(now or time.time())
        reasons: list[str] = []
        epoch = self.db.active_evidence_epoch()
        if not epoch:
            reasons.append("missing_evidence_epoch")

        measurement_trust = self.db.get_meta("measurement_trust", {})
        if not measurement_trust_matches_epoch(measurement_trust, epoch):
            reasons.append("measurement_trust_not_ready")

        coverage_days = int(self.config.get("stable.coverage_days", 30))
        target_fraction = float(self.config.get("stable.target_trusted_fraction", 0.90))
        coverage = UsageCoverage(self.db).summarize(
            since_ts=now - coverage_days * 86400.0,
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
        )
        trusted_fraction = coverage.get("trusted_fraction")
        if not isinstance(trusted_fraction, (int, float)):
            reasons.append("trusted_usage_coverage_unavailable")
        elif float(trusted_fraction) < target_fraction:
            reasons.append("trusted_usage_coverage_below_target")

        reference_count = 0
        if epoch:
            reference_count = int(
                self.db.conn.execute(
                    """SELECT COUNT(*) FROM reference_baselines
                    WHERE evidence_epoch_id=? AND frozen=1""",
                    (epoch["epoch_id"],),
                ).fetchone()[0]
            )
        if reference_count <= 0:
            reasons.append("missing_frozen_reference_baseline")

        if self.db.active_trial():
            reasons.append("trial_active")
        if self.db.active_investigation():
            reasons.append("investigation_active")

        open_events = [
            event
            for event in self.db.recent_unexpected_power_events(200)
            if event.get("status") == "OPEN"
        ]
        if open_events:
            reasons.append("unresolved_unexpected_power_event")

        overhead_runs = [
            run for run in self.db.monitoring_overhead_runs(100) if run.get("end_ts") is not None
        ]
        net_benefit = assess_net_benefit(
            overhead_runs,
            practical_threshold_w=float(self.config.get("evidence.practical_threshold_w", 0.10)),
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
        )
        if not net_benefit["complete"]:
            reasons.append("net_benefit_validation_incomplete")

        feedback_lookback_days = int(self.config.get("stable.feedback_lookback_days", 7))
        negative_feedback = [
            item
            for item in self.db.recent_feedback(200)
            if float(item.get("ts") or 0.0) >= now - feedback_lookback_days * 86400.0
            and item.get("rating") in {"sluggish", "bad", "unstable"}
        ]
        if negative_feedback:
            reasons.append("recent_negative_user_feedback")

        return {
            "ready": not reasons,
            "reasons": reasons,
            "evidence_epoch": epoch,
            "measurement_trust": measurement_trust,
            "coverage_days": coverage_days,
            "target_trusted_fraction": target_fraction,
            "usage_coverage": coverage,
            "frozen_reference_count": reference_count,
            "net_benefit": net_benefit,
            "open_unexpected_power_events": len(open_events),
            "recent_negative_feedback_count": len(negative_feedback),
        }


@dataclass
class DriftDetector:
    db: Database
    minimum_recent_windows: int = 8
    absolute_threshold_w: float = 0.30
    relative_threshold: float = 0.08
    noise_multiplier: float = 2.0
    cooldown_seconds: float = 6 * 3600.0

    def detect(
        self,
        rollup: dict[str, Any],
        *,
        evidence_epoch_id: str,
    ) -> dict[str, Any] | None:
        if (
            rollup.get("trial_id")
            or rollup.get("demand_region") == "MIXED"
            or not bool(rollup.get("reference_eligible", True))
            or str(rollup.get("evidence_epoch_id") or "") != str(evidence_epoch_id)
        ):
            return None
        strata = reference_strata_key(rollup)
        reference = self.db.reference_baseline(evidence_epoch_id, strata)
        recent = self.db.noise_distribution(
            evidence_epoch_id,
            strata,
            window_seconds=7 * 86400.0,
        )
        if not reference or not recent:
            return None
        if int(recent.get("sample_count") or 0) < self.minimum_recent_windows:
            return None

        reference_median = reference.get("median_power_w")
        recent_median = recent.get("median_power_w")
        if not isinstance(reference_median, (int, float)) or not isinstance(
            recent_median, (int, float)
        ):
            return None

        noise_floor = float(recent.get("noise_floor_w") or 0.0)
        threshold = max(
            self.absolute_threshold_w,
            abs(float(reference_median)) * self.relative_threshold,
            noise_floor * self.noise_multiplier,
        )
        delta = float(recent_median) - float(reference_median)
        if delta <= threshold:
            return None

        key = f"drift_last:{evidence_epoch_id}:{strata}"
        last = float(self.db.get_meta(key, 0.0) or 0.0)
        now = float(rollup.get("bucket_ts") or time.time())
        if now - last < self.cooldown_seconds:
            return None
        self.db.set_meta(key, now)
        return {
            "start_ts": now,
            "severity": "medium" if delta < threshold + 0.7 else "high",
            "status": "OPEN",
            "classification": "SUSTAINED_DRIFT",
            "reason": "recent power distribution drifted above frozen reference",
            "evidence_epoch_id": evidence_epoch_id,
            "strata_key": strata,
            "reference_median_w": float(reference_median),
            "recent_median_w": float(recent_median),
            "delta_w": delta,
            "threshold_w": threshold,
            "noise_floor_w": noise_floor,
            "recent_sample_count": int(recent.get("sample_count") or 0),
        }


def summarize_minimal_meter_samples(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: float = 90.0,
) -> dict[str, Any]:
    summary = measurement_energy_summary(
        rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=1.0,
        max_consistency_abs_wh=999.0,
    )
    powers = [
        float(row["battery_power_w"])
        for row in rows
        if row.get("battery_status") == "Discharging"
        and isinstance(row.get("battery_power_w"), (int, float))
    ]
    summary.update(
        {
            "sample_count": len(rows),
            "median_power_w": statistics.median(powers) if powers else None,
            "mean_power_w": statistics.fmean(powers) if powers else None,
        }
    )
    return summary


def compare_meter_runs(
    reference_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    usable_battery_wh: float | None = None,
    max_gap_seconds: float = 90.0,
) -> dict[str, Any]:
    reference = summarize_minimal_meter_samples(
        reference_rows,
        max_gap_seconds=max_gap_seconds,
    )
    candidate = summarize_minimal_meter_samples(
        candidate_rows,
        max_gap_seconds=max_gap_seconds,
    )
    ref_power = reference.get("mean_power_w")
    cand_power = candidate.get("mean_power_w")
    delta_w = None
    delta_pct = None
    minutes = None
    if isinstance(ref_power, (int, float)) and isinstance(cand_power, (int, float)):
        delta_w = float(cand_power) - float(ref_power)
        delta_pct = delta_w / float(ref_power) * 100.0 if ref_power else None
        if isinstance(usable_battery_wh, (int, float)):
            minutes = minutes_gained_per_charge(
                usable_battery_wh=float(usable_battery_wh),
                baseline_power_w=float(ref_power),
                candidate_power_w=float(cand_power),
            )
    return {
        "reference": reference,
        "candidate": candidate,
        "candidate_minus_reference_w": delta_w,
        "candidate_minus_reference_percent": delta_pct,
        "minutes_gained_per_charge": minutes,
    }
