from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


DEFAULT_APP_GROUPS = {
    "browser": ["firefox*", "chrome*", "chromium*", "brave*", "vivaldi*", "microsoft-edge*"],
    "editor": ["code", "code-*", "codium", "zed", "nvim", "vim", "emacs*", "idea*", "pycharm*"],
    "office": ["libreoffice*", "soffice*", "onlyoffice*", "evince", "okular", "zathura"],
    "video_call": ["zoom*", "teams*", "slack*", "discord*", "webex*"],
    "remote": ["remmina*", "rustdesk*", "anydesk*", "parsec*", "xfreerdp*"],
    "media": ["mpv", "vlc", "totem", "celluloid", "spotify*"],
}


@dataclass
class ContextResult:
    scene: str
    confidence: float
    features: dict[str, Any]
    rule_version: str = "v1"

    @property
    def context_id(self) -> str:
        identity = {
            "scene": self.scene,
            "foreground_group": self.features.get("foreground_group"),
            "background_classes": self.features.get("background_classes"),
            "media": self.features.get("media_playing"),
            "active": self.features.get("user_active"),
        }
        raw = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        return f"ctx-{hashlib.sha256(raw).hexdigest()[:16]}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "scene": self.scene,
            "confidence": self.confidence,
            "features": self.features,
            "rule_version": self.rule_version,
        }


class ContextEngine:
    def __init__(self, rules_path: Path | None = None):
        self.groups = {key: list(value) for key, value in DEFAULT_APP_GROUPS.items()}
        self.rule_version = "v1"
        if rules_path and rules_path.exists():
            with rules_path.open("rb") as fh:
                data = tomllib.load(fh)
            groups = data.get("groups", {})
            if isinstance(groups, dict):
                for key, value in groups.items():
                    if isinstance(value, list):
                        self.groups[key] = [str(v) for v in value]
            meta = data.get("meta", {})
            if isinstance(meta, dict) and meta.get("version"):
                self.rule_version = str(meta["version"])

    def app_group(self, app: str | None, executable: str | None = None) -> str:
        values = [str(app or "").lower(), str(executable or "").split("/")[-1].lower()]
        for group, patterns in self.groups.items():
            for pattern in patterns:
                if any(fnmatch.fnmatch(value, pattern.lower()) for value in values if value):
                    return group
        return "unknown"

    def infer(
        self,
        *,
        activity: dict[str, Any],
        process_summary: dict[str, Any],
        media: dict[str, Any],
        system: dict[str, Any],
    ) -> ContextResult:
        afk = activity.get("afk")
        user_active = afk is not True
        foreground_group = self.app_group(activity.get("app"), activity.get("executable"))
        classes = process_summary.get("class_cpu_percent") or {}
        compile_cpu = float(classes.get("compile", 0.0))
        language_cpu = float(classes.get("language_server", 0.0))
        total_cpu = float(system.get("cpu_usage") or 0.0)
        load1 = float(system.get("load1") or 0.0)
        network_mbps = float(system.get("network_mbps") or 0.0)
        media_playing = bool(media.get("playing"))
        app_version = activity.get("app_version")
        background_classes = [
            key for key, value in classes.items() if key != "other" and float(value or 0.0) >= 5.0
        ]

        features = {
            "foreground_app": activity.get("app"),
            "foreground_group": foreground_group,
            "foreground_app_major_version": (
                app_version.get("major")
                if isinstance(app_version, dict)
                else None
            ),
            "window_title": activity.get("title"),
            "user_active": user_active,
            "media_playing": media_playing,
            "background_classes": sorted(background_classes),
            "compile_cpu_percent": compile_cpu,
            "language_server_cpu_percent": language_cpu,
            "total_cpu_percent": total_cpu,
            "load1": load1,
            "network_mbps": network_mbps,
            "battery_status": system.get("battery_status"),
        }

        if afk is True:
            return ContextResult("idle", 0.98, features, self.rule_version)

        if foreground_group == "video_call":
            return ContextResult("video_call", 0.88, features, self.rule_version)

        if media_playing:
            if compile_cpu >= 20.0:
                return ContextResult("mixed", 0.80, features, self.rule_version)
            return ContextResult("media_playback", 0.92, features, self.rule_version)

        if compile_cpu >= 25.0:
            return ContextResult("compile", 0.90, features, self.rule_version)

        if foreground_group == "editor":
            return ContextResult("coding_interactive", 0.90, features, self.rule_version)

        if foreground_group == "browser":
            if total_cpu <= 18.0 and load1 <= 1.5:
                return ContextResult("reading", 0.70, features, self.rule_version)
            return ContextResult("web_interactive", 0.88, features, self.rule_version)

        if foreground_group == "office":
            if total_cpu <= 15.0:
                return ContextResult("reading", 0.70, features, self.rule_version)
            return ContextResult("office_interactive", 0.86, features, self.rule_version)

        if foreground_group == "remote":
            return ContextResult("remote_interactive", 0.86, features, self.rule_version)

        if foreground_group == "media":
            return ContextResult("media_playback", 0.78, features, self.rule_version)

        if network_mbps >= 20.0 and total_cpu < 50.0:
            return ContextResult("file_transfer", 0.72, features, self.rule_version)

        if total_cpu >= 45.0:
            return ContextResult("background_compute", 0.68, features, self.rule_version)

        if len(background_classes) >= 2:
            return ContextResult("mixed", 0.60, features, self.rule_version)

        return ContextResult("unknown", 0.35, features, self.rule_version)
