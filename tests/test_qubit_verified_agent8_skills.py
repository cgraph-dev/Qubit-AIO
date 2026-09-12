"""Agent 8 — skill manifest lifecycle through the production caller.

Closes the Qubit ``skills_*`` rows (list/show/enable/disable/remove/rollback)
by driving the exact backend the voice command registry and dashboard
dispatcher invoke (``voice_commands._b_skills``) over the same permission
store the CLI uses, with persistence and typed negatives.
"""
from __future__ import annotations

import pytest

from voicebridge import permissions as PERM
from voicebridge import voice_commands as VC
from voicebridge.capabilities import Tier
from voicebridge.websites import canonical_manifest_hash


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _Ctx:
    def __init__(self, cfg):
        self.cfg = cfg
        self.source = "voice"
        self.actor = "voice:user"
        self.session = "test-session"


def _manifest(**overrides):
    base = dict(
        skill_id="weather.editor", version="1.0.0", name="Weather Editor",
        description="synthetic skill", source="https://example.test/skills",
        revision="abc123", license="MIT", content_hash="",
        capabilities=("web.observe.reachability", "web.observe.metadata"),
        scopes=("web",), tier=Tier.READ_ONLY, sensitivity="internal",
        approval_policy="auto", timeout_s=30.0,
        resource_limits={"max_bytes": 4096}, audit_events=("skill.run",),
        compatibility="linux", rollback="remove restores prior manifest state")
    base.update(overrides)
    if not base.get("content_hash"):
        probe = dict(base)
        probe["tier"] = (base["tier"].value
                         if hasattr(base["tier"], "value") else base["tier"])
        probe.pop("content_hash", None)
        base["content_hash"] = canonical_manifest_hash(probe)
    return PERM.SkillManifest(**base)


def test_voice_backend_skill_lifecycle_and_persistence(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _Ctx(cfg)
    man = _manifest()

    perms = PERM.permissions_store(cfg)
    perms.install_skill(man, requested_by="cli:user", confirmed=True)
    perms.close()

    listed = VC._b_skills(ctx, {"action": "list"})
    assert [s["skill_id"] for s in listed["skills"]] == [man.skill_id]
    assert listed["skills"][0]["status"] == "staged"

    shown = VC._b_skills(ctx, {"action": "show", "skill_id": man.skill_id})
    assert shown["skill"]["manifest"]["version"] == "1.0.0"

    enabled = VC._b_skills(ctx, {"action": "enable", "skill_id": man.skill_id})
    assert enabled["status"] == "active"
    reopened = PERM.permissions_store(cfg)
    assert reopened.skill(man.skill_id).status == "active"
    reopened.close()

    disabled = VC._b_skills(ctx, {"action": "disable", "skill_id": man.skill_id})
    assert disabled["status"] == "disabled"

    rolled = VC._b_skills(ctx, {"action": "rollback", "skill_id": man.skill_id})
    assert rolled["status"] == "active"
    reopened = PERM.permissions_store(cfg)
    assert reopened.skill(man.skill_id).status == "active"
    reopened.close()

    removed = VC._b_skills(ctx, {"action": "remove", "skill_id": man.skill_id})
    assert removed == {"status": "removed", "skill_id": man.skill_id}
    reopened = PERM.permissions_store(cfg)
    assert reopened.skill(man.skill_id) is None
    reopened.close()


def test_voice_backend_skill_negatives(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _Ctx(cfg)
    with pytest.raises(PERM.PermissionError):
        VC._b_skills(ctx, {"action": "enable", "skill_id": "missing.skill"})
    with pytest.raises(ValueError):
        VC._b_skills(ctx, {"action": "bogus"})


def test_dashboard_remote_plugin_install_refused():
    from voicebridge import dashboard_ops as DO

    out = DO._extra_plugins_remote(None, {"identifier": "git:evil"},
                                   "dashboard:user")
    assert out["state"] == "BLOCKED"
    assert out["code"] == "BLOCKED_REMOTE_PLUGIN"


def test_unconfirmed_mutations_are_refused_and_nothing_persists(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    perms = PERM.permissions_store(cfg)
    man = _manifest(skill_id="weather.unconfirmed")
    perms.install_skill(man, requested_by="cli:user", confirmed=True)
    with pytest.raises(PERM.ConfirmationRequiredError):
        perms.enable_skill(man.skill_id, requested_by="cli:user",
                           confirmed=False)
    assert perms.skill(man.skill_id).status == "staged"
    with pytest.raises(PERM.ConfirmationRequiredError):
        perms.remove_skill(man.skill_id, requested_by="cli:user",
                           confirmed=False)
    assert perms.skill(man.skill_id) is not None
    perms.close()
