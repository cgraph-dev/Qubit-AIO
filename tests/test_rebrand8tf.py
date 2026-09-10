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
    from voicebridge import webui as W

    assert "<title>Qubit Agent" in W.CHAT_PAGE
    assert "<title>Qubit Agent" in W.ACTIVITY_PAGE


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
        ({"event": "worker.dispatch", "task": "t"},
         ("dispatched to the worker", "worker")),
        ({"event": "worker.summary", "summary": "s"},
         ("Worker task", "worker")),
        ({"event": "worker.error", "error": "e"},
         ("Worker task", "worker")),
        ({"event": "worker.status", "elapsed_s": 1},
         ("Worker running", "worker")),
        ({"event": "agent.summary", "summary": "s"},
         ("Agent task", "agent")),
        ({"event": "agent.task", "task": "t"},
         ("Agent task dispatched", "agent")),
        ({"event": "agent.result.discarded", "why": "w"},
         ("Agent task interrupted", "agent")),
        # retired journal entries still render (history compat)
        ({"event": "openclaw.summary", "summary": "s"},
         ("Agent task", "worker")),
        ({"event": "hermes.task", "task": "t"},
         ("Voice task dispatched", "agent")),
    ]
    for ev, (text, route) in cases:
        item = f(ev)
        assert item is not None
        assert item["text"] == text, ev
        assert item["route"] == route, ev
        assert "Hermes" not in item["text"] and "OpenClaw" not in item["text"]


# ---------------------------------------------------------------------------
# Residual surfaces: no upstream product identity in user-facing strings
# ---------------------------------------------------------------------------
RESIDUAL_BAD = [
    '"OpenClaw task"', '"Hermes task"', '"OpenClaw progress"',
    '"Hermes error"', '"OpenClaw Voice Bridge — push-to-talk"',
    '"OpenClaw route:', '"# OpenClaw full report',
    '"Using Hermes."', '"Using OpenClaw."', """"I'll use OpenClaw""",
    "OpenClaw or Hermes?", "voice bridge for OpenClaw",
    "same as hermes chat", "(like hermes", "voice-bridge terminal (like",
    'Routing to Hermes."',
]


def test_no_residual_upstream_product_strings():
    orch_text = (REPO_ROOT / "voicebridge" / "orchestrator.py").read_text()
    webui_text = (REPO_ROOT / "voicebridge" / "webui.py").read_text()
    cli_text = (REPO_ROOT / "voicebridge" / "cli.py").read_text()
    term_text = (REPO_ROOT / "voicebridge" / "terminal.py").read_text()
    for bad in RESIDUAL_BAD:
        for name, text in (("orchestrator", orch_text), ("webui", webui_text),
                           ("cli", cli_text), ("terminal", term_text)):
            assert bad not in text, f"{name} still shows {bad!r}"
    # Variable-routed speech: every notice= literal reaching speak() must
    # already be Qubit-named (covers failover paths the static list misses).
    import re as _re

    for match in _re.finditer(r'notice="([^"]*)"', orch_text):
        literal = match.group(1)
        assert "Hermes" not in literal and "OpenClaw" not in literal, literal
    for page in PAGES:
        html = (FRONTEND / page).read_text()
        assert "Hermes" not in html, page
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
    orch._handle_agent = lambda task, notice="": handled.append(("voice", task))
    orch._launch_task = lambda task, model=None: handled.append(("agent", task))
    return orch, spoken, handled


# ---------------------------------------------------------------------------
# Orchestrator speech routes the Qubit lanes under Qubit names
# ---------------------------------------------------------------------------
def test_agent_worker_lanes_exist_without_legacy_choice(tmp_path, monkeypatch):
    orch, spoken, handled = _orch(tmp_path, monkeypatch)
    assert not hasattr(orch, "executor_choice"), \
        "executor choice is retired: one worker lane, no question"
    assert callable(orch._handle_agent) and callable(orch._launch_task)
    for line in spoken:
        assert "Hermes" not in line and "OpenClaw" not in line


def test_legacy_backend_names_still_understood(tmp_path, monkeypatch):
    from voicebridge.intent.classifier import Classifier
    from voicebridge.intent.state import ConfirmationState, ConversationState

    clf = Classifier({"llm_classifier": False}, ConfirmationState(),
                     ConversationState())
    # legacy spoken names map to native lanes (documented migration aliases)
    assert clf.classify("hermes, check the inbox").kind == "agent"
    assert clf.classify("ask hermes to check my inbox").kind == "agent"
    assert clf._sticky_route("route to openclaw") == "worker"
    assert clf._sticky_route("route to hermes") == "agent"
