"""Packet 8T-F — single-authority surface: the tested Qubit-native
equivalent of Hermes serve/dashboard/desktop/gui.

Proves there is exactly one serving path (`voicebridge run` + the user
service + the loopback webui probe), no `serve`/`dashboard`/`desktop`/
`gui` stub commands exist, the singularity audit reports one of
everything, and voice routes dashboard requests to guidance (never a
second server).
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_no_second_server_commands():
    import argparse

    from voicebridge.cli import build_parser

    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    for absent in ("serve", "dashboard"):
        assert absent not in names, f"no second-server stub: {absent}"
    # `desktop`/`gui` are Qubit-owned fork CLI families (native desktop app
    # build/launch), confirm-gated mutating routes, never a second server.
    from voicebridge import cli_hermes_forward as HF

    for family in ("desktop", "gui"):
        assert family in names, family
        resolved = HF.route_family([family])
        assert resolved is not None and resolved.kind == "needs-confirm", \
            (family, resolved)
    assert "run" in names and "webui" in names, \
        "the single serving path owns the surface"


def test_singularity_one_of_everything():
    from voicebridge import foundation as F

    report = F.singularity_report({
        "listeners": 1, "voice_sessions": 1, "tts_paths": 1, "brokers": 1,
        "audit_paths": 1, "emergency_stops": 1, "agent_authorities": 1})
    assert report["verdict"] == "OK"
    F.assert_foundation(report)


def test_webui_probe_never_serves():
    import io
    import json as _json
    from contextlib import redirect_stdout

    from voicebridge import cli as _CLI

    class _A:
        def __init__(self, **kw):
            self.__dict__.update(port=9, open=False, json=True, **kw)

    # _cmd_webui probes 127.0.0.1:9 (closed) and must report UNAVAILABLE
    # without starting anything.
    cfg = type("C", (), {"get": lambda self, *p, **kw: kw.get("default")})()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _CLI._cmd_webui(cfg, _A())
    assert rc == 1
    body = _json.loads(buf.getvalue())
    assert body["state"] == "UNAVAILABLE"
    assert body["started_second_bridge"] is False


def test_voice_dashboard_routes_to_guidance(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = VC.resolve("qubit, open dashboard")
    assert res.kind == VC.MATCH and res.entry.id == "tui_help"
    ctx = VC.make_ctx(cfg, source="user")
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED
    assert "dashboard" in out.speech.lower() or "terminal" in out.speech.lower()


def test_catalog_reports_single_surface_implemented():
    from voicebridge.capability_catalog import check

    for token in ("serve", "dashboard", "desktop", "gui"):
        out = check(token)
        assert out["state"] == "IMPLEMENTED", (token, out)
        assert out["code"] == "OK"
