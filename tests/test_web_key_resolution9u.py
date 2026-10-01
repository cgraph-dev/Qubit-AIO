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


def _cfg(deepseek_env: str = "", perplexity_env: str = ""):
    inner = {"deepseek_env": deepseek_env, "perplexity_env": perplexity_env}
    return SimpleNamespace(
        get=lambda *p, **kw: inner.get(p[1], kw.get("default"))
        if len(p) > 1 and p[0] == "intent" else kw.get("default"))


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


def _readers(deepseek_env: str, perplexity_env: str = ""):
    from voicebridge import orchestrator as ORCH
    cfg = _cfg(deepseek_env, perplexity_env)
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


def test_custom_perplexity_env_is_honored(tmp_path, monkeypatch):
    # Wart fix: intent.perplexity_env must be consulted for PERPLEXITY_*
    # names — a custom path holding only the TEST-ONLY key must win even
    # when deepseek_env + runtime .env lack it. Hermetic tmp dirs only.
    runtime_env = _isolate(monkeypatch, tmp_path)
    _write(runtime_env, "# no keys here\n")
    voice_env = _write(tmp_path / "voice.env", "# no keys here\n")
    pplx_env = _write(tmp_path / "pplx.env",
                      f"{TEST_KEY}=pplx-custom-TEST-ONLY\n")
    web_read, voice_read = _readers(voice_env, pplx_env)
    assert web_read(TEST_KEY) == "pplx-custom-TEST-ONLY"
    assert voice_read(TEST_KEY) == "pplx-custom-TEST-ONLY"


def test_deepseek_env_still_honored_for_perplexity_key(tmp_path, monkeypatch):
    # perplexity_env unset -> the deepseek_env file still serves
    # PERPLEXITY_* names (back-compat with single-file configs).
    runtime_env = _isolate(monkeypatch, tmp_path)
    _write(runtime_env, "# no keys here\n")
    voice_env = _write(tmp_path / "voice.env",
                       f"{TEST_KEY}=via-deepseek-env-TEST-ONLY\n")
    web_read, voice_read = _readers(voice_env, "")
    assert web_read(TEST_KEY) == "via-deepseek-env-TEST-ONLY"
    assert voice_read(TEST_KEY) == "via-deepseek-env-TEST-ONLY"


def test_perplexity_env_wins_over_deepseek_env(tmp_path, monkeypatch):
    # Both set, both hold the key -> perplexity_env first (documented
    # precedence: perplexity_env -> deepseek_env -> runtime .env -> environ).
    _isolate(monkeypatch, tmp_path)
    voice_env = _write(tmp_path / "voice.env",
                       f"{TEST_KEY}=deepseek-file-TEST-ONLY\n")
    pplx_env = _write(tmp_path / "pplx.env",
                      f"{TEST_KEY}=pplx-wins-TEST-ONLY\n")
    web_read, voice_read = _readers(voice_env, pplx_env)
    assert web_read(TEST_KEY) == "pplx-wins-TEST-ONLY"
    assert voice_read(TEST_KEY) == "pplx-wins-TEST-ONLY"


def test_same_file_both_keys_byte_identical(tmp_path, monkeypatch):
    # Default shape: both settings point at the same file -> identical
    # result to the deepseek_env-only path (no behavior change).
    _isolate(monkeypatch, tmp_path)
    shared = _write(tmp_path / "shared.env", f"{TEST_KEY}=shared-TEST-ONLY\n")
    web_read, voice_read = _readers(shared, shared)
    assert web_read(TEST_KEY) == "shared-TEST-ONLY"
    assert voice_read(TEST_KEY) == "shared-TEST-ONLY"


def test_non_perplexity_key_ignores_perplexity_env(tmp_path, monkeypatch):
    # Deepseek behavior identical: a DEEPSEEK_* name never reads the
    # perplexity_env file, even when it holds the key.
    _isolate(monkeypatch, tmp_path)
    voice_env = _write(tmp_path / "voice.env",
                       "DEEPSEEK_API_KEY=deepseek-TEST-ONLY\n")
    pplx_env = _write(tmp_path / "pplx.env",
                      "DEEPSEEK_API_KEY=pplx-leak-TEST-ONLY\n")
    web_read, voice_read = _readers(voice_env, pplx_env)
    assert web_read("DEEPSEEK_API_KEY") == "deepseek-TEST-ONLY"
    assert voice_read("DEEPSEEK_API_KEY") == "deepseek-TEST-ONLY"
