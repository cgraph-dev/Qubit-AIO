"""Hermes dashboard parity — safe read-only API block (fix10).

Covers the three surfaces inspected in this lane:

* ``GET /api/desktop-plugins/{root,list,read}`` — served from the host's real
  ``<QUBIT_HOME>/desktop-plugins`` tree through the vendored module's own
  audited containment/read helpers. The Qubit dashboard surface is read-only,
  so ``POST /api/desktop-plugins/reconcile`` is a typed refusal.
* ``GET /api/plugins/kanban/workflow`` — the vendored plugin's one missing
  read; served from ``kanban_workflow.DEFAULT_WORKFLOW`` (the same data the
  vendored route returns) instead of a bare no-compat-route 404.
* ``GET /api/hermes/update/check`` — deliberately NOT aliased (owner ruling:
  no publicly reachable legacy-branded update route). The canonical
  ``/api/qubit/update/check`` remains the real, read-only 200 probe.

All tests are hermetic: a throwaway ``QUBIT_HOME``, no network, no live service.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: test-token\n")
    return C.Config.load(p)


def _install(home: Path, name: str, source: str, marker: str | None = None) -> Path:
    folder = home / "desktop-plugins" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.js").write_text(source, encoding="utf-8")
    if marker is not None:
        (folder / ".hermes-package.json").write_text(marker, encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Route table / authority
# ---------------------------------------------------------------------------
def test_desktop_plugin_reads_are_registered_and_quarantined():
    from voicebridge import qubit_compat as HC
    from voicebridge import webui

    for path, handler in (
            ("/api/desktop-plugins/root", "h_desktop_plugins_root"),
            ("/api/desktop-plugins/list", "h_desktop_plugins_list"),
            ("/api/desktop-plugins/read", "h_desktop_plugins_read")):
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["disposition"] == "implemented", (path, route)
        assert route["handler"] == handler, (path, route)
        assert callable(HC.HANDLERS.get(handler)), handler
        # The dashboard must never fork-proxy these: the fork's dashboard
        # surface answers 404, and the Qubit handlers are the authority.
        assert webui.qubit_read_authority(path), path

    reconcile, _ = HC.match("POST", "/api/desktop-plugins/reconcile")
    assert reconcile is not None
    assert reconcile["disposition"] == "gated"
    assert reconcile["gate"]["code"] == "DESKTOP_PLUGINS_RECONCILE_UNAVAILABLE"


def test_kanban_workflow_read_is_registered():
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("GET", "/api/plugins/kanban/workflow")
    assert route is not None
    assert route["disposition"] == "implemented"
    assert route["handler"] == "h_plugin_kanban_workflow"
    assert callable(HC.HANDLERS.get("h_plugin_kanban_workflow"))


def test_legacy_hermes_update_alias_stays_refused():
    from voicebridge import qubit_compat as HC

    # The owner ruling removed the public legacy-branded alias entirely.
    route, _ = HC.match("GET", "/api/hermes/update/check")
    assert route is None
    # The canonical Qubit route is the real probe.
    canonical, _ = HC.match("GET", "/api/qubit/update/check")
    assert canonical is not None and canonical["handler"] == "h_qubit_update_check"


# ---------------------------------------------------------------------------
# Desktop-plugin real reads
# ---------------------------------------------------------------------------
def test_desktop_plugins_root_list_read_real_tree(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    home = Path(os.environ["QUBIT_HOME"])
    folder = _install(home, "clock", "export default { id: 'clock' }",
                      marker='{"package": "clock"}')

    code, root = HC.dispatch(cfg, "GET", "/api/desktop-plugins/root", {}, {})
    assert code == 200, (code, root)
    assert root == {"root": str((home / "desktop-plugins").resolve())}

    code, listed = HC.dispatch(cfg, "GET", "/api/desktop-plugins/list",
                               {"path": root["root"]}, {})
    assert code == 200, (code, listed)
    assert listed == {"entries": [
        {"name": "clock", "path": str(folder.resolve()), "isDirectory": True}]}

    code, files = HC.dispatch(cfg, "GET", "/api/desktop-plugins/list",
                              {"path": str(folder.resolve())}, {})
    assert code == 200, (code, files)
    assert {e["name"] for e in files["entries"]} == {
        "plugin.js", ".hermes-package.json"}

    code, read = HC.dispatch(cfg, "GET", "/api/desktop-plugins/read",
                             {"path": str(folder.resolve() / "plugin.js")}, {})
    assert code == 200, (code, read)
    assert read["text"] == "export default { id: 'clock' }"
    assert read["truncated"] is False and read["byteSize"] > 0


def test_desktop_plugins_read_refuses_anything_but_entries_and_markers(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    home = Path(os.environ["QUBIT_HOME"])
    folder = _install(home, "notes", "export default {}")
    (folder / "secrets.txt").write_text("sk-test", encoding="utf-8")
    (folder / "lib").mkdir()
    (folder / "lib" / "plugin.js").write_text("export default {}", encoding="utf-8")
    (home / ".env").write_text("OPENAI_API_KEY=sk-test", encoding="utf-8")

    for path in (
            str(folder.resolve() / "secrets.txt"),
            str(folder.resolve() / "lib" / "plugin.js"),
            str((home / "desktop-plugins").resolve() / "plugin.js"),
            str((home / "desktop-plugins").resolve() / ".." / ".env"),
            str((home / ".env").resolve())):
        code, out = HC.dispatch(cfg, "GET", "/api/desktop-plugins/read",
                                {"path": path}, {})
        assert code == 404, (path, code, out)
        assert out["ok"] is False and out["code"] == "DESKTOP_PLUGINS_NOT_FOUND"
        assert "sk-test" not in str(out)

    code, out = HC.dispatch(cfg, "GET", "/api/desktop-plugins/list",
                            {"path": str(folder.resolve() / "lib")}, {})
    assert code == 404 and out["code"] == "DESKTOP_PLUGINS_NOT_FOUND"


def test_desktop_plugins_missing_path_is_typed(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for path in ("/api/desktop-plugins/list", "/api/desktop-plugins/read"):
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 400, (path, code, out)
        assert out["code"] == "DESKTOP_PLUGINS_BAD_PATH"


def test_desktop_plugins_oversized_source_is_413(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    home = Path(os.environ["QUBIT_HOME"])
    folder = _install(home, "big", "x" * 64)
    # Root/list resolve the vendored module; then shrink its cap to prove the
    # SAME bound is enforced here (not a reimplementation).
    HC.dispatch(cfg, "GET", "/api/desktop-plugins/root", {}, {})
    from qubit_cli.web_routers import desktop_plugins as routes

    monkeypatch.setattr(routes, "PLUGIN_SOURCE_MAX_BYTES", 8)
    code, out = HC.dispatch(cfg, "GET", "/api/desktop-plugins/read",
                            {"path": str(folder.resolve() / "plugin.js")}, {})
    assert code == 413, (code, out)
    assert out["code"] == "DESKTOP_PLUGINS_TOO_LARGE"


def test_desktop_plugins_linked_entry_is_not_followed(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    home = Path(os.environ["QUBIT_HOME"])
    folder = _install(home, "evil", "export default {}")
    (home / ".env").write_text("OPENAI_API_KEY=sk-test", encoding="utf-8")
    try:
        (folder / "plugin.js").unlink()
        (folder / "plugin.js").symlink_to(home / ".env")
    except OSError:
        pytest.skip("symlinks need privileges here")

    code, out = HC.dispatch(cfg, "GET", "/api/desktop-plugins/read",
                            {"path": str(folder.resolve() / "plugin.js")}, {})
    assert code == 404, (code, out)
    assert "sk-test" not in str(out)


def test_desktop_plugins_root_is_read_only(tmp_path, monkeypatch):
    """The root read must not run the Webapp reconcile copy pass."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    home = Path(os.environ["QUBIT_HOME"])
    source = home / "plugins" / "radar" / "desktop"
    source.mkdir(parents=True)
    (source / "plugin.js").write_text("export default { v: 1 }", encoding="utf-8")

    code, out = HC.dispatch(cfg, "GET", "/api/desktop-plugins/root", {}, {})
    assert code == 200, (code, out)
    assert not (home / "desktop-plugins" / "radar").exists(), (
        "the read-only root route must not reconcile package halves")


def test_desktop_plugins_reconcile_is_a_typed_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/desktop-plugins/reconcile", {},
                            {})
    assert code == 403, (code, out)
    assert out["ok"] is False and out["state"] == "UNAVAILABLE"
    assert out["code"] == "DESKTOP_PLUGINS_RECONCILE_UNAVAILABLE"
    assert out["qubit_counterpart"]


# ---------------------------------------------------------------------------
# Kanban workflow real data
# ---------------------------------------------------------------------------
def test_kanban_workflow_serves_the_runtime_default(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from qubit_cli import kanban_workflow

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/plugins/kanban/workflow", {}, {})
    assert code == 200, (code, out)
    assert out == kanban_workflow.DEFAULT_WORKFLOW.to_dict()
    assert [c["key"] for c in out["columns"]][:2] == ["triage", "todo"]
    assert out["archived"] == "archived"
    assert set(out["manual"]) >= {"triage", "todo", "done", "archived"}


# ---------------------------------------------------------------------------
# HTTP boundary + auth
# ---------------------------------------------------------------------------
def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _get(port, path, token=None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    if token:
        req.add_header("X-Qubit-Session-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning")
def test_desktop_plugins_root_over_http_requires_the_token(tmp_path, monkeypatch):
    # The WebUI startup path leaves a temp-file closer for the GC on this host;
    # the repo's ``filterwarnings = error`` turns that environment noise into a
    # failure. The assertions below are the product contract.
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    Path(os.environ["QUBIT_HOME"]).mkdir(parents=True, exist_ok=True)
    ui, port = _start(tmp_path)
    try:
        status, _ = _get(port, "/agent/api/desktop-plugins/root")
        assert status in (401, 403), status
        status, body = _get(port, "/agent/api/desktop-plugins/root",
                            token="test-token")
        assert status == 200, (status, body[:200])
        import json

        payload = json.loads(body)
        assert payload["root"].endswith("desktop-plugins"), payload
    finally:
        ui.stop()
