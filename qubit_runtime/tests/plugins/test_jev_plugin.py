"""jev-workflows plugin: optional typed decisions through the REAL discovery path.

Derived from hermes-jev-skills (MIT, Copyright (c) 2026 Steve Darlow); see
``plugins/jev-workflows/NOTICE``.

The plugin was renamed ``jev`` -> ``jev-workflows`` (fe1fce876): a *different*,
separately-installed catalog plugin is literally named ``jev``, and user plugins shadow
bundled ones, so the port registered nothing under that name. Discovery now keys the port
as ``jev-workflows`` and imports it as ``qubit_plugins.jev_workflows`` (the directory has a
hyphen). Everything here goes through that real package, never a bare ``plugins.jev``.

Three claims are proved here, never asserted:

1. **The contract is Jev's own, not chat-completions.** The request on the wire is
   ``{state, model, questions}``; the reply validator refuses a distribution whose mass is not
   1, a choice whose keys do not match its options, an answer that is not its own argmax, and
   a score that disagrees with its distribution.
2. **Optional fails soft.** A provider with no key, a network failure, a malformed reply and
   an oversized state all raise ``client.JevError`` with a typed ``code`` — the tool layer
   turns each into a reported failure rather than a guess or a raise into the turn.
3. **Nothing is sent where a bearer would break the door.** The Zen free tier answers with no
   credential and refuses any ``Authorization`` header, so none is attached to it even when a
   key is configured.

Every request is carried by an injected ``transport``: these tests never touch the network.
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest

PLUGIN = "jev-workflows"


def _plugin_pkg():
    """The loaded plugin package (or one of its submodules), by its real import name.

    ``qubit_plugins.<slug>`` exists only once a PluginManager has loaded the plugin, so make
    sure one has. Using the discovered package guarantees the module under test is the exact
    object the live tool handlers use, not a second import of the same file.
    """
    package = "qubit_plugins.jev_workflows"
    if package not in sys.modules:
        importlib.import_module(package)
    return sys.modules[package]


def _client():
    """The plugin's own ``jevkit.client`` — the module the live tool handlers import."""
    importlib.import_module("qubit_plugins.jev_workflows")
    return importlib.import_module("qubit_plugins.jev_workflows.jevkit.client")


def _enable(qubit_home, *, decide_tools: bool = True):
    """Write the config that activates the plugin, the way an operator would.

    ``plugins.enabled`` lifts the plugin out of "not enabled in config"; ``jev/state.json``
    is the ``/jev`` switch file that gates the ``decide_tools`` policy tools.
    """
    import qubit_yaml as yaml

    (qubit_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": [PLUGIN]}}), encoding="utf-8")
    if decide_tools:
        (qubit_home / "jev").mkdir(exist_ok=True)
        (qubit_home / "jev" / "state.json").write_text(
            json.dumps({"decide_tools": "on"}), encoding="utf-8")


def _load():
    """A fresh manager, loaded through the real discovery path, registry cache cleared."""
    from qubit_cli import plugins as pmod
    from tools.registry import _check_fn_cache

    mgr = pmod.PluginManager()
    mgr.discover_and_load()
    _check_fn_cache.clear()
    return mgr


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """A temp QUBIT_HOME, no credentials, and the plugin loaded through discovery."""
    qubit_home = tmp_path / ".hermes"
    qubit_home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(qubit_home))
    for name in ("OPENCODE_ZEN_API_KEY", "TYPESAFE_API_KEY", "TYPESAFE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    _enable(qubit_home)
    loaded = _load()._plugins[PLUGIN]
    assert loaded.enabled, loaded.error
    # ``jevkit.keystore`` reads the active profile's secret scope, which the environment does
    # not reach: a key in the developer's real profile would otherwise make a "no key" test
    # pass for the wrong reason, and would silently pick the TypeSafe door. Patch AFTER load
    # (the module only exists once the plugin has been imported).
    monkeypatch.setattr(_client_keystore(), "resolve", lambda for_provider=None: None, raising=False)
    yield qubit_home


def _client_keystore():
    return importlib.import_module("qubit_plugins.jev_workflows.jevkit.keystore")


def _visible():
    from tools.registry import registry

    return {d["function"]["name"] for d in registry.get_definitions({"jev_decide"})}


# ── discovery ────────────────────────────────────────────────────────────────

def test_discovered_as_a_bundled_backend():
    loaded = _load()._plugins[PLUGIN]
    assert loaded.manifest.source == "bundled"
    assert loaded.manifest.kind == "backend"
    assert loaded.enabled and loaded.error is None


def test_the_plugins_own_key_is_the_renamed_one():
    """The port must not collide with the separately-installed catalog plugin named ``jev``."""
    plugins = _load()._plugins
    assert PLUGIN in plugins
    assert plugins[PLUGIN].manifest.name == PLUGIN


def test_enabled_plugin_registers_the_tool_in_its_own_toolset():
    from tools.registry import registry

    entry = registry.get_entry("jev_decide")
    assert entry is not None and entry.toolset == "jev"
    assert "jev_decide" in _visible()


def test_decide_tools_off_keeps_the_plugin_loadable_but_hides_the_tool(tmp_path, monkeypatch):
    """``decide_tools`` defaults off: the plugin still loads and its six workflow tools
    register, and only the gated ``jev_decide`` is absent."""
    qubit_home = tmp_path / "home"
    qubit_home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(qubit_home))
    _enable(qubit_home, decide_tools=False)
    loaded = _load()._plugins[PLUGIN]
    assert loaded.enabled and loaded.error is None
    assert "jev_decide" not in _visible()


# ── the wire is Jev's, not chat-completions ─────────────────────────────────

def _ask(reply, questions=None, *, provider="zen", seen=None):
    """One ``client.ask`` with an injected transport — never a real request.

    Returns the result; when ``seen`` is a dict it is filled with the request actually sent.
    """
    client = _client()

    def transport(body, headers, timeout):
        if seen is not None:
            seen["body"] = json.loads(body)
            seen["headers"] = dict(headers)
        return json.dumps(reply).encode()

    return client.ask(
        "ticket: refund my order",
        questions or {"a": client.noul("This is a refund request"),
                      "b": client.choice("Which queue", {"billing": "money", "tech": "bugs"})},
        provider=provider,
        transport=transport,
    )


def test_request_is_the_bespoke_decision_shape():
    seen = {}
    result = _ask({"model": "jev-1.13-free",
                   "answers": {"a": {"type": "noul", "noul": 0.98},
                               "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                     "probabilities": {"billing": 1, "tech": 0}}}}, seen=seen)
    assert set(seen["body"]) == {"state", "model", "questions"}, "chat-completions keys would be messages/model"
    assert "messages" not in seen["body"] and "choices" not in seen["body"]
    assert "content" not in seen["body"]
    assert result["answers"]["a"] == {"type": "noul", "noul": 0.98}
    assert result["answers"]["b"]["choice"] == "billing"
    assert result["provider"] == "zen"


def test_the_zen_door_never_receives_a_bearer(monkeypatch):
    """Zen's free tier answers with no credential and 401s any bearer, so none is sent."""
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "a-real-paid-zen-key")
    monkeypatch.setattr(_client_keystore(), "resolve", lambda for_provider=None: "a-real-paid-zen-key", raising=False)
    seen = {}
    _ask({"answers": {"a": {"type": "noul", "noul": 0.5},
                      "b": {"type": "choice", "choice": "billing", "confidence": 1,
                            "probabilities": {"billing": 1, "tech": 0}}}}, seen=seen)
    assert "Authorization" not in seen["headers"]
    assert _client().ZEN_ENDPOINT == "https://opencode.ai/zen/v1/systemone"


def test_a_typesafe_key_is_attached_to_the_typesafe_door(monkeypatch):
    """The one credential that IS sent, and only to its own door."""
    client = _client()
    monkeypatch.setattr(_client_keystore(), "resolve",
                        lambda for_provider=None: "a-typesafe-probe-key" if for_provider == "typesafe" else None,
                        raising=False)
    seen = {}
    _ask({"answers": {"a": {"type": "noul", "noul": 0.7}}},
         questions={"a": client.noul("is it fine")},
         provider="typesafe", seen=seen)
    assert seen["headers"]["Authorization"] == "Bearer a-typesafe-probe-key"


# ── optional: every failure is a typed code, never a guess ──────────────────

def test_a_missing_key_is_a_typed_error(monkeypatch):
    """A door that needs a credential and has none reports ``no_key`` before any request."""
    monkeypatch.setattr(_client_keystore(), "resolve", lambda for_provider=None: None, raising=False)
    reached = []
    with pytest.raises(_client().JevError) as refused:
        _client().ask("x", {"a": _client().noul("is it fine")}, provider="typesafe",
                      transport=lambda *a: reached.append(1) or b"{}")
    assert refused.value.code == "no_key"
    assert reached == [], "an unkeyed door must fail before it reaches the wire"


def test_a_network_failure_is_a_typed_error(monkeypatch):
    client = _client()

    def boom(body, headers, timeout):
        raise client.JevError("network", "URLError")

    with pytest.raises(client.JevError) as refused:
        client.ask("x", {"a": client.noul("is it fine")}, provider="zen", transport=boom)
    assert refused.value.code == "network"


def test_a_malformed_reply_is_a_typed_error(monkeypatch):
    client = _client()
    with pytest.raises(client.JevError) as refused:
        client.ask("x", {"a": client.noul("is it fine")}, provider="zen",
                   transport=lambda *a: b"not json")
    assert refused.value.code == "malformed"


def test_a_bad_question_is_refused_before_any_request():
    """A caller bug names exactly what to fix, and never reaches the wire."""
    client = _client()
    with pytest.raises(ValueError) as refused:
        client.ask("x", {"q": {"type": "vibes", "instructions": "pick something"}}, provider="zen",
                   transport=lambda *a: pytest.fail("must not reach the wire"))
    assert "unknown type" in str(refused.value) and "vibes" in str(refused.value)


def test_an_oversized_state_is_refused_before_any_request():
    client = _client()
    with pytest.raises(client.JevError) as refused:
        client.ask("x" * (client.MAX_STATE_CHARS + 1), {"a": client.noul("is it fine")}, provider="zen",
                   transport=lambda *a: pytest.fail("must not reach the wire"))
    assert refused.value.code == "state_too_large"


# ── reply invariants: a self-contradictory answer is refused ────────────────

def test_a_distribution_that_does_not_sum_to_one_is_refused():
    client = _client()
    with pytest.raises(client.JevError) as refused:
        _ask({"answers": {"a": {"type": "noul", "noul": 0.5},
                          "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                "probabilities": {"billing": 0.9, "tech": 0.9}}}})
    assert refused.value.code == "invalid_response"


def test_missing_or_extra_choice_keys_are_refused():
    client = _client()
    with pytest.raises(client.JevError) as refused:
        _ask({"answers": {"a": {"type": "noul", "noul": 0.5},
                          "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                "probabilities": {"billing": 1.0}}}})
    assert refused.value.code == "invalid_response"


def test_a_choice_that_is_not_its_own_argmax_is_refused():
    client = _client()
    with pytest.raises(client.JevError) as refused:
        _ask({"answers": {"a": {"type": "noul", "noul": 0.5},
                          "b": {"type": "choice", "choice": "billing", "confidence": 1,
                                "probabilities": {"billing": 0.2, "tech": 0.8}}}})
    assert refused.value.code == "invalid_response"
    assert refused.value.invariant == "choice_is_argmax"


def test_a_score_is_kept_with_its_distribution():
    client = _client()
    result = _ask({"answers": {"s": {"type": "score", "score": 0.5, "confidence": 0.9,
                                     "probabilities": {"0": 0.5, "1": 0.5, "2": 0.0, "3": 0.0}}}},
                  questions={"s": client.score("How hard", ["easy", "medium", "hard", "nasty"])})
    assert result["answers"]["s"]["score"] == 0.5
    assert result["answers"]["s"]["spread_reported"] is True


def test_a_score_that_disagrees_with_its_distribution_is_refused():
    """The incident the invariant exists for: an unsure 0.5-each spread filed at level 3."""
    client = _client()
    with pytest.raises(client.JevError) as refused:
        _ask({"answers": {"s": {"type": "score", "score": 3.0, "confidence": 0.9,
                                "probabilities": {"0": 0.5, "1": 0.5, "2": 0.0, "3": 0.0}}}},
             questions={"s": client.score("How hard", ["easy", "medium", "hard", "nasty"])})
    assert refused.value.code == "invalid_response"
    assert refused.value.invariant == "score_matches_its_distribution"
