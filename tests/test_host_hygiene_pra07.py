"""PRA-07 — no silent dependency on the stale external runtime.

Qubit must resolve its own runtime home, never the operator's external
``~/.hermes`` install, and the product source must not hard-code the stale
external checkout path. The operator hygiene record carries the exact commands
for stopping the stale process/listener/unit (the auditor never stops them).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Absolute paths that identify the stale external install. ``~/.hermes`` is a
# legitimate *rejection* target in qubit_runtime, so only the hard-coded
# checkout paths are forbidden.
_FORBIDDEN = (
    re.compile(r"/home/trick/\.hermes"),
    re.compile(r"hermes-agent/venv"),
    re.compile(r"openclaw-voice-bridge/\.venv"),
)


def _iter_sources():
    for base in ("voicebridge", "scripts"):
        root = REPO / base
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_no_product_source_hardcodes_external_install():
    hits = []
    for path in _iter_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for rx in _FORBIDDEN:
            if rx.search(text):
                hits.append((str(path.relative_to(REPO)), rx.pattern))
    assert not hits, hits


def test_external_qubit_home_is_rejected_as_backend_home(tmp_path,
                                                          monkeypatch):
    from voicebridge import config as C
    from voicebridge import qubit_runtime as HR

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)
    # Even with QUBIT_HOME pointed at the external install, Qubit resolves its
    # own home and never that directory.
    external = Path.home() / ".hermes"
    monkeypatch.setenv("QUBIT_HOME", str(external))
    home = HR.runtime_home_for_cfg(cfg)
    assert home.resolve() != external.resolve(), home
    assert str(home).startswith(str(tmp_path / "s").replace("/s", "")) or \
        str(tmp_path) in str(home), home


def test_operator_hygiene_record_has_exact_commands():
    doc = REPO / "docs/archive/2026-09-21" / "QUBIT_OPERATOR_HYGIENE_2026-09-19.md"
    text = doc.read_text(encoding="utf-8")
    for needle in (
        "systemctl --user disable --now hermes-dashboard.service",
        "~/.config/systemd/user/qubit-voice-agent.service",
        "qubit-voicebridge.service",
        "ss -ltnp",
    ):
        assert needle in text, needle
