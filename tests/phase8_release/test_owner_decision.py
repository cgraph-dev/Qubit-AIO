"""P8-05 — owner decision and waiver rules (hermetic fixtures only)."""
from __future__ import annotations

from pathlib import Path

import pytest

import p8r_fixture as fx


RE = fx.load_script("release_evidence")


@pytest.fixture
def now():
    return RE.parse_time(fx.NOW)


def evaluate(root: Path, now):
    return RE.evaluate(root, now=now, max_age_days=30.0,
                       commit=fx.FROZEN_COMMIT, tree=fx.FROZEN_TREE)


def test_missing_owner_decision_is_ready_and_never_synthesizes_a_signature(
        tmp_path, now):
    root = fx.valid_root(tmp_path, include_decision=False)

    report = evaluate(root, now)

    assert report["status"] == "READY_FOR_OWNER_DECISION"
    assert any("owner release decision" in item for item in report["missing"])
    decision = report["owner_decision"]
    assert decision["present"] is False
    assert decision["signed"] is False
    assert "signed_by" not in decision
    assert not (root / "evidence" / "phase-8" /
                "owner-release-decision.json").exists()


@pytest.mark.parametrize("signer", ["", "agent", "AI", "opencode", "tbd"])
def test_agent_or_placeholder_signature_is_refused(tmp_path, now, signer):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(signed_by=signer))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("not a named human owner" in problem
               for problem in report["problems"])


def test_non_owner_role_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(role="integration-lead"))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("role" in problem for problem in report["problems"])


def test_decision_must_be_approved(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(decision="REJECTED"))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("expected 'APPROVED'" in problem
               for problem in report["problems"])


def test_decision_must_name_the_frozen_sha(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(
        root, fx.owner_decision(release_sha="f" * 40))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("release_sha" in problem for problem in report["problems"])


def test_waiver_without_owner_signoff_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(waivers=[{
        "id": "W-1",
        "risk": "Accessibility manual review pending",
        "expires_at": "2026-12-01T00:00:00Z",
        "compensating_control": "Keyboard-only smoke test each release",
    }]))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("owner sign-off is missing" in problem
               for problem in report["problems"])


def test_waiver_without_risk_expiry_or_control_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(waivers=[{
        "id": "W-2",
        "owner_signoff": True,
    }]))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    problems = " ".join(report["problems"])
    assert "risk is missing" in problems
    assert "expires_at is missing" in problems
    assert "compensating_control is missing" in problems


def test_expired_waiver_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(waivers=[{
        "id": "W-3",
        "owner_signoff": True,
        "risk": "Known medium finding",
        "expires_at": "2026-11-01T00:00:00Z",
        "compensating_control": "Feature disabled by default",
    }]))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("expiry has already passed" in problem
               for problem in report["problems"])


def test_signed_waiver_is_recorded_but_is_not_pass_evidence(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_owner_decision(root, fx.owner_decision(waivers=[{
        "id": "W-4",
        "owner_signoff": True,
        "risk": "Screen-reader review scheduled after release",
        "expires_at": "2026-12-01T00:00:00Z",
        "compensating_control": "Keyboard-only smoke test each release",
    }]))

    report = evaluate(root, now)

    assert report["status"] == "PASS"
    assert report["owner_decision"]["waiver_note"] == (
        "waivers are recorded exceptions, not PASS evidence")
    assert report["waivers"][0]["id"] == "W-4"
    assert report["waivers"][0]["problems"] == []


def test_live_trading_claim_without_separate_signature_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    document = fx.owner_decision()
    document["live_trading"] = {"ready": True}
    fx.write_owner_decision(root, document)

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("live-trading readiness" in problem
               for problem in report["problems"])
