"""Focused fixes from the 2026-09-23 dashboard audit (/chat + /qubit-chat).

Three defects, all fixed by porting the reference/vendored behavior into the
owned modules (``voicebridge/chat_transport.py``, ``voicebridge/providers/
resolver.py``):

* **Default-model reconciliation.** ``/api/config`` serves the vendored runtime
  ``model.default`` while ``/api/model/info`` and ``/qubit-chat`` turns answered
  with the shipped local default. The resolver now honors the operator-configured
  default (reference resolution: ``model`` setting / runtime ``model.default``)
  when it maps to a registered, role-eligible profile, so every surface agrees.
* **Atomic turn handling.** A refused escalation used to persist the user row
  before the refusal, leaving an orphan user message. The escalation decision is
  now resolved before the user row is appended.
* **Honest context verification.** ``/chat`` reported the declared window as
  ``context_verified=false / not-cached`` because ``when_available`` never
  probed. The real vendored window is now resolved (bounded), so the surface
  agrees with the runtime.
* **Independence.** No ``~/.hermes`` fallback in either module.

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


def _frontier_for(provider: str, model: str) -> str:
    return ("intent:\n  frontier:\n    enabled: true\n"
            f"    allowlist: ['{provider}/{model}']\n")


def _api_profile(pid, provider, model, *, base_url, ctx, **over):
    from voicebridge.providers import resolver as R

    kwargs = dict(
        id=pid, kind=R.API, provider=provider, model=model, base_url=base_url,
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=ctx)
    kwargs.update(over)
    return R.ModelProfile(**kwargs)


def _write_runtime_model(cfg, model: str = "deepseek-v4-flash"):
    """Write the vendored runtime config ``model.default`` (the /api/config value)."""
    from voicebridge import qubit_runtime as QR

    home = QR.runtime_home_for_cfg(cfg)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"model:\n  default: {model}\n  provider: opencode-go\n",
        encoding="utf-8")
    return home


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    yield
    R._RUNTIME_MODEL_CACHE.clear()


# ---------------------------------------------------------------------------
# Item 1 — configured default reconciles /api/config, /api/model/info, turns
# ---------------------------------------------------------------------------
def test_config_surface_default_is_honored_by_resolver(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _frontier_for("opencode-go", "deepseek-v4-flash"))
    _write_runtime_model(cfg, "deepseek-v4-flash")
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)

    assert R.configured_default_model(cfg) == "deepseek-v4-flash"
    out = R.resolve_model(cfg)
    assert out["ok"] is True, out
    assert out["model"] == "deepseek-v4-flash"
    assert out["profile_id"] == "qubit-go"
    assert out["source"] == "configured-default"
    # The canonical default chat model agrees with the resolver.
    assert CT.configured_model(cfg) == "deepseek-v4-flash"


def test_explicit_active_selection_still_beats_configured_default(
        tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _frontier_for("opencode-go", "deepseek-v4-flash"))
    _write_runtime_model(cfg, "deepseek-v4-flash")
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)
    # A second profile, explicitly selected as active, must win.
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-alt", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)
    R.set_active_profile(cfg, "qubit-alt", confirm=True)
    out = R.resolve_model(cfg)
    assert out["ok"] is True, out
    assert out["profile_id"] == "qubit-alt"


def test_configured_default_without_registered_profile_falls_back(
        tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    _write_runtime_model(cfg, "ghost-model")
    # The candidate is reported honestly...
    assert R.configured_default_model(cfg) == "ghost-model"
    # ...but never silently substituted into an unregistered model.
    out = R.resolve_model(cfg)
    assert out["ok"] is True
    assert out["model"] == "qubit-brain"
    assert out["source"] != "configured-default"


def test_no_reference_home_fallback_for_default_model(tmp_path, monkeypatch):
    """With no Qubit runtime config, the resolver reads nothing from ~/.hermes."""
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    assert R._runtime_configured_model() is None
    assert R.configured_default_model(cfg) is None


# ---------------------------------------------------------------------------
# Item 2 — a refused escalation leaves no orphan user row
# ---------------------------------------------------------------------------
class _Decision:
    requires_escalation = True
    reasons = ["long-message"]
    tier = "escalated"
    window_tokens = 16384

    def public(self):
        return {"tier": self.tier, "window_tokens": self.window_tokens}


def test_refused_escalation_writes_no_user_row(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CT, "_context_prompt",
                        lambda *a, **k: ("", {}, _Decision()), raising=True)
    monkeypatch.setattr(
        CT, "_escalation_route",
        lambda *a, **k: {"ok": False, "code": "CONTEXT_ESCALATION_UNAVAILABLE",
                         "detail": "no escalation target"},
        raising=True)

    out = CT.turn(cfg, "a very large turn", session="audit-escalation-refused")
    assert out["ok"] is False
    assert out["code"] == "CONTEXT_ESCALATION_UNAVAILABLE"
    shown = CT.store(cfg).messages("audit-escalation-refused")
    assert shown["state"] == "OK"
    assert shown["total"] == 0, shown["messages"]


def test_successful_escalation_still_persists_the_user_row(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CT, "_context_prompt",
                        lambda *a, **k: ("", {}, _Decision()), raising=True)
    monkeypatch.setattr(
        CT, "_escalation_route",
        lambda *a, **k: {"ok": True, "model": "qubit-brain", "kind": "local"},
        raising=True)
    monkeypatch.setattr(
        CT, "_generate",
        lambda *a, **k: {"ok": True, "reply": "ok", "session_id": a[1]},
        raising=True)

    out = CT.turn(cfg, "a very large turn", session="audit-escalation-ok")
    assert out["ok"] is True
    shown = CT.store(cfg).messages("audit-escalation-ok")
    assert shown["total"] == 1
    assert shown["messages"][-1]["role"] == "user"


def test_refused_escalation_stream_writes_no_user_row(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _frontier_for("opencode-go", "deepseek-v4-flash"))
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)
    monkeypatch.setattr(CT, "_context_prompt",
                        lambda *a, **k: ("", {}, _Decision()), raising=True)
    monkeypatch.setattr(
        CT, "_escalation_route",
        lambda *a, **k: {"ok": False, "code": "CONTEXT_ESCALATION_UNAVAILABLE",
                         "detail": "no escalation target"},
        raising=True)

    events = list(CT._stream_remote(
        cfg, "a very large turn", session="audit-stream-refused",
        routed={"ok": True, "model": "deepseek-v4-flash", "provider":
                "opencode-go", "kind": "api", "context_tokens": 200000},
        timeout_s=1.0, cancel=None))
    assert events and events[0]["event"] == "error"
    assert events[0]["code"] == "CONTEXT_ESCALATION_UNAVAILABLE"
    shown = CT.store(cfg).messages("audit-stream-refused")
    assert shown["total"] == 0, shown["messages"]


# ---------------------------------------------------------------------------
# Item 3 — honest context verification against the real resolved window
# ---------------------------------------------------------------------------
def _guard_cfg(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _frontier_for("opencode-go", "deepseek-v4-flash"))
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)
    return cfg


def test_when_available_resolves_the_real_window(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    monkeypatch.setattr(mm, "get_cached_context_length",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: 1000000, raising=True)

    cfg = _guard_cfg(tmp_path, monkeypatch)
    out = R.terminal_context_guard(cfg, profile_id="qubit-go")
    assert out["ok"] is True, out
    assert out["context_verified"] is True
    assert out["context_window_source"] == "resolved"
    assert out["real_context_tokens"] == 1000000
    assert out["declared_context_tokens"] == 200000
    # N29: the effective window is the configured/declared profile value — the
    # same one /agent/api/model/info reports — not the advertised registry value
    # (the advertised value stays in ``real_context_tokens`` for audit).
    assert out["context_tokens"] == 200000


def test_cache_hit_skips_the_live_probe(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    monkeypatch.setattr(mm, "get_cached_context_length",
                        lambda *a, **k: 500000, raising=True)

    def _boom(*a, **k):
        raise AssertionError("live probe must not run on a cache hit")

    monkeypatch.setattr(mm, "get_model_context_length", _boom, raising=True)
    cfg = _guard_cfg(tmp_path, monkeypatch)
    out = R.terminal_context_guard(cfg, profile_id="qubit-go")
    assert out["ok"] is True
    assert out["context_window_source"] == "cache"
    assert out["real_context_tokens"] == 500000
    assert out["context_tokens"] == 200000


def test_generic_fallback_stays_unverified(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    monkeypatch.setattr(mm, "get_cached_context_length",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: mm.DEFAULT_FALLBACK_CONTEXT,
                        raising=True)
    cfg = _guard_cfg(tmp_path, monkeypatch)
    out = R.terminal_context_guard(cfg, profile_id="qubit-go")
    assert out["ok"] is True
    assert out["context_verified"] is False
    assert out["context_window_source"] == "fallback"
    assert out["context_tokens"] == 200000
