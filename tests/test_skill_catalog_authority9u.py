"""9U — one skills authority: catalog/detail/edit/save/enable/disable/delete.

Ports the live Hermes dashboard skills behavior (``get_skills`` lists
disabled skills with ``enabled: false``; content reads the raw SKILL.md
resolved by ``_find_skill``; create/update/delete go through the fork's
skill manager; toggle flips the vendored disabled set) and proves the
reported split is closed: every catalog entry resolves through the detail
path, edits persist to Qubit-owned storage, and enable/disable persists.

Hermetic: tmp XDG state + tmp QUBIT_HOME only; no network.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    qhome = tmp_path / "qhome"
    qhome.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p), qhome


def _seed_skill(qhome, rel: str, body: str = "Do the thing.\n") -> object:
    d = qhome / "skills" / rel
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(
        f"---\nname: {d.name}\ndescription: A test skill.\n---\n\n{body}")
    return md


_AGENTMAIL_BODY = (
    "---\nname: agentmail\ndescription: Send agent mail.\n---\n\n"
    "Send mail via the agent mailbox.\n")


def test_catalog_shows_disabled_with_enabled_false(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")
    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    assert names["demo"]["enabled"] is True

    SC.set_skill_enabled(cfg, "demo", False, requested_by="test:user")
    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    # Live Hermes behavior: disabled skills stay listed with enabled:false,
    # they are not hidden from the catalog.
    assert names["demo"]["enabled"] is False

    SC.set_skill_enabled(cfg, "demo", True, requested_by="test:user")
    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    assert names["demo"]["enabled"] is True


def test_detail_resolves_nested_catalog_skill_like_agentmail(tmp_path,
                                                             monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    md = qhome / "skills" / "email" / "agentmail"
    md.mkdir(parents=True, exist_ok=True)
    (md / "SKILL.md").write_text(_AGENTMAIL_BODY)

    res = SC.read_skill_content(cfg, "agentmail")
    assert res is not None
    assert res["content"] == _AGENTMAIL_BODY
    assert res["path"] == str(md / "SKILL.md")
    assert res["origin"] == "catalog"

    shown = SC.show_skill(cfg, "agentmail")
    assert shown is not None and shown["name"] == "agentmail"
    assert shown["path"] == str(md / "SKILL.md")


def test_detail_missing_returns_none(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, _qhome = _cfg(tmp_path, monkeypatch)
    assert SC.read_skill_content(cfg, "nope-missing") is None
    assert SC.show_skill(cfg, "nope-missing") is None


def test_edit_save_reload_round_trip(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")
    new = ("---\nname: demo\ndescription: Updated desc.\n---\n\nNew body.\n")
    out = SC.edit_skill_file(cfg, "demo", new, requested_by="test:user")
    assert out.get("success") is True
    assert (qhome / "skills" / "demo" / "SKILL.md").read_text() == new
    assert SC.read_skill_content(cfg, "demo")["content"] == new


def test_edit_invalid_and_missing_are_typed(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")
    with pytest.raises(SC.SkillAuthorityError) as exc:
        SC.edit_skill_file(cfg, "demo", "no frontmatter here",
                           requested_by="test:user")
    assert exc.value.code == SC.SkillAuthorityError.INVALID
    with pytest.raises(SC.SkillAuthorityError) as exc:
        SC.edit_skill_file(cfg, "ghost", "---\nname: ghost\n"
                           "description: x.\n---\n\nbody\n",
                           requested_by="test:user")
    assert exc.value.code == SC.SkillAuthorityError.NOT_FOUND


def test_enable_disable_persists_in_vendored_set(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")
    SC.set_skill_enabled(cfg, "demo", False, requested_by="test:user")

    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    qubit_runtime.use_qubit_home(cfg)
    from qubit_cli.skills_config import get_disabled_skills  # type: ignore
    from qubit_cli.config import load_config  # type: ignore

    assert "demo" in get_disabled_skills(load_config())


def test_create_then_detail_then_delete(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    body = ("---\nname: fresh\ndescription: A brand new test skill.\n---\n\n"
            "Fresh body.\n")
    out = SC.create_skill_file(cfg, "fresh", body, "qubit",
                               requested_by="test:user")
    assert out.get("success") is True
    res = SC.read_skill_content(cfg, "fresh")
    assert res is not None and res["content"] == body
    SC.delete_skill_file(cfg, "fresh", requested_by="test:user")
    assert SC.read_skill_content(cfg, "fresh") is None
    assert not (qhome / "skills" / "qubit" / "fresh").exists()


def test_handler_content_update_stage_settle_round_trip(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")

    code, out = HC.h_skill_content(cfg, {"name": "demo"}, {}, {})
    assert code == 200 and "Do the thing." in out["content"], out

    code, missing = HC.h_skill_content(cfg, {"name": "ghost"}, {}, {})
    assert code == 404 and missing["code"] == "NOT_FOUND", missing

    new = "---\nname: demo\ndescription: Saved via route.\n---\n\nRouted.\n"
    code, staged = HC.skill_update(cfg, {}, {"name": "demo", "content": new},
                                   {})
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged
    assert staged["confirmation_id"], staged  # populated, never ""

    code, done = HC.skill_update(
        cfg, {}, {"name": "demo", "content": new,
                  "confirmation_id": staged["confirmation_id"]}, {})
    assert code == 200 and done.get("ok") is True, done
    assert SC.read_skill_content(cfg, "demo")["content"] == new

    code, done = HC.skill_update(
        cfg, {}, {"name": "demo", "content": new, "confirm": True}, {})
    assert code == 200, done


def test_handler_create_stage_settle_and_delete_route(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import skill_catalog as SC

    cfg, _qhome = _cfg(tmp_path, monkeypatch)
    body = ("---\nname: routed\ndescription: Created through the route.\n---\n\n"
            "Routed body.\n")
    payload = {"manifest": {"skill_id": "routed", "name": "routed",
                            "instructions": body, "category": "qubit"}}
    code, staged = HC.skill_create(cfg, {}, dict(payload), {})
    assert code == 409 and staged["confirmation_id"], staged
    code, done = HC.skill_create(
        cfg, {}, dict(payload, confirmation_id=staged["confirmation_id"]), {})
    assert code == 200 and done.get("ok") is True, done
    assert SC.read_skill_content(cfg, "routed")["content"] == body

    code, staged = HC.skill_delete(cfg, {"name": "routed"}, {}, {})
    assert code == 409 and staged["confirmation_id"], staged
    code, done = HC.skill_delete(
        cfg, {"name": "routed",
              "confirmation_id": staged["confirmation_id"]}, {}, {})
    assert code == 200 and done.get("ok") is True, done
    assert SC.read_skill_content(cfg, "routed") is None

    code, missing = HC.skill_delete(cfg, {"name": "ghost", "confirm": True},
                                    {}, {})
    assert code == 404, missing


def test_permissioned_manifest_fallback_still_resolves(tmp_path, monkeypatch):
    import hashlib

    from voicebridge.capabilities import Tier
    from voicebridge import permissions as P
    from voicebridge import skill_catalog as SC

    cfg, _qhome = _cfg(tmp_path, monkeypatch)
    man = P.SkillManifest(
        skill_id="manifest-only", version="1.0.0", name="manifest-only",
        description="manifest text here", source="qubit-test", revision="1",
        license="MIT",
        content_hash=hashlib.sha256(b"x").hexdigest(),
        capabilities=("skill.local",), scopes=("skill:read",),
        tier=Tier.REVERSIBLE,
        sensitivity="internal", approval_policy="confirm", timeout_s=30.0,
        resource_limits={}, audit_events=("skill.install",),
        compatibility="", rollback="remove the skill")
    store = P.permissions_store(cfg)
    store.install_skill(man, requested_by="test:user", confirmed=True)
    res = SC.read_skill_content(cfg, "manifest-only")
    assert res is not None and res["origin"] == "permissioned"
    assert res["content"] == "manifest text here"


# ---------------------------------------------------------------------------
# Split-authority closure: dir basename OR frontmatter name, one resolver.
# ---------------------------------------------------------------------------
def _seed_fm_skill(qhome, rel: str, fm_name: str) -> object:
    d = qhome / "skills" / rel
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(
        f"---\nname: {fm_name}\ndescription: A test skill.\n---\n\nbody\n")
    return md


def test_resolver_matches_frontmatter_name_when_dir_differs(tmp_path,
                                                            monkeypatch):
    """The catalog exposes the frontmatter name; resolve_skill must accept it
    (dir basename saelens -> frontmatter sparse-autoencoder-training)."""
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    md = _seed_fm_skill(qhome, "mlops/saelens", "sparse-autoencoder-training")

    resolved = SC.resolve_skill(cfg, "sparse-autoencoder-training")
    assert resolved is not None and resolved["skill_dir"] == md.parent
    # The directory basename resolves to the SAME file.
    by_dir = SC.resolve_skill(cfg, "saelens")
    assert by_dir is not None and by_dir["skill_md"] == md

    detail = SC.read_skill_content(cfg, "sparse-autoencoder-training")
    assert detail["path"] == str(md)
    names = {e["name"] for e in SC.catalog_entries(cfg)}
    assert "sparse-autoencoder-training" in names
    assert SC.show_skill(cfg, "sparse-autoencoder-training")["path"] == str(md)


def test_edit_and_delete_resolve_frontmatter_name(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    md = _seed_fm_skill(qhome, "mlops/saelens", "sparse-autoencoder-training")
    new = ("---\nname: sparse-autoencoder-training\n"
           "description: Updated.\n---\n\nnew body\n")
    out = SC.edit_skill_file(cfg, "sparse-autoencoder-training", new,
                             requested_by="test:user")
    assert out.get("success") is True
    assert md.read_text() == new

    SC.delete_skill_file(cfg, "sparse-autoencoder-training",
                         requested_by="test:user")
    assert not md.parent.exists()
    assert SC.resolve_skill(cfg, "sparse-autoencoder-training") is None


def test_toggle_keys_disabled_set_by_frontmatter_name(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC
    from voicebridge import qubit_runtime

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_fm_skill(qhome, "mlops/saelens", "sparse-autoencoder-training")
    rec = SC.set_skill_enabled(cfg, "sparse-autoencoder-training", False,
                               requested_by="test:user")
    assert rec["skill_id"] == "sparse-autoencoder-training"

    qubit_runtime.ensure_path()
    qubit_runtime.use_qubit_home(cfg)
    from qubit_cli.skills_config import get_disabled_skills  # type: ignore
    from qubit_cli.config import load_config  # type: ignore

    disabled = set(get_disabled_skills(load_config()) or [])
    assert "sparse-autoencoder-training" in disabled
    assert "saelens" not in disabled  # the vendored loader matches the fm name


def test_catalog_drops_manifest_only_but_keeps_real_permissioned(tmp_path,
                                                                 monkeypatch):
    """A permissioned record with no SKILL.md is NOT a catalog row (phantom
    neutralized); a permissioned record WITH a file stays, origin permissioned."""
    import hashlib

    from voicebridge.capabilities import Tier
    from voicebridge import permissions as P
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)

    def _man(sid):
        return P.SkillManifest(
            skill_id=sid, version="1.0.0", name=sid,
            description=f"permissioned {sid}", source="qubit-test",
            revision="1", license="MIT",
            content_hash=hashlib.sha256(sid.encode()).hexdigest(),
            capabilities=("skill.local",), scopes=("skill:read",),
            tier=Tier.REVERSIBLE, sensitivity="internal",
            approval_policy="confirm", timeout_s=30.0, resource_limits={},
            audit_events=("skill.install",), compatibility="",
            rollback="remove the skill")

    store = P.permissions_store(cfg)
    store.install_skill(_man("phantom-only"), requested_by="test:user",
                        confirmed=True)
    store.install_skill(_man("real-skill"), requested_by="test:user",
                        confirmed=True)
    _seed_fm_skill(qhome, "qubit/real-skill", "real-skill")

    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    assert "phantom-only" not in names
    assert names["real-skill"]["origin"] == "permissioned"
    # Detail of the manifest-only id still resolves through the store fallback.
    assert SC.read_skill_content(cfg, "phantom-only")["origin"] == "permissioned"


def test_merged_view_matches_agent_visible_set(tmp_path, monkeypatch):
    """N14: ``qubit skills list`` (``merged_store_view``) must agree with the
    agent's directory scan. A permissioned record with no on-disk SKILL.md is a
    capability grant, not a listed skill (the phantom ``qubit-boards`` row);
    one with a real file stays, in its permission-record shape."""
    import hashlib

    from voicebridge.capabilities import Tier
    from voicebridge import permissions as P
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)

    def _man(sid):
        return P.SkillManifest(
            skill_id=sid, version="1.0.0", name=sid,
            description=f"permissioned {sid}", source="qubit-test",
            revision="1", license="MIT",
            content_hash=hashlib.sha256(sid.encode()).hexdigest(),
            capabilities=("skill.local",), scopes=("skill:read",),
            tier=Tier.REVERSIBLE, sensitivity="internal",
            approval_policy="confirm", timeout_s=30.0, resource_limits={},
            audit_events=("skill.install",), compatibility="",
            rollback="remove the skill")

    store = P.permissions_store(cfg)
    store.install_skill(_man("grant-only"), requested_by="test:user",
                        confirmed=True)
    store.install_skill(_man("real-skill"), requested_by="test:user",
                        confirmed=True)
    _seed_fm_skill(qhome, "qubit/real-skill", "real-skill")

    view = {r["skill_id"]: r for r in SC.merged_store_view(cfg)}
    assert "grant-only" not in view          # not agent-visible -> not listed
    assert "real-skill" in view              # on disk -> listed
    assert view["real-skill"]["status"] == "staged"  # permission-record shape
    # The merged view is exactly the agent-visible catalog set.
    catalog_names = {e["name"] for e in SC.catalog_entries(cfg)}
    assert set(view) == catalog_names
