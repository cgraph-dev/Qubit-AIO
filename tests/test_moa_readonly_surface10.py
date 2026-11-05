"""MoA read-only surface (fix10): serve the REAL engine state, never a fiction.

Qubit executes MoA through the vendored engine and advertises the ``moa``
virtual provider exactly when the profile's RAW config enables a preset. The
dashboard ``GET /api/model/moa`` mirrors that same gate: real presets when
opted in, an explicit opt-in refusal otherwise. These tests are hermetic (tmp
``QUBIT_HOME``/XDG only; no network, no live services).
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    qh = tmp_path / "qh"
    qh.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(qh))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    return qh


def _write_config(home, text: str) -> None:
    (home / "config.yaml").write_text(text, encoding="utf-8")


def _get():
    from voicebridge import qubit_compat as QC
    return QC.h_model_moa(None, {}, {}, {})


def test_configured_profile_serves_exactly_its_real_presets(home):
    _write_config(home, (
        "moa:\n"
        "  default_preset: studio\n"
        "  presets:\n"
        "    studio:\n"
        "      reference_models:\n"
        "        - {provider: openrouter, model: deepseek/deepseek-v4-pro}\n"
        "      aggregator: {provider: openrouter, model: anthropic/claude-opus-4.8}\n"
        "      enabled: true\n"
        "    quiet:\n"
        "      reference_models:\n"
        "        - {provider: openai-codex, model: gpt-5.5}\n"
        "      aggregator: {provider: openai-codex, model: gpt-5.5}\n"
        "      enabled: true\n"
    ))
    from qubit_cli.inventory import _raw_config_has_enabled_moa_preset

    code, body = _get()
    assert code == 200 and body["ok"] is True, body
    # The handler's own gate agrees with the model picker.
    assert _raw_config_has_enabled_moa_preset() is True
    # Exactly the two presets the user wrote — nothing merged in, nothing lost.
    assert set(body["presets"]) == {"studio", "quiet"}
    assert body["default_preset"] == "studio"
    assert body["enabled"] is True
    assert body["read_only"] is True
    assert body["presets"]["quiet"]["aggregator"]["model"] == "gpt-5.5"
    assert "qubit moa configure" in body["detail"]


def test_disabled_preset_is_not_served(home):
    # A raw `moa` section with only a DISABLED preset is not an opt-in: the
    # picker hides MoA, so the card must not show a preset it cannot select.
    _write_config(home, (
        "moa:\n"
        "  presets:\n"
        "    off:\n"
        "      reference_models:\n"
        "        - {provider: openrouter, model: x}\n"
        "      aggregator: {provider: openrouter, model: y}\n"
        "      enabled: false\n"
    ))
    code, body = _get()
    assert code == 200 and body["presets"] == {}, body
    assert body["enabled"] is False
    assert "opt-in" in body["detail"]


def test_served_state_equals_the_engine_config(home):
    # Whatever the engine will run is what the surface serves: the SAME
    # normalize_moa_config(load_config()) the MoA runtime resolves presets from.
    _write_config(home, (
        "moa:\n"
        "  default_preset: studio\n"
        "  presets:\n"
        "    studio:\n"
        "      reference_models:\n"
        "        - {provider: openrouter, model: deepseek/deepseek-v4-pro}\n"
        "      aggregator: {provider: openrouter, model: anthropic/claude-opus-4.8}\n"
        "      enabled: true\n"
    ))
    code, body = _get()
    assert code == 200 and body["ok"] is True, body
    from qubit_cli.config import load_config
    from qubit_cli.moa_config import normalize_moa_config

    expected = normalize_moa_config(load_config().get("moa") or {})
    assert body["presets"] == expected["presets"]
    assert body["default_preset"] == expected["default_preset"]
    assert body["reference_models"] == expected["reference_models"]
    assert body["aggregator"] == expected["aggregator"]
    assert body["read_only"] is True


def test_unconfigured_profile_fabricates_nothing(home):
    _write_config(home, "model:\n  provider: ollama\n")
    code, body = _get()
    assert code == 200 and body["ok"] is True, body
    assert body["presets"] == {}
    assert body["reference_models"] == []
    assert body["aggregator"] == {"provider": "", "model": "",
                                  "enabled": False}
    assert body["enabled"] is False and body["read_only"] is True
    # The merged DEFAULT_CONFIG preset names real providers, but they are NOT
    # this profile's opt-in choice, so they must not leak into the refusal.
    blob = str(body)
    assert "openai-codex" not in blob and "openrouter" not in blob


def test_read_is_side_effect_free(home):
    text = (
        "moa:\n"
        "  default_preset: studio\n"
        "  presets:\n"
        "    studio:\n"
        "      reference_models:\n"
        "        - {provider: openrouter, model: deepseek/deepseek-v4-pro}\n"
        "      aggregator: {provider: openrouter, model: anthropic/claude-opus-4.8}\n"
        "      enabled: true\n"
    )
    _write_config(home, text)
    before = (home / "config.yaml").read_bytes()
    _get()
    assert (home / "config.yaml").read_bytes() == before


def test_put_is_a_typed_read_only_refusal(home):
    from voicebridge import qubit_compat as QC

    code, refused = QC.moa_put(None, {}, {"presets": {}}, {})
    assert code == 501 and refused["ok"] is False, refused
    assert refused["code"] == "MOA_READ_ONLY"
    assert "qubit moa" in refused["qubit_counterpart"]
    assert "read-only" in refused["detail"]


def test_literal_reference_route_serves_real_state(home, tmp_path):
    """The reference-literal ``/api/model/moa`` reaches the same real handler."""
    import json
    import urllib.error
    import urllib.request

    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    _write_config(home, (
        "moa:\n"
        "  default_preset: studio\n"
        "  presets:\n"
        "    studio:\n"
        "      reference_models:\n"
        "        - {provider: openrouter, model: deepseek/deepseek-v4-pro}\n"
        "      aggregator: {provider: openrouter, model: anthropic/claude-opus-4.8}\n"
        "      enabled: true\n"
    ))

    class StubOrch:
        pass

    orch = StubOrch()
    orch.cfg = Config.load()
    orch.cfg.data["webui"].update(host="127.0.0.1", token="tok123")

    w = WebUI(orchestrator=orch, port=0, state_dir=tmp_path / "state")
    w.start()
    try:
        base = f"http://127.0.0.1:{w._server.server_address[1]}"

        def get(token: str = ""):
            req = urllib.request.Request(f"{base}/api/model/moa")
            if token:
                req.add_header("X-Api-Token", token)
            return urllib.request.urlopen(req, timeout=5)

        with pytest.raises(urllib.error.HTTPError) as ei:
            get()
        assert ei.value.code == 401
        ei.value.close()

        r = get("tok123")
        try:
            body = json.loads(r.read())
        finally:
            r.close()
        assert body["ok"] is True, body
        assert set(body["presets"]) == {"studio"}
        assert body["read_only"] is True
        assert body["default_preset"] == "studio"
    finally:
        w.stop()
