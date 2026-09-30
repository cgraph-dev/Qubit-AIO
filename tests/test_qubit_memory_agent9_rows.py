"""Agent 9 — Qubit-owned memory / Brain / proxy / OpenCode / active-memory.

Production-caller closeout for the Agent-9 rows: the real voice backends
(``voice_commands._b_memory``, ``_b_brain``, ``_b_proxy``, ``_b_active_memory``)
over the single memory authority (``MemoryStore``), the Brain registry, the
deny-by-default egress broker, and the managed OpenCode worktree/confinement
lifecycle. Hermetic: tmp state, no network, no credentials, no live server.

Covers behavioral, negative, persistence, recovery, audit, and fail-closed
paths. It does not create a second store, secret value, or forwarding server.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch, body: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + body)
    return C.Config.load(p)


def _voice(cfg, entry_id, session="v-agent9", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


# ------------------------------------------------------------------- memory
def test_memory_authority_roundtrip_persistence_and_negative(tmp_path,
                                                             monkeypatch):
    from voicebridge.memory import MemoryStore

    cfg = _cfg(tmp_path, monkeypatch)
    remembered = _voice(cfg, "memory_remember", text="the sky is blue")
    assert remembered.kind == "executed", remembered.speech

    hit = _voice(cfg, "memory_search", query="sky")
    results = hit.receipt["result"]["results"]
    assert [r["text"] for r in results] == ["the sky is blue"]
    memory_id = results[0]["id"]

    # One authority: the voice backend wrote the canonical state-dir DB.
    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        assert store.get(memory_id)["text"] == "the sky is blue"
    finally:
        store.close()

    forgotten = _voice(cfg, "memory_forget", memory_id=memory_id)
    assert forgotten.receipt["result"]["forgotten"] is True
    assert _voice(cfg, "memory_search", query="sky").receipt["result"][
        "results"] == []

    # Negative: secret-shaped text is refused and never persisted.
    leak = "sk-abcdefghijklmnop123456"
    out = _voice(cfg, "memory_remember", text=f"token {leak}")
    assert out.kind == "failed"
    assert "SecretRejected" in str(out.receipt.get("code", ""))
    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        assert store.search("token") == []
    finally:
        store.close()


# ------------------------------------------------------------- active memory
def test_active_memory_policy_recall_and_escalate_state_transitions(
        tmp_path, monkeypatch):
    from voicebridge import active_memory as AM
    from voicebridge.memory import MemoryStore

    def _am(cfg, entry, **args):
        result = _voice(cfg, entry, **args).receipt["result"]
        return result["active_memory"]

    cfg = _cfg(tmp_path, monkeypatch)
    disabled = _am(cfg, "recall_query", query="planet")
    assert disabled["state"] == "DISABLED"

    enabled = AM.policy_set(cfg, enabled=True, min_score=0.0, max_items=3,
                            confirmed=True)
    assert enabled["state"] == "OK"

    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        rec = store.remember("Jupiter is the largest planet", tier="recall",
                             confidence=0.5)
    finally:
        store.close()

    body = _am(cfg, "recall_query", query="planet")
    assert body["state"] == "OK"
    assert body["count"] == 1
    assert body["results"][0]["id"] == rec["id"]
    # Snippets are redacted views; provenance is present.
    assert body["results"][0]["refs"]

    resolved = _am(cfg, "recall_resolve", query="planet")
    assert resolved["state"] == "OK"
    assert resolved["context"]

    esc = _am(cfg, "recall_escalate", memory_id=rec["id"],
              reason="confirmed by operator")
    assert esc["state"] == "OK" and esc["tier"] == "working"

    # Persistence: a fresh store sees the tier + append-only history.
    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        assert store.get(rec["id"])["tier"] == "working"
        assert any(h["action"] == "update" for h in store.history(rec["id"]))
    finally:
        store.close()

    # Policy is persisted on disk (survives a reload).
    reloaded = AM.policy_get(cfg)
    assert reloaded["policy"]["enabled"] is True
    assert reloaded["policy"]["max_items"] == 3


def test_active_memory_estop_fails_closed(tmp_path, monkeypatch):
    from voicebridge import active_memory as AM

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(AM, "estop_engaged", lambda _c: True)
    for entry in ("recall_query", "recall_resolve", "recall_status"):
        result = _voice(cfg, entry, query="x").receipt["result"]
        body = result.get("active_memory", result)
        assert body.get("state") == "ESTOP", (entry, result)
    # policy_get stays readable but honestly reports the latched e-stop.
    policy = _voice(cfg, "recall_policy_get").receipt["result"][
        "active_memory"]
    assert policy["estop"] is True


# -------------------------------------------------------------------- proxy
def test_proxy_deny_by_default_registry_and_no_values(tmp_path, monkeypatch):
    from voicebridge import proxy_secrets as PXS
    from voicebridge import proxy_status as PS

    cfg = _cfg(tmp_path, monkeypatch,
               "proxy:\n  allowed_hosts:\n    - api.example.com\n")

    assert PS.status()["state"] == "NOT_RUNNING"
    assert PS.status()["forwards"] == 0
    refused = PS.refuse_start()
    assert refused["state"] == "REFUSED" and refused["code"] == PS.NO_SERVER_CODE

    # Deny by default: unknown hosts are never allowed, and a denied host
    # never resolves a secret value.
    monkeypatch.setenv("QUBIT_AGENT9_SECRET", "value-please-redact")
    reg = PXS.SecretRegistry()
    reg.register("api", "env:QUBIT_AGENT9_SECRET")
    assert PS.check_egress(cfg, "evil.example.com")["allowed"] is False
    with pytest.raises(PXS.EgressError) as ei:
        PS.substitute_egress(cfg, "{{secret:api}}", "evil.example.com",
                             registry=reg)
    assert ei.value.code == PXS.EGRESS_DENIED
    assert "value-please-redact" not in ei.value.detail

    allowed = PS.substitute_egress(cfg, "Auth: {{secret:api}}",
                                   "api.example.com", registry=reg)
    assert allowed == "Auth: value-please-redact"

    st = PS.egress_status(cfg, registry=reg)
    assert st["server"]["code"] == PS.NO_SERVER_CODE
    assert st["registry"]["secrets"] == [{"name": "api", "kind": "env"}]
    assert "value-please-redact" not in json.dumps(st)

    # Raw values are refused at the registry boundary (references only).
    with pytest.raises(PXS.EgressError) as ei:
        PXS.SecretRegistry().register("raw", "value-please-redact")
    assert ei.value.code == PXS.EGRESS_BAD_REFERENCE


# ----------------------------------------------------------------- opencode
def _require_git():
    if shutil.which("git") is None:
        pytest.skip("git not installed")


def _git_project(tmp_path, name="proj"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    (root / "main.py").write_text("x = 1\n")
    git = shutil.which("git")
    subprocess.run([git, "init", "-q"], cwd=root, check=False,
                   capture_output=True)
    subprocess.run([git, "config", "user.email", "a9@example.test"], cwd=root,
                   check=False, capture_output=True)
    subprocess.run([git, "config", "user.name", "agent9"], cwd=root,
                   check=False, capture_output=True)
    subprocess.run([git, "add", "."], cwd=root, check=False,
                   capture_output=True)
    subprocess.run([git, "commit", "-qm", "init"], cwd=root, check=False,
                   capture_output=True)
    return root


def test_opencode_session_repository_worktree_lifecycle_and_cleanup(tmp_path):
    """Prepare -> isolated write -> validate -> cleanup: the project
    checkout is never touched and the worktree registration is reaped."""
    _require_git()
    from voicebridge.opencode import workdir as WD

    root = _git_project(tmp_path, "session-repo")
    run_id = f"a9rows-{tmp_path.name}"
    wt = WD._prepare_workdir(root, disposable=True, run_id=run_id)
    base = wt.parent
    try:
        assert wt.is_dir() and wt != root
        assert base.name == f"vb-opencode-{run_id}"
        (wt / "scratch.txt").write_text("work\n")
        assert not (root / "scratch.txt").exists(), "root must stay clean"
        report = WD._validate_workdir(root, wt)
        assert report["ok"] is True, report
    finally:
        WD._cleanup_workdir(root, wt, run_id)
    assert not base.exists()
    # Recovery: the registered worktree admin entry is gone, not stale.
    listed = subprocess.run([shutil.which("git"), "worktree", "list"],
                            cwd=root, capture_output=True, text=True)
    assert str(wt) not in (listed.stdout or "")


def test_opencode_worktree_negative_paths(tmp_path):
    _require_git()
    from voicebridge.opencode import workdir as WD
    from voicebridge.opencode.policy import OpenCodeError

    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(OpenCodeError) as exc:
        WD._prepare_workdir(plain, disposable=True, run_id="a9rows-norepo")
    assert "DISPOSABLE_UNAVAILABLE" in str(exc.value)

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    wd = root / "wt"
    wd.mkdir()
    if hasattr(os, "symlink"):
        os.symlink(outside, wd / "escape")
        out = WD._validate_workdir(root, wd)
        assert out["ok"] is False and out["code"] == "SYMLINK_ESCAPE", out


def test_opencode_confinement_argv_is_fail_closed(tmp_path):
    from voicebridge.opencode import confinement as CF

    ok, _bwrap, reason = CF.confinement_available()
    assert isinstance(ok, bool) and isinstance(reason, str)

    workdir = tmp_path / "wt"
    workdir.mkdir()
    inner = ["/opencode-bin", "run", "--format", "json", "--dir",
             str(workdir), "task"]
    argv = CF.build_confinement_argv(
        "/usr/bin/bwrap", "/bin/true", workdir, inner,
        {"PATH": "/usr/bin", "HOME": "/home/operator"})
    joined = " ".join(argv)
    assert argv[0] == "/usr/bin/bwrap"
    assert "--ro-bind /usr /usr" in joined, "system is read-only"
    assert f"--bind {workdir} {workdir}" in joined, "workdir is rw"
    assert CF.CONFINED_HOME in argv and "HOME" in argv
    assert "/home/operator" not in argv, "operator HOME never leaks in"
    assert argv[-len(inner):] == inner
    assert "prlimit" in argv


# ------------------------------------------------------------------- brain
def test_brain_vault_remove_roundtrip_audit_and_negative(tmp_path, monkeypatch):
    from voicebridge import brain as BR
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        BR.principal_add(cfg, "owner", "person", "Owner", confirmed=True)
        staged = _voice(cfg, "brain_vault_set", name="main",
                        **{"ref": "env:ACME_KEY"})
        assert staged.kind == "executed", staged.speech
        assert BR.vault_list(cfg)["entries"][0]["reference"] == \
            "env:<reference>"

        # Negative: removal without confirmation is refused and persists
        # nothing.
        try:
            BR.vault_remove(cfg, "main")
        except BR.BrainError as exc:
            assert exc.code == BR.CONFIRM_CODE
        else:
            raise AssertionError("unconfirmed vault remove must fail")
        assert len(BR.vault_list(cfg)["entries"]) == 1

        removed = _voice(cfg, "brain_vault_remove", name="main")
        assert removed.kind == "executed", removed.speech
        assert removed.receipt["result"]["removed"] == "main"
        assert BR.vault_list(cfg)["entries"] == []

        # Negative: removing a missing entry is typed NOT_FOUND, not a crash.
        try:
            BR.vault_remove(cfg, "main", confirmed=True)
        except BR.BrainError as exc:
            assert exc.code == BR.NOT_FOUND_CODE
        else:
            raise AssertionError("missing vault entry must be NOT_FOUND")

        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "brain.vault.set" in blob and "brain.vault.remove" in blob
        assert "ACME_KEY" not in blob, "audit never carries the reference"
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_brain_onboard_apply_transitions_and_audit(tmp_path, monkeypatch):
    from voicebridge import brain as BR
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        plan = BR.onboard_plan()
        assert len(plan["sections"]) == 5

        answers = {"personal": {"display_name": "Ada"},
                   "organization": {"company": "Acme"}}
        preview = BR.onboard_apply(cfg, answers, dry_run=True)
        assert preview["state"] == "preview"
        assert not Path(cfg.state_dir(), "brain.json").exists()

        applied = BR.onboard_apply(cfg, answers, confirmed=True)
        assert applied["state"] == "OK"
        assert set(applied["applied"]) == {"personal", "organization"}
        # Real transition: state persisted and readable.
        assert BR.principal_list(cfg)["principals"]
        shown = BR.profile_show(cfg, "owner")["fields"]
        assert any(f["key"] == "display_name" for f in shown)
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "brain.onboard.apply" in blob
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_brain_profile_set_sweep_forget_transitions_and_audit(tmp_path,
                                                              monkeypatch):
    import time as _time

    from voicebridge import brain as BR
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        BR.principal_add(cfg, "owner", "person", "Owner", confirmed=True)
        BR.profile_set(cfg, "owner", "timezone", "Europe/Bucharest",
                       purpose="locale", trust="user", confirmed=True)
        listed = BR.profile_show(cfg, "owner")["fields"]
        assert listed[0]["key"] == "timezone"
        assert "Europe/Bucharest" not in json.dumps(listed), "masked"
        exported = BR.profile_export(cfg, "owner")
        assert exported["profiles"]["default"]["timezone"]["value"] == \
            "Europe/Bucharest"

        # Retention sweep removes only the expired field.
        BR.profile_set(cfg, "owner", "old_nick", "x", retention_days=0,
                       confirmed=True)
        _time.sleep(0.02)
        swept = BR.profile_sweep(cfg, confirmed=True)
        assert any("old_nick" in r for r in swept["removed"])
        assert not any("timezone" in r for r in swept["removed"])

        forgotten = BR.profile_forget(cfg, "owner", key="timezone",
                                      confirmed=True)
        assert forgotten["forgotten"] == "owner::timezone"
        assert BR.profile_show(cfg, "owner")["fields"] == []

        # Whole-identity forget is a distinct, audited transition.
        BR.profile_set(cfg, "owner", "locale", "ro", confirmed=True)
        wiped = BR.profile_forget(cfg, "owner", confirmed=True)
        assert "owner::default" in wiped["forgotten_profiles"]

        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        for event in ("brain.profile.set", "brain.profile.sweep",
                      "brain.profile.forget_field", "brain.profile.forget"):
            assert event in blob, event
        assert "Europe/Bucharest" not in blob, "audit never carries values"
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


# ------------------------------------------------------------------- OC-48
def test_workspace_markdown_memory_dream_and_provider_refusal(tmp_path,
                                                             monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    assert WS.write_note(cfg, "plan", "ship it")["state"] == "denied"
    out = WS.write_note(cfg, "plan", "ship it", tags=["v1"], confirmed=True)
    assert out["state"] == "OK" and out["created"] is True

    read = WS.read_note(cfg, "plan")
    assert read["note"]["body"] == "ship it"

    # Torn-index recovery rebuilds from durable note files.
    index = Path(WS.workspace_dir(cfg)) / WS.INDEX_NAME
    index.write_text("{corrupt")
    assert WS.list_notes(cfg)["count"] == 1

    dreamed = WS.dream(cfg, confirmed=True)
    assert dreamed["state"] == "OK"
    assert (Path(WS.workspace_dir(cfg)) / WS.DREAM_NAME).is_file()

    providers = WS.providers()
    by_name = {p["name"]: p for p in providers["providers"]}
    assert by_name["markdown-workspace"]["state"] == "AVAILABLE"
    for name in ("lancedb", "wiki", "honcho", "cloud-sync"):
        assert by_name[name]["state"] == "UNAVAILABLE"
        assert by_name[name]["code"] == WS.UNAVAILABLE_CODE
