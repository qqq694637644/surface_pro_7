from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Database

CLASSIFICATIONS = {
    "EXPECTED_WORKLOAD_CHANGE",
    "INSUFFICIENT_EVIDENCE",
    "SUSPECTED_REGRESSION",
    "ACTIONABLE_WASTE",
    "CONFIRMED_CONFIG_REGRESSION",
}


@dataclass
class AttributionEngine:
    db: Database

    def attribute(self, investigation_id: str) -> dict[str, Any]:
        investigation = self.db.investigation(investigation_id)
        if not investigation:
            raise KeyError(investigation_id)

        event_id = investigation.get("event_id")
        event = self.db.unexpected_power_event(str(event_id)) if event_id else None
        if not event:
            return {
                "classification": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.0,
                "local_evidence": ["unexpected-power event is unavailable"],
                "verification_plan": ["inspect recent telemetry and logs manually"],
            }

        evidence: list[str] = []
        verification: list[str] = []
        classification = "INSUFFICIENT_EVIDENCE"
        confidence = 0.35

        top_processes = event.get("top_processes") or []
        hot_processes = [
            row
            for row in top_processes
            if isinstance(row.get("cpu_percent"), (int, float))
            and float(row["cpu_percent"]) >= 15.0
        ]
        rapl = event.get("avg_rapl_w")
        network = event.get("avg_network_mbps")
        media_decode = event.get("media_decode_hint")
        drift = event.get("classification") == "SUSTAINED_DRIFT"

        if hot_processes:
            names = ", ".join(
                str(row.get("name") or row.get("executable") or row.get("pid"))
                for row in hot_processes[:5]
            )
            evidence.append(f"high local CPU attribution: {names}")
            classification = "SUSPECTED_REGRESSION"
            confidence = 0.65
            verification.append("repeat the window with the suspected process quiesced or stopped")

        if isinstance(rapl, (int, float)) and float(rapl) >= 2.5:
            evidence.append(f"CPU package power is elevated ({float(rapl):.2f} W)")
            classification = "SUSPECTED_REGRESSION"
            confidence = max(confidence, 0.60)
            verification.append(
                "compare package RAPL against the frozen reference under the same strata"
            )

        if isinstance(network, (int, float)) and float(network) >= 20.0:
            evidence.append(f"network throughput is high ({float(network):.1f} Mbps)")
            verification.append("confirm whether a user-requested transfer explains the event")

        if media_decode:
            evidence.append("media decode telemetry changed during the event")
            classification = "SUSPECTED_REGRESSION"
            confidence = max(confidence, 0.65)
            verification.append(
                "verify hardware video decode and compare browser/media backend versions"
            )

        if drift:
            evidence.append("recent distribution drifted above the frozen reference")
            classification = "SUSPECTED_REGRESSION"
            confidence = max(confidence, 0.55)
            verification.append("compare 24h/7d/30d distributions and compatibility-tag changes")

        if not evidence:
            evidence.append(
                "whole-device battery power is elevated but deterministic attribution is weak"
            )
            verification.extend(
                [
                    "collect a diagnostic burst",
                    "inspect process/GPU/runtime-PM/wakeup/network attribution",
                    "compare against the frozen reference and recent compatibility changes",
                ]
            )

        return {
            "classification": classification,
            "confidence": confidence,
            "event_id": event_id,
            "local_evidence": evidence,
            "verification_plan": list(dict.fromkeys(verification)),
            "event": event,
        }

    def validate_classification(self, classification: str) -> str:
        value = classification.upper()
        if value not in CLASSIFICATIONS:
            raise ValueError("classification must be one of: " + ", ".join(sorted(CLASSIFICATIONS)))
        return value
