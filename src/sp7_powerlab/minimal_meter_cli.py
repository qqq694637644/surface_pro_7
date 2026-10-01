from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import load_config, load_machine
from .measurement import MinimalMeter, measurement_trust_matches_epoch
from .storage import Database

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
    p.add_argument("--config")
    p.add_argument("--campaign", required=True)
    p.add_argument("--mode", required=True, choices=CAPTURE_MODES)
    return p


def _capture_context(root: Path, config: Any, db: Database) -> dict[str, Any]:
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

    return {
        "evidence_epoch_id": str(epoch["epoch_id"]),
        "battery_epoch": int(battery["epoch"]),
        "battery_identity_hash": str(battery["identity_hash"]),
        "hard_identity_hash": str(epoch["hard_identity_hash"]),
        "calibration_version": int(epoch["calibration_version"]),
        "evidence_semantics_version": int(epoch["evidence_semantics_version"]),
        "envelope": envelope,
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
    )
    if any(final[field] != start[field] for field in stable_context_fields):
        return "capture_context_changed"
    if mode in {"FIXED_GOOD", "MONITORING"} and final["envelope"] != start["envelope"]:
        return "fixed_capture_envelope_changed"
    return None


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
    meter = MinimalMeter(Path(args.sys_root))
    start_context = _capture_context(root, config, db)
    run_id = db.start_minimal_meter_run(
        capture_mode=args.mode,
        campaign_id=args.campaign,
        **start_context,
        payload={
            "capture_contract_version": 1,
            "interval_seconds": float(args.interval),
        },
    )
    count = 0
    terminal_status = "COMPLETE"
    terminal_reason: str | None = None
    final_context: dict[str, Any] | None = None
    try:
        while True:
            started = time.monotonic()
            sample = meter.sample()
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
            final_context = _capture_context(root, config, db)
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
        db.finish_minimal_meter_run(
            run_id,
            {
                "capture_contract_version": 1,
                "interval_seconds": float(args.interval),
                "sample_count": count,
                "terminal_reason": terminal_reason,
                "final_context": final_context,
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
