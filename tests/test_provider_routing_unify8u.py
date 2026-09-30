"""Provider routing unification (A4/A5/A45, PV-02/PV-04).

Focused regression tests for the wave-17 provider/routing slice:

* A4 — the local chat turn executes through the canonical provider gateway
  (``gateway.run_role``) instead of calling ``local_llm.chat_local`` directly;
* A5 — ``embedding`` is a canonical resolver role and embeddings route through
  ``gateway.embed_texts`` + the one shared local adapter (router L1 and semantic
  memory no longer issue their own Ollama requests);
* A45 — a ``plugin`` provider kind and the OpenClaw Radius adapter, keyed by
  ``env:`` references and valid config-only without a present key;
* PV-02 — no hard-coded ``qubit-brain4`` default-selection; the resolver
  answers the default;
* PV-04 — the resolved provider/model/role identity reaches the receipt.

Hermetic: temp XDG trees, no network, no live model.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, frontier: bool = False):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    body = "logging:\n  jsonl: false\n"
    if frontier:
        body += ("intent:\n"
                 "  frontier:\n"
                 "    enabled: true\n"
                 "    allowlist: ['radius/balanced']\n")
    p = tmp_path / "voice.yaml"
    p.write_text(body)
    return C.Config.load(p)


class _NoGet:
    """Config stand-in with no ``get`` (resolver/agent must still work)."""


# ---------------------------------------------------------------------------
# A4 — local chat turn executes through the gateway
# ---------------------------------------------------------------------------
def test_agent_turn_executes_through_gateway(monkeypatch):
    from voicebridge import agent as A
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderResult, UsageRecord

    calls = []

    def _fake_run_role(prompt, cfg, **kw):
        calls.append(kw)
        return ProviderResult(
            ok=True, text="gateway reply", provider="local",
            model="qubit-brain4", identity="local/qubit-brain4",
            usage=UsageRecord(provider="local", model="qubit-brain4",
                              identity="local/qubit-brain4", latency_ms=2),
            details={"profile": "qubit-brain4", "role": "chat",
                     "source": "role-default", "route": "local"})

    monkeypatch.setattr(GW, "run_role", _fake_run_role)

    # A direct local_llm call would be a bypass: prove it is never reached.
    import voicebridge.local_llm as LL

    def _boom(*a, **k):
        raise AssertionError("agent.turn bypassed the gateway")

    monkeypatch.setattr(LL, "chat_local", _boom)

    r = A.turn(None, "hi", session="s")
    assert r["ok"] is True and r["reply"] == "gateway reply"
    assert calls and calls[0].get("role") == "chat"


def test_agent_turn_forwards_role_and_profile(monkeypatch):
    from voicebridge import agent as A
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderResult

    seen = {}

    def _fake_run_role(prompt, cfg, **kw):
        seen.update(kw)
        return ProviderResult(ok=True, text="ok", provider="local",
                              model="phi4", identity="local/phi4")

    monkeypatch.setattr(GW, "run_role", _fake_run_role)
    r = A.turn(None, "code this", session="s", role="coding",
               profile_id="local-coder")
    assert r["ok"] is True
    assert seen.get("role") == "coding"
    assert seen.get("profile_id") == "local-coder"


def test_gateway_local_adapter_still_reaches_chat_local(monkeypatch):
    """The local adapter stays behind the gateway (inference not removed)."""
    import voicebridge.local_llm as LL
    from voicebridge.providers import gateway as GW

    seen = {}

    def _fake_chat_local(text, conversation_prompt="", **kw):
        seen["model"] = kw.get("model")
        return "local through gateway"

    monkeypatch.setattr(LL, "chat_local", _fake_chat_local)
    res = GW.run_role("hi", None, role="chat")
    assert res.ok and res.text == "local through gateway"
    assert seen["model"] == "qubit-brain4"


# ---------------------------------------------------------------------------
# A5 — embedding role + shared adapter
# ---------------------------------------------------------------------------
def test_embedding_role_resolves_locally_without_changing_inventory():
    from voicebridge.providers import resolver as R

    assert "embedding" in R.ROLES
    eff = R.resolve(None, role="embedding")
    assert eff.profile_id == R.DEFAULT_EMBED_ID
    assert eff.model == "nomic-embed-text"
    assert eff.kind == "local" and eff.route == "local"
    assert eff.executable is True
    # Adding the role must not change the shipped model-profile inventory.
    assert [p.id for p in R.default_profiles()] == ["qubit-brain4"]


def test_embedding_role_session_override(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    assert R.upsert_model_profile(cfg, R.ModelProfile(
        id="my-embed", kind="local", provider="ollama",
        model="mxbai-embed-large", locality="local",
        roles=["embedding"]), confirm=True)["state"] == "OK"
    R.set_session_profile(cfg, "s1", "my-embed", role="embedding",
                          confirm=True)
    eff = R.resolve_embedding(cfg, session="s1")
    assert eff.profile_id == "my-embed" and eff.model == "mxbai-embed-large"


def test_embed_texts_routes_through_resolver_and_shared_adapter(monkeypatch):
    from voicebridge.providers import embedding as E
    from voicebridge.providers import gateway as GW

    seen = {}

    def _fake_embed_local(texts, *, url="", model="", timeout_s=10.0,
                          num_gpu=0):
        seen.update(texts=list(texts), model=model, num_gpu=num_gpu)
        return [[0.25, 0.75] for _ in texts]

    monkeypatch.setattr(E, "embed_local", _fake_embed_local)
    out = GW.embed_texts(["hello", "world"], None)
    assert out == [[0.25, 0.75], [0.25, 0.75]]
    assert seen["model"] == "nomic-embed-text"
    assert seen["num_gpu"] == 0


def test_l1_router_uses_gateway_embedding(monkeypatch):
    from voicebridge.providers import gateway as GW
    from voicebridge.router.l1 import SemanticRouter

    seen = {}

    def _fake_embed_texts(texts, cfg=None, *, url="", model="",
                          timeout_s=None, num_gpu=0):
        seen.update(texts=list(texts), model=model)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(GW, "embed_texts", _fake_embed_texts)
    router = SemanticRouter(routes={"a": ["x"]})
    router.cfg = object()
    vecs = router.embed(["tell me something"], query=True)
    assert vecs == [[1.0, 0.0]]
    assert seen["texts"][0].startswith("search_query: ")


def test_semantic_ollama_embed_uses_gateway(monkeypatch):
    from voicebridge.memory.semantic import OllamaEmbed
    from voicebridge.providers import gateway as GW

    seen = {}

    def _fake_embed_texts(texts, cfg=None, **kw):
        seen.update(kw)
        return [[0.5] for _ in texts]

    monkeypatch.setattr(GW, "embed_texts", _fake_embed_texts)
    embed = OllamaEmbed()
    assert embed.embed(["x"]) == [[0.5]]
    assert seen["model"] == "nomic-embed-text"


# ---------------------------------------------------------------------------
# A45 — provider-plugin kind + Radius adapter (env: refs, config-only)
# ---------------------------------------------------------------------------
def test_radius_plugin_kind_is_valid_config_only(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    assert "plugin" in R.KINDS
    prof = R.radius_profile()
    assert prof.kind == "plugin" and prof.provider == "radius"
    assert prof.base_url == R.RADIUS_BASE_URL
    assert prof.key_ref == "env:RADIUS_API_KEY"
    assert prof.validate(cfg)["state"] == "OK"

    # Config-only: valid with no key reference at all.
    no_key = R.ModelProfile(
        id="radius-nokey", kind="plugin", provider="radius", model="balanced",
        base_url=R.RADIUS_BASE_URL, key_ref="", locality="external",
        roles=["chat"])
    assert no_key.validate(cfg)["state"] == "OK"

    assert R.upsert_model_profile(cfg, prof, confirm=True)["state"] == "OK"
    stored = R.get_profile(cfg, "radius")
    assert stored.kind == "plugin" and stored.key_ref == "env:RADIUS_API_KEY"


def test_radius_plugin_raw_secret_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    bad = R.radius_profile(key_ref="sk-abcd1234abcd1234abcd1234abcd1234")
    assert bad.validate(cfg)["code"] == "RAW_SECRET"


def test_radius_plugin_gated_and_refused_without_key(tmp_path, monkeypatch):
    from voicebridge.providers import gateway as GW
    from voicebridge.providers import resolver as R

    # Frontier disabled -> the external plugin is not executable.
    cfg_off = _cfg(tmp_path, monkeypatch, frontier=False)
    assert R.upsert_model_profile(cfg_off, R.radius_profile(
        egress_consent="allow"), confirm=True)["state"] == "OK"
    assert R.resolve_model(cfg_off, "radius")["ok"] is False

    # Enabled + allowlisted but no RADIUS_API_KEY -> typed AUTH_MISSING, never
    # a silent local substitution.
    cfg_on = _cfg(tmp_path, monkeypatch, frontier=True)
    assert R.upsert_model_profile(cfg_on, R.radius_profile(
        egress_consent="allow"), confirm=True)["state"] == "OK"
    assert R.resolve_model(cfg_on, "radius")["ok"] is True
    res = GW.run_role("hello", cfg_on, profile_id="radius", role="chat")
    assert res.ok is False and res.code == "AUTH_MISSING", res


# ---------------------------------------------------------------------------
# PV-02 — canonical resolver default, no hard-coded alias
# ---------------------------------------------------------------------------
def test_configured_model_uses_resolver_default(monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R

    class _Eff:
        model = "resolver-chosen"

    monkeypatch.setattr(R, "resolve", lambda *a, **k: _Eff())
    assert CT.configured_model(_NoGet()) == "resolver-chosen"


def test_agent_model_falls_back_to_resolver_default(monkeypatch):
    from voicebridge import agent as A
    from voicebridge.providers import resolver as R

    class _Eff:
        model = "resolver-chosen"

    monkeypatch.setattr(R, "resolve", lambda *a, **k: _Eff())
    assert A._model(_NoGet()) == "resolver-chosen"


# ---------------------------------------------------------------------------
# PV-04 — resolved identity propagation into the receipt
# ---------------------------------------------------------------------------
def test_receipt_carries_resolved_identity(monkeypatch):
    from voicebridge import agent as A
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderResult, UsageRecord

    res = ProviderResult(
        ok=True, text="hello", provider="local", model="phi4",
        identity="local/phi4",
        usage=UsageRecord(provider="local", model="phi4",
                          identity="local/phi4", latency_ms=4),
        details={"profile": "my-local", "role": "chat", "source": "explicit",
                 "route": "local", "locality": "local"})

    monkeypatch.setattr(GW, "run_role", lambda *a, **k: res)
    receipt = A.turn(None, "hi")["receipt"]
    assert receipt["runtime"] == "qubit-native"
    assert receipt["model"] == "phi4"
    assert receipt["provider"] == "local"
    assert receipt["identity"] == "local/phi4"
    assert receipt["profile"] == "my-local"
    assert receipt["role"] == "chat"
    assert receipt["usage"]["identity"] == "local/phi4"


def test_chat_transport_receipt_keeps_local_identity(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    import voicebridge.local_llm as LL

    cfg = _cfg(tmp_path, monkeypatch)

    def _fake_chat_local(text, conversation_prompt="", **kw):
        return "answer"

    monkeypatch.setattr(LL, "chat_local", _fake_chat_local)
    res = CT.turn(cfg, "hi", session="ident")
    receipt = res["receipt"]
    assert res["ok"] is True
    assert receipt["model"] == "qubit-brain4"
    assert receipt["identity"] == "local/qubit-brain4"
    assert receipt["profile"] == "qubit-brain4"
    assert receipt["role"] == "chat"
