"""jev plugin: optional typed decisions through the REAL discovery path.

Derived from hermes-jev-skills (MIT, Copyright (c) 2026 Steve Darlow); see
``plugins/jev/NOTICE``.

Three claims are proved here, never asserted:

1. **The contract is Jev's own, not chat-completions.** The request on the wire is
   ``{state, model, questions}``; the reply validator refuses a distribution whose mass is not
   1, an answer that is not its own argmax, and a score that disagrees with its distribution.
2. **Optional fails soft.** A pinned provider with no key, a network error, a 401 and a
   malformed reply all surface as ``available: false`` with a typed reason — never a guess and
   never a raise into the turn.
3. **Nothing is sent where a bearer would break the door.** The Zen free tier refuses any
   ``Authorization`` header, so no key is attached to it even when one is configured.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """A temp QUBIT_HOME, no credentials, and the plugin loaded through discovery."""
    qubit_home = tmp_path / ".hermes"
    qubit_home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(qubit_home))
    for name in ("OPENCODE_ZEN_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from qubit_cli import plugins as pmod

    mgr = pmod.PluginManager()
    mgr.discover_and_load()
    assert mgr._plugins["jev"].enabled, mgr._plugins["jev"].error
    yield qubit_home


def _pin(settings):
    """Pin ``plugins.entries.jev.settings`` the way an operator would, then re-load."""
    import qubit_yaml as yaml
    from qubit_cli import plugins as pmod
    from qubit_constants import get_qubit_home
    from tools.registry import _check_fn_cache

    (get_qubit_home() / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"entries": {"jev": {"settings": settings}}}}), encoding="utf-8")
    mgr = pmod.PluginManager()
    mgr.discover_and_load()
    _check_fn_cache.clear()


def _visible():
    from tools.registry import registry

    return {d["function"]["name"] for d in registry.get_definitions({"jev_decide"})}


def _call(state="The build finished and all tests passed.", questions=None, **extra):
    from tools.registry import registry

    raw = registry.dispatch("jev_decide", {
        "state": state,
        "questions": questions or {"ok": {"type": "noul", "instructions": "The text reports success"}},
        **extra,
    })
    assert isinstance(raw, str), raw
    return json.loads(raw)


# ── discovery ────────────────────────────────────────────────────────────────

def test_discovered_as_a_bundled_backend():
    from qubit_cli import plugins as pmod

    mgr = pmod.PluginManager()
    mgr.discover_and_load()
    loaded = mgr._plugins["jev"]
    assert loaded.manifest.source == "bundled"
    assert loaded.manifest.kind == "backend"
    assert loaded.enabled and loaded.error is None


def test_enabled_plugin_registers_the_tool_in_its_own_toolset():
    from tools.registry import registry

    entry = registry.get_entry("jev_decide")
    assert entry is not None and entry.toolset == "jev"
    assert "jev_decide" in _visible(), "the free Zen tier needs no credential"


# ── optional: every failure is a typed 'unavailable', never a guess ──────────

def test_a_pinned_typesafe_without_its_key_is_unavailable():
    _pin({"provider": "typesafe"})
    assert "jev_decide" not in _visible()
    out = _call(provider="typesafe")
    assert out["available"] is False
    assert out["code"] == "no_key"
    assert "TYPESAFE_API_KEY" in out["reason"]
    assert out["answers"] == {}


def test_an_unknown_pinned_provider_is_reported_not_guessed():
    _pin({"provider": "nonsense"})
    out = _call()
    assert out["available"] is False
    assert "unknown decision provider" in out["reason"]


def test_a_network_failure_is_unavailable(monkeypatch):
    from plugins.jev import client

    def boom(endpoint, body, headers, timeout):
        raise client.JevUnavailable("network", "URLError")

    monkeypatch.setattr(client, "_post", boom)
    out = _call(provider="zen")
    assert out["available"] is False and out["code"] == "network" and out["answers"] == {}


def test_a_malformed_reply_is_unavailable(monkeypatch):
    from plugins.jev import client

    monkeypatch.setattr(client, "_post", lambda *a, **k: b"not json")
    out = _call(provider="zen")
    assert out["available"] is False and out["code"] == "malformed"


def test_a_bad_question_is_a_tool_error_naming_the_fix():
    out = _call(questions={"q": {"type": "vibes", "instructions": "pick something"}})
    assert "error" in out and "invalid question" in out["error"]


def test_missing_arguments_are_a_tool_error():
    from tools.registry import registry

    assert "error" in json.loads(registry.dispatch("jev_decide", {"state": "x"}))


# ── the wire is Jev's, not chat-completions ─────────────────────────────────

def _ask(monkeypatch, reply, questions=None, headers_seen=None):
    from plugins.jev import client

    seen = {}

    def transport(endpoint, body, headers, timeout):
        seen["endpoint"] = endpoint
        seen["body"] = json.loads(body)
        seen["headers"] = headers
        return json.dumps(reply).encode()

    result = client.ask(
        "ticket: refund my order",
        questions or {"a": client.noul("This is a refund request"),
                      "b": client.choice("Which queue", {"billing": "money", "tech": "bugs"})},
        transport=transport,
    )
    if headers_seen is not None:
        headers_seen.update(seen)
    return result, seen


def test_request_is_the_bespoke_decision_shape(monkeypatch):
    result, seen = _ask(monkeypatch, {"model": "jev-1.13-free",
                                      "answers": {"a": {"type": "noul", "noul": 0.98},
                                                  "b": {"type": "choice", "choice": "billing",
                                                        "confidence": 1, "probabilities": {"billing": 1, "tech": 0}}}})
    assert seen["endpoint"] == "https://opencode.ai/zen/v1/systemone"
    assert set(seen["body"]) == {"state", "model", "questions"}, "chat-completions keys would be messages/model"
    assert "messages" not in seen["body"] and "choices" not in seen["body"]
    assert seen["body"]["model"] == "jev-1.13-free"
    assert "Authorization" not in seen["headers"], "the free Zen tier refuses a bearer"
    assert result["answers"]["a"] == {"type": "noul", "noul": 0.98}
    assert result["answers"]["b"]["choice"] == "billing"
    assert result["provider"] == "zen"


def test_a_configured_zen_key_is_never_attached(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "a-real-paid-zen-key")
    _result, seen = _ask(monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.5},
                                                   "b": {"type": "choice", "choice": "billing",
                                                         "confidence": 1,
                                                         "probabilities": {"billing": 1, "tech": 0}}}})
    assert "Authorization" not in seen["headers"]


def test_a_typesafe_key_is_attached_to_the_typesafe_door(monkeypatch):
    from plugins.jev import client

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-probe-key")
    seen = {}

    def transport(endpoint, body, headers, timeout):
        seen.update(endpoint=endpoint, headers=headers)
        return json.dumps({"answers": {"a": {"type": "noul", "noul": 0.7}}}).encode()

    client.ask("x", {"a": client.noul("is it fine")}, preferred_provider="typesafe", transport=transport)
    assert seen["endpoint"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer ts-probe-key"


def test_a_distribution_that_does_not_sum_to_one_is_refused(monkeypatch):
    from plugins.jev import client

    with pytest.raises(client.JevUnavailable) as refused:
        _ask(monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.5},
                                       "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                             "probabilities": {"billing": 0.9, "tech": 0.9}}}})
    assert refused.value.code == "invalid_response"


def test_missing_or_extra_choice_keys_are_refused(monkeypatch):
    from plugins.jev import client

    with pytest.raises(client.JevUnavailable) as refused:
        _ask(monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.5},
                                       "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                             "probabilities": {"billing": 1.0}}}})
    assert refused.value.code == "invalid_response"


def test_a_choice_that_is_not_its_own_argmax_is_refused(monkeypatch):
    from plugins.jev import client

    with pytest.raises(client.JevUnavailable) as refused:
        _ask(monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.5},
                                       "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                             "probabilities": {"billing": 0.2, "tech": 0.8}}}})
    assert refused.value.code == "invalid_response"


def test_an_oversized_state_is_refused_before_any_request(monkeypatch):
    from plugins.jev import client

    with pytest.raises(client.JevUnavailable) as refused:
        client.ask("x" * (client.MAX_STATE_CHARS + 1), {"a": client.noul("is it fine")},
                   transport=lambda *a, **k: pytest.fail("must not reach the wire"))
    assert refused.value.code == "state_too_large"


def test_a_score_is_kept_with_its_distribution(monkeypatch):
    from plugins.jev import client

    result, _ = _ask(
        monkeypatch,
        {"answers": {"s": {"type": "score", "score": 0.5, "confidence": 0.9,
                           "probabilities": {"0": 0.5, "1": 0.5, "2": 0.0, "3": 0.0}}}},
        questions={"s": client.score("How hard", ["easy", "medium", "hard", "nasty"])},
    )
    assert result["answers"]["s"]["score"] == 0.5
    assert result["answers"]["s"]["spread_reported"] is True


def test_a_score_that_disagrees_with_its_distribution_is_refused(monkeypatch):
    """The incident the invariant exists for: an unsure 0.5-each spread filed at level 3."""
    from plugins.jev import client

    with pytest.raises(client.JevUnavailable) as refused:
        _ask(monkeypatch,
             {"answers": {"s": {"type": "score", "score": 3.0, "confidence": 0.9,
                                "probabilities": {"0": 0.5, "1": 0.5, "2": 0.0, "3": 0.0}}}},
             questions={"s": client.score("How hard", ["easy", "medium", "hard", "nasty"])})
    assert refused.value.code == "invalid_response"
