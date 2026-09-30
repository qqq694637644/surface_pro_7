from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class AppVersionCache:
    def __init__(self):
        self.cache: dict[str, dict[str, Any] | None] = {}

    @staticmethod
    def _candidate(app: str | None, executable: str | None) -> str | None:
        if executable:
            path = Path(executable)
            if path.is_file():
                return str(path)
        if app and re.fullmatch(r"[A-Za-z0-9._+-]+", app):
            return shutil.which(app)
        return None

    def get(self, app: str | None, executable: str | None = None) -> dict[str, Any] | None:
        candidate = self._candidate(app, executable)
        key = candidate or str(app or "")
        if not key:
            return None
        if key in self.cache:
            return self.cache[key]
        if not candidate:
            self.cache[key] = None
            return None
        try:
            proc = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            output = (proc.stdout or proc.stderr).strip().splitlines()
        except (OSError, subprocess.TimeoutExpired):
            self.cache[key] = None
            return None
        if not output:
            self.cache[key] = None
            return None
        raw = output[0][:300]
        match = re.search(r"(?<!\d)(\d+)(?:\.\d+)*", raw)
        result = {
            "executable": candidate,
            "raw": raw,
            "major": int(match.group(1)) if match else None,
        }
        self.cache[key] = result
        return result
