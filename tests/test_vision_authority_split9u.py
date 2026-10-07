"""Vision model authority: configured tag wins + the two authorities agree.

Defect: ``vision.describe`` read its own ``intent.vision.model`` while the
resolver's ``role="vision"`` resolved a SEPARATE ladder (``auxiliary.vision``
first), and ``find_vision_model`` matched only the family prefix *before* the
colon and returned the FIRST ``/api/tags`` entry — so a configured tag suffix
(or an ``auxiliary``-only pin) could be silently ignored.

The contract now pinned here:

* ``find_vision_model`` precedence is configured exact id/tag, then family
  prefix (``:latest`` preferred), then any installed vision-capable model;
* ``vision._settings`` takes the model from the ONE resolver vision ladder, so
  the CLI/dashboard vision surface and ``resolver.resolve(role="vision")``
  report the same model (``auxiliary`` > ``intent`` > registered > installed >
  shipped);
* resolution never raises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from voicebridge import vision as VI
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


def _entries(*names):
    return [{"name": n, "size": 1024 * 1024, "digest": f"sha256:{n}"}
            for n in names]


# ── find_vision_model precedence ────────────────────────────────────────────

def test_configured_untagged_model_prefers_latest_over_sibling(monkeypatch):
    """The configured id (no tag) must bind to ``:latest``, not a family sibling."""
    monkeypatch.setattr(
        VI, "_tags", lambda url, timeout_s=10.0: _entries(
            "qubit-brain-vision:q4", "qubit-brain-vision:latest"))
    found = VI.find_vision_model("http://x", "qubit-brain-vision")
    assert found["name"] == "qubit-brain-vision:latest"


def test_configured_exact_tag_beats_family_sibling(monkeypatch):
    monkeypatch.setattr(
        VI, "_tags", lambda url, timeout_s=10.0: _entries(
            "qwen3-vl:8b-instruct", "qwen3-vl:8b"))
    found = VI.find_vision_model("http://x", "qwen3-vl:8b")
    assert found["name"] == "qwen3-vl:8b"


def test_family_prefix_is_the_second_rung(monkeypatch):
    """No exact/latest tag installed: the same family still binds."""
    monkeypatch.setattr(
        VI, "_tags", lambda url, timeout_s=10.0: _entries("qwen3-vl:q4"))
    found = VI.find_vision_model("http://x", "qwen3-vl:8b")
    assert found["name"] == "qwen3-vl:q4"


def test_any_vision_capable_model_is_the_last_rung(monkeypatch):
    """A known VLM family absent from disk falls back to another installed VLM."""
    monkeypatch.setattr(
        VI, "_tags", lambda url, timeout_s=10.0: _entries(
            "llama3:8b", "qwen3-vl:4b"))
    found = VI.find_vision_model("http://x", "qwen3-vl:8b")
    assert found["name"] == "qwen3-vl:4b"


def test_custom_configured_family_absent_is_not_silently_substituted(monkeypatch):
    """A custom-named configured model that is not installed is a typed refusal."""
    monkeypatch.setattr(
        VI, "_tags", lambda url, timeout_s=10.0: _entries(
            "llama3:8b", "qwen3-vl:8b"))
    with pytest.raises(VI.VisionError) as excinfo:
        VI.find_vision_model("http://x", "qubit-brain-vision")
    assert excinfo.value.code == VI.NO_BACKEND_CODE


def test_no_vision_capable_model_is_typed_no_backend(monkeypatch):
    monkeypatch.setattr(
        VI, "_tags", lambda url, timeout_s=10.0: _entries("llama3:8b"))
    with pytest.raises(VI.VisionError) as excinfo:
        VI.find_vision_model("http://x", "qubit-brain-vision")
    assert excinfo.value.code == VI.NO_BACKEND_CODE


def test_unreachable_endpoint_stays_typed_upstream():
    with pytest.raises(VI.VisionError) as excinfo:
        VI.find_vision_model("http://127.0.0.1:9", "qubit-brain-vision",
                             timeout_s=1)
    assert excinfo.value.code == VI.UPSTREAM_CODE


# ── the two authorities agree ───────────────────────────────────────────────

def test_settings_model_matches_resolver_vision_profile(tmp_path, monkeypatch):
    """``auxiliary.vision.model`` is the resolver's winner; both must report it."""
    cfg = _Cfg({"auxiliary": {"vision": {"model": "qubit-brain-vision"}},
                "intent": {"vision": {"model": "qwen3-vl:8b"}}},
               state_dir=tmp_path)
    resolved = R.resolve(cfg, role="vision").model
    assert resolved == "qubit-brain-vision"
    assert VI._settings(cfg)["model"] == resolved


def test_settings_uses_intent_when_auxiliary_absent(tmp_path):
    cfg = _Cfg({"intent": {"vision": {"model": "qubit-brain-vision"}}},
               state_dir=tmp_path)
    assert VI._settings(cfg)["model"] == "qubit-brain-vision"
    assert VI._settings(cfg)["model"] == R.resolve(cfg, role="vision").model


def test_settings_never_raises_and_returns_a_real_model(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "load_profiles", lambda cfg: [])
    for cfg in (_Cfg({}, state_dir=tmp_path),
                _Cfg({"intent": {"vision": {"model": ""}}}, state_dir=tmp_path)):
        settings = VI._settings(cfg)
        assert isinstance(settings["model"], str) and settings["model"]
