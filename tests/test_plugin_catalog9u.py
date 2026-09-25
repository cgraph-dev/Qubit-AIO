"""Qubit dashboard plugin catalog — the real local loadable catalog.

Proves, through the production compat dispatcher and the catalog module:

* ``GET /api/dashboard/plugins/catalog`` is a registered real route (not a 404)
  and returns the real local loadable plugin set with provenance;
* every listed entry has a real on-disk manifest path and a real loader;
* the upstream remote curated index is a typed ``BLOCKED_REMOTE_PLUGIN``
  refusal naming the prerequisite, never fetched;
* the detail route reports manifest validation + dependency checks;
* the hub surfaces the vendored agent fleet, the vendored dashboard
  extensions, the Qubit overlays, and the real memory providers;
* enable/disable routes a real vendored plugin through the runtime allow/deny
  lists; an unknown id is a typed ``PLUGIN_NOT_FOUND`` (never a raw Python
  exception class name); and reload requires confirmation and reports the real
  store/catalog counts.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    # Isolate the vendored runtime home so enable/disable config writes and the
    # plugin store never touch the operator's live QUBIT_HOME.
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _catalog(cfg, query=None):
    from voicebridge import qubit_compat as HC

    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/plugins/catalog",
                            dict(query or {}), {})
    return code, out


def test_catalog_route_is_registered_and_real():
    from voicebridge import qubit_compat as HC

    route, params = HC.match("GET", "/api/dashboard/plugins/catalog")
    assert route is not None
    assert route["handler"] == "plugins_catalog"
    assert route["disposition"] == "implemented"
    assert callable(HC.HANDLERS.get("plugins_catalog"))
    detail, params = HC.match("GET", "/api/dashboard/plugins/catalog/kanban")
    assert detail is not None and detail["handler"] == "plugin_catalog_detail"
    assert params == {"id": "kanban"}
    reload_route, _ = HC.match("POST", "/api/dashboard/plugins/reload")
    assert reload_route is not None and reload_route["handler"] == "plugins_reload"


def test_catalog_lists_real_loadable_plugins_with_provenance(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg)
    assert code == 200 and out["ok"] is True
    entries = out["entries"]
    assert entries, "catalog must not be empty"
    # Every listed entry is loadable; every on-disk plugin carries a real
    # manifest path (the in-code builtin carries its manifest in the registry).
    assert all(e["loadable"] for e in entries)
    assert all(e["manifest_path"] for e in entries
               if e["source"] != "qubit-builtin"), [
        e["id"] for e in entries
        if e["source"] != "qubit-builtin" and not e["manifest_path"]]
    by_id = {e["id"]: e for e in entries}
    # The vendored agent fleet, the vendored dashboard extensions, and the
    # Qubit overlays are all present with distinct real sources.
    assert by_id["security-guidance"]["source"] == "bundled"
    assert by_id["security-guidance"]["kind"] == "hook"
    assert by_id["kanban"]["source"] == "dashboard"
    assert by_id["kanban"]["has_dashboard_manifest"] is True
    assert by_id["qubit-boards"]["source"] == "overlay"
    assert by_id["qubit-boards"]["has_dashboard_manifest"] is True
    # Real plugin-provided capabilities are surfaced.
    assert by_id["spotify"]["provides"]["tools"], by_id["spotify"]
    assert set(by_id["security-guidance"]["provides"]["hooks"]) == {
        "pre_tool_call", "transform_tool_result"}


def test_catalog_remote_index_is_a_typed_refusal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg)
    assert code == 200
    remote = out["remote"]
    assert remote["state"] == "REFUSED"
    assert remote["code"] == "BLOCKED_REMOTE_PLUGIN"
    assert remote["prerequisite"]


def test_catalog_search_filters_real_entries(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg, {"q": "browser", "source": "bundled"})
    assert code == 200
    ids = {e["id"] for e in out["entries"]}
    assert ids == {"browser/browser_use", "browser/browserbase",
                   "browser/firecrawl"}, ids
    code, out = _catalog(cfg, {"q": "spotify"})
    assert code == 200
    assert {e["id"] for e in out["entries"]} == {"spotify"}


def test_catalog_detail_reports_validation_and_dependencies(tmp_path,
                                                            monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET",
                            "/api/dashboard/plugins/catalog/security-guidance",
                            {}, {})
    assert code == 200 and out["ok"] is True
    plugin = out["plugin"]
    assert plugin["validation"]["ok"] is True
    assert plugin["validation"]["checks"]
    assert plugin["dependencies"]["ok"] is True
    assert plugin["configuration"]["config_surface"] in ("env", "none")
    # Unknown id -> typed 404, never a raw class name.
    code, out = HC.dispatch(cfg, "GET",
                            "/api/dashboard/plugins/catalog/zzz-nope", {}, {})
    assert code == 404
    assert out["code"] == "PLUGIN_NOT_FOUND"


def test_hub_surfaces_the_real_fleet_and_providers(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/plugins/hub", {}, {})
    assert code == 200 and out["ok"] is True
    names = {r["name"] for r in out["plugins"]}
    # A real bundled plugin with the reference activation verdict, not the
    # single permission-store record the old hub showed.
    assert "browser-browser-use" in names
    assert len(names) >= 50, len(names)
    orphans = {o["name"] for o in out["orphan_dashboard_plugins"]}
    assert {"kanban", "qubit-achievements"} <= orphans
    assert {"qubit-boards", "qubit-ops"} <= orphans
    # Real vendored memory providers (not a hardcoded single option).
    assert len(out["providers"]["memory_options"]) >= 5


def test_enable_disable_toggles_a_real_vendored_plugin(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/security-guidance/enable",
        {}, {"confirm": True})
    assert code == 200 and out["ok"] is True
    assert out["runtime"] == "vendored"
    # The status is observable through the catalog (real config write).
    _code, cat = _catalog(cfg)
    row = next(e for e in cat["entries"] if e["id"] == "security-guidance")
    assert row["runtime_status"] == "enabled"
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/security-guidance/disable",
        {}, {"confirm": True})
    assert code == 200 and out["ok"] is True


def test_unknown_plugin_toggle_is_typed_not_a_class_name(tmp_path,
                                                         monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/zzz-nope/enable", {},
        {"confirm": True})
    assert code == 404
    assert out["code"] == "PLUGIN_NOT_FOUND"
    assert out["code"] != "PermissionError"


def test_reload_requires_confirmation_and_reports_real_counts(tmp_path,
                                                              monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/dashboard/plugins/reload",
                            {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED"
    code, out = HC.dispatch(cfg, "POST", "/api/dashboard/plugins/reload",
                            {}, {"confirm": True})
    assert code == 200 and out["ok"] is True
    assert "plugin_count" in out and "catalog_count" in out
    assert out["reloaded"] is True
