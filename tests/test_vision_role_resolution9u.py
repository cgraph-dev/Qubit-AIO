"""The ``vision`` resolver role must resolve to a vision-capable model.

Regression: ``resolver.resolve(cfg, role="vision")`` fell through to the shipped
text-only chat default (``qubit-brain4``) and reported
``capabilities.vision == False``. The vision role now has its own resolution
ladder (configured aux pin → registered vision profile → installed local VLM →
shipped local VLM) and never reports a text-only model. Other roles are
unchanged and resolution never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from voicebridge.providers import resolver as R


class _Cfg:
    """Minimal config: dotted ``get(*path)`` plus a state dir for the registry."""

    def __init__(self, data: Optional[Dict[str, Any]] = None, state_dir=None):
        self._data = data or {}
        self._state_dir = state_dir

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def state_dir(self):
        return self._state_dir


@pytest.fixture(autouse=True)
def _no_live_probe(monkeypatch):
    """Never touch a live Ollama in a hermetic test."""
    monkeypatch.setattr(R, "_vision_installed_local", lambda cfg: ())


def _vision_profiles(cfg):
    return [p for p in R.load_profiles(cfg) if p.supports_vision]


def test_vision_role_resolves_to_a_vision_capable_model():
    eff = R.resolve(None, role="vision")
    assert eff.capabilities["vision"] is True
    assert eff.kind == R.LOCAL
    assert eff.model  # a real model id, not the text-only chat default
    assert eff.model != R.DEFAULT_LOCAL_ID
    assert eff.provider == "ollama"


def test_vision_role_does_not_change_the_profile_inventory():
    assert [p.id for p in R.default_profiles()] == [R.DEFAULT_LOCAL_ID]
    # The synthetic vision default is not listed in the inventory.
    assert all(p.id != R.DEFAULT_VISION_ID for p in R.default_profiles())


def test_configured_auxiliary_vision_model_wins():
    cfg = _Cfg({"auxiliary": {"vision": {"model": "qwen3-vl:8b",
                                         "provider": "ollama"}}})
    eff = R.resolve(cfg, role="vision")
    assert eff.model == "qwen3-vl:8b"
    assert eff.provider == "ollama"
    assert eff.capabilities["vision"] is True
    assert eff.source == "vision-default"


def test_configured_vision_provider_is_honoured():
    cfg = _Cfg({"auxiliary": {"vision": {"model": "google/gemini-2.5-flash",
                                         "provider": "openrouter"}}})
    eff = R.resolve(cfg, role="vision")
    assert eff.model == "google/gemini-2.5-flash"
    assert eff.provider == "openrouter"
    assert eff.locality == "external"
    assert eff.capabilities["vision"] is True


def test_registered_vision_profile_is_preferred(monkeypatch):
    profile = R.ModelProfile(
        id="my-vlm", kind=R.LOCAL, provider="ollama", model="my-vlm",
        locality="local", supports_vision=True, roles=[R.VISION], enabled=True)
    monkeypatch.setattr(R, "load_profiles", lambda cfg: [profile])
    eff = R.resolve(None, role="vision")
    assert eff.profile_id == "my-vlm"
    assert eff.capabilities["vision"] is True


def test_installed_local_vlm_is_selected(monkeypatch):
    monkeypatch.setattr(
        R, "_vision_installed_local", lambda cfg: ("qwen3-vl:8b",))
    eff = R.resolve(None, role="vision")
    assert eff.model == "qwen3-vl:8b"
    assert eff.capabilities["vision"] is True


def test_other_roles_are_unchanged():
    chat = R.resolve(None, role="chat")
    assert chat.capabilities["vision"] is False
    embed = R.resolve(None, role="embedding")
    assert embed.profile_id == R.DEFAULT_EMBED_ID
    assert embed.model == R.DEFAULT_EMBED_MODEL
    # chat/embedding never gain the vision synthetic default
    assert chat.profile_id != R.DEFAULT_VISION_ID
    assert embed.profile_id != R.DEFAULT_VISION_ID


def test_vision_resolution_never_raises_on_empty_config():
    for cfg in (None, _Cfg({}), _Cfg({"auxiliary": {"vision": {"model": ""}}})):
        eff = R.resolve(cfg, role="vision")
        assert eff.capabilities["vision"] is True
