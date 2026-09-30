"""Packet 8T-F — Qubit Brain profiles: multi-principal identity layer.

Proves principals never merge, memberships preserve data on removal,
fields carry provenance + retention, secret-class values are refused,
vault holds references only, origins bind strictly (homographs out),
onboarding previews offline, cross-principal reads stay scoped, and
audits never carry values.
"""
from __future__ import annotations

import io
import json
import socket
import subprocess
from contextlib import redirect_stdout
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _owner(cfg):
    from voicebridge import brain as BR

    BR.principal_add(cfg, "owner", "person", "Owner", confirmed=True)


def test_principals_never_merge_and_memberships(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    cfg = _cfg(tmp_path, monkeypatch)
    BR.principal_add(cfg, "maria", "employee", "Employee — Maria",
                     confirmed=True)
    BR.principal_add(cfg, "acme", "organization", "Acme — Finance",
                     confirmed=True)
    try:
        BR.principal_add(cfg, "maria", "employee", "Dupe", confirmed=True)
    except BR.BrainError as exc:
        assert exc.code == "BRAIN_EXISTS"
    else:
        raise AssertionError("duplicate principal must fail")
    out = BR.member_add(cfg, "acme", "maria", confirmed=True)
    assert out["membership"]["org"] == "acme"
    try:
        BR.member_add(cfg, "maria", "acme", confirmed=True)
    except BR.BrainError as exc:
        assert exc.code == "BRAIN_BAD_INPUT", "kinds enforced"
    else:
        raise AssertionError("kind mismatch must fail")
    BR.profile_set(cfg, "maria", "email", "m@example.test", confirmed=True)
    out = BR.member_remove(cfg, "acme", "maria", confirmed=True)
    assert "preserved" in out["note"]
    assert BR.profile_show(cfg, "maria")["fields"], \
        "member data survives removal"


def test_profile_provenance_forget_sweep_export(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    cfg = _cfg(tmp_path, monkeypatch)
    _owner(cfg)
    BR.profile_set(cfg, "owner", "timezone", "Europe/Bucharest",
                   purpose="locale", trust="user", retention_days=30,
                   confirmed=True)
    shown = BR.profile_show(cfg, "owner")
    assert shown["fields"][0]["trust"] == "user"
    assert shown["fields"][0]["retention_days"] == 30
    assert "Europe/Bucharest" not in json.dumps(shown), "listing masked"
    BR.profile_set(cfg, "owner", "old_nick", "x", retention_days=0,
                   confirmed=True)
    import time as _t

    _t.sleep(0.02)
    out = BR.profile_sweep(cfg, confirmed=True)
    assert any("old_nick" in r for r in out["removed"])
    assert not any("timezone" in r for r in out["removed"])
    assert BR.profile_export(cfg, "owner")["profiles"]["default"]["timezone"][
        "value"] == "Europe/Bucharest"
    BR.profile_forget(cfg, "owner", key="timezone", confirmed=True)
    assert BR.profile_show(cfg, "owner")["fields"] == []


def test_secret_class_refused_vault_refs_only(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    cfg = _cfg(tmp_path, monkeypatch)
    _owner(cfg)
    for bad in ("password", "api_key", "token", "cookie"):
        try:
            BR.profile_set(cfg, "owner", bad, "s3cr3t", confirmed=True)
        except BR.BrainError as exc:
            assert exc.code == "BRAIN_SECRET_CLASS", bad
        else:
            raise AssertionError(f"secret-class {bad} must fail")
    try:
        BR.vault_set(cfg, "main", "s3cr3t-raw-value", confirmed=True)
    except BR.BrainError as exc:
        assert exc.code == "BRAIN_SECRET_CLASS"
    else:
        raise AssertionError("raw vault values must fail")
    out = BR.vault_set(cfg, "main", "env:ACME_KEY", principal="owner",
                       confirmed=True)
    assert out["entry"]["reference"] == "env:<reference>"
    assert BR.vault_list(cfg)["entries"][0]["reference"] == "env:<reference>"
    blob = json.dumps(BR.vault_list(cfg))
    assert "ACME_KEY" not in blob


def test_account_binding_and_homograph(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    cfg = _cfg(tmp_path, monkeypatch)
    _owner(cfg)
    BR.account_add(cfg, "work", "mail.example.test", "owner",
                   identity="work", confirmed=True)
    BR.account_add(cfg, "personal", "mail.example.test", "owner",
                   identity="personal", confirmed=True)
    recs = BR.account_list(cfg)["accounts"]
    assert len(recs) == 2, "same origin may hold many accounts"
    assert {r["identity"] for r in recs} == {"work", "personal"}
    try:
        BR.account_add(cfg, "evil", "mаil.example.test", "owner",
                       confirmed=True)
    except BR.BrainError as exc:
        assert exc.code == "BRAIN_HOMOGRAPH", "IDN homograph refused"
    else:
        raise AssertionError("homograph must fail")
    assert BR.account_remove(cfg, "work", confirmed=True)["removed"] == "work"
    assert len(BR.account_list(cfg)["accounts"]) == 1


def test_cross_principal_isolation(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    cfg = _cfg(tmp_path, monkeypatch)
    _owner(cfg)
    BR.principal_add(cfg, "maria", "employee", "Maria", confirmed=True)
    BR.profile_set(cfg, "owner", "email", "o@example.test", confirmed=True)
    BR.profile_set(cfg, "maria", "email", "m@example.test", confirmed=True)
    assert BR.profile_show(cfg, "maria")["fields"][0]["key"] == "email"
    owner_fields = BR.profile_show(cfg, "owner")["fields"]
    assert all("m@example" not in json.dumps(f) for f in owner_fields)
    try:
        BR.profile_show(cfg, "ghost")
    except BR.BrainError as exc:
        assert exc.code == "BRAIN_NOT_FOUND"
    else:
        raise AssertionError("unknown principal must fail")


def test_onboarding_offline_preview_and_apply(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    def _no_net(*a, **k):
        raise AssertionError("onboarding is offline")

    monkeypatch.setattr(socket, "create_connection", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_net)
    cfg = _cfg(tmp_path, monkeypatch)
    plan = BR.onboard_plan()
    assert len(plan["sections"]) == 5
    assert any("skippable" in r for r in plan["rules"])
    answers = {"personal": {"display_name": "T", "email": "t@example.test"},
               "vault": {"entry": "x", "reference": "raw-value"}}
    try:
        BR.onboard_apply(cfg, answers, confirmed=True)
    except BR.BrainError as exc:
        assert exc.code == "BRAIN_SECRET_CLASS", \
            "onboarding enforces vault references"
    else:
        raise AssertionError("raw vault value must fail")
    preview = BR.onboard_apply(
        cfg, {"personal": {"display_name": "T"}}, dry_run=True)
    assert preview["state"] == "preview"
    out = BR.onboard_apply(cfg, {"personal": {"display_name": "T"}},
                           confirmed=True)
    assert out["state"] == "OK" and "personal" in out["applied"]
    assert BR.profile_show(cfg, "owner")["fields"]


def test_audit_never_carries_values(tmp_path, monkeypatch):
    from voicebridge import brain as BR
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        _owner(cfg)
        BR.profile_set(cfg, "owner", "email", "secret-mailbox-9@example.test",
                       confirmed=True)
        BR.vault_set(cfg, "k", "env:SOME_KEY", confirmed=True)
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "brain.profile.set" in blob and "brain.principal.add" in blob
        assert "secret-mailbox-9" not in blob and "SOME_KEY" not in blob
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_estop_and_cli_surface(tmp_path, monkeypatch):
    from voicebridge import brain as BR
    from voicebridge import cli as CLI
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _owner(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        assert BR.estop_engaged(cfg) is True
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")
    assert "brain" in CLI.build_parser().format_help()


def test_cli_onboard_flow(tmp_path, monkeypatch):
    import io as _io

    from voicebridge import cli as CLI
    from contextlib import redirect_stdout as _redir

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, **kw):
            self.brain_command = kw.get("cmd", "principal-list")
            self.__dict__.update(kw)

    buf = _io.StringIO()
    with _redir(buf):
        assert CLI._cmd_brain(cfg, _A(cmd="onboard")) == 0
    assert json.loads(buf.getvalue())["schema"] == "qubit-brain-onboard/1"
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"personal": {"display_name": "T"}}))
    buf = _io.StringIO()
    with _redir(buf):
        assert CLI._cmd_brain(cfg, _A(cmd="onboard",
                                      answers_file=str(answers))) == 0
    assert json.loads(buf.getvalue())["state"] == "preview"
    buf = _io.StringIO()
    with _redir(buf):
        rc = CLI._cmd_brain(cfg, _A(cmd="onboard", answers_file=str(answers),
                                    confirm=True))
    assert rc == 0
    assert "personal" in json.loads(buf.getvalue())["applied"]
