"""9U — OpenClaw ``skills.*`` extra family (verified-skill surface).

Static behavior proof (the manifest's own checks) plus runtime proof against the
canonical authorities: the verified-skill memory package
(``voicebridge.verified_skills``: store lifecycle, mutations journal, workshop),
the one chat-session authority (``voicebridge.qubit_session_store``), and the
permissioned skill store (``voicebridge.permissions``).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

MODULE_PATH = "voicebridge/gateway/openclaw_handlers/skills_extra.py"
MODULE = "voicebridge.gateway.openclaw_handlers.skills_extra"

SAFE = {
    "skills.bins": "SKILLS_BINS_LOCAL_BUNDLED",
    "skills.curator.pin": "SKILLS_CURATOR_PIN_LOCAL",
    "skills.curator.unpin": "SKILLS_CURATOR_UNPIN_LOCAL",
    "skills.curator.restore": "SKILLS_CURATOR_RESTORE_LOCAL",
    "skills.proposals.apply": "SKILLS_PROPOSALS_APPLY_LOCAL_VERDICT",
    "skills.proposals.reject": "SKILLS_PROPOSALS_REJECT_LOCAL_VERDICT",
    "skills.proposals.quarantine": "SKILLS_PROPOSALS_QUARANTINE_LOCAL",
    "skills.proposals.revise": "SKILLS_PROPOSALS_REVISE_LOCAL_STAGE",
    "skills.proposals.update": "SKILLS_PROPOSALS_UPDATE_LOCAL_STAGE",
    "skills.proposals.requestRevision":
        "SKILLS_PROPOSALS_REQUESTREVISION_LOCAL_SESSION",
    "skills.proposals.events.list": "SKILLS_PROPOSALS_EVENTS_LOCAL_JOURNAL",
    "skills.update": "SKILLS_UPDATE_LOCAL_CONFIG",
}
OPS = {
    "skills.proposals.evaluate": "SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED",
    "skills.proposals.historyScan":
        "SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED",
    "skills.securityVerdicts": "SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED",
    "skills.upload.begin": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
    "skills.upload.chunk": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
    "skills.upload.commit": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
}
IMPL: tuple = ()
ALL = set(SAFE) | set(OPS) | set(IMPL)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway.rpc import MethodRegistry

    mod = importlib.import_module(MODULE)
    reg = MethodRegistry()
    mod.register(reg)
    return reg


def _seed(cfg, name="demo-skill", verify=False):
    from voicebridge.verified_skills import SkillProposal, verified_skills_store

    store = verified_skills_store(cfg)
    rec = store.submit(SkillProposal(
        name=name, interface="interface x", implementation="impl x",
        acceptance_test='run(task) == "ok"', task="a demo task",
        capabilities=("skill.demo",), cost="cheap", trust=1, model=""))
    if verify:
        store.apply_verdict(rec.id, decision="verified",
                            requested_by="dashboard:user", reason="ok")
    return store, rec.id


def _manifest(skill_id="demo-cfg"):
    from voicebridge.capabilities import Tier
    from voicebridge.permissions import SkillManifest

    return SkillManifest(
        skill_id=skill_id, version="1.0.0", name="Demo", description="d",
        source="qubit-local", revision="1", license="MIT",
        content_hash="a" * 64, capabilities=("skill.demo",),
        scopes=("skill:read",), tier=Tier.READ_ONLY, sensitivity="internal",
        approval_policy="confirm", timeout_s=30.0, resource_limits={},
        audit_events=("skill.install",), compatibility="",
        rollback="remove the skill")


# --------------------------------------------------------------- static proof
def test_family_static_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    ours = {m for m, origin in origins.items() if origin.endswith(MODULE_PATH)}
    assert ours == ALL, ours
    assert set(_registry().names()) == ALL, set(_registry().names())
    for method in sorted(ALL):
        node = methods[method]
        code = SAFE.get(method) or OPS.get(method)
        assert code, method
        assert OUM._emits_code(node, funcs, code, consts), (
            f"{method} never emits {code!r}")


# ------------------------------------------------------------------ bins
def test_skills_bins_reports_bundled_without_executables(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "skills.bins", {})
    assert out["code"] == SAFE["skills.bins"], out
    assert out["bins"] == []
    assert "visualize" in out["bundledSkills"], out


# --------------------------------------------------------------- curator
def test_curator_pin_unpin_round_trip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, sid = _seed(cfg, verify=True)
    reg = _registry()

    pinned = reg.handle(cfg, "skills.curator.pin",
                        {"skill_id": sid, "confirmed": True})
    assert pinned["code"] == SAFE["skills.curator.pin"], pinned
    assert pinned["skill"]["pinned"] is True, pinned

    unpinned = reg.handle(cfg, "skills.curator.unpin",
                          {"skill_id": sid, "confirmed": True})
    assert unpinned["code"] == SAFE["skills.curator.unpin"], unpinned
    assert unpinned["skill"]["pinned"] is False, unpinned


def test_curator_pin_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    _, sid = _seed(cfg, verify=True)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "skills.curator.pin", {"skill_id": sid})
    assert exc.value.code == "CONFIRMATION_REQUIRED", exc.value


def test_curator_restore_rolls_back_a_real_backup(tmp_path, monkeypatch):
    from voicebridge.verified_skills.workshop import verified_skill_workshop

    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, verify=True)
    workshop = verified_skill_workshop(cfg)
    workshop.backup(label="before")
    out = _registry().handle(cfg, "skills.curator.restore",
                             {"confirmed": True})
    assert out["code"] == SAFE["skills.curator.restore"], out
    assert out["state"] == "OK", out


# ------------------------------------------------------- proposals decisions
def test_proposals_apply_promotes_a_quarantined_proposal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, sid = _seed(cfg)
    out = _registry().handle(cfg, "skills.proposals.apply",
                             {"skill_id": sid, "confirmed": True, "reason": "ok"})
    assert out["code"] == SAFE["skills.proposals.apply"], out
    assert out["skill"]["status"] == "verified", out


def test_proposals_reject_refutes_a_quarantined_proposal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, sid = _seed(cfg)
    out = _registry().handle(cfg, "skills.proposals.reject",
                             {"skill_id": sid, "confirmed": True})
    assert out["code"] == SAFE["skills.proposals.reject"], out
    assert out["skill"]["status"] == "refuted", out


def test_proposals_quarantine_a_verified_skill(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, sid = _seed(cfg, verify=True)
    out = _registry().handle(cfg, "skills.proposals.quarantine",
                             {"proposalId": sid, "confirmed": True})
    assert out["code"] == SAFE["skills.proposals.quarantine"], out
    assert out["skill"]["status"] == "quarantined", out


# ----------------------------------------------------------- proposals drafts
def test_proposals_revise_stages_on_the_confirmation_journal(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "skills.proposals.revise", {
        "name": "revised-skill", "license": "MIT", "task": "t",
        "content": "print('hi')", "description": "d"})
    assert out["code"] == SAFE["skills.proposals.revise"], out
    assert out["state"] == "needs-confirm", out
    assert out["confirmation_id"], out
    assert out["mutation_code"] == "CONFIRM_REQUIRED", out


def test_proposals_update_stages_on_the_confirmation_journal(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "skills.proposals.update", {
        "name": "updated-skill", "license": "Apache-2.0", "task": "t",
        "content": "print('hi')", "description": "d"})
    assert out["code"] == SAFE["skills.proposals.update"], out
    assert out["state"] == "needs-confirm", out
    assert out["confirmation_id"], out


def test_proposals_request_revision_appends_to_the_session(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "skills.proposals.requestRevision", {
        "sessionKey": "sess-rev", "proposalId": "p-1",
        "instructions": "please tighten the acceptance test", "confirmed": True})
    assert out["code"] == SAFE["skills.proposals.requestRevision"], out
    assert out["appended"]["state"] == "OK", out
    assert out["appended"]["message_count"] == 1, out


# --------------------------------------------------------- proposals history
def test_proposals_events_list_reads_the_journal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, sid = _seed(cfg, verify=True)
    out = _registry().handle(cfg, "skills.proposals.events.list",
                             {"proposalId": sid})
    assert out["code"] == SAFE["skills.proposals.events.list"], out
    assert out["journal"]["state"] == "OK", out
    assert any(e["action"] == "submit" for e in out["events"]), out


# ------------------------------------------------------------------- update
def test_skills_update_toggles_the_permissioned_skill(tmp_path, monkeypatch):
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    permissions_store(cfg).install_skill(_manifest(), requested_by="dashboard:user",
                                         confirmed=True)
    out = _registry().handle(cfg, "skills.update",
                             {"skill_id": "demo-cfg", "enabled": False,
                              "confirmed": True})
    assert out["code"] == SAFE["skills.update"], out
    assert out["skill"]["status"] == "disabled", out


# ---------------------------------------------------------- typed refusals
def test_operator_only_refusals(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method, code in OPS.items():
        out = reg.handle(cfg, method, {})
        assert out["state"] == "UNAVAILABLE", (method, out)
        assert out["code"] == code, (method, out)


def test_missing_required_params_fail_closed(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "skills.proposals.apply", {"confirmed": True})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value
    with pytest.raises(GatewayError) as exc2:
        reg.handle(cfg, "skills.proposals.requestRevision",
                   {"confirmed": True})
    assert exc2.value.code == "GATEWAY_BAD_PARAMS", exc2.value
