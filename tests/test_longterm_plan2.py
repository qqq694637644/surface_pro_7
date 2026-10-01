from __future__ import annotations

import time
from pathlib import Path

from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.evidence import reference_strata_key
from sp7_powerlab.lifecycle import LifecycleManager
from sp7_powerlab.longterm import (
    DriftDetector,
    StableReadiness,
    UsageCoverage,
    assess_net_benefit,
    compare_meter_runs,
    compare_paired_meter_runs,
    minutes_gained_per_charge,
    negative_feedback_blocks_current_policy,
    runtime_mode_status,
    runtime_policy_snapshot,
    stage_e_code_identity,
    summarize_minimal_meter_samples,
)
from sp7_powerlab.runtime_audit import fixed_runtime_identity
from sp7_powerlab.storage import Database


def rollup(ts: float, power: float) -> dict:
    return {
        "bucket_ts": ts,
        "battery_epoch": 1,
        "brightness_bucket": 40,
        "demand_region": "ACTIVE|LAT_MEDIUM|CPU_LOW|NO_MEDIA|NET_LOW|LOCAL",
        "media_playing": False,
        "user_active": True,
        "remote_bucket": 0,
        "thermal_start": "COOL",
        "valid_seconds": 60,
        "avg_power_w": power,
        "avg_rapl_w": 1.2,
        "local_compute_pressure": "LOW",
        "current_envelope": "INTERACTIVE_EFFICIENT",
        "trial_id": None,
    }


def bind_current_envelope(db: Database, item: dict) -> dict:
    envelope = db.envelope(str(item["current_envelope"]))
    assert envelope is not None
    item["current_envelope_content_hash"] = str(envelope["content_hash"])
    return item


def meter_row(ts: float, power: float, energy: float) -> dict:
    return {
        "ts": ts,
        "battery_status": "Discharging",
        "battery_power_w": power,
        "battery_energy_wh": energy,
    }


def paired_meter_rows(
    power: float,
    *,
    brightness: float = 50.0,
    active: bool = True,
    media: bool = False,
    remote: bool = False,
    network_mbps: float = 1.0,
    temp_c: float = 42.0,
) -> list[dict]:
    return [
        {
            **meter_row(ts, power, 40.0 - power * ts / 3600.0),
            "brightness_pct": brightness,
            "user_active": active,
            "media_playing": media,
            "remote_present": remote,
            "network_rx_mbps": network_mbps / 2.0,
            "network_tx_mbps": network_mbps / 2.0,
            "package_temp_c": temp_c,
            "hwp_matches_fixed_envelope": True,
        }
        for ts in range(0, 601, 60)
    ]


def test_usage_coverage_requires_verified_policy_and_frozen_reference(project_root: Path):
    db = Database(project_root / "runtime/coverage.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        now = time.time()
        item = bind_current_envelope(db, rollup(now - 60, 5.0))
        item["evidence_epoch_id"] = epoch
        item["reference_eligible"] = True
        db.add_rollup(item)

        before = UsageCoverage(db).summarize(
            since_ts=now - 3600,
            evidence_epoch_id=epoch,
        )
        assert before["verified_fraction"] == 1.0
        assert before["trusted_fraction"] == 0.0

        strata = reference_strata_key(item)
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-1",
                "created_ts": now,
                "evidence_epoch_id": epoch,
                "strata_key": strata,
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 5.0,
                "mad_power_w": 0.05,
                "p25_power_w": 4.95,
                "p75_power_w": 5.05,
                "sample_count": 10,
                "frozen": True,
            }
        )
        after = UsageCoverage(db).summarize(
            since_ts=now - 3600,
            evidence_epoch_id=epoch,
        )
        assert after["trusted_fraction"] == 1.0
    finally:
        db.close()


def test_drift_detector_compares_recent_distribution_to_frozen_reference(tmp_path: Path):
    db = Database(tmp_path / "drift.sqlite3")
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        item = rollup(time.time(), 5.8)
        item["evidence_epoch_id"] = epoch
        item["reference_eligible"] = True
        strata = reference_strata_key(item)
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-1",
                "created_ts": time.time() - 86400,
                "evidence_epoch_id": epoch,
                "strata_key": strata,
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 5.0,
                "mad_power_w": 0.05,
                "p25_power_w": 4.95,
                "p75_power_w": 5.05,
                "sample_count": 20,
                "frozen": True,
            }
        )
        db.upsert_noise_distribution(
            {
                "evidence_epoch_id": epoch,
                "strata_key": strata,
                "window_seconds": 7 * 86400,
                "median_power_w": 5.7,
                "mad_power_w": 0.08,
                "p25_power_w": 5.6,
                "p75_power_w": 5.8,
                "p10_power_w": 5.5,
                "p90_power_w": 5.9,
                "noise_floor_w": 0.16,
                "sample_count": 20,
            }
        )
        event = DriftDetector(
            db,
            minimum_recent_windows=8,
            absolute_threshold_w=0.3,
            relative_threshold=0.05,
            noise_multiplier=1.5,
            cooldown_seconds=3600,
        ).detect(item, evidence_epoch_id=epoch)
        assert event is not None
        assert event["classification"] == "SUSTAINED_DRIFT"
        assert event["delta_w"] > 0.6
    finally:
        db.close()


def test_meter_comparison_reports_end_to_end_delta_and_minutes():
    reference = [
        meter_row(0, 5.0, 40.0),
        meter_row(60, 5.0, 40.0 - 5.0 / 60.0),
        meter_row(120, 5.0, 40.0 - 10.0 / 60.0),
    ]
    candidate = [
        meter_row(0, 4.8, 40.0),
        meter_row(60, 4.8, 40.0 - 4.8 / 60.0),
        meter_row(120, 4.8, 40.0 - 9.6 / 60.0),
    ]
    result = compare_meter_runs(
        reference,
        candidate,
        usable_battery_wh=40.0,
        max_gap_seconds=90.0,
    )
    assert abs(result["candidate_minus_reference_w"] + 0.2) < 1e-9
    assert result["minutes_gained_per_charge"] > 15.0
    assert (
        minutes_gained_per_charge(
            usable_battery_wh=40.0,
            baseline_power_w=5.0,
            candidate_power_w=4.8,
        )
        == result["minutes_gained_per_charge"]
    )


def test_stable_readiness_requires_measurement_coverage_and_system_value_evidence(
    project_root: Path,
    monkeypatch,
):
    from sp7_powerlab.config import load_config

    config = load_config(project_root)
    fixed_audit = {"ready": True, "reasons": []}
    monkeypatch.setattr(
        "sp7_powerlab.longterm.audit_fixed_runtime",
        lambda *_args, **_kwargs: dict(fixed_audit),
    )
    db = Database(project_root / "runtime/readiness.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        blocked = StableReadiness(config, db).assess()
        assert blocked["ready"] is False
        assert "measurement_trust_not_ready" in blocked["reasons"]

        db.set_meta(
            "measurement_trust",
            {
                "status": "READY",
                "evidence_epoch_id": epoch,
                "battery_epoch": 1,
                "calibration_version": 1,
                "evidence_semantics_version": 1,
            },
        )
        now = time.time()
        item = bind_current_envelope(db, rollup(now - 60, 5.0))
        item["evidence_epoch_id"] = epoch
        item["reference_eligible"] = True
        db.add_rollup(item)
        fixed_hash = str(item["current_envelope_content_hash"])
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-ready",
                "created_ts": now,
                "evidence_epoch_id": epoch,
                "strata_key": reference_strata_key(item),
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 5.0,
                "mad_power_w": 0.05,
                "p25_power_w": 4.95,
                "p75_power_w": 5.05,
                "sample_count": 10,
                "frozen": True,
            }
        )
        db.create_net_benefit_campaign(
            campaign_id="stable-campaign",
            evidence_epoch_id=epoch,
            battery_epoch=1,
            hard_identity_hash="hard",
            calibration_version=1,
            evidence_semantics_version=1,
            fixed_baseline_envelope="INTERACTIVE_EFFICIENT",
            fixed_baseline_content_hash=fixed_hash,
        )
        mode_results = {
            "MONITORING_OVERHEAD": (0.18, "monitoring-policy"),
            "DYNAMIC_CONTROLLER": (-0.05, "dynamic-policy"),
        }
        for mode, (delta_w, run_policy_fingerprint) in mode_results.items():
            run_id = db.start_net_benefit_result(mode=mode)
            db.finish_net_benefit_result(
                run_id,
                {
                    "candidate_minus_reference_w": delta_w,
                    "comparison_quality": "OK",
                    "comparison_design": "A_B_B_A",
                    "evidence_epoch_id": epoch,
                    "campaign_id": "stable-campaign",
                    "fixed_baseline_envelope": "INTERACTIVE_EFFICIENT",
                    "fixed_baseline_content_hash": fixed_hash,
                    "runtime_policy_fingerprint": run_policy_fingerprint,
                    "candidate_block_deltas_w": [delta_w, delta_w],
                },
            )
            if mode == "DYNAMIC_CONTROLLER":
                db.record_net_benefit_campaign_comparison(
                    "stable-campaign",
                    mode=mode,
                    overhead_run_id=run_id,
                    runtime_policy_fingerprint=run_policy_fingerprint,
                )

        too_short = StableReadiness(config, db).assess(now=now)
        assert too_short["ready"] is False
        assert "total_valid_usage_below_minimum" in too_short["reasons"]
        assert "distinct_usage_days_below_minimum" in too_short["reasons"]
        assert "observation_span_below_minimum" in too_short["reasons"]

        for days_ago in (7, 5, 3, 1):
            observed = bind_current_envelope(
                db,
                rollup(now - days_ago * 86400.0 - 60.0, 5.0),
            )
            observed["evidence_epoch_id"] = epoch
            observed["reference_eligible"] = True
            observed["valid_seconds"] = 7200.0
            db.add_rollup(observed)

        ready = StableReadiness(config, db).assess(now=now)
        assert ready["ready"] is True
        assert ready["net_benefit"]["recommendation"] == "FIXED_GOOD_ENVELOPE"
        assert ready["net_benefit"]["selected_policy_mode"] == "FIXED_GOOD"
        fixed_policy_fingerprint = fixed_runtime_identity(
            evidence_epoch_id=epoch,
            envelope="INTERACTIVE_EFFICIENT",
            envelope_content_hash=fixed_hash,
        )
        assert ready["net_benefit"]["selected_policy_fingerprint"] == fixed_policy_fingerprint
        assert ready["current_runtime_mode"]["mode"] == "FIXED_GOOD"
        assert ready["fixed_runtime_audit"]["ready"] is True
        assert ready["usage_coverage"]["trusted_fraction"] == 1.0
        assert ready["usage_coverage"]["distinct_usage_days"] == 5
        assert ready["usage_coverage"]["observation_span_seconds"] >= 7 * 86400.0

        LifecycleManager(db).freeze("qualified fixed-good stable")
        db.set_meta(
            "stable_entry_readiness",
            {
                "qualified": True,
                "evidence_epoch_id": epoch,
                "selected_policy_mode": "FIXED_GOOD",
                "selected_policy_fingerprint": fixed_policy_fingerprint,
                "usage_coverage": ready["usage_coverage"],
            },
        )
        aged_fixed = StableReadiness(config, db).assess(now=now + 40 * 86400.0)
        assert aged_fixed["ready"] is True
        assert aged_fixed["using_stable_entry_coverage"] is True
        assert "total_valid_usage_below_minimum" in aged_fixed["coverage_reasons"]

        db.set_meta(
            "service_heartbeat",
            {
                "ts": now,
                "automation_level": 0,
                "control_state": "READ_ONLY",
            },
        )
        stale_heartbeat_does_not_override_live_audit = StableReadiness(config, db).assess(now=now)
        assert stale_heartbeat_does_not_override_live_audit["ready"] is True
        assert stale_heartbeat_does_not_override_live_audit["current_runtime_mode"]["mode"] == (
            "FIXED_GOOD"
        )

        db.set_meta("service_heartbeat", {"ts": 0.0})
        config.data["automation"]["level"] = 4
        fixed_is_physical = StableReadiness(config, db).assess(now=now)
        assert fixed_is_physical["ready"] is True

        fixed_audit.update({"ready": False, "reasons": ["fixed_hwp_state_mismatch"]})
        audit_failed = StableReadiness(config, db).assess(now=now)
        assert audit_failed["ready"] is False
        assert "fixed_runtime_audit_failed" in audit_failed["reasons"]
    finally:
        db.close()


def _completed_run(
    mode: str,
    delta_w: float,
    ts: float,
    *,
    policy_fingerprint: str = "policy-fingerprint",
    block_deltas: list[float] | None = None,
) -> dict:
    return {
        "run_id": f"{mode}-{ts}",
        "start_ts": ts - 60,
        "end_ts": ts,
        "mode": mode,
        "result": {
            "candidate_minus_reference_w": delta_w,
            "comparison_quality": "OK",
            "comparison_design": "A_B_B_A",
            "campaign_id": "unit-campaign",
            "evidence_epoch_id": "epoch-current",
            "fixed_baseline_envelope": "INTERACTIVE_EFFICIENT",
            "fixed_baseline_content_hash": "fixed-hash",
            "runtime_policy_fingerprint": policy_fingerprint,
            "candidate_block_deltas_w": (
                list(block_deltas) if block_deltas is not None else [delta_w, delta_w]
            ),
        },
    }


def test_net_benefit_keeps_dynamic_when_dynamic_clears_threshold():
    result = assess_net_benefit(
        [
            _completed_run("DYNAMIC_CONTROLLER", -0.30, 110, policy_fingerprint="dynamic"),
        ],
        practical_threshold_w=0.10,
    )
    assert result["complete"] is True
    assert result["recommendation"] == "KEEP_DYNAMIC_CONTROLLER"
    assert result["selected_policy_mode"] == "DYNAMIC_CONTROLLER"
    assert result["selected_policy_fingerprint"] == "dynamic"


def test_net_benefit_ignores_optional_monitoring_diagnostic_for_policy_selection():
    result = assess_net_benefit(
        [
            _completed_run("MONITORING_OVERHEAD", 0.18, 100, policy_fingerprint="monitoring"),
            _completed_run("DYNAMIC_CONTROLLER", -0.28, 110, policy_fingerprint="dynamic"),
        ],
        practical_threshold_w=0.10,
    )
    assert result["recommendation"] == "KEEP_DYNAMIC_CONTROLLER"
    assert result["selected_policy_mode"] == "DYNAMIC_CONTROLLER"
    assert result["selected_policy_fingerprint"] == "dynamic"
    assert result["deltas_w"] == {"DYNAMIC_CONTROLLER": -0.28}


def test_net_benefit_prefers_fixed_good_when_complexity_has_no_practical_gain():
    result = assess_net_benefit(
        [
            _completed_run("MONITORING_OVERHEAD", 0.04, 100, policy_fingerprint="monitoring"),
            _completed_run("DYNAMIC_CONTROLLER", -0.03, 110, policy_fingerprint="dynamic"),
        ],
        practical_threshold_w=0.10,
    )
    assert result["recommendation"] == "FIXED_GOOD_ENVELOPE"
    assert result["selected_policy_mode"] == "FIXED_GOOD"
    assert result["selected_policy_fingerprint"] == fixed_runtime_identity(
        evidence_epoch_id="epoch-current",
        envelope="INTERACTIVE_EFFICIENT",
        envelope_content_hash="fixed-hash",
    )


def test_net_benefit_needs_more_data_when_only_one_dynamic_block_is_practical():
    result = assess_net_benefit(
        [
            _completed_run(
                "DYNAMIC_CONTROLLER",
                -0.11,
                110,
                policy_fingerprint="dynamic",
                block_deltas=[-0.02, -0.20],
            ),
        ],
        practical_threshold_w=0.10,
    )
    assert result["complete"] is False
    assert result["recommendation"] == "NEED_MORE_DATA"
    assert result["dynamic_block_deltas_w"] == [-0.02, -0.20]


def test_usage_coverage_ignores_rollups_from_other_evidence_epochs(project_root: Path):
    db = Database(project_root / "runtime/coverage-epoch.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        old_epoch = db.ensure_evidence_epoch(
            hard_identity_hash="old",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        now = time.time()
        old = bind_current_envelope(db, rollup(now - 120, 5.0))
        old["evidence_epoch_id"] = old_epoch
        old["reference_eligible"] = True
        db.add_rollup(old)

        new_epoch = db.ensure_evidence_epoch(
            hard_identity_hash="new",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        new = bind_current_envelope(db, rollup(now - 60, 6.0))
        new["evidence_epoch_id"] = new_epoch
        new["reference_eligible"] = True
        db.add_rollup(new)
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-new",
                "created_ts": now,
                "evidence_epoch_id": new_epoch,
                "strata_key": reference_strata_key(new),
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 6.0,
                "mad_power_w": 0.05,
                "p25_power_w": 5.95,
                "p75_power_w": 6.05,
                "sample_count": 10,
                "frozen": True,
            }
        )
        coverage = UsageCoverage(db).summarize(
            since_ts=now - 3600,
            evidence_epoch_id=new_epoch,
        )
        assert coverage["total_valid_seconds"] == 60.0
        assert coverage["trusted_seconds"] == 60.0
    finally:
        db.close()


def test_usage_coverage_keeps_dirty_rollup_in_total_but_not_trusted(project_root: Path):
    db = Database(project_root / "runtime/coverage-dirty.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        now = time.time()
        item = bind_current_envelope(db, rollup(now - 60, 5.0))
        item["evidence_epoch_id"] = epoch
        item["reference_eligible"] = False
        item["reference_ineligible_reasons"] = ["power_source_not_all_discharging"]
        item["valid_seconds"] = 20.0
        db.add_rollup(item)
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-dirty-stratum",
                "created_ts": now,
                "evidence_epoch_id": epoch,
                "strata_key": reference_strata_key(item),
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 5.0,
                "mad_power_w": 0.05,
                "p25_power_w": 4.95,
                "p75_power_w": 5.05,
                "sample_count": 10,
                "frozen": True,
            }
        )
        coverage = UsageCoverage(db).summarize(
            since_ts=now - 3600,
            evidence_epoch_id=epoch,
        )
        assert coverage["total_valid_seconds"] == 20.0
        assert coverage["verified_seconds"] == 20.0
        assert coverage["trusted_seconds"] == 0.0
        assert coverage["trusted_fraction"] == 0.0
        assert sum(coverage["uncovered_demand_seconds"].values()) == 20.0
    finally:
        db.close()


def test_usage_coverage_does_not_trust_old_envelope_revision(project_root: Path):
    db = Database(project_root / "runtime/coverage-envelope-revision.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        epoch = db.ensure_evidence_epoch(
            hard_identity_hash="hard",
            battery_epoch=1,
            calibration_version=1,
            evidence_semantics_version=1,
            payload={},
        )
        now = time.time()
        old = bind_current_envelope(db, rollup(now - 60, 5.0))
        old["evidence_epoch_id"] = epoch
        old["reference_eligible"] = True
        db.add_rollup(old)
        db.upsert_reference_baseline(
            {
                "reference_id": "ref-old-revision",
                "created_ts": now,
                "evidence_epoch_id": epoch,
                "strata_key": reference_strata_key(old),
                "envelope": "INTERACTIVE_EFFICIENT",
                "median_power_w": 5.0,
                "mad_power_w": 0.05,
                "p25_power_w": 4.95,
                "p75_power_w": 5.05,
                "sample_count": 10,
                "frozen": True,
            }
        )
        envelope = db.envelope("INTERACTIVE_EFFICIENT")
        assert envelope is not None
        envelope["revision"] = int(envelope.get("revision") or 1) + 1
        envelope["content_hash"] = "new-content-hash"
        envelope["max_perf_pct"] = max(30, int(envelope["max_perf_pct"]) - 5)
        envelope["status"] = "VERIFIED"
        db.upsert_envelope(envelope)

        coverage = UsageCoverage(db).summarize(
            since_ts=now - 3600,
            evidence_epoch_id=epoch,
        )
        assert coverage["total_valid_seconds"] == 60.0
        assert coverage["verified_seconds"] == 0.0
        assert coverage["trusted_seconds"] == 0.0
    finally:
        db.close()


def test_net_benefit_filters_runs_by_current_evidence_epoch():
    runs = [
        {
            **_completed_run("MONITORING_OVERHEAD", 0.01, 100),
            "result": {
                **_completed_run("MONITORING_OVERHEAD", 0.01, 100)["result"],
                "evidence_epoch_id": "old",
            },
        },
        {
            **_completed_run("DYNAMIC_CONTROLLER", -0.2, 110),
            "result": {
                **_completed_run("DYNAMIC_CONTROLLER", -0.2, 110)["result"],
                "evidence_epoch_id": "old",
            },
        },
    ]
    result = assess_net_benefit(
        runs,
        practical_threshold_w=0.1,
        evidence_epoch_id="current",
        complete_campaign_ids={"a"},
    )
    assert result["complete"] is False
    assert result["recommendation"] == "NEED_MORE_DATA"


def test_net_benefit_requires_one_complete_campaign_within_epoch():
    runs = [
        {
            **_completed_run("MONITORING_OVERHEAD", 0.01, 100),
            "result": {
                **_completed_run("MONITORING_OVERHEAD", 0.01, 100)["result"],
                "evidence_epoch_id": "current",
                "campaign_id": "a",
            },
        },
        {
            **_completed_run("DYNAMIC_CONTROLLER", -0.2, 110),
            "result": {
                **_completed_run("DYNAMIC_CONTROLLER", -0.2, 110)["result"],
                "evidence_epoch_id": "current",
                "campaign_id": "b",
            },
        },
    ]
    result = assess_net_benefit(
        runs,
        practical_threshold_w=0.1,
        evidence_epoch_id="current",
        complete_campaign_ids={"a"},
    )
    assert result["complete"] is False
    assert result["campaign_id"] is None


def test_net_benefit_does_not_let_monitoring_diagnostic_change_formal_baseline():
    monitoring = _completed_run("MONITORING_OVERHEAD", 0.05, 100)
    monitoring["result"] = {
        **monitoring["result"],
        "fixed_baseline_content_hash": "diagnostic-fixed",
    }
    dynamic = _completed_run("DYNAMIC_CONTROLLER", -0.2, 110)
    result = assess_net_benefit(
        [monitoring, dynamic],
        practical_threshold_w=0.1,
    )
    assert result["complete"] is True
    assert result["fixed_baseline_content_hash"] == "fixed-hash"


def test_net_benefit_requires_campaign_marked_complete_by_capture_contract():
    runs = [_completed_run("DYNAMIC_CONTROLLER", -0.2, 100.0)]
    result = assess_net_benefit(
        runs,
        practical_threshold_w=0.1,
        complete_campaign_ids={"different-campaign"},
    )
    assert result["complete"] is False
    assert result["recommendation"] == "NEED_MORE_DATA"


def test_runtime_policy_snapshot_changes_when_core_control_code_changes(project_root: Path):
    from sp7_powerlab.config import load_config

    package_root = project_root / "src" / "sp7_powerlab"
    package_root.mkdir(parents=True)
    controller = package_root / "controller.py"
    controller.write_text("CONTROL_VERSION = 1\n", encoding="utf-8")
    config = load_config(project_root)
    db = Database(project_root / "runtime/policy-code.sqlite3")
    try:
        first = runtime_policy_snapshot(config, db)
        controller.write_text("CONTROL_VERSION = 2\n", encoding="utf-8")
        second = runtime_policy_snapshot(config, db)
        assert first["payload"]["code_identity"]["aggregate_sha256"]
        assert first["fingerprint"] != second["fingerprint"]
    finally:
        db.close()


def test_runtime_policy_snapshot_ignores_scheduler_code_for_level_one_runtime(project_root: Path):
    from sp7_powerlab.config import load_config

    package_root = project_root / "src" / "sp7_powerlab"
    package_root.mkdir(parents=True)
    scheduler = package_root / "scheduler.py"
    scheduler.write_text("SEARCH_VERSION = 1\n", encoding="utf-8")
    config = load_config(project_root)
    db = Database(project_root / "runtime/policy-scheduler.sqlite3")
    try:
        first = runtime_policy_snapshot(config, db)
        scheduler.write_text("SEARCH_VERSION = 2\n", encoding="utf-8")
        second = runtime_policy_snapshot(config, db)
        assert first["fingerprint"] == second["fingerprint"]
    finally:
        db.close()


def test_stage_e_code_identity_includes_cli_contract(project_root: Path):
    from sp7_powerlab.config import load_config

    package_root = project_root / "src" / "sp7_powerlab"
    package_root.mkdir(parents=True)
    cli = package_root / "cli.py"
    cli.write_text("STAGE_E_VERSION = 1\n", encoding="utf-8")
    config = load_config(project_root)
    first = stage_e_code_identity(config)
    cli.write_text("STAGE_E_VERSION = 2\n", encoding="utf-8")
    second = stage_e_code_identity(config)
    assert first["aggregate_sha256"] != second["aggregate_sha256"]


def test_runtime_mode_status_is_independent_of_policy_fingerprint(project_root: Path):
    from sp7_powerlab.config import load_config

    config = load_config(project_root)
    config.data["automation"]["level"] = 0
    db = Database(project_root / "runtime/runtime-mode.sqlite3")
    try:
        assert runtime_mode_status(config, db)["mode"] == "FIXED_GOOD"
        now = time.time()
        db.set_meta(
            "service_heartbeat",
            {"ts": now, "automation_level": 0, "control_state": "READ_ONLY"},
        )
        assert runtime_mode_status(config, db, now=now)["mode"] == "MONITORING"

        config.data["automation"]["level"] = 1
        db.set_meta(
            "service_heartbeat",
            {"ts": now, "automation_level": 1, "control_state": "CONTROL_ALLOWED"},
        )
        assert runtime_mode_status(config, db, now=now)["mode"] == "DYNAMIC_CONTROLLER"
    finally:
        db.close()


def test_resolved_negative_feedback_does_not_block_current_policy(project_root: Path):
    db = Database(project_root / "runtime/feedback-scope.sqlite3")
    registry = EnvelopeRegistry(project_root, db)
    registry.load()
    try:
        envelope = db.envelope("INTERACTIVE_EFFICIENT")
        assert envelope is not None
        envelope["status"] = "VERIFIED"
        db.upsert_envelope(envelope)
        db.set_meta("current_envelope", "INTERACTIVE_EFFICIENT")

        assert negative_feedback_blocks_current_policy(
            db,
            {"rating": "sluggish", "envelope": "INTERACTIVE_EFFICIENT"},
            current_envelope="INTERACTIVE_EFFICIENT",
        )

        db.create_trial(
            {
                "trial_id": "trial-rejected-feedback",
                "state": "ROLLED_BACK",
                "candidate": {},
            }
        )
        assert not negative_feedback_blocks_current_policy(
            db,
            {"rating": "bad", "trial_id": "trial-rejected-feedback"},
            current_envelope="INTERACTIVE_EFFICIENT",
        )

        db.create_trial(
            {
                "trial_id": "trial-equivalent-feedback",
                "state": "EQUIVALENT",
                "candidate": {},
            }
        )
        assert not negative_feedback_blocks_current_policy(
            db,
            {"rating": "sluggish", "trial_id": "trial-equivalent-feedback"},
            current_envelope="INTERACTIVE_EFFICIENT",
        )

        envelope["status"] = "BLOCKED"
        db.upsert_envelope(envelope)
        assert not negative_feedback_blocks_current_policy(
            db,
            {"rating": "sluggish", "envelope": "INTERACTIVE_EFFICIENT"},
            current_envelope="INTERACTIVE_EFFICIENT",
        )
    finally:
        db.close()


def test_meter_comparison_uses_time_weighted_power_and_fails_closed_on_mismatch():
    reference = [
        meter_row(0, 4.0, 40.0),
        meter_row(30, 4.0, 40.0 - 4.0 * 30 / 3600.0),
        meter_row(120, 8.0, 40.0 - (4.0 * 30 + 8.0 * 90) / 3600.0),
    ]
    candidate = [
        meter_row(0, 5.0, 40.0),
        meter_row(30, 5.0, 40.0 - 5.0 * 30 / 3600.0),
        meter_row(120, 5.0, 40.0 - 5.0 * 120 / 3600.0),
    ]
    result = compare_meter_runs(reference, candidate, max_gap_seconds=120.0)
    assert result["comparison_quality"] == "OK"
    assert abs(result["reference"]["mean_power_w"] - 5.5) < 1e-9
    assert abs(result["candidate_minus_reference_w"] + 0.5) < 1e-9

    mismatched = [
        meter_row(0, 5.0, 40.0),
        meter_row(60, 5.0, 39.99),
        meter_row(120, 5.0, 39.98),
    ]
    failed = compare_meter_runs(reference, mismatched, max_gap_seconds=120.0)
    assert failed["comparison_quality"] == "DATA_QUALITY_FAILURE"
    assert failed["candidate_minus_reference_w"] is None


def test_paired_meter_comparison_rejects_discontinuous_candidate_block():
    before = paired_meter_rows(5.0)
    discontinuous = paired_meter_rows(4.8)
    discontinuous[5]["battery_status"] = "Charging"
    after = paired_meter_rows(5.0)

    summary = summarize_minimal_meter_samples(discontinuous)
    assert summary["consistency_status"] == "DISCONTINUOUS_OBSERVATION"
    assert summary["energy_valid_seconds"] >= 300.0
    assert summary["data_quality"] == "DATA_QUALITY_FAILURE"
    assert "discontinuous_observation" in summary["data_quality_reasons"]
    assert "multiple_or_missing_discharge_segments" in summary["data_quality_reasons"]

    result = compare_paired_meter_runs(
        before,
        discontinuous,
        paired_meter_rows(4.85),
        after,
        minimum_block_seconds=300.0,
    )
    assert result["comparison_quality"] == "DATA_QUALITY_FAILURE"
    assert "candidate_first_data_quality_failed" in result["comparison_quality_reasons"]
    assert result["candidate_minus_reference_w"] is None


def test_paired_meter_comparison_requires_comparable_a_b_b_a_blocks():
    before = paired_meter_rows(5.0)
    candidate = paired_meter_rows(4.8)
    after = paired_meter_rows(5.1)
    result = compare_paired_meter_runs(
        before,
        candidate,
        paired_meter_rows(4.85),
        after,
        minimum_block_seconds=300.0,
    )
    assert result["comparison_design"] == "A_B_B_A"
    assert result["comparison_quality"] == "OK"
    assert abs(result["paired_reference_power_w"] - 5.05) < 1e-9
    assert abs(result["candidate_minus_reference_w"] + 0.225) < 1e-9
    assert len(result["candidate_block_deltas_w"]) == 2

    incomparable = compare_paired_meter_runs(
        before,
        paired_meter_rows(4.8, brightness=80.0),
        paired_meter_rows(4.85, brightness=80.0),
        after,
        minimum_block_seconds=300.0,
    )
    assert incomparable["comparison_quality"] == "DATA_QUALITY_FAILURE"
    assert "candidate_first_brightness_not_comparable" in incomparable["comparison_quality_reasons"]
    assert incomparable["candidate_minus_reference_w"] is None
