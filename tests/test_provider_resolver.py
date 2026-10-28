"""Canonical model/provider profile resolver + chat integration.

Covers the required first implementation slice from
``docs/archive/2026-09-21/QUBIT_CURRENT_ARCHITECTURE_GAP_AUDIT_2026-09-15.md``:

* one resolver answers "which provider/model/policy is effective" for a
  session/task role (PV-01, PV-09);
* the shipped Brain4 default is registry data, not a hard-wired code path, and
  can be replaced/selected per session (PV-02);
* dashboard chat accepts a registered non-default/API model and executes it
  through the single provider gateway with a truthful receipt (PV-04, PV-03 for
  the chat path);
* credentials are secret references only (raw keys refused).

Hermetic: no network. The gateway is stubbed at its boundary for the API path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, frontier: bool = False):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    body = "logging:\n  jsonl: false\n"
    if frontier:
        body += (
            "intent:\n"
            "  frontier:\n"
            "    enabled: true\n"
            "    allowlist: ['openai/gpt-4o-mini']\n")
    p.write_text(body)
    return C.Config.load(p)


def _api_profile(pid="my-api", **over):
    from voicebridge.providers import resolver as R

    kwargs = dict(
        id=pid, kind="api", provider="openai", model="gpt-4o-mini",
        base_url="https://api.openai.com/v1", key_ref="env:OPENAI_API_KEY",
        locality="external", egress_consent="allow", roles=["chat", "hard"])
    kwargs.update(over)
    return R.ModelProfile(**kwargs)


# ------------------------------------------------------------------ default
def test_default_resolves_to_shipped_local_brain(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    eff = R.resolve(cfg)
    assert eff.profile_id == "qubit-brain"
    assert eff.kind == "local" and eff.route == "local"
    assert eff.locality == "local" and eff.egress_required is False
    assert eff.executable is True


def test_default_brain_is_replaceable_registry_data(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    assert [p.id for p in R.load_profiles(cfg)] == ["qubit-brain"]
    # A loaded local profile may be cloned/edited; the default stays a record.
    assert R.clone_model_profile(cfg, "qubit-brain", "my-local",
                                 confirm=True)["state"] == "OK"
    assert R.get_profile(cfg, "my-local") is not None
    assert R.get_profile(cfg, "qubit-brain").default is True


def test_remove_default_is_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(R.ResolverError) as exc:
        R.remove_model_profile(cfg, "qubit-brain", confirm=True)
    assert exc.value.code == "IMMUTABLE_DEFAULT"


# ------------------------------------------------------------------ security
def test_raw_secret_in_profile_is_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    p = _api_profile(key_ref="sk-abcd1234abcd1234abcd1234abcd1234")
    out = R.upsert_model_profile(cfg, p, confirm=True)
    assert out["state"] == "FAILED" and out["code"] == "RAW_SECRET"


def test_secret_ref_helpers():
    from voicebridge.providers import resolver as R

    assert R.is_secret_ref("env:OPENAI_API_KEY")
    assert R.is_secret_ref("file:~/.config/qubit/secrets/openai")
    assert not R.is_secret_ref("sk-live-1234567890abcdef")
    assert R.looks_like_raw_secret("sk-live-1234567890abcdef")
    assert not R.looks_like_raw_secret("env:OPENAI_API_KEY")


def test_api_profile_cannot_be_default(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    out = R.upsert_model_profile(cfg, _api_profile(default=True), confirm=True)
    assert out["state"] == "FAILED" and out["code"] == "EXTERNAL_DEFAULT"


# ------------------------------------------------------------ API selection
def test_api_profile_not_executable_without_policy_and_consent(tmp_path,
                                                               monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, frontier=False)
    out = R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    assert out["state"] == "OK"
    routed = R.resolve_model(cfg, "my-api")
    assert routed["ok"] is False
    assert routed["code"] in ("PROFILE_NOT_EXECUTABLE", "EGRESS_DENIED")


def test_api_profile_executable_when_policy_consent_allowlist_met(tmp_path,
                                                                  monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    routed = R.resolve_model(cfg, "my-api")
    assert routed["ok"] is True
    assert (routed["provider"], routed["model"], routed["route"]) == (
        "openai", "gpt-4o-mini", "frontier")
    assert routed["locality"] == "external"


def test_session_override_selects_and_clears(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_session_profile(cfg, "s1", "my-api", confirm=True)
    eff = R.resolve(cfg, session="s1")
    assert eff.profile_id == "my-api" and eff.source == "session"
    # other sessions keep the default
    assert R.resolve(cfg, session="s2").profile_id == "qubit-brain"
    R.clear_session_profile(cfg, "s1")
    assert R.resolve(cfg, session="s1").profile_id == "qubit-brain"


def test_remove_profile_in_use_is_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_session_profile(cfg, "s1", "my-api", confirm=True)
    out = R.remove_model_profile(cfg, "my-api", confirm=True)
    assert out["state"] == "DENIED" and out["code"] == "PROFILE_IN_USE"


def test_fallback_chain_is_recorded(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_model_profile(cfg, R.ModelProfile(
        id="smart", kind="local", provider="ollama", model="phi4",
        locality="local", fallback_to="qubit-brain"), confirm=True)
    eff = R.resolve(cfg, profile_id="smart")
    assert eff.fallback_chain == ("qubit-brain",)


# ----------------------------------------------------- chat transport wiring
def test_chat_resolve_model_accepts_registered_profile(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    ok = CT.resolve_route(cfg, "my-api")
    assert ok["ok"] is True and ok["profile_id"] == "my-api"
    # minimal backward-compatible shape still returns the effective model
    assert CT.resolve_model(cfg, "my-api") == {
        "ok": True, "model": "gpt-4o-mini"}
    # unknown model is still a typed refusal
    bad = CT.resolve_model(cfg, "gpt-4o")
    assert bad["ok"] is False and bad["code"] == "MODEL_NOT_ALLOWED"


def test_chat_turn_routes_api_profile_through_gateway(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderResult, UsageRecord

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)

    calls = []

    def fake_run(req, cfg, **kw):
        calls.append(req)
        assert req.route == "frontier"
        return ProviderResult(
            ok=True, text="remote reply", provider="openai",
            model="gpt-4o-mini", identity="openai/gpt-4o-mini",
            usage=UsageRecord(provider="openai", model="gpt-4o-mini",
                              identity="openai/gpt-4o-mini", latency_ms=12))

    monkeypatch.setattr(GW, "run", fake_run)
    res = CT.turn(cfg, "hello", model="my-api", session="s1")
    assert res["ok"] is True and res["reply"] == "remote reply"
    assert res["receipt"]["runtime"] == "provider-gateway"
    assert res["receipt"]["identity"] == "openai/gpt-4o-mini"
    assert calls and calls[0].provider == "openai"
    stored = [m["role"] for m in
              CT.store(cfg).show("s1")["messages"]]
    assert stored == ["user", "assistant"]


def test_chat_stream_api_profile_emits_truthful_envelope(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderResult

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    monkeypatch.setattr(GW, "run", lambda req, cfg, **kw: ProviderResult(
        ok=True, text="streamed remote", provider="openai",
        model="gpt-4o-mini", identity="openai/gpt-4o-mini"))
    events = list(CT.stream_turn(cfg, "hi", model="my-api", session="s2"))
    kinds = [e["event"] for e in events]
    assert kinds == ["start", "delta", "done"]
    assert events[-1]["reply"] == "streamed remote"
    assert events[-1]["receipt"]["streaming"] is False


# ------------------------------------------------------------- session stats
def test_sessions_stats_does_not_raise_on_any_state(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = HC.h_sessions_stats(cfg, {}, {}, {})
    assert status == 200 and payload.get("ok") is True
    assert isinstance(payload.get("by_state"), dict)


# ------------------------------------------------- session-role isolation (V2)
def test_speech_selection_does_not_block_model_removal(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as SP

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    out_p = SP.load_speech_profiles(cfg, SP.SPEECH_OUT)[0]
    SP.set_session_speech_profile(cfg, "s1", SP.SPEECH_OUT, out_p.id,
                                  confirm=True)
    removed = R.remove_model_profile(cfg, "my-api", confirm=True)
    assert removed["state"] == "OK", removed


def test_turn_honors_role_selection(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_model_profile(cfg, R.ModelProfile(
        id="local-coder", kind="local", provider="ollama", model="phi4",
        locality="local", roles=["coding"]), confirm=True)
    R.set_session_profile(cfg, "s1", "local-coder", role="coding",
                          confirm=True)
    assert CT.resolve_route(cfg, None, session="s1",
                            role="coding")["profile_id"] == "local-coder"
    assert CT.resolve_route(cfg, None,
                            session="s1")["profile_id"] == "qubit-brain"


# ------------------------------------------------- gateway execution (P2/PV-03)
def _no_urllib(monkeypatch):
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("network call bypassed the injected transport/adapter")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_run_role_local_profile_model_reaches_local_adapter(tmp_path,
                                                           monkeypatch):
    """The resolved local profile's model must reach the local adapter."""
    import voicebridge.local_llm as LL
    from voicebridge.providers import gateway as GW
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_model_profile(cfg, R.ModelProfile(
        id="my-local", kind="local", provider="ollama", model="phi4",
        locality="local", roles=["chat"]), confirm=True)

    captured = {}

    def fake_chat_local(text, conversation_prompt="", **kw):
        captured["model"] = kw.get("model")
        return "phi4 says hi"

    monkeypatch.setattr(LL, "chat_local", fake_chat_local)
    _no_urllib(monkeypatch)

    res = GW.run_role("hello", cfg, profile_id="my-local")
    assert res.ok and res.text == "phi4 says hi"
    assert captured["model"] == "phi4"
    assert res.identity == "local/phi4"


def test_run_role_default_still_resolves_brain4(tmp_path, monkeypatch):
    import voicebridge.local_llm as LL
    from voicebridge.providers import gateway as GW

    cfg = _cfg(tmp_path, monkeypatch)
    captured = {}

    def fake_chat_local(text, conversation_prompt="", **kw):
        captured["model"] = kw.get("model")
        return "brain reply"

    monkeypatch.setattr(LL, "chat_local", fake_chat_local)
    _no_urllib(monkeypatch)

    res = GW.run_role("hi", cfg)
    assert res.ok and res.text == "brain reply"
    assert captured["model"] == "qubit-brain"


def test_run_role_generate_mode_sends_profile_model_to_adapter(tmp_path,
                                                                monkeypatch):
    """The generate adapter payload carries the resolved profile model."""
    import json
    import urllib.request
    from voicebridge.providers import gateway as GW
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_model_profile(cfg, R.ModelProfile(
        id="gen-local", kind="local", provider="ollama", model="phi4-mini",
        locality="local", roles=["summarize"]), confirm=True)

    seen = {}

    class _Resp:
        status = 200

        def read(self):
            return json.dumps({"response": "summary ok"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/generate"):
            seen["payload"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = GW.run_role("summarize this", cfg, profile_id="gen-local",
                      role="summarize", local_mode="generate")
    assert res.ok and res.text == "summary ok"
    assert seen["payload"]["model"] == "phi4-mini"


def test_run_executes_frontier_with_injected_transport(tmp_path, monkeypatch):
    """Frontier executes through the gateway transport, never raw urllib."""
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderRequest

    cfg = _cfg(tmp_path, monkeypatch, frontier=True)
    seen = {}

    def fake_transport(url, headers, payload, timeout_s):
        seen["url"] = url
        seen["headers"] = headers
        seen["payload"] = payload
        return 200, {"choices": [{"message": {"content": "remote answer"}}],
                     "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    _no_urllib(monkeypatch)

    res = GW.run(
        ProviderRequest(prompt="hello", route="frontier", provider="openai",
                        model="gpt-4o-mini"),
        cfg, transport=fake_transport,
        base_url="https://api.openai.com/v1", api_key="test-key")
    assert res.ok and res.text == "remote answer"
    assert res.provider == "openai" and res.model == "gpt-4o-mini"
    assert res.identity == "openai/gpt-4o-mini"
    assert seen["payload"]["model"] == "gpt-4o-mini"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
