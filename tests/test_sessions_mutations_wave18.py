"""Session mutation routes are real, confirm-gated Qubit-backed handlers.

Wave 18: ``POST /api/sessions/{prune,bulk-delete,import}`` and
``DELETE /api/sessions/empty`` were converted from typed refusals to real
handlers over the canonical :class:`voicebridge.qubit_session_store.
QubitSessionStore` (the vendored ``qubit_state`` maintainer). Each destructive
path must stage through the shared confirmation journal (409 +
``confirmation_id``) and only act on ``confirm``. ``prune`` additionally
supports a non-destructive ``dry_run`` report.
"""
from __future__ import annotations

from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _dispatch(cfg, method, path, body):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, {}, body)


def test_prune_dry_run_is_non_destructive(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "POST", "/api/sessions/prune", {"dry_run": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out.get("dry_run") is True, out
    assert out.get("removed") == 0, out
    assert isinstance(out.get("matched"), int), out


def test_prune_requires_confirmation_then_prunes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "POST", "/api/sessions/prune", {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out.get("confirmation_id"), out
    code, out = _dispatch(cfg, "POST", "/api/sessions/prune", {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert isinstance(out.get("removed"), int), out


def test_bulk_delete_validates_and_confirms(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "POST", "/api/sessions/bulk-delete", {})
    assert code == 400 and out["code"] == "MALFORMED", (code, out)

    code, out = _dispatch(cfg, "POST", "/api/sessions/bulk-delete",
                          {"ids": ["does-not-exist"]})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out.get("confirmation_id"), out

    code, out = _dispatch(cfg, "POST", "/api/sessions/bulk-delete",
                          {"ids": ["does-not-exist"], "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out.get("deleted") == 0, out
    assert out.get("requested") == 1, out


def test_import_validates_and_is_additive(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "POST", "/api/sessions/import", {})
    assert code == 400 and out["code"] == "MALFORMED", (code, out)

    # Import is additive (not destructive), so it does not require confirmation.
    code, out = _dispatch(cfg, "POST", "/api/sessions/import",
                          {"sessions": [{"id": "imported-1"}]})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out.get("imported") == 1, out
    assert out.get("imported_ids") == ["imported-1"], out

    # Re-importing the same id is skipped, not duplicated.
    code, out = _dispatch(cfg, "POST", "/api/sessions/import",
                          {"sessions": [{"id": "imported-1"}]})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out.get("skipped") == 1, out


def test_empty_delete_confirms(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "DELETE", "/api/sessions/empty", {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out.get("confirmation_id"), out
    code, out = _dispatch(cfg, "DELETE", "/api/sessions/empty",
                          {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert isinstance(out.get("deleted"), int), out
