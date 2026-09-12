"""Vendored Hermes dashboard plugin fleet — real backend routes + state.

Proves that the pinned ``plugins/hermes-achievements`` and ``plugins/kanban``
dashboard APIs are served by Qubit's real compat backend
(``voicebridge/hermes_plugins.py``), compute from REAL local session history /
the real kanban DB, persist state under the Qubit state dir, and never
fabricate badges. No mock replaces the production caller: the achievement scan
runs the vendored engine over a seeded ``hermes_state.SessionDB`` and the kanban
reads run the vendored plugin functions over a seeded ``kanban_db``.

Hermetic: temp ``XDG_*`` + ``HERMES_HOME``; no network; no live services.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _hermes_home(cfg) -> Path:
    return Path(cfg.state_dir()) / "hermes"


def _seed_history(cfg, monkeypatch):
    """Seed a real SessionDB under the Qubit HERMES_HOME with synthetic history."""
    from voicebridge import hermes_runtime

    home = _hermes_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    hermes_runtime.ensure_path()
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        db.create_session("sess-local", "cli", model="ollama/llama3",
                          display_name="Local model session")
        db.append_message("sess-local", "user",
                          content="hit a permission denied and an error")
        db.append_message("sess-local", "assistant", content="working on it",
                          tool_calls=[{"function": {"name": "terminal"}}])
        db.append_message("sess-local", "tool", tool_name="terminal",
                          content="Error: port 3000 already in use")
    finally:
        db.close()
    return "sess-local"


def _seed_kanban(cfg, monkeypatch):
    from voicebridge import hermes_runtime

    monkeypatch.setenv("HERMES_HOME", str(_hermes_home(cfg)))
    hermes_runtime.ensure_path()
    from hermes_cli import kanban_db, kanban_db_connect as kbc

    kanban_db.init_db()
    with kbc.connect() as conn:
        task_id = kanban_db.create_task(
            conn, title="Plugin parity task", created_by="dashboard")
    return task_id


# ---------------------------------------------------------------------------
# Route registration evidence
# ---------------------------------------------------------------------------
PLUGIN_ROUTES = [
    ("GET", "/api/plugins/hermes-achievements/achievements",
     "h_plugin_achievements"),
    ("GET", "/api/plugins/hermes-achievements/scan-status",
     "h_plugin_achievements_scan_status"),
    ("GET", "/api/plugins/hermes-achievements/recent-unlocks",
     "h_plugin_achievements_recent_unlocks"),
    ("POST", "/api/plugins/hermes-achievements/rescan",
     "h_plugin_achievements_rescan"),
    ("POST", "/api/plugins/hermes-achievements/reset-state",
     "h_plugin_achievements_reset_state"),
    ("GET", "/api/plugins/kanban/board", "h_plugin_kanban_board"),
    ("GET", "/api/plugins/kanban/stats", "h_plugin_kanban_stats"),
    ("GET", "/api/plugins/kanban/assignees", "h_plugin_kanban_assignees"),
    ("GET", "/api/plugins/kanban/boards", "h_plugin_kanban_boards"),
    ("GET", "/api/plugins/kanban/config", "h_plugin_kanban_config"),
    ("GET", "/api/plugins/kanban/workers/active", "h_plugin_kanban_workers"),
    ("GET", "/api/plugins/kanban/diagnostics", "h_plugin_kanban_diagnostics"),
    ("GET", "/api/plugins/kanban/model-options",
     "h_plugin_kanban_model_options"),
    ("GET", "/api/dashboard/plugins/fleet", "h_plugin_fleet"),
]


def test_plugin_routes_resolve_to_registered_handlers():
    from voicebridge import hermes_compat as HC

    for method, path, handler in PLUGIN_ROUTES:
        route, _params = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["handler"] == handler, (method, path, route["handler"])
        assert route["disposition"] == "implemented", (method, path, route)
        assert callable(HC.HANDLERS.get(handler)), (method, path, handler)
    # The parameterized task route resolves with its id captured.
    route, params = HC.match("GET", "/api/plugins/kanban/tasks/abc123")
    assert route is not None and route["handler"] == "h_plugin_kanban_task"
    assert params == {"task_id": "abc123"}


# ---------------------------------------------------------------------------
# hermes-achievements: real computed state from real session history
# ---------------------------------------------------------------------------
def test_achievements_routes_compute_real_state(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    sid = _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()

    code, rescan = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert code == 200 and rescan["ok"] is True, (code, str(rescan)[:300])
    assert rescan["unlocked_count"] >= 1, rescan["unlocked_count"]

    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert code == 200, (code, str(out)[:300])
    assert out["unlocked_count"] >= 1, out
    assert out["discovered_count"] + out["secret_count"] >= 1, out
    assert out["total_count"] >= 60, out

    by_id = {a["id"]: a for a in out["achievements"]}
    # ollama/llama3 metadata -> the local/open-weight badge really unlocked.
    assert by_id["open_weights_pilgrim"]["unlocked"] is True, \
        by_id["open_weights_pilgrim"]
    assert by_id["open_weights_pilgrim"]["tier"] == "Copper"
    # "permission denied" history -> a discovered (non-secret-blind) badge.
    assert by_id["permission_denied_any_percent"]["state"] == "discovered", \
        by_id["permission_denied_any_percent"]

    # scan-status reflects the real scan bookkeeping.
    code, status = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/scan-status", {}, {})
    assert code == 200 and status["run_count"] >= 1, status
    assert status["state"] in ("idle", "running"), status

    # recent-unlocks returns the persisted unlock with its timestamp.
    code, recent = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/recent-unlocks", {}, {})
    assert code == 200 and isinstance(recent, list), (code, recent)
    assert any(a["id"] == "open_weights_pilgrim" for a in recent), recent

    # per-session badges evaluate against the real session stats.
    code, badges = HC.dispatch(
        cfg, "GET",
        f"/api/plugins/hermes-achievements/sessions/{sid}/badges", {}, {})
    assert code == 200 and badges["session_id"] == sid, (code, badges)
    assert any(b["id"] == "open_weights_pilgrim" for b in badges["badges"]), \
        badges


def test_achievements_state_persists_across_reload(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()

    code, _out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert code == 200

    state_root = _hermes_home(cfg) / "plugin-data" / "hermes-achievements"
    snapshot = state_root / "scan_snapshot.json"
    unlock_state = state_root / "state.json"
    checkpoint = state_root / "scan_checkpoint.json"
    assert snapshot.is_file() and unlock_state.is_file(), \
        sorted(p.name for p in state_root.iterdir())
    assert checkpoint.is_file(), sorted(p.name for p in state_root.iterdir())

    persisted = json.loads(unlock_state.read_text(encoding="utf-8"))
    assert "open_weights_pilgrim" in persisted.get("unlocks", {}), persisted

    # Register 0600 on durable state.
    for path in (snapshot, unlock_state, checkpoint):
        assert (path.stat().st_mode & 0o777) == 0o600, (
            path.name, oct(path.stat().st_mode & 0o777))

    # Simulate a process restart: drop in-memory caches, keep disk state.
    HP.reset_plugin_caches()
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert code == 200, (code, str(out)[:300])
    by_id = {a["id"]: a for a in out["achievements"]}
    assert by_id["open_weights_pilgrim"]["unlocked"] is True, \
        by_id["open_weights_pilgrim"]
    assert by_id["open_weights_pilgrim"].get("unlocked_at"), \
        by_id["open_weights_pilgrim"]


def test_achievements_reset_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    HC.dispatch(cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})

    code, out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/reset-state", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)

    code, out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/reset-state", {},
        {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    state_root = _hermes_home(cfg) / "plugin-data" / "hermes-achievements"
    assert not (state_root / "scan_snapshot.json").exists()


# ---------------------------------------------------------------------------
# kanban: real reads from the vendored kanban_db
# ---------------------------------------------------------------------------
def test_kanban_read_routes_serve_real_board(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    task_id = _seed_kanban(cfg, monkeypatch)

    code, board = HC.dispatch(cfg, "GET", "/api/plugins/kanban/board", {}, {})
    assert code == 200 and isinstance(board, dict), (code, str(board)[:300])
    assert "columns" in board and "latest_event_id" in board, board
    titles = [t["title"] for col in board["columns"] for t in col["tasks"]]
    assert "Plugin parity task" in titles, titles

    code, task = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{task_id}", {}, {})
    assert code == 200 and task["task"]["title"] == "Plugin parity task", task

    code, stats = HC.dispatch(cfg, "GET", "/api/plugins/kanban/stats", {}, {})
    assert code == 200 and "by_status" in stats, stats

    for path in ("/api/plugins/kanban/assignees",
                 "/api/plugins/kanban/boards",
                 "/api/plugins/kanban/config",
                 "/api/plugins/kanban/workers/active",
                 "/api/plugins/kanban/diagnostics"):
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200 and isinstance(out, dict), (path, code, out)

    # Missing task is a typed 404 from the vendored plugin, not a fabricated ok.
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/tasks/does-not-exist", {}, {})
    assert code == 404 and out["ok"] is False, (code, out)


def test_kanban_writes_stay_on_policy_gate(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/plugins/kanban/tasks", {},
        {"title": "should be gated"})
    assert code == 403 and out["code"] == "KANBAN_WRITE_BROKERED", (code, out)
    assert out["qubit_counterpart"], out


# ---------------------------------------------------------------------------
# Plugin fleet discovery
# ---------------------------------------------------------------------------
def test_plugin_fleet_discovery_lists_vendored_plugins(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/plugins/fleet", {}, {})
    assert code == 200 and isinstance(out, dict), (code, out)
    names = {m["name"] for m in out["plugins"]}
    assert {"hermes-achievements", "kanban"} <= names, names
    achievements = next(m for m in out["plugins"]
                        if m["name"] == "hermes-achievements")
    assert achievements["entry"] == "dist/index.js", achievements
    assert achievements["tab"]["path"] == "/achievements", achievements
    # The vendored fleet view is additive: the overlay list is unchanged.
    code, overlays = HC.dispatch(cfg, "GET", "/api/dashboard/plugins", {}, {})
    assert code == 200 and isinstance(overlays, list)
    assert {m["name"] for m in overlays}.isdisjoint(
        {"hermes-achievements", "kanban"}), overlays
