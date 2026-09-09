"""Packet 8T-F paths — credential env files live under the Qubit tree.

Proves the canonical `~/.config/qubit-voicebridge/.env` wins, the legacy
operator-Hermes file is honored only when canonical is absent (with a loud
one-time migration warning), and values never reach logs.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _reader(monkeypatch, tmp_path, deepseek_env=""):
    from voicebridge import orchestrator as ORCH

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    cfg = SimpleNamespace(
        get=lambda *p, **kw: {"intent": {"deepseek_env": deepseek_env}}.
        get(p[0], {}).get(p[1], kw.get("default"))
        if len(p) > 1 else kw.get("default"))
    fake = SimpleNamespace(cfg=cfg)
    return lambda name: ORCH.VoiceOrchestrator._env_key(fake, name)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_canonical_wins_over_legacy(tmp_path, monkeypatch):
    read = _reader(monkeypatch, tmp_path)
    _write(tmp_path / "home" / ".config" / "qubit-voicebridge" / ".env",
           "DEEPSEEK_API_KEY=canonical-1\n")
    _write(tmp_path / "home" / ".hermes" / ".env",
           "DEEPSEEK_API_KEY=legacy-1\n")
    assert read("DEEPSEEK_API_KEY") == "canonical-1"


def test_legacy_fallback_warns_loudly(tmp_path, monkeypatch):
    from voicebridge import orchestrator as ORCH
    from voicebridge.logging_setup import setup

    state = tmp_path / "state"
    state.mkdir()
    setup(level="INFO", jsonl=True, state_dir=state)
    try:
        ORCH._LEGACY_ENV_WARNED.clear()
        read = _reader(monkeypatch, tmp_path)
        _write(tmp_path / "home" / ".hermes" / ".env",
               "DEEPSEEK_API_KEY=legacy-2\n")
        assert read("DEEPSEEK_API_KEY") == "legacy-2"
        blob = (state / "events.jsonl").read_text()
        assert "config.legacy_env" in blob
        assert "legacy-2" not in blob, "values never logged"
        assert len(ORCH._LEGACY_ENV_WARNED) == 1, "warned once"
        read("DEEPSEEK_API_KEY")
        assert len(ORCH._LEGACY_ENV_WARNED) == 1
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_env_and_missing_and_configured_override(tmp_path, monkeypatch):
    read = _reader(monkeypatch, tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert read("DEEPSEEK_API_KEY") == "from-env"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert read("DEEPSEEK_API_KEY") == ""
    custom = tmp_path / "custom.env"
    custom.write_text("DEEPSEEK_API_KEY=custom-3\n")
    read2 = _reader(monkeypatch, tmp_path, deepseek_env=str(custom))
    assert read2("DEEPSEEK_API_KEY") == "custom-3"


def test_defaults_use_canonical_env_path():
    from voicebridge import config as C

    blob = __import__("json").dumps(C.DEFAULTS)
    assert "~/.config/qubit-voicebridge/.env" in blob
    assert "~/.hermes/.env" not in blob, \
        "no default credential path may point at the operator install"
