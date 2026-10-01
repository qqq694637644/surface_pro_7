from __future__ import annotations

from pathlib import Path
from typing import Any

from .actuators.hwp import HWPActuator
from .config import Config, load_machine, load_thermal_config
from .envelopes import snapshot_matches_envelope
from .hardware import (
    compatibility_state,
    hard_control_identity,
    inspect_hardware,
    systemd_user_unit_enabled,
    systemd_user_unit_state,
)
from .storage import Database


def fixed_runtime_identity(
    *,
    evidence_epoch_id: str,
    envelope: str,
    envelope_content_hash: str,
) -> str:
    from .hardware import fingerprint_hash

    return fingerprint_hash(
        {
            "evidence_epoch_id": evidence_epoch_id,
            "envelope": envelope,
            "envelope_content_hash": envelope_content_hash,
        }
    )


def live_media_compatibility(
    config: Config,
    *,
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    machine = load_machine(config.root)
    identity = machine.get("identity") or {}
    report = inspect_hardware(
        sys_root=sys_root,
        proc_root=proc_root,
        expected_product=str(identity.get("expected_product", "Surface Pro 7")),
        expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
        configured_thermal_sensor=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
        include_versions=True,
    )
    state = compatibility_state(report)
    return {
        "media_compatibility_generation": state["media_compatibility_generation"],
        "media_versions": state["media_versions"],
    }


def audit_fixed_runtime(
    config: Config,
    db: Database,
    *,
    evidence_epoch: dict[str, Any] | None,
    fixed_baseline_envelope: str | None,
    fixed_baseline_content_hash: str | None,
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    require_persistent_selection: bool = False,
) -> dict[str, Any]:
    machine = load_machine(config.root)
    identity = machine.get("identity") or {}
    calibration = machine.get("calibration") or {}
    thermal_config = load_thermal_config(config.root)
    report = inspect_hardware(
        sys_root=sys_root,
        proc_root=proc_root,
        expected_product=str(identity.get("expected_product", "Surface Pro 7")),
        expected_cpu_substring=str(identity.get("expected_cpu_substring", "i5-1035G4")),
        configured_thermal_sensor=str((machine.get("thermal") or {}).get("sensor_path") or "")
        or None,
        include_versions=True,
    )
    live_hard_hash, live_hard_payload = hard_control_identity(
        report,
        thermal_config=thermal_config,
        calibration_version=int(calibration.get("version") or 0),
    )

    service_state = systemd_user_unit_state("sp7-powerlab.service")
    service_enabled = systemd_user_unit_enabled("sp7-powerlab.service")
    fixed_unit_enabled = systemd_user_unit_enabled("sp7-powerlab-fixed.service")
    hourly_timer_state = systemd_user_unit_state("sp7-powerlab-hourly.timer")
    hourly_service_state = systemd_user_unit_state("sp7-powerlab-hourly.service")

    reasons: list[str] = []
    if service_state != "inactive":
        reasons.append("main_service_not_inactive")
    selection = db.get_meta("fixed_good_selection", {})
    if require_persistent_selection:
        if service_enabled not in {"disabled", "masked"}:
            reasons.append("main_service_not_disabled")
        if fixed_unit_enabled != "enabled":
            reasons.append("fixed_oneshot_not_enabled")
        if not isinstance(selection, dict) or (
            str(selection.get("envelope") or "") != str(fixed_baseline_envelope or "")
            or str(selection.get("content_hash") or "") != str(fixed_baseline_content_hash or "")
        ):
            reasons.append("fixed_persistent_selection_mismatch")
    if hourly_timer_state not in {"inactive", "unavailable"}:
        reasons.append("hourly_timer_active")
    if hourly_service_state not in {"inactive", "unavailable"}:
        reasons.append("hourly_service_active")
    if report.thermald.get("active") is not True:
        reasons.append("thermald_not_active")
    if report.ownership_conflicts:
        reasons.append("conflicting_power_writer_active")
    if not report.supported_machine:
        reasons.append("hardware_contract_mismatch")
    if evidence_epoch is None:
        reasons.append("missing_evidence_epoch")
    elif str(evidence_epoch.get("hard_identity_hash") or "") != live_hard_hash:
        reasons.append("live_hard_identity_mismatch")

    envelope = db.envelope(str(fixed_baseline_envelope)) if fixed_baseline_envelope else None
    if not envelope or envelope.get("status") != "VERIFIED":
        reasons.append("fixed_baseline_not_verified")
    elif str(envelope.get("content_hash") or "") != str(fixed_baseline_content_hash or ""):
        reasons.append("fixed_baseline_content_hash_mismatch")

    hwp_snapshot: dict[str, Any] | None = None
    hwp_matches = False
    if envelope:
        try:
            hwp_snapshot = HWPActuator(sys_root).snapshot()
            hwp_matches = snapshot_matches_envelope(hwp_snapshot, envelope)
        except Exception:
            reasons.append("fixed_hwp_snapshot_unavailable")
        else:
            if not hwp_matches:
                reasons.append("fixed_hwp_state_mismatch")
    else:
        reasons.append("fixed_hwp_baseline_unavailable")
    compatibility = compatibility_state(report)

    return {
        "ready": not reasons,
        "reasons": sorted(set(reasons)),
        "service_state": service_state,
        "service_enabled": service_enabled,
        "fixed_oneshot_enabled": fixed_unit_enabled,
        "fixed_good_selection": selection,
        "hourly_timer_state": hourly_timer_state,
        "hourly_service_state": hourly_service_state,
        "thermald_active": report.thermald.get("active"),
        "ownership_conflicts": list(report.ownership_conflicts),
        "expected_hard_identity_hash": ((evidence_epoch or {}).get("hard_identity_hash")),
        "live_hard_identity_hash": live_hard_hash,
        "live_hard_identity": live_hard_payload,
        "fixed_baseline_envelope": fixed_baseline_envelope,
        "fixed_baseline_content_hash": fixed_baseline_content_hash,
        "fixed_baseline_verified": bool(envelope and envelope.get("status") == "VERIFIED"),
        "hwp_matches_fixed_baseline": hwp_matches,
        "hwp_snapshot": hwp_snapshot,
        "live_media_compatibility_generation": compatibility["media_compatibility_generation"],
        "live_media_versions": compatibility["media_versions"],
    }
