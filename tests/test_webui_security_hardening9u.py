"""Security hardening regressions for the dashboard transport.

Covers the four findings fixed together:

* every ``_send`` response carries a browser-hardening header set (CSP, frame
  denial, nosniff, referrer policy), with the ONE same-origin iframe document
  allowed to frame only itself;
* REST token comparison uses ``hmac.compare_digest`` rather than ``==``;
* a config file that carries ``webui.token`` is written ``0600`` while an
  ordinary token-free config keeps its normal permissions;
* static dashboard reads resolve inside the build root (encoded traversal and
  symlink escapes are refused).
"""
from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as ui  # noqa: E402


# --------------------------------------------------------------------------
# 1. Response headers
# --------------------------------------------------------------------------
def _send_headers(*, allow_same_origin_frame: bool = False,
                  extra_headers=None) -> dict:
    """Run the real ``_send`` against a recording handler (no socket)."""
    h = object.__new__(ui.WebUIHandler)
    recorded: list = []

    h.send_response = lambda code: recorded.append((":status", code))
    h.send_header = lambda k, v: recorded.append((k, v))
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    h.headers = {}
    h._head_only = False
    h._allow_same_origin_frame = allow_same_origin_frame
    h.server = type("S", (), {
        "app": type("A", (), {"allowed_origins": ()})()})()
    h._send(200, b"ok", "text/plain", extra_headers=extra_headers)
    return {k: v for k, v in recorded if not k.startswith(":")}


def test_every_response_carries_hardening_headers():
    headers = _send_headers()
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = headers["Content-Security-Policy"]
    for required in (
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "connect-src 'self'",
        "frame-src 'self' https://www.youtube.com",
    ):
        assert required in csp, (required, csp)
    # The SPA's inline bootstrap needs unsafe-inline, but dynamic code
    # evaluation is never required and must stay forbidden.
    assert "'unsafe-eval'" not in csp
    assert "https://fonts.gstatic.com" in csp


def test_office_iframe_gets_sameorigin_not_deny():
    headers = _send_headers(allow_same_origin_frame=True)
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]


def test_caller_headers_still_reach_the_wire():
    headers = _send_headers(extra_headers=[("Set-Cookie", "a=b")])
    assert headers["Set-Cookie"] == "a=b"
    # Security headers are not displaced by caller-supplied extras.
    assert headers["X-Frame-Options"] == "DENY"


def test_office_app_html_enables_same_origin_framing_for_that_call(tmp_path):
    handler = object.__new__(ui.WebUIHandler)
    handler.path = "/plugin-apps/qubit-office/habblaud/index.html"
    handler.headers = {"Host": "127.0.0.1"}
    handler.client_address = ("127.0.0.1", 50000)
    handler.server = type("S", (), {
        "app": type("A", (), {"api_token": "tok"})()})()
    handler._issue_office_nonce = lambda: "nonce"
    seen: dict = {}

    def _send(code, body, ctype="text/plain", extra_headers=None):
        seen["flag"] = getattr(handler, "_allow_same_origin_frame", False)

    handler._send = _send
    target = tmp_path / "index.html"
    target.write_text("<!doctype html><html><head></head><body></body></html>")
    handler._serve_app_html(target)
    assert seen["flag"] is True
    # The exception is scoped to that one response.
    assert getattr(handler, "_allow_same_origin_frame", False) is False


# --------------------------------------------------------------------------
# 2. Constant-time REST token comparison
# --------------------------------------------------------------------------
def test_token_comparison_uses_compare_digest(monkeypatch):
    seen: dict = {}

    def fake_compare(a, b):
        seen["args"] = (a, b)
        return True

    monkeypatch.setattr(ui.hmac, "compare_digest", fake_compare)
    ok, reason = ui.token_ok("configured", "presented", False)
    assert ok is True
    assert reason == "ok"
    assert seen["args"] == (b"presented", b"configured")


def test_token_contract_still_rejects_and_normalizes():
    assert ui.token_ok("tok", "tok", False) == (True, "ok")
    assert ui.token_ok("tok", "nope", False) == (False,
                                                 "bad-or-missing-header")
    assert ui.token_ok("tok", "tok", True) == (False, "query-token-rejected")
    assert ui.token_ok("", "", False) == (True, "loopback-open")
    # A non-string configured token is normalized rather than raising, and a
    # non-ASCII token keeps working (compare_digest is fed UTF-8 bytes).
    assert ui.token_ok(123, "123", False)[0] is True
    assert ui.token_ok("päss", "päss", False)[0] is True
    assert ui.token_ok("päss", "pass", False)[0] is False


# --------------------------------------------------------------------------
# 3. Private writes for configs carrying webui.token
# --------------------------------------------------------------------------
def test_secure_config_permissions_only_tightens_token_configs(tmp_path):
    from voicebridge.config import (config_holds_webui_token,
                                    secure_config_permissions)

    assert config_holds_webui_token({"webui": {"token": "s3cret"}})
    assert not config_holds_webui_token({"webui": {"token": ""}})
    assert not config_holds_webui_token({"webui": {"token": "   "}})
    assert not config_holds_webui_token({"webui": {}})
    assert not config_holds_webui_token({"webui": "not-a-mapping"})
    assert not config_holds_webui_token(None)

    token_path = tmp_path / "token.yaml"
    token_path.write_text("webui:\n  token: s3cret\n", encoding="utf-8")
    os.chmod(token_path, 0o644)
    assert secure_config_permissions(token_path, {"webui": {"token": "s3cret"}})
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    plain_path = tmp_path / "plain.yaml"
    plain_path.write_text("webui:\n  token: ''\n", encoding="utf-8")
    os.chmod(plain_path, 0o644)
    assert not secure_config_permissions(plain_path, {"webui": {"token": ""}})
    assert stat.S_IMODE(plain_path.stat().st_mode) == 0o644


def test_config_migration_writes_a_token_config_private(tmp_path):
    from voicebridge import update_config_migration as M

    previous_umask = os.umask(0o022)
    try:
        token_cfg = tmp_path / "voice.yaml"
        token_cfg.write_text(
            "webui:\n  token: s3cret\nlogging:\n  jsonl: false\n",
            encoding="utf-8")
        os.chmod(token_cfg, 0o644)
        out = M.migrate_config(None, override=token_cfg)
        assert out["migrated"] is True, out
        assert stat.S_IMODE(token_cfg.stat().st_mode) == 0o600, out
        assert out["backup"] is not None
        assert stat.S_IMODE(Path(out["backup"]).stat().st_mode) == 0o600

        plain_cfg = tmp_path / "plain.yaml"
        plain_cfg.write_text("webui:\n  token: ''\nlogging:\n  jsonl: false\n",
                             encoding="utf-8")
        os.chmod(plain_cfg, 0o644)
        M.migrate_config(None, override=plain_cfg)
        # Token-free configs keep ordinary permissions (the write must not
        # turn every config into a secret).
        assert stat.S_IMODE(plain_cfg.stat().st_mode) == 0o644
    finally:
        os.umask(previous_umask)


def test_terminal_bridge_config_write_is_private_with_a_token(tmp_path):
    from voicebridge import terminal_bridge as TB

    previous_umask = os.umask(0o022)
    try:
        path = tmp_path / "config.yaml"
        path.write_text("webui:\n  token: s3cret\n", encoding="utf-8")
        os.chmod(path, 0o644)
        TB._atomic_write(path, {"webui": {"token": "s3cret"}})
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

        plain = tmp_path / "plain.yaml"
        TB._atomic_write(plain, {"webui": {"token": ""}})
        assert stat.S_IMODE(plain.stat().st_mode) == 0o644
    finally:
        os.umask(previous_umask)


# --------------------------------------------------------------------------
# 4. Static dashboard reads stay inside the build root
# --------------------------------------------------------------------------
def _static_handler(root: Path):
    handler = object.__new__(ui.WebUIHandler)
    handler._qubit_dist = lambda: root
    captured: dict = {}

    def _send(code, body, ctype="text/plain", extra_headers=None,
              allow_same_origin_frame=False):
        captured.update(code=code, body=body)

    def _file(path, ctype):
        captured.update(code=200, file=path, ctype=ctype)

    handler._send = _send
    handler._file = _file
    return handler, captured


def test_static_route_serves_files_inside_the_root(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    target = root / "assets" / "app.js"
    target.write_text("console.log(1)")
    handler, captured = _static_handler(root)
    handler._serve_qubit_static("/assets/app.js")
    assert captured["code"] == 200
    assert captured["file"] == target.resolve()


def test_static_route_refuses_traversal_encodings_and_symlink_escape(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")

    for name in (
        "/assets/../secret.txt",
        "/assets/%2e%2e/secret.txt",
        "/assets/..%2fsecret.txt",
        "/assets/..\\secret.txt",
    ):
        handler, captured = _static_handler(root)
        handler._serve_qubit_static(name)
        assert captured.get("code") == 404, (name, captured)

    # A symlink planted inside the dist tree must not escape it either.
    (root / "media").mkdir()
    (root / "media" / "leak.txt").symlink_to(outside)
    handler, captured = _static_handler(root)
    handler._serve_qubit_static("/media/leak.txt")
    assert captured.get("code") == 404, captured
    assert "file" not in captured
