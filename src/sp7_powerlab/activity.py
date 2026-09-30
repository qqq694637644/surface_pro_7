from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class ActivityState:
    app: str | None = None
    title: str | None = None
    executable: str | None = None
    afk: bool | None = None
    source: str = "none"
    event_timestamp: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "title": self.title,
            "executable": self.executable,
            "afk": self.afk,
            "source": self.source,
            "event_timestamp": self.event_timestamp,
        }


class ActivityWatchClient:
    def __init__(self, server_url: str = "http://127.0.0.1:5600", timeout: float = 1.0):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def _json_get(self, path: str) -> Any:
        req = urllib.request.Request(
            self.server_url + path,
            headers={"Accept": "application/json", "User-Agent": "sp7-powerlab/1"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def available(self) -> bool:
        try:
            payload = self._json_get("/api/0/buckets/")
            return isinstance(payload, dict)
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False

    def buckets(self) -> dict[str, Any]:
        try:
            payload = self._json_get("/api/0/buckets/")
            return payload if isinstance(payload, dict) else {}
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return {}

    def _latest_event(self, bucket_id: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(bucket_id, safe="")
        try:
            payload = self._json_get(f"/api/0/buckets/{quoted}/events?limit=1")
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return None
        if isinstance(payload, list) and payload:
            event = payload[0]
            return event if isinstance(event, dict) else None
        return None

    @staticmethod
    def _pick_bucket(buckets: dict[str, Any], kinds: tuple[str, ...]) -> str | None:
        scored: list[tuple[int, str]] = []
        for bucket_id, meta in buckets.items():
            text = " ".join(
                [
                    bucket_id.lower(),
                    str((meta or {}).get("type", "")).lower() if isinstance(meta, dict) else "",
                    str((meta or {}).get("client", "")).lower() if isinstance(meta, dict) else "",
                ]
            )
            score = max((len(kinds) - i for i, kind in enumerate(kinds) if kind in text), default=0)
            if score:
                scored.append((score, bucket_id))
        scored.sort(reverse=True)
        return scored[0][1] if scored else None

    def current(self) -> ActivityState:
        buckets = self.buckets()
        if not buckets:
            return ActivityState(source="activitywatch-unavailable")

        window_bucket = self._pick_bucket(
            buckets,
            ("awatcher", "window", "currentwindow", "toplevel"),
        )
        afk_bucket = self._pick_bucket(buckets, ("afk", "idle"))

        state = ActivityState(source="activitywatch")
        if window_bucket:
            event = self._latest_event(window_bucket)
            if event:
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                state.app = str(data.get("app") or data.get("application") or data.get("class") or "") or None
                state.title = str(data.get("title") or data.get("window_title") or "") or None
                state.executable = str(data.get("executable") or data.get("exe") or "") or None
                state.event_timestamp = str(event.get("timestamp") or "") or None
                if "afk" in data:
                    state.afk = bool(data.get("afk"))

        if afk_bucket:
            event = self._latest_event(afk_bucket)
            if event:
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                status = str(data.get("status") or data.get("state") or "").lower()
                if status:
                    state.afk = status in {"afk", "idle", "true", "1"}
                elif "afk" in data:
                    state.afk = bool(data.get("afk"))
        return state


def mpris_state() -> dict[str, Any]:
    if not shutil.which("playerctl"):
        return {"available": False, "playing": False, "players": []}
    try:
        players = subprocess.run(
            ["playerctl", "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "playing": False, "players": []}

    result = []
    playing = False
    for player in players[:8]:
        player = player.strip()
        if not player:
            continue
        try:
            proc = subprocess.run(
                ["playerctl", "-p", player, "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            status = proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            status = ""
        result.append({"player": player, "status": status})
        playing = playing or status.lower() == "playing"
    return {"available": True, "playing": playing, "players": result}
