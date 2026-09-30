"""Qubit curated, pinned remote plugin index (Owner Decision #1, 2026-09-23;
install policy amended by owner: fetch curated entries at the pin).

Proves, through the production compat dispatcher and the catalog module:

* ``GET /api/dashboard/plugins/catalog`` serves a curated, pinned index
  alongside the real local loadable catalog, clearly marked ``curated`` +
  ``pinned``;
* every pinned entry is a real remote source with a full 40-character commit
  SHA, a license field, and provenance — and every ``pinned_ref`` matches the
  vendored source index ``qubit_runtime/qubit_cli/data/plugin_index.json``
  (no fabricated entries);
* ``GET /api/dashboard/plugins/hub`` surfaces the same ``catalog_index``;
* the install path is pinned/verified and routed through the canonical
  e-stop -> confirmation -> audit gate, then fetched at exactly the pinned
  SHA through the vendored catalog pipeline (kill-list, exact-SHA clone,
  pre-publish scan, admission-gated enable): no confirm is a typed 409, an
  off-pin ``ref`` is a typed mismatch, an unknown name is a typed 404, and
  the fetch core receives the curated pin;
* raw, unvetted (non-index) remote fetch stays a typed ``BLOCKED_REMOTE_PLUGIN``
  refusal.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _catalog(cfg, query=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "GET", "/api/dashboard/plugins/catalog",
                       dict(query or {}), {})


def _source_index() -> dict:
    path = REPO_ROOT / "qubit_runtime" / "qubit_cli" / "data" / "plugin_index.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_pinned_index_route_is_registered():
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("GET", "/api/dashboard/plugins/catalog")
    assert route is not None and route["handler"] == "plugins_catalog"
    install, _ = HC.match("POST", "/api/dashboard/agent-plugins/install")
    assert install is not None and install["handler"] == "h_agent_plugin_install"


def test_catalog_serves_curated_pinned_index(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg)
    assert code == 200 and out["ok"] is True
    idx = out["pinned_index"]
    assert idx["state"] == "CURATED_PINNED"
    assert idx["curated"] is True and idx["pinned"] is True
    assert idx["schema"] == "qubit-pinned-plugin-index/1"
    assert idx["count"] == len(idx["entries"]) >= 1
    assert idx["license_note"]
    assert idx["install"]["code"] == "PLUGIN_INDEX_INSTALLABLE"
    assert idx["install"]["state"] == "READY"
    for entry in idx["entries"]:
        assert entry["name"]
        # B4-06: the served display row is de-branded; the real upstream source
        # lives only in the internal ``provenance`` sub-field.
        assert "nousresearch" not in json.dumps(
            {k: v for k, v in entry.items() if k != "provenance"}).lower()
        assert entry["provenance"]["upstream_repo"].startswith("https://")
        assert _SHA40.match(entry["pinned_ref"]), entry
        assert entry["license"] == "unverified"
        assert entry["pinned"] is True
        if entry.get("curated"):
            assert entry["provenance"]["curation"] == "curated+pinned"
        else:
            assert entry["review"] == "unreviewed"
            assert entry["provenance"]["curation"] == "upstream-unreviewed"


def test_pinned_refs_match_the_vendored_source_index(tmp_path, monkeypatch):
    """Every served pin is the real pin from the vendored source index — no
    fabricated entries, no invented SHAs."""
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg)
    assert code == 200
    # B4-06: served ids are de-branded; the provenance source_name is the link
    # back to the vendored source index.
    served = {e["provenance"]["source_name"]: e["pinned_ref"]
              for e in out["pinned_index"]["entries"]}
    assert served, "pinned index must not be empty"
    source_path = (REPO_ROOT / "qubit_runtime" / "qubit_cli" / "data"
                   / "plugin_index.json")
    if not source_path.exists():
        pytest.skip("vendored plugin_index.json absent (vendored .gitignore "
                    "excludes data/; packaging owns restoring it)")
    source = {p["name"]: p["ref"] for p in _source_index()["plugins"]}
    for name, ref in served.items():
        assert name in source, f"{name!r} is not in the vendored source index"
        assert ref == source[name], f"{name}: served pin {ref} != source {source[name]}"


def test_hub_surfaces_the_catalog_index(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/plugins/hub", {}, {})
    assert code == 200 and out["ok"] is True
    idx = out["catalog_index"]
    assert idx["state"] == "CURATED_PINNED" and idx["count"] >= 1


def test_raw_remote_fetch_stays_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg)
    assert code == 200
    remote = out["remote"]
    assert remote["state"] == "REFUSED"
    assert remote["code"] == "BLOCKED_REMOTE_PLUGIN"
    assert remote["prerequisite"]
    # The index block itself keeps the raw-fetch refusal explicit.
    assert out["pinned_index"]["raw_remote_fetch"]["code"] == "BLOCKED_REMOTE_PLUGIN"


def test_pinned_index_search_filters_real_entries(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _catalog(cfg, {"q": "telegram"})
    assert code == 200
    names = {e["name"] for e in out["pinned_index"]["entries"]}
    assert "qubit-telegram-business" in names, names
    code, out = _catalog(cfg, {"q": "zzz-no-match"})
    assert code == 200
    assert out["pinned_index"]["entries"] == []


def test_install_requires_confirmation_then_fetches_at_pin(tmp_path,
                                                             monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # No confirm -> canonical 409 (nothing fetched).
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/install", {},
        {"catalog_name": "qubit-media-studio"})
    assert code == 409
    assert out["code"] == "PLUGIN_CONFIRM_REQUIRED"
    # Confirmed install goes to the vendored fetch core at the curated pin —
    # hermetic here by stubbing the network boundary (the core), while the
    # pin resolution, gate, and enable wiring stay real.
    from voicebridge import plugin_catalog as PC

    PC._prepare_runtime(cfg)
    from qubit_cli import plugins_cmd_catalog as _VCC
    calls = {}

    def _fake_install(entry, **kw):
        calls["repo"] = entry.repo
        calls["sha"] = entry.sha
        calls["ref"] = kw.get("ref")
        return ("/tmp/fake-target", {"name": "fake-from-index"}, "fake-from-index")

    monkeypatch.setattr(_VCC, "install_catalog_entry", _fake_install)
    from qubit_cli import plugins_cmd as _PCM
    monkeypatch.setattr(_PCM, "_set_plugin_enabled",
                        lambda *a, **k: calls.setdefault("enabled", True))
    import qubit_cli.plugins_activation as _PA
    monkeypatch.setattr(_PA, "activate_plugin_now", lambda *a, **k: {})
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/install", {},
        {"catalog_name": "plugin-llm-example", "confirm": True})
    assert code == 200, out
    assert out["ok"] is True and out["state"] == "INSTALLED", out
    assert out["revision"] == out["pinned_ref"]
    assert calls["sha"] == out["pinned_ref"]
    assert calls.get("enabled") is True


def test_install_off_pin_ref_is_a_typed_mismatch(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/install", {},
        {"catalog_name": "qubit-media-studio", "ref": "0" * 40, "confirm": True})
    assert code == 403
    assert out["code"] == "PLUGIN_INDEX_REF_MISMATCH"


def test_install_unknown_catalog_name_is_typed_not_found(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/install", {},
        {"catalog_name": "zzz-not-in-index", "confirm": True})
    assert code == 404
    assert out["code"] == "PLUGIN_INDEX_NOT_FOUND"


def test_install_from_index_refuses_while_estop_latched(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        code, out = HC.dispatch(
            cfg, "POST", "/api/dashboard/agent-plugins/install", {},
            {"catalog_name": "qubit-media-studio", "confirm": True})
        assert code == 403
        assert out["code"] == "PLUGIN_ESTOP"
    finally:
        power_control_store(cfg).set(emergency_stop=False,
                                     global_disable=False, issued_by="cli:user")


def test_install_from_index_module_audits_and_fetches_at_pin(tmp_path,
                                                              monkeypatch):
    from voicebridge import plugin_catalog as PC

    cfg = _cfg(tmp_path, monkeypatch)
    PC._prepare_runtime(cfg)
    from qubit_cli import plugins_cmd_catalog as _VCC
    calls = {}

    def _fake_install(entry, **kw):
        calls["name"] = entry.name
        calls["repo"] = entry.repo
        calls["sha"] = entry.sha
        return ("/tmp/fake-target", {"name": "fake-from-index"}, "fake-from-index")

    monkeypatch.setattr(_VCC, "install_catalog_entry", _fake_install)
    from qubit_cli import plugins_cmd as _PCM
    monkeypatch.setattr(_PCM, "_set_plugin_enabled", lambda *a, **k: None)
    import qubit_cli.plugins_activation as _PA
    monkeypatch.setattr(_PA, "activate_plugin_now", lambda *a, **k: {})
    out = PC.install_from_index(cfg, "qubit-telegram-business", confirmed=True,
                                requested_by="test")
    assert out["ok"] is True and out["state"] == "INSTALLED", out
    assert out["pinned_ref"] == "e905f3bc5eeaa5a9dab9bc5155601b3ebec75757"
    assert out["revision"] == out["pinned_ref"]
    # The core was asked for the curated upstream repo at exactly the pin.
    assert calls["sha"] == out["pinned_ref"]
    assert "hermes-telegram-business" in calls["repo"]
    # Nothing reaches the network in this test: the fetch core is stubbed,
    # and no plugin tree was published into the store.
    plugins_dir = Path(cfg.state_dir()) / "plugins"
    assert not plugins_dir.exists() or not any(plugins_dir.iterdir())


def test_upstream_catalog_rows_are_installable_unreviewed(tmp_path, monkeypatch):
    from voicebridge import plugin_catalog as PC

    cfg = _cfg(tmp_path, monkeypatch)
    rows = PC.pinned_index_entries()
    upstream = [r for r in rows if not r.get("curated")]
    assert len(upstream) >= 290, len(upstream)
    by_name = {r["name"]: r for r in rows}
    adspirer = by_name.get("adspirer")
    assert adspirer is not None and adspirer["pinned_ref"].startswith("4688c38")
    assert adspirer["review"] == "unreviewed"
    # Resolution by upstream name (the Hermes catalog key).
    assert PC._index_entry("adspirer")["pinned_ref"] == adspirer["pinned_ref"]
    # Unreviewed install goes through the same pinned pipeline (core stubbed;
    # pin resolution + warning are real).
    PC._prepare_runtime(cfg)
    from qubit_cli import plugins_cmd_catalog as _VCC
    calls = {}

    def _fake_install(entry, **kw):
        calls["repo"] = entry.repo
        calls["sha"] = entry.sha
        calls["ref"] = kw.get("ref")
        return ("/tmp/fake-target", {"name": "fake-upstream"}, "fake-upstream")

    monkeypatch.setattr(_VCC, "install_catalog_entry", _fake_install)
    from qubit_cli import plugins_cmd as _PCM
    monkeypatch.setattr(_PCM, "_set_plugin_enabled", lambda *a, **k: None)
    import qubit_cli.plugins_activation as _PA
    monkeypatch.setattr(_PA, "activate_plugin_now", lambda *a, **k: {})
    out = PC.install_from_index(cfg, "adspirer", confirmed=True,
                                requested_by="test")
    assert out["ok"] is True and out["state"] == "INSTALLED", out
    assert out["review"] == "unreviewed" and out["warning"], out
    assert calls["sha"] == out["pinned_ref"]


def test_subdir_missing_install_teaches_the_catalog(tmp_path, monkeypatch):
    from voicebridge import plugin_catalog as PC

    cfg = _cfg(tmp_path, monkeypatch)
    PC._prepare_runtime(cfg)
    from qubit_cli import plugins_cmd_catalog as _VCC
    from qubit_cli.plugins_cmd import PluginOperationError

    def _fake_missing(entry, **kw):
        raise PluginOperationError(
            "Plugin subdirectory 'plugins/qubit/markifact' does not exist "
            "in the repository.")

    monkeypatch.setattr(_VCC, "install_catalog_entry", _fake_missing)
    import pytest as _pytest
    with _pytest.raises(Exception) as excinfo:
        PC.install_from_index(cfg, "markifact", confirmed=True,
                              requested_by="test")
    assert "does not exist in the repository" in str(excinfo.value)
    rows = PC.pinned_index_entries(
        known_bad=PC._known_bad_map(cfg))
    row = next(r for r in rows if r["name"] == "markifact")
    assert row["install_state"] == "BROKEN", row
    assert row["install_code"] == "PLUGIN_INDEX_KNOWN_BAD", row
    assert "missing" in row["install_note"]


def test_index_rows_reflect_installed_state(tmp_path, monkeypatch):
    from voicebridge import plugin_catalog as PC

    cfg = _cfg(tmp_path, monkeypatch)
    pin = next(r["pinned_ref"] for r in PC.pinned_index_entries()
               if r["name"] == "agent-log")
    monkeypatch.setattr(PC, "_installed_catalog_map", lambda: {
        "agent-log": {"plugin": "agent-log", "revision": pin,
                      "pinned": True}})
    rows = {r["name"]: r for r in PC.pinned_index_entries()}
    row = rows["agent-log"]
    assert row["installed"] is True, row
    assert row["install_state"] == "INSTALLED", row
    assert row["installed_plugin"] == "agent-log", row


def test_reinstall_at_same_pin_is_idempotent_success(tmp_path, monkeypatch):
    from voicebridge import plugin_catalog as PC

    cfg = _cfg(tmp_path, monkeypatch)
    pin = next(r["pinned_ref"] for r in PC.pinned_index_entries()
               if r["name"] == "agent-log")
    monkeypatch.setattr(PC, "_installed_catalog_map", lambda: {
        "agent-log": {"plugin": "agent-log", "revision": pin,
                      "pinned": True}})
    from qubit_cli import plugins_cmd_catalog as _VCC

    def _boom(entry, **kw):
        raise AssertionError("fetch core must not run for same-pin reinstall")

    monkeypatch.setattr(_VCC, "install_catalog_entry", _boom)
    out = PC.install_from_index(cfg, "agent-log", confirmed=True,
                                requested_by="test")
    assert out["ok"] is True and out.get("unchanged") is True, out
    assert out["revision"] == pin
