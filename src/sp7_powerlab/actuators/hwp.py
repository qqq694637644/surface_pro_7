from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ActuatorError

VALID_EPP = {"performance", "balance_performance", "balance_power", "power"}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _write(path: Path, value: str | int) -> None:
    try:
        path.write_text(str(value), encoding="utf-8")
    except OSError as exc:
        raise ActuatorError(f"failed writing {path}: {exc}") from exc


class HWPActuator:
    def __init__(self, sys_root: Path = Path("/sys")):
        self.sys_root = sys_root

    @property
    def pstate_root(self) -> Path:
        return self.sys_root / "devices" / "system" / "cpu" / "intel_pstate"

    def epp_paths(self) -> list[Path]:
        root = self.sys_root / "devices" / "system" / "cpu" / "cpufreq"
        return sorted(root.glob("policy*/energy_performance_preference"))

    def available(self) -> bool:
        return (
            (self.pstate_root / "max_perf_pct").exists()
            and (self.pstate_root / "no_turbo").exists()
            and bool(self.epp_paths())
        )

    def inspect(self) -> dict[str, Any]:
        max_perf = _read(self.pstate_root / "max_perf_pct")
        no_turbo = _read(self.pstate_root / "no_turbo")
        epp = {str(path): _read(path) for path in self.epp_paths()}
        return {
            "available": self.available(),
            "epp": epp,
            "max_perf_pct": int(max_perf) if max_perf and max_perf.isdigit() else None,
            "turbo": None if no_turbo not in {"0", "1"} else no_turbo == "0",
        }

    def snapshot(self) -> dict[str, Any]:
        state = self.inspect()
        if not state["available"]:
            raise ActuatorError("intel_pstate/HWP control surface is unavailable")
        return state

    @staticmethod
    def validate_values(epp: str, max_perf_pct: int, turbo: bool) -> None:
        if epp not in VALID_EPP:
            raise ActuatorError(f"invalid EPP value: {epp}")
        if not 10 <= int(max_perf_pct) <= 100:
            raise ActuatorError(f"max_perf_pct out of range: {max_perf_pct}")
        if not isinstance(turbo, bool):
            raise ActuatorError("turbo must be boolean")

    def apply_values(
        self,
        *,
        epp: str,
        max_perf_pct: int,
        turbo: bool,
    ) -> dict[str, Any]:
        self.validate_values(epp, max_perf_pct, turbo)
        before = self.snapshot()
        _write(self.pstate_root / "max_perf_pct", int(max_perf_pct))
        for path in self.epp_paths():
            _write(path, epp)
        no_turbo_path = self.pstate_root / "no_turbo"
        if not no_turbo_path.exists():
            raise ActuatorError("intel_pstate turbo control is unavailable")
        _write(no_turbo_path, 0 if turbo else 1)
        after = self.inspect()

        if after.get("max_perf_pct") != int(max_perf_pct):
            raise ActuatorError("max_perf_pct read-back mismatch")
        if any(value != epp for value in (after.get("epp") or {}).values()):
            raise ActuatorError("EPP read-back mismatch")
        if after.get("turbo") is not None and after.get("turbo") != turbo:
            raise ActuatorError("turbo read-back mismatch")
        return {"before": before, "after": after}

    def apply_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.apply_values(
            epp=str(envelope["epp"]),
            max_perf_pct=int(envelope["max_perf_pct"]),
            turbo=bool(envelope["turbo"]),
        )

    def restore(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        allowed_paths = {str(path): path for path in self.epp_paths()}
        snapshot_epp = snapshot.get("epp") or {}
        unexpected = set(snapshot_epp) - set(allowed_paths)
        if unexpected:
            raise ActuatorError(f"snapshot contains unauthorized EPP paths: {sorted(unexpected)}")

        max_perf = snapshot.get("max_perf_pct")
        turbo = snapshot.get("turbo")
        if not isinstance(max_perf, int) or not 10 <= max_perf <= 100:
            raise ActuatorError("snapshot max_perf_pct is invalid")
        if turbo is not None and not isinstance(turbo, bool):
            raise ActuatorError("snapshot turbo state is invalid")

        before = self.snapshot()
        _write(self.pstate_root / "max_perf_pct", max_perf)
        for raw_path, value in snapshot_epp.items():
            if value not in VALID_EPP:
                raise ActuatorError(f"snapshot contains invalid EPP value: {value}")
            _write(allowed_paths[raw_path], value)
        no_turbo_path = self.pstate_root / "no_turbo"
        if turbo is not None and no_turbo_path.exists():
            _write(no_turbo_path, 0 if turbo else 1)
        return {"before": before, "after": self.inspect()}
