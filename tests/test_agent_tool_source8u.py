"""Regression: planner/model tool dispatch uses its TRUE origin, not "user".

A planner-proposed ``local.*`` tool must never be rubber-stamped as human
input. Reversible tools execute through the canonical broker with
``source="model"`` (honest audit attribution); MUTATING/DESTRUCTIVE tools are
refused with ``DENY_CONFIRM_SOURCE`` until a user-issued confirmation exists.

Hermetic: a temp state dir and a temp config only; no host process is touched
for the reversible case (``time_date`` reads the clock) and the mutating case is
denied before any execution.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.dispatch_guard import DispatchBlocked  # noqa: E402
from voicebridge.orchestrator import VoiceOrchestrator  # noqa: E402


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def _orch(tmp_path, power_control=None):
    from voicebridge.broker import build_host_broker
    cfg = _cfg(tmp_path)
    orch = VoiceOrchestrator.__new__(VoiceOrchestrator)
    orch.cfg = cfg
    orch._broker = build_host_broker(power_control=power_control, cfg=cfg)
    orch._register_web_caps()
    return orch


def test_reversible_planner_tool_runs_with_model_source(tmp_path):
    orch = _orch(tmp_path)
    out = orch._agent_tool("local.time_date", {})
    assert isinstance(out, str) and out.strip()


def test_mutating_planner_tool_fails_closed_without_user_confirmation(tmp_path):
    orch = _orch(tmp_path)
    with pytest.raises(DispatchBlocked) as exc:
        orch._agent_tool("local.window_close", {})
    assert exc.value.code == "CONFIRMATION_SOURCE_FORBIDDEN"


def test_unknown_planner_tool_is_rejected(tmp_path):
    orch = _orch(tmp_path)
    with pytest.raises(ValueError):
        orch._agent_tool("local.no_such_tool_xyz", {})


class _Resp:
    def __init__(self, body=b"", *, status=200,
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


def test_web_tool_resolves_through_shared_broker_as_model(tmp_path,
                                                          monkeypatch):
    """A ``web.*`` planner tool must resolve through the shared broker with
    ``source="model"``, execute exactly once, and be audited by the broker."""
    from voicebridge import broker as B
    from voicebridge import web_tool as WT

    orch = _orch(tmp_path)
    seen = {}
    real_execute = orch._broker.execute

    def spy(capability, args, *, source, **kw):
        seen["capability"] = capability
        seen["source"] = source
        return real_execute(capability, args, source=source, **kw)

    monkeypatch.setattr(orch._broker, "execute", spy)
    monkeypatch.setattr(WT, "_open", lambda req, timeout: _Resp(b"<p>ok</p>"))
    events = []
    monkeypatch.setattr(B, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    monkeypatch.setattr(WT, "log_event",
                        lambda ev, **f: events.append((ev, f)))

    out = orch._agent_tool("web.fetch",
                           {"url": "http://93.184.216.34/page"})
    assert json.loads(out)["state"] == WT.OK

    # Resolved through the ONE broker, attributed to the model, not "user".
    assert seen == {"capability": "web.fetch", "source": "model"}
    starts = [f for ev, f in events if ev == "broker.exec.start"]
    finishes = [f for ev, f in events if ev == "broker.exec.finish"]
    assert len(starts) == 1 and len(finishes) == 1
    assert starts[0]["capability"] == "web.fetch"
    assert starts[0]["source"] == "model"
    # Executed once: one operation receipt, no direct + broker double run.
    assert len([1 for ev, _ in events if ev == "web_tool.fetch"]) == 1


def test_web_tool_estop_is_enforced_by_shared_broker(tmp_path, monkeypatch):
    """The broker's e-stop latch refuses a ``web.*`` call before any network
    work, so a web tool cannot bypass the canonical decision junction."""
    from voicebridge import power as PW
    from voicebridge import web_tool as WT

    control = PW.PowerControl()
    orch = _orch(tmp_path, power_control=control)
    reached = []
    monkeypatch.setattr(WT, "_open",
                        lambda req, timeout: reached.append(1) or _Resp())
    control.emergency_stop()

    with pytest.raises(DispatchBlocked) as exc:
        orch._agent_tool("web.fetch", {"url": "http://93.184.216.34/page"})
    assert exc.value.code == "EMERGENCY_STOP"
    assert reached == []          # refused before any fetch
