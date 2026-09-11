"""Board tabs + rich widgets on the single kanban store (OpenClaw control-ui).

Extends the O-board canvas with ordered board tabs, richer widget content
kinds (note/text/link/html/svg), inert markup trust markers, and
per-board/per-tab role grants that never widen access. Everything runs the
real SQLite store in a hermetic tmp state dir: no mocks.

Backward compatibility is proven explicitly: a legacy database created
with the old canvas schema (no ``tab``/``grants`` columns, no tabs table)
is opened and read through the new code path after an additive migration.
"""
from __future__ import annotations

import sqlite3

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _board(cfg, slug="main"):
    from voicebridge import kanban as KB

    KB.init(cfg)
    KB.boards_create(cfg, slug, slug, requested_by="t", confirmed=True)
    return slug


# ------------------------------------------------------------------- tabs
def test_tabs_create_list_order_rename_delete(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)

    first = KB.tab_create(cfg, "main", "inbox", "Inbox",
                          requested_by="t", confirmed=True)
    assert first["state"] == "OK" and first["tab"] == "inbox"
    KB.tab_create(cfg, "main", "doing", "Doing", requested_by="t",
                  confirmed=True)

    listed = KB.tab_list(cfg, "main")
    assert [t["slug"] for t in listed["tabs"]] == ["inbox", "doing"], listed
    assert listed["virtual_default"] is False
    assert all(t["default"] is False for t in listed["tabs"])

    KB.tab_rename(cfg, "main", "inbox", "Intake", requested_by="t",
                  confirmed=True)
    names = {t["slug"]: t["name"] for t in KB.tab_list(cfg, "main")["tabs"]}
    assert names["inbox"] == "Intake", names

    moved = KB.tab_move(cfg, "main", "doing", 0, requested_by="t",
                        confirmed=True)
    assert moved["state"] == "OK" and moved["position"] == 0
    assert [t["slug"] for t in KB.tab_list(cfg, "main")["tabs"]] == \
        ["doing", "inbox"]

    removed = KB.tab_delete(cfg, "main", "inbox", requested_by="t",
                            confirmed=True)
    assert removed["removed"] == "inbox"
    assert [t["slug"] for t in KB.tab_list(cfg, "main")["tabs"]] == ["doing"]

    with pytest.raises(KB.KanbanError) as dup:
        KB.tab_create(cfg, "main", "doing", requested_by="t", confirmed=True)
    assert dup.value.code == KB.EXISTS_CODE


def test_default_tab_for_legacy_board(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    # Widget created through the unchanged (legacy) API, no tab argument.
    w = KB.widget_add(cfg, "main", "note", "old note", content="hi",
                      requested_by="t", confirmed=True)["widget"]
    assert w["tab"] == KB.DEFAULT_TAB_SLUG

    tabs = KB.tab_list(cfg, "main")
    assert len(tabs["tabs"]) == 1
    default = tabs["tabs"][0]
    assert default["slug"] == KB.DEFAULT_TAB_SLUG
    assert default["default"] is True
    assert default["id"] is None
    assert default["widget_count"] == 1

    opened = KB.board_open(cfg, "main")
    assert opened["tabs"][0]["slug"] == KB.DEFAULT_TAB_SLUG
    assert opened["widgets"][0]["tab"] == KB.DEFAULT_TAB_SLUG

    filtered = KB.widget_list(cfg, "main", tab=KB.DEFAULT_TAB_SLUG)
    assert [x["id"] for x in filtered["widgets"]] == [w["id"]]


# ---------------------------------------------------------- widget kinds
def test_widget_kinds_roundtrip_with_size_caps(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)

    for kind in ("note", "text", "tasks", "stats", "links"):
        out = KB.widget_add(cfg, "main", kind, f"w-{kind}", content="body",
                            requested_by="t", confirmed=True)
        assert out["widget"]["kind"] == kind, out

    link = KB.widget_add(cfg, "main", "link", "docs",
                         content="https://example.com/a?b=1",
                         requested_by="t", confirmed=True)
    assert link["widget"]["kind"] == "link"
    assert link["widget"]["url"] == "https://example.com/a?b=1"
    assert link["widget"]["trust"] == KB.WIDGET_TRUST_SANITIZED

    html = KB.widget_add(cfg, "main", "html", "rich", content="<b>hi</b>",
                         requested_by="t", confirmed=True)
    assert html["widget"]["kind"] == "html"
    svg = KB.widget_add(cfg, "main", "svg", "vector", content="<svg/>",
                        requested_by="t", confirmed=True)
    assert svg["widget"]["kind"] == "svg"

    # content_kind is an accepted alias for kind.
    alias = KB.widget_add(cfg, "main", title="alias",
                          content_kind="text", content="x",
                          requested_by="t", confirmed=True)
    assert alias["widget"]["kind"] == "text"

    # Per-kind caps are enforced (bounded size).
    with pytest.raises(KB.KanbanError) as e1:
        KB.widget_add(cfg, "main", "text", "big",
                      content="x" * (KB.WIDGET_CONTENT_LEN + 1),
                      requested_by="t", confirmed=True)
    assert e1.value.code == KB.BAD_INPUT_CODE
    with pytest.raises(KB.KanbanError) as e2:
        KB.widget_add(cfg, "main", "html", "big",
                      content="x" * (KB.WIDGET_MARKUP_LEN + 1),
                      requested_by="t", confirmed=True)
    assert e2.value.code == KB.BAD_INPUT_CODE
    with pytest.raises(KB.KanbanError) as e3:
        KB.widget_add(cfg, "main", "link", "big",
                      content="https://e.com/" + "x" * KB.WIDGET_URL_LEN,
                      requested_by="t", confirmed=True)
    assert e3.value.code == KB.BAD_INPUT_CODE

    # Link must be an absolute http(s) reference; it is never fetched.
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "not a url"):
        with pytest.raises(KB.KanbanError) as e4:
            KB.widget_add(cfg, "main", "link", "bad", content=bad,
                          requested_by="t", confirmed=True)
        assert e4.value.code == KB.BAD_INPUT_CODE

    with pytest.raises(KB.KanbanError):
        KB.widget_add(cfg, "main", "terminal", "nope", requested_by="t",
                      confirmed=True)


def test_html_svg_marked_inert_and_not_sanitized_or_executed(tmp_path,
                                                             monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    hostile = ("<script>fetch('//evil')</script>"
               "<img src=x onerror=\"steal()\">")
    html = KB.widget_add(cfg, "main", "html", "hostile", content=hostile,
                         requested_by="t", confirmed=True)["widget"]
    # Stored verbatim, explicitly inert, never executable server-side.
    assert html["content"] == hostile
    assert html["trust"] == KB.WIDGET_TRUST_INERT
    assert html["inert"] is True
    assert html["executable"] is False
    assert html["sanitized"] is False

    svg = KB.widget_add(cfg, "main", "svg", "hostile",
                        content="<svg onload=\"x()\"></svg>",
                        requested_by="t", confirmed=True)["widget"]
    assert svg["trust"] == KB.WIDGET_TRUST_INERT and svg["inert"] is True

    # Contrast: the same bytes in a plain-text kind are stripped, proving
    # only inert markup kinds skip the sanitizer.
    note = KB.widget_add(cfg, "main", "note", "hostile", content=hostile,
                         requested_by="t", confirmed=True)["widget"]
    assert note["sanitized"] is True
    assert "<script" not in note["content"] and "onerror" not in note["content"]

    # The inert marker and raw bytes survive a read-back.
    listed = {w["id"]: w for w in KB.widget_list(cfg, "main")["widgets"]}
    assert listed[html["id"]]["trust"] == KB.WIDGET_TRUST_INERT
    assert listed[html["id"]]["content"] == hostile


def test_declared_grants_are_bounded_and_review_only(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    ok = KB.widget_add(cfg, "main", "note", "g",
                       declared_grants=["tasks:read", "stats:read"],
                       requested_by="t", confirmed=True)["widget"]
    assert ok["grants"] == ["tasks:read", "stats:read"]
    with pytest.raises(KB.KanbanError) as bad:
        KB.widget_add(cfg, "main", "note", "g",
                      declared_grants=["root:everything"],
                      requested_by="t", confirmed=True)
    assert bad.value.code == KB.BAD_INPUT_CODE
    with pytest.raises(KB.KanbanError):
        KB.widget_add(cfg, "main", "note", "g",
                      declared_grants=["tasks:read"] * 99,
                      requested_by="t", confirmed=True)


# ------------------------------------------------------- scoped grants
def test_tab_grants_never_widen_board_access(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    KB.role_grant(cfg, "main", "alice", "owner", requested_by="t",
                  confirmed=True)
    KB.role_grant(cfg, "main", "bob", "owner", requested_by="t",
                  confirmed=True)
    KB.tab_create(cfg, "main", "focus", "Focus", requested_by="t",
                  confirmed=True)
    KB.tab_role_grant(cfg, "main", "focus", "alice", "viewer",
                      requested_by="t", confirmed=True)

    roles = KB.tab_roles_list(cfg, "main", "focus")["roles"]
    assert roles == {"alice": "viewer"}, roles

    # A tab role can only lower a board owner to viewer, never widen.
    with pytest.raises(KB.KanbanError) as denied:
        KB.widget_add(cfg, "main", "note", "a", tab="focus",
                      principal="alice", requested_by="t", confirmed=True)
    assert denied.value.code == KB.DENIED_CODE

    # A board owner without a tab role inherits the board rank.
    allowed = KB.widget_add(cfg, "main", "note", "b", tab="focus",
                            principal="bob", requested_by="t", confirmed=True)
    assert allowed["widget"]["tab"] == "focus"

    # A tab grant to a principal with no board role grants nothing.
    KB.tab_role_grant(cfg, "main", "focus", "carol", "editor",
                      requested_by="t", confirmed=True)
    with pytest.raises(KB.KanbanError) as denied2:
        KB.widget_add(cfg, "main", "note", "c", tab="focus",
                      principal="carol", requested_by="t", confirmed=True)
    assert denied2.value.code == KB.DENIED_CODE

    # Revoking the tab ceiling restores the board role.
    KB.tab_role_revoke(cfg, "main", "focus", "alice", requested_by="t",
                       confirmed=True)
    back = KB.widget_add(cfg, "main", "note", "d", tab="focus",
                         principal="alice", requested_by="t", confirmed=True)
    assert back["widget"]["tab"] == "focus"

    # On an unrestricted board, tab grants neither restrict nor widen.
    KB.role_revoke(cfg, "main", "alice", requested_by="t", confirmed=True)
    removed = KB.role_revoke(cfg, "main", "bob", requested_by="t",
                             confirmed=True)
    assert removed["restricted"] is False
    free = KB.widget_add(cfg, "main", "note", "e", tab="focus",
                         principal="carol", requested_by="t", confirmed=True)
    assert free["widget"]["tab"] == "focus"


# --------------------------------------------------- reversibility/guards
def test_snapshot_restore_restores_tabs(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    KB.tab_create(cfg, "main", "alpha", "Alpha", requested_by="t",
                  confirmed=True)
    KB.tab_rename(cfg, "main", "alpha", "Renamed", requested_by="t",
                  confirmed=True)
    # The rename auto-snapshot captures alpha as it was ("Alpha").
    snaps = KB.snapshot_list(cfg, "main")["snapshots"]
    assert snaps, "tab mutations must auto-snapshot"
    target = snaps[0]["id"]
    KB.tab_delete(cfg, "main", "alpha", requested_by="t", confirmed=True)
    assert KB.tab_list(cfg, "main")["tabs"][0]["slug"] == KB.DEFAULT_TAB_SLUG

    out = KB.snapshot_restore(cfg, target, requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    restored = KB.tab_list(cfg, "main")["tabs"]
    assert [t["slug"] for t in restored] == ["alpha"], restored
    assert restored[0]["name"] == "Alpha"


def test_tab_mutations_require_confirm_and_respect_estop(tmp_path,
                                                         monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    with pytest.raises(KB.KanbanError) as needs:
        KB.tab_create(cfg, "main", "x", requested_by="t", confirmed=False)
    assert needs.value.code == KB.CONFIRM_CODE
    monkeypatch.setattr(KB, "estop_engaged", lambda _c: True)
    with pytest.raises(KB.KanbanError) as stopped:
        KB.tab_create(cfg, "main", "x", requested_by="t", confirmed=True)
    assert stopped.value.code == KB.ESTOP_CODE


# ------------------------------------------------- typed errors / legacy
def test_unknown_board_and_tab_typed_errors(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    with pytest.raises(KB.KanbanError) as nb:
        KB.tab_list(cfg, "ghost")
    assert nb.value.code == KB.NOT_FOUND_CODE
    with pytest.raises(KB.KanbanError) as nt:
        KB.tab_rename(cfg, "main", "nope", "N", requested_by="t",
                      confirmed=True)
    assert nt.value.code == KB.NOT_FOUND_CODE
    with pytest.raises(KB.KanbanError) as wt:
        KB.widget_add(cfg, "main", "note", "x", tab="nope",
                      requested_by="t", confirmed=True)
    assert wt.value.code == KB.NOT_FOUND_CODE
    with pytest.raises(KB.KanbanError) as gb:
        KB.tab_create(cfg, "ghost", "t", requested_by="t", confirmed=True)
    assert gb.value.code == KB.NOT_FOUND_CODE
    with pytest.raises(KB.KanbanError) as gr:
        KB.tab_role_grant(cfg, "main", "nope", "sam", "editor",
                          requested_by="t", confirmed=True)
    assert gr.value.code == KB.NOT_FOUND_CODE


def test_legacy_canvas_schema_migrates_additively(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    db = KB._db_path(cfg)
    db.parent.mkdir(parents=True, exist_ok=True)
    # Old schema exactly as shipped before tabs/grants existed.
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE boards(slug TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " project_slug TEXT, created_at REAL NOT NULL);"
        "INSERT INTO boards VALUES('main','main',NULL,1.0);"
        "CREATE TABLE canvas_widgets("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL,"
        " kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
        " content TEXT NOT NULL DEFAULT '', x INTEGER NOT NULL DEFAULT 0,"
        " y INTEGER NOT NULL DEFAULT 0, w INTEGER NOT NULL DEFAULT 4,"
        " h INTEGER NOT NULL DEFAULT 3, created_at REAL NOT NULL,"
        " updated_at REAL NOT NULL);"
        "INSERT INTO canvas_widgets(board, kind, title, content, x, y, w, h,"
        " created_at, updated_at) VALUES('main','note','legacy','old',0,0,4,3,1.0,1.0);")
    conn.commit()
    conn.close()

    # Reads run the additive migration, then keep working.
    widgets = KB.widget_list(cfg, "main")["widgets"]
    assert len(widgets) == 1
    assert widgets[0]["title"] == "legacy"
    assert widgets[0]["tab"] == KB.DEFAULT_TAB_SLUG
    assert widgets[0]["grants"] == []
    assert widgets[0]["trust"] == KB.WIDGET_TRUST_SANITIZED

    tabs = KB.tab_list(cfg, "main")["tabs"]
    assert tabs[0]["default"] is True

    # The migration only added nullable/defaulted columns.
    with sqlite3.connect(str(db)) as check:
        cols = {r[1] for r in check.execute(
            "PRAGMA table_info(canvas_widgets)")}
    assert {"tab", "grants"} <= cols
