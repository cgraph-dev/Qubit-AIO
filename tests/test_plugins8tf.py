"""Qubit plugin installs behind the Qubit skill contract.

Proves the safe subset is real (local reviewed manifests install staged
DISABLED through broker-adjacent permission policy with confirmation, audit,
redaction, e-stop, and rollback) and the unsafe subset is typed-refused
(remote Git/index identifiers are never fetched; missing/invalid/oversized
manifests never traceback).
"""
from __future__ import annotations

import io
import json
import socket
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _manifest_dict(**overrides):
    base = dict(
        skill_id="acme.notes", version="1.0.0", name="Acme Notes",
        description="synthetic plugin", source="https://example.test/acme-notes",
        revision="deadbeef", license="MIT", content_hash="0" * 64,
        capabilities=("notes.read",), scopes=("notes",),
        tier="read_only", sensitivity="internal", approval_policy="confirm",
        timeout_s=30.0, resource_limits={"max_bytes": 4096},
        audit_events=("skill.run",), compatibility="linux",
        rollback="remove restores prior manifest state")
    base.update(overrides)
    return base


def _manifest_file(tmp_path, **overrides):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps(_manifest_dict(**overrides)))
    return p


class _Args:
    def __init__(self, cmd, **kw):
        self.skills_command = cmd
        self.manifest = kw.get("manifest", "")
        self.skill_id = kw.get("skill_id", "")
        self.confirm = kw.get("confirm", False)
        self.dry_run = kw.get("dry_run", False)
        self.backup = kw.get("backup", None)


def _run(cfg, args):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_skills(cfg, args)
    return rc, json.loads(buf.getvalue())


def _store(cfg):
    from voicebridge.permissions import permissions_store

    return permissions_store(cfg)


# ---------------------------------------------------------------- classify
def test_classify_local_remote_missing(tmp_path):
    from voicebridge import plugins as PLUG

    local = tmp_path / "p.json"
    local.write_text("{}")
    assert PLUG.classify_install_source(str(local))["kind"] == "local-file"
    assert PLUG.classify_install_source("anpicasso/qubit-plugin-x")["kind"] == "remote"
    assert PLUG.classify_install_source(
        "https://github.com/a/b.git")["kind"] == "remote"
    assert PLUG.classify_install_source("some-index-name")["kind"] == "remote"
    assert PLUG.classify_install_source("/nonexistent-8tf/m.json")["kind"] == "missing-file"
    assert PLUG.classify_install_source("./rel/m.json")["kind"] == "missing-file"
    assert PLUG.classify_install_source("")["kind"] == "missing-file"


# ------------------------------------------------- local install is real
def test_install_stages_disabled_with_audit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    rc, out = _run(cfg, _Args("install", manifest=str(man), confirm=True))
    assert rc == 0, out
    assert out["state"] == "OK" and out["staged"] == "staged"
    store = _store(cfg)
    rec = store.skill("acme.notes")
    assert rec is not None and rec.status == "staged"
    assert rec.status != "active", "staged plugins grant no authority"
    assert any("skill.install" in str(e) for e in store.audit())


def test_install_requires_confirmation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    rc, out = _run(cfg, _Args("install", manifest=str(man), confirm=False))
    assert rc == 3
    assert _store(cfg).skill("acme.notes") is None, "nothing staged without confirm"


def test_install_dry_run_changes_nothing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    rc, out = _run(cfg, _Args("install", manifest=str(man), confirm=True,
                              dry_run=True))
    assert rc == 0 and out["state"] == "preview"
    assert _store(cfg).skill("acme.notes") is None


def test_staged_plugin_cannot_back_integrations(tmp_path, monkeypatch):
    from voicebridge.websites import validate_manifest_link, WebsiteIntegrationError

    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    rc, _ = _run(cfg, _Args("install", manifest=str(man), confirm=True))
    assert rc == 0
    with pytest.raises(WebsiteIntegrationError, match="SKILL_NOT_ACTIVE"):
        validate_manifest_link("acme.notes", _store(cfg))


def test_enable_disable_remove_rollback_lifecycle(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    assert _run(cfg, _Args("install", manifest=str(man), confirm=True))[0] == 0
    # enable needs its own confirmation and records rollback state
    assert _run(cfg, _Args("enable", skill_id="acme.notes"))[0] == 3
    rc, out = _run(cfg, _Args("enable", skill_id="acme.notes", confirm=True))
    assert rc == 0 and out["status"] == "active"
    rc, out = _run(cfg, _Args("rollback", skill_id="acme.notes", confirm=True))
    assert rc == 0 and out["status"] == "staged", out
    rc, out = _run(cfg, _Args("remove", skill_id="acme.notes", confirm=True))
    assert rc == 0 and out["removed"] == "acme.notes"
    assert _store(cfg).skill("acme.notes") is None


def test_restart_persistence(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    assert _run(cfg, _Args("install", manifest=str(man), confirm=True))[0] == 0
    assert _store(cfg).skill("acme.notes") is not None, \
        "staged record survives store reload"


# ------------------------------------------------- unsafe subset refused
@pytest.mark.parametrize("identifier", [
    "anpicasso/qubit-plugin-chrome-profiles",
    "https://github.com/anpicasso/qubit-plugin-x.git",
    " hermes-pack ",
])
def test_remote_identifiers_never_fetched(tmp_path, monkeypatch, identifier):
    cfg = _cfg(tmp_path, monkeypatch)

    def _no_net(*a, **k):
        raise AssertionError("network must never be touched")

    monkeypatch.setattr(socket, "create_connection", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_net)
    rc, out = _run(cfg, _Args("install", manifest=identifier, confirm=True))
    assert rc == 2, out
    assert out["code"] == "BLOCKED_REMOTE_PLUGIN", out
    assert "allowlist review" in out["explanation"]
    assert _store(cfg).skills() == [], "nothing installed from remote"


def test_token_bearing_identifier_redacted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ident = "https://deployer:ghp_s3cr3tvalue123@example.test/x?token=abc123"
    rc, out = _run(cfg, _Args("install", manifest=ident, confirm=True))
    assert rc == 2
    blob = json.dumps(out)
    assert "ghp_s3cr3tvalue123" not in blob and "abc123" not in blob
    assert "[REDACTED]" in blob


def test_missing_file_is_typed_not_traceback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, _Args("install",
                              manifest="/nonexistent-8tf/m.json", confirm=True))
    assert rc == 2 and out["code"] == "PLUGIN_MANIFEST_IO", out


def test_invalid_json_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    rc, out = _run(cfg, _Args("install", manifest=str(bad), confirm=True))
    assert rc == 2 and out["code"] == "PLUGIN_MANIFEST_INVALID", out


def test_schema_invalid_manifest_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    bad = _manifest_file(tmp_path, license="", content_hash="zzz")
    rc, out = _run(cfg, _Args("install", manifest=str(bad), confirm=True))
    assert rc == 2, out
    assert _store(cfg).skill("acme.notes") is None


def test_oversize_manifest_refused_before_read(tmp_path, monkeypatch):
    from voicebridge import plugins as PLUG

    cfg = _cfg(tmp_path, monkeypatch)
    big = tmp_path / "big.json"
    with open(big, "wb") as fh:
        fh.write(b'{"skill_id": "x",')
        fh.truncate(PLUG.MAX_MANIFEST_BYTES + 16)
    rc, out = _run(cfg, _Args("install", manifest=str(big), confirm=True))
    assert rc == 2 and out["code"] == "PLUGIN_MANIFEST_TOO_LARGE", out
    assert _store(cfg).skills() == []


def test_check_validates_without_persisting(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    rc, out = _run(cfg, _Args("check", manifest=str(man)))
    assert rc == 0 and out["valid"] is True
    assert _store(cfg).skills() == []


# ------------------------------------------------- emergency stop
def test_estop_latch_refuses_mutations_but_not_reads(tmp_path, monkeypatch):
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    man = _manifest_file(tmp_path)
    assert _run(cfg, _Args("install", manifest=str(man), confirm=True))[0] == 0
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        rc, out = _run(cfg, _Args("enable", skill_id="acme.notes", confirm=True))
        assert rc == 3 and out["code"] == "PLUGIN_ESTOP", out
        rc, out = _run(cfg, _Args("install", manifest=str(man), confirm=True))
        assert rc == 3 and out["code"] == "PLUGIN_ESTOP", out
        assert _store(cfg).skill("acme.notes").status == "staged"
        rc, out = _run(cfg, _Args("list"))
        assert rc == 0 and out["state"] == "OK", "reads stay available"
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")


def test_corrupt_control_state_fails_closed(tmp_path, monkeypatch):
    from voicebridge import plugins as PLUG

    cfg = _cfg(tmp_path, monkeypatch)
    ctl = cfg.state_dir() / "power-control.json"
    ctl.parent.mkdir(parents=True, exist_ok=True)
    ctl.write_text("{corrupt")
    assert PLUG.estop_engaged(cfg) is True


# ------------------------------------------------- catalog + parser
def test_capability_catalog_reports_plugins_install_implemented():
    from voicebridge.capability_catalog import check

    out = check("plugins-install")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"


def test_skills_surface_in_parser_help():
    from voicebridge import cli as CLI

    text = CLI.build_parser().format_help()
    assert "skills" in text and "install" in text
