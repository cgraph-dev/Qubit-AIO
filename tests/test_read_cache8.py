"""Focused tests for the dashboard read cache (``voicebridge.read_cache``).

Covers the cache primitive (hit/miss, TTL, invalidation, disabled env, deep
copy isolation, profile-scoped keys) and the handlers that use it: cached
reads return the same payload as an uncached read, and a mutation invalidates
the relevant namespace.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    from voicebridge import read_cache as RC

    # Enable the cache in this file only: live suites run with it off so a
    # retained snapshot cannot perturb their strict GC/warning checks.
    monkeypatch.setenv("QUBIT_READ_CACHE", "1")
    RC.reset_for_tests()
    yield
    RC.reset_for_tests()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    voice = tmp_path / "voice.yaml"
    voice.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    c = C.Config.load(voice)
    home = c.state_dir() / "runtime"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n  provider: opencode-go\n  default: deepseek-v4-flash\n",
        encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return c


# --------------------------------------------------------------------------
# primitive
# --------------------------------------------------------------------------
def test_get_or_compute_hit_miss_and_deep_copy():
    from voicebridge import read_cache as RC

    calls = []

    def compute():
        calls.append(1)
        return {"value": len(calls)}

    key = ("unit", "a")
    assert RC.get(key) is None
    first = RC.get_or_compute(key, compute, 60)
    second = RC.get_or_compute(key, compute, 60)
    assert len(calls) == 1
    assert first == second == {"value": 1}
    second["value"] = 99
    assert RC.get(key)["value"] == 1  # callers cannot mutate the cached copy


def test_ttl_expiry_and_invalidation(monkeypatch):
    from voicebridge import read_cache as RC

    calls = []
    key = ("unit", "ttl")
    RC.put(key, {"n": 1}, 0.01)
    RC.get_or_compute(key, lambda: calls.append(1) or {"n": 2}, 60)
    assert calls == []

    # Expire the entry by moving the clock past its deadline.
    current = RC.time.monotonic()
    monkeypatch.setattr(RC.time, "monotonic", lambda: current + 1.0)
    RC.get_or_compute(key, lambda: calls.append(1) or {"n": 3}, 60)
    assert calls == [1]

    RC.put(key, {"n": 4}, 60)
    assert RC.invalidate("unit") == 1
    assert RC.get(key) is None


def test_env_can_disable_cache(monkeypatch):
    from voicebridge import read_cache as RC

    monkeypatch.setenv("QUBIT_READ_CACHE", "0")
    calls = []
    key = ("unit", "off")
    for _ in range(2):
        RC.get_or_compute(key, lambda: calls.append(1) or {"n": len(calls)}, 60)
    assert len(calls) == 2
    assert RC.get(key) is None


def test_cfg_key_is_profile_scoped(tmp_path):
    from voicebridge import read_cache as RC

    class _Cfg:
        path = None

        def __init__(self, state):
            self._state = state

        def state_dir(self):
            return self._state

    a = RC.cfg_key(_Cfg(tmp_path / "a"), "ns", "same")
    b = RC.cfg_key(_Cfg(tmp_path / "b"), "ns", "same")
    assert a != b
    assert a[0] == "ns"


# --------------------------------------------------------------------------
# handlers: hit/miss, unchanged output, invalidation
# --------------------------------------------------------------------------
def test_model_options_cache_refresh_and_invalidation(cfg, monkeypatch):
    import qubit_cli.inventory as inv
    from voicebridge import qubit_compat as QC
    from voicebridge import read_cache as RC

    calls = []

    def _build(*_a, **_k):
        calls.append(1)
        return {
            "providers": [{
                "slug": "alpha", "name": "Alpha", "models": ["a-1"],
                "total_models": 1, "authenticated": True,
                "is_current": False}],
            "model": "a-1", "provider": "alpha"}

    monkeypatch.setattr(QC, "_vendored_runtime", lambda _cfg: None)
    monkeypatch.setattr(inv, "load_picker_context", lambda: {})
    monkeypatch.setattr(inv, "build_model_options_payload", _build)

    status, first = QC.h_model_options(cfg, {}, {}, {})
    assert status == 200
    status, second = QC.h_model_options(cfg, {}, {}, {})
    assert status == 200 and second == first
    assert len(calls) == 1

    # refresh=1 always re-runs the builder (and re-warms the entry).
    QC.h_model_options(cfg, {"refresh": "1"}, {}, {})
    assert len(calls) == 2

    # A different query is a different entry.
    QC.h_model_options(cfg, {"include_unconfigured": "1"}, {}, {})
    assert len(calls) == 3

    RC.invalidate("model_options")
    QC.h_model_options(cfg, {}, {}, {})
    assert len(calls) == 4


def test_office_tower_cache_hits_and_pinned_clock_bypasses(cfg, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import read_cache as RC
    from voicebridge.office import tower as TWR

    calls = []

    def _state(cfg, *, now=None, preview=False):
        calls.append(now)
        return {"schema": "office/tower", "now": now or 1.0, "empty": True,
                "characters": [], "activity": [], "history": [],
                "hud": {}, "floorplan": {"floors": [], "grid": {}},
                "empty_reason": "not started", "motion_rule": "events",
                "read_only": True, "preview": False}

    monkeypatch.setattr(TWR, "state", _state)
    status, first = QC.h_office_tower(cfg, {}, {}, {})
    assert status == 200
    status, second = QC.h_office_tower(cfg, {}, {}, {})
    assert status == 200 and second == first
    assert len(calls) == 1

    # A pinned clock always computes (never served from the poll snapshot).
    status, pinned = QC.h_office_tower(cfg, {"now": "1700000000"}, {}, {})
    assert status == 200 and pinned["now"] == 1700000000.0
    assert len(calls) == 2

    RC.invalidate("office_tower")
    QC.h_office_tower(cfg, {}, {}, {})
    assert len(calls) == 3


def test_mcp_list_cache_and_write_invalidation(cfg, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import read_cache as RC

    calls = []
    original = MS._list_servers_uncached

    def _counted(cfg):
        calls.append(1)
        return original(cfg)

    monkeypatch.setattr(MS, "_list_servers_uncached", _counted)

    MS.list_servers(cfg)
    MS.list_servers(cfg)
    assert len(calls) == 1

    MS.upsert(cfg, "cached-emitter", {
        "transport": "stdio", "command": "cached-emitter", "enabled": True})
    listing = MS.list_servers(cfg)
    assert len(calls) == 2
    assert "cached-emitter" in {s["name"] for s in listing["servers"]}
    assert RC.stats()["invalidations"] >= 1


def test_skills_cache_and_mutation_invalidation(cfg, monkeypatch):
    from voicebridge import read_cache as RC
    from voicebridge import skill_catalog

    calls = []
    payload = [{"name": "demo", "enabled": True, "origin": "catalog"}]

    def _entries(_cfg):
        calls.append(1)
        return list(payload)

    monkeypatch.setattr(skill_catalog, "_catalog_entries_uncached", _entries)

    assert skill_catalog.catalog_entries(cfg) == payload
    assert skill_catalog.catalog_entries(cfg) == payload
    assert len(calls) == 1

    # The write-path invalidation hook (used by create/edit/delete/toggle).
    skill_catalog.clear_skills_prompt_cache()
    assert skill_catalog.catalog_entries(cfg) == payload
    assert len(calls) == 2
    assert RC.stats()["invalidations"] >= 1


def test_device_inventory_component_cache(cfg, monkeypatch):
    from voicebridge import read_cache as RC
    from voicebridge import runtime_inventory as RI

    calls = []

    def _probe(_cfg):
        calls.append(1)
        return {"schema": "qubit-device-capabilities/1", "state": "OK"}

    monkeypatch.setattr(RI, "_device_inventory", _probe)

    first = RI.device_inventory(cfg)
    second = RI.device_inventory(cfg)
    assert calls == [1]
    assert first == second
    assert first is not second

    RC.invalidate("device_inventory")
    RI.device_inventory(cfg)
    assert calls == [1, 1]


def test_cached_payloads_equal_uncached_output(cfg, monkeypatch):
    """A cache hit must be byte-for-byte the payload the live read produces."""
    from voicebridge import read_cache as RC
    from voicebridge import mcp_servers as MS
    from voicebridge import skill_catalog

    home = Path(os.environ["QUBIT_HOME"])
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill\n---\nbody\n",
        encoding="utf-8")
    MS.upsert(cfg, "demo-mcp", {
        "transport": "stdio", "command": "demo-mcp", "enabled": True})

    cached_skills = skill_catalog.catalog_entries(cfg)
    cached_mcp = MS.list_servers(cfg)
    assert any(e.get("name") == "demo" for e in cached_skills)
    assert "demo-mcp" in {s["name"] for s in cached_mcp["servers"]}

    monkeypatch.setenv("QUBIT_READ_CACHE", "0")
    RC.reset_for_tests()
    fresh_skills = skill_catalog.catalog_entries(cfg)
    fresh_mcp = MS.list_servers(cfg)
    assert cached_skills == fresh_skills
    assert cached_mcp == fresh_mcp
