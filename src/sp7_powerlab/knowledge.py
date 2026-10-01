from __future__ import annotations

import time
from typing import Any

from .analytics import battery_usage_summary
from .config import Config, load_machine
from .envelopes import EnvelopeRegistry
from .lifecycle import LifecycleManager
from .longterm import UsageCoverage, assess_net_benefit
from .storage import Database


def build_review_pack(
    config: Config,
    db: Database,
    registry: EnvelopeRegistry,
) -> dict[str, Any]:
    now = time.time()
    history_hours = float(config.get("review.history_hours", 24))
    since = now - history_hours * 3600
    samples = db.recent_samples(since)
    incidents = db.recent_incidents(
        since,
        limit=int(config.get("review.max_incidents", 20)),
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
    lifecycle = LifecycleManager(db)
    evidence_epoch = db.active_evidence_epoch()
    evidence_epoch_id = (evidence_epoch or {}).get("epoch_id")
    rollups = db.recent_rollups(
        since,
        limit=500,
        evidence_epoch_id=evidence_epoch_id,
    )
    coverage_days = int(config.get("stable.coverage_days", 30))
    usage_coverage = UsageCoverage(db).summarize(
        since_ts=now - coverage_days * 86400.0,
        evidence_epoch_id=evidence_epoch_id,
    )
    net_benefit_results = db.net_benefit_results(50)
    complete_campaign_ids = {
        str(item["campaign_id"]) for item in db.net_benefit_campaigns(status="COMPLETE", limit=100)
    }
    net_benefit = assess_net_benefit(
        net_benefit_results,
        practical_threshold_w=float(config.get("evidence.practical_threshold_w", 0.10)),
        evidence_epoch_id=evidence_epoch_id,
        complete_campaign_ids=complete_campaign_ids,
        max_campaign_span_seconds=float(
            config.get("net_benefit.max_campaign_span_seconds", 86400.0)
        ),
    )
    noise_rows = (
        [
            dict(row)
            for row in db.conn.execute(
                """SELECT updated_ts,evidence_epoch_id,strata_key,window_seconds,
                median_power_w,mad_power_w,noise_floor_w,sample_count
                FROM recent_noise_distributions
                WHERE evidence_epoch_id=?
                ORDER BY updated_ts DESC LIMIT 50""",
                (evidence_epoch_id,),
            )
        ]
        if evidence_epoch_id
        else []
    )
    return {
        "generated_ts": now,
        "objective": (
            "Minimize whole-device battery discharge while preserving acceptable "
            "user experience, stability, and sustainable thermals."
        ),
        "rules": {
            "battery_power_is_primary_reward": True,
            "agent_not_in_realtime_control": True,
            "trial_success_requires_measurement": True,
            "negative_user_feedback_rejects_candidate": True,
            "thermal_safety_can_preempt_any_trial": True,
            "prefer_unexpected_power_investigation_before_performance_restriction": True,
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
        "lifecycle": {
            **lifecycle.status(),
            "usage_coverage_days": coverage_days,
            "usage_coverage": usage_coverage,
            "target_trusted_fraction": float(config.get("stable.target_trusted_fraction", 0.90)),
        },
        "evidence": {
            "active_epoch": evidence_epoch,
            "compatibility_tags": db.active_compatibility_tags(),
            "recent_decisions": db.evidence_decisions(
                evidence_epoch_id=evidence_epoch_id,
                limit=20,
            ),
            "recent_noise": noise_rows,
            "frozen_reference_count": (
                db.conn.execute(
                    """SELECT COUNT(*) FROM reference_baselines
                    WHERE frozen=1 AND evidence_epoch_id=?""",
                    (evidence_epoch_id,),
                ).fetchone()[0]
                if evidence_epoch_id
                else 0
            ),
        },
        "investigations": db.recent_investigations(20),
        "unexpected_power_events": db.recent_unexpected_power_events(20),
        "net_benefit_results": net_benefit_results[:20],
        "net_benefit": net_benefit,
        "trials": db.recent_trials(int(config.get("review.max_trials", 20))),
        "feedback": db.recent_feedback(50),
        "rejections": db.recent_rejections(50),
        "system_fingerprint": db.active_system_fingerprint(),
        "software_version_drift": db.get_meta("software_version_drift"),
        "calibration": machine.get("calibration"),
    }
