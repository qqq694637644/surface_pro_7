from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from .config import load_envelope_config
from .storage import Database

VALID_EPP = {"performance", "balance_performance", "balance_power", "power"}
VALID_STATUS = {
    "CANDIDATE",
    "VALIDATING",
    "REVALIDATING",
    "VERIFIED",
    "NEEDS_REVALIDATION",
    "BLOCKED",
    "RETIRED",
}


def snapshot_matches_envelope(
    snapshot: dict[str, Any],
    envelope: dict[str, Any],
) -> bool:
    if snapshot.get("max_perf_pct") != int(envelope["max_perf_pct"]):
        return False
    if snapshot.get("turbo") is not None and snapshot.get("turbo") != bool(envelope["turbo"]):
        return False
    epp_values = list((snapshot.get("epp") or {}).values())
    return bool(epp_values) and all(value == envelope["epp"] for value in epp_values)


def _hash(envelope: dict[str, Any]) -> str:
    payload = {
        "epp": envelope["epp"],
        "max_perf_pct": int(envelope["max_perf_pct"]),
        "turbo": bool(envelope["turbo"]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def validate_envelope(envelope: dict[str, Any]) -> None:
    if envelope.get("epp") not in VALID_EPP:
        raise ValueError(f"invalid EPP: {envelope.get('epp')}")
    max_perf = int(envelope.get("max_perf_pct", 0))
    if not 10 <= max_perf <= 100:
        raise ValueError(f"max_perf_pct out of range: {max_perf}")
    if envelope.get("status") not in VALID_STATUS:
        raise ValueError(f"invalid envelope status: {envelope.get('status')}")
    if not isinstance(envelope.get("turbo"), bool):
        raise ValueError("turbo must be boolean")


@dataclass
class EnvelopeRegistry:
    root: Any
    db: Database

    def load(self) -> dict[str, dict[str, Any]]:
        configured = load_envelope_config(self.root).get("envelopes") or {}
        existing = {item["name"]: item for item in self.db.envelopes()}
        result: dict[str, dict[str, Any]] = {}
        for name, raw in configured.items():
            env = {
                "name": name,
                "revision": int(raw.get("revision", 1)),
                "status": str(raw.get("status", "CANDIDATE")).upper(),
                "epp": str(raw.get("epp", "balance_power")),
                "max_perf_pct": int(raw.get("max_perf_pct", 60)),
                "turbo": bool(raw.get("turbo", True)),
                "source": "config",
                "updated_ts": time.time(),
            }
            env["content_hash"] = _hash(env)
            validate_envelope(env)
            stored = existing.get(name)
            if stored:
                stored_revision = int(stored.get("revision") or 1)
                config_revision = int(env.get("revision") or 1)
                if (
                    stored.get("source") in {"trial", "adopted", "rollback"}
                    and stored_revision >= config_revision
                    and stored.get("content_hash") == env["content_hash"]
                ):
                    result[name] = stored
                    continue
                if (
                    stored.get("status") == "VERIFIED"
                    and stored.get("content_hash") != env["content_hash"]
                ):
                    env["status"] = "NEEDS_REVALIDATION"
                elif stored.get("status") in {
                    "VERIFIED",
                    "NEEDS_REVALIDATION",
                    "BLOCKED",
                    "RETIRED",
                }:
                    env["status"] = stored["status"]
                env["revision"] = max(int(env["revision"]), int(stored.get("revision") or 1))
            self.db.upsert_envelope(env)
            result[name] = env

        for name, stored in existing.items():
            if name not in result and stored.get("source") != "config":
                result[name] = stored
        return result

    def _persist_parameters(self) -> None:
        lines = [
            "# Parameter persistence only.",
            "# Runtime VERIFIED/NEEDS_REVALIDATION state lives in SQLite and is",
            "# intentionally not trusted on a fresh machine or fresh battery epoch.",
            "",
        ]
        for env in sorted(self.db.envelopes(), key=lambda item: item["name"]):
            lines.extend(
                [
                    f"[envelopes.{env['name']}]",
                    f"revision = {int(env.get('revision') or 1)}",
                    'status = "CANDIDATE"',
                    f'epp = "{env["epp"]}"',
                    f"max_perf_pct = {int(env['max_perf_pct'])}",
                    f"turbo = {'true' if env['turbo'] else 'false'}",
                    "",
                ]
            )
        path = self.root / "config" / "envelopes.toml"
        path.write_text("\n".join(lines), encoding="utf-8")

    def get(self, name: str) -> dict[str, Any] | None:
        self.load()
        return self.db.envelope(name)

    def list(self) -> list[dict[str, Any]]:
        self.load()
        return self.db.envelopes()

    def verified(self, name: str) -> bool:
        env = self.get(name)
        return bool(env and env.get("status") == "VERIFIED")

    def match_verified_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        preferred: str | None = None,
    ) -> str | None:
        matches = [
            env["name"]
            for env in self.list()
            if env.get("status") == "VERIFIED" and snapshot_matches_envelope(snapshot, env)
        ]
        if preferred in matches:
            return preferred
        return matches[0] if len(matches) == 1 else None

    def set_status(self, name: str, status: str) -> dict[str, Any]:
        env = self.get(name)
        if not env:
            raise KeyError(name)
        status = status.upper()
        if status not in VALID_STATUS:
            raise ValueError(status)
        env["status"] = status
        env["updated_ts"] = time.time()
        self.db.upsert_envelope(env)
        return env

    def verify(
        self,
        name: str,
        *,
        battery_epoch: int | None,
        system_fingerprint: str | None,
        calibration_version: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        env = self.set_status(name, "VERIFIED")
        self.db.add_envelope_validation(
            {
                "envelope_name": name,
                "revision": env["revision"],
                "battery_epoch": battery_epoch,
                "system_fingerprint": system_fingerprint,
                "calibration_version": calibration_version,
                "result": result,
            }
        )
        return env

    def candidate_from_change(
        self,
        base_name: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        base = self.get(base_name)
        if not base:
            raise KeyError(base_name)
        allowed = {"epp", "max_perf_pct", "turbo"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported envelope changes: {sorted(unknown)}")
        candidate = {
            **base,
            **changes,
            "name": base_name,
            "status": "VALIDATING",
            "revision": int(base.get("revision") or 1) + 1,
            "source": "trial",
            "updated_ts": time.time(),
        }
        candidate["content_hash"] = _hash(candidate)
        validate_envelope(candidate)
        return candidate

    def candidate_from_named(self, name: str) -> dict[str, Any]:
        envelope = self.get(name)
        if not envelope:
            raise KeyError(name)
        if envelope.get("status") in {"BLOCKED", "RETIRED"}:
            raise ValueError(f"envelope is not eligible for validation: {name}")
        candidate = {
            **envelope,
            "status": "VALIDATING",
            "source": "trial",
            "updated_ts": time.time(),
        }
        candidate["content_hash"] = _hash(candidate)
        validate_envelope(candidate)
        return candidate

    def adopt_current(
        self,
        name: str,
        snapshot: dict[str, Any],
        *,
        battery_epoch: int | None,
        system_fingerprint: str | None,
        calibration_version: int,
        note: str,
    ) -> dict[str, Any]:
        configured = self.get(name)
        if not configured:
            raise KeyError(name)
        epp_values = list((snapshot.get("epp") or {}).values())
        if not epp_values or len(set(epp_values)) != 1:
            raise ValueError("current HWP policies do not share one EPP value")
        max_perf_pct = snapshot.get("max_perf_pct")
        turbo = snapshot.get("turbo")
        if not isinstance(max_perf_pct, int) or turbo is None:
            raise ValueError("current HWP snapshot is incomplete")
        adopted = {
            "name": name,
            "revision": max(int(configured.get("revision") or 1) + 1, 2),
            "status": "VERIFIED",
            "epp": epp_values[0],
            "max_perf_pct": max_perf_pct,
            "turbo": bool(turbo),
            "source": "adopted",
            "updated_ts": time.time(),
        }
        adopted["content_hash"] = _hash(adopted)
        validate_envelope(adopted)
        self.db.upsert_envelope(adopted)
        self.db.add_envelope_validation(
            {
                "envelope_name": name,
                "revision": adopted["revision"],
                "battery_epoch": battery_epoch,
                "system_fingerprint": system_fingerprint,
                "calibration_version": calibration_version,
                "result": {
                    "manual": True,
                    "adopted_current_hwp": True,
                    "note": note,
                },
            }
        )
        self._persist_parameters()
        return adopted

    def promote_candidate(
        self,
        candidate: dict[str, Any],
        *,
        battery_epoch: int | None,
        system_fingerprint: str | None,
        calibration_version: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        promoted = {
            **candidate,
            "status": "VERIFIED",
            "source": "trial",
            "updated_ts": time.time(),
        }
        promoted["content_hash"] = _hash(promoted)
        validate_envelope(promoted)
        self.db.upsert_envelope(promoted)
        self.db.add_envelope_validation(
            {
                "envelope_name": promoted["name"],
                "revision": promoted["revision"],
                "battery_epoch": battery_epoch,
                "system_fingerprint": system_fingerprint,
                "calibration_version": calibration_version,
                "result": result,
            }
        )
        self._persist_parameters()
        return promoted

    def restore_previous_verified(
        self,
        previous: dict[str, Any],
        *,
        battery_epoch: int | None,
        system_fingerprint: str | None,
        calibration_version: int,
        reason: str,
    ) -> dict[str, Any]:
        current = self.get(str(previous["name"]))
        restored = {
            **previous,
            "revision": max(
                int(previous.get("revision") or 1) + 1,
                int((current or {}).get("revision") or 0) + 1,
            ),
            "status": "VERIFIED",
            "source": "rollback",
            "updated_ts": time.time(),
        }
        restored["content_hash"] = _hash(restored)
        validate_envelope(restored)
        self.db.upsert_envelope(restored)
        self.db.add_envelope_validation(
            {
                "envelope_name": restored["name"],
                "revision": restored["revision"],
                "battery_epoch": battery_epoch,
                "system_fingerprint": system_fingerprint,
                "calibration_version": calibration_version,
                "result": {
                    "rollback": True,
                    "reason": reason,
                    "restored_content_hash": restored["content_hash"],
                },
            }
        )
        self._persist_parameters()
        return restored

    def mark_verified_needs_revalidation(self, reason: str) -> list[str]:
        return self.mark_needs_revalidation(None, reason)

    def mark_needs_revalidation(
        self,
        names: set[str] | None,
        reason: str,
    ) -> list[str]:
        changed: list[str] = []
        for env in self.db.envelopes():
            if env.get("status") == "VERIFIED" and (names is None or env.get("name") in names):
                env["status"] = "NEEDS_REVALIDATION"
                env["updated_ts"] = time.time()
                env["drift_reason"] = reason
                self.db.upsert_envelope(env)
                changed.append(env["name"])
        return changed
