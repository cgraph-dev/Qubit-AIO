"""Lane 4: proxy-path fallback audit carries the failing api_path.

Recurring ``webui.qubit_backend.fallback`` events with
``'dict' object has no attribute 'state_dir'`` (2026-10-03 09:50-09:52, in
pairs) could not be attributed to a route: the fallback audit logged only the
error, never the path. Journal correlation points at the roster pair
``GET /api/git/worktrees`` + ``GET /api/git/branches`` (each fallback is
immediately followed by the compat-served 200 for exactly those routes; the
Worktrees page polls both via ``Promise.all``), and the only exact-string
reproducer in the tree is the fork's ``_git_read_path`` passing its
dict-config into ``voicebridge.projects.active_project`` (a config-object
contract) -- another lane's active region, not fixed here.

These tests pin, through the real production functions:

* the fallback audit for the read-proxy path records the api_path that
  failed (regression: the event that cost this investigation its path);
* the roster input shape (no ``path`` query) never raises out of the proxy
  call itself -- the fork answers HTTP 422 and the dashboard degrades to the
  compat handler;
* the receiving contract: ``projects.active_project`` requires a config
  object (``cfg.state_dir()``); a bare dict raises ``AttributeError`` -- the
  exact journal string. Any future default-root wiring must resolve the
  active project through the Qubit config authority, never the fork dict.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_ROSTER_PATHS = ("/api/git/worktrees", "/api/git/branches")


def _handler_for_get(api_path: str):
    """A bare ``WebUIHandler`` aimed at ``_qubit_api("GET")`` (no sockets)."""
    from voicebridge import webui as W

    h = W.WebUIHandler.__new__(W.WebUIHandler)
    h.path = "/agent" + api_path
    h.headers = {}
    h._head_only = False

    class _FakeCfg:
        def get(self, *a, **k):
            return True

    class _FakeOrch:
        cfg = _FakeCfg()

    class _FakeApp:
        orch = _FakeOrch()
        allowed_origins = ()

    h.server = type("S", (), {"app": _FakeApp()})()
    h._qubit_token_ok = lambda: True  # noqa: E731
    h._qubit_backend_enabled = lambda: True  # noqa: E731
    sent = {}
    h._send = lambda code, body, ctype="text/plain": sent.update(  # noqa: E731
        code=code, body=body, ctype=ctype)
    return h, sent


def test_read_proxy_fallback_audit_records_the_failing_path(monkeypatch):
    """The fallback event names the api_path (was error-only)."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC
    from voicebridge import webui as W

    monkeypatch.setattr(HA, "gated_path", lambda p: False)
    monkeypatch.setattr(HA, "safe_to_proxy", lambda m, p: True)

    def _boom(*a, **k):
        raise AttributeError("'dict' object has no attribute 'state_dir'")

    monkeypatch.setattr(HA, "call", _boom)
    monkeypatch.setattr(HC, "dispatch", lambda *a, **k: (200, {"ok": True}))

    records = []
    monkeypatch.setattr(W, "log_event",
                        lambda event, **f: records.append((event, f)))

    for api_path in _ROSTER_PATHS:
        h, sent = _handler_for_get(api_path)
        h._qubit_api("GET")
        assert sent.get("code") == 200, (api_path, sent)

    fallbacks = [f for e, f in records if e == "webui.qubit_backend.fallback"]
    assert len(fallbacks) == 2, records
    assert sorted(f["path"] for f in fallbacks) == sorted(_ROSTER_PATHS), \
        fallbacks
    for f in fallbacks:
        assert "state_dir" in f["error"], f


def test_roster_shape_never_raises_out_of_the_proxy_call():
    """No-``path`` roster reads get an HTTP answer, never an exception."""
    from voicebridge import qubit_asgi as HA

    for api_path in _ROSTER_PATHS:
        assert HA.gated_path(api_path) is False, api_path
        assert HA.safe_to_proxy("GET", api_path) is True, api_path
        code, hdrs, body = HA.call("GET", api_path, query={},
                                  headers={"accept": "application/json"})
        assert isinstance(code, int), (api_path, code)
        assert isinstance(body, bytes) and body, (api_path, body)


def test_active_project_requires_a_config_object_not_a_dict():
    """Receiving contract behind the journal string (dict has no state_dir)."""
    from voicebridge import projects as P

    try:
        P.active_project({})
    except AttributeError as exc:
        assert str(exc) == "'dict' object has no attribute 'state_dir'", exc
    else:
        raise AssertionError("active_project({}) did not raise")
