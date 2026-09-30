from sp7_powerlab.config import load_config
from sp7_powerlab.experiments import TrialManager
from sp7_powerlab.profiles import ProfileRegistry
from sp7_powerlab.proposals import normalize_proposal, proposal_template
from sp7_powerlab.storage import Database


class FakeProfileActuators:
    def __init__(self):
        self.current = "baseline"

    def profile_trial_errors(self, profile, unattended=False):
        if unattended and not (profile.get("evidence") or {}).get(
            "auto_trial_allowed", False
        ):
            return ["candidate profile is not enabled for unattended trials"]
        return []

    def parameter_trial_errors(self, parameter):
        return []

    def snapshot_profile_state(self, candidate_profile, baseline_profile=None):
        return {
            "kind": "profile",
            "backend": "power-profiles-daemon",
            "current_backend_profile": self.current,
            "baseline_profile_id": (
                baseline_profile.get("profile_id") if baseline_profile else None
            ),
        }

    def apply_profile(self, profile):
        before = self.current
        self.current = profile["profile_id"]
        return {
            "backend": profile["backend"],
            "profile": profile["profile_id"],
            "before": {"profile": before},
            "after": {"profile": self.current},
        }

    def restore_profile_state(self, snapshot, baseline_profile=None):
        before = self.current
        self.current = (
            baseline_profile["profile_id"]
            if baseline_profile
            else snapshot.get("current_backend_profile")
        )
        return {
            "backend": snapshot.get("backend"),
            "before": before,
            "after": self.current,
        }


def write_profile(path, profile_id, status, *, auto_trial=False):
    path.write_text(
        "[profile]\n"
        f'id = "{profile_id}"\n'
        'backend = "power-profiles-daemon"\n'
        f'backend_profile = "{profile_id}"\n'
        f'status = "{status}"\n'
        'scenes = ["reading"]\n'
        "\n[evidence]\n"
        f"auto_trial_allowed = {'true' if auto_trial else 'false'}\n",
        encoding="utf-8",
    )


def make_manager(tmp_path, *, candidate_auto=False):
    profile_dir = tmp_path / "config" / "profiles"
    profile_dir.mkdir(parents=True)
    write_profile(profile_dir / "baseline.toml", "baseline", "verified")
    write_profile(
        profile_dir / "candidate.toml",
        "candidate",
        "experimental",
        auto_trial=candidate_auto,
    )
    config = load_config(tmp_path)
    config.data["experiment"]["settle_seconds"] = 0
    db = Database(tmp_path / "runtime" / "powerlab.sqlite3")
    registry = ProfileRegistry(tmp_path, db)
    registry.load()
    actuators = FakeProfileActuators()
    return config, db, registry, actuators, TrialManager(
        config, db, actuators, registry
    )


def profile_proposal():
    proposal = proposal_template("", "reading")
    proposal["title"] = "Compare external power profile"
    proposal["rationale"] = "Measure a candidate profile under the same reading context."
    proposal["change"] = {
        "parameter": "profile.id",
        "from": "baseline",
        "to": "candidate",
    }
    proposal["expected_effect"]["confidence"] = "medium"
    proposal["validation"]["duration_seconds"] = 0
    return normalize_proposal(proposal)


def test_external_profile_trial_can_apply_and_rollback(tmp_path):
    config, db, registry, actuators, manager = make_manager(tmp_path)
    try:
        trial = manager.start(
            profile_proposal(),
            current_context={
                "scene": "reading",
                "confidence": 0.95,
                "battery_status": "Discharging",
                "battery_pct": 70,
            },
        )
        assert trial["state"] == "SETTLING"
        assert trial["baseline_profile"] == "baseline"
        assert trial["candidate_profile"] == "candidate"
        assert actuators.current == "candidate"

        rolled = manager.rollback(trial["trial_id"], reason="test")
        assert rolled["state"] == "ROLLED_BACK"
        assert actuators.current == "baseline"
    finally:
        db.close()


def test_external_profile_unattended_trial_requires_explicit_opt_in(tmp_path):
    config, db, registry, actuators, manager = make_manager(tmp_path)
    try:
        errors = manager.validate_proposal(
            profile_proposal(),
            current_context={
                "scene": "reading",
                "confidence": 0.95,
                "battery_status": "Discharging",
                "battery_pct": 70,
            },
            unattended=True,
        )
        assert any("unattended trials" in error for error in errors)
    finally:
        db.close()


def test_external_profile_can_opt_in_to_unattended_trial(tmp_path):
    config, db, registry, actuators, manager = make_manager(
        tmp_path, candidate_auto=True
    )
    try:
        errors = manager.validate_proposal(
            profile_proposal(),
            current_context={
                "scene": "reading",
                "confidence": 0.95,
                "battery_status": "Discharging",
                "battery_pct": 70,
            },
            unattended=True,
        )
        assert errors == []
    finally:
        db.close()


def test_promoting_profile_trial_verifies_candidate_and_scene_policy(tmp_path):
    config, db, registry, actuators, manager = make_manager(tmp_path)
    try:
        trial = manager.start(
            profile_proposal(),
            current_context={
                "scene": "reading",
                "confidence": 0.95,
                "battery_status": "Discharging",
                "battery_pct": 70,
            },
        )
        result = {
            "verdict": "CANDIDATE_WINNER",
            "candidate_conditions": {
                "kernel": "test-kernel",
                "app_major_version": 1,
                "battery_health_pct": 98.0,
            },
        }
        db.update_trial(
            trial["trial_id"],
            state="CANDIDATE_WINNER",
            result=result,
        )

        promoted = manager.promote(trial["trial_id"])
        assert promoted["profile_id"] == "candidate"
        assert promoted["status"] == "verified"

        stored = {
            profile["profile_id"]: profile for profile in db.profiles()
        }["candidate"]
        assert stored["status"] == "verified"
        assert stored["evidence"]["source_trial"] == trial["trial_id"]
        assert stored["last_validated"]["kernel"] == "test-kernel"

        policy = db.context_policy("reading")
        assert policy["profile_id"] == "candidate"
        assert db.get_trial(trial["trial_id"])["state"] == "PROMOTED"
    finally:
        db.close()
