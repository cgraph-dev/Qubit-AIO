import pytest
"""Behavior contracts for computer_use latency knobs."""


from tools.computer_use import tool as cu_tool


def test_aux_vision_route_caches_per_provider_model(monkeypatch):
    cu_tool._AUX_VISION_ROUTE_CACHE.clear()
    calls = {"n": 0}

    monkeypatch.setattr(
        "agent.auxiliary_client._read_main_provider", lambda: "openai"
    )
    monkeypatch.setattr(
        "agent.auxiliary_client._read_main_model", lambda: "gpt-test"
    )

    def fake_load():
        calls["n"] += 1
        return {"auxiliary": {"vision": {}}}

    monkeypatch.setattr("qubit_cli.config.load_config", fake_load)
    monkeypatch.setattr(
        "tools.computer_use.vision_routing.should_route_capture_to_aux_vision",
        lambda *a, **k: True,
    )

    assert cu_tool._should_route_through_aux_vision() is True
    assert cu_tool._should_route_through_aux_vision() is True
    assert calls["n"] == 1


# --- ported from upstream pin (wave 25 short-port) ---
@pytest.mark.xfail(strict=True, reason="wave-25 short-port: exposed local-port defect")
def test_aux_vision_route_follows_a_config_change_without_restart(monkeypatch):
    """The capture route reads the signature-cached config on every capture, so an ``image_input_mode`` edit
    (or /model, or a profile switch) applies to the next screenshot instead of a per-process verdict."""
    cfg = {"agent": {"image_input_mode": "native"}}
    monkeypatch.setattr("agent.auxiliary_client._read_main_provider", lambda: "anthropic")
    monkeypatch.setattr("agent.auxiliary_client._read_main_model", lambda: "claude-opus-4-5")
    monkeypatch.setattr("qubit_cli.config.load_config_readonly", lambda: cfg)
    monkeypatch.setattr("agent.image_routing._lookup_supports_vision", lambda *a, **k: True)
    monkeypatch.setattr("tools.vision_tools._profile_rejects_tool_media", lambda *a, **k: False)

    assert cu_tool._should_route_through_aux_vision() is False
    cfg["agent"]["image_input_mode"] = "text"
    assert cu_tool._should_route_through_aux_vision() is True

