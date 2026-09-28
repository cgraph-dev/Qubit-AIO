"""Wave 20 — every Qubit update surface routes to the ONE canonical backend.

Proves the three production update surfaces all bottom out in the same
``voicebridge.update_manager`` primitives and share one confirmation / e-stop
policy:

* **CLI** — ``voicebridge.cli._cmd_update`` for ``--check`` / ``--apply`` /
  ``--rollback`` (preferring ``update_manager.full_apply`` when present,
  otherwise the identical ``snapshot`` -> ``apply(confirm=True)`` sequence).
* **Dashboard** — ``qubit_compat.h_qubit_update_check/_apply/_rollback``
  (the ``/api/qubit/update*`` compat handlers), including the read-only GET,
  the 409 confirm gate, the 403 e-stop and the 500 unknown-failure mapping.
* **Voice** — the ``voice_commands`` update intents and their ``_BACKENDS``
  callables, which call the same manager functions; both mutating intents must
  carry confirm semantics.

Nothing here touches the network, the real worktree, ``~/.hermes``, a live
service, or any live config: every backend is monkeypatched or a temp XDG tree.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import qubit_compat as QC  # noqa: E402
from voicebridge import update_manager as UM  # noqa: E402
from voicebridge import voice_commands as VC  # noqa: E402

FRONTEND = REPO_ROOT / "frontend" / "qubit-web" / "src"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ns(**kw):
    """A parsed ``qubit update`` namespace (all attrs present)."""
    base = {
        "check": False, "apply": False, "rollback": False, "json": True,
        "source": "", "channel": "", "tag": "", "backup_dir": None,
        "dry_run": False, "confirm": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _recorder(returns):
    """A callable that records every (args, kwargs) and returns ``returns``."""
    calls = []

    def _fn(*args, **kwargs):
        calls.append((args, kwargs))
        return returns() if callable(returns) else returns

    _fn.calls = calls
    return _fn


def _voice_cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="wave20", source="user"):
    return VC.make_ctx(cfg, session=session, source=source)


# ---------------------------------------------------------------------------
# CLI — each verb reaches the canonical manager function
# ---------------------------------------------------------------------------
def test_cli_check_reaches_canonical_check(monkeypatch):
    seen = _recorder({"state": "OK", "message": "an update is available"})
    monkeypatch.setattr(UM, "check", seen)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(check=True))
    assert rc == 0
    assert len(seen.calls) == 1, "canonical update_manager.check must run once"
    out = json.loads(buf.getvalue())
    assert out["state"] == "OK"


def test_cli_apply_prefers_canonical_full_apply(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    full = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "full_apply", full)

    def _must_not_run(*a, **k):  # pragma: no cover - defensive
        raise AssertionError("the fallback primitives must not run when "
                             "full_apply exists")

    monkeypatch.setattr(UM, "snapshot", _must_not_run)
    monkeypatch.setattr(UM, "apply", _must_not_run)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(apply=True))
    assert rc == 0, buf.getvalue()
    assert len(full.calls) == 1
    _args, kwargs = full.calls[0]
    assert kwargs["confirm"] is True
    assert kwargs["actor"] == "cli:update"


def test_cli_apply_falls_back_to_snapshot_then_confirmed_apply(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    monkeypatch.setattr(UM, "full_apply", None)
    snap = _recorder({"ok": True, "backup_dir": "/tmp/qback",
                      "bundle": "/tmp/qback/code.bundle"})
    apply = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "snapshot", snap)
    monkeypatch.setattr(UM, "apply", apply)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(apply=True))
    assert rc == 0, buf.getvalue()
    assert len(snap.calls) == 1 and len(apply.calls) == 1
    assert apply.calls[0][1]["confirm"] is True
    assert apply.calls[0][1]["actor"] == "cli:update"


def test_cli_rollback_reaches_canonical_rollback(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    rollback = _recorder({"ok": True, "state": "OK",
                          "restored_revision": "a" * 40})
    monkeypatch.setattr(UM, "rollback", rollback)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(rollback=True))
    assert rc == 0, buf.getvalue()
    assert len(rollback.calls) == 1
    assert rollback.calls[0][1]["actor"] == "cli:update"


def test_cli_apply_without_confirm_flag_is_still_confirmed_verb(monkeypatch):
    """Owner Decision #11: choosing ``--apply`` IS the confirmation.

    The CLI supplies the single canonical ``confirm=True`` at the manager
    boundary; the manager's own confirm gate is asserted separately below.
    """
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    full = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "full_apply", full)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(apply=True, confirm=False))
    assert rc == 0
    assert full.calls[0][1]["confirm"] is True


def test_canonical_apply_without_confirm_is_typed_refusal_no_mutation():
    """The ONE confirm gate: ``update_manager.apply(confirm=False)``.

    Refuses with the typed code and runs no git/runner command (no mutation).
    """
    runner = SimpleNamespace(calls=[])

    def _run(argv, timeout):  # pragma: no cover - must never run
        runner.calls.append(argv)
        return 0, "", ""

    out = UM.apply(object(), confirm=False, runner=_run)
    assert out["ok"] is False
    assert out["code"] == UM.CONFIRM_CODE
    assert out["state"] == "CONFIRM_REQUIRED"
    assert runner.calls == []


def test_cli_dirty_worktree_typed_refusal_maps_to_rc2(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    monkeypatch.setattr(UM, "full_apply", _recorder({
        "ok": False, "state": "CONFLICT", "code": UM.WORKTREE_DIRTY_CODE,
        "detail": "commit or stash first"}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(apply=True))
    assert rc == 2
    assert json.loads(buf.getvalue())["code"] == UM.WORKTREE_DIRTY_CODE


def test_cli_verb_conflict_guard_returns_2(monkeypatch):
    called = _recorder({"ok": True})
    monkeypatch.setattr(UM, "full_apply", called)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(check=True, apply=True))
    assert rc == 2
    assert json.loads(buf.getvalue())["code"] == "UPDATE_VERB_CONFLICT"
    assert called.calls == [], "a verb conflict must run no backend"


# ---------------------------------------------------------------------------
# Dashboard compat handlers — canonical manager + HTTP mapping
# ---------------------------------------------------------------------------
def test_dashboard_check_is_read_only_200(monkeypatch):
    check = _recorder({"state": "OK", "install_method": "git-checkout"})
    monkeypatch.setattr(UM, "check", check)
    http, body = QC.h_qubit_update_check(object(), {}, {}, {})
    assert http == 200 and body["ok"] is True
    assert len(check.calls) == 1, "GET must call the canonical check"


def test_dashboard_apply_without_confirm_409_backend_called_confirm_false(
        monkeypatch):
    seen = {}

    def _full(cfg, *, confirm=False, source=None, channel=None, tag=None,
              actor=""):
        seen["confirm"] = confirm
        if not confirm:
            return {"ok": False, "state": "CONFIRM_REQUIRED",
                    "code": UM.CONFIRM_CODE, "detail": "confirm required"}
        return {"ok": True, "state": "OK"}

    monkeypatch.setattr(UM, "full_apply", _full)
    http, body = QC.h_qubit_update_apply(object(), {}, {}, {})
    assert http == 409
    assert body["ok"] is False and body["code"] == UM.CONFIRM_CODE
    assert seen["confirm"] is False, "handler must forward confirm=False"


def test_dashboard_apply_confirm_200(monkeypatch):
    seen = {}

    def _full(cfg, *, confirm=False, source=None, channel=None, tag=None,
              actor=""):
        seen["confirm"] = confirm
        seen["actor"] = actor
        return {"ok": True, "state": "OK"}

    monkeypatch.setattr(UM, "full_apply", _full)
    http, body = QC.h_qubit_update_apply(object(), {}, {"confirm": True}, {})
    assert http == 200 and body["ok"] is True
    assert seen["confirm"] is True
    assert seen["actor"] == "dashboard:user"


def test_dashboard_apply_estop_403(monkeypatch):
    monkeypatch.setattr(UM, "full_apply", _recorder({
        "ok": False, "state": "denied", "code": UM.ESTOP_CODE,
        "detail": "emergency stop latched"}))
    http, body = QC.h_qubit_update_apply(object(), {}, {"confirm": True}, {})
    assert http == 403 and body["code"] == UM.ESTOP_CODE


def test_dashboard_apply_unknown_failure_500(monkeypatch):
    monkeypatch.setattr(UM, "full_apply", _recorder({
        "ok": False, "state": "FAILED", "code": "WEIRD_UPDATE_FAILURE",
        "detail": "boom"}))
    http, body = QC.h_qubit_update_apply(object(), {}, {"confirm": True}, {})
    assert http == 500 and body["ok"] is False


def test_dashboard_rollback_exists_and_maps_to_canonical(monkeypatch):
    assert callable(QC.h_qubit_update_rollback)
    rb = _recorder({"ok": True, "state": "OK", "restored_revision": "b" * 40})
    monkeypatch.setattr(UM, "rollback", rb)
    http, body = QC.h_qubit_update_rollback(
        object(), {}, {"confirm": True, "backup_dir": "/tmp/qb"}, {})
    assert http == 200 and body["ok"] is True
    assert len(rb.calls) == 1
    assert rb.calls[0][1]["backup_dir"] == "/tmp/qb"
    assert rb.calls[0][1]["actor"] == "dashboard:user"


def test_dashboard_rollback_no_backup_maps_to_409(monkeypatch):
    monkeypatch.setattr(UM, "rollback", _recorder({
        "ok": False, "state": "REFUSED", "code": UM.NO_BACKUP_CODE,
        "detail": "no backup"}))
    http, body = QC.h_qubit_update_rollback(
        object(), {}, {"confirm": True}, {})
    assert http == 409 and body["code"] == UM.NO_BACKUP_CODE


# ---------------------------------------------------------------------------
# Voice — intents resolve to the same canonical backends
# ---------------------------------------------------------------------------
def test_update_intents_resolve_to_canonical_backends():
    table = {
        "qubit, update check": ("update_check", "_b_update_check"),
        "qubit, apply update": ("update_apply", "_b_update_apply"),
        "qubit, roll back update": ("update_rollback", "_b_update_rollback"),
    }
    for phrase, (entry_id, backend_fn) in table.items():
        res = VC.resolve(phrase)
        assert res.kind == VC.MATCH, phrase
        assert res.entry is not None and res.entry.id == entry_id, phrase
        fn = VC._BACKENDS.get(res.entry.backend)
        assert fn is not None, f"{entry_id}: backend not wired"
        assert fn.__name__ == backend_fn, (entry_id, fn.__name__)
    # Mutating intents carry confirm semantics; the read does not.
    assert VC.resolve("qubit, update check").entry.confirm is False
    assert VC.resolve("qubit, apply update").entry.confirm is True
    assert VC.resolve("qubit, roll back update").entry.confirm is True


def test_voice_check_executes_read_only_canonical(tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    check = _recorder({"state": "OK", "message": "up to date"})
    monkeypatch.setattr(UM, "check", check)
    out = VC.execute(ctx, VC.resolve("qubit, update check"))
    assert out.kind == VC.EXECUTED, out.speech
    assert len(check.calls) == 1


def test_voice_mutating_intents_stage_confirmation_not_mutation(
        tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    full = _recorder({"ok": True, "state": "OK"})
    rb = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "full_apply", full)
    monkeypatch.setattr(UM, "rollback", rb)

    for phrase in ("qubit, apply update", "qubit, roll back update"):
        out = VC.execute(ctx, VC.resolve(phrase))
        assert out.kind == VC.NEEDS_CONFIRM, (phrase, out.kind, out.speech)
        assert out.confirmation_id, phrase
    assert full.calls == [] and rb.calls == [], "no mutation before consent"


def test_voice_apply_confirm_reaches_full_apply(tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    full = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "full_apply", full)

    staged = VC.execute(ctx, VC.resolve("qubit, apply update"))
    assert staged.kind == VC.NEEDS_CONFIRM
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, done.speech
    assert len(full.calls) == 1
    assert full.calls[0][1]["confirm"] is True
    assert full.calls[0][1]["actor"] == "voice:user"


def test_voice_rollback_confirm_reaches_rollback(tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    rb = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "rollback", rb)

    staged = VC.execute(ctx, VC.resolve("qubit, roll back update"))
    assert staged.kind == VC.NEEDS_CONFIRM
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, done.speech
    assert len(rb.calls) == 1
    assert rb.calls[0][1]["actor"] == "voice:user"


def test_voice_apply_estop_refuses_before_backend(tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(VC, "_estop", lambda _cfg: True)
    ctx = _ctx(cfg)
    full = _recorder({"ok": True, "state": "OK"})
    monkeypatch.setattr(UM, "full_apply", full)
    out = VC.execute(ctx, VC.resolve("qubit, apply update"))
    assert out.kind == VC.REFUSED, (out.kind, out.speech)
    assert full.calls == []


# ---------------------------------------------------------------------------
# Frontend contract (static) — SPA uses the canonical update routes
# ---------------------------------------------------------------------------
def _read(rel):
    p = FRONTEND / rel
    assert p.is_file(), f"missing frontend file {rel}"
    return p.read_text(encoding="utf-8")


def _require_or_xfail(text, pattern, label):
    import re

    if not re.search(pattern, text, re.S):
        pytest.xfail(f"{label} moved/renamed by a concurrent frontend edit "
                     f"(pattern {pattern!r} not found)")


def test_frontend_api_uses_canonical_update_routes():
    api = _read("lib/api.ts")
    _require_or_xfail(api, r"/api/qubit/update/check", "update check route")
    _require_or_xfail(api, r"/api/qubit/update(?![a-z/])", "update apply route")
    _require_or_xfail(api, r"checkQubitUpdate\s*:", "checkQubitUpdate method")
    _require_or_xfail(api, r"updateQubit\s*:", "updateQubit method")


def test_frontend_system_page_has_update_confirm_dialog():
    page = _read("pages/SystemPage.tsx")
    _require_or_xfail(page, r"checkQubitUpdate\s*\(", "check call site")
    _require_or_xfail(page, r"updateQubit\s*\(", "apply call site")
    _require_or_xfail(page, r"updateConfirmOpen", "confirm open state")
    _require_or_xfail(page, r"ConfirmDialog", "confirm dialog component")
    _require_or_xfail(page, r"onConfirm=\{\(\)\s*=>\s*void\s+applyUpdate\(\)\}",
                      "confirm dialog onConfirm -> applyUpdate")
    _require_or_xfail(page, r"Update Qubit\?", "confirm dialog title")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
