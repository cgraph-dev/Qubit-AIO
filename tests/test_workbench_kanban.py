"""The workbench kanban family, end to end (real store, real requests).

WHAT THESE TESTS PROVE
---------------------------------------------------------------------------
``docs/WORKBENCH_UI_PORT.md`` listed twelve ``/api/kanban/*`` paths as GAP ("no
kanban REST family at these paths"). Every test below drives
``workbench_ui_routes.h_wb_gateway`` — the SAME entry point the listener calls
for ``/api/wb/api/...`` — against a REAL boards store in a temp home, and then
re-reads that store to check the write actually landed. Nothing here asserts
that a handler "is wired".

The three things worth breaking:

1. **Writes land on the ONE store.** A card created through ``kanban/tasks`` must
   be visible to ``voicebridge.kanban.list_tasks`` — the same call the CLI,
   voice and ``/qubit-boards`` make. A port that wrote to a second database
   would pass a "does it respond" test and fail this one.
2. **Board reads report the store's REAL lanes.** The native store has
   ``open/claimed/blocked/done``. A card must never appear in a lane its status
   is not, and the response must say which vocabulary it is in, because the page
   has upstream's nine lanes baked in.
3. **The refusals are typed, not silent.** ``kanban/links`` cannot be a ``200``
   (the store has no edges), and a real ``kanban/dispatch`` must not spawn worker
   subprocesses behind a dashboard click. Both answer a code that names the
   missing primitive, and neither invents data.

The store is reached through a fresh ``QUBIT_HOME`` per test, with the delegate
fence cleared: pytest run from inside a Kanban dispatcher worker inherits the
``HERMES_DELEGATED_CHILD_CONTEXT`` marker, under which the vendored store opens
read-only and every write refuses — the module's correct behaviour, and not what
these tests are about.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import workbench_ui_routes as R


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def home(tmp_path, monkeypatch):
    """A real temp home: config file, state dirs, and the kanban store in it."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home" / ".hermes"))
    # Not a dispatcher worker: see the module docstring.
    monkeypatch.delenv("HERMES_DELEGATED_CHILD_CONTEXT", raising=False)
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    from voicebridge import config as C

    return C.Config.load(cfg_file)


@pytest.fixture
def store(home):
    """The native store module, pointing at this test's home."""
    from voicebridge import kanban as N

    return N


def call(home, method, sub, body=None, query=None):
    """One workbench API request, exactly as the listener dispatches it."""
    return R.h_wb_gateway(home, dict(query or {}), dict(body or {}),
                          {"sub": sub, "method": method})


def titles(board_payload):
    return [t["title"] for col in board_payload.get("columns") or []
            for t in col["tasks"]]


def lane_of(board_payload, title):
    for column in board_payload.get("columns") or []:
        for task in column["tasks"]:
            if task["title"] == title:
                return column["name"]
    return None


# ------------------------------------------------------------- kanban/board
def test_board_reads_the_native_store_and_names_its_lanes(home, store):
    """A card created on the native store appears on the board, in ITS lane."""
    made = store.create(home, "from-the-cli", board="main", confirmed=True)
    task_id = made["task"]["id"]

    status, out = call(home, "GET", "kanban/board")
    assert status == 200, out
    assert out["ok"] is True
    assert "from-the-cli" in titles(out)
    assert lane_of(out, "from-the-cli") == "open"
    # The vocabulary is stated, not implied: the page paints upstream's nine
    # lanes by default and would mis-file every card under them.
    assert out["native_statuses"] is True
    assert out["upstream_columns"] == ["triage", "todo", "scheduled", "ready",
                                       "running", "blocked", "review", "done"]
    assert [c["name"] for c in out["columns"]] == ["open", "claimed", "blocked", "done"]
    assert out["read_only"] is False

    # A move is a move on the board: same store, next read.
    store.move_task(home, task_id, status="done", confirmed=True)
    status, after = call(home, "GET", "kanban/board")
    assert status == 200, after
    assert lane_of(after, "from-the-cli") == "done"


def test_board_filters_by_assignee_and_hides_archived(home, store):
    """The page's `assignee` filter and the default archive rule both apply."""
    store.create(home, "mine", board="main", assignee="alice", confirmed=True)
    store.create(home, "theirs", board="main", assignee="bob", confirmed=True)
    gone = store.create(home, "archived-one", board="main", confirmed=True)
    store.archive_task(home, gone["task"]["id"], confirmed=True)

    status, out = call(home, "GET", "kanban/board", query={"assignee": "alice"})
    assert status == 200, out
    assert titles(out) == ["mine"]
    # The picker offers EVERY assignee on the board (filtering it down to the
    # one already selected would leave no way back), while the cards are filtered.
    assert out["assignees"] == ["alice", "bob"]

    status, all_of_them = call(home, "GET", "kanban/board")
    assert status == 200, all_of_them
    assert "archived-one" not in titles(all_of_them)


def test_board_answers_a_fresh_store_as_empty_not_missing(home):
    """Nothing has been created yet: an empty board, never a 404/500 stack."""
    status, out = call(home, "GET", "kanban/board")
    assert status == 200, out
    assert titles(out) == []
    assert out["latest_event_id"] == 0


# ------------------------------------------------------------- kanban/tasks
def test_create_task_lands_in_the_store_the_cli_reads(home, store):
    """`POST kanban/tasks` → the row the CLI's own `kanban list` returns."""
    status, out = call(home, "POST", "kanban/tasks", {"title": "port the board"})
    assert status == 200, out
    assert out["task"]["title"] == "port the board"

    listed = store.list_tasks(home, board="main")
    assert [t["title"] for t in listed["tasks"]] == ["port the board"]
    # And the board read agrees with the store it wrote to.
    status, board = call(home, "GET", "kanban/board")
    assert titles(board) == ["port the board"]


def test_create_task_honours_body_assignee_priority_and_status(home, store):
    """The modal's full body: body/assignee/priority land, `status` transitions."""
    status, out = call(home, "POST", "kanban/tasks",
                       {"title": "worked", "body": "the spec", "assignee": "alice",
                        "priority": 2, "status": "done"})
    assert status == 200, out
    row = store.show(home, int(out["task"]["id"]))["task"]
    assert row["body"] == "the spec"
    assert row["assignee"] == "alice"
    assert row["priority"] == "high"
    # `status: done` was applied as a real transition, not echoed back.
    assert row["status"] == "done"


def test_create_task_refuses_a_missing_title_and_a_claimed_start(home, store):
    """A blank title is a 400; `running`/`review` cannot be created from a board."""
    status, out = call(home, "POST", "kanban/tasks", {"title": "   "})
    assert status == 400, out
    assert out["code"] == "KANBAN_BAD_INPUT"

    status, out = call(home, "POST", "kanban/tasks",
                       {"title": "claimed-by-itself", "status": "running"})
    assert status == 501, out
    assert out["code"] == "WB_CAPABILITY_UNBOUND"
    assert "qubit kanban claim" in out["qubit_counterpart"]
    # Refused means NOT created — nothing half-written behind the refusal.
    assert store.list_tasks(home, board="main")["tasks"] == []


def test_bulk_update_moves_each_id_and_reports_each_result(home, store):
    """`kanban/tasks/bulk` {ids,status} is per-id and honest about failures."""
    one = store.create(home, "one", board="main", confirmed=True)["task"]["id"]
    two = store.create(home, "two", board="main", confirmed=True)["task"]["id"]

    status, out = call(home, "POST", "kanban/tasks/bulk",
                       {"ids": [str(one), str(two), "999999"], "status": "done"})
    assert status == 200, out
    assert out["updated"] == 2
    assert [r["ok"] for r in out["results"]] == [True, True, False]
    assert out["results"][2]["error"]
    assert {t["status"] for t in store.list_tasks(home, board="main")["tasks"]} \
        == {"done"}


# ------------------------------------------------- boards / stats / assignees
def test_boards_list_and_create_reach_the_native_board_store(home, store):
    """The switcher's list and its create both hit `voicebridge.kanban`."""
    status, listed = call(home, "GET", "kanban/boards")
    assert status == 200, listed
    assert [b["slug"] for b in listed["boards"]] == ["main"]
    assert listed["current"] == "main"

    status, made = call(home, "POST", "kanban/boards",
                        {"slug": "release", "name": "Release"})
    assert status == 200, made
    assert made["board"]["slug"] == "release"
    assert [b["board"] for b in store.boards_list(home)["boards"]] == \
        ["main", "release"]

    status, listed = call(home, "GET", "kanban/boards")
    assert [b["slug"] for b in listed["boards"]] == ["main", "release"]


def test_board_rename_and_archive_are_the_store_own_writes(home, store):
    """PATCH renames the row; DELETE archives the board's tasks, never `main`."""
    call(home, "POST", "kanban/boards", {"slug": "release", "name": "Release"})
    task = store.create(home, "ship it", board="release", confirmed=True)

    status, out = call(home, "PATCH", "kanban/boards/release", {"name": "Ship"})
    assert status == 200, out
    assert out["board"]["name"] == "Ship"
    rows = {b["board"]: b for b in store.boards_list(home)["boards"]}
    assert rows["release"]["name"] == "Ship"

    status, out = call(home, "DELETE", "kanban/boards/release")
    assert status == 200, out
    assert out["result"]["archived_tasks"] == 1
    assert store.show(home, int(task["task"]["id"]))["task"]["status"] == "archived"

    status, out = call(home, "DELETE", "kanban/boards/main")
    assert status == 403, out
    assert out["code"] == "WB_READ_ONLY_SURFACE"


def test_board_switch_and_unknown_slug(home, store):
    """`…/switch` names the board; an unknown slug is a 404, not an empty board."""
    call(home, "POST", "kanban/boards", {"slug": "release", "name": "Release"})
    status, out = call(home, "POST", "kanban/boards/release/switch")
    assert status == 200, out
    assert out["current"] == "release"

    status, out = call(home, "GET", "kanban/board", query={"board": "ghost"})
    assert status == 404, out


def test_stats_and_assignees_come_from_the_same_store(home, store):
    """Counts and the picker roster are the store's, not a client-side tally."""
    store.create(home, "a", board="main", assignee="alice", confirmed=True)
    done = store.create(home, "b", board="main", assignee="bob", confirmed=True)
    store.complete(home, done["task"]["id"], confirmed=True)

    status, stats = call(home, "GET", "kanban/stats")
    assert status == 200, stats
    assert stats["by_status"]["open"] == 1
    assert stats["by_status"]["done"] == 1

    status, roster = call(home, "GET", "kanban/assignees")
    assert status == 200, roster
    assert sorted(a["name"] for a in roster["assignees"]) == ["alice", "bob"]


# ----------------------------------------------------------------- config
def test_config_reads_and_writes_the_one_dashboard_kanban_section(home):
    """PATCH persists `dashboard.kanban.lane_by_profile` where the CLI reads it."""
    from qubit_cli.config import get_config_path, read_user_config_raw

    status, before = call(home, "GET", "kanban/config")
    assert status == 200, before
    assert before["lane_by_profile"] is True
    assert before["columns"] == ["open", "claimed", "blocked", "done"]

    status, after = call(home, "PATCH", "kanban/config",
                         {"lane_by_profile": False})
    assert status == 200, after
    assert after["lane_by_profile"] is False

    raw = read_user_config_raw(get_config_path()) or {}
    assert raw["dashboard"]["kanban"]["lane_by_profile"] is False

    # A key this page never writes is a refusal, not a silent drop.
    status, out = call(home, "PATCH", "kanban/config", {"render_markdown": False})
    assert status == 400, out
    assert out["code"] == "KANBAN_BAD_INPUT"


# ----------------------------------------------------------------- events
def test_events_page_the_board_timeline_with_since(home, store):
    """`?since=` returns exactly the delta, and `latest_event_id` is its cursor."""
    store.create(home, "a", board="main", confirmed=True)

    status, first = call(home, "GET", "kanban/events")
    assert status == 200, first
    assert first["latest_event_id"] == first["cursor"] == 1
    assert [e["task_id"] for e in first["events"]] == [1]

    store.create(home, "b", board="main", confirmed=True)
    status, delta = call(home, "GET", "kanban/events",
                         query={"since": first["latest_event_id"]})
    assert status == 200, delta
    assert delta["cursor"] == 2
    assert len(delta["events"]) == 1
    assert delta["events"][0]["id"] == 2
    assert delta["synthetic"] is True


def test_events_stream_is_sse_on_the_json_route_or_declared(home, store):
    """The stream path is claimed as SSE, and its frames carry the page's events."""
    assert R.stream_kind("kanban/events/stream") == "kanban"
    store.create(home, "a", board="main", confirmed=True)
    frames = []
    for frame in R.stream_frames("kanban/events/stream", home, {"board": "main"}):
        frames.append(frame)
        if len(frames) == 2:  # the hello frame, then the first delta
            break
    assert frames[0].startswith("event: hello"), frames[0]
    assert "event: events" in frames[1], frames[1]
    payload = json.loads(frames[1].split("data: ", 1)[1])
    assert payload["cursor"] >= 1
    assert payload["events"][0]["task_id"] == 1

    # Reached through the JSON gateway anyway, it says so instead of hanging.
    status, out = call(home, "GET", "kanban/events/stream")
    assert status == 200, out
    assert out["stream"] == "kanban/events/stream"


# --------------------------------------------------------------- dispatch
def test_dispatch_dry_run_reports_the_real_queue_and_spawns_nothing(home, store):
    """`?dry_run=1` is a real plan: what is queued, what the dispatcher skips."""
    store.create(home, "assigned", board="main", assignee="alice",
                 confirmed=True)
    store.create(home, "unassigned", board="main", confirmed=True)

    status, out = call(home, "POST", "kanban/dispatch",
                       query={"dry_run": "1", "max": "8"})
    assert status == 200, out
    assert out["dry_run"] is True
    assert out["queued"] == 2
    assert [s["task_id"] for s in out["spawned"]] == ["1"]
    assert out["skipped_unassigned"] == ["2"]
    # A dry run changed nothing: both cards are still where they were.
    assert {t["status"] for t in store.list_tasks(home, board="main")["tasks"]} \
        == {"open"}


def test_dispatch_real_run_refuses_instead_of_spawning_workers(home, store):
    """A real tick spawns subprocesses; this listener answers a named refusal."""
    store.create(home, "assigned", board="main", assignee="alice",
                 confirmed=True)
    status, out = call(home, "POST", "kanban/dispatch", query={"max": "8"})
    assert status == 501, out
    assert out["code"] == "WB_CAPABILITY_UNBOUND"
    assert out["dry_run"] is False
    assert out["spawned"] == []
    assert "qubit kanban run" in out["qubit_counterpart"]


# ------------------------------------------------------------------ links
def test_links_refuses_typed_and_delete_is_an_idempotent_no_op(home):
    """No dependency table exists: adding refuses, removing cannot fail."""
    status, out = call(home, "POST", "kanban/links",
                       {"parent_id": "1", "child_id": "2"})
    assert status == 501, out
    assert out["code"] == "WB_CAPABILITY_UNBOUND"
    assert "task_links" in out["detail"]

    status, out = call(home, "POST", "kanban/links/delete",
                       {"parent_id": "1", "child_id": "2"})
    assert status == 200, out
    assert out["removed"] is False


# ------------------------------------------------------- the port table
def test_every_kanban_endpoint_the_page_calls_is_a_port_row():
    """The twelve paths the client builds are ALL answered (never GAP)."""
    for sub in ("kanban/board", "kanban/boards", "kanban/config", "kanban/tasks",
                "kanban/tasks/bulk", "kanban/links", "kanban/links/delete",
                "kanban/assignees", "kanban/stats", "kanban/dispatch",
                "kanban/events", "kanban/events/stream"):
        assert sub in R.PORT_SET, sub
        assert getattr(R, R.PORT_SET[sub], None) is not None, sub
    # A slug-scoped board verb resolves too (it is not a bare PORT_SET key).
    assert R._resolve_handler("kanban/boards/release/switch", "POST") is not None
