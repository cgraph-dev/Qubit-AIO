"""The workbench model picker must list Qubit's REAL models, or refuse.

The defect this pins: ``/wb/api/models`` answered ``{"ok": true, "active":
"deepseek-v4-flash", "provider": "", "models": []}``. The ported client then
falls back to upstream's hardcoded names (``GPT-5.4 Mini``), sends one to
``/api/chat/start``, and the turn dies inside the transport with CIRCUIT_OPEN
while the pane shows a blank "Processed" row — a silent lie.

Two lessons the tests encode:

* The inventory is NOT in Qubit's own compat ``h_config``. ``config.DEFAULTS``
  has no ``providers`` key, and ``HC.dispatch(None, ...)`` degrades to the
  voice-only subset. An earlier "fix" probed with ``cfg=None`` and read zero
  models regardless of the code, so it proved nothing. These tests feed the
  ``/api/config``-shaped record the fork actually serves.
* An empty inventory is a typed refusal, never ``models: []`` with ``ok: true``.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import workbench_ui_routes as R


#: The live shape, trimmed: four providers, only one of which carries a
#: ``models`` dict. ``provider`` is null at the top level exactly as the fork
#: serves it, and ``model`` names a model that belongs to ``opencode-go``.
LIVE_SHAPED_CONFIG = {
    "model": "deepseek-v4.1-flash",
    "provider": None,
    "providers": {
        "ollama": {"default_model": "qubit-brain", "base_url": "http://localhost:11434/v1"},
        "omniroute": {"default_model": "auto"},
        "cheapvibecode": {
            "default_model": "claude-sonnet-4-6",
            "models": {"gpt-6-astra": {}, "gpt-5.6-sol": {}, "claude-sonnet-4-6": {}},
        },
        "opencode-go": {"default_model": "deepseek-v4.1-flash"},
    },
}


class _FakeHA:
    """Stands in for ``voicebridge.qubit_asgi`` — the in-process fork bridge."""

    def __init__(self, config, info=None):
        self._config = config
        self._info = info
        self.calls = []

    def call(self, method, path, *, query=None, headers=None, body=b"", timeout=None):
        self.calls.append((method, path))
        if path == "/api/config":
            if self._config is None:
                return 500, {"content-type": "application/json"}, b'{"detail":"boom"}'
            return (200, {"content-type": "application/json"},
                    json.dumps(self._config).encode())
        if path == "/api/model/info":
            if self._info is None:
                return 404, {"content-type": "application/json"}, b"{}"
            return (200, {"content-type": "application/json"},
                    json.dumps(self._info).encode())
        return 404, {}, b"{}"


@pytest.fixture
def fake_ha(monkeypatch):
    # Import first so ``voicebridge.qubit_asgi`` exists as an attribute of the
    # package; monkeypatch cannot create it via a dotted string.
    import voicebridge.qubit_asgi  # noqa: F401

    def install(config, info=None):
        fake = _FakeHA(config, info)
        monkeypatch.setattr("voicebridge.qubit_asgi", fake, raising=True)
        return fake
    return install


# --------------------------------------------------------------- derivation
def test_flattening_covers_every_default_model_and_every_model_id():
    pairs = R._wb_model_rows(LIVE_SHAPED_CONFIG)
    ids = [mid for _prov, mid in pairs]
    # every provider's default_model
    assert "qubit-brain" in ids
    assert "auto" in ids
    assert "claude-sonnet-4-6" in ids
    assert "deepseek-v4.1-flash" in ids
    # every id in the one providers dict that has models
    assert "gpt-6-astra" in ids
    assert "gpt-5.6-sol" in ids
    # default_model also present in its own models dict -> one pair, not two
    assert ids.count("claude-sonnet-4-6") == 1
    # no invented ids: exactly the union of what the config named
    assert set(ids) == {
        "qubit-brain", "auto", "claude-sonnet-4-6",
        "gpt-6-astra", "gpt-5.6-sol", "deepseek-v4.1-flash",
    }


def test_each_pair_is_labelled_with_its_own_provider():
    pairs = dict((mid, prov) for prov, mid in R._wb_model_rows(LIVE_SHAPED_CONFIG))
    assert pairs["qubit-brain"] == "ollama"
    assert pairs["auto"] == "omniroute"
    assert pairs["gpt-6-astra"] == "cheapvibecode"
    assert pairs["deepseek-v4.1-flash"] == "opencode-go"


def test_flattening_dedupes_a_model_repeated_within_one_provider():
    config = {"providers": {
        "a": {"default_model": "shared-model"},
        "b": {"default_model": "shared-model", "models": {"shared-model": {}}},
    }}
    pairs = R._wb_model_rows(config)
    # Pair-level dedupe: one entry per (provider, model). Cross-provider
    # duplicates survive here and are collapsed at the row layer, where the id
    # is the picker's value.
    assert pairs == [("a", "shared-model"), ("b", "shared-model")]


def test_flattening_accepts_a_list_shaped_models_field():
    config = {"providers": {"p": {"default_model": "d", "models": ["x", "y"]}}}
    assert R._wb_model_rows(config) == [("p", "d"), ("p", "x"), ("p", "y")]


@pytest.mark.parametrize("config", [
    None, {}, {"providers": None}, {"providers": {}},
    {"providers": {"p": {}}}, {"providers": {"p": "not-a-dict"}},
    {"providers": {"p": {"default_model": "  "}}},
])
def test_flattening_yields_nothing_for_an_inventory_free_record(config):
    assert R._wb_model_rows(config) == []


# ------------------------------------------------------------------ handler
def test_handler_returns_the_real_inventory_not_an_empty_list(fake_ha):
    fake_ha(LIVE_SHAPED_CONFIG, info={"model": "deepseek-v4.1-flash",
                                      "provider": "opencode-go"})
    status, payload = R.h_wb_models(None, {}, {}, {})
    assert status == 200
    assert payload["ok"] is True
    assert payload["models"], "an empty picker is the defect being fixed"
    assert payload["active"] == "deepseek-v4.1-flash"
    assert payload["provider"] == "opencode-go"
    assert payload["source"] == "qubit_asgi:/api/config"
    rows = {r["id"]: r for r in payload["models"]}
    assert rows["gpt-6-astra"]["provider"] == "cheapvibecode"
    assert rows["gpt-6-astra"]["label"] == "gpt-6-astra (cheapvibecode)"
    assert [r["id"] for r in payload["models"]].count("claude-sonnet-4-6") == 1


def test_handler_marks_exactly_one_row_active(fake_ha):
    fake_ha(LIVE_SHAPED_CONFIG, info={"model": "deepseek-v4.1-flash",
                                      "provider": "opencode-go"})
    _status, payload = R.h_wb_models(None, {}, {}, {})
    active_rows = [r for r in payload["models"] if r.get("active")]
    assert [r["id"] for r in active_rows] == ["deepseek-v4.1-flash"]
    assert "active_not_in_inventory" not in payload


def test_handler_resolves_the_active_provider_from_model_info(fake_ha):
    # /api/config serves provider: null; the fork's /api/model/info is the
    # resolver. Without it the picker cannot tell which row is current.
    fake_ha(LIVE_SHAPED_CONFIG, info={"model": "deepseek-v4.1-flash",
                                      "provider": "opencode-go"})
    _status, payload = R.h_wb_models(None, {}, {}, {})
    assert payload["provider"] == "opencode-go"


def test_handler_falls_back_to_the_configs_own_provider_when_present(fake_ha):
    config = dict(LIVE_SHAPED_CONFIG, provider="cheapvibecode")
    fake = fake_ha(config)  # no /api/model/info on this fake
    _status, payload = R.h_wb_models(None, {}, {}, {})
    assert payload["provider"] == "cheapvibecode"
    assert payload["active"] == "deepseek-v4.1-flash"
    assert ("GET", "/api/model/info") not in fake.calls


def test_handler_flags_an_active_model_absent_from_the_inventory(fake_ha):
    fake_ha(LIVE_SHAPED_CONFIG, info={"model": "some-custom-pin",
                                      "provider": "opencode-go"})
    _status, payload = R.h_wb_models(None, {}, {}, {})
    assert payload["active"] == "some-custom-pin"
    assert payload["active_not_in_inventory"] is True
    assert not any(r.get("active") for r in payload["models"])


def test_a_duplicated_id_is_attributed_to_the_provider_that_owns_the_active_model(fake_ha):
    """The LIVE collision: ``deepseek-v4.1-flash`` is listed under BOTH
    cheapvibecode's models dict and opencode-go's default_model. Dedupe on the
    id keeps one row, but it must carry the ACTIVE model's provider.
    """
    config = {
        "model": "deepseek-v4.1-flash",
        "provider": None,
        "providers": {
            "cheapvibecode": {"default_model": "claude-sonnet-4-6",
                              "models": {"gpt-6-astra": {},
                                         "deepseek-v4.1-flash": {}}},
            "opencode-go": {"default_model": "deepseek-v4.1-flash"},
        },
    }
    fake_ha(config, info={"model": "deepseek-v4.1-flash",
                          "provider": "opencode-go"})
    _status, payload = R.h_wb_models(None, {}, {}, {})
    rows = {r["id"]: r for r in payload["models"]}
    assert [r["id"] for r in payload["models"]].count("deepseek-v4.1-flash") == 1
    assert rows["deepseek-v4.1-flash"]["provider"] == "opencode-go"
    assert rows["deepseek-v4.1-flash"]["label"] == "deepseek-v4.1-flash (opencode-go)"
    assert rows["deepseek-v4.1-flash"]["active"] is True
    assert "active_not_in_inventory" not in payload


def test_empty_inventory_is_a_typed_refusal_not_an_empty_models_array(fake_ha):
    # The whole point: no providers -> refuse with a reason, never
    # {"ok": true, "models": []}.
    fake_ha({"model": "whatever", "providers": {}})
    status, payload = R.h_wb_models(None, {}, {}, {})
    assert status == 503
    assert payload["ok"] is False
    assert payload["state"] == "UNAVAILABLE"
    assert payload["code"] == "WB_NO_MODEL_INVENTORY"
    assert "models" not in payload
    assert payload["detail"].strip()
    assert payload["qubit_counterpart"].strip()


def test_a_failing_config_read_is_a_typed_refusal(fake_ha):
    fake_ha(None)  # /api/config answers 500
    status, payload = R.h_wb_models(None, {}, {}, {})
    assert status == 503
    assert payload["ok"] is False
    assert payload["code"] == "WB_NO_MODEL_INVENTORY"
    assert "models" not in payload


def test_a_raising_bridge_is_a_backend_refusal(monkeypatch):
    import voicebridge.qubit_asgi  # noqa: F401

    class Boom:
        def call(self, *a, **k):
            raise RuntimeError("bridge down")
    monkeypatch.setattr("voicebridge.qubit_asgi", Boom(), raising=True)
    status, payload = R.h_wb_models(None, {}, {}, {})
    assert status == 503
    assert payload["ok"] is False
    assert payload["code"] == "WB_BACKEND_UNAVAILABLE"
    assert "bridge down" in payload["detail"]
    assert "models" not in payload


def test_the_handler_reads_the_fork_config_not_the_voice_only_compat_subset(fake_ha):
    """Regression on the original misdiagnosis.

    ``qubit_compat.dispatch(cfg=None, "GET", "/api/config")`` returns the
    voice-only subset with no ``providers`` key at all — a probe over it can
    only ever read zero models. The handler must consult the fork record.
    """
    from voicebridge import qubit_compat as HC
    _code, voice_cfg = HC.dispatch(None, "GET", "/api/config", query={}, body={})
    assert "providers" not in (voice_cfg or {}), (
        "premise changed: the compat /api/config now carries providers; the "
        "handler's source-of-truth comment needs re-checking")

    fake = fake_ha(LIVE_SHAPED_CONFIG, info={"model": "deepseek-v4.1-flash",
                                            "provider": "opencode-go"})
    _status, payload = R.h_wb_models(None, {}, {}, {})
    assert ("GET", "/api/config") in fake.calls
    assert len(payload["models"]) == 6
