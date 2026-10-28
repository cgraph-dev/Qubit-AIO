"""Turn-time dead-model gates (G1 residual).

A model the registry lists but upstream rejects at turn time (muse-spark
resolves 1048576 statically, then ModelProtocolUnsupported on use) used to
slip past the select-time gate and surface as generic MALFORMED_RESPONSE.
The turn path now classifies the three dead-model classes —
protocol-unsupported / unavailable / not-found — as typed
MODEL_UNAVAILABLE_TURN (naming the dead model + the still-working slot),
with the slot never auto-moved (failover policy is an owner call: typed
error only, never silent fallback, never auto-switch). Local-tier turns
are untouched.

Hermetic: stub config + injected transports only. No live slots, no live
provider calls (muse is already proven dead live), no ~/.hermes, no :9119.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.providers import gateway as G  # noqa: E402
from voicebridge.providers import frontier as F  # noqa: E402
from voicebridge.providers.catalog import (  # noqa: E402
    ModelEntry, build_catalog)
from voicebridge.providers.contracts import (  # noqa: E402
    FAIL_CODES, ProviderRequest)
from voicebridge.providers.policy import reset_governor  # noqa: E402

import time as _time


class Cfg:
    def __init__(self, frontier: dict):
        self._f = frontier

    def get(self, *path, default=None):
        if len(path) >= 2 and path[0] == "intent" and path[1] == "frontier":
            node: object = self._f
            for p in path[2:]:
                if not isinstance(node, dict) or p not in node:
                    return default
                node = node[p]
            return node
        if path == ("intent", "runner", "alias"):
            return "qubit-brain"
        return default


def _base_frontier(**kw):
    f = {
        "enabled": True, "route": "frontier", "privacy_tier": "standard",
        "allowlist": ["zen/m1"],
        "providers": {"zen": {"base_url": "https://zen.example/v1",
                              "key_ref": "env:QUBIT_TEST_ZEN_KEY"}},
        "max_tokens": 64, "max_context_chars": 4000,
        "max_response_chars": 4000, "timeout_s": 5.0, "max_retries": 1,
        "rpm": 10, "max_concurrency": 2, "daily_usd": 1.0, "monthly_usd": 10.0,
    }
    f.update(kw)
    return f


def _cat():
    entry = ModelEntry(provider="zen", model="m1", context_tokens=8192,
                       max_output_tokens=512, supports_tools=True,
                       supports_structured=True, supports_streaming=True,
                       price_in_per_1k=0.001, price_out_per_1k=0.002)
    return build_catalog([entry], source="test", fetched_at=_time.time())


def _ok_body(text="hello"):
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7}}


@pytest.fixture(autouse=True)
def _fresh_gov(monkeypatch):
    reset_governor()
    monkeypatch.setenv("QUBIT_TEST_ZEN_KEY", "test-key-123")
    yield
    reset_governor()


def _req(**kw):
    base = dict(prompt="hi", max_tokens=32, timeout_s=5, route="frontier",
                provider="zen", model="m1")
    base.update(kw)
    return ProviderRequest(**base)


def _client(transport):
    return F.FrontierClient("https://zen.example/v1", "k", "zen", "m1",
                            max_retries=0, transport=transport)


# --- turn-time classification (frontier client) ------------------------------

def test_model_protocol_unsupported_is_typed_not_malformed():
    body = {"error": {"message": "ModelProtocolUnsupported: model "
                                 "zen/m1 does not support chat completions",
                      "code": "ModelProtocolUnsupported"}}
    res = _client(lambda *a: (400, body)).complete(_req(), "hi")
    assert res.ok is False
    assert res.code == "MODEL_UNAVAILABLE_TURN", res
    assert "zen/m1" in res.message
    assert res.identity == "zen/m1"


def test_model_unavailable_variant_is_typed():
    body = {"error": {"message": "ModelUnavailable: upstream reports the "
                                 "model unavailable", "type": "model_unavailable"}}
    res = _client(lambda *a: (400, body)).complete(_req(), "hi")
    assert res.ok is False
    assert res.code == "MODEL_UNAVAILABLE_TURN", res


def test_model_not_found_variant_is_typed():
    body = {"error": {"message": "model_not_found: no such model zen/m1",
                      "code": "model_not_found"}}
    res = _client(lambda *a: (404, body)).complete(_req(), "hi")
    assert res.ok is False
    assert res.code == "MODEL_UNAVAILABLE_TURN", res


def test_generic_400_without_dead_markers_stays_malformed():
    body = {"error": {"message": "bad request: max_tokens must be positive",
                      "code": "invalid_request"}}
    res = _client(lambda *a: (400, body)).complete(_req(), "hi")
    assert res.ok is False
    assert res.code == "MALFORMED_RESPONSE", res


def test_plain_404_without_dead_markers_stays_not_configured():
    res = _client(lambda *a: (404, {"error": "no such endpoint"})).complete(
        _req(), "hi")
    assert res.ok is False
    assert res.code == "NOT_CONFIGURED", res


def test_success_path_unchanged():
    res = _client(lambda *a: (200, _ok_body("REAL"))).complete(_req(), "hi")
    assert res.ok is True and res.text == "REAL"
    assert res.code == ""


# --- gateway: typed error only, slot never moved -------------------------------

def test_gateway_explicit_frontier_names_dead_model_and_working_slot():
    cfg = Cfg(_base_frontier())
    before = copy.deepcopy(cfg._f)
    body = {"error": {"message": "ModelProtocolUnsupported for zen/m1",
                      "code": "ModelProtocolUnsupported"}}
    res = G.run(_req(), cfg, catalog=_cat(),
                transport=lambda *a: (400, body))
    assert res.ok is False
    assert res.code == "MODEL_UNAVAILABLE_TURN", res
    assert "zen/m1" in res.message  # the dead model, named
    assert "still serving" in res.message  # the working slot, named
    assert res.identity == "zen/m1"  # no silent substitution
    assert res.usage is None or res.usage.fallback is False
    assert cfg._f == before  # slot/config byte-unchanged


def test_gateway_does_not_fallback_silently_on_dead_model():
    cfg = Cfg(_base_frontier())
    body = {"error": {"code": "model_not_found",
                      "message": "model_not_found: zen/m1"}}
    res = G.run(_req(), cfg, catalog=_cat(),
                transport=lambda *a: (400, body))
    assert res.ok is False
    assert res.text == ""  # never a fabricated success
    assert res.code == "MODEL_UNAVAILABLE_TURN", res


def test_local_turn_untouched_by_dead_model_gates(monkeypatch):
    import voicebridge.providers.gateway as Gw
    monkeypatch.setattr(Gw, "_local_complete",
                        lambda *a, **k: Gw.ProviderResult(
                            ok=True, text="local answer", provider="local",
                            model="qubit-brain",
                            identity="local/qubit-brain",
                            usage=Gw.UsageRecord(
                                provider="local", model="qubit-brain",
                                identity="local/qubit-brain", latency_ms=1)))
    cfg = Cfg(_base_frontier())
    called = []
    res = G.run(ProviderRequest(prompt="hi", route="local"), cfg,
                catalog=_cat(),
                transport=lambda *a: (called.append(1), (400, {
                    "error": "ModelProtocolUnsupported"}))[1])
    assert called == []  # local performs no frontier request
    assert res.ok and res.identity == "local/qubit-brain"
    assert res.code != "MODEL_UNAVAILABLE_TURN"


def test_new_code_is_wire_stable():
    assert "MODEL_UNAVAILABLE_TURN" in FAIL_CODES
    assert "MALFORMED_RESPONSE" in FAIL_CODES  # generic path preserved
