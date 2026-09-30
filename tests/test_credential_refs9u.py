"""9u — credential ``env:`` refs resolve from the canonical runtime ``.env``.

The migrated API profiles declare ``env:NAME`` references, but the process
environment often lacks the variable: the real key lives in the canonical
runtime ``.env`` (``<state_dir>/runtime/.env``). These tests prove, hermetically
and with an isolated ``XDG_STATE_HOME`` (never the operator's real
``~/.hermes``):

* an ``env:`` reference resolves from the canonical runtime ``.env`` while the
  process environment still wins when it carries the variable;
* a missing variable is a typed ``AUTH_MISSING`` (never a raw key or a raise);
* the value never leaks through a redaction, a typed result, or a log line;
* a config-declared profile ``policy``/``allowlist`` override flips
  ``executable`` without weakening the fail-closed default; and
* the declared context window is overridable and the ``/chat`` floor is
  enforced before a profile is admitted.

Only ``voicebridge/config.py``, ``voicebridge/providers/credentials.py`` and
``voicebridge/providers/resolver.py`` are under test.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# A value shaped like a live key so any leakage is unmistakable and greppable.
SECRET = "sk-opencode-go-9u-DO-NOT-LEAK-0123456789abcdef"


def _cfg(tmp_path, monkeypatch, extra_yaml: str = ""):
    """An isolated Config (temp XDG roots; never the operator's real state)."""
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    path = tmp_path / "voice.yaml"
    path.write_text("logging:\n  jsonl: false\n" + extra_yaml)
    return C.Config.load(path)


def _write_runtime_env(text: str) -> Path:
    from voicebridge import config as C

    path = C.canonical_runtime_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _api_profile(**over):
    from voicebridge.providers import resolver as R

    kwargs = dict(
        id="qubit-model", kind="api", provider="opencode-go",
        model="deepseek-v4.1-flash",
        base_url="https://api.example.invalid/v1",
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=8192)
    kwargs.update(over)
    return R.ModelProfile(**kwargs)


# ---------------------------------------------------------------- env resolution
def test_env_ref_resolves_from_canonical_runtime_env(tmp_path, monkeypatch):
    from voicebridge.providers.credentials import resolve_key

    _cfg(tmp_path, monkeypatch)
    _write_runtime_env(
        "# provider keys\n"
        f"OPENCODE_GO_API_KEY={SECRET}\n"
        "UNRELATED=ignored\n")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)

    key, code = resolve_key("opencode-go", "env:OPENCODE_GO_API_KEY")
    assert code == ""
    assert key == SECRET


def test_env_file_supports_export_and_quotes(tmp_path, monkeypatch):
    from voicebridge.providers.credentials import resolve_key

    _cfg(tmp_path, monkeypatch)
    _write_runtime_env(f'export OPENCODE_GO_API_KEY="{SECRET}"\n')
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)

    key, code = resolve_key("opencode-go", "env:OPENCODE_GO_API_KEY")
    assert (key, code) == (SECRET, "")


def test_process_env_wins_over_runtime_env(tmp_path, monkeypatch):
    from voicebridge.providers.credentials import resolve_key

    _cfg(tmp_path, monkeypatch)
    _write_runtime_env(f"OPENCODE_GO_API_KEY={SECRET}\n")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-process-wins")

    key, code = resolve_key("opencode-go", "env:OPENCODE_GO_API_KEY")
    assert (key, code) == ("sk-process-wins", "")


def test_missing_env_ref_is_typed_auth_missing(tmp_path, monkeypatch):
    from voicebridge.providers.credentials import resolve_key

    _cfg(tmp_path, monkeypatch)
    _write_runtime_env("SOME_OTHER_KEY=whatever\n")
    monkeypatch.delenv("QUBIT_ABSENT_9U", raising=False)

    key, code = resolve_key("opencode-go", "env:QUBIT_ABSENT_9U")
    assert key == "" and code == "AUTH_MISSING"


def test_unreadable_runtime_env_fails_closed(tmp_path, monkeypatch):
    from voicebridge.providers.credentials import resolve_key

    _cfg(tmp_path, monkeypatch)
    # A directory where the .env file would be: read must not raise.
    env_path = _write_runtime_env("")
    env_path.unlink()
    env_path.mkdir()
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)

    key, code = resolve_key("opencode-go", "env:OPENCODE_GO_API_KEY")
    assert key == "" and code == "AUTH_MISSING"


# ------------------------------------------------------------- leakage checks
def test_secret_ref_helpers_never_leak_value(tmp_path, monkeypatch):
    from voicebridge.providers.credentials import (
        SecretRef, redact_ref, redact_secret_ref, resolve_secret_ref)

    _cfg(tmp_path, monkeypatch)
    _write_runtime_env(f"OPENCODE_GO_API_KEY={SECRET}\n")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)

    key, code = resolve_secret_ref("env:OPENCODE_GO_API_KEY", "opencode-go")
    assert key == SECRET and code == ""

    for field in (redact_ref("env:OPENCODE_GO_API_KEY"),
                  redact_secret_ref(SecretRef("env", "OPENCODE_GO_API_KEY")),
                  redact_secret_ref({"source": "env",
                                     "name": "OPENCODE_GO_API_KEY"}),
                  code):
        assert SECRET not in str(field), field


def test_resolver_result_and_logs_never_expose_secret(tmp_path, monkeypatch,
                                                      caplog):
    from voicebridge.providers import resolver as R

    body = (
        "intent:\n"
        "  frontier:\n"
        "    profile_overrides:\n"
        "      qubit-model:\n"
        "        policy_enabled: true\n"
        "        allowlisted: true\n")
    cfg = _cfg(tmp_path, monkeypatch, body)
    _write_runtime_env(f"OPENCODE_GO_API_KEY={SECRET}\n")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    assert R.upsert_model_profile(cfg, _api_profile(), confirm=True)["state"] == "OK"

    with caplog.at_level(logging.DEBUG):
        eff = R.resolve(cfg, profile_id="qubit-model")
        routed = R.resolve_model(cfg, "qubit-model")
        listed = R.list_profiles(cfg)

    assert routed["ok"] is True
    blob = (json.dumps(eff.to_public_dict()) + json.dumps(routed)
            + json.dumps(listed) + (caplog.text or ""))
    assert SECRET not in blob
    # The reference may appear; the value must not.
    assert eff.key_ref == "env:OPENCODE_GO_API_KEY"


# ------------------------------------------------ policy / allowlist override
def test_profile_override_changes_executable(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    closed = _cfg(tmp_path, monkeypatch)
    assert R.upsert_model_profile(closed, _api_profile(), confirm=True)["state"] == "OK"
    before = R.resolve_model(closed, "qubit-model")
    assert before["ok"] is False
    assert before["code"] in ("PROFILE_NOT_EXECUTABLE", "EGRESS_DENIED")

    opened = _cfg(
        tmp_path, monkeypatch,
        "intent:\n  frontier:\n    profile_overrides:\n"
        "      qubit-model:\n        policy_enabled: true\n"
        "        allowlisted: true\n")
    after = R.resolve_model(opened, "qubit-model")
    assert after["ok"] is True
    assert after["executable"] is True


def test_profile_override_absent_is_fail_closed(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)  # global frontier disabled, no override
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    eff = R.resolve(cfg, profile_id="qubit-model")
    assert eff.executable is False
    assert eff.policy_enabled is False
    assert eff.allowlisted is False


def test_malformed_and_unknown_overrides_are_dropped(tmp_path, monkeypatch):
    from voicebridge import config as C

    cfg = _cfg(
        tmp_path, monkeypatch,
        "intent:\n  frontier:\n    profile_overrides:\n"
        "      qubit-model:\n        policy_enabled: maybe\n"
        "        allowlisted: definitely\n        context_tokens: NaN\n"
        "        key_ref: env:SHOULD_BE_DROPPED\n")
    assert C.model_profile_overrides(cfg, "qubit-model") == {}

    cfg2 = _cfg(
        tmp_path, monkeypatch,
        "intent:\n  frontier:\n    profile_overrides:\n"
        "      qubit-model:\n        allowlisted: true\n"
        "        context_tokens: 131072\n")
    assert C.model_profile_overrides(cfg2, "qubit-model") == {
        "allowlisted": True, "context_tokens": 131072}


# ---------------------------------------------------------- context floor
def test_context_floor_honored_and_override_applies(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    base_body = (
        "terminal:\n"
        "  min_context_tokens: 65536\n"
        "  context_window_verification: declared\n"
        "intent:\n"
        "  frontier:\n"
        "    profile_overrides:\n"
        "      qubit-model:\n"
        "        policy_enabled: true\n"
        "        allowlisted: true\n")
    cfg = _cfg(tmp_path, monkeypatch, base_body)
    R.upsert_model_profile(cfg, _api_profile(context_tokens=8192), confirm=True)

    below = R.terminal_context_guard(cfg, profile_id="qubit-model")
    assert below["ok"] is False
    assert below["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
    assert below["context_tokens"] == 8192

    raised = _cfg(
        tmp_path, monkeypatch,
        base_body + "        context_tokens: 131072\n")
    ok = R.terminal_context_guard(raised, profile_id="qubit-model")
    assert ok["ok"] is True
    assert ok["context_tokens"] == 131072
    assert ok["min_context_tokens"] == 65536


def test_context_floor_default_is_unchanged(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    assert R.terminal_min_context_tokens(cfg) == R.TERMINAL_MIN_CONTEXT_TOKENS
