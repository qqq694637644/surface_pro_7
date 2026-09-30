from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .collector import version_fingerprint
from .metrics import system_snapshot
from .proposals import proposal_template


class KnowledgeManager:
    def __init__(self, config, db, registry):
        self.config = config
        self.db = db
        self.registry = registry

    def detect_drift(self) -> dict[str, Any]:
        current = system_snapshot()
        fingerprint, versions = version_fingerprint(current)
        previous = self.db.latest_version_snapshot()
        changed = bool(previous and previous.get("fingerprint") != fingerprint)
        details = {
            "changed": changed,
            "current_fingerprint": fingerprint,
            "previous_fingerprint": previous.get("fingerprint") if previous else None,
            "current": versions,
            "previous": previous.get("versions") if previous else None,
        }
        if changed:
            for profile in self.db.profiles(status="verified"):
                if profile.get("backend") != "noop":
                    self.db.set_profile_status(profile["profile_id"], "needs_revalidation")
            self.db.add_system_event("system_version_drift", details)
        self.db.add_version_snapshot(fingerprint, versions)
        return details

    def scene_summary(self, hours: float) -> dict[str, Any]:
        since = time.time() - hours * 3600
        rollups = self.db.recent_rollups(since)
        sessions = self.db.recent_sessions(since)
        by_scene: dict[str, dict[str, Any]] = {}
        source = rollups if rollups else sessions
        for session in source:
            scene = str(session.get("scene") or "unknown")
            bucket = by_scene.setdefault(
                scene,
                {
                    "sessions": 0,
                    "valid_duration_s": 0.0,
                    "energy_wh": 0.0,
                    "weighted_power_numerator": 0.0,
                },
            )
            duration = float(session.get("valid_duration_s") or 0.0)
            energy = float(session.get("energy_wh") or 0.0)
            bucket["sessions"] += 1
            bucket["valid_duration_s"] += duration
            bucket["energy_wh"] += energy
            avg = session.get("avg_power_w")
            if isinstance(avg, (int, float)):
                bucket["weighted_power_numerator"] += float(avg) * duration
        for bucket in by_scene.values():
            duration = bucket["valid_duration_s"]
            bucket["average_power_w"] = (
                bucket.pop("weighted_power_numerator") / duration if duration else None
            )
        return {
            "hours": hours,
            "scenes": by_scene,
            "session_count": len(sessions),
            "rollup_count": len(rollups),
            "source": "minute_rollups" if rollups else "sessions",
        }

    def app_summary(self, hours: float) -> list[dict[str, Any]]:
        since = time.time() - hours * 3600
        now = time.time()
        totals: dict[str, float] = {}
        for event in self.db.recent_app_events(since):
            app = str(event.get("app") or "unknown")
            start = max(since, float(event.get("start_ts") or since))
            end = min(now, float(event.get("end_ts") or now))
            totals[app] = totals.get(app, 0.0) + max(0.0, end - start)
        return [
            {"app": app, "active_seconds": seconds}
            for app, seconds in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:20]
        ]

    def regression_summary(self, scene: str, recent_hours: float = 24, baseline_days: int = 14) -> dict[str, Any]:
        now = time.time()
        recent = self.db.recent_rollups(now - recent_hours * 3600, scene)
        historical = [
            s for s in self.db.recent_rollups(now - baseline_days * 86400, scene)
            if float(s.get("bucket_ts") or 0) < now - recent_hours * 3600
        ]
        if not recent:
            recent = self.db.recent_sessions(now - recent_hours * 3600, scene)
        if not historical:
            historical = [
                s for s in self.db.recent_sessions(now - baseline_days * 86400, scene)
                if float(s.get("end_ts") or 0) < now - recent_hours * 3600
            ]

        def weighted(sessions):
            total_s = sum(float(s.get("valid_duration_s") or 0) for s in sessions)
            if not total_s:
                return None
            return sum(
                float(s.get("avg_power_w") or 0) * float(s.get("valid_duration_s") or 0)
                for s in sessions if isinstance(s.get("avg_power_w"), (int, float))
            ) / total_s

        recent_avg, historical_avg = weighted(recent), weighted(historical)
        delta = (
            recent_avg - historical_avg
            if recent_avg is not None and historical_avg is not None
            else None
        )
        return {
            "scene": scene,
            "recent_average_power_w": recent_avg,
            "historical_average_power_w": historical_avg,
            "delta_w": delta,
            "recent_sessions": len(recent),
            "historical_sessions": len(historical),
        }

    def build_pack(self, recent_hours: float | None = None) -> dict[str, Any]:
        recent_hours = recent_hours or float(self.config.get("llm.recent_hours", 24))
        summary_1h = self.scene_summary(1)
        summary_recent = self.scene_summary(recent_hours)
        profiles = self.db.profiles()
        trials = []
        history_cutoff = time.time() - int(
            self.config.get("llm.history_days", 30)
        ) * 86400
        rows = self.db.conn.execute(
            """SELECT trial_id FROM trials
            WHERE COALESCE(start_ts,end_ts,0)>=?
               OR state NOT IN ('PROMOTED','REJECTED','ROLLED_BACK','FAILED','INSUFFICIENT_DATA')
            ORDER BY COALESCE(start_ts,end_ts,0) DESC LIMIT ?""",
            (
                history_cutoff,
                int(self.config.get("llm.max_trials_in_pack", 40)),
            ),
        )
        for row in rows:
            trial = self.db.get_trial(row["trial_id"])
            if trial:
                trials.append(trial)
        active = self.db.active_trial()
        scenes = sorted(summary_recent["scenes"].keys())
        regressions = [self.regression_summary(scene) for scene in scenes]
        current = self.db.latest_sample()
        current_scene = (
            str(current.get("context_scene"))
            if isinstance(current, dict) and current.get("context_scene")
            else "unknown"
        )
        pack = {
            "schema_version": 2,
            "generated_at": time.time(),
            "objective": (
                "Optimize real battery energy per usage context while preserving "
                "responsiveness, task completion time, media quality, stability and suspend/wake."
            ),
            "system_health": self.db.health(),
            "current": current,
            "system_version": self.db.latest_version_snapshot(),
            "battery_health": self.db.latest_battery_health(),
            "last_1h": summary_1h,
            "recent": summary_recent,
            "apps_last_1h": self.app_summary(1),
            "apps_recent": self.app_summary(recent_hours),
            "regressions": regressions,
            "active_trial": active,
            "profiles": profiles,
            "context_policies": self.db.context_policies(),
            "feedback": self.db.recent_feedback(limit=200),
            "rejections": self.db.rejections(limit=1000),
            "recent_decisions": self.db.recent_decisions(limit=100),
            "recent_system_events": self.db.recent_system_events(
                time.time() - recent_hours * 3600,
                limit=200,
            ),
            "recent_trials": trials,
            "automation": self.config.section("automation"),
            "allowed_llm_actions": [
                "NO_CHANGE",
                "NEED_MORE_DATA",
                "PROPOSE_TRIAL",
                "ROLLBACK_TRIAL",
                "PROMOTE_PROFILE",
                "INVESTIGATE_REGRESSION",
                "UPDATE_CONTEXT_RULE",
            ],
            "rules": {
                "llm_is_not_reward_function": True,
                "one_primary_change_per_trial": True,
                "unknown_context_uses_safe_baseline": True,
                "missing_core_evidence_means_insufficient_data": True,
                "real_time_policy_must_not_depend_on_llm": True,
            },
            "next_proposal_template": proposal_template("", current_scene),
        }
        raw = json.dumps(pack, sort_keys=True, ensure_ascii=False, default=str).encode()
        pack["pack_sha256"] = hashlib.sha256(raw).hexdigest()
        return pack

    def export_git_knowledge(self) -> dict[str, Any]:
        root = self.config.root / "history" / "continuous"
        trials_dir = root / "trials"
        daily_dir = root / "daily"
        trials_dir.mkdir(parents=True, exist_ok=True)
        daily_dir.mkdir(parents=True, exist_ok=True)

        exported_trials = 0
        for row in self.db.conn.execute(
            "SELECT trial_id FROM trials ORDER BY COALESCE(start_ts,0)"
        ):
            trial = self.db.get_trial(row["trial_id"])
            if not trial:
                continue
            (trials_dir / f"{trial['trial_id']}.json").write_text(
                json.dumps(trial, indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
            exported_trials += 1

        compact = {
            "generated_at": time.time(),
            "profiles": self.db.profiles(),
            "context_policies": self.db.context_policies(),
            "rejections": self.db.rejections(limit=100000),
            "feedback": self.db.recent_feedback(limit=100000),
            "decisions": self.db.recent_decisions(limit=5000),
            "system_version": self.db.latest_version_snapshot(),
            "battery_health": self.db.latest_battery_health(),
        }
        (root / "knowledge.json").write_text(
            json.dumps(compact, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

        day = datetime.now().astimezone().date().isoformat()
        daily = {
            "date": day,
            "scene_summary_24h": self.scene_summary(24),
            "apps_24h": self.app_summary(24),
            "regressions": [
                self.regression_summary(scene)
                for scene in sorted(self.scene_summary(24)["scenes"])
            ],
        }
        (daily_dir / f"{day}.json").write_text(
            json.dumps(daily, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        return {
            "root": str(root),
            "trials": exported_trials,
            "knowledge_file": str(root / "knowledge.json"),
            "daily_file": str(daily_dir / f"{day}.json"),
        }
