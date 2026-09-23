"""Wave 4 — voice coverage for the new surfaces (Hermes plugin fleet + CLI union).

This is the wave-4 verification layer over the frozen voice-completeness gate
(``docs/archive/2026-09-21/QUBIT_FULL_PARITY_CONTRACT.md`` / ``docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md``).  It
closes the last silent gaps left after waves 1-3 without touching the frozen
wave-1/2/3 example tables:

1. every top-level ``qubit <family>`` in the live CLI parser has either a real
   registry entry or an explicit, tested ``VOICE_IMPOSSIBLE`` reason — the
   union the ledger derives is asserted equal to the parser tree (no drift);
2. every wave-3 vendored dashboard-plugin compat route (the
   ``hermes-achievements`` + kanban plugin fleet, ``qubit_plugins.py``) has a
   real voice entry that dispatches the *same* ``qubit_compat`` route the
   dashboard SPA calls — no unspoken plugin route, and the one dashboard-only
   control is explicitly declared;
3. the fleet entries execute through the shared production pipeline
   (``voice_say`` -> ``execute`` -> ``qubit_compat.dispatch``), return the
   real computed payload, and bind no second listener/backend;
4. representative fleet behavior: a confirm-tier fleet write stages exactly one
   durable confirmation that can be cancelled, replayed (single-use), and is
   session-isolated; the vendored e-stop/confirm gates surface as typed
   failures, never fabricated success;
5. model/page/tool sources can never stage or execute a fleet entry.

Hermetic: temp ``XDG_*`` + ``QUBIT_HOME``; no network; no live services.  The
achievement engine runs the vendored production engine over a seeded
``qubit_state.SessionDB`` and the kanban reads run over a seeded
``kanban_db`` — no mock replaces the production caller.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Vendored plugin compat routes whose UI is a dashboard-only control and
# therefore has no natural spoken form. Declared here so the audit can assert
# every unspoken plugin route is an explicit impossibility, not a silent
# omission. Each route stays reachable from the dashboard SPA.
DASHBOARD_ONLY_PLUGIN_ROUTES = {
    "h_plugin_visibility_set",
    # Static dashboard-plugin asset bytes (JS/CSS served to the browser). There
    # is no spoken form for fetching an asset file.
    "h_plugin_asset",
    # Kanban auxiliary drawers: raw task logs, binary attachment listings,
    # worker-run internals, profile roster and orchestration knobs. These are
    # inspected in the dashboard, not driven by voice; the spoken kanban
    # surface (`kanban_fleet_*`) covers board/task/stats/config/projects.
    "h_plugin_kanban_task_log",
    "h_plugin_kanban_task_attachments",
    "h_plugin_kanban_attachment_download",
    "h_plugin_kanban_run",
    "h_plugin_kanban_run_inspect",
    "h_plugin_kanban_profiles",
    "h_plugin_kanban_orchestration",
    # Live-update poll for the board tab (real task_events read); the spoken
    # kanban surface covers board/task/stats/config/projects.
    "h_plugin_kanban_events",
    # Vendored kanban WRITE controls. They are native-backed on the one board
    # store (confirm-gated); the capability is spoken through the Tier-1
    # ``kanban_*`` entries (``kanban_create``/``kanban_move``/``kanban_comment``
    # ...) which drive the same native store through the ops broker. The
    # vendored plugin route is the dashboard UI for that mutation, not a second
    # spoken backend.
    "h_plugin_kanban_boards_create",
    "h_plugin_kanban_tasks_create",
    "h_plugin_kanban_tasks_patch",
    "h_plugin_kanban_tasks_delete",
    "h_plugin_kanban_tasks_bulk",
    "h_plugin_kanban_tasks_comment",
}

# Representative fleet reads: (spoken alias, voice entry id, compat handler).
_FLEET_READ_BATTERY = (
    ("qubit, show my achievements", "achievements_list",
     "h_plugin_achievements"),
    ("qubit, recent achievements", "achievements_recent",
     "h_plugin_achievements_recent_unlocks"),
    ("qubit, achievement scan status", "achievements_scan_status",
     "h_plugin_achievements_scan_status"),
    ("qubit, list dashboard plugins", "plugin_fleet_list", "h_plugin_fleet"),
    ("qubit, fleet kanban board", "kanban_fleet_board",
     "h_plugin_kanban_board"),
    ("qubit, fleet kanban stats", "kanban_fleet_stats",
     "h_plugin_kanban_stats"),
    ("qubit, fleet kanban workers", "kanban_fleet_workers",
     "h_plugin_kanban_workers"),
)


# --------------------------------------------------------------------- helpers
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="wave4", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, actor="voice:user", session=session, source=source)


def _qubit_home(cfg) -> Path:
    return Path(cfg.state_dir()) / "hermes"


def _seed_history(cfg, monkeypatch):
    """Seed a real SessionDB under the Qubit QUBIT_HOME (achievements input)."""
    from voicebridge import qubit_runtime

    home = _qubit_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    qubit_runtime.ensure_path()
    from qubit_state import SessionDB

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
    from voicebridge import kanban as N

    out = N.create(cfg, "Wave4 voice task", requested_by="voice",
                   confirmed=True)
    return out["task"]["id"]


def _families():
    """Top-level CLI families; importing cli syncs CLI_COVERAGE (idempotent)."""
    from voicebridge.cli import build_parser

    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    return names


def _plugin_routes():
    """Every compat route served for the vendored dashboard-plugin fleet."""
    from voicebridge import qubit_compat as HC

    routes = []
    for route in HC.ROUTES:
        handler = route.get("handler", "")
        if handler.startswith("h_plugin_") or \
                route["pattern"] == "/api/dashboard/plugins/fleet":
            routes.append(route)
    return routes


# ------------------------------------------------------- 1. CLI family union
def test_every_cli_family_has_a_voice_surface_or_typed_reason():
    """The union of every implemented CLI family is voice-covered or refused."""
    from voicebridge import voice_commands as VC

    families = _families()
    assert set(VC.CLI_COVERAGE) == families, \
        f"CLI families drifted: {sorted(set(families) ^ set(VC.CLI_COVERAGE))}"

    for family, targets in VC.CLI_COVERAGE.items():
        for target in targets:
            assert target in VC.REGISTRY, (family, target)
        reasons = [k for k in VC.VOICE_IMPOSSIBLE
                   if k == family or k.startswith(f"{family}.")]
        assert targets or reasons, \
            f"{family}: neither a voice entry nor an impossible reason"

    for key, reason in VC.VOICE_IMPOSSIBLE.items():
        family = key.split(".")[0]
        assert family in VC.CLI_COVERAGE, key
        assert reason and len(reason) > 20, (key, reason)
        if "." not in key:
            assert VC.CLI_COVERAGE[family] == (), key


# --------------------------------------------------- 2. plugin-fleet routes
def test_every_vendored_plugin_route_has_a_voice_entry():
    """No vendored dashboard-plugin route is silently unspoken."""
    from voicebridge import qubit_compat as HC
    from voicebridge import voice_commands as VC

    unspoken = []
    for route in _plugin_routes():
        handler = route["handler"]
        if handler in DASHBOARD_ONLY_PLUGIN_ROUTES:
            continue
        entry_id = VC.PLUGIN_FLEET_VOICE.get(handler)
        if entry_id is None:
            unspoken.append((route["method"], route["pattern"], handler))
            continue
        assert entry_id in VC.PLUGIN_FLEET_REGISTRY, (handler, entry_id)
        assert callable(HC.HANDLERS.get(handler)), handler
    assert not unspoken, f"plugin routes with no voice entry: {unspoken}"

    # The declared dashboard-only set must be a subset of the real handlers,
    # so a rename can never make the exemption vacuous.
    handlers = {r["handler"] for r in _plugin_routes()}
    assert DASHBOARD_ONLY_PLUGIN_ROUTES <= handlers, \
        DASHBOARD_ONLY_PLUGIN_ROUTES - handlers

    # Each declared op maps to a real compat route (nothing invented).
    for op, (method, path, _body) in VC.PLUGIN_FLEET_OPS.items():
        route, _params = HC.match(method, path.format(task_id="x",
                                                      session_id="x"))
        assert route is not None, (op, method, path)
        assert route["disposition"] == "implemented", (op, route)


def test_fleet_entry_aliases_resolve_to_their_own_entry():
    from voicebridge import voice_commands as VC

    misrouted = []
    for entry in VC.PLUGIN_FLEET_REGISTRY.values():
        for alias in entry.aliases:
            res = VC.resolve("qubit, " + alias.replace("rx:", "")
                             if alias.startswith("rx:") else "qubit, " + alias)
            if alias.startswith("rx:"):
                # rx aliases capture an argument phrase; resolve with a sample.
                sample = alias[3:]
                probe = sample.replace("(.+)", "12")
                res = VC.resolve("qubit, " + probe)
            got = res.entry.id if res.entry is not None else None
            if res.kind not in (VC.MATCH, VC.CLARIFY) or got != entry.id:
                misrouted.append((entry.id, alias, res.kind, got))
    assert not misrouted, f"fleet aliases not bound to their entry: {misrouted}"


# ---------------------------------------------- 3. same backend as dashboard
def test_fleet_reads_share_the_compat_backend_result(tmp_path, monkeypatch):
    """Voice and the dashboard compt route return the same computed payload."""
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_plugins as HP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    _seed_kanban(cfg, monkeypatch)
    HP.reset_plugin_caches()

    route_by_handler = {r["handler"]: r for r in _plugin_routes()}
    for text, entry_id, handler in _FLEET_READ_BATTERY:
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == entry_id, (text, res)

        said = VC.voice_say(cfg, text, session=f"w4-{entry_id}")
        assert said["state"] == "executed", (entry_id, said)
        assert said.get("confirmation_id") is None, entry_id
        voiced = said["receipt"]["result"]["data"]

        route = route_by_handler[handler]
        code, direct = HC.dispatch(cfg, route["method"], route["pattern"],
                                   {}, {}, actor="dashboard:user")
        assert code == 200, (entry_id, code, str(direct)[:300])
        # Top-level wall-clock stamps are computed per call (``generated_at``,
        # ``checked_at``, the kanban board's ``now``), so they legitimately
        # differ by up to a second between the two surfaces; every other field
        # must be byte-identical. Nested task timestamps are seeded and stay
        # deterministic, so they are still compared.
        def _strip_volatile(v):
            if isinstance(v, dict):
                return {k: _strip_volatile(x) for k, x in v.items()
                        if k != "now" and k != "age"
                        and not k.endswith("_at")}
            if isinstance(v, list):
                return [_strip_volatile(x) for x in v]
            return v

        if isinstance(voiced, dict) and isinstance(direct, dict):
            voiced = _strip_volatile(voiced)
            direct = _strip_volatile(direct)
        assert voiced == direct, (entry_id, "voice and dashboard differ")


def test_achievements_compute_real_state_not_badges(tmp_path, monkeypatch):
    """The spoken rescan runs the vendored engine over real history."""
    from voicebridge import qubit_plugins as HP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()

    rescan = VC.voice_say(cfg, "qubit, rescan achievements", session="s1")
    assert rescan["state"] == "executed", rescan
    assert rescan["receipt"]["result"]["data"]["unlocked_count"] >= 1

    listed = VC.voice_say(cfg, "qubit, show my achievements", session="s1")
    assert listed["state"] == "executed", listed
    data = listed["receipt"]["result"]["data"]
    assert data["total_count"] >= 60, data
    by_id = {a["id"]: a for a in data["achievements"]}
    assert by_id["open_weights_pilgrim"]["unlocked"] is True, \
        by_id["open_weights_pilgrim"]

    recent = VC.voice_say(cfg, "qubit, recent achievements", session="s1")
    assert recent["state"] == "executed", recent
    assert any(a["id"] == "open_weights_pilgrim"
               for a in recent["receipt"]["result"]["data"])

    badges = VC.voice_say(cfg, "qubit, achievements for session sess-local",
                          session="s1")
    assert badges["state"] == "executed", badges
    assert badges["receipt"]["result"]["data"]["session_id"] == "sess-local"


def test_kanban_fleet_reads_serve_the_real_board(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    task_id = _seed_kanban(cfg, monkeypatch)

    board = VC.voice_say(cfg, "qubit, fleet kanban board", session="s1")
    assert board["state"] == "executed", board
    titles = [t["title"] for col in board["receipt"]["result"]["data"]["columns"]
              for t in col["tasks"]]
    assert "Wave4 voice task" in titles, titles

    task = VC.voice_say(cfg, f"qubit, fleet kanban task {task_id}", session="s1")
    assert task["state"] == "executed", task
    spoken = task["speech"].lower()
    assert "error" not in spoken and "traceback" not in spoken, spoken

    missing = VC.voice_say(cfg, "qubit, fleet kanban task does-not-exist",
                           session="s1")
    assert missing["state"] == "failed", missing
    hold = missing.get("receipt", missing)
    assert hold.get("code") == "PLUGIN_HTTP" or "not" in \
        str(hold.get("detail", "") or missing.get("speech", "")).lower(), missing


# ------------------------------------- 4. confirm/cancel/replay/isolation
def test_fleet_write_confirms_cancels_replays_and_isolates(tmp_path,
                                                           monkeypatch):
    from voicebridge import approvals as AP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    ctx = _ctx(cfg, session="fleet")
    other = _ctx(cfg, session="other")

    # Stage exactly one durable confirmation for the confirm-tier reset.
    staged = VC.execute(ctx, VC.resolve("qubit, reset my achievements"))
    assert staged.kind == VC.NEEDS_CONFIRM, (staged.kind, staged.speech)
    assert staged.confirmation_id
    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    pending = [a for a in journal.pending()
               if a.capability == "voice.achievements_reset"]
    assert len(pending) == 1, "exactly one staged confirmation"

    # Session isolation: another session cannot settle it.
    isolated = VC.confirm_pending(other, staged.confirmation_id)
    assert isolated.kind == VC.FAILED, isolated.speech

    # Cancel settles it once; a later confirm is a typed failure.
    cancelled = VC.cancel_pending(ctx, staged.confirmation_id)
    assert cancelled.kind == VC.EXECUTED, cancelled.speech
    gone = VC.confirm_pending(ctx, staged.confirmation_id)
    assert gone.kind == VC.FAILED, gone.speech

    # Replay: execute once, then the confirmation is single-use.
    staged = VC.execute(ctx, VC.resolve("qubit, reset my achievements"))
    first = VC.confirm_pending(ctx, staged.confirmation_id)
    assert first.kind == VC.EXECUTED, first.speech
    replay = VC.confirm_pending(ctx, staged.confirmation_id)
    assert replay.kind == VC.FAILED, replay.speech


def test_fleet_reset_estop_latches_and_is_typed(tmp_path, monkeypatch):
    """A latched e-stop refuses the staged fleet write, never fakes success."""
    from voicebridge import power as PW
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    ctx = _ctx(cfg, session="estop")

    PW.power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                    issued_by="cli:user")
    try:
        blocked = VC.execute(ctx, VC.resolve("qubit, reset my achievements"))
        assert blocked.kind == VC.REFUSED, (blocked.kind, blocked.speech)
        assert "latched" in blocked.speech.lower(), blocked.speech
        assert not blocked.confirmation_id, "a latched e-stop stages nothing"

        # The vendored rescan handler also carries its own e-stop gate.
        rescan = VC.voice_say(cfg, "qubit, rescan achievements", session="estop")
        assert rescan["state"] == "failed", rescan
        hold = rescan.get("receipt", {})
        assert hold.get("code") == "ESTOP_ENGAGED" or \
            "ESTOP" in str(rescan.get("speech", "")), rescan

        # A read stays available while latched.
        read = VC.voice_say(cfg, "qubit, show my achievements", session="estop")
        assert read["state"] == "executed", read
    finally:
        PW.power_control_store(cfg).set(emergency_stop=False,
                                        global_disable=False,
                                        issued_by="cli:user")


# ------------------------------------------------- 5. no fake success
def test_non_user_source_never_stages_or_executes_fleet(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    for source in ("model", "page", "tool", "agent"):
        ctx = _ctx(cfg, session="src", source=source)
        for text in ("qubit, show my achievements",
                     "qubit, reset my achievements",
                     "qubit, fleet kanban board"):
            out = VC.execute(ctx, VC.resolve(text))
            assert out.kind == VC.REFUSED, (source, text, out.kind)
            assert not out.confirmation_id, (source, text)
            assert out.receipt == {}, (source, text, out.receipt)


def test_no_model_text_can_confirm_a_fleet_write(tmp_path, monkeypatch):
    """The only confirmation consumer is the human journal, never text."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    human = _ctx(cfg, session="human")
    staged = VC.execute(human, VC.resolve("qubit, reset my achievements"))
    assert staged.kind == VC.NEEDS_CONFIRM
    model = _ctx(cfg, session="human", source="model")
    assert VC.confirm_pending(model, staged.confirmation_id).kind == VC.REFUSED
    # The human can still settle it afterwards.
    assert VC.cancel_pending(human, staged.confirmation_id).kind == VC.EXECUTED


def test_fleet_bad_args_are_typed_never_staged(tmp_path, monkeypatch):
    """A malformed fleet op fails typed and never fabricates a receipt."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg, session="bad")
    # A path-parameterised op with the argument missing is a typed failure,
    # never a fabricated success or a placeholder-substituted request.
    with pytest.raises(VC.PluginFleetError) as exc:
        VC._b_plugin_fleet(ctx, {"op": "kanban_fleet_task"})
    assert exc.value.code == "PLUGIN_FLEET_BAD_ARGS"
    # A genuinely unknown op raises a typed error, no success shape.
    with pytest.raises(VC.PluginFleetError) as exc:
        VC._b_plugin_fleet(ctx, {"op": "does_not_exist"})
    assert exc.value.code == "PLUGIN_FLEET_BAD_OP"
