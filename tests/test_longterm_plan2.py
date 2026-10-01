from __future__ import annotations

import time
from pathlib import Path

from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.evidence import reference_strata_key
from sp7_powerlab.longterm import (
    DriftDetector,
    StableReadiness,
    UsageCoverage,
    assess_net_benefit,
    compare_meter_runs,
    compare_paired_meter_runs,
    minutes_gained_per_charge,
)
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
        item = rollup(now - 60, 5.0)
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
):
    from sp7_powerlab.config import load_config

    config = load_config(project_root)
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
        item = rollup(now - 60, 5.0)
        item["evidence_epoch_id"] = epoch
        item["reference_eligible"] = True
        db.add_rollup(item)
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
        for mode in ("MONITORING_OVERHEAD", "DYNAMIC_CONTROLLER", "FULL_POWERLAB"):
            run_id = db.start_monitoring_overhead_run(mode=mode)
            db.finish_monitoring_overhead_run(
                run_id,
                {
                    "candidate_minus_reference_w": -0.1,
                    "comparison_quality": "OK",
                    "comparison_design": "A_B_B_A",
                    "evidence_epoch_id": epoch,
                    "campaign_id": "stable-campaign",
                    "fixed_baseline_envelope": "INTERACTIVE_EFFICIENT",
                    "fixed_baseline_content_hash": "fixed-hash",
                },
            )

        ready = StableReadiness(config, db).assess(now=now)
        assert ready["ready"] is True
        assert ready["usage_coverage"]["trusted_fraction"] == 1.0
    finally:
        db.close()


def _completed_run(mode: str, delta_w: float, ts: float) -> dict:
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
            "fixed_baseline_envelope": "INTERACTIVE_EFFICIENT",
            "fixed_baseline_content_hash": "fixed-hash",
        },
    }


def test_net_benefit_recommends_full_when_full_system_clears_threshold():
    result = assess_net_benefit(
        [
            _completed_run("MONITORING_OVERHEAD", 0.05, 100),
            _completed_run("DYNAMIC_CONTROLLER", -0.30, 110),
            _completed_run("FULL_POWERLAB", -0.20, 120),
        ],
        practical_threshold_w=0.10,
    )
    assert result["complete"] is True
    assert result["recommendation"] == "KEEP_FULL_POWERLAB"


def test_net_benefit_prefers_dynamic_when_monitoring_consumes_controller_savings():
    result = assess_net_benefit(
        [
            _completed_run("MONITORING_OVERHEAD", 0.18, 100),
            _completed_run("DYNAMIC_CONTROLLER", -0.28, 110),
            _completed_run("FULL_POWERLAB", -0.05, 120),
        ],
        practical_threshold_w=0.10,
    )
    assert result["recommendation"] == "KEEP_DYNAMIC_REDUCE_MONITORING"
    assert "monitoring overhead consumes part" in " ".join(result["reasons"])


def test_net_benefit_prefers_fixed_good_when_complexity_has_no_practical_gain():
    result = assess_net_benefit(
        [
            _completed_run("MONITORING_OVERHEAD", 0.04, 100),
            _completed_run("DYNAMIC_CONTROLLER", -0.03, 110),
            _completed_run("FULL_POWERLAB", 0.02, 120),
        ],
        practical_threshold_w=0.10,
    )
    assert result["recommendation"] == "FIXED_GOOD_ENVELOPE"


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
        old = rollup(now - 120, 5.0)
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
        new = rollup(now - 60, 6.0)
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
        item = rollup(now - 60, 5.0)
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


def test_net_benefit_filters_runs_by_current_evidence_epoch():
    runs = [
        {
            **_completed_run("MONITORING_OVERHEAD", 0.01, 100),
            "result": {
                "candidate_minus_reference_w": 0.01,
                "comparison_quality": "OK",
                "evidence_epoch_id": "old",
            },
        },
        {
            **_completed_run("DYNAMIC_CONTROLLER", -0.2, 110),
            "result": {
                "candidate_minus_reference_w": -0.2,
                "comparison_quality": "OK",
                "evidence_epoch_id": "old",
            },
        },
        {
            **_completed_run("FULL_POWERLAB", -0.2, 120),
            "result": {
                "candidate_minus_reference_w": -0.2,
                "comparison_quality": "OK",
                "evidence_epoch_id": "old",
            },
        },
    ]
    result = assess_net_benefit(
        runs,
        practical_threshold_w=0.1,
        evidence_epoch_id="current",
    )
    assert result["complete"] is False
    assert result["recommendation"] == "NEED_MORE_DATA"


def test_net_benefit_requires_one_complete_campaign_within_epoch():
    runs = [
        {
            **_completed_run("MONITORING_OVERHEAD", 0.01, 100),
            "result": {
                "candidate_minus_reference_w": 0.01,
                "comparison_quality": "OK",
                "evidence_epoch_id": "current",
                "campaign_id": "a",
            },
        },
        {
            **_completed_run("DYNAMIC_CONTROLLER", -0.2, 110),
            "result": {
                "candidate_minus_reference_w": -0.2,
                "comparison_quality": "OK",
                "evidence_epoch_id": "current",
                "campaign_id": "b",
            },
        },
        {
            **_completed_run("FULL_POWERLAB", -0.2, 120),
            "result": {
                "candidate_minus_reference_w": -0.2,
                "comparison_quality": "OK",
                "evidence_epoch_id": "current",
                "campaign_id": "c",
            },
        },
    ]
    result = assess_net_benefit(
        runs,
        practical_threshold_w=0.1,
        evidence_epoch_id="current",
    )
    assert result["complete"] is False
    assert result["campaign_id"] is None


def test_net_benefit_rejects_same_campaign_with_different_fixed_baselines():
    runs = []
    for index, mode in enumerate(("MONITORING_OVERHEAD", "DYNAMIC_CONTROLLER", "FULL_POWERLAB")):
        run = _completed_run(mode, -0.2, 100 + index * 10)
        run["result"] = {
            **run["result"],
            "evidence_epoch_id": "current",
            "campaign_id": "campaign-a",
            "fixed_baseline_content_hash": f"fixed-{index}",
        }
        runs.append(run)
    result = assess_net_benefit(
        runs,
        practical_threshold_w=0.1,
        evidence_epoch_id="current",
    )
    assert result["complete"] is False
    assert result["recommendation"] == "NEED_MORE_DATA"


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
