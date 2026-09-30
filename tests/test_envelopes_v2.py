from pathlib import Path

from sp7_powerlab.envelopes import EnvelopeRegistry
from sp7_powerlab.storage import Database


def test_config_definition_change_invalidates_verified_envelope(project_root: Path):
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        registry = EnvelopeRegistry(project_root, db)
        registry.load()
        assert registry.get("INTERACTIVE_EFFICIENT")["status"] == "VERIFIED"

        path = project_root / "config/envelopes.toml"
        text = path.read_text(encoding="utf-8").replace("max_perf_pct = 60", "max_perf_pct = 55", 1)
        path.write_text(text, encoding="utf-8")

        second = EnvelopeRegistry(project_root, db)
        second.load()
        assert second.get("INTERACTIVE_EFFICIENT")["status"] == "NEEDS_REVALIDATION"
    finally:
        db.close()


def test_candidate_only_allows_hwp_fields(project_root: Path):
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        registry = EnvelopeRegistry(project_root, db)
        registry.load()
        try:
            registry.candidate_from_change("INTERACTIVE_EFFICIENT", {"arbitrary_sysfs": 1})
        except ValueError as exc:
            assert "unsupported" in str(exc)
        else:
            raise AssertionError("unexpected field should be rejected")
    finally:
        db.close()


def test_adopt_current_records_real_hwp_as_verified(project_root: Path):
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        registry = EnvelopeRegistry(project_root, db)
        registry.load()
        adopted = registry.adopt_current(
            "INTERACTIVE_EFFICIENT",
            {
                "epp": {"policy0": "balance_power", "policy1": "balance_power"},
                "max_perf_pct": 57,
                "turbo": True,
            },
            battery_epoch=1,
            system_fingerprint="fp",
            calibration_version=1,
            note="real baseline",
        )
        assert adopted["status"] == "VERIFIED"
        assert adopted["source"] == "adopted"
        assert adopted["max_perf_pct"] == 57
        persisted = (project_root / "config/envelopes.toml").read_text(encoding="utf-8")
        assert 'status = "CANDIDATE"' in persisted
        assert "max_perf_pct = 57" in persisted

        reloaded = EnvelopeRegistry(project_root, db)
        reloaded.load()
        assert reloaded.get("INTERACTIVE_EFFICIENT")["max_perf_pct"] == 57
    finally:
        db.close()


def test_named_candidate_can_validate_complete_envelope(project_root: Path):
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        registry = EnvelopeRegistry(project_root, db)
        registry.load()
        candidate = registry.candidate_from_named("REMOTE_EFFICIENT")
        assert candidate["name"] == "REMOTE_EFFICIENT"
        assert candidate["status"] == "VALIDATING"
        assert candidate["max_perf_pct"] == 50
    finally:
        db.close()


def test_promoted_trial_revision_survives_static_config_reload(project_root: Path):
    db = Database(project_root / "runtime/db.sqlite3")
    try:
        registry = EnvelopeRegistry(project_root, db)
        registry.load()
        candidate = registry.candidate_from_change(
            "INTERACTIVE_EFFICIENT",
            {"max_perf_pct": 50},
        )
        promoted = registry.promote_candidate(
            candidate,
            battery_epoch=1,
            system_fingerprint="fp",
            calibration_version=1,
            result={"verdict": "CANDIDATE_WINNER"},
        )
        assert promoted["revision"] == 2
        assert promoted["max_perf_pct"] == 50

        reloaded = EnvelopeRegistry(project_root, db)
        reloaded.load()
        stored = reloaded.get("INTERACTIVE_EFFICIENT")
        assert stored["revision"] == 2
        assert stored["max_perf_pct"] == 50
        assert stored["status"] == "VERIFIED"
        assert stored["source"] == "trial"
    finally:
        db.close()
