"""Packet 8T-F — complete Qubit Agent rebrand (behavioral, no 8U work).

Proves user-facing surfaces read as one Qubit product while backend
route/event/contract identifiers (openclaw/hermes lane + journal names)
stay intact for compatibility, and legal/provenance references are kept:
CLI description/header/sample, terminal banner/help, dashboard titles/nav,
activity-feed text, orchestrator speech, a11y/lang, and frontend budgets.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"
PAGES = ["activity.html", "approvals.html", "index.html", "reports.html",
         "settings.html", "setup.html", "token-setup.html"]


# ---------------------------------------------------------------------------
# CLI + terminal
# ---------------------------------------------------------------------------
def test_cli_parser_description_is_qubit():
    from voicebridge.cli import build_parser

    text = build_parser().format_help()
    assert "Qubit Agent (Qubit VoiceBridge)" in text
    assert "voice bridge for OpenClaw" not in text


def test_status_output_opens_with_qubit_header(tmp_path, monkeypatch, capsys):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")
    from voicebridge import cli as CLI

    CLI._cmd_status(cfg)
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Qubit Agent")


def test_terminal_banner_and_help_are_qubit():
    from voicebridge import terminal as T

    banner = T._qubit_banner(color=False)
    assert "QUBIT" in banner and "hermes" not in banner.lower()
    assert "Hermes" not in T.CMD_HELP and "OpenClaw" not in T.CMD_HELP
    assert "Qubit Agent" in T.CMD_HELP
    assert "Qubit Agent" in T.build_parser().description


# ---------------------------------------------------------------------------
# Dashboard pages (shipped content)
# ---------------------------------------------------------------------------
def test_dashboard_pages_qubit_branded_and_accessible():
    for page in PAGES:
        html = (FRONTEND / page).read_text()
        assert "<title>Qubit" in html, page
        assert "Qubit Agent</a>" in html, page
        assert 'lang="en"' in html, page
        assert 'aria-label="Primary"' in html, page
    assert "Hermes" not in "".join((FRONTEND / p).read_text() for p in PAGES)


def test_frontend_stays_bounded_single_design_system():
    total = sum(p.stat().st_size for p in FRONTEND.iterdir() if p.is_file())
    assert total < 300_000, f"frontend budget exceeded: {total}"
    css = (FRONTEND / "app.css").read_text()
    for token in (":root", "--bg", "--text", "--primary", "--radius",
                  ".brand-mark", "focus-visible"):
        assert token in css, token


# ---------------------------------------------------------------------------
# Activity feed: Qubit text, preserved backend routes
# ---------------------------------------------------------------------------
def test_activity_feed_qubit_text_preserved_routes():
    from voicebridge.webui import _event_to_feed_item as f

    cases = [
        ({"event": "openclaw.dispatch", "task": "t"},
         ("dispatched to the agent runtime", "openclaw")),
        ({"event": "openclaw.summary", "summary": "s"},
         ("Agent task", "openclaw")),
        ({"event": "openclaw.request.failed", "error": "e"},
         ("Agent request", "openclaw")),
        ({"event": "openclaw.error", "error": "e"},
         ("Agent error", "openclaw")),
        ({"event": "openclaw.status", "elapsed_s": 1},
         ("Agent runtime running", "openclaw")),
        ({"event": "hermes.summary", "summary": "s"},
         ("Voice task", "hermes")),
        ({"event": "hermes.task", "task": "t"},
         ("Voice task dispatched", "hermes")),
        ({"event": "hermes.result.discarded", "why": "w"},
         ("Voice task interrupted", "hermes")),
    ]
    for ev, (text, route) in cases:
        item = f(ev)
        assert item is not None
        assert item["text"] == text, ev
        assert item["route"] == route, ev
        assert "Hermes" not in item["text"] and "OpenClaw" not in item["text"]


# ---------------------------------------------------------------------------
# Orchestrator speech routes the same backends under Qubit names
# ---------------------------------------------------------------------------
def _orch(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")
    from voicebridge.orchestrator import VoiceOrchestrator

    orch = VoiceOrchestrator(cfg, no_hotkey=True, dry_run=True)
    spoken, handled = [], []
    orch.speak = lambda text, *a, **k: spoken.append(text)
    orch._handle_hermes = lambda task: handled.append(("voice", task))
    orch._launch_task = lambda task, model=None: handled.append(("agent", task))
    return orch, spoken, handled


def test_executor_answers_route_qubit_named_backends(tmp_path, monkeypatch):
    orch, spoken, handled = _orch(tmp_path, monkeypatch)
    orch.executor_choice.request("do the thing", "m")
    orch._handle_executor_answer("use the voice runtime please")
    assert spoken == ["Using the voice runtime."]
    assert handled == [("voice", "do the thing")]

    orch.executor_choice.request("do the other thing", "m")
    orch._handle_executor_answer("agent runtime")
    assert spoken[-1] == "Using the agent runtime."
    assert handled[-1] == ("agent", "do the other thing")

    orch.executor_choice.request("whatever", "m")
    orch._handle_executor_answer("you pick")
    assert spoken[-1] == "I'll use the agent runtime for that."
    assert handled[-1] == ("agent", "whatever")
    for line in spoken:
        assert "Hermes" not in line and "OpenClaw" not in line


def test_legacy_backend_names_still_understood(tmp_path, monkeypatch):
    orch, spoken, handled = _orch(tmp_path, monkeypatch)
    orch.executor_choice.request("old habit", "m")
    orch._handle_executor_answer("hermes")
    assert handled == [("voice", "old habit")]
