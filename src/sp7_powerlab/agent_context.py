from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, load_machine
from .envelopes import EnvelopeRegistry
from .hardware import inspect_hardware
from .lifecycle import LifecycleManager
from .longterm import StableReadiness, UsageCoverage, assess_net_benefit
from .measurement import measurement_trust_matches_epoch
from .scheduler import CandidateScheduler
from .storage import SCHEMA_VERSION, Database


def _git(args: list[str], root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_context(root: Path) -> dict[str, Any]:
    porcelain = _git(["status", "--porcelain"], root)
    return {
        "commit": _git(["rev-parse", "HEAD"], root),
        "branch": _git(["branch", "--show-current"], root),
        "dirty": bool(porcelain) if porcelain is not None else None,
    }


def _project_context(config: Config, identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "Surface Pro 7 PowerLab",
        "version": __version__,
        "runtime_schema": SCHEMA_VERSION,
        "evidence_semantics_version": int(config.get("evidence.semantics_version", 1)),
        "git": _git_context(config.root),
        "objective": (
            "maximize real battery runtime while preserving user experience, "
            "stability, and sustainable thermals"
        ),
        "hardware_target": {
            "product": str(identity.get("expected_product", "Surface Pro 7")),
            "cpu": str(identity.get("expected_cpu_substring", "i5-1035G4")),
            "workload_assumption": (
                "sustained heavy compute is not a target local workload; "
                "heavy jobs are normally offloaded to a remote server"
            ),
        },
    }


def _documentation_context() -> dict[str, str]:
    return {
        "ai_entry": "AGENTS.md",
        "human_entry": "README.md",
        "project_map": "docs/PROJECT_MAP.md",
        "implementation_status": "docs/PROJECT_STATUS.md",
        "design_contract": "PLAN2.md",
        "agent_behavior": "docs/LLM_BEHAVIOR.md",
        "first_run": "docs/FIRST_RUN.md",
        "operations": "docs/OPERATIONS.md",
        "deployment": "docs/DEPLOYMENT.md",
        "agent_loop": "docs/AI_LOOP.md",
    }


def build_agent_context_without_runtime(
    config: Config,
    *,
    error: str,
) -> dict[str, Any]:
    machine = load_machine(config.root)
    identity = machine.get("identity") or {}
    calibration = machine.get("calibration") or {}
    report = inspect_hardware(
        expected_product=str(identity.get("expected_product", "Surface Pro 7")),
        expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
        configured_thermal_sensor=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
    )
    return {
        "generated_ts": time.time(),
        "project": _project_context(config, identity),
        "documentation": _documentation_context(),
        "hardware": report.as_dict(),
        "runtime_database": {
            "available": False,
            "error": error,
        },
        "runtime": {
            "control_safety_state": "UNKNOWN",
            "learning_lifecycle": "UNKNOWN",
            "investigation_status": "UNKNOWN",
            "automation_level": int(config.get("automation.level", 0)),
            "current_envelope": None,
            "latest_sample": None,
        },
        "battery": {
            "active_epoch": None,
            "calibration": calibration,
            "measurement_trust": {},
        },
        "evidence": {
            "active_epoch": None,
            "compatibility_tags": {},
            "frozen_reference_count": 0,
            "recent_noise_distribution_count": 0,
            "recent_decisions": [],
        },
        "verified_envelopes": [],
        "trial": {"active": None},
        "investigation": {"active": None, "recent_events": []},
        "scheduler": {
            "eligible": False,
            "reasons": ["runtime_database_unavailable"],
            "details": {},
            "candidate_count": 0,
            "stop_reason": "runtime_database_unavailable",
        },
        "usage_coverage": {
            "days": int(config.get("stable.coverage_days", 30)),
            "available": False,
        },
        "net_benefit": {
            "complete": False,
            "recommendation": "NEED_MORE_DATA",
            "reasons": ["runtime database is unavailable"],
        },
        "stable_readiness": {
            "ready": False,
            "reasons": ["runtime_database_unavailable"],
        },
        "current_stage": {
            "name": "RUNTIME_DATABASE",
            "status": "BLOCKED",
            "reason": error,
        },
        "recommended_next_actions": [
            {
                "action": "inspect_or_reset_runtime_database",
                "reason": (
                    "back up any needed legacy runtime data; only reset the runtime "
                    "database when the user has authorized the destructive reset"
                ),
            }
        ],
        "truth_note": (
            "The runtime database could not be read, so machine learning/evidence state "
            "is intentionally reported as unknown. Hardware and repository facts remain "
            "useful for diagnosis; do not infer live evidence state from Markdown."
        ),
    }


def _count(db: Database, sql: str, args: tuple[Any, ...] = ()) -> int:
    return int(db.conn.execute(sql, args).fetchone()[0])


def _compact_evidence_epoch(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        key: value.get(key)
        for key in (
            "epoch_id",
            "start_ts",
            "battery_epoch",
            "calibration_version",
            "evidence_semantics_version",
        )
    }


def _compact_decision(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "decision_id",
            "trial_id",
            "candidate_key",
            "verdict",
            "minimum_useful_effect_w",
            "median_effect_w",
            "direction_consistency",
            "evidence_count",
            "created_ts",
        )
    }


def _compact_trial(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    result = value.get("result") or {}
    return {
        "trial_id": value.get("trial_id"),
        "state": value.get("state"),
        "kind": value.get("kind"),
        "baseline_envelope": value.get("baseline_envelope"),
        "current_arm": value.get("current_arm"),
        "created_ts": value.get("created_ts"),
        "verdict": result.get("verdict") if isinstance(result, dict) else None,
    }


def _compact_investigation(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        key: value.get(key)
        for key in (
            "investigation_id",
            "start_ts",
            "status",
            "event_id",
            "classification",
        )
    }


def _compact_event(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "event_id",
            "start_ts",
            "severity",
            "status",
            "classification",
            "reason",
        )
    }


def _compact_net_benefit(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "complete",
            "missing_modes",
            "practical_threshold_w",
            "deltas_w",
            "dynamic_net_saving_w",
            "campaign_id",
            "fixed_baseline_envelope",
            "fixed_baseline_content_hash",
            "selected_policy_mode",
            "selected_policy_fingerprint",
            "recommendation",
            "reasons",
        )
    }


def _compact_stable_readiness(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "ready",
            "reasons",
            "coverage_days",
            "target_trusted_fraction",
            "minimum_total_valid_usage_seconds",
            "minimum_total_trusted_usage_seconds",
            "minimum_distinct_usage_days",
            "minimum_observation_span_days",
            "coverage_reasons",
            "using_stable_entry_coverage",
            "frozen_reference_count",
            "open_unexpected_power_events",
            "recent_negative_feedback_count",
            "current_runtime_mode",
            "fixed_runtime_audit",
        )
    }


def _compact_scheduler_details(value: dict[str, Any]) -> dict[str, Any]:
    minimum = value.get("minimum_useful_effect") or {}
    arm = value.get("recommended_arm") or {}
    trust = value.get("measurement_trust") or {}
    epoch = value.get("evidence_epoch")
    return {
        "automation_level": value.get("automation_level"),
        "learning_lifecycle": value.get("learning_lifecycle"),
        "control_safety_state": value.get("control_safety_state"),
        "battery_pct": value.get("battery_pct"),
        "weekly_trial_count": value.get("weekly_trial_count"),
        "weekly_trial_budget": value.get("weekly_trial_budget"),
        "candidate_minutes_last_24h": value.get("candidate_minutes_last_24h"),
        "candidate_minutes_budget": value.get("candidate_minutes_budget"),
        "measurement_trust_status": (trust.get("status") if isinstance(trust, dict) else None),
        "evidence_epoch_id": (epoch.get("epoch_id") if isinstance(epoch, dict) else None),
        "strata_key": value.get("strata_key"),
        "minimum_useful_effect_w": (
            minimum.get("minimum_useful_effect_w") if isinstance(minimum, dict) else None
        ),
        "noise_floor_w": (minimum.get("noise_floor_w") if isinstance(minimum, dict) else None),
        "recommended_min_arm_seconds": (
            arm.get("recommended_min_arm_seconds") if isinstance(arm, dict) else None
        ),
        "avg_rapl_w": value.get("avg_rapl_w"),
    }


def _stage_and_actions(
    *,
    hardware: dict[str, Any],
    calibration_valid: bool,
    measurement_trust: dict[str, Any],
    lifecycle: dict[str, Any],
    active_battery_epoch: int | None,
    evidence_epoch: dict[str, Any] | None,
    frozen_reference_count: int,
    active_investigation: dict[str, Any] | None,
    stable_readiness: dict[str, Any],
    scheduler: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    actions: list[dict[str, str]] = []

    if not hardware.get("supported_machine"):
        stage = {
            "name": "HARDWARE_CONTRACT",
            "status": "BLOCKED",
            "reason": "current machine does not satisfy the Surface Pro 7 hardware contract",
        }
        actions.append(
            {
                "action": "inspect_hardware_contract",
                "reason": "PowerLab control must not proceed on an unsupported machine",
            }
        )
        return stage, actions

    if active_battery_epoch is None:
        stage = {
            "name": "STAGE_A_MEASUREMENT_TRUST",
            "status": "BLOCKED",
            "reason": "no active battery epoch has been established",
        }
        actions.append(
            {
                "action": "collect_real_battery_telemetry",
                "reason": "battery epoch and discharge telemetry are prerequisites for calibration",
            }
        )
        return stage, actions

    if not measurement_trust_matches_epoch(measurement_trust, evidence_epoch):
        stage = {
            "name": "STAGE_A_MEASUREMENT_TRUST",
            "status": "BLOCKED",
            "reason": "battery gauge and BAT measurement trust are not ready",
        }
        actions.append(
            {
                "action": "run_measurement_trust_assessment",
                "reason": "resolve gauge cadence, quantization, consistency, and minimum arm duration",
            }
        )
        return stage, actions

    if not calibration_valid:
        stage = {
            "name": "CALIBRATION",
            "status": "BLOCKED",
            "reason": "real-machine calibration is not valid for the active battery epoch",
        }
        actions.append(
            {
                "action": "complete_calibration",
                "reason": "calibration should use a battery measurement path that has already passed Measurement Trust",
            }
        )
        return stage, actions

    if active_investigation:
        stage = {
            "name": "INVESTIGATION",
            "status": "ACTIVE",
            "reason": "an UnexpectedPower or drift investigation is active",
        }
        actions.append(
            {
                "action": "finish_active_investigation",
                "reason": "investigation should resolve before parameter exploration resumes",
            }
        )
        return stage, actions

    learning = str(lifecycle.get("learning_lifecycle") or "")
    if learning == "STABLE" and not stable_readiness.get("ready"):
        stable_reasons = set(stable_readiness.get("reasons") or [])
        selected_policy_reasons = {
            "net_benefit_selected_policy_is_stale",
            "net_benefit_selected_runtime_mode_mismatch",
            "fixed_runtime_audit_failed",
        }
        if stable_reasons and stable_reasons <= selected_policy_reasons:
            return (
                {
                    "name": "STABLE_STALE",
                    "status": "BLOCKED",
                    "reason": "the selected Stage E policy no longer matches the live runtime",
                },
                [
                    {
                        "action": (
                            "repair_fixed_runtime_state"
                            if "fixed_runtime_audit_failed" in stable_reasons
                            else "reconcile_selected_net_benefit_policy"
                        ),
                        "reason": ", ".join(sorted(stable_reasons)),
                    }
                ],
            )
        return (
            {
                "name": "STABLE_STALE",
                "status": "BLOCKED",
                "reason": (
                    "learning lifecycle still says STABLE but current-epoch "
                    "readiness requirements are no longer satisfied"
                ),
            },
            [
                {
                    "action": "rebuild_current_epoch_evidence",
                    "reason": ", ".join(
                        stable_readiness.get("reasons")
                        or ["current-epoch STABLE readiness is not satisfied"]
                    ),
                }
            ],
        )

    if evidence_epoch is None or frozen_reference_count <= 0:
        stage = {
            "name": "STAGE_B_BASELINE_AND_NOISE",
            "status": "ACTIVE",
            "reason": "trusted frozen reference/noise evidence is not yet established",
        }
        actions.append(
            {
                "action": "collect_natural_baseline_windows",
                "reason": "Evidence Engine and Scheduler need empirical reference and recent noise distributions",
            }
        )
        return stage, actions

    if learning == "BASELINE_OBSERVATION":
        return (
            {
                "name": "STAGE_C_READY",
                "status": "READY",
                "reason": "current-epoch baseline/reference evidence exists; bounded coarse search may begin",
            },
            [
                {
                    "action": "begin_coarse_search",
                    "reason": "explicitly transition learning lifecycle with lifecycle optimize before candidate search",
                }
            ],
        )

    if learning in {"COARSE_OPTIMIZATION", "REOPENED"}:
        stage = {
            "name": "STAGE_C_COARSE_SEARCH",
            "status": "ACTIVE",
            "reason": "learning lifecycle currently permits bounded candidate exploration",
        }
        if scheduler.get("eligible"):
            actions.append(
                {
                    "action": "evaluate_scheduler_candidate",
                    "reason": "safety, evidence, noise, and experiment-budget gates currently permit exploration",
                }
            )
        else:
            actions.append(
                {
                    "action": "resolve_scheduler_blockers",
                    "reason": ", ".join(scheduler.get("reasons") or ["scheduler not eligible"]),
                }
            )
        return stage, actions

    if learning == "STABLE":
        return (
            {
                "name": "STABLE",
                "status": "ACTIVE",
                "reason": "PowerLab is converged and should remain quiet unless evidence reopens learning",
            },
            [
                {
                    "action": "monitor_only",
                    "reason": "normal mature behavior is NO_CHANGE plus drift/UnexpectedPower monitoring",
                }
            ],
        )

    if stable_readiness.get("ready"):
        return (
            {
                "name": "STABLE_READY",
                "status": "READY",
                "reason": "current evidence satisfies the deterministic STABLE readiness gate",
            },
            [
                {
                    "action": "consider_freezing_stable",
                    "reason": "usage coverage, measurement trust, unresolved events, and net-benefit checks are satisfied",
                }
            ],
        )

    readiness_reasons = set(stable_readiness.get("reasons") or [])
    net_benefit_reasons = {
        "net_benefit_validation_incomplete",
        "net_benefit_selected_policy_is_stale",
        "net_benefit_selected_runtime_mode_mismatch",
        "fixed_runtime_audit_failed",
    }
    if readiness_reasons and readiness_reasons <= net_benefit_reasons:
        stale_reasons = {
            "net_benefit_selected_policy_is_stale",
            "net_benefit_selected_runtime_mode_mismatch",
            "fixed_runtime_audit_failed",
        }
        if readiness_reasons <= stale_reasons:
            action = (
                "repair_fixed_runtime_state"
                if "fixed_runtime_audit_failed" in readiness_reasons
                else "reconcile_selected_net_benefit_policy"
            )
            net_benefit = stable_readiness.get("net_benefit") or {}
            current_mode = stable_readiness.get("current_runtime_mode") or {}
            reason = (
                f"selected={net_benefit.get('selected_policy_mode')} "
                f"selected_fp={net_benefit.get('selected_policy_fingerprint')} "
                f"current={current_mode.get('mode')}; " + ", ".join(sorted(readiness_reasons))
            )
        else:
            action = "complete_net_benefit_validation"
            reason = ", ".join(sorted(readiness_reasons))
        return (
            {
                "name": "STAGE_E_NET_BENEFIT",
                "status": "ACTIVE",
                "reason": (
                    "measurement/reference/usage validation is sufficient; end-to-end "
                    "PowerLab net-benefit evidence is the remaining convergence gate"
                ),
            },
            [
                {
                    "action": action,
                    "reason": reason,
                }
            ],
        )

    if learning == "VALIDATING":
        return (
            {
                "name": "STAGE_D_VALIDATION_BURN_IN",
                "status": "ACTIVE",
                "reason": (
                    "candidate/controller behavior and representative real usage are being "
                    "validated before end-to-end Net Benefit and convergence"
                ),
            },
            [
                {
                    "action": "continue_validation_burn_in",
                    "reason": ", ".join(
                        stable_readiness.get("reasons")
                        or ["collect independent evidence and representative real usage"]
                    ),
                }
            ],
        )

    return (
        {
            "name": "STAGE_D_VALIDATION_BURN_IN",
            "status": "ACTIVE",
            "reason": (
                "current-epoch reference exists, but representative real-usage validation "
                "or another non-Net-Benefit STABLE prerequisite is still incomplete"
            ),
        },
        [
            {
                "action": "continue_validation_burn_in",
                "reason": ", ".join(stable_readiness.get("reasons") or ["more evidence required"]),
            }
        ],
    )


def build_agent_context(
    config: Config,
    db: Database,
    registry: EnvelopeRegistry,
) -> dict[str, Any]:
    now = time.time()
    machine = load_machine(config.root)
    identity = machine.get("identity") or {}
    calibration = machine.get("calibration") or {}
    report = inspect_hardware(
        expected_product=str(identity.get("expected_product", "Surface Pro 7")),
        expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
        configured_thermal_sensor=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
    )
    lifecycle_manager = LifecycleManager(db)
    lifecycle = lifecycle_manager.status()
    evidence_epoch = db.active_evidence_epoch()
    measurement_trust = db.get_meta("measurement_trust", {})
    if not isinstance(measurement_trust, dict):
        measurement_trust = {}
    if not measurement_trust_matches_epoch(measurement_trust, evidence_epoch):
        measurement_trust = {
            **measurement_trust,
            "status": "BLOCKED",
            "valid_for_active_evidence_epoch": False,
            "reasons": list(
                dict.fromkeys(
                    [
                        *(measurement_trust.get("reasons") or []),
                        "measurement_trust_not_bound_to_active_evidence_epoch",
                    ]
                )
            ),
        }
    else:
        measurement_trust = {
            **measurement_trust,
            "valid_for_active_evidence_epoch": True,
        }

    coverage_days = int(config.get("stable.coverage_days", 30))
    usage_coverage = UsageCoverage(db).summarize(
        since_ts=now - coverage_days * 86400.0,
        evidence_epoch_id=(evidence_epoch or {}).get("epoch_id"),
    )
    stable_readiness = StableReadiness(config, db).assess(now=now)
    net_benefit_results = db.net_benefit_results(50)
    complete_campaign_ids = {
        str(item["campaign_id"]) for item in db.net_benefit_campaigns(status="COMPLETE", limit=100)
    }
    net_benefit = assess_net_benefit(
        net_benefit_results,
        practical_threshold_w=float(config.get("evidence.practical_threshold_w", 0.10)),
        evidence_epoch_id=(evidence_epoch or {}).get("epoch_id"),
        complete_campaign_ids=complete_campaign_ids,
        max_campaign_span_seconds=float(
            config.get("net_benefit.max_campaign_span_seconds", 86400.0)
        ),
    )

    latest = db.latest_sample()
    current_envelope = (
        (latest or {}).get("current_envelope") or db.get_meta("current_envelope") or None
    )
    scheduler = CandidateScheduler(config, db, registry)
    scheduler_context: dict[str, Any]
    if current_envelope:
        scheduler_context = scheduler.eligibility(
            baseline_name=str(current_envelope),
        )
        scheduler_context = {
            "eligible": bool(scheduler_context.get("eligible")),
            "reasons": scheduler_context.get("reasons") or [],
            "details": _compact_scheduler_details(scheduler_context.get("details") or {}),
            "potential_neighbor_count": int(scheduler_context.get("potential_neighbor_count") or 0),
        }
    else:
        scheduler_context = {
            "eligible": False,
            "reasons": ["current_envelope_unknown"],
            "details": {},
            "potential_neighbor_count": 0,
        }

    active_epoch_id = (evidence_epoch or {}).get("epoch_id")
    frozen_reference_count = (
        _count(
            db,
            "SELECT COUNT(*) FROM reference_baselines WHERE frozen=1 AND evidence_epoch_id=?",
            (active_epoch_id,),
        )
        if active_epoch_id
        else 0
    )
    recent_noise_count = (
        _count(
            db,
            "SELECT COUNT(*) FROM recent_noise_distributions WHERE evidence_epoch_id=?",
            (active_epoch_id,),
        )
        if active_epoch_id
        else 0
    )
    active_investigation = db.active_investigation()

    stage, actions = _stage_and_actions(
        hardware=report.as_dict(),
        calibration_valid=bool(calibration.get("valid", False)),
        measurement_trust=measurement_trust,
        lifecycle=lifecycle,
        active_battery_epoch=db.active_battery_epoch(),
        evidence_epoch=evidence_epoch,
        frozen_reference_count=frozen_reference_count,
        active_investigation=active_investigation,
        stable_readiness=stable_readiness,
        scheduler=scheduler_context,
    )

    verified = [envelope for envelope in db.envelopes() if envelope.get("status") == "VERIFIED"]
    latest_summary = None
    if latest:
        latest_summary = {
            key: latest.get(key)
            for key in (
                "ts",
                "battery_status",
                "battery_pct",
                "battery_power_w",
                "battery_energy_wh",
                "battery_epoch",
                "brightness_pct",
                "package_temp_c",
                "thermal_state",
                "thermal_pressure",
                "demand_region",
                "local_compute_pressure",
                "remote_hint",
                "media_playing",
                "current_envelope",
                "current_envelope_content_hash",
                "trial_id",
                "resume_grace",
            )
        }

    return {
        "generated_ts": now,
        "project": _project_context(config, identity),
        "documentation": _documentation_context(),
        "runtime_database": {
            "available": True,
            "schema_version": SCHEMA_VERSION,
        },
        "hardware": report.as_dict(),
        "runtime": {
            "control_safety_state": lifecycle.get("control_safety_state"),
            "learning_lifecycle": lifecycle.get("learning_lifecycle"),
            "investigation_status": lifecycle.get("investigation_status"),
            "automation_level": int(config.get("automation.level", 0)),
            "current_envelope": current_envelope,
            "latest_sample": latest_summary,
        },
        "battery": {
            "active_epoch": db.active_battery_epoch(),
            "calibration": calibration,
            "measurement_trust": measurement_trust,
        },
        "evidence": {
            "active_epoch": _compact_evidence_epoch(evidence_epoch),
            "compatibility_tags": db.active_compatibility_tags(),
            "frozen_reference_count": frozen_reference_count,
            "recent_noise_distribution_count": recent_noise_count,
            "recent_decisions": [
                _compact_decision(value)
                for value in db.evidence_decisions(
                    evidence_epoch_id=active_epoch_id,
                    limit=5,
                )
            ],
        },
        "verified_envelopes": [
            {
                key: envelope.get(key)
                for key in (
                    "name",
                    "status",
                    "revision",
                    "content_hash",
                    "epp",
                    "max_perf_pct",
                    "turbo",
                )
            }
            for envelope in verified
        ],
        "trial": {
            "active": _compact_trial(db.active_trial()),
        },
        "investigation": {
            "active": _compact_investigation(active_investigation),
            "recent_events": [
                _compact_event(value) for value in db.recent_unexpected_power_events(3)
            ],
        },
        "scheduler": scheduler_context,
        "usage_coverage": {
            "days": coverage_days,
            **usage_coverage,
        },
        "net_benefit": _compact_net_benefit(net_benefit),
        "stable_readiness": _compact_stable_readiness(stable_readiness),
        "current_stage": stage,
        "recommended_next_actions": actions,
        "truth_note": (
            "Runtime state in this output and the current OS/SQLite are authoritative "
            "for this machine. PLAN2 defines intended design; PROJECT_STATUS describes "
            "implementation maturity and must not be used as live machine state."
        ),
    }
