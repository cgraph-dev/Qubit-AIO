"""Focused: every remaining direct web-tool caller dispatches via the broker.

Regression for the three paths that called ``web_tool.fetch``/``search``
directly and therefore bypassed canonical e-stop / policy / audit:

* gateway ``controlUi.linkPreview`` (``voicebridge/gateway/rpc.py``)
* voice ``websites`` fetch/search backend (``voicebridge/voice_commands.py``)
* CLI ``qubit websites fetch/search`` (``voicebridge/cli.py``)

Each must resolve the ``web.*`` capability through the ONE shared
``CapabilityBroker`` with ``source="user"``, so the shared power-control
(e-stop) refusal applies before any network work and the broker audits the
decision exactly once.

Hermetic: temp state dir and cfg only; the HTTP boundary is monkeypatched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _latch_estop(cfg):
    from voicebridge import power as PW

    PW.power_control_store(cfg).set(
        emergency_stop=True, global_disable=False, issued_by="cli:user")


class _Resp:
    def __init__(self, body=b"<p>ok</p>", *, status=200,
                 url="http://93.184.216.34/page", ctype="text/html"):
        self._body = body
        self._status = status
        self._url = url
        self.headers = {"Content-Type": ctype}

    def getcode(self):
        return self._status

    def geturl(self):
        return self._url

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------- gateway
def test_controlui_link_preview_dispatches_web_fetch_via_broker(
        tmp_path, monkeypatch):
    from voicebridge import broker as B
    from voicebridge import web_tool as WT
    from voicebridge.gateway.rpc import _controlui_link_preview

    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(B, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    monkeypatch.setattr(WT, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    opened = []
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: opened.append(req.full_url)
                        or _Resp())

    out = _controlui_link_preview(cfg, {"url": "http://93.184.216.34/page"})
    assert out["state"] == WT.OK
    assert out["code"] == "LINK_PREVIEW_LOCAL_FETCH"

    starts = [f for ev, f in events if ev == "broker.exec.start"]
    assert len(starts) == 1 and len([1 for ev, _ in events
                                     if ev == "broker.exec.finish"]) == 1
    assert starts[0]["capability"] == "web.fetch"
    assert starts[0]["source"] == "user"
    # Executed exactly once: one network open, one web-tool audit.
    assert len(opened) == 1
    assert len([1 for ev, _ in events if ev == "web_tool.fetch"]) == 1


def test_controlui_link_preview_estop_refused_before_network(
        tmp_path, monkeypatch):
    from voicebridge import web_tool as WT
    from voicebridge.gateway.gateway_ws import GatewayError
    from voicebridge.gateway.rpc import _controlui_link_preview

    cfg = _cfg(tmp_path, monkeypatch)
    _latch_estop(cfg)
    reached = []
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: reached.append(1) or _Resp())
    ran = []
    monkeypatch.setattr(WT, "fetch", lambda *a, **k: ran.append(1) or {})

    with pytest.raises(GatewayError) as exc:
        _controlui_link_preview(cfg, {"url": "http://93.184.216.34/page"})
    assert exc.value.code == "EMERGENCY_STOP"
    assert reached == [] and ran == []


# ----------------------------------------------------------------- voice
def test_voice_web_fetch_dispatches_via_broker(tmp_path, monkeypatch):
    from voicebridge import broker as B
    from voicebridge import voice_commands as VC
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="voice", source="user")
    events = []
    monkeypatch.setattr(B, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    monkeypatch.setattr(WT, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: _Resp())

    out = VC.execute(
        ctx, VC.resolve("qubit, fetch a web page http://93.184.216.34/page"))
    assert out.kind == VC.EXECUTED
    assert out.receipt["result"]["web"]["state"] == WT.OK
    starts = [f for ev, f in events if ev == "broker.exec.start"]
    assert len(starts) == 1
    assert starts[0]["capability"] == "web.fetch"
    assert starts[0]["source"] == "user"


def test_voice_web_fetch_estop_refused_before_network(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    _latch_estop(cfg)
    ctx = VC.make_ctx(cfg, session="voice", source="user")
    reached = []
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: reached.append(1) or _Resp())
    ran = []
    monkeypatch.setattr(WT, "fetch", lambda *a, **k: ran.append(1) or {})

    out = VC.execute(
        ctx, VC.resolve("qubit, fetch a web page http://93.184.216.34/page"))
    assert out.kind == VC.REFUSED
    assert "latched" in out.speech.lower()
    assert out.receipt == {}
    assert reached == [] and ran == []


def test_voice_web_search_estop_refused_before_network(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data["web"] = {"search": {"provider": "searxng",
                                  "endpoint": "http://93.184.216.34/search"}}
    _latch_estop(cfg)
    ctx = VC.make_ctx(cfg, session="voice", source="user")
    ran = []
    monkeypatch.setattr(WT, "search", lambda *a, **k: ran.append(1) or {})

    out = VC.execute(ctx, VC.resolve("qubit, search the web for rust async"))
    assert out.kind == VC.REFUSED
    assert out.receipt == {}
    assert ran == []


# ------------------------------------------------------------------- CLI
def test_cli_websites_fetch_estop_refused_before_network(
        tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from voicebridge import cli as CLI
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    _latch_estop(cfg)
    reached = []
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: reached.append(1) or _Resp())
    ran = []
    monkeypatch.setattr(WT, "fetch", lambda *a, **k: ran.append(1) or {})
    args = SimpleNamespace(websites_command="fetch",
                           url="http://93.184.216.34/page", human=False)

    rc = CLI._cmd_websites(cfg, args)
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "denied"
    assert out["code"] == "EMERGENCY_STOP"
    assert rc == 2
    assert reached == [] and ran == []


def test_cli_websites_fetch_dispatches_via_broker(
        tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from voicebridge import broker as B
    from voicebridge import cli as CLI
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(B, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    monkeypatch.setattr(WT, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: _Resp())
    args = SimpleNamespace(websites_command="fetch",
                           url="http://93.184.216.34/page", human=False)

    rc = CLI._cmd_websites(cfg, args)
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == WT.OK
    assert rc == 0
    starts = [f for ev, f in events if ev == "broker.exec.start"]
    assert len(starts) == 1
    assert starts[0]["capability"] == "web.fetch"
    assert starts[0]["source"] == "user"
