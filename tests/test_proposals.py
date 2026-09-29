from sp7_powerlab.proposals import (
    classify_parameter,
    normalize_proposal,
    proposal_template,
    validate_proposal,
)


def valid_proposal():
    proposal = proposal_template("exp-1", "web")
    proposal["title"] = "Try EPP power"
    proposal["rationale"] = "Previous web run showed frequent short CPU bursts."
    proposal["change"] = {
        "parameter": "cpu.epp",
        "from": "balance_power",
        "to": "power",
    }
    proposal["expected_effect"]["average_power_delta_w"] = {"min": -0.4, "max": -0.1}
    proposal["expected_effect"]["confidence"] = "medium"
    proposal["rollback"]["instruction"] = "Restore EPP to balance_power."
    proposal["validation"]["acceptance"]["max_average_power_delta_w"] = -0.05
    return proposal


def test_allowlisted_parameter_is_reviewed_not_auto_applied():
    policy = classify_parameter("cpu.epp")
    assert policy["classification"] == "allowlisted-reversible"
    assert policy["autonomous_apply_allowed"] is False
    assert policy["human_review_required"] is True


def test_sensitive_parameter_is_human_review_only():
    policy = classify_parameter("suspend.mem_sleep")
    assert policy["classification"] == "sensitive-human-review-only"


def test_valid_proposal_normalizes_id_and_policy():
    proposal = valid_proposal()
    assert validate_proposal(proposal) == []
    normalized = normalize_proposal(proposal)
    assert normalized["id"].startswith("p-")
    assert normalized["powerlab_policy"]["classification"] == "allowlisted-reversible"


def test_unknown_parameter_is_rejected():
    proposal = valid_proposal()
    proposal["change"]["parameter"] = "mystery.magic_knob"
    errors = validate_proposal(proposal)
    assert any("not classified" in error for error in errors)
