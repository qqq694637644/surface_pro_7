from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


VALID_STATUSES = {"experimental", "verified", "needs_revalidation", "deprecated", "blocked"}


def load_profile(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    meta = data.get("profile") or {}
    profile_id = str(meta.get("id") or path.stem)
    status = str(meta.get("status") or "experimental")
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid profile status {status}: {path}")
    raw = path.read_bytes()
    return {
        "profile_id": profile_id,
        "backend": str(meta.get("backend") or "power-profiles-daemon"),
        "backend_profile": meta.get("backend_profile"),
        "status": status,
        "scenes": [str(x) for x in meta.get("scenes", [])],
        "content_hash": hashlib.sha256(raw).hexdigest(),
        "parameters": data.get("parameters") or {},
        "evidence": data.get("evidence") or {},
        "source_path": str(path),
    }


class ProfileRegistry:
    def __init__(self, root: Path, db=None):
        self.root = root
        self.db = db
        self._profiles: dict[str, dict[str, Any]] = {}

    def load(self) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        existing_db = (
            {p["profile_id"]: p for p in self.db.profiles()}
            if self.db
            else {}
        )
        directory = self.root / "config" / "profiles"
        if directory.exists():
            for path in sorted(directory.glob("*.toml")):
                profile = load_profile(path)
                profiles[profile["profile_id"]] = profile
                if self.db:
                    stored = existing_db.get(profile["profile_id"])
                    db_profile = dict(profile)
                    if stored:
                        stored_status = stored.get("status", profile["status"])
                        definition_changed = bool(
                            stored.get("content_hash")
                            and stored.get("content_hash") != profile.get("content_hash")
                        )
                        if (
                            definition_changed
                            and stored_status == "verified"
                            and profile.get("backend") != "noop"
                        ):
                            db_profile["status"] = "needs_revalidation"
                            self.db.add_system_event(
                                "profile_definition_drift",
                                {
                                    "profile_id": profile["profile_id"],
                                    "old_content_hash": stored.get("content_hash"),
                                    "new_content_hash": profile.get("content_hash"),
                                },
                            )
                        else:
                            db_profile["status"] = stored_status
                        db_profile["evidence"] = {
                            **(profile.get("evidence") or {}),
                            **(stored.get("evidence") or {}),
                        }
                        if stored.get("last_validated"):
                            db_profile["last_validated"] = stored["last_validated"]
                    self.db.upsert_profile(db_profile)
        if self.db:
            db_profiles = {p["profile_id"]: p for p in self.db.profiles()}
            for profile_id, profile in profiles.items():
                db_item = db_profiles.get(profile_id)
                if db_item and db_item.get("status") in VALID_STATUSES:
                    profile["status"] = db_item["status"]
                    profile["evidence"] = db_item.get("evidence", profile.get("evidence", {}))
                    profile["last_validated"] = db_item.get("last_validated")
            for profile_id, db_item in db_profiles.items():
                if profile_id not in profiles:
                    profiles[profile_id] = {
                        **db_item,
                        "scenes": [
                            str(x)
                            for x in (db_item.get("evidence") or {}).get("scenes", [])
                        ],
                        "source_path": None,
                    }
        self._profiles = profiles
        return profiles

    def get(self, profile_id: str) -> dict[str, Any] | None:
        if not self._profiles:
            self.load()
        return self._profiles.get(profile_id)

    def verified_for_scene(self, scene: str) -> list[dict[str, Any]]:
        if not self._profiles:
            self.load()
        return [
            profile for profile in self._profiles.values()
            if profile.get("status") == "verified"
            and (not profile.get("scenes") or scene in profile.get("scenes", []))
        ]

    def set_status(self, profile_id: str, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(status)
        if self.db:
            self.db.set_profile_status(profile_id, status)
        if profile_id in self._profiles:
            self._profiles[profile_id]["status"] = status
