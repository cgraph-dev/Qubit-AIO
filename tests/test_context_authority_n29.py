"""N29 — one context authority for ``/chat`` and ``/agent/api/model/info``.

The ``/chat`` panel header renders ``context_tokens`` from
``GET /agent/api/terminal/profiles`` (the default profile row), while the model
badge / ``GET /agent/api/model/info`` reports ``effective_context_length``. The
terminal context guard used to promote the vendored static registry's
*advertised* window (``deepseek-v4-flash`` -> 1,000,000) over the profile's
configured/declared window (200,000), so the two dashboard surfaces disagreed.

The guard now reports the configured/declared window as the effective context —
the same value ``model/info`` reports — and keeps the advertised window in
``real_context_tokens`` for audit (it is still used for the floor refusal).

Hermetic: temp XDG trees, stubbed vendored metadata, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _api_profile(pid, provider, model, *, base_url, ctx):
    from voicebridge.providers import resolver as R

    return R.ModelProfile(
        id=pid, kind=R.API, provider=provider, model=model, base_url=base_url,
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=ctx)


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    yield
    R._RUNTIME_MODEL_CACHE.clear()


def test_chat_grant_and_model_info_report_one_context(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    # The vendored static registry advertises 1M for deepseek-v4-flash; the
    # configured profile declares 200k. The effective value must be the config.
    monkeypatch.setattr(mm, "get_cached_context_length",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: 1_000_000, raising=True)

    cfg = _cfg(tmp_path, monkeypatch,
               "intent:\n  frontier:\n    enabled: true\n"
               "    allowlist: ['opencode-go/deepseek-v4-flash']\n"
               "terminal:\n  profile: qubit-go\n")
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)
    R.set_active_profile(cfg, "qubit-go", confirm=True)

    # Surface 1 — the /chat grant (GET /agent/api/terminal/profiles default row).
    code, rows = HC.h_terminal_profiles(cfg, {}, {}, {})
    assert code == 200, rows
    grant = next(r for r in rows if r["id"] == "qubit-go")
    assert grant["default"] is True, rows
    assert grant["meets_floor"] is True, grant

    # Surface 2 — GET /agent/api/model/info.
    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info

    # One authority: the two surfaces agree for the configured model, and the
    # advertised registry value does not leak into either effective field.
    assert grant["context_tokens"] == info["effective_context_length"] == 200000
    assert info["config_context_length"] == 200000
    assert info["capabilities"]["context_window"] == 200000


def test_guard_reports_declared_and_keeps_advertised_for_audit(
        tmp_path, monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    monkeypatch.setattr(mm, "get_cached_context_length",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: 1_000_000, raising=True)

    cfg = _cfg(tmp_path, monkeypatch,
               "intent:\n  frontier:\n    enabled: true\n"
               "    allowlist: ['opencode-go/deepseek-v4-flash']\n")
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)

    out = R.terminal_context_guard(cfg, profile_id="qubit-go")
    assert out["ok"] is True, out
    assert out["context_tokens"] == 200000
    assert out["declared_context_tokens"] == 200000
    assert out["real_context_tokens"] == 1_000_000
    assert out["context_window_source"] == "resolved"
