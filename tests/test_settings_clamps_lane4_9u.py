"""Lane 4 (2026-10-03) — settings clamp gaps + script timeout budgets.

RED-first audit findings:
(a) ``wake.arm_window_s`` is accepted by ``update_voice_edit`` with NO clamp,
so a negative or absurd window persists (every sibling wake key is clamped);
(b) ``brain_timeout_s`` likewise unclamped (a negative breaks the brain-router
classify wait); (c) ``local_profile``/``api_profile`` persist unbounded
strings (siblings are [:120]-capped); (d) ``scripts/web_search.py`` DDG layer
had no timeout (the browser layer is 25 s-bounded); (e) ``scripts/weather.py``
fetches twice at 8 s each, over its caller's 12 s subprocess cap.

All hermetic: temp config paths only, DDGS stubbed, no network, no service.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402

from voicebridge.voice_edit import settings as settings_mod  # noqa: E402


class FakeCfg:
    def __init__(self, data=None, path=None):
        self.data = data or {}
        self.path = path

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _cfg(tmp_path: Path) -> FakeCfg:
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")
    return FakeCfg({"voice_edit": {"enabled": True}}, path=p)


def test_arm_window_s_clamped(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"wake": {"arm_window_s": -5}})
    assert settings_mod.settings_view(cfg)["wake"]["arm_window_s"] == 2.0
    settings_mod.update_voice_edit(cfg, {"wake": {"arm_window_s": 999}})
    assert settings_mod.settings_view(cfg)["wake"]["arm_window_s"] == 60.0
    settings_mod.update_voice_edit(cfg, {"wake": {"arm_window_s": 12.0}})
    assert settings_mod.settings_view(cfg)["wake"]["arm_window_s"] == 12.0


def test_brain_timeout_s_clamped(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"brain_timeout_s": -1})
    assert settings_mod.settings_view(cfg)["brain_timeout_s"] == 0.5
    settings_mod.update_voice_edit(cfg, {"brain_timeout_s": 999})
    assert settings_mod.settings_view(cfg)["brain_timeout_s"] == 30.0
    settings_mod.update_voice_edit(cfg, {"brain_timeout_s": 4.0})
    assert settings_mod.settings_view(cfg)["brain_timeout_s"] == 4.0


def test_profile_names_length_capped(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"local_profile": "x" * 200,
                                         "api_profile": "y" * 200})
    view = settings_mod.settings_view(cfg)
    assert len(view["local_profile"]) == 120
    assert len(view["api_profile"]) == 120


def test_ddg_layer_bounded(monkeypatch):
    import web_search as ws
    seen = {}

    class FakeDDGS:
        def __init__(self, *a, **k):
            seen.update(k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=5):
            return [{"title": "t", "body": "b"},
                    {"title": "t2", "body": "b2"}]

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    assert ws.ddg_answer("latest node version") != ""
    assert float(seen.get("timeout", 0)) > 0, seen


def test_weather_fetch_budget_under_caller_cap():
    import inspect
    import weather as w
    per_fetch = inspect.signature(w._get_json).parameters["timeout"].default
    assert float(per_fetch) * 2 <= 12.0, per_fetch  # caller cap is 12 s


# --- Lane 4b (2026-10-03) — type parity + unbounded-string gaps. ---
#
# RED-first audit findings (same class as the Slice-2 web_paid_first gap):
# (f) ``enabled``/``allow_api``/``confirm_before_replace``/``auto_send``/
# ``brain_router`` are accepted by ``update_voice_edit`` with NO bool coercion
# (every sibling bool key is coerced), so a ``"true"``/``"false"`` string
# persists as a YAML string — the GET view masks it via ``bool()`` while the
# file holds the wrong type; (g) ``service_url``/``stt_ws_url`` (helper
# network-path URLs) persist raw: unstripped and unbounded; (h) ``asr_device``/
# ``stt_engine`` likewise unbounded (model-name siblings are [:120]-capped).
#
# All hermetic: temp config paths only, no network, no service.


def test_bool_settings_coerced_to_bool(tmp_path):
    import yaml
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "enabled": "false",
        "allow_api": "true",
        "auto_send": "yes",
        "confirm_before_replace": 0,
        "brain_router": "False",
    })
    disk = yaml.safe_load((tmp_path / "voice.yaml").read_text())["voice_edit"]
    for key in ("enabled", "allow_api", "auto_send",
                "confirm_before_replace", "brain_router"):
        assert isinstance(disk[key], bool), (key, disk[key])
    # bool() semantics, identical to every sibling bool knob (web_paid_first
    # precedent): non-empty strings are True, 0 is False.
    assert disk["enabled"] is True
    assert disk["allow_api"] is True
    assert disk["auto_send"] is True
    assert disk["confirm_before_replace"] is False
    assert disk["brain_router"] is True
    view = settings_mod.settings_view(cfg)
    for key in ("allow_api", "auto_send",
                "confirm_before_replace", "brain_router"):
        assert isinstance(view[key], bool), key
    assert isinstance(view["enabled"], bool)


def test_helper_endpoint_strings_stripped_and_capped(tmp_path):
    import yaml
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "service_url": "  https://helper.local:8891  ",
        "stt_ws_url": "w" * 600,
        "asr_device": "d" * 200,
        "stt_engine": "e" * 200,
    })
    disk = yaml.safe_load((tmp_path / "voice.yaml").read_text())["voice_edit"]
    assert disk["service_url"] == "https://helper.local:8891"
    assert len(disk["stt_ws_url"]) <= 512
    assert disk["stt_ws_url"] == "w" * 512
    assert len(disk["asr_device"]) == 120
    assert len(disk["stt_engine"]) == 120
