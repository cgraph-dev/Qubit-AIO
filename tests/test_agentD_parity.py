"""Agent D — real Achievements + Kanban workflow proof (Qubit parity).

Complements ``tests/test_hermes_plugins.py`` (read routing + wire shape) by
exercising the *complete* workflows end to end:

* achievements: the full real catalog really evaluates (>=60 badges, unique
  ids, per-category spread, 5-tier ladders, secret redaction) from a seeded
  ``hermes_state.SessionDB`` — no fabricated badges.
* kanban: the vendored dashboard reads are real; every vendored *write* is
  intentionally stopped by the shared Qubit broker gate
  (``KANBAN_WRITE_BROKERED``), and the counterpart the gate points at
  (``voicebridge.kanban`` + the dashboard ``/api/ops`` console) really performs
  the write.

Hermetic: temp ``XDG_*`` + ``HERMES_HOME``; no network; no live services.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

TIER_NAMES = ["Copper", "Silver", "Gold", "Diamond", "Olympian"]


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
                          content="permission denied and an error")
        db.append_message("sess-local", "assistant", content="working",
                          tool_calls=[{"function": {"name": "terminal"}}])
        db.append_message("sess-local", "tool", tool_name="terminal",
                          content="Error: port 3000 already in use")
    finally:
        db.close()


def test_achievements_full_real_catalog(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import hermes_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()

    # The vendored engine serves a structurally-valid "pending" snapshot while
    # its first scan runs in the background (see plugin_api.evaluate_all), and
    # the dashboard polls /scan-status. Drive the documented synchronous rescan
    # workflow (same as test_hermes_plugins.test_achievements_routes_compute_real_state)
    # so this test deterministically observes the real computed catalog.
    rcode, rout = HC.dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan", {}, {})
    assert rcode == 200, (rcode, str(rout)[:300])

    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert code == 200, (code, str(out)[:300])

    achs = out["achievements"]
    assert len(achs) >= 60, len(achs)
    assert len({a["id"] for a in achs}) == len(achs), "duplicate badge ids"
    categories = {a["category"] for a in achs}
    assert len(categories) >= 5, categories
    # Every tiered badge carries a full, ascending 5-tier ladder.
    for a in achs:
        if a["kind"] == "multi_condition":
            assert a["requirements"], a["id"]
            continue
        assert [t["name"] for t in a["tiers"]] == TIER_NAMES, a["id"]
        thr = [t["threshold"] for t in a["tiers"]]
        assert thr == sorted(thr) and len(set(thr)) == len(thr), a["id"]
        assert 0 <= a["progress_pct"] <= 100, a

    counts = out["unlocked_count"] + out["discovered_count"] + out["secret_count"]
    assert counts == out["total_count"], out
    # Secret badges stay hidden until the first matching signal.
    for a in achs:
        if a.get("secret") and a["state"] == "secret":
            assert a["name"] == "???", a["id"]
            assert a["criteria"], a["id"]
    # Real history drives real progress: local model + permission-denied.
    by_id = {a["id"]: a for a in achs}
    assert by_id["open_weights_pilgrim"]["unlocked"] is True
    assert by_id["permission_denied_any_percent"]["state"] == "discovered"


def test_kanban_vendored_reads_are_real(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import kanban as N

    cfg = _cfg(tmp_path, monkeypatch)
    tid = N.create(cfg, "Agent D board task", requested_by="test",
                   confirmed=True)["task"]["id"]
    for method, path in (
            ("GET", "/api/plugins/kanban/board"),
            ("GET", "/api/plugins/kanban/stats"),
            ("GET", "/api/plugins/kanban/boards"),
            ("GET", "/api/plugins/kanban/assignees"),
            ("GET", "/api/plugins/kanban/config"),
            ("GET", "/api/plugins/kanban/projects"),
            ("GET", "/api/plugins/kanban/workers/active"),
            ("GET", "/api/plugins/kanban/diagnostics"),
            ("GET", "/api/plugins/kanban/profiles"),
            ("GET", "/api/plugins/kanban/orchestration"),
            ("GET", f"/api/plugins/kanban/tasks/{tid}")):
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code == 200, (path, code, str(out)[:200])
    code, board = HC.dispatch(cfg, "GET", "/api/plugins/kanban/board", {}, {})
    titles = [t["title"] for col in board["columns"] for t in col["tasks"]]
    assert "Agent D board task" in titles, titles


def test_kanban_write_gate_is_real_and_counterpart_writes(tmp_path, monkeypatch):
    """Vendored writes are refused by the shared broker gate; the counterpart
    the gate names (``voicebridge.kanban`` / the /api/ops console) performs the
    same write for real against the Qubit board store."""
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path in (
            ("POST", "/api/plugins/kanban/tasks"),
            ("PATCH", "/api/plugins/kanban/tasks/1"),
            ("POST", "/api/plugins/kanban/tasks/1/comments"),
            ("POST", "/api/plugins/kanban/boards"),
            ("PUT", "/api/plugins/kanban/orchestration")):
        code, out = HC.dispatch(cfg, method, path, {}, {"title": "x"})
        assert code == 403, (method, path, code)
        assert out["code"] == "KANBAN_WRITE_BROKERED", (method, path, out)
        assert out["qubit_counterpart"], out

    # The named counterpart really writes: broker stages then executes.
    code, staged = HC.dispatch(
        cfg, "POST", "/api/ops/run", {},
        {"id": "kanban_create", "args": {"title": "Agent D broker", "board": "main"}})
    assert code == 409, (code, staged)
    cid = staged["confirmation_id"]
    code, done = HC.dispatch(
        cfg, "POST", "/api/ops/confirm", {}, {"confirmation_id": cid})
    assert code == 200 and done["ok"] is True, (code, done)

    code, listed = HC.dispatch(
        cfg, "POST", "/api/ops/run", {},
        {"id": "kanban_list", "args": {"board": "main"}})
    titles = [t["title"] for t in listed["result"]["tasks"]]
    assert "Agent D broker" in titles, titles
