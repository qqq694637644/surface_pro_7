from __future__ import annotations

import hashlib
from typing import Any

from .quality import data_quality


class SessionTracker:
    def __init__(self, db, max_gap_seconds: float = 45.0):
        self.db = db
        self.max_gap_seconds = max_gap_seconds
        self.current_context_id: str | None = None
        self.current_scene: str | None = None
        self.current_profile_id: str | None = None
        self.samples: list[dict[str, Any]] = []

    def _session_id(self, start_ts: float, context_id: str | None) -> str:
        raw = f"{start_ts:.6f}:{context_id or 'none'}".encode()
        return f"s-{hashlib.sha256(raw).hexdigest()[:18]}"

    def add(self, sample: dict[str, Any], context: dict[str, Any]) -> None:
        context_id = context["context_id"]
        scene = context["scene"]
        profile_id = sample.get("profile_id")
        if self.samples:
            gap = float(sample["ts"]) - float(self.samples[-1]["ts"])
            if gap > self.max_gap_seconds:
                self.flush(extra_reason="collector_gap")
        if self.samples and context_id != self.current_context_id:
            self.flush(extra_reason="context_changed")
        if self.samples and profile_id != self.current_profile_id:
            self.flush(extra_reason="profile_changed")

        if not self.samples:
            self.current_context_id = context_id
            self.current_scene = scene
            self.current_profile_id = profile_id
        self.samples.append(sample)

    def flush(self, extra_reason: str | None = None) -> dict[str, Any] | None:
        if len(self.samples) < 2:
            self.samples.clear()
            self.current_context_id = None
            self.current_scene = None
            self.current_profile_id = None
            return None
        quality = data_quality(
            self.samples,
            max_gap_seconds=self.max_gap_seconds,
            min_samples=2,
            min_valid_seconds=0.0,
            require_single_scene=False,
        )
        if extra_reason:
            quality.setdefault("notes", []).append(extra_reason)
        start_ts = float(self.samples[0]["ts"])
        end_ts = float(self.samples[-1]["ts"])
        session = {
            "session_id": self._session_id(start_ts, self.current_context_id),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "context_id": self.current_context_id,
            "scene": self.current_scene or "unknown",
            "profile_id": self.current_profile_id,
            "valid_duration_s": quality["valid_duration_s"],
            "energy_wh": quality["energy_wh"],
            "avg_power_w": quality["average_power_w"],
            "sample_count": len(self.samples),
            "quality": quality,
        }
        self.db.add_session(session)
        self.samples.clear()
        self.current_context_id = None
        self.current_scene = None
        self.current_profile_id = None
        return session
