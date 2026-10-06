"""Wake-word list + wake routing through the voice-edit settings surface.

The dashboard / voice settings surface must be able to read and mutate the
wake words (``wake_words.json``) and the wake routing rules
(``wake_routing.json``) through ``settings_view`` / ``update_voice_edit`` — no
new route, no new auth, no legacy brand.

Hermetic: the two JSON stores are redirected into ``tmp_path`` by
monkeypatching their path resolvers (``wake_words.wake_words_path`` /
``wake_routing.routing_path``), and the config target is the fake cfg's own
``.path``. No service, no network, no real state dir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod  # noqa: E402
from voicebridge.voice_edit import wake_routing as WR  # noqa: E402
from voicebridge.voice_edit import wake_words as WW  # noqa: E402
from voicebridge.voice_edit.schema import VoiceEditError  # noqa: E402


class FakeCfg:
    """Mirrors the settings tests' cfg surface: ``.path``, ``.get`` and
    ``.data`` (the live tree ``_sync_live_cfg`` reflects into)."""

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


def _cfg(tmp_path: Path) -> FakeCfg:
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n", encoding="utf-8")
    return FakeCfg({"voice_edit": {"enabled": True}}, path=p)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Never resolve the real config/state roots during these tests."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(WW, "wake_words_path",
                        lambda: tmp_path / "wake_words.json")
    monkeypatch.setattr(WR, "routing_path",
                        lambda: tmp_path / "wake_routing.json")


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------
def test_view_includes_wake_words_and_routing_defaults(tmp_path):
    view = settings_mod.settings_view(_cfg(tmp_path))
    assert view["wake_words"] == ["hey qubit"]
    assert view["wake_words_default"] == list(WW.DEFAULT_TRIGGERS)
    assert view["wake_words_default"] == ["hey qubit"]
    assert view["wake_routing"]["version"] == 1
    assert view["wake_routing"]["defaultTarget"] == {"mode": "current"}
    assert view["wake_routing"]["routes"] == []
    # the routing view must be JSON-safe (a plain dict/list tree)
    json.dumps(view["wake_routing"])


def test_view_reflects_persisted_stores(tmp_path):
    WW.save_triggers(["hey qubit", "computer"])
    WR.save_config({
        "defaultTarget": {"agentId": "main"},
        "routes": [{"trigger": "robot wake",
                    "target": {"sessionKey": "agent:main:main"}}],
    })
    view = settings_mod.settings_view(_cfg(tmp_path))
    assert view["wake_words"] == ["hey qubit", "computer"]
    assert view["wake_routing"]["defaultTarget"] == {"agentId": "main"}
    assert view["wake_routing"]["routes"] == [
        {"trigger": "robot wake", "target": {"sessionKey": "agent:main:main"}}]


def test_view_defaults_do_not_write_any_store(tmp_path):
    settings_mod.settings_view(_cfg(tmp_path))
    assert not (tmp_path / "wake_words.json").exists()
    assert not (tmp_path / "wake_routing.json").exists()


# ---------------------------------------------------------------------------
# wake_words: PUT
# ---------------------------------------------------------------------------
def test_put_wake_words_add_remove_round_trips(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"wake_words_add": "Computer!"})
    assert settings_mod.settings_view(cfg)["wake_words"] == \
        ["hey qubit", "computer"]
    # persisted to the JSON store, never the voice_edit YAML
    disk = json.loads((tmp_path / "wake_words.json").read_text(encoding="utf-8"))
    assert disk["triggers"] == ["hey qubit", "computer"]
    assert disk["updatedAtMs"] > 0
    assert "computer" not in cfg.path.read_text(encoding="utf-8")

    settings_mod.update_voice_edit(cfg, {"wake_words_remove": "computer"})
    assert settings_mod.settings_view(cfg)["wake_words"] == ["hey qubit"]


def test_put_wake_words_full_list_replaces(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(
        cfg, {"wake_words": ["Hey, Qubit!", "Computer", "computer"]})
    assert settings_mod.settings_view(cfg)["wake_words"] == \
        ["hey qubit", "computer"]


def test_put_wake_words_add_rejects_non_string(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, {"wake_words_add": 42})
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"
    assert not (tmp_path / "wake_words.json").exists()


@pytest.mark.parametrize("body", [
    {"wake_words": "hey qubit"},
    {"wake_words": 42},
    {"wake_words": {"triggers": ["hey qubit"]}},
    {"wake_words_add": 42},
    {"wake_words_add": None},
    {"wake_words_add": ["computer"]},
    {"wake_words_remove": 7},
    {"wake_words_remove": None},
])
def test_put_wake_words_invalid_rejected(tmp_path, body):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, body)
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"
    assert not (tmp_path / "wake_words.json").exists()
    assert "wake" not in cfg.path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# wake_routing: PUT
# ---------------------------------------------------------------------------
def test_put_wake_route_add_remove_round_trips(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "wake_route_add": {"trigger": "Open YouTube!",
                           "target": {"agentId": "main"}}})
    routes = settings_mod.settings_view(cfg)["wake_routing"]["routes"]
    assert routes == [{"trigger": "open youtube",
                       "target": {"agentId": "main"}}]
    disk = json.loads(
        (tmp_path / "wake_routing.json").read_text(encoding="utf-8"))
    assert disk["routes"] == routes
    assert "open youtube" not in cfg.path.read_text(encoding="utf-8")

    settings_mod.update_voice_edit(cfg, {"wake_route_remove": "open youtube"})
    assert settings_mod.settings_view(cfg)["wake_routing"]["routes"] == []


def test_put_wake_routing_full_config_round_trips(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"wake_routing": {
        "version": 1,
        "defaultTarget": {"agentId": "main"},
        "routes": [{"trigger": "Robot, Wake!",
                    "target": {"sessionKey": "agent:main:main"}}],
    }})
    rt = settings_mod.settings_view(cfg)["wake_routing"]
    assert rt["defaultTarget"] == {"agentId": "main"}
    assert rt["routes"] == [
        {"trigger": "robot wake", "target": {"sessionKey": "agent:main:main"}}]


@pytest.mark.parametrize("body", [
    {"wake_routing": "not-an-object"},
    {"wake_routing": [1, 2, 3]},
    {"wake_routing": 42},
    {"wake_route_add": "not-an-object"},
    {"wake_route_add": None},
    {"wake_route_add": {"trigger": "", "target": {"mode": "current"}}},
    {"wake_route_add": {"trigger": "   ", "target": {"mode": "current"}}},
    {"wake_route_add": {"target": {"mode": "current"}}},
    {"wake_route_add": {"trigger": "x", "target": {}}},
    {"wake_route_add": {"trigger": "x",
                        "target": {"agentId": "a", "sessionKey": "b"}}},
    {"wake_route_add": {"trigger": "x" * 100,
                        "target": {"mode": "current"}}},
    {"wake_route_remove": 7},
    {"wake_route_remove": None},
])
def test_put_wake_routing_invalid_rejected(tmp_path, body):
    cfg = _cfg(tmp_path)
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, body)
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"
    assert not (tmp_path / "wake_routing.json").exists()
    assert "routing" not in cfg.path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# combined + no partial state
# ---------------------------------------------------------------------------
def test_put_both_wake_stores_at_once(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {
        "wake_words_add": "computer",
        "wake_route_add": {"trigger": "hey computer",
                           "target": {"agentId": "main"}},
    })
    view = settings_mod.settings_view(cfg)
    assert view["wake_words"] == ["hey qubit", "computer"]
    assert view["wake_routing"]["routes"][0]["trigger"] == "hey computer"


def test_invalid_field_never_writes_partial_state(tmp_path):
    cfg = _cfg(tmp_path)
    settings_mod.update_voice_edit(cfg, {"wake_words": ["hey qubit"]})
    assert settings_mod.settings_view(cfg)["wake_words"] == ["hey qubit"]

    # one valid half + one invalid half -> the whole request is rejected and
    # neither store is touched.
    with pytest.raises(VoiceEditError) as exc:
        settings_mod.update_voice_edit(cfg, {
            "wake_words_add": "computer",
            "wake_route_add": {"trigger": "", "target": {}},
        })
    assert exc.value.code == "VOICE_EDIT_BAD_SETTINGS"
    assert settings_mod.settings_view(cfg)["wake_words"] == ["hey qubit"]
    assert not (tmp_path / "wake_routing.json").exists()


def test_mixing_valid_config_and_route_convenience(tmp_path):
    cfg = _cfg(tmp_path)
    # an explicit full config is the base for the convenience add
    settings_mod.update_voice_edit(cfg, {
        "wake_routing": {"defaultTarget": {"agentId": "base"}},
        "wake_route_add": {"trigger": "hey base",
                           "target": {"agentId": "main"}},
    })
    rt = settings_mod.settings_view(cfg)["wake_routing"]
    assert rt["defaultTarget"] == {"agentId": "base"}
    assert rt["routes"] == [{"trigger": "hey base",
                             "target": {"agentId": "main"}}]


# ---------------------------------------------------------------------------
# fail-soft: junk never escapes as an unexpected exception
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("junk", [None, 42, "nope", object(), ["a", None]])
def test_wake_words_junk_is_typed_or_accepted_never_raises(tmp_path, junk):
    cfg = _cfg(tmp_path)
    try:
        settings_mod.update_voice_edit(cfg, {"wake_words_add": junk})
    except VoiceEditError as exc:
        assert exc.code == "VOICE_EDIT_BAD_SETTINGS"
    # any other exception type would fail the test naturally


def test_structurally_valid_but_useless_wake_words_degrades_to_defaults(
        tmp_path):
    cfg = _cfg(tmp_path)
    # all elements sanitize away -> the store records an empty list, and the
    # effective view falls back to the built-in default (fail-soft).
    settings_mod.update_voice_edit(
        cfg, {"wake_words": [None, {}, "   ", "!!!"]})
    assert settings_mod.settings_view(cfg)["wake_words"] == ["hey qubit"]


def test_setting_key_is_listed_and_default_list_is_view_only(tmp_path):
    keys = set(settings_mod.SETTINGS_KEYS)
    for k in ("wake_words", "wake_words_add", "wake_words_remove",
              "wake_routing", "wake_route_add", "wake_route_remove"):
        assert k in keys, f"{k!r} missing from SETTINGS_KEYS"
    # the read-only default list is surfaced but is not an accepted key
    assert "wake_words_default" not in keys
