from __future__ import annotations

import shutil
import subprocess
from typing import Any

from .base import ActuatorError, ProfileActuator


class PowerProfilesDaemonActuator(ProfileActuator):
    name = "power-profiles-daemon"

    def __init__(self, binary: str = "powerprofilesctl"):
        self.binary = binary

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, args: list[str]) -> str:
        if not self.available():
            raise ActuatorError(f"{self.binary} not found")
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActuatorError(str(exc)) from exc
        if proc.returncode != 0:
            raise ActuatorError((proc.stderr or proc.stdout or "powerprofilesctl failed").strip())
        return proc.stdout.strip()

    def inspect(self) -> dict[str, Any]:
        if not self.available():
            return {"backend": self.name, "available": False}
        current = None
        profiles: list[str] = []
        try:
            current = self._run(["get"])
        except ActuatorError:
            pass
        try:
            listing = self._run(["list"])
            for line in listing.splitlines():
                clean = line.strip().lstrip("*").strip()
                if clean.endswith(":"):
                    profiles.append(clean[:-1])
                elif clean in {"power-saver", "balanced", "performance"}:
                    profiles.append(clean)
        except ActuatorError:
            pass
        return {
            "backend": self.name,
            "available": True,
            "current_profile": current,
            "profiles": sorted(set(profiles)),
        }

    def apply_profile(self, backend_profile: str) -> dict[str, Any]:
        before = self.inspect()
        self._run(["set", backend_profile])
        after = self.inspect()
        current = after.get("current_profile")
        if current and current != backend_profile:
            raise ActuatorError(f"Read-back mismatch: wanted {backend_profile}, got {current}")
        return {"backend": self.name, "profile": backend_profile, "before": before, "after": after}
