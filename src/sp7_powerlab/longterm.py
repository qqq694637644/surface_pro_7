from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .evidence import reference_strata_key
from .measurement import (
    contiguous_discharge_segments,
    measurement_energy_summary,
    measurement_trust_matches_epoch,
)
from .storage import Database

NET_BENEFIT_MODES = (
    "MONITORING_OVERHEAD",
    "DYNAMIC_CONTROLLER",
    "FULL_POWERLAB",
)


def runtime_policy_snapshot(config: Config, db: Database) -> dict[str, Any]:
    excluded_config_sections = {"storage", "helper", "minimal_meter", "net_benefit", "stable"}
    policy_config = {
        key: value for key, value in config.data.items() if key not in excluded_config_sections
    }
    verified_envelopes = [
        {
            "name": str(item.get("name") or ""),
            "revision": int(item.get("revision") or 0),
            "content_hash": str(item.get("content_hash") or ""),
        }
        for item in db.envelopes()
        if item.get("status") == "VERIFIED"
    ]
    verified_envelopes.sort(key=lambda item: item["name"])
    payload = {
        "config": policy_config,
        "verified_envelopes": verified_envelopes,
        "manual_override": db.get_meta("manual_override"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24],
        "payload": payload,
    }


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
        usage_timestamps: list[float] = []
        usage_days: set[int] = set()

        for row in rows:
            if evidence_epoch_id and str(row.get("evidence_epoch_id") or "") != str(
                evidence_epoch_id
            ):
                continue
            seconds = float(row.get("valid_seconds") or 0.0)
            if seconds <= 0 or row.get("trial_id"):
                continue
            bucket_ts = float(row.get("bucket_ts") or 0.0)
            if bucket_ts > 0:
                usage_timestamps.append(bucket_ts)
                usage_days.add(int(bucket_ts // 86400.0))
            total_seconds += seconds
            envelope_name = str(row.get("current_envelope") or "UNMANAGED")
            by_envelope[envelope_name] = by_envelope.get(envelope_name, 0.0) + seconds
            envelope = self.db.envelope(envelope_name)
            envelope_hash = str(row.get("current_envelope_content_hash") or "")
            verified = bool(
                envelope
                and envelope.get("status") == "VERIFIED"
                and envelope_hash
                and str(envelope.get("content_hash") or "") == envelope_hash
            )
            if verified:
                verified_seconds += seconds

            trusted = False
            if (
                verified
                and evidence_epoch_id
                and row.get("demand_region") != "MIXED"
                and bool(row.get("reference_eligible"))
            ):
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

        first_usage_ts = min(usage_timestamps) if usage_timestamps else None
        last_usage_ts = max(usage_timestamps) if usage_timestamps else None
        observation_span_seconds = (
            last_usage_ts - first_usage_ts
            if first_usage_ts is not None and last_usage_ts is not None
            else 0.0
        )

        return {
            "since_ts": since_ts,
            "total_valid_seconds": total_seconds,
            "verified_seconds": verified_seconds,
            "trusted_seconds": trusted_seconds,
            "verified_fraction": fraction(verified_seconds),
            "trusted_fraction": fraction(trusted_seconds),
            "first_usage_ts": first_usage_ts,
            "last_usage_ts": last_usage_ts,
            "observation_span_seconds": observation_span_seconds,
            "distinct_usage_days": len(usage_days),
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
    complete_campaign_ids: set[str] | None = None,
    max_campaign_span_seconds: float = 86400.0,
) -> dict[str, Any]:
    selected_campaign: str | None = None
    filtered_runs = [
        run
        for run in runs
        if run.get("end_ts") is not None
        and (run.get("result") or {}).get("comparison_quality") == "OK"
        and (run.get("result") or {}).get("comparison_design") == "A_B_B_A"
        and bool((run.get("result") or {}).get("fixed_baseline_envelope"))
        and bool((run.get("result") or {}).get("fixed_baseline_content_hash"))
        and bool((run.get("result") or {}).get("runtime_policy_fingerprint"))
        and (
            complete_campaign_ids is None
            or str((run.get("result") or {}).get("campaign_id") or "") in complete_campaign_ids
        )
        and (
            evidence_epoch_id is None
            or str(((run.get("result") or {}).get("evidence_epoch_id")) or "")
            == str(evidence_epoch_id)
        )
    ]
    campaigns: dict[str, list[dict[str, Any]]] = {}
    for run in filtered_runs:
        campaign = str(((run.get("result") or {}).get("campaign_id")) or "")
        if campaign:
            campaigns.setdefault(campaign, []).append(run)
    complete_campaigns: list[tuple[str, list[dict[str, Any]]]] = []
    for campaign, campaign_runs in campaigns.items():
        modes = {str(run.get("mode") or "") for run in campaign_runs}
        results = [run.get("result") or {} for run in campaign_runs]
        baseline_hashes = {
            str(result.get("fixed_baseline_content_hash") or "") for result in results
        }
        baseline_names = {str(result.get("fixed_baseline_envelope") or "") for result in results}
        timestamps = [
            float(run.get("end_ts") or run.get("start_ts") or 0.0) for run in campaign_runs
        ]
        campaign_span = max(timestamps) - min(timestamps) if timestamps else float("inf")
        if (
            set(NET_BENEFIT_MODES) <= modes
            and all(result.get("comparison_design") == "A_B_B_A" for result in results)
            and len(baseline_hashes) == 1
            and "" not in baseline_hashes
            and len(baseline_names) == 1
            and "" not in baseline_names
            and campaign_span <= float(max_campaign_span_seconds)
        ):
            complete_campaigns.append((campaign, campaign_runs))
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
            "fixed_baseline_content_hash": None,
            "full_policy_fingerprint": None,
            "selected_policy_mode": None,
            "selected_policy_fingerprint": None,
            "latest_runs": latest,
        }

    monitoring_delta = float(deltas["MONITORING_OVERHEAD"])
    dynamic_delta = float(deltas["DYNAMIC_CONTROLLER"])
    full_delta = float(deltas["FULL_POWERLAB"])
    reasons: list[str] = []
    fixed_baseline_content_hash = str(
        ((latest.get("MONITORING_OVERHEAD") or {}).get("result") or {}).get(
            "fixed_baseline_content_hash"
        )
        or ""
    )
    full_policy_fingerprint = str(
        ((latest.get("FULL_POWERLAB") or {}).get("result") or {}).get("runtime_policy_fingerprint")
        or ""
    )
    monitoring_policy_fingerprint = str(
        ((latest.get("MONITORING_OVERHEAD") or {}).get("result") or {}).get(
            "runtime_policy_fingerprint"
        )
        or ""
    )
    dynamic_policy_fingerprint = str(
        ((latest.get("DYNAMIC_CONTROLLER") or {}).get("result") or {}).get(
            "runtime_policy_fingerprint"
        )
        or ""
    )

    if full_delta <= -practical_threshold_w:
        recommendation = "KEEP_FULL_POWERLAB"
        selected_policy_mode = "FULL_POWERLAB"
        selected_policy_fingerprint = full_policy_fingerprint
        reasons.append("full PowerLab has practically meaningful net battery savings")
    elif dynamic_delta <= -practical_threshold_w:
        recommendation = "KEEP_DYNAMIC_REDUCE_MONITORING"
        selected_policy_mode = "DYNAMIC_CONTROLLER"
        selected_policy_fingerprint = dynamic_policy_fingerprint
        reasons.append(
            "dynamic control helps but full PowerLab does not clear the net-benefit threshold"
        )
        if monitoring_delta > 0:
            reasons.append("monitoring overhead consumes part of the controller savings")
    else:
        recommendation = "FIXED_GOOD_ENVELOPE"
        selected_policy_mode = "FIXED_GOOD"
        # The MONITORING comparison runs at Automation Level 0 and _meter_campaign
        # requires the same policy fingerprint for its A1/B1/B2/A2 blocks. Its
        # fingerprint therefore represents the validated fixed-good runtime policy.
        selected_policy_fingerprint = monitoring_policy_fingerprint
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
        "fixed_baseline_content_hash": fixed_baseline_content_hash or None,
        "full_policy_fingerprint": full_policy_fingerprint or None,
        "selected_policy_mode": selected_policy_mode,
        "selected_policy_fingerprint": selected_policy_fingerprint or None,
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
        minimum_total_valid_usage_seconds = float(
            self.config.get("stable.minimum_total_valid_usage_seconds", 28800.0)
        )
        minimum_total_trusted_usage_seconds = float(
            self.config.get("stable.minimum_total_trusted_usage_seconds", 25920.0)
        )
        minimum_distinct_usage_days = int(self.config.get("stable.minimum_distinct_usage_days", 5))
        minimum_observation_span_days = float(
            self.config.get("stable.minimum_observation_span_days", 7.0)
        )
        if float(coverage.get("total_valid_seconds") or 0.0) < minimum_total_valid_usage_seconds:
            reasons.append("total_valid_usage_below_minimum")
        if float(coverage.get("trusted_seconds") or 0.0) < minimum_total_trusted_usage_seconds:
            reasons.append("trusted_usage_below_minimum")
        if int(coverage.get("distinct_usage_days") or 0) < minimum_distinct_usage_days:
            reasons.append("distinct_usage_days_below_minimum")
        if float(coverage.get("observation_span_seconds") or 0.0) < (
            minimum_observation_span_days * 86400.0
        ):
            reasons.append("observation_span_below_minimum")

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
        complete_campaign_ids = {
            str(item["campaign_id"])
            for item in self.db.net_benefit_campaigns(status="COMPLETE", limit=100)
        }
        net_benefit = assess_net_benefit(
            overhead_runs,
            practical_threshold_w=float(self.config.get("evidence.practical_threshold_w", 0.10)),
            evidence_epoch_id=(epoch or {}).get("epoch_id"),
            complete_campaign_ids=complete_campaign_ids,
            max_campaign_span_seconds=float(
                self.config.get("net_benefit.max_campaign_span_seconds", 86400.0)
            ),
        )
        if not net_benefit["complete"]:
            reasons.append("net_benefit_validation_incomplete")
        current_policy = runtime_policy_snapshot(self.config, self.db)
        selected_policy_fingerprint = str(net_benefit.get("selected_policy_fingerprint") or "")
        if net_benefit["complete"] and selected_policy_fingerprint != str(
            current_policy["fingerprint"]
        ):
            reasons.append("net_benefit_selected_policy_is_stale")

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
            "minimum_total_valid_usage_seconds": minimum_total_valid_usage_seconds,
            "minimum_total_trusted_usage_seconds": minimum_total_trusted_usage_seconds,
            "minimum_distinct_usage_days": minimum_distinct_usage_days,
            "minimum_observation_span_days": minimum_observation_span_days,
            "usage_coverage": coverage,
            "frozen_reference_count": reference_count,
            "net_benefit": net_benefit,
            "current_runtime_policy": current_policy,
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
    max_consistency_ratio: float = 0.35,
    max_consistency_abs_wh: float = 0.05,
) -> dict[str, Any]:
    summary = measurement_energy_summary(
        rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    segment_summaries = [
        measurement_energy_summary(
            segment,
            max_gap_seconds=max_gap_seconds,
            max_consistency_ratio=max_consistency_ratio,
            max_consistency_abs_wh=max_consistency_abs_wh,
        )
        for segment in contiguous_discharge_segments(
            rows,
            max_gap_seconds=max_gap_seconds,
        )
    ]
    consistency_reasons: list[str] = []
    if (
        summary.get("integrated_energy_wh") is None
        or float(summary.get("energy_valid_seconds") or 0) <= 0
    ):
        consistency_reasons.append("no_valid_discharging_energy")
    if any(item.get("consistency_status") == "MISMATCH" for item in segment_summaries):
        consistency_reasons.append("battery_energy_consistency_mismatch")
    if any(
        item.get("consistency_status") == "ENERGY_DELTA_UNAVAILABLE" for item in segment_summaries
    ):
        consistency_reasons.append("battery_energy_delta_unavailable")
    consistent_segments = [
        item for item in segment_summaries if item.get("consistency_status") == "CONSISTENT"
    ]
    if not consistent_segments:
        consistency_reasons.append("no_consistent_energy_segment")

    powers = [
        float(row["battery_power_w"])
        for row in rows
        if row.get("battery_status") == "Discharging"
        and isinstance(row.get("battery_power_w"), (int, float))
        and not row.get("resume_grace")
    ]
    integrated = summary.get("integrated_energy_wh")
    valid_seconds = float(summary.get("energy_valid_seconds") or 0.0)
    mean_power = (
        float(integrated) * 3600.0 / valid_seconds
        if isinstance(integrated, (int, float)) and valid_seconds > 0
        else None
    )
    brightness_values = [
        float(row["brightness_pct"])
        for row in rows
        if isinstance(row.get("brightness_pct"), (int, float))
    ]
    temp_values = [
        float(row["package_temp_c"])
        for row in rows
        if isinstance(row.get("package_temp_c"), (int, float))
    ]
    network_values = [
        float(row.get("network_rx_mbps") or 0.0) + float(row.get("network_tx_mbps") or 0.0)
        for row in rows
        if isinstance(row.get("network_rx_mbps"), (int, float))
        and isinstance(row.get("network_tx_mbps"), (int, float))
    ]
    active_values = [
        bool(row["user_active"]) for row in rows if isinstance(row.get("user_active"), (bool, int))
    ]
    media_values = [
        bool(row["media_playing"])
        for row in rows
        if isinstance(row.get("media_playing"), (bool, int))
    ]
    remote_values = [
        bool(row["remote_present"])
        for row in rows
        if isinstance(row.get("remote_present"), (bool, int))
    ]
    fixed_hwp_values = [
        bool(row["hwp_matches_fixed_envelope"])
        for row in rows
        if isinstance(row.get("hwp_matches_fixed_envelope"), (bool, int))
    ]
    summary.update(
        {
            "sample_count": len(rows),
            "median_power_w": statistics.median(powers) if powers else None,
            "mean_power_w": mean_power,
            "data_quality": "OK" if not consistency_reasons else "DATA_QUALITY_FAILURE",
            "data_quality_reasons": consistency_reasons,
            "consistency_segment_count": len(segment_summaries),
            "consistent_segment_count": len(consistent_segments),
            "consistency_segments": segment_summaries,
            "mean_brightness_pct": (
                statistics.fmean(brightness_values) if brightness_values else None
            ),
            "active_fraction": (sum(active_values) / len(active_values) if active_values else None),
            "media_fraction": (sum(media_values) / len(media_values) if media_values else None),
            "remote_fraction": (sum(remote_values) / len(remote_values) if remote_values else None),
            "mean_network_mbps": (statistics.fmean(network_values) if network_values else None),
            "mean_package_temp_c": statistics.fmean(temp_values) if temp_values else None,
            "fixed_hwp_sample_count": len(fixed_hwp_values),
            "fixed_hwp_all_match": (all(fixed_hwp_values) if fixed_hwp_values else None),
        }
    )
    return summary


def _paired_covariate_reasons(
    reference_before: dict[str, Any],
    candidate: dict[str, Any],
    reference_after: dict[str, Any],
    *,
    max_brightness_delta_pct: float,
    max_active_fraction_delta: float,
    max_media_fraction_delta: float,
    max_remote_fraction_delta: float,
    max_network_mbps_delta: float,
    max_mean_temp_delta_c: float,
) -> list[str]:
    checks = (
        ("mean_brightness_pct", max_brightness_delta_pct, "brightness"),
        ("active_fraction", max_active_fraction_delta, "active_fraction"),
        ("media_fraction", max_media_fraction_delta, "media_fraction"),
        ("remote_fraction", max_remote_fraction_delta, "remote_fraction"),
        ("mean_network_mbps", max_network_mbps_delta, "network"),
        ("mean_package_temp_c", max_mean_temp_delta_c, "temperature"),
    )
    reasons: list[str] = []
    for field, threshold, label in checks:
        before = reference_before.get(field)
        current = candidate.get(field)
        after = reference_after.get(field)
        if not all(isinstance(value, (int, float)) for value in (before, current, after)):
            reasons.append(f"missing_{label}_covariate")
            continue
        reference_mid = statistics.fmean((float(before), float(after)))
        if abs(float(current) - reference_mid) > float(threshold):
            reasons.append(f"{label}_not_comparable")
    return reasons


def compare_paired_meter_runs(
    reference_before_rows: list[dict[str, Any]],
    candidate_first_rows: list[dict[str, Any]],
    candidate_second_rows: list[dict[str, Any]],
    reference_after_rows: list[dict[str, Any]],
    *,
    usable_battery_wh: float | None = None,
    max_gap_seconds: float = 90.0,
    max_consistency_ratio: float = 0.35,
    max_consistency_abs_wh: float = 0.05,
    minimum_block_seconds: float = 300.0,
    max_brightness_delta_pct: float = 10.0,
    max_active_fraction_delta: float = 0.15,
    max_media_fraction_delta: float = 0.10,
    max_remote_fraction_delta: float = 0.10,
    max_network_mbps_delta: float = 5.0,
    max_mean_temp_delta_c: float = 5.0,
    max_reference_drift_w: float = 0.30,
    max_candidate_delta_spread_w: float = 0.30,
    require_candidate_fixed_hwp: bool = False,
) -> dict[str, Any]:
    before = summarize_minimal_meter_samples(
        reference_before_rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    candidate_first = summarize_minimal_meter_samples(
        candidate_first_rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    candidate_second = summarize_minimal_meter_samples(
        candidate_second_rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    after = summarize_minimal_meter_samples(
        reference_after_rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )

    quality_reasons: list[str] = []
    for label, block in (
        ("reference_before", before),
        ("candidate_first", candidate_first),
        ("candidate_second", candidate_second),
        ("reference_after", after),
    ):
        if block.get("data_quality") != "OK":
            quality_reasons.append(f"{label}_data_quality_failed")
        if float(block.get("energy_valid_seconds") or 0.0) < float(minimum_block_seconds):
            quality_reasons.append(f"{label}_too_short")
    if before.get("fixed_hwp_all_match") is not True:
        quality_reasons.append("reference_before_hwp_not_fixed")
    if after.get("fixed_hwp_all_match") is not True:
        quality_reasons.append("reference_after_hwp_not_fixed")
    if require_candidate_fixed_hwp:
        if candidate_first.get("fixed_hwp_all_match") is not True:
            quality_reasons.append("candidate_first_hwp_not_fixed")
        if candidate_second.get("fixed_hwp_all_match") is not True:
            quality_reasons.append("candidate_second_hwp_not_fixed")

    for label, candidate_block in (
        ("candidate_first", candidate_first),
        ("candidate_second", candidate_second),
    ):
        quality_reasons.extend(
            f"{label}_{reason}"
            for reason in _paired_covariate_reasons(
                before,
                candidate_block,
                after,
                max_brightness_delta_pct=max_brightness_delta_pct,
                max_active_fraction_delta=max_active_fraction_delta,
                max_media_fraction_delta=max_media_fraction_delta,
                max_remote_fraction_delta=max_remote_fraction_delta,
                max_network_mbps_delta=max_network_mbps_delta,
                max_mean_temp_delta_c=max_mean_temp_delta_c,
            )
        )

    before_power = before.get("mean_power_w")
    candidate_first_power = candidate_first.get("mean_power_w")
    candidate_second_power = candidate_second.get("mean_power_w")
    after_power = after.get("mean_power_w")
    baseline_power = None
    candidate_block_deltas: list[float] = []
    candidate_delta_spread_w = None
    delta_w = None
    delta_pct = None
    minutes = None
    reference_drift_w = None
    if all(
        isinstance(value, (int, float))
        for value in (
            before_power,
            candidate_first_power,
            candidate_second_power,
            after_power,
        )
    ):
        reference_drift_w = float(after_power) - float(before_power)
        if abs(reference_drift_w) > float(max_reference_drift_w):
            quality_reasons.append("fixed_reference_drift_exceeds_limit")
        baseline_power = statistics.fmean((float(before_power), float(after_power)))
        candidate_block_deltas = [
            float(candidate_first_power) - float(before_power),
            float(candidate_second_power) - float(after_power),
        ]
        candidate_delta_spread_w = abs(candidate_block_deltas[0] - candidate_block_deltas[1])
        if candidate_delta_spread_w > float(max_candidate_delta_spread_w):
            quality_reasons.append("candidate_block_delta_spread_exceeds_limit")
        if candidate_block_deltas[0] * candidate_block_deltas[1] < 0:
            quality_reasons.append("candidate_block_effect_direction_inconsistent")
        if not quality_reasons:
            delta_w = statistics.median(candidate_block_deltas)
            delta_pct = delta_w / baseline_power * 100.0 if baseline_power else None
            if isinstance(usable_battery_wh, (int, float)):
                minutes = minutes_gained_per_charge(
                    usable_battery_wh=float(usable_battery_wh),
                    baseline_power_w=baseline_power,
                    candidate_power_w=baseline_power + delta_w,
                )
    else:
        quality_reasons.append("missing_paired_power")

    return {
        "comparison_design": "A_B_B_A",
        "reference_before": before,
        "candidate_first": candidate_first,
        "candidate_second": candidate_second,
        "reference_after": after,
        "paired_reference_power_w": baseline_power,
        "reference_drift_w": reference_drift_w,
        "candidate_block_deltas_w": candidate_block_deltas,
        "candidate_delta_spread_w": candidate_delta_spread_w,
        "candidate_minus_reference_w": delta_w,
        "candidate_minus_reference_percent": delta_pct,
        "minutes_gained_per_charge": minutes,
        "comparison_quality": "OK" if not quality_reasons else "DATA_QUALITY_FAILURE",
        "comparison_quality_reasons": sorted(set(quality_reasons)),
    }


def compare_meter_runs(
    reference_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    usable_battery_wh: float | None = None,
    max_gap_seconds: float = 90.0,
    max_consistency_ratio: float = 0.35,
    max_consistency_abs_wh: float = 0.05,
) -> dict[str, Any]:
    reference = summarize_minimal_meter_samples(
        reference_rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    candidate = summarize_minimal_meter_samples(
        candidate_rows,
        max_gap_seconds=max_gap_seconds,
        max_consistency_ratio=max_consistency_ratio,
        max_consistency_abs_wh=max_consistency_abs_wh,
    )
    quality_reasons: list[str] = []
    if reference.get("data_quality") != "OK":
        quality_reasons.append("reference_data_quality_failed")
    if candidate.get("data_quality") != "OK":
        quality_reasons.append("candidate_data_quality_failed")
    ref_power = reference.get("mean_power_w")
    cand_power = candidate.get("mean_power_w")
    delta_w = None
    delta_pct = None
    minutes = None
    if (
        not quality_reasons
        and isinstance(ref_power, (int, float))
        and isinstance(cand_power, (int, float))
    ):
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
        "comparison_quality": "OK" if not quality_reasons else "DATA_QUALITY_FAILURE",
        "comparison_quality_reasons": quality_reasons,
    }
