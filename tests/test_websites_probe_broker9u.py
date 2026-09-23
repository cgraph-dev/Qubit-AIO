"""Focused: ``qubit websites probe --live`` dispatches via the canonical broker.

Regression for the last web-egress authority bypass: the CLI live probe called
``websites.probe_url`` directly and opened its own bounded network connection
with NO broker / e-stop / policy / audit gate.

The live probe must now resolve the ``web.probe`` READ_ONLY capability through
the ONE shared ``CapabilityBroker`` with ``source="user"``, so the shared
power-control (e-stop) refusal applies before any network work and the broker
audits the decision exactly once (the probe does no e-stop check or audit of
its own). ``LIVE_REQUIRED`` default refusal, the bounded probe semantics, the
redaction and the printed/``--json`` output shape are preserved.

Hermetic: temp state dir and cfg only; the HTTP boundary is monkeypatched, so
no real network or DNS is used (the target is a literal public IP).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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
    """Minimal bounded-probe response (no socket)."""

    def __init__(self, body=b"<html><head><title>Example Domain</title>"
                            b"</head><body>ok</body></html>", *,
                 status=200, url="https://93.184.216.34/",
                 ctype="text/html"):
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


def _probe_args(url="https://93.184.216.34/", *, live=True, human=False):
    return SimpleNamespace(websites_command="probe", url=url, live=live,
                           human=human)


def test_probe_live_refused_under_latched_estop_before_network(
        tmp_path, monkeypatch, capsys):
    from voicebridge import broker as B
    from voicebridge import cli as CLI
    from voicebridge import websites as W

    cfg = _cfg(tmp_path, monkeypatch)
    _latch_estop(cfg)
    events = []
    monkeypatch.setattr(B, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    reached = []
    monkeypatch.setattr(W, "urlopen",
                        lambda req, timeout: reached.append(req.full_url)
                        or _Resp())

    rc = CLI._cmd_websites(cfg, _probe_args())
    out = json.loads(capsys.readouterr().out)
    # Typed broker denial, no network, no probe execution.
    assert rc == 3
    assert out["state"] == "denied"
    assert out["code"] == "EMERGENCY_STOP"
    assert reached == []
    # Broker decided exactly once (audited), and only the broker audited.
    denied = [f for ev, f in events if ev == "broker.exec.denied"]
    assert len(denied) == 1
    assert denied[0]["capability"] == W.PROBE_CAPABILITY
    assert denied[0]["decision"] == "EMERGENCY_STOP"
    assert not [ev for ev, _ in events if ev == "broker.exec.start"]
    assert not [ev for ev, _ in events if ev.startswith("web_tool.")]


def test_probe_live_succeeds_via_broker_when_unlatched(
        tmp_path, monkeypatch, capsys):
    from voicebridge import broker as B
    from voicebridge import cli as CLI
    from voicebridge import websites as W

    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(B, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    reached = []
    monkeypatch.setattr(W, "urlopen",
                        lambda req, timeout: reached.append(req.full_url)
                        or _Resp())

    rc = CLI._cmd_websites(cfg, _probe_args())
    out = json.loads(capsys.readouterr().out)
    # Output shape preserved: state/url/result/probe, SUCCESS for a READY page.
    assert rc == 0
    assert out["state"] == "OK"
    assert out["url"] == "https://93.184.216.34/"
    assert out["result"] == "SUCCESS"
    assert out["probe"]["status"] == "READY"
    assert out["probe"]["title"] == "Example Domain"
    assert len(reached) == 1
    # Exactly one broker decision/execution; the probe does not self-audit.
    starts = [f for ev, f in events if ev == "broker.exec.start"]
    assert len(starts) == 1
    assert starts[0]["capability"] == W.PROBE_CAPABILITY
    assert starts[0]["source"] == "user"
    assert len([1 for ev, _ in events if ev == "broker.exec.finish"]) == 1
    assert not [ev for ev, _ in events if ev.startswith("web_tool.")]


def test_probe_live_required_default_refusal_preserved(
        tmp_path, monkeypatch, capsys):
    from voicebridge import cli as CLI
    from voicebridge import websites as W

    cfg = _cfg(tmp_path, monkeypatch)
    reached = []
    monkeypatch.setattr(W, "urlopen",
                        lambda req, timeout: reached.append(1) or _Resp())

    rc = CLI._cmd_websites(cfg, _probe_args(live=False))
    out = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert out["state"] == "denied" and out["code"] == "LIVE_REQUIRED"
    assert reached == []


def test_probe_live_blocked_url_fails_typed_via_broker(
        tmp_path, monkeypatch, capsys):
    from voicebridge import cli as CLI
    from voicebridge import websites as W

    cfg = _cfg(tmp_path, monkeypatch)
    reached = []
    monkeypatch.setattr(W, "urlopen",
                        lambda req, timeout: reached.append(1) or _Resp())

    # Private literal origin: refused by the bounded probe before any network.
    rc = CLI._cmd_websites(cfg, _probe_args(url="https://127.0.0.1/admin"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["state"] == "failed"
    assert reached == []
