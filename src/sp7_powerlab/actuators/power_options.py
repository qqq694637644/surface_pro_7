from __future__ import annotations

import ast
import shutil
import subprocess
from typing import Any

from .base import ActuatorError, ProfileActuator


class PowerOptionsActuator(ProfileActuator):
    name = "power-options"

    def __init__(self, binary: str = "power-daemon-mgr"):
        self.binary = binary

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, args: list[str], timeout: float = 6.0) -> subprocess.CompletedProcess[str]:
        if not self.available():
            raise ActuatorError(f"{self.binary} not found")
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActuatorError(str(exc)) from exc
        if proc.returncode != 0:
            raise ActuatorError((proc.stderr or proc.stdout or "power-options command failed").strip())
        return proc

    def inspect(self) -> dict[str, Any]:
        if not self.available():
            return {"backend": self.name, "available": False, "profiles": []}
        try:
            output = self._run(["list-profiles"]).stdout.strip()
            profiles: list[str] = []
            if output:
                try:
                    parsed = ast.literal_eval(output)
                    if isinstance(parsed, list):
                        profiles = [str(x) for x in parsed]
                except (ValueError, SyntaxError):
                    profiles = [x.strip(" \t\"'") for x in output.strip("[]").split(",") if x.strip()]
            return {
                "backend": self.name,
                "available": True,
                "profiles": profiles,
                "current_override": None,
                "note": "power-daemon-mgr does not expose the current temporary override in its CLI; PowerLab validates low-risk effects separately.",
            }
        except ActuatorError as exc:
            return {"backend": self.name, "available": True, "error": str(exc), "profiles": []}

    def apply_profile(self, backend_profile: str) -> dict[str, Any]:
        before = self.inspect()
        self._run(["set-profile-override", backend_profile])
        after = self.inspect()
        if after.get("profiles") and backend_profile not in after["profiles"]:
            try:
                self._run(["reset-profile-override"])
            except ActuatorError:
                pass
            raise ActuatorError(f"Power Options profile not present after apply: {backend_profile}")
        return {"backend": self.name, "profile": backend_profile, "before": before, "after": after}

    def reset_override(self) -> dict[str, Any]:
        before = self.inspect()
        self._run(["reset-profile-override"])
        return {"backend": self.name, "before": before, "after": self.inspect()}
