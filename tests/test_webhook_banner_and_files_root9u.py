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


def test_webhook_approval_keeps_target_and_toggle_value(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as ops, qubit_compat as compat, webhooks

    cfg = _cfg(tmp_path, monkeypatch)
    route = "/api/webhooks"
    payload = {"name": "first", "url": "http://127.0.0.1:9999/hook",
               "events": ["test"], "secret_ref": "env:PROBE_HOOK"}
    code, _ = compat.dispatch(cfg, "POST", route, {}, {**payload, "confirm": True})
    assert code == 200
    code, _ = compat.dispatch(cfg, "POST", route, {},
                              {**payload, "name": "second", "confirm": True})
    assert code == 200

    def staged(method, path, body=None):
        code, result = compat.dispatch(cfg, method, path, {}, body or {})
        assert code == 409 and result["confirmation_id"], result
        return result["confirmation_id"]

    def confirm(receipt, args=None):
        result = ops.confirm_op(cfg, receipt, actor="dashboard:user", session="dashboard", sensitive=args)
        assert result["state"] == "executed", result

    create = staged("POST", route, {**payload, "name": "third"})
    code, stale = compat.dispatch(cfg, "POST", route, {},
                                  {**payload, "name": "fourth", "confirmation_id": create})
    assert code == 409 and stale["confirmation_id"] != create
    confirm(create, {"id": "fourth"})
    assert {row["id"] for row in webhooks.list_subs(cfg)["subscriptions"]} == {"first", "second", "third"}

    global_off = staged("POST", route + "/enable", {"enabled": False})
    confirm(global_off, {"enabled": True})
    assert webhooks.is_enabled(cfg) is False

    first_off = staged("PUT", route + "/first/enabled", {"enabled": False})
    code, other = compat.dispatch(cfg, "PUT", route + "/second/enabled", {},
                                  {"enabled": False, "confirmation_id": first_off})
    assert code == 409 and other["confirmation_id"] != first_off
    confirm(first_off, {"id": "second", "enabled": True})
    assert webhooks.show(cfg, "first")["subscription"]["enabled"] is False
    assert webhooks.show(cfg, "second")["subscription"]["enabled"] is True

    first_remove = staged("POST", route + "/first")
    code, other = compat.dispatch(cfg, "DELETE", route + "/second", {"confirmation_id": first_remove})
    assert code == 409 and other["confirmation_id"] != first_remove
    confirm(first_remove, {"id": "second"})
    assert {row["id"] for row in webhooks.list_subs(cfg)["subscriptions"]} == {"second", "third"}


def test_invalid_webhook_secret_cannot_enter_approval_journal(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as ops, qubit_compat as compat

    cfg = _cfg(tmp_path, monkeypatch)
    journal = ops._journal(cfg)
    before = [entry.id for entry in journal.pending()]
    code, response = compat.dispatch(cfg, "POST", "/api/webhooks", {},
                                     {"name": "fixture", "url": "http://127.0.0.1:9999/hook",
                                      "events": ["test"], "secret_ref": "synthetic-raw-canary"})
    assert code == 400 and response["code"] == "WEBHOOK_BAD_INPUT", response
    assert [entry.id for entry in journal.pending()] == before


def test_webhook_signed_retry_respects_delivery_switches(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from voicebridge import qubit_compat as compat

    cfg = _cfg(tmp_path, monkeypatch)
    key = "disposable-webhook-secret"
    secret = tmp_path / "fixture-secret"
    secret.write_text(key)
    calls = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            signed = self.headers["X-Qubit-Timestamp"].encode() + b"." + body
            expected = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
            calls.append((json.loads(body)["event"],
                          self.headers["X-Qubit-Signature"] == f"sha256={expected}"))
            self.send_response(502 if len(calls) == 1 else 204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    route = "/api/webhooks"
    sub = route + "/receiver"
    test = sub + "/test"
    retry = sub + "/retry"

    def send(method, target, **fields):
        return compat.dispatch(cfg, method, target, {}, {**fields, "confirm": True})

    try:
        code, created = send("POST", route, name="receiver", events=["test"],
                             url=f"http://127.0.0.1:{receiver.server_port}/hook",
                             secret_ref=f"file:{secret}")
        assert code == 200 and created["secret"] == "file:<reference>", created
        code, failed = send("POST", test)
        assert code == 200 and failed["verdict"] == "FAILED" and failed["http_status"] == 502, failed
        assert calls == [("webhook.test", True)]
        assert send("PUT", sub + "/enabled", enabled=False)[0] == 200
        assert send("POST", retry)[0] == 400 and len(calls) == 1
        assert send("PUT", sub + "/enabled", enabled=True)[0] == 200
        assert send("POST", route + "/enable", enabled=False)[0] == 200
        assert send("POST", retry)[0] == 400 and len(calls) == 1
        assert send("POST", route + "/enable", enabled=True)[0] == 200
        code, retried = send("POST", retry)
        assert code == 200 and retried["delivered"] is True, retried
        assert calls == [("webhook.test", True), ("webhook.retry", True)]
        code, receipt = compat.dispatch(cfg, "GET", sub + "/deliveries", {})
        assert code == 200 and [row["verdict"] for row in receipt["deliveries"]] == ["FAILED", "DELIVERED"]
        assert key not in json.dumps(receipt)
        assert send("DELETE", sub)[0] == 200
        assert compat.dispatch(cfg, "GET", sub, {})[0] == 404
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=5)


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
