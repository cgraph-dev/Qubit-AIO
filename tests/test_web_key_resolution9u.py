"""Web/voice key readers consult the canonical runtime .env.

The KEYS page pastes raw secrets via ``env_store.write_secret`` into the
canonical runtime ``.env`` (``<state_dir>/runtime/.env``) while the
configured ``intent.deepseek_env`` voice ``.env`` may lack them. Both
``_web_env_key`` and ``VoiceOrchestrator._env_key`` must therefore check
the canonical runtime ``.env`` as a file candidate between the configured
path and ``os.environ`` (mirroring
``providers/credentials.resolve_key`` precedence for ``env:`` refs).

Hermetic: tmp HOME/XDG dirs only, TEST-ONLY values, never real secrets.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

TEST_KEY = "PERPLEXITY_API_KEY"


def _cfg(deepseek_env: str = ""):
    return SimpleNamespace(
        get=lambda *p, **kw: {"intent": {"deepseek_env": deepseek_env}}.
        get(p[0], {}).get(p[1], kw.get("default"))
        if len(p) > 1 else kw.get("default"))


def _isolate(monkeypatch, tmp_path) -> Path:
    """Point HOME + XDG dirs at tmp; return the canonical runtime .env path."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(TEST_KEY, raising=False)
    from voicebridge import config as C
    return C.canonical_runtime_env_path()


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


def _readers(deepseek_env: str):
    from voicebridge import orchestrator as ORCH
    cfg = _cfg(deepseek_env)
    fake = SimpleNamespace(cfg=cfg)
    return (lambda name: ORCH._web_env_key(cfg, name),
            lambda name: ORCH.VoiceOrchestrator._env_key(fake, name))


def test_runtime_env_fallback_when_voice_env_lacks_key(tmp_path, monkeypatch):
    runtime_env = _isolate(monkeypatch, tmp_path)
    _write(runtime_env, f"{TEST_KEY}=runtime-only-TEST-ONLY\n")
    voice_env = _write(tmp_path / "voice.env", "# no keys here\n")
    web_read, voice_read = _readers(voice_env)
    assert web_read(TEST_KEY) == "runtime-only-TEST-ONLY"
    assert voice_read(TEST_KEY) == "runtime-only-TEST-ONLY"


def test_configured_voice_env_wins_over_runtime(tmp_path, monkeypatch):
    runtime_env = _isolate(monkeypatch, tmp_path)
    _write(runtime_env, f"{TEST_KEY}=runtime-TEST-ONLY\n")
    voice_env = _write(tmp_path / "voice.env",
                       f"{TEST_KEY}=voice-wins-TEST-ONLY\n")
    web_read, voice_read = _readers(voice_env)
    assert web_read(TEST_KEY) == "voice-wins-TEST-ONLY"
    assert voice_read(TEST_KEY) == "voice-wins-TEST-ONLY"


def test_default_configured_path_falls_back_to_runtime(tmp_path, monkeypatch):
    # Live shape: intent.deepseek_env unset (defaults to the canonical
    # ~/.config .env, empty/missing) while the runtime .env holds the key.
    runtime_env = _isolate(monkeypatch, tmp_path)
    _write(runtime_env, f"{TEST_KEY}=runtime-default-TEST-ONLY\n")
    web_read, voice_read = _readers("")
    assert web_read(TEST_KEY) == "runtime-default-TEST-ONLY"
    assert voice_read(TEST_KEY) == "runtime-default-TEST-ONLY"


def test_missing_everywhere_returns_empty_then_environ(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    voice_env = _write(tmp_path / "voice.env", "# empty\n")
    web_read, voice_read = _readers(voice_env)
    assert web_read(TEST_KEY) == ""
    assert voice_read(TEST_KEY) == ""
    monkeypatch.setenv(TEST_KEY, "from-env-TEST-ONLY")
    assert web_read(TEST_KEY) == "from-env-TEST-ONLY"
    assert voice_read(TEST_KEY) == "from-env-TEST-ONLY"
