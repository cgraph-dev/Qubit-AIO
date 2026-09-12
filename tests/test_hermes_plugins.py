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


def _seed_sessions(cfg, monkeypatch, count):
    """Seed ``count`` real SessionDB sessions under the Qubit HERMES_HOME."""
    from voicebridge import hermes_runtime

    home = _hermes_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    hermes_runtime.ensure_path()
    from hermes_state import SessionDB

    ids = []
    db = SessionDB()
    try:
        for i in range(count):
            sid = f"sess-{i}"
            db.create_session(sid, "cli", model="ollama/llama3",
                              display_name=f"Session {i}")
            db.append_message(sid, "user",
                              content="permission denied and an error")
            ids.append(sid)
    finally:
        db.close()
    return ids


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


# Every pinned kanban write route (plugin_api.py) must resolve to the shared
# typed confirmation/broker gate — never a fabricated 2xx and never a 404.
KANBAN_WRITE_ROUTES = [
    ("POST", "/api/plugins/kanban/tasks"),
    ("POST", "/api/plugins/kanban/tasks/bulk"),
    ("PATCH", "/api/plugins/kanban/tasks/task-1"),
    ("DELETE", "/api/plugins/kanban/tasks/task-1"),
    ("POST", "/api/plugins/kanban/tasks/task-1/comments"),
    ("POST", "/api/plugins/kanban/links"),
    ("DELETE", "/api/plugins/kanban/links"),
    ("POST", "/api/plugins/kanban/tasks/task-1/attachments"),
    ("DELETE", "/api/plugins/kanban/attachments/5"),
    ("POST", "/api/plugins/kanban/tasks/task-1/reclaim"),
    ("POST", "/api/plugins/kanban/tasks/task-1/reassign"),
    ("POST", "/api/plugins/kanban/tasks/task-1/specify"),
    ("POST", "/api/plugins/kanban/tasks/task-1/decompose"),
    ("POST", "/api/plugins/kanban/estimate"),
    ("POST", "/api/plugins/kanban/tasks/task-1/estimate"),
    ("POST", "/api/plugins/kanban/tasks/task-1/home-subscribe/telegram"),
    ("DELETE", "/api/plugins/kanban/tasks/task-1/home-subscribe/telegram"),
    ("POST", "/api/plugins/kanban/dispatch"),
    ("POST", "/api/plugins/kanban/boards"),
    ("PATCH", "/api/plugins/kanban/boards/board-1"),
    ("DELETE", "/api/plugins/kanban/boards/board-1"),
    ("POST", "/api/plugins/kanban/boards/board-1/export"),
    ("POST", "/api/plugins/kanban/boards/import"),
    ("POST", "/api/plugins/kanban/boards/board-1/switch"),
    ("PATCH", "/api/plugins/kanban/profiles/profile-1"),
    ("POST", "/api/plugins/kanban/profiles/profile-1/describe-auto"),
    ("POST", "/api/plugins/kanban/runs/5/terminate"),
]


def test_kanban_writes_stay_on_policy_gate(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path in KANBAN_WRITE_ROUTES:
        code, out = HC.dispatch(cfg, method, path, {}, {"title": "gated"})
        assert code == 403, (method, path, code, out)
        assert out["code"] == "KANBAN_WRITE_BROKERED", (method, path, out)
        assert out["qubit_counterpart"], (method, path, out)
        assert out["ok"] is False, (method, path, out)


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


# ---------------------------------------------------------------------------
# Achievements: full state machine, lifecycle, bounds, typed failures
# ---------------------------------------------------------------------------
REQUIRED_ACH_KEYS = {
    "id", "name", "description", "category", "kind", "icon", "unlocked",
    "discovered", "state", "tier", "progress", "next_tier", "next_threshold",
    "progress_pct", "criteria",
}


def test_achievements_wire_shape_states_and_tier_progression(
        tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    sid = _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    code, _out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert code == 200
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert code == 200, (code, str(out)[:300])

    states = {"unlocked": 0, "discovered": 0, "secret": 0}
    for a in out["achievements"]:
        assert REQUIRED_ACH_KEYS <= set(a), \
            (a.get("id"), sorted(REQUIRED_ACH_KEYS - set(a)))
        assert a["state"] in states, a["state"]
        states[a["state"]] += 1
    assert out["unlocked_count"] == states["unlocked"]
    assert out["discovered_count"] == states["discovered"]
    assert out["secret_count"] == states["secret"]
    assert sum(states.values()) == out["total_count"] >= 60

    by_id = {a["id"]: a for a in out["achievements"]}
    pilgrim = by_id["open_weights_pilgrim"]
    assert pilgrim["unlocked"] is True and pilgrim["state"] == "unlocked"
    assert pilgrim["tier"] == "Copper"
    assert pilgrim["next_tier"] == "Silver" and pilgrim["next_threshold"] == 3
    assert pilgrim["progress"] == 1 and 0 <= pilgrim["progress_pct"] < 100
    assert pilgrim["unlocked_at"], pilgrim

    # A secret badge with no matching signal is masked; one with progress is
    # revealed as discovered — never fabricated.
    masked = by_id["forgot_the_env_var"]
    assert masked["state"] == "secret" and masked["name"] == "???"
    assert masked["unlocked"] is False
    discovered = by_id["permission_denied_any_percent"]
    assert discovered["state"] == "discovered"
    assert discovered["unlocked"] is False and discovered["name"] != "???"
    assert discovered["progress"] >= 1


def test_achievements_scan_status_lifecycle(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()

    code, before = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/scan-status", {}, {})
    assert code == 200 and before["state"] == "idle", before
    assert before["ttl_seconds"] == 120, before
    run0 = before["run_count"]

    code, _out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert code == 200
    code, after = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/scan-status", {}, {})
    assert code == 200, (code, after)
    assert after["run_count"] == run0 + 1, after
    assert after["state"] == "idle", after
    assert after["started_at"] and after["finished_at"], after
    assert after["last_duration_ms"] is not None, after
    assert after["snapshot_generated_at"], after
    assert after["snapshot_stale"] is False, after
    assert after["snapshot_age_seconds"] >= 0, after


def test_achievements_recent_unlocks_sorted_and_capped(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    HC.dispatch(cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})

    code, recent = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/recent-unlocks", {}, {})
    assert code == 200 and isinstance(recent, list), (code, recent)
    assert 0 < len(recent) <= 20, recent
    assert all(a["unlocked"] for a in recent), recent
    times = [a.get("unlocked_at") or 0 for a in recent]
    assert times == sorted(times, reverse=True), times


def test_achievements_session_badges_real_and_unknown(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    sid = _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    HC.dispatch(cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})

    code, badges = HC.dispatch(
        cfg, "GET",
        f"/api/plugins/hermes-achievements/sessions/{sid}/badges", {}, {})
    assert code == 200 and badges["session_id"] == sid, (code, badges)
    assert any(b["id"] == "open_weights_pilgrim" for b in badges["badges"]), \
        badges

    code, empty = HC.dispatch(
        cfg, "GET",
        "/api/plugins/hermes-achievements/sessions/nope/badges", {}, {})
    assert code == 200 and empty == {"session_id": "nope", "badges": []}, empty


def test_achievements_scan_is_bounded(tmp_path, monkeypatch):
    """QUBIT_ACHIEVEMENTS_SCAN_LIMIT caps a scan even though the vendored engine
    defaults to the full history (SQLite ``LIMIT -1``)."""
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg, monkeypatch, 3)
    monkeypatch.setenv("QUBIT_ACHIEVEMENTS_SCAN_LIMIT", "2")
    HP.reset_plugin_caches()
    code, out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert code == 200, (code, str(out)[:300])
    assert out["scan_meta"]["sessions_total"] == 2, out["scan_meta"]


def test_achievements_mutations_refuse_under_estop(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    monkeypatch.setattr(HP, "_estop", lambda _cfg: True)

    code, out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert code == 403 and out["code"] == "ESTOP_ENGAGED", (code, out)
    code, out = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/reset-state", {},
        {"confirm": True})
    assert code == 403 and out["code"] == "ESTOP_ENGAGED", (code, out)


def test_plugin_engine_unavailable_is_typed(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HP, "_ENGINES", {})

    def _boom(_name):
        raise HP.PluginUnavailable("engine exploded")

    monkeypatch.setattr(HP, "_load_engine", _boom)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert code == 503 and out["code"] == "PLUGIN_ENGINE_UNAVAILABLE", \
        (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/plugins/kanban/board", {}, {})
    assert code == 503 and out["code"] == "PLUGIN_ENGINE_UNAVAILABLE", \
        (code, out)


# ---------------------------------------------------------------------------
# Kanban: richer real reads + typed errors
# ---------------------------------------------------------------------------
def test_kanban_task_detail_carries_comments_links_and_runs(
        tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(_hermes_home(cfg)))
    hermes_runtime.ensure_path()
    from hermes_cli import kanban_db, kanban_db_connect as kbc

    kanban_db.init_db()
    with kbc.connect() as conn:
        parent = kanban_db.create_task(conn, title="Parent", created_by="t")
        child = kanban_db.create_task(conn, title="Child", created_by="t")
        kanban_db.link_tasks(conn, parent, child)
        kanban_db.add_comment(conn, parent, author="tester", body="hello")

    code, out = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{parent}", {}, {})
    assert code == 200 and isinstance(out, dict), (code, str(out)[:300])
    for key in ("task", "comments", "events", "attachments", "links",
                "child_results", "runs"):
        assert key in out, (key, sorted(out))
    assert out["task"]["title"] == "Parent"
    assert any(c["body"] == "hello" for c in out["comments"]), out["comments"]
    assert out["links"]["children"] == [child], out["links"]
    assert [c["id"] for c in out["child_results"]] == [child], out["child_results"]


def test_kanban_unknown_board_is_typed_404(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/board", {"board": "no-such-board"}, {})
    assert code == 404 and out["ok"] is False, (code, out)


def test_kanban_home_channels_shape(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/home-channels", {}, {})
    assert code == 200 and isinstance(out.get("home_channels"), list), out


# Pinned auxiliary reads whose compat routes are not registered yet. They must
# never fabricate success: either the route is wired (200) or it is a typed
# NOT_FOUND (404). This keeps the honest open row observable.
KANBAN_AUX_READS = [
    ("GET", "/api/plugins/kanban/tasks/task-1/attachments"),
    ("GET", "/api/plugins/kanban/attachments/5"),
    ("GET", "/api/plugins/kanban/tasks/task-1/log"),
    ("GET", "/api/plugins/kanban/runs/5"),
    ("GET", "/api/plugins/kanban/runs/5/inspect"),
    ("GET", "/api/plugins/kanban/profiles"),
    ("GET", "/api/plugins/kanban/orchestration"),
    ("PUT", "/api/plugins/kanban/orchestration"),
]


def test_kanban_auxiliary_reads_never_fabricate(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path in KANBAN_AUX_READS:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code in (200, 404), (method, path, code, out)
        if code == 404:
            assert out["ok"] is False and out["code"] == "NOT_FOUND", \
                (method, path, out)


# ---------------------------------------------------------------------------
# Fleet audit: every vendored plugin has a disposition; only the two dashboard
# plugins declare a dashboard surface.
# ---------------------------------------------------------------------------
def test_vendored_fleet_audit_covers_every_plugin():
    from voicebridge import hermes_plugins as HP

    expected = (
        "hermes-achievements", "kanban", "browser", "context_engine",
        "cron_providers", "dashboard_auth", "disk-cleanup", "google_meet",
        "image_gen", "memory", "model-providers", "observability", "platforms",
        "security-guidance", "spotify", "teams_pipeline", "video_gen", "web",
    )
    assert HP.VENDORED_PLUGIN_FLEET == expected
    audit = HP.plugin_fleet_audit()
    assert [r["name"] for r in audit] == list(expected)
    dashboard = [r["name"] for r in audit if r["has_dashboard"]]
    assert dashboard == ["hermes-achievements", "kanban"], dashboard
    for row in audit:
        if row["has_dashboard"]:
            assert row["manifest"].endswith("dashboard/manifest.json"), row
            assert row["api"].endswith("dashboard/plugin_api.py"), row
