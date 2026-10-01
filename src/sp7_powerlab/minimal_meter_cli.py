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
from .hardware import thermal_sensor_path
from .longterm import runtime_policy_snapshot
from .measurement import MinimalMeter, measurement_trust_matches_epoch
from .service import build_actuator
from .storage import Database
from .telemetry import _brightness, _media_playing, _network_bytes, _temperature_c, _user_active

CAPTURE_MODES = ("FIXED_GOOD", "MONITORING", "DYNAMIC_CONTROLLER", "FULL_POWERLAB")


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
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--count", type=int)
    p.add_argument("--sys-root", default="/sys")
    p.add_argument("--proc-root", default="/proc")
    p.add_argument("--config")
    p.add_argument("--campaign", required=True)
    p.add_argument("--mode", required=True, choices=CAPTURE_MODES)
    return p


def _service_mode_status(config: Any, db: Database, mode: str) -> dict[str, Any]:
    heartbeat = db.get_meta("service_heartbeat", {})
    heartbeat_ts = float(heartbeat.get("ts") or 0.0) if isinstance(heartbeat, dict) else 0.0
    heartbeat_limit = max(float(config.get("collector.sample_seconds", 10.0)) * 3.0, 30.0)
    heartbeat_fresh = heartbeat_ts > 0 and time.time() - heartbeat_ts <= heartbeat_limit
    configured_level = int(config.get("automation.level", 0))
    runtime_level = (
        int(heartbeat.get("automation_level", -1))
        if heartbeat_fresh and isinstance(heartbeat, dict)
        else None
    )
    control_state = str(heartbeat.get("control_state") or "") if heartbeat_fresh else ""
    if mode == "FIXED_GOOD":
        if heartbeat_fresh:
            raise RuntimeError("FIXED_GOOD capture requires the PowerLab service to be stopped")
    elif mode == "MONITORING":
        if not heartbeat_fresh or configured_level != 0 or runtime_level != 0:
            raise RuntimeError("MONITORING capture requires a live service with automation.level=0")
    elif mode == "DYNAMIC_CONTROLLER":
        if (
            not heartbeat_fresh
            or configured_level != 1
            or runtime_level != 1
            or control_state != "CONTROL_ALLOWED"
        ):
            raise RuntimeError(
                "DYNAMIC_CONTROLLER capture requires a live CONTROL_ALLOWED service at automation.level=1"
            )
    elif mode == "FULL_POWERLAB":
        if (
            not heartbeat_fresh
            or configured_level < 2
            or runtime_level is None
            or runtime_level < 2
            or control_state != "CONTROL_ALLOWED"
        ):
            raise RuntimeError(
                "FULL_POWERLAB capture requires a live CONTROL_ALLOWED service at automation.level>=2"
            )
    else:  # pragma: no cover - argparse prevents this
        raise RuntimeError(f"unknown capture mode: {mode}")
    return {
        "service_heartbeat_fresh": heartbeat_fresh,
        "service_heartbeat": heartbeat if isinstance(heartbeat, dict) else {},
        "configured_automation_level": configured_level,
        "runtime_automation_level": runtime_level,
        "control_state": control_state or None,
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
        "runtime_policy": policy["payload"],
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
            payload={"comparisons": {}},
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


def _intervening_learning_activity(db: Database, start_ts: float) -> list[str]:
    reasons: list[str] = []
    if db.conn.execute("SELECT 1 FROM trials WHERE created_ts>=? LIMIT 1", (start_ts,)).fetchone():
        reasons.append("trial_occurred_during_capture")
    if db.conn.execute(
        "SELECT 1 FROM calibration_runs WHERE start_ts>=? LIMIT 1",
        (start_ts,),
    ).fetchone():
        reasons.append("calibration_occurred_during_capture")
    return reasons


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.interval <= 0:
        raise SystemExit("--interval must be > 0")
    if args.count is not None and args.count <= 0:
        raise SystemExit("--count must be > 0")

    root = _default_root()
    config_path = Path(args.config).expanduser() if args.config else None
    config = load_config(root, config_path)
    db = Database(config.path("storage.database"))
    sys_root = Path(args.sys_root)
    proc_root = Path(args.proc_root)
    meter = MinimalMeter(sys_root)
    context_sampler = _MinimalContextSampler(
        sys_root=sys_root,
        proc_root=proc_root,
        thermal_path=thermal_sensor_path(sys_root),
    )
    actuator, actuator_available, actuator_mode = build_actuator(config)
    if not actuator_available:
        db.close()
        raise SystemExit("MinimalMeter Net Benefit capture requires readable actual HWP state")
    start_context = _capture_context(root, config, db, mode=args.mode)
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
            "capture_contract_version": 2,
            "interval_seconds": float(args.interval),
            "actuator_mode": actuator_mode,
            "service_mode": start_context["service_mode"],
            "runtime_policy": start_context["runtime_policy"],
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
            time.sleep(max(0.0, args.interval - elapsed))
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
        if count < 2:
            terminal_status = "INVALID"
            terminal_reason = terminal_reason or "insufficient_samples"
        learning_activity = _intervening_learning_activity(db, capture_start_ts)
        if learning_activity:
            terminal_status = "INVALID"
            terminal_reason = terminal_reason or ",".join(learning_activity)
        db.finish_minimal_meter_run(
            run_id,
            {
                "capture_contract_version": 2,
                "interval_seconds": float(args.interval),
                "sample_count": count,
                "terminal_reason": terminal_reason,
                "final_context": final_context,
                "learning_activity": learning_activity,
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
