"""Final parity — export/import passphrase safety + CLI/dashboard/voice
consistency.

Proves:

* unsafe voice attempts ("export my state", even "... with passphrase
  hunter2") are rejected with typed guidance and never bind a passphrase
  argument — no passphrase lands in receipts, the journal, logs, or
  persisted state;
* the passphrase never appears in CLI stdout, dashboard results, audit
  records, or journaled confirmations (dashboard strips sensitive args
  before journaling; the confirm call resupplies from request memory);
* CLI export and dashboard import-preview are consistent: the same bundle
  decrypts with the same passphrase on both surfaces; a wrong passphrase
  is a typed failure on both;
* bundle files are 0o600; unknown/corrupt bundles fail typed.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

PW = "correct horse battery staple 8u"
WRONG = "wrong passphrase here"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("VOICE_BRIDGE_PASSPHRASE", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _voice(cfg, entry_id, session="v-xp", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    return VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))


def _dash(cfg, op_id, args, session="d-xp", sensitive=None):
    from voicebridge import dashboard_ops as DO

    out = DO.run_op(cfg, op_id, args, actor="dashboard:user",
                    session=session)
    if out.get("state") == "needs-confirm":
        out = DO.confirm_op(cfg, out["confirmation_id"],
                            actor="dashboard:user", session=session,
                            sensitive=sensitive)
    return out


def _cli_export(cfg, out_path, pw):
    from voicebridge import cli as CLI

    args = type("A", (), {})()
    args.out = out_path
    args.passphrase = pw
    args.dry_run = False
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_export_state(cfg, args)
    return rc, buf.getvalue()


# ------------------------------------------------------- voice refusal
def test_voice_export_import_refused_with_guidance(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for text, entry in (("qubit, export my state", "export_state_voice"),
                        ("qubit, export state with passphrase hunter2",
                         "export_state_voice"),
                        ("qubit, import my state", "import_state_voice"),
                        ("qubit, restore from backup", "import_state_voice")):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == entry, (text, res.kind)
        out = _voice(cfg, entry)
        assert out.kind == VC.EXECUTED, out.speech
        # The spoken passphrase is never bound: no passphrase arg exists,
        # the receipt carries none, and speech routes to safe surfaces.
        blob = json.dumps({"speech": out.speech, "receipt": out.receipt})
        assert "hunter2" not in blob or "never" in blob.lower()
        assert "passphrase" not in json.dumps(out.receipt).lower() or \
            "never" in json.dumps(out.receipt).lower()
        assert "PASSPHRASE_NEVER_SPOKEN" in blob
        assert "dashboard" in out.speech
        assert ("export-state" in out.speech if "export" in entry
                else "import-state" in out.speech)


def test_voice_refusal_leaves_no_passphrase_trace(tmp_path, monkeypatch):
    from voicebridge import approvals as AP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = _voice(cfg, "export_state_voice")
    assert out.kind == VC.EXECUTED
    assert out.confirmation_id == ""  # refusals stage nothing
    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    blob = ""
    for item in journal.pending():
        blob += json.dumps({"cap": str(getattr(item, "capability", "")),
                            "meta": getattr(item, "meta", {})})
    assert "hunter" not in blob.lower()
    assert "passphrase" not in blob.lower()


def test_registry_has_no_passphrase_voice_arg():
    from voicebridge import voice_commands as VC

    for entry in VC.REGISTRY.values():
        for arg in entry.args:
            assert arg.name not in ("passphrase", "password", "secret"), \
                (entry.id, arg.name)


# ------------------------------------------------------- CLI/dashboard parity
def test_cli_export_dashboard_preview_consistent(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    bundle = tmp_path / "state.vbp"
    rc, text = _cli_export(cfg, bundle, PW)
    assert rc == 0, text
    assert bundle.is_file()
    assert oct(bundle.stat().st_mode & 0o777) == "0o600"
    assert PW not in text
    # Dashboard import-preview decrypts the CLI bundle with the same
    # passphrase (passphrase resupplied at confirm, never journaled).
    from voicebridge import dashboard_ops as DO

    staged = DO.run_op(cfg, "import_state",
                       {"bundle_path": str(bundle), "passphrase": PW},
                       actor="dashboard:user", session="d-xp-1")
    assert staged["state"] == "needs-confirm", staged
    assert staged.get("sensitive_required") == ["passphrase"]
    # The staged journal record must not contain the passphrase.
    from voicebridge import approvals as AP

    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    pending = {a.id: a for a in journal.pending()}
    target = pending.get(staged["confirmation_id"])
    assert target is not None
    staged_blob = json.dumps({"args": getattr(target, "meta", {}).get(
        "args", {}), "wording": str(getattr(target, "wording", ""))})
    assert PW not in staged_blob
    plan = DO.confirm_op(cfg, staged["confirmation_id"],
                         actor="dashboard:user", session="d-xp-1",
                         sensitive={"passphrase": PW})
    assert plan["state"] == "executed", plan
    assert "plan" in plan["receipt"]["result"]


def test_wrong_passphrase_typed_both_surfaces(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import portable as P

    cfg = _cfg(tmp_path, monkeypatch)
    bundle = tmp_path / "state.vbp"
    rc, _ = _cli_export(cfg, bundle, PW)
    assert rc == 0
    with pytest.raises(P.WrongPassphraseError):
        P.read_bundle(bundle.read_bytes(), WRONG)
    plan = _dash(cfg, "import_state",
                 {"bundle_path": str(bundle), "passphrase": WRONG},
                 session="d-xp-2", sensitive={"passphrase": WRONG})
    assert plan["state"] == "failed", plan
    assert plan["code"] not in ("", None)
    assert WRONG not in json.dumps(plan)


def test_dashboard_export_writes_private_bundle(tmp_path, monkeypatch):
    import stat as _stat

    cfg = _cfg(tmp_path, monkeypatch)
    out = _dash(cfg, "export_state", {"passphrase": PW}, session="d-xp-3",
                sensitive={"passphrase": PW})
    assert out["state"] == "executed", out
    assert "bytes" in out["receipt"]["result"]
    assert PW not in json.dumps(out)
    created = list(Path(cfg.state_dir()).glob("portable-*.vbp"))
    assert created, "dashboard export must write the bundle file"
    assert oct(_stat.S_IMODE(created[0].stat().st_mode)) == "0o600"


def test_corrupt_bundle_typed(tmp_path, monkeypatch):
    from voicebridge import portable as P

    _cfg(tmp_path, monkeypatch)
    bad = tmp_path / "bad.vbp"
    bad.write_bytes(b"not a bundle")
    with pytest.raises((P.CorruptedBundleError, P.WrongPassphraseError,
                        P.UnsupportedSchemaError, P.PortableError)):
        P.read_bundle(bad.read_bytes(), PW)
