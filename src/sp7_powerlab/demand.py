from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

REMOTE_PROCESS_HINTS = (
    "ssh",
    "mosh",
    "remmina",
    "xfreerdp",
    "code-tunnel",
    "code-server",
    "vscode-server",
)


def _level(value: float, low: float, high: float) -> str:
    if value >= high:
        return "HIGH"
    if value >= low:
        return "MEDIUM"
    return "LOW"


@dataclass
class DemandObserver:
    cpu_count: int = max(1, os.cpu_count() or 1)
    machine: dict[str, Any] | None = None

    def observe(self, sample: dict[str, Any]) -> dict[str, Any]:
        active = bool(sample.get("user_active", True))
        cpu = float(sample.get("cpu_usage") or 0.0)
        cpu_psi = float(sample.get("cpu_psi") or 0.0)
        io_psi = float(sample.get("io_psi") or 0.0)
        load1 = float(sample.get("load1") or 0.0)
        rapl = float(sample.get("rapl_power_60s_w") or 0.0)
        rx = float(sample.get("network_rx_mbps") or 0.0)
        tx = float(sample.get("network_tx_mbps") or 0.0)
        network = max(rx, tx)

        if not active:
            latency = "LOW"
        elif cpu_psi >= 5.0 or io_psi >= 5.0 or load1 >= self.cpu_count:
            latency = "HIGH"
        elif cpu >= 20.0 or load1 >= max(1.0, self.cpu_count * 0.35):
            latency = "HIGH"
        else:
            latency = "MEDIUM"

        machine = self.machine or {}
        baselines = machine.get("baselines") or {}
        thermal = machine.get("thermal") or {}
        rapl_reference = max(
            4.0,
            float(thermal.get("rapl_reference_w") or 0.0),
            float(baselines.get("normal_rapl_p90_w") or 0.0) * 1.5,
        )
        if rapl_reference <= 4.0:
            rapl_reference = 12.0
        compute_score = max(
            cpu / 100.0,
            min(1.0, cpu_psi / 20.0),
            min(1.0, load1 / max(1.0, self.cpu_count)),
            min(1.0, rapl / rapl_reference),
        )
        if compute_score >= 0.65:
            compute = "SUSTAINED"
        elif compute_score >= 0.25:
            compute = "MODERATE"
        else:
            compute = "LOW"

        if network >= 10.0:
            network_intensity = "TRANSFER"
        elif network >= 0.20:
            network_intensity = "INTERACTIVE"
        else:
            network_intensity = "LOW"

        process_names = " ".join(
            str(row.get("name") or "").lower() for row in sample.get("processes") or []
        )
        explicit_remote = any(token in process_names for token in REMOTE_PROCESS_HINTS)
        remote_hint = 0.0
        if explicit_remote:
            remote_hint += 0.55
        if active and network_intensity == "INTERACTIVE" and compute == "LOW":
            remote_hint += 0.30
        if active and network_intensity == "TRANSFER" and compute != "SUSTAINED":
            remote_hint += 0.10
        remote_hint = min(1.0, remote_hint)

        media = 1.0 if sample.get("media_playing") else 0.0
        io_pressure = _level(io_psi, 1.0, 5.0)

        region = "|".join(
            (
                "ACTIVE" if active else "IDLE",
                f"LAT_{latency}",
                f"CPU_{compute}",
                "MEDIA" if media else "NO_MEDIA",
                f"NET_{network_intensity}",
                "REMOTE" if remote_hint >= 0.5 else "LOCAL",
            )
        )

        return {
            "ts": float(sample["ts"]),
            "region": region,
            "user_active": active,
            "latency_need": latency,
            "local_compute_pressure": compute,
            "media_continuity": media,
            "remote_hint": remote_hint,
            "network_intensity": network_intensity,
            "io_pressure": io_pressure,
            "features": {
                "cpu_usage": cpu,
                "cpu_psi": cpu_psi,
                "io_psi": io_psi,
                "load1": load1,
                "rapl_60s_w": rapl,
                "rapl_reference_w": rapl_reference,
                "network_mbps": network,
            },
        }
