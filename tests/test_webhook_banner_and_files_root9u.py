"""Banner id-only settle for webhook create + the Files active-project root.

Two real bugs found by the browser control E2E (2026-09-24):
* ``POST /api/webhooks`` staged a confirmation but the pending-approvals
  banner's id-only ``POST /api/ops/confirm`` returned ``CONFIRM_UNSUPPORTED``
  (``_compat_runner`` had no ``webhook_create`` branch). The staged payload now
  carries the creation fields and the runner re-enters ``h_webhooks_mutate``.
* ``_files_root`` called the nonexistent ``projects.active(cfg)`` so the Files
  page reported "no active project folder" even with one active; it now delegates
  to ``_active_folder``.

Hermetic: temp XDG/QUBIT_HOME only; no live service or network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_compat_runner_has_webhook_family():
    from voicebridge import dashboard_ops as OPS

    for action in ("webhook_create", "webhook_enable", "webhook_enabled",
                   "webhook_delete", "webhook_rotate", "webhook_test",
                   "webhook_retry"):
        assert OPS._compat_runner(action) is not None, action


def test_staged_webhook_create_settles_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    code, staged = HC.h_webhooks_mutate(
        "add", cfg, {},
        {"name": "banner-probe", "url": "http://127.0.0.1:9999/hook",
         "events": ["test"], "secret_ref": "env:PROBE_HOOK"}, {})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged

    out = OPS.confirm_op(cfg, cid, actor="dashboard:user", session="dashboard")
    assert out["state"] == "executed", out
    subs = HC.h_webhooks_list(cfg, {}, {}, {})[1].get("subscriptions", [])
    assert any(s.get("name") == "banner-probe" for s in subs), subs

    # settle-once
    again = OPS.confirm_op(cfg, cid, actor="dashboard:user", session="dashboard")
    assert again["state"] == "failed", again


def test_files_root_uses_active_project(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import projects as PJ
    from voicebridge import qubit_compat as HC

    root = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(PJ, "active_project", lambda cfg: {
        "state": "OK", "project": {"folders": [root], "primary": root}})
    assert HC._files_root(cfg) == root


def test_files_root_empty_without_active_project(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import projects as PJ
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(PJ, "active_project", lambda cfg: {"state": "EMPTY"})
    assert HC._files_root(cfg) == ""


def _files_project(tmp_path, monkeypatch):
    """Register a temp project root so ``files.approved_roots`` admits it."""
    from voicebridge import projects as PJ

    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha")
    (root / "sub" / "b.txt").write_text("beta")
    rec = {"slug": "probe", "folders": [str(root)], "archived": False}
    monkeypatch.setattr(
        PJ, "list_projects",
        lambda cfg, include_archived=False: {"state": "OK", "projects": [rec]})
    monkeypatch.setattr(
        PJ, "active_project",
        lambda cfg: {"state": "OK", "project": rec})
    return root


def test_files_list_exposes_absolute_paths_and_directory_flag(
        tmp_path, monkeypatch):
    """Regression: ``files._public_dir`` emits ``dir``/``rel``; the compat
    adapter must surface ``is_directory`` + an absolute ``path`` (a folder read
    as a file could not be navigated) and return ``parent`` for the up control.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import qubit_compat as HC

    root = _files_project(tmp_path, monkeypatch)
    code, body = HC.h_files(cfg, {"path": str(root)}, {}, {})
    assert code == 200, body
    entries = {e["name"]: e for e in body["entries"]}
    assert entries["sub"]["is_directory"] is True, entries["sub"]
    assert entries["sub"]["path"] == str(root / "sub"), entries["sub"]
    assert entries["a.txt"]["is_directory"] is False, entries["a.txt"]
    assert entries["a.txt"]["path"] == str(root / "a.txt"), entries["a.txt"]
    # Directories sort before files (reference ``list_managed_files``).
    assert body["entries"][0]["name"] == "sub", body["entries"]
    # Immediate children only: a recursive walk would surface ``sub/b.txt`` and
    # spend the entry cap on descendants instead of sibling directories.
    assert all("/" not in e["name"] for e in body["entries"]), body["entries"]
    assert not any(e["path"].endswith("sub/b.txt") for e in body["entries"])

    # Nested listing works and points back at the parent for "up".
    code, nested = HC.h_files(cfg, {"path": str(root / "sub")}, {}, {})
    assert code == 200, nested
    assert nested["parent"] == str(root), nested
    assert {e["name"] for e in nested["entries"]} == {"b.txt"}, nested

    # A nested file still downloads by its absolute ``path``.
    code, read = HC.h_files_read(cfg, {"path": str(root / "sub" / "b.txt")},
                                 {}, {})
    assert code == 200, read
    assert read["name"] == "b.txt", read
    assert read["data_url"].startswith("data:text/plain;base64,"), read
