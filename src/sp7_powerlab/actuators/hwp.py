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

    @staticmethod
    def _same_state(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (
            left.get("max_perf_pct") == right.get("max_perf_pct")
            and left.get("turbo") == right.get("turbo")
            and (left.get("epp") or {}) == (right.get("epp") or {})
        )

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        allowed_paths = {str(path): path for path in self.epp_paths()}
        snapshot_epp = snapshot.get("epp") or {}
        if set(snapshot_epp) != set(allowed_paths):
            raise ActuatorError("snapshot EPP policy set no longer matches the live HWP policy set")
        max_perf = snapshot.get("max_perf_pct")
        turbo = snapshot.get("turbo")
        if not isinstance(max_perf, int) or not 10 <= max_perf <= 100:
            raise ActuatorError("snapshot max_perf_pct is invalid")
        if not isinstance(turbo, bool):
            raise ActuatorError("snapshot turbo state is invalid")
        for value in snapshot_epp.values():
            if value not in VALID_EPP:
                raise ActuatorError(f"snapshot contains invalid EPP value: {value}")

        _write(self.pstate_root / "max_perf_pct", max_perf)
        for raw_path, value in snapshot_epp.items():
            _write(allowed_paths[raw_path], value)
        no_turbo_path = self.pstate_root / "no_turbo"
        if not no_turbo_path.exists():
            raise ActuatorError("intel_pstate turbo control is unavailable")
        _write(no_turbo_path, 0 if turbo else 1)

    def _restore_exact(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self._write_snapshot(snapshot)
        after = self.snapshot()
        if not self._same_state(after, snapshot):
            raise ActuatorError("exact HWP rollback read-back mismatch")
        return after

    def apply_values(
        self,
        *,
        epp: str,
        max_perf_pct: int,
        turbo: bool,
    ) -> dict[str, Any]:
        self.validate_values(epp, max_perf_pct, turbo)
        before = self.snapshot()
        try:
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
        except Exception as exc:
            try:
                self._restore_exact(before)
            except Exception as rollback_exc:
                raise ActuatorError(
                    f"HWP transaction failed: {exc}; exact rollback failed: {rollback_exc}"
                ) from exc
            raise ActuatorError(f"HWP transaction failed and was rolled back: {exc}") from exc
        return {"before": before, "after": after}

    def apply_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.apply_values(
            epp=str(envelope["epp"]),
            max_perf_pct=int(envelope["max_perf_pct"]),
            turbo=bool(envelope["turbo"]),
        )

    def restore(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        before = self.snapshot()
        try:
            after = self._restore_exact(snapshot)
        except Exception as exc:
            try:
                self._restore_exact(before)
            except Exception as rollback_exc:
                raise ActuatorError(
                    f"HWP restore failed: {exc}; rollback to pre-restore state failed: "
                    f"{rollback_exc}"
                ) from exc
            raise ActuatorError(
                f"HWP restore failed and pre-restore state was recovered: {exc}"
            ) from exc
        return {"before": before, "after": after}
