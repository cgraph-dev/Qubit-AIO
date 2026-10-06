"""Voice wake routing store: normalization, parse, persistence, resolve.

Ported from OpenClaw's ``voicewake-routing`` semantics. Every test is
hermetic (tmp_path / in-memory); the only I/O the module performs is
``load_config`` / ``save_config``.
"""
from __future__ import annotations

import json

from voicebridge.voice_edit import wake_routing as WR


class _Boom:
    def __str__(self):  # noqa: D105
        raise RuntimeError("boom")

    def __repr__(self):  # noqa: D105
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# normalize_trigger
# ---------------------------------------------------------------------------
def test_normalize_trigger_matches_upstream_semantics():
    assert WR.normalize_trigger("  Hey,  QUBIT!  ") == "hey qubit"
    assert WR.normalize_trigger("robot   wake") == "robot wake"
    # edge punctuation + symbols stripped per token
    assert WR.normalize_trigger("#open$ the <door>") == "open the door"
    # empties dropped
    assert WR.normalize_trigger("!! ,,  robot ,, wake ..") == "robot wake"


def test_normalize_trigger_junk_is_empty_and_never_raises():
    assert WR.normalize_trigger(None) == ""
    assert WR.normalize_trigger("") == ""
    assert WR.normalize_trigger("   ") == ""
    assert WR.normalize_trigger("!!!") == ""
    assert WR.normalize_trigger(_Boom()) == ""


# ---------------------------------------------------------------------------
# parse_config
# ---------------------------------------------------------------------------
def test_parse_drops_invalid_targets_and_rules():
    raw = {
        "version": 99,
        "defaultTarget": {"agentId": "main", "sessionKey": "agent:x:y"},
        "routes": [
            {"trigger": "robot wake", "target": {"agentId": "main"}},
            {"trigger": "both", "target": {"agentId": "a",
                                           "sessionKey": "agent:a:a"}},
            {"trigger": "none", "target": {}},
            {"trigger": "", "target": {"mode": "current"}},
            {"trigger": "current one", "target": {"mode": "current"}},
            {"trigger": "sess", "target": {"sessionKey": "agent:main:main"}},
            {"trigger": "x" * 65, "target": {"mode": "current"}},
            {"trigger": "badmode", "target": {"mode": "weird"}},
            "not-an-object",
            {"target": {"mode": "current"}},
        ],
        "updatedAtMs": 1730000000000,
    }
    cfg = WR.parse_config(raw)
    assert cfg.version == 1
    # malformed default (both agentId + sessionKey) -> current
    assert cfg.default_target == WR.RouteTarget.current()
    assert [r.trigger for r in cfg.routes] == [
        "robot wake", "current one", "sess"]
    assert cfg.updated_at_ms == 1730000000000


def test_parse_caps_routes_at_32():
    raw = {"routes": [{"trigger": f"t{i}", "target": {"mode": "current"}}
                      for i in range(50)]}
    cfg = WR.parse_config(raw)
    assert len(cfg.routes) == WR.MAX_ROUTES == 32
    assert [r.trigger for r in cfg.routes] == [f"t{i}" for i in range(32)]


def test_parse_non_dict_is_default_and_never_raises():
    assert WR.parse_config(None) == WR.WakeRoutingConfig()
    assert WR.parse_config("junk") == WR.WakeRoutingConfig()
    assert WR.parse_config([1, 2, 3]) == WR.WakeRoutingConfig()
    assert WR.parse_config(_Boom()) == WR.WakeRoutingConfig()
    cfg = WR.parse_config({"routes": "notalist", "defaultTarget": "x",
                           "updatedAtMs": 5})
    assert cfg.default_target == WR.RouteTarget.current()
    assert cfg.routes == []
    assert cfg.updated_at_ms == 5


def test_parse_updated_at_ms_junk_is_zero():
    for bad in (True, False, float("inf"), float("nan"), -5, "123", None):
        assert WR.parse_config({"updatedAtMs": bad}).updated_at_ms == 0


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------
def test_load_missing_and_corrupt_is_default(tmp_path):
    assert WR.load_config(tmp_path / "nope.json") == WR.WakeRoutingConfig()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert WR.load_config(bad) == WR.WakeRoutingConfig()
    null = tmp_path / "null.json"
    null.write_text("null", encoding="utf-8")
    assert WR.load_config(null) == WR.WakeRoutingConfig()


def test_save_round_trips_and_is_atomic(tmp_path):
    p = tmp_path / "wake_routing.json"
    cfg = WR.WakeRoutingConfig(
        default_target=WR.RouteTarget.for_agent("main"),
        routes=[WR.RouteRule("robot wake",
                             WR.RouteTarget.for_session("agent:main:main"))],
        updated_at_ms=0)
    saved = WR.save_config(cfg, p)
    assert saved.updated_at_ms > 0

    back = WR.load_config(p)
    assert back.default_target == WR.RouteTarget.for_agent("main")
    assert back.routes == [WR.RouteRule(
        "robot wake", WR.RouteTarget.for_session("agent:main:main"))]
    assert back.updated_at_ms == saved.updated_at_ms

    # the on-disk shape mirrors upstream
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert set(doc) == {"version", "defaultTarget", "routes", "updatedAtMs"}
    assert doc["version"] == 1
    assert doc["defaultTarget"] == {"agentId": "main"}

    # atomic: no temp files left behind
    assert [f.name for f in tmp_path.iterdir()
            if f.name.startswith(".wake-routing-")] == []


def test_save_normalizes_and_never_raises_on_junk(tmp_path):
    # a malformed in-memory config is normalized before it is written
    out = WR.save_config({"routes": [{"trigger": "T, t", "target": {}}]},
                         tmp_path / "a.json")
    assert out.routes == []
    assert out.default_target == WR.RouteTarget.current()
    assert out.updated_at_ms > 0
    # junk config object -> default, still returns
    junk = WR.save_config(_Boom(), tmp_path / "b.json")
    assert junk.routes == []
    assert junk.default_target == WR.RouteTarget.current()
    assert (tmp_path / "b.json").exists()


def test_save_failure_returns_config_without_partial_file(tmp_path, monkeypatch):
    p = tmp_path / "wake_routing.json"

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(WR.os, "replace", boom)
    cfg = WR.WakeRoutingConfig(
        routes=[WR.RouteRule("hi", WR.RouteTarget.current())])
    out = WR.save_config(cfg, p)
    assert out.routes == cfg.routes
    assert out.updated_at_ms > 0
    assert not p.exists()
    assert [f.name for f in tmp_path.iterdir()
            if f.name.startswith(".wake-routing-")] == []


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------
def _cfg(*rules, default=None):
    return WR.WakeRoutingConfig(
        default_target=default or WR.RouteTarget.current(),
        routes=[WR.RouteRule(t, tgt) for t, tgt in rules])


def test_resolve_picks_longest_matching_trigger():
    cfg = _cfg(
        ("google", WR.RouteTarget.for_agent("g")),
        ("hey google", WR.RouteTarget.for_session("agent:main:google")),
        ("open", WR.RouteTarget.for_agent("o")),
    )
    assert WR.resolve_target("hey google play music", cfg) == \
        WR.RouteTarget.for_session("agent:main:google")
    # case / space / punctuation insensitive thanks to normalization
    assert WR.resolve_target("HEY,   Google!", cfg) == \
        WR.RouteTarget.for_session("agent:main:google")
    assert WR.resolve_target("please open youtube", cfg) == \
        WR.RouteTarget.for_agent("o")
    assert WR.resolve_target("google", cfg) == WR.RouteTarget.for_agent("g")


def test_resolve_falls_back_to_default_and_handles_junk():
    cfg = _cfg(("google", WR.RouteTarget.for_agent("g")),
               default=WR.RouteTarget.for_agent("main"))
    assert WR.resolve_target("nothing here", cfg) == \
        WR.RouteTarget.for_agent("main")
    assert WR.resolve_target("", cfg) == WR.RouteTarget.for_agent("main")
    assert WR.resolve_target(None, cfg) == WR.RouteTarget.for_agent("main")
    assert WR.resolve_target(123, cfg) == WR.RouteTarget.for_agent("main")
    # word-boundary: "open" must not match inside "opened"
    cfg2 = _cfg(("open", WR.RouteTarget.for_agent("o")))
    assert WR.resolve_target("opened the door", cfg2) == \
        WR.RouteTarget.current()
    # malformed cfg -> current, never raises
    assert WR.resolve_target("x", object()) == WR.RouteTarget.current()
    assert WR.resolve_target("x", _Boom()) == WR.RouteTarget.current()


def test_resolve_equal_rank_prefers_first_declared():
    cfg = _cfg(
        ("hey qubit", WR.RouteTarget.for_agent("a")),
        ("hey qubit", WR.RouteTarget.for_session("agent:a:a")),
    )
    assert WR.resolve_target("hey qubit do it", cfg) == \
        WR.RouteTarget.for_agent("a")


# ---------------------------------------------------------------------------
# add_route / remove_route (pure)
# ---------------------------------------------------------------------------
def test_add_route_is_pure_and_replaces_by_normalized_trigger():
    base = WR.WakeRoutingConfig()
    added = WR.add_route(base, "Robot, Wake!", WR.RouteTarget.for_agent("main"))
    assert base.routes == []  # input untouched
    assert [r.trigger for r in added.routes] == ["robot wake"]

    replaced = WR.add_route(added, "ROBOT WAKE",
                            WR.RouteTarget.for_session("agent:main:main"))
    assert len(replaced.routes) == 1
    assert replaced.routes[0].target == \
        WR.RouteTarget.for_session("agent:main:main")


def test_add_route_rejects_invalid_and_respects_cap():
    base = WR.WakeRoutingConfig()
    assert WR.add_route(base, "", WR.RouteTarget.current()) == base
    assert WR.add_route(base, "t",
                        {"agentId": "a", "sessionKey": "s"}) == base
    assert WR.add_route(base, "x" * 65, WR.RouteTarget.current()) == base
    assert WR.add_route(base, "t", {"mode": "weird"}) == base

    full = WR.WakeRoutingConfig(routes=[
        WR.RouteRule(f"t{i}", WR.RouteTarget.current())
        for i in range(WR.MAX_ROUTES)])
    capped = WR.add_route(full, "overflow", WR.RouteTarget.current())
    assert len(capped.routes) == WR.MAX_ROUTES
    assert all(r.trigger != "overflow" for r in capped.routes)


def test_remove_route_is_pure():
    cfg = _cfg(("a", WR.RouteTarget.current()),
               ("b", WR.RouteTarget.current()))
    out = WR.remove_route(cfg, "A!")
    assert [r.trigger for r in out.routes] == ["b"]
    assert [r.trigger for r in cfg.routes] == ["a", "b"]  # input untouched
    assert WR.remove_route(cfg, "") is cfg or \
        WR.remove_route(cfg, "").routes == cfg.routes


def test_public_api_never_raises_on_junk(tmp_path):
    junk = _Boom()
    WR.normalize_trigger(junk)
    WR.parse_config(junk)
    WR.load_config(junk)
    WR.save_config(junk, tmp_path / "j.json")
    WR.resolve_target(junk, junk)
    WR.add_route(junk, junk, junk)
    WR.remove_route(junk, junk)
