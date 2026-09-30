from __future__ import annotations

import subprocess
import time
import uuid
from typing import Any

from .quality import data_quality


def run_measured_task(
    db,
    *,
    scene: str,
    label: str,
    command: list[str],
    max_gap_seconds: float = 45.0,
    profile_id: str | None = None,
    trial_id: str | None = None,
) -> dict[str, Any]:
    if not command:
        raise ValueError("command is required")
    start = time.time()
    proc = subprocess.run(command, check=False)
    end = time.time()
    samples = db.samples_between(start, end, scene=scene)
    quality = data_quality(
        samples,
        max_gap_seconds=max_gap_seconds,
        min_samples=3,
        min_valid_seconds=max(0.0, (end - start) * 0.6),
        require_single_scene=True,
    )
    run = {
        "task_run_id": f"job-{uuid.uuid4().hex[:16]}",
        "scene": scene,
        "label": label,
        "start_ts": start,
        "end_ts": end,
        "duration_s": end - start,
        "energy_wh": quality.get("energy_wh"),
        "avg_power_w": quality.get("average_power_w"),
        "profile_id": profile_id,
        "trial_id": trial_id,
        "exit_code": proc.returncode,
        "quality": quality,
        "metadata": {"command": command},
    }
    db.add_task_run(run)
    return run
