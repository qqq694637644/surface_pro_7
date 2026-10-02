from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from .config import load_config, load_machine
from .demand import remote_process_tags
from .envelopes import snapshot_matches_envelope
from .hardware import systemd_user_unit_state, thermal_sensor_path
from .longterm import (
    dynamic_runtime_code_identity,
    dynamic_runtime_config_identity,
    runtime_mode_status,
    runtime_policy_snapshot,
    stage_e_contract_identity,
)
from .measurement import MinimalMeter, measurement_trust_matches_epoch
from .runtime_audit import live_media_compatibility
from .service import build_actuator
from .storage import Database
from .telemetry import _brightness, _media_playing, _network_bytes, _temperature_c, _user_active

CAPTURE_MODES = ("FIXED_GOOD", "MONITORING", "DYNAMIC_CONTROLLER")
NET_BENEFIT_BACKGROUND_UNITS = (
    "sp7-powerlab-hourly.timer",
    "sp7-powerlab-hourly.service",
)


def _default_root() -> Path:
    configured = os.environ.get("SP7_POWERLAB_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "config" / "powerlab.toml").exists():
        return cwd
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "config" / "powerlab.toml").exists():
        return source_root
    return cwd


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sp7-powerlab-meter",
        description="Minimal provenance-bound Surface Pro 7 battery meter for Net Benefit runs.",
    )
    p.add_argument("--count", type=int)
    p.add_argument("--config")
    p.add_argument("--campaign", required=True)
    p.add_argument("--mode", required=True, choices=CAPTURE_MODES)
    return p


def _hourly_units_status() -> dict[str, str]:
    return {unit: systemd_user_unit_state(unit) for unit in NET_BENEFIT_BACKGROUND_UNITS}


def _service_mode_status(config: Any, db: Database, mode: str) -> dict[str, Any]:
    status = runtime_mode_status(config, db)
    actual_mode = str(status["mode"])
    service_unit_state = systemd_user_unit_state("sp7-powerlab.service")
    if mode == "FIXED_GOOD":
        if service_unit_state != "inactive":
            raise RuntimeError("FIXED_GOOD capture requires the PowerLab service to be stopped")
    elif mode == "MONITORING":
        if service_unit_state != "active" or actual_mode != "MONITORING":
            raise RuntimeError("MONITORING capture requires a live service with automation.level=0")
    elif mode == "DYNAMIC_CONTROLLER":
        if service_unit_state != "active" or actual_mode not in {
            "DYNAMIC_CONTROLLER",
            "THERMAL_INTERVENTION",
        }:
            raise RuntimeError(
                "DYNAMIC_CONTROLLER capture requires a live CONTROL_ALLOWED service at automation.level=1"
            )
    else:  # pragma: no cover - argparse prevents this
        raise RuntimeError(f"unknown capture mode: {mode}")
    hourly_units = _hourly_units_status()
    active_units = [
        unit
        for unit, state in hourly_units.items()
        if state in {"active", "activating", "reloading", "deactivating"}
    ]
    if active_units:
        raise RuntimeError(
            "Net Benefit capture requires hourly background units to be stopped: "
            + ", ".join(active_units)
        )
    if (
        mode in {"MONITORING", "DYNAMIC_CONTROLLER"}
        and str(status.get("telemetry_mode") or "") == "DIAGNOSTIC_BURST"
    ):
        raise RuntimeError("formal Net Benefit capture cannot run during a diagnostic burst")
    if mode in {"MONITORING", "DYNAMIC_CONTROLLER"}:
        expected_code = str(dynamic_runtime_code_identity(config).get("aggregate_sha256") or "")
        expected_config = str(dynamic_runtime_config_identity(config).get("identity") or "")
        if (
            str(status.get("runtime_code_identity") or "") != expected_code
            or str(status.get("runtime_config_identity") or "") != expected_config
        ):
            raise RuntimeError(
                "runtime implementation stale; restart sp7-powerlab.service before capture"
            )
    return {
        **status,
        "service_unit_state": service_unit_state,
        "hourly_units": hourly_units,
    }


def _capture_context(root: Path, config: Any, db: Database, *, mode: str) -> dict[str, Any]:
    epoch = db.active_evidence_epoch()
    battery = db.active_battery_epoch_record()
    fingerprint = db.active_system_fingerprint()
    machine = load_machine(root)
    calibration = machine.get("calibration") or {}
    trust = db.get_meta("measurement_trust", {})
    if not epoch:
        raise RuntimeError("active evidence epoch is required for a MinimalMeter capture")
    if not battery:
        raise RuntimeError("active battery epoch is required for a MinimalMeter capture")
    if not measurement_trust_matches_epoch(trust, epoch):
        raise RuntimeError("current evidence epoch requires Measurement Trust READY")
    if db.active_trial():
        raise RuntimeError("MinimalMeter Net Benefit capture cannot run during an active trial")
    if db.active_calibration():
        raise RuntimeError("MinimalMeter Net Benefit capture cannot run during calibration")
    if db.active_investigation():
        raise RuntimeError("MinimalMeter Net Benefit capture cannot run during an investigation")
    if any(event.get("status") == "OPEN" for event in db.recent_unexpected_power_events(200)):
        raise RuntimeError(
            "MinimalMeter Net Benefit capture requires no unresolved UnexpectedPower event"
        )
    if int(epoch.get("battery_epoch") or 0) != int(battery.get("epoch") or 0):
        raise RuntimeError("active evidence epoch does not match the active battery epoch")
    if int(epoch.get("calibration_version") or 0) != int(calibration.get("version") or 0):
        raise RuntimeError("active evidence epoch does not match current calibration")
    if int(epoch.get("evidence_semantics_version") or 0) != int(
        config.get("evidence.semantics_version", 0)
    ):
        raise RuntimeError("active evidence epoch does not match current evidence semantics")
    if not fingerprint or str(epoch.get("hard_identity_hash") or "") != str(fingerprint):
        raise RuntimeError("active evidence epoch does not match the current hard fingerprint")

    envelope = str(db.get_meta("current_envelope", "") or "")
    envelope_record = db.envelope(envelope) if envelope else None
    if not envelope_record or envelope_record.get("status") != "VERIFIED":
        raise RuntimeError("MinimalMeter capture requires a current VERIFIED envelope")
    service_mode = _service_mode_status(config, db, mode)
    policy = runtime_policy_snapshot(config, db)
    stage_e_identity = stage_e_contract_identity(config)
    media_generation = str(
        live_media_compatibility(config).get("media_compatibility_generation") or "media-missing"
    )

    return {
        "evidence_epoch_id": str(epoch["epoch_id"]),
        "battery_epoch": int(battery["epoch"]),
        "battery_identity_hash": str(battery["identity_hash"]),
        "hard_identity_hash": str(epoch["hard_identity_hash"]),
        "calibration_version": int(epoch["calibration_version"]),
        "evidence_semantics_version": int(epoch["evidence_semantics_version"]),
        "envelope": envelope,
        "envelope_content_hash": str(envelope_record.get("content_hash") or ""),
        "runtime_policy_fingerprint": str(policy["fingerprint"]),
        "stage_e_contract_identity": str(stage_e_identity.get("identity") or ""),
        "media_compatibility_generation": media_generation,
        "runtime_policy": policy["payload"],
        "stage_e_contract": stage_e_identity["payload"],
        "service_mode": service_mode,
    }


def _capture_context_change_reason(
    start: dict[str, Any],
    final: dict[str, Any],
    *,
    mode: str,
) -> str | None:
    stable_context_fields = (
        "evidence_epoch_id",
        "battery_epoch",
        "battery_identity_hash",
        "hard_identity_hash",
        "calibration_version",
        "evidence_semantics_version",
        "runtime_policy_fingerprint",
        "stage_e_contract_identity",
        "media_compatibility_generation",
    )
    if any(final[field] != start[field] for field in stable_context_fields):
        return "capture_context_changed"
    if mode in {"FIXED_GOOD", "MONITORING"} and (
        final["envelope"] != start["envelope"]
        or final["envelope_content_hash"] != start["envelope_content_hash"]
    ):
        return "fixed_capture_envelope_changed"
    return None


def _prepare_campaign(
    db: Database,
    *,
    campaign_id: str,
    mode: str,
    context: dict[str, Any],
    max_campaign_span_seconds: float,
) -> dict[str, Any]:
    campaign = db.net_benefit_campaign(campaign_id)
    now = time.time()
    if campaign is None:
        if mode != "FIXED_GOOD":
            raise RuntimeError("a new Net Benefit campaign must begin with a FIXED_GOOD capture")
        return db.create_net_benefit_campaign(
            campaign_id=campaign_id,
            evidence_epoch_id=str(context["evidence_epoch_id"]),
            battery_epoch=int(context["battery_epoch"]),
            hard_identity_hash=str(context["hard_identity_hash"]),
            calibration_version=int(context["calibration_version"]),
            evidence_semantics_version=int(context["evidence_semantics_version"]),
            fixed_baseline_envelope=str(context["envelope"]),
            fixed_baseline_content_hash=str(context["envelope_content_hash"]),
            payload={
                "comparisons": {},
                "stage_e_contract_identity": str(context["stage_e_contract_identity"]),
                "media_compatibility_generation": str(context["media_compatibility_generation"]),
            },
        )
    if campaign.get("status") != "OPEN":
        raise RuntimeError(f"Net Benefit campaign is not OPEN: {campaign.get('status')}")
    if now - float(campaign.get("created_ts") or 0.0) > float(max_campaign_span_seconds):
        db.invalidate_net_benefit_campaign(campaign_id, "campaign_span_exceeded")
        raise RuntimeError("Net Benefit campaign exceeded max_campaign_span_seconds")
    context_fields = (
        "evidence_epoch_id",
        "battery_epoch",
        "hard_identity_hash",
        "calibration_version",
        "evidence_semantics_version",
    )
    mismatched = [
        field for field in context_fields if str(campaign.get(field)) != str(context.get(field))
    ]
    if mismatched:
        db.invalidate_net_benefit_campaign(
            campaign_id,
            "campaign_context_changed:" + ",".join(sorted(mismatched)),
        )
        raise RuntimeError("Net Benefit campaign context changed: " + ", ".join(sorted(mismatched)))
    campaign_payload = campaign.get("payload") or {}
    campaign_contract_identity = str(campaign_payload.get("stage_e_contract_identity") or "")
    if campaign_contract_identity != str(context.get("stage_e_contract_identity") or ""):
        db.invalidate_net_benefit_campaign(campaign_id, "stage_e_contract_identity_changed")
        raise RuntimeError("Net Benefit campaign Stage E contract identity changed")
    campaign_media_generation = str(campaign_payload.get("media_compatibility_generation") or "")
    if campaign_media_generation != str(context.get("media_compatibility_generation") or ""):
        db.invalidate_net_benefit_campaign(campaign_id, "media_compatibility_generation_changed")
        raise RuntimeError("Net Benefit campaign media compatibility generation changed")
    if mode == "FIXED_GOOD" and (
        str(campaign.get("fixed_baseline_envelope") or "") != str(context.get("envelope") or "")
        or str(campaign.get("fixed_baseline_content_hash") or "")
        != str(context.get("envelope_content_hash") or "")
    ):
        db.invalidate_net_benefit_campaign(campaign_id, "fixed_baseline_changed")
        raise RuntimeError("Net Benefit fixed baseline changed within the campaign")
    return campaign


def _remote_present() -> bool:
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            info = proc.info
            if remote_process_tags(info.get("name"), info.get("exe"), info.get("cmdline")):
                return True
        except (psutil.Error, OSError):
            continue
    return False


@dataclass
class _MinimalContextSampler:
    sys_root: Path
    proc_root: Path
    thermal_path: Path | None
    _last_network: tuple[float, int, int] | None = None

    def sample(self, ts: float) -> dict[str, Any]:
        rx, tx = _network_bytes(self.proc_root)
        rx_mbps = 0.0
        tx_mbps = 0.0
        if self._last_network is not None:
            previous_ts, previous_rx, previous_tx = self._last_network
            dt = ts - previous_ts
            if dt > 0:
                rx_mbps = max(0, rx - previous_rx) * 8 / dt / 1_000_000.0
                tx_mbps = max(0, tx - previous_tx) * 8 / dt / 1_000_000.0
        self._last_network = (ts, rx, tx)
        return {
            "brightness_pct": _brightness(self.sys_root),
            "user_active": _user_active(),
            "media_playing": _media_playing(),
            "remote_present": _remote_present(),
            "network_rx_mbps": rx_mbps,
            "network_tx_mbps": tx_mbps,
            "package_temp_c": _temperature_c(self.sys_root, self.thermal_path),
        }


def _intervening_runtime_activity(db: Database, start_ts: float) -> list[str]:
    reasons: list[str] = []
    if db.conn.execute("SELECT 1 FROM trials WHERE created_ts>=? LIMIT 1", (start_ts,)).fetchone():
        reasons.append("trial_occurred_during_capture")
    if db.conn.execute(
        "SELECT 1 FROM calibration_runs WHERE start_ts>=? LIMIT 1",
        (start_ts,),
    ).fetchone():
        reasons.append("calibration_occurred_during_capture")
    if db.conn.execute(
        "SELECT 1 FROM investigations WHERE start_ts>=? LIMIT 1",
        (start_ts,),
    ).fetchone():
        reasons.append("investigation_occurred_during_capture")
    if db.conn.execute(
        "SELECT 1 FROM unexpected_power_events WHERE start_ts>=? LIMIT 1",
        (start_ts,),
    ).fetchone():
        reasons.append("unexpected_power_event_occurred_during_capture")
    return reasons


def _control_safety_activity(db: Database, start_ts: float) -> dict[str, list[dict[str, Any]]]:
    invalid: list[dict[str, Any]] = []
    thermal: list[dict[str, Any]] = []
    for row in db.runtime_states_since("control", start_ts):
        state = str(row.get("state") or "")
        reason = str(row.get("reason") or "")
        if state == "CONTROL_ALLOWED":
            continue
        if state == "EMERGENCY" and reason == "thermal emergency":
            thermal.append(row)
            continue
        invalid.append(row)
    return {"invalid": invalid, "thermal": thermal}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.count is not None and args.count <= 0:
        raise SystemExit("--count must be > 0")

    root = _default_root()
    config_path = Path(args.config).expanduser() if args.config else None
    config = load_config(root, config_path)
    interval = float(config.get("net_benefit.sample_seconds", 60.0))
    minimum_samples = int(config.get("net_benefit.minimum_samples_per_block", 6))
    if interval <= 0 or minimum_samples < 2:
        raise SystemExit("invalid local Net Benefit cadence contract")
    db = Database(config.path("storage.database"))
    sys_root = Path("/sys")
    proc_root = Path("/proc")
    meter = MinimalMeter(sys_root)
    context_sampler = _MinimalContextSampler(
        sys_root=sys_root,
        proc_root=proc_root,
        thermal_path=thermal_sensor_path(sys_root),
    )
    actuator, actuator_available, actuator_mode = build_actuator(config)
    if not actuator_available:
        db.close()
        if actuator_mode == "root-helper-mismatch":
            raise SystemExit(
                "root helper implementation does not match current runtime; "
                "rerun scripts/install-root-helper.sh"
            )
        raise SystemExit("MinimalMeter Net Benefit capture requires readable actual HWP state")
    start_context = _capture_context(root, config, db, mode=args.mode)
    if (
        args.mode == "DYNAMIC_CONTROLLER"
        and str((start_context.get("service_mode") or {}).get("mode") or "") != "DYNAMIC_CONTROLLER"
    ):
        db.close()
        raise SystemExit("DYNAMIC_CONTROLLER capture must start outside a thermal intervention")
    _prepare_campaign(
        db,
        campaign_id=args.campaign,
        mode=args.mode,
        context=start_context,
        max_campaign_span_seconds=float(
            config.get("net_benefit.max_campaign_span_seconds", 86400.0)
        ),
    )
    fixed_envelope = db.envelope(str(start_context["envelope"]))
    if not fixed_envelope:
        db.close()
        raise SystemExit("capture baseline envelope disappeared before start")
    start_snapshot = actuator.snapshot()
    if args.mode in {"FIXED_GOOD", "MONITORING"} and not snapshot_matches_envelope(
        start_snapshot,
        fixed_envelope,
    ):
        db.close()
        raise SystemExit("fixed-mode capture actual HWP state does not match the VERIFIED envelope")
    capture_start_ts = time.time()
    run_id = db.start_minimal_meter_run(
        capture_mode=args.mode,
        campaign_id=args.campaign,
        evidence_epoch_id=start_context["evidence_epoch_id"],
        battery_epoch=start_context["battery_epoch"],
        battery_identity_hash=start_context["battery_identity_hash"],
        hard_identity_hash=start_context["hard_identity_hash"],
        calibration_version=start_context["calibration_version"],
        evidence_semantics_version=start_context["evidence_semantics_version"],
        envelope=start_context["envelope"],
        envelope_content_hash=start_context["envelope_content_hash"],
        runtime_policy_fingerprint=start_context["runtime_policy_fingerprint"],
        payload={
            "capture_contract_version": 4,
            "interval_seconds": interval,
            "actuator_mode": actuator_mode,
            "service_mode": start_context["service_mode"],
            "runtime_policy": start_context["runtime_policy"],
            "stage_e_contract_identity": start_context["stage_e_contract_identity"],
            "stage_e_contract": start_context["stage_e_contract"],
            "media_compatibility_generation": start_context["media_compatibility_generation"],
            "start_hwp_snapshot": start_snapshot,
        },
    )
    count = 0
    terminal_status = "COMPLETE"
    terminal_reason: str | None = None
    final_context: dict[str, Any] | None = None
    try:
        while True:
            started = time.monotonic()
            try:
                service_mode = _service_mode_status(config, db, args.mode)
            except RuntimeError as exc:
                terminal_status = "INVALID"
                terminal_reason = f"capture_mode_invalid:{exc}"
                break
            sample = meter.sample()
            sample.update(context_sampler.sample(float(sample["ts"])))
            sample["service_mode"] = service_mode
            hwp_snapshot = actuator.snapshot()
            fixed_match = snapshot_matches_envelope(hwp_snapshot, fixed_envelope)
            sample["hwp_matches_fixed_envelope"] = fixed_match
            sample["hwp_snapshot"] = hwp_snapshot
            db.add_minimal_meter_sample(run_id, sample)
            print(
                json.dumps(
                    {"run_id": run_id, **sample},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            count += 1
            if args.mode in {"FIXED_GOOD", "MONITORING"} and not fixed_match:
                terminal_status = "INVALID"
                terminal_reason = "fixed_capture_hwp_changed"
                break
            if args.count is not None and count >= args.count:
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, interval - elapsed))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        terminal_status = "INVALID"
        terminal_reason = f"capture_failed:{exc}"
        raise
    finally:
        try:
            final_context = _capture_context(root, config, db, mode=args.mode)
            context_reason = _capture_context_change_reason(
                start_context,
                final_context,
                mode=args.mode,
            )
            if context_reason:
                terminal_status = "INVALID"
                terminal_reason = context_reason
        except Exception as exc:
            terminal_status = "INVALID"
            terminal_reason = f"capture_context_invalid:{exc}"
        if count < minimum_samples:
            terminal_status = "INVALID"
            terminal_reason = terminal_reason or "insufficient_samples"
        runtime_activity = _intervening_runtime_activity(db, capture_start_ts)
        if runtime_activity:
            terminal_status = "INVALID"
            terminal_reason = terminal_reason or ",".join(runtime_activity)
        control_safety = _control_safety_activity(db, capture_start_ts)
        if args.mode == "DYNAMIC_CONTROLLER" and control_safety["invalid"]:
            terminal_status = "INVALID"
            terminal_reason = terminal_reason or "control_safety_interrupted_dynamic_capture"
        db.finish_minimal_meter_run(
            run_id,
            {
                "capture_contract_version": 4,
                "interval_seconds": interval,
                "sample_count": count,
                "terminal_reason": terminal_reason,
                "final_context": final_context,
                "runtime_activity": runtime_activity,
                "control_safety_activity": control_safety,
            },
            status=terminal_status,
        )
        db.close()
        print(
            json.dumps({"run_id": run_id, "status": terminal_status, "reason": terminal_reason}),
            file=sys.stderr,
            flush=True,
        )
    return 0 if terminal_status == "COMPLETE" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
