"""Independent parity audit: /qubit-chat vs /chat slash/skill/mcp/plugin coverage.

Verifier-lane file. READ-ONLY on product code: this test never writes product
files; it imports live backend symbols (chat_transport, qubit_compat ROUTES /
HANDLERS) and reads ChatPage.tsx as text for a landing-detector string check.

Terminal inventory (TERMINAL_SLASH_INVENTORY) was enumerated by the verifier on
2026-10-04 with::

    rg -n "async def _handle_[a-z_]+_command" qubit_runtime/gateway/
    sed -n '865,915p' qubit_runtime/gateway/run_busy.py   # _PLAIN/_IDLE_COMMANDS

Source of truth for the registry shape is ``run_busy.py`` (``_PLAIN_COMMANDS``
22 + ``_IDLE_COMMANDS`` 36 = 58 names) plus handler-only extras (``reset``,
``undo`` — real ``_handle_*_command`` methods with no registry row) = 60
canonical slash names. Handler -> slash-name mapping notes: ``bg`` is the
registered name for ``_handle_background_command`` (alias table), ``sethome``
for ``_handle_set_home_command``, hyphenated names (``codex-runtime``,
``reload-mcp``, ``reload-skills``) map ``-`` -> ``_`` to the handler.

To regenerate after gateway changes, re-run the rg above and update the
frozenset. (Deliberately a frozen list, not a live gateway import: importing
the gateway package executes config/env side effects; the live side of this
audit is /qubit-chat.)

Scope note: the strictest /chat surface is wider than this inventory (the
vendored TUI/CLI ``_SLASH_DISPATCH`` + 110-name ``commands.py`` registry on
top of the gateway set). This audit pins the gateway set per the lane brief;
the CLI-registry delta is tracked in docs/QUBIT_CHAT_SLASH_PARITY_2026-10-04.md
as follow-up, not asserted here.

Expected state: the catalog-coverage test PASSES (backend-catalog lane
landed: GET /api/chat/slash-commands serves every terminal verb as data).
The turn-execution test and the popup-wiring test FAIL with exact names —
/qubit-chat can LIST 55 verbs it cannot yet EXECUTE, and the composer has no
popup. Those failures are the audited signal for the turn-routing and
frontend lanes, not regressions.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CHAT_PAGE = (
    REPO_ROOT / "frontend" / "qubit-web" / "src" / "pages" / "ChatPage.tsx"
)

# -- terminal (gateway) slash inventory, frozen 2026-10-04 -------------------
# 60 canonical names. See module docstring for provenance/regeneration.
TERMINAL_SLASH_INVENTORY = frozenset({
    "agents", "approve", "approvals", "bg", "blueprint", "branch", "btw",
    "bundles", "busy", "codex-runtime", "commands", "compress", "context",
    "debug", "deny", "diff", "fast", "footer", "goal", "heartbeat", "help",
    "insights", "kanban", "login", "loop", "memory", "model", "pause",
    "personality", "platform", "profile", "reasoning", "refine", "reload-mcp",
    "reload-skills", "reset", "restart", "resume", "retry", "review",
    "rollback", "save", "sessions", "sethome", "skills", "status", "stop",
    "subgoal", "suggestions", "title", "topic", "topup", "undo", "update",
    "usage", "verbose", "version", "voice", "whoami", "yolo",
})

# Commands with a load-bearing reason to never exist on the dashboard chat
# surface. Every entry needs its reason; the allowlist cannot grow silently
# (test_out_of_scope_reasons_are_load_bearing pins the current size).
OUT_OF_SCOPE = {
    # Binds whole-install credentials through a paired-DM consent ceremony
    # (slash_commands_login.py: refuses outside a paired DM). The dashboard
    # session is already authenticated; there is no ceremony to run here.
    "login": "transport-bound auth ceremony; dashboard session already authed",
    # Lists/pauses/resumes messaging transport adapters. A dashboard chat
    # turn has no transport adapter, so the command has no referent here.
    "platform": "no messaging transport exists on the dashboard chat surface",
}

# Verbs chat_transport serves that have NO terminal counterpart (dashboard-
# native additions, not parity gaps). Pinned so a newly added verb without an
# audit update fails loudly in both directions.
QUBITCHAT_EXTRA = frozenset({"mcp", "plugins"})

# Any ONE of these strings in ChatPage.tsx counts as the slash-popup wiring
# the frontend lane owns (filter row + popup testids). Any-of semantics so the
# lane is free to pick names; absence of all of them is the gap signal.
POPUP_MARKERS = (
    "slash-popup",
    "slashPopup",
    "slash-filter",
    "slashFilter",
    "SlashCommands",
    "slashCommands",
    "chat-slash",
    "chatSlash",
)


def _qubitchat_verbs():
    from voicebridge import chat_transport as CT

    return frozenset(CT.SLASH_COMMANDS)


# -- coverage ----------------------------------------------------------------

def test_terminal_inventory_has_expected_size():
    # Change-detector with a purpose: the gateway set must only change with a
    # verifier re-enumeration (see module docstring). If this fails, re-run
    # the rg provenance command and update the frozenset + docs together.
    assert len(TERMINAL_SLASH_INVENTORY) == 60, (
        f"terminal slash inventory is {len(TERMINAL_SLASH_INVENTORY)}, "
        f"expected 60: {sorted(TERMINAL_SLASH_INVENTORY)}"
    )


def test_slash_catalog_covers_terminal_inventory():
    """Every terminal verb is listed by GET /api/chat/slash-commands (data).

    Probes the landed handler directly — no live server: calls
    h_chat_slash_commands with an empty cfg (all sources are fail-soft
    read-only aggregations) and compares command names. PASSES since the
    backend-catalog lane landed; fails loudly with names if the catalog
    regresses or the gateway inventory grows.
    """
    from voicebridge import qubit_compat as qc

    code, body = qc.h_chat_slash_commands({}, {}, {}, {})
    assert code == 200, f"catalog handler returned {code}: {body}"
    names = {str(c.get("name", "")).lstrip("/")
             for c in (body.get("commands") or [])}
    uncovered = sorted(TERMINAL_SLASH_INVENTORY - names - set(OUT_OF_SCOPE))
    assert not uncovered, (
        f"catalog gap ({len(uncovered)} terminal verbs not listed by "
        f"GET /api/chat/slash-commands): {uncovered}. "
        f"Catalog total: {body.get('total')}, sources: {body.get('sources')}."
    )


def test_qubitchat_turn_executes_terminal_verbs():
    """Every terminal slash verb EXECUTES on a /qubit-chat turn, not just lists.

    chat_transport currently executes 5 verbs (help/skills/model/mcp/plugins);
    any other leading-slash turn answers honest help naming the verb — correct
    fallback, but not parity with /chat, which runs the verb. FAILS LOUDLY
    with the verb list until the turn-routing lane wires execution; the
    failure message IS the gap list for that lane. (Catalog-as-data coverage
    is pinned separately by test_slash_catalog_covers_terminal_inventory.)
    """
    covered = _qubitchat_verbs()
    unexecuted = sorted(
        TERMINAL_SLASH_INVENTORY - covered - set(OUT_OF_SCOPE))
    assert not unexecuted, (
        f"/qubit-chat turn-execution gap ({len(unexecuted)}/"
        f"{len(TERMINAL_SLASH_INVENTORY)} terminal verbs list but do not "
        f"execute): {unexecuted}. Executing now: "
        f"{sorted(covered & TERMINAL_SLASH_INVENTORY)}. "
        f"Out-of-scope: {sorted(OUT_OF_SCOPE)}."
    )


def test_out_of_scope_reasons_are_load_bearing():
    assert set(OUT_OF_SCOPE) <= TERMINAL_SLASH_INVENTORY, (
        f"out-of-scope names outside the inventory (stale allowlist): "
        f"{sorted(set(OUT_OF_SCOPE) - TERMINAL_SLASH_INVENTORY)}"
    )
    assert not (set(OUT_OF_SCOPE) & _qubitchat_verbs()), (
        "out-of-scope allowlist overlaps live coverage; remove the entry, "
        "the command is covered now."
    )
    for name, reason in OUT_OF_SCOPE.items():
        assert isinstance(reason, str) and len(reason) >= 20, (
            f"out-of-scope entry {name!r} needs a load-bearing reason "
            f"(>= 20 chars), got: {reason!r}"
        )
    assert len(OUT_OF_SCOPE) == 2, (
        f"out-of-scope allowlist grew to {len(OUT_OF_SCOPE)} without a "
        f"verifier review: {sorted(OUT_OF_SCOPE)}"
    )


def test_qubitchat_extra_verbs_documented():
    extra = _qubitchat_verbs() - TERMINAL_SLASH_INVENTORY
    assert extra == QUBITCHAT_EXTRA, (
        f"/qubit-chat verbs without a terminal counterpart changed: "
        f"live={sorted(extra)} pinned={sorted(QUBITCHAT_EXTRA)}. Update the "
        f"audit (and docs) if a verb was added or removed."
    )


# -- catalog endpoint --------------------------------------------------------

def test_slash_catalog_endpoint_landed():
    """GET /api/chat/slash-commands resolves through the real route matcher.

    No live server: probes the imported ROUTES table + HANDLERS registry
    directly. FAILS until the backend-catalog lane registers the endpoint.
    """
    from voicebridge import qubit_compat as qc

    row, _params = qc.match("GET", "/api/chat/slash-commands")
    assert row is not None, (
        "gap: no ROUTES row matches GET /api/chat/slash-commands "
        "(backend-catalog lane has not landed the endpoint)."
    )
    handler = row.get("handler")
    assert handler in qc.HANDLERS and callable(qc.HANDLERS[handler]), (
        f"gap: route row found but handler {handler!r} is not a callable "
        f"in HANDLERS."
    )


# -- skills/plugins/mcp/toolsets reachability --------------------------------

def test_skills_plugins_mcp_toolsets_endpoints_reachable():
    """The four coverage surfaces resolve + dispatch, no live server."""
    from voicebridge import qubit_compat as qc

    cases = {
        "GET /api/skills": "h_skills",
        "GET /api/dashboard/plugins": "h_plugins",
        "GET /api/mcp/servers": "h_mcp_servers",
        "GET /api/tools/toolsets": "h_toolsets",
    }
    failures = []
    for label, want_handler in cases.items():
        method, _, path = label.partition(" ")
        row, _params = qc.match(method.strip(), path.strip())
        if row is None:
            failures.append(f"{label}: no ROUTES row")
            continue
        handler = row.get("handler")
        if handler != want_handler:
            failures.append(
                f"{label}: handler is {handler!r}, expected {want_handler!r}"
            )
        elif handler not in qc.HANDLERS or not callable(qc.HANDLERS[handler]):
            failures.append(f"{label}: {handler!r} not callable in HANDLERS")
        elif row.get("disposition") != "implemented":
            failures.append(
                f"{label}: disposition is {row.get('disposition')!r}, "
                f"not 'implemented'"
            )
    assert not failures, (
        f"skills/plugins/mcp/toolsets reachability failures: {failures}"
    )


# -- frontend wiring (landing detector) ---------------------------------------

def test_chatpage_send_path_intact():
    """Sanity: ChatPage.tsx still sends via the canonical turn transport."""
    text = CHAT_PAGE.read_text(encoding="utf-8")
    assert "streamChatTurn" in text, (
        "ChatPage.tsx no longer references streamChatTurn "
        "(POST /api/qubit/chat/stream send path broken)."
    )


def test_chatpage_slash_popup_wiring():
    """ChatPage.tsx carries the slash filter/popup the frontend lane owns.

    Landing detector (string check by lane brief): passes when the popup
    wiring lands under any of POPUP_MARKERS. FAILS until then. Deliberate
    source-text assertion — the behavioral E2E (type '/' -> popup lists
    catalog verbs) replaces this once the endpoint + popup both exist.
    """
    text = CHAT_PAGE.read_text(encoding="utf-8")
    assert any(m in text for m in POPUP_MARKERS), (
        f"gap: ChatPage.tsx has no slash popup wiring (none of "
        f"{list(POPUP_MARKERS)} present). Send path (streamChatTurn) is "
        f"separate and pinned by test_chatpage_send_path_intact."
    )
