"""Focused round-trips for the journal-backed skill/toolset mutation contract.

Covers the isolated dashboard-sweep failures: skill create/install/remove and
the toolset enable/disable toggle. Each mutation must
  * validate the request (license required for manifests),
  * stage a 409 ``needs-confirm`` envelope with a **populated**
    ``confirmation_id`` on the shared approval journal,
  * settle exactly once and apply idempotently through the same backend the
    CLI/voice use, and
  * reflect persisted state on the read path.

Hermetic: tmp XDG state only; no network.
"""
from __future__ import annotations

import hashlib

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _estop(cfg, latched: bool):
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=latched, global_disable=False, issued_by="cli:user")
    return store


_CONTENT = "# Demo\n\nDo the thing.\n"


def _manifest(skill_id: str = "demo-skill", **over):
    man = {
        "skill_id": skill_id,
        "name": skill_id,
        "version": "1.0.0",
        "description": "demo skill",
        "source": "qubit-dashboard",
        "revision": "1",
        "license": "MIT",
        "content_hash": hashlib.sha256(_CONTENT.encode()).hexdigest(),
        "capabilities": ["skill.demo"],
        "scopes": ["skill:read"],
        "tier": "reversible",
        "rollback": "remove the skill",
    }
    man.update(over)
    return man


# ---------------------------------------------------------------------------
# Skill create
# ---------------------------------------------------------------------------
def test_skill_create_stage_settle_round_trip(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    staged = V.stage_skill_mutation(
        cfg, "create", {"name": "demo-skill", "content": _CONTENT,
                        "license": "MIT", "category": "qubit"},
        actor="dashboard:user", session="dashboard")
    assert staged["state"] == "needs-confirm"
    assert staged["code"] == "CONFIRM_REQUIRED"
    assert staged["http"] == 409
    assert staged["confirmation_id"]          # populated, not ""
    assert staged["risk"] == "elevated"
    # nothing installed before settle
    assert permissions_store(cfg).skills() == []

    # the pending banner can see the real id
    pending = V.pending_skill_mutations(cfg)
    assert [p["id"] for p in pending] == [staged["confirmation_id"]]

    done = V.settle_skill_mutation(cfg, staged["confirmation_id"],
                                   actor="dashboard:user", session="dashboard")
    assert done["state"] == "executed" and done["http"] == 200
    assert done["skill_id"] == "demo-skill"

    reopened = permissions_store(cfg)
    rec = reopened.skill("demo-skill")
    assert rec is not None and rec.status == "staged"
    assert rec.manifest.license == "MIT"

    # settle is consume-once
    again = V.settle_skill_mutation(cfg, staged["confirmation_id"])
    assert again["ok"] is False and again["code"] == "CONFIRMATION_SETTLED"

    # staging the same manifest again is idempotent (already installed)
    pre = V.stage_skill_mutation(
        cfg, "create", {"name": "demo-skill", "content": _CONTENT,
                        "license": "MIT"})
    assert pre["state"] == "executed" and pre["already"] is True


def test_skill_create_requires_license(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    out = V.stage_skill_mutation(
        cfg, "create", {"name": "no-license", "content": _CONTENT})
    assert out["ok"] is False and out["code"] == "LICENSE_REQUIRED"
    assert out["http"] == 400 and not out.get("confirmation_id")
    assert permissions_store(cfg).skills() == []
    assert V.pending_skill_mutations(cfg) == []


def test_skill_create_conflicting_manifest_refused(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    staged = V.stage_skill_mutation(
        cfg, "create", {"name": "conflict", "content": _CONTENT,
                        "license": "MIT"})
    assert V.settle_skill_mutation(cfg, staged["confirmation_id"])["state"] \
        == "executed"
    other = V.stage_skill_mutation(
        cfg, "create", {"name": "conflict", "content": _CONTENT + "changed\n",
                        "license": "MIT"})
    assert other["ok"] is False and other["code"] == "SKILL_EXISTS"
    assert other["http"] == 409


# ---------------------------------------------------------------------------
# Skill install (resolved manifest)
# ---------------------------------------------------------------------------
def test_skill_install_manifest_round_trip(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    staged = V.stage_skill_mutation(cfg, "install", {"manifest": _manifest(
        "hub-skill", description="reviewed")})
    assert staged["state"] == "needs-confirm"
    assert staged["confirmation_id"] and staged["risk"] == "elevated"
    assert permissions_store(cfg).skills() == []

    done = V.settle_skill_mutation(cfg, staged["confirmation_id"])
    assert done["state"] == "executed" and done["skill_id"] == "hub-skill"
    assert permissions_store(cfg).skill("hub-skill").status == "staged"

    # idempotent re-stage
    pre = V.stage_skill_mutation(cfg, "install",
                                 {"manifest": _manifest("hub-skill",
                                                        description="reviewed")})
    assert pre["state"] == "executed" and pre["already"] is True


def test_skill_install_missing_license_refused(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest("bad-license")
    man["license"] = ""
    out = V.stage_skill_mutation(cfg, "install", {"manifest": man})
    assert out["code"] == "LICENSE_REQUIRED" and out["http"] == 400
    assert V.pending_skill_mutations(cfg) == []


def test_skill_install_via_handle_with_confirmation_id(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    staged = V.handle_skill_mutation(cfg, "install", {"manifest":
                                                      _manifest("h2-skill")})
    assert staged["state"] == "needs-confirm"
    done = V.handle_skill_mutation(cfg, "install", {},
                                   confirmation_id=staged["confirmation_id"])
    assert done["state"] == "executed" and done["skill_id"] == "h2-skill"


# ---------------------------------------------------------------------------
# Skill remove
# ---------------------------------------------------------------------------
def test_skill_remove_round_trip_and_idempotent(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    staged = V.stage_skill_mutation(cfg, "install", {"manifest":
                                                     _manifest("removable")})
    V.settle_skill_mutation(cfg, staged["confirmation_id"])
    assert permissions_store(cfg).skill("removable") is not None

    rem = V.stage_skill_mutation(cfg, "remove", {"skill_id": "removable"})
    assert rem["state"] == "needs-confirm" and rem["risk"] == "destructive"
    assert rem["confirmation_id"]
    done = V.settle_skill_mutation(cfg, rem["confirmation_id"])
    assert done["state"] == "executed" and done["removed"] is True
    assert permissions_store(cfg).skill("removable") is None

    # removing again is an idempotent success, not an error
    pre = V.stage_skill_mutation(cfg, "remove", {"skill_id": "removable"})
    assert pre["state"] == "executed" and pre["already"] is True


def test_settle_if_owned_falls_through_for_foreign_id(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    assert V.settle_skill_mutation_if_owned(cfg, "not-a-real-id") is None

    staged = V.stage_skill_mutation(cfg, "install", {"manifest":
                                                     _manifest("owned")})
    out = V.settle_skill_mutation_if_owned(cfg, staged["confirmation_id"])
    assert out is not None and out["state"] == "executed"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_skill_mutation_actor_and_estop_guards(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    refused = V.stage_skill_mutation(cfg, "install",
                                     {"manifest": _manifest()}, actor="tool")
    assert refused["code"] == "ACTOR_REFUSED" and refused["http"] == 403

    store = _estop(cfg, True)
    try:
        latched = V.stage_skill_mutation(
            cfg, "install", {"manifest": _manifest()},
            actor="voice:user", session="voice")
        assert latched["code"] == "ESTOP_LATCHED" and latched["http"] == 403
    finally:
        store.set(emergency_stop=False, global_disable=False, issued_by="cli:user")


def test_skill_mutation_session_binding(tmp_path, monkeypatch):
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    staged = V.stage_skill_mutation(cfg, "install", {"manifest":
                                                     _manifest("sess")},
                                    session="dashboard")
    wrong = V.settle_skill_mutation(cfg, staged["confirmation_id"],
                                    session="other")
    assert wrong["code"] == "SESSION_MISMATCH"
    # a wrong-session attempt must NOT consume the id
    ok = V.settle_skill_mutation(cfg, staged["confirmation_id"])
    assert ok["state"] == "executed"


# ---------------------------------------------------------------------------
# Toolset toggle
# ---------------------------------------------------------------------------
def test_toolset_toggle_persists_and_reflects_enabled(tmp_path, monkeypatch):
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    assert TS.toolset_enabled(cfg, "web") is True          # default
    rows = {r["name"]: r["enabled"] for r in TS.toolset_rows(cfg, ["web"])}
    assert rows["web"] is True

    staged = TS.toolset_disable(cfg, "web")
    assert staged["state"] == "needs-confirm"
    assert staged["code"] == "CONFIRM_REQUIRED" and staged["http"] == 409
    assert staged["confirmation_id"] and staged["risk"] == "reversible"
    assert TS.toolset_enabled(cfg, "web") is True          # not yet settled

    done = TS.settle_toolset_mutation(cfg, staged["confirmation_id"])
    assert done["state"] == "executed" and done["enabled"] is False
    assert TS.toolset_enabled(cfg, "web") is False
    assert TS.toolset_states(cfg) == {"web": False}
    assert TS.toolset_rows(cfg, ["web"])[0]["enabled"] is False

    # consume-once + idempotent restage
    assert TS.settle_toolset_mutation(cfg, staged["confirmation_id"])["code"] \
        == "CONFIRMATION_SETTLED"
    assert TS.toolset_disable(cfg, "web")["already"] is True

    # re-enable persists too
    staged_on = TS.toolset_enable(cfg, "web")
    assert staged_on["risk"] == "elevated"
    assert TS.settle_toolset_mutation(cfg, staged_on["confirmation_id"]) \
        ["enabled"] is True
    assert TS.toolset_enabled(cfg, "web") is True


def test_toolset_state_survives_reopen(tmp_path, monkeypatch):
    from pathlib import Path

    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = TS.toolset_disable(cfg, "files")
    TS.settle_toolset_mutation(cfg, staged["confirmation_id"])
    # a fresh read (new store load) still reports disabled
    assert TS.toolset_enabled(cfg, "files") is False
    assert (Path(cfg.state_dir()) / TS.TOOLSET_STORE_NAME).exists()


def test_toolset_plugin_runtime_public_api(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    staged = PR.toolset_disable(cfg, "web")
    assert staged["confirmation_id"]
    assert PR.settle_toolset_mutation_if_owned(
        cfg, staged["confirmation_id"])["state"] == "executed"
    assert PR.toolset_enabled(cfg, "web") is False
    assert PR.toolset_doctor(cfg)["store_ok"] is True


def test_toolset_handle_and_guards(tmp_path, monkeypatch):
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = TS.handle_toolset_mutation(cfg, "web", False)
    done = TS.handle_toolset_mutation(cfg, "web", False,
                                      confirmation_id=staged["confirmation_id"])
    assert done["state"] == "executed" and done["enabled"] is False

    bad = TS.stage_toolset_mutation(cfg, "web", True, actor="tool")
    assert bad["code"] == "ACTOR_REFUSED"
    malformed = TS.stage_toolset_mutation(cfg, "BAD NAME", True)
    assert malformed["code"] == "MALFORMED"

    store = _estop(cfg, True)
    try:
        latched = TS.stage_toolset_mutation(cfg, "web", True,
                                            actor="dashboard:user")
        assert latched["code"] == "ESTOP_LATCHED"
    finally:
        store.set(emergency_stop=False, global_disable=False, issued_by="cli:user")
