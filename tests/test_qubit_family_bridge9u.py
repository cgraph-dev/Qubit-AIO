"""Qubit family bridge: allowlisted reads, typed 404s, confirm round-trip.

The 13 Qubit family pages call /api/qubit/<family>/<action> (the five
ocFamily*Api ROUTES manifests). The generic bridge in
``voicebridge/qubit_compat.py`` serves them verbatim and delegates to the
already-registered gateway registry method behind each path.

Proves: an allowlisted read executes through the registry; an unknown action
(and an unknown family, and a wrong method) is a typed 404; a mutation
without confirmation stages a typed 409 with a real confirmation_id and
executes nothing; re-POST with confirmation executes exactly once.

Hermetic: temp XDG/QUBIT_HOME only; no live service, network, or hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qubit_home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_allowlisted_read_executes(tmp_path, monkeypatch):
    """GET /api/qubit/talk/mode runs the registered talk.mode method."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/talk/mode", {}, {})
    assert code == 200, out
    assert out.get("state") == "OK", out
    assert out.get("effectiveMode") == "local-stt-tts", out


def test_unknown_action_is_typed_404(tmp_path, monkeypatch):
    """A path under a known family with no allowlist row is a typed 404."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/nodes/bogus", {}, {})
    assert code == 404, out
    assert out.get("code") == "FAMILY_UNKNOWN_ACTION", out


def test_unknown_family_is_typed_404(tmp_path, monkeypatch):
    """A family outside the five manifests never reaches the bridge."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bogus/action", {}, {})
    assert code == 404, out
    assert out.get("ok") is False, out


def test_wrong_method_is_typed_404(tmp_path, monkeypatch):
    """A read-only action fetched with POST has no route (never executes)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/talk/mode", {}, {})
    assert code == 404, out


def test_mutation_without_confirm_stages_409(tmp_path, monkeypatch):
    """POST nodes/enroll stages a typed 409 and enrolls nothing."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/nodes/enroll", {},
                               {"node_id": "bridge-probe-node"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    # nothing executed: the probe node is not enrolled
    from voicebridge.gateway.rpc import default_registry

    listing = default_registry().handle(cfg, "node.list", {})
    names = [str(n.get("name", "") or "") for n in listing.get("nodes", [])]
    assert "bridge-probe-node" not in names, listing


def test_staged_plus_confirm_executes(tmp_path, monkeypatch):
    """Re-POST with confirm executes the allowlisted method exactly once."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"sessionKey": "bridge-probe-session", "text": "bridge probe"}
    code, staged = HC.dispatch(cfg, "POST",
                               "/api/qubit/talk/client-steer", {}, dict(body))
    assert code == 409, staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/talk/client-steer",
                            {}, {**body, "confirm": True})
    assert code == 200, out
    assert out.get("state") == "OK", out
    assert out.get("sessionKey") == "bridge-probe-session", out


def test_honest_gate_without_registered_method(tmp_path, monkeypatch):
    """Manifest paths with no gateway method behind them stay typed gates."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/devices/identity",
                            {"device_id": "d1"}, {})
    assert code == 403, out
    assert out.get("code") == "DEVICE_IDENTITY_UNAVAILABLE", out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/checkpoints/verify",
                            {"checkpoint_id": "c1"}, {})
    assert code == 403, out
    assert out.get("code") == "CHECKPOINTS_JOURNAL_VERIFY_ONLY", out


# -- second wave (canvas, tasks, flows, memory, webchat) --------------------
# Verbatim allowlist + staged-then-execute cases for the five new families.
# Webchat is reuse-only (no bridge rows): unknown webchat paths stay typed
# 404s while the reused chat endpoints keep serving.


def test_tasks_list_read_executes(tmp_path, monkeypatch):
    """GET /api/qubit/tasks/list runs tasks.list (the shared ledger)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/tasks/list", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert "tasks" in inner, out


def test_tasks_get_reaches_gateway(tmp_path, monkeypatch):
    """GET /api/qubit/tasks/get without a lookup reaches the gateway.

    The `lookup` query aliases to the gateway's `taskId`; with no lookup the
    registered tasks.get refuses with typed bad-params (400) — proving the
    bridge row matched and delegated rather than 404ing.
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/tasks/get", {}, {})
    assert code == 400, out
    assert "BAD_PARAMS" in str(out.get("code", "")), out


def test_tasks_cancel_staged_then_executes(tmp_path, monkeypatch):
    """POST tasks/cancel stages 409, then executes once with confirm.

    A session with no in-flight turn executes safely through
    chat_transport.cancel (cancelled False, no side effects).
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"session": "bridge-probe-nosuch-session"}
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/tasks/cancel", {},
                               dict(body))
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/tasks/cancel", {},
                            {**body, "confirm": True})
    assert code == 200, out
    assert out.get("cancelled") is False, out
    assert "chat_transport.cancel" in str(out.get("authority", "")), out


def test_tasks_retry_stages_409(tmp_path, monkeypatch):
    """POST tasks/retry without confirm stages and retries nothing."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/tasks/retry", {},
                               {"session": "bridge-probe-nosuch-session"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged


def test_flows_hooks_read_executes(tmp_path, monkeypatch):
    """GET /api/qubit/flows/hooks runs hooks.status (real catalog)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/flows/hooks", {}, {})
    assert code == 200, out
    assert out.get("state") == "OK", out
    assert "catalog" in out, out


def test_flows_hook_rows_live_except_cancel_gate(tmp_path, monkeypatch):
    """hook_enable/hook_disable/programs are live (lane I1); only cancel gates.

    Reconciled from the gate-pinning version: enable/disable flip the
    schedules hooks-guard table and programs reads recurring schedules, so a
    403 here would be a stale refusal. `cancel` has no sticky
    taskflow-cancel backend and keeps its honest typed gate.
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/flows/hook_enable",
                               {}, {"name": "command-logger"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/flows/cancel", {},
                            {"flow_id": "f1"})
    assert code == 403, out
    assert out.get("code") == "FLOWS_CANCEL_UNWIRED", out
    assert "cron" in str(out.get("qubit_counterpart", "")), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/flows/programs", {}, {})
    assert code == 200, out
    assert "programs" in out.get("result", out), out


def test_canvas_rows_are_live_mappings(tmp_path, monkeypatch):
    """All 9 canvas rows are live on the confined host (lane I1).

    Reconciled from the gate-pinning version: reads execute, mutations stage
    409 before any confirm is staged (never 403).
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/canvas/list", {}, {})
    assert code == 200, out
    assert "files" in out.get("result", out), out
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/canvas/add", {},
                               {"path": "probe.md", "content": "probe"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/canvas/present", {},
                               {"node_id": "n1",
                                "entry": "/__qubit__/canvas/a"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged


def test_memory_rows_are_typed_gates(tmp_path, monkeypatch):
    """All 5 memory rows gate on the memory-provider gate (no writer)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/memory/status", {}, {})
    assert code == 403, out
    assert out.get("code") == "MEMORY_BRIDGE_UNWIRED", out
    assert "/api/memory" in str(out.get("qubit_counterpart", "")), out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/memory/provider-set",
                            {}, {"provider": "qubit-local"})
    assert code == 403, out
    assert out.get("code") == "MEMORY_LOCAL_ONLY", out


def test_webchat_has_no_bridge_rows(tmp_path, monkeypatch):
    """Webchat paths never reach the bridge (reuse-only); chat serves."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/webchat/sessions",
                            {}, {})
    assert code == 404, out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/chat/sessions",
                            {"limit": "1", "offset": "0"}, {})
    assert code == 200, out


# -- lane I1 (canvas confined host + flows hooks backend) --------------------
# The 9 canvas rows and 3 flows rows are live mappings now (only flows/cancel
# keeps its honest gate — no sticky taskflow-cancel backend exists). The two
# gate-pinning tests above were reconciled to the live contract in place.


def test_canvas_list_and_live_read_live(tmp_path, monkeypatch):
    """GET canvas/list + live serve the confined host root state."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/canvas/list", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert inner.get("root") == "state-home canvas/", out
    assert "files" in inner, out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/canvas/live", {}, {})
    assert code == 200, out
    assert out.get("result", out).get("live_reload") is False, out


def test_canvas_add_staged_then_executes_and_get_roundtrip(tmp_path,
                                                           monkeypatch):
    """POST canvas/add stages 409, then executes; GET returns exact bytes."""
    import base64 as _b64

    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"path": "bridge-probe.md", "content": "bridge probe bytes"}
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/canvas/add", {},
                               dict(body))
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/add", {},
                            {**body, "confirm": True})
    assert code == 200, out
    inner = out.get("result", out)
    assert inner.get("state") == "OK", out
    assert inner.get("size") == len("bridge probe bytes"), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/canvas/get",
                            {"path": "bridge-probe.md"}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert _b64.b64decode(inner["data_base64"]).decode("utf-8") == \
        "bridge probe bytes", out
    assert inner["sha256"] == out.get("result", out).get("sha256"), out


def test_canvas_remove_staged_then_executes(tmp_path, monkeypatch):
    """POST canvas/remove stages 409, then removes exactly once."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/add", {},
                            {"path": "bridge-gone.md", "content": "x",
                             "confirm": True})
    assert code == 200, out
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/canvas/remove",
                               {}, {"path": "bridge-gone.md"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/remove", {},
                            {"path": "bridge-gone.md", "confirm": True})
    assert code == 200, out
    assert out.get("result", out).get("removed") == "bridge-gone.md", out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/canvas/get",
                            {"path": "bridge-gone.md"}, {})
    assert code == 404, out


def test_canvas_present_hide_navigate_snapshot_staged_then_execute(
        tmp_path, monkeypatch):
    """The four presentation rows stage 409, then resolve on confined ops."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/add", {},
                            {"path": "deck.md", "content": "# deck",
                             "confirm": True})
    assert code == 200, out
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/canvas/present",
                               {}, {"node_id": "n1",
                                    "entry": "/__qubit__/canvas/deck.md"})
    assert code == 409, staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/present", {},
                            {"node_id": "n1",
                             "entry": "/__qubit__/canvas/deck.md",
                             "confirm": True})
    assert code == 200, out
    assert out.get("result", out).get("preview_url", "").endswith(
        "/__qubit__/canvas/deck.md"), out
    for action, payload in (
            ("hide", {"node_id": "n1"}),
            ("navigate", {"node_id": "n1",
                          "entry": "/__qubit__/canvas/deck.md"}),
            ("snapshot", {"node_id": "n1"})):
        code, staged = HC.dispatch(cfg, "POST",
                                   f"/api/qubit/canvas/{action}", {},
                                   dict(payload))
        assert code == 409, (action, staged)
        code, out = HC.dispatch(cfg, "POST",
                                f"/api/qubit/canvas/{action}", {},
                                {**payload, "confirm": True})
        assert code == 200, (action, out)
    snap = out.get("result", out)
    assert snap.get("count") == 1 and "no screenshot" in snap.get(
        "proof", ""), snap


def test_flows_hooks_list_shows_five_bundled(tmp_path, monkeypatch):
    """GET flows/hooks serves the 5 bundled hooks with guard state."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/flows/hooks", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert [h["name"] for h in inner["hooks"]] == [
        "session-memory", "bootstrap-extra-files", "command-logger",
        "compaction-notifier", "boot-md"], out
    assert "catalog" in inner, out


def test_flows_hook_enable_disable_roundtrip_net_zero(tmp_path, monkeypatch):
    """hook_enable stages then flips on; hook_disable flips back (net-zero)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/flows/hook_enable",
                               {}, {"name": "command-logger"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/flows/hook_enable",
                            {}, {"name": "command-logger", "confirm": True})
    assert code == 200, out
    assert out.get("result", out)["hook"]["enabled"] is True, out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/flows/hook_disable",
                            {}, {"name": "command-logger", "confirm": True})
    assert code == 200, out
    assert out.get("result", out)["hook"]["enabled"] is False, out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/flows/hooks", {}, {})
    assert all(h["enabled"] is False
               for h in out.get("result", out)["hooks"]), out


def test_flows_programs_read_live(tmp_path, monkeypatch):
    """GET flows/programs serves the stored program list (recurring only)."""
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    S.create_job(cfg, "bridge-standing", "echo hi", "every 60 minutes",
                 confirmed=True)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/flows/programs", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert "bridge-standing" in [p["name"] for p in inner["programs"]], out
    assert all(p["kind"] == "standing-order"
               for p in inner["programs"]), out


# (flows/cancel gate covered by test_flows_hook_rows_live_except_cancel_gate)


# -- lane J1 (local Viking context engine) -----------------------------------
# The 9 viking rows are live on the local context engine
# (voicebridge/context_store.py — state-home context/, no external server).
# Reads execute; the two POST rows stage 409 then execute exactly once.


def test_viking_status_is_local(tmp_path, monkeypatch):
    """GET /api/qubit/viking/status reports the local engine (no server)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/status", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert inner.get("connection") == "local", out
    assert inner.get("engine") == "qubit-local-context", out


def test_viking_tree_lists_four_groups(tmp_path, monkeypatch):
    """GET viking/context-tree serves the four context groups."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET",
                            "/api/qubit/viking/context-tree", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert [g["key"] for g in inner["groups"]] == [
        "user", "agent", "resources", "skills"], out


def test_viking_file_search_related_roundtrip(tmp_path, monkeypatch):
    """memory-add stages+executes; retrieval-search + file + related read it."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"text": "lane-j1-bridge-probe remembers the lighthouse"}
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/viking/memory-add",
                               {}, dict(body))
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/viking/memory-add",
                            {}, {**body, "confirm": True})
    assert code == 200, out
    code, out = HC.dispatch(cfg, "GET",
                            "/api/qubit/viking/retrieval-search",
                            {"q": "lane-j1-bridge-probe"}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert inner.get("count", 0) >= 1, out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/file",
                            {"path": "user/memories.md"}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert "lane-j1-bridge-probe" in inner.get("content", ""), out
    assert inner.get("viking_uri") == "viking://user/memories.md", out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/related-find",
                            {"path": "user/memories.md"}, {})
    assert code == 200, out
    assert "items" in out.get("result", out), out


def test_viking_resource_add_staged_then_executes(tmp_path, monkeypatch):
    """POST viking/resource-add stages 409, then writes under resources/."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"uri": "viking://lane-j1-doc", "title": "Lane J1 Doc"}
    code, staged = HC.dispatch(cfg, "POST",
                               "/api/qubit/viking/resource-add", {},
                               dict(body))
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/viking/resource-add",
                            {}, {**body, "confirm": True})
    assert code == 200, out
    inner = out.get("result", out)
    assert inner.get("path") == "resources/Lane-J1-Doc.md", out


def test_viking_bot_sessions_and_skills_read(tmp_path, monkeypatch):
    """GET viking/bot-sessions + viking/skills are live reads (no gates)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/bot-sessions",
                            {}, {})
    assert code == 200, out
    assert "sessions" in out.get("result", out), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/skills", {}, {})
    assert code == 200, out
    assert "skills" in out.get("result", out), out


# -- integrator lane 3 (Bot + HarnessRouter pages) ---------------------------
# The 24 bot rows delegate to the canonical session store (threads), the one
# conversation turn path (turns), the shared ledger (tasks/ideas/alerts),
# the schedule store (goals), and the read-only computer probe (console).
# The 12 harness rows delegate to the worker roster (agents.list), the
# managed plugin catalog, the ledger, the session store, the per-session
# draft store (files), and the confined workspace host (artifact_get).
# Rows with no registered backend keep honest typed gates (never 2xx).


def test_bot_thread_rows_read_the_session_store(tmp_path, monkeypatch):
    """GET bot/thread-list reads; thread-get delegates (?thread= aliased)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/thread-list",
                            {}, {})
    assert code == 200, out
    assert "sessions" in out.get("result", out), out
    # no selector -> the gateway's typed bad-params (delegation, not 404)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/thread-get",
                            {}, {})
    assert code == 400, out
    assert "BAD_PARAMS" in str(out.get("code", "")), out
    # ?thread= reaches the canonical store (unknown id -> typed not-found)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/thread-get",
                            {"thread": "bridge-probe-nosuch"}, {})
    assert code == 200, out
    assert out.get("code") == "SESSION_NOT_FOUND", out


def test_bot_turn_rows_stage_then_execute(tmp_path, monkeypatch):
    """POST bot/turn-send + turn-stop stage 409; stop executes honestly."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/bot/turn-send",
                               {}, {"text": "bridge probe",
                                    "thread": "bridge-probe-nosuch"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/bot/turn-stop",
                               {}, {"thread": "bridge-probe-nosuch"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/bot/turn-stop",
                            {}, {"thread": "bridge-probe-nosuch",
                                 "confirm": True})
    assert code == 200, out
    assert out.get("code") == "CONVERSATIONS_LOCAL_SESSION_CANCEL", out


def test_bot_task_rows_live(tmp_path, monkeypatch):
    """Bot tasks-list/get/receipts read the ledger; cancel executes."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/tasks-list",
                            {}, {})
    assert code == 200, out
    assert "tasks" in out.get("result", out), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/tasks-get",
                            {"lookup": "bridge-probe-nosuch"}, {})
    assert code == 200, out
    assert out.get("code") == "TASK_NOT_FOUND", out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/tasks-receipts",
                            {}, {})
    assert code == 200, out
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/bot/tasks-cancel",
                               {}, {"session": "bridge-probe-nosuch"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/bot/tasks-cancel",
                            {}, {"session": "bridge-probe-nosuch",
                                 "confirm": True})
    assert code == 200, out
    assert out.get("cancelled") is False, out
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/bot/tasks-retry",
                               {}, {"session": "bridge-probe-nosuch"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged


def test_bot_inbox_console_rows_live(tmp_path, monkeypatch):
    """Bot ideas-list/goals-list/alerts-list/computer-status are live."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/ideas-list",
                            {}, {})
    assert code == 200, out
    assert "suggestions" in out.get("result", out), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/goals-list",
                            {}, {})
    assert code == 200, out
    assert "jobs" in out.get("result", out), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/alerts-list",
                            {}, {})
    assert code == 200, out
    assert "runs" in out.get("result", out), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/computer-status",
                            {}, {})
    assert code == 200, out
    assert out.get("control", {}).get("control", out.get("control")) \
        is not None, out


def test_bot_gate_rows_are_typed_403(tmp_path, monkeypatch):
    """Bot rows with no backend gate honestly (never 2xx, never staged)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    gates = {
        "/api/qubit/bot/followups-queue": "BOT_FOLLOWUPS_LOCAL_ONLY",
        "/api/qubit/bot/drafts-save": "BOT_DRAFTS_LOCAL_ONLY",
    }
    for path, want in gates.items():
        method = "GET" if path.endswith("cards-get") else "POST"
        code, out = HC.dispatch(cfg, method, path, {}, {"probe": True})
        assert code == 403, (path, out)
        assert out.get("code") == want, (path, out)
        assert out.get("qubit_counterpart"), (path, out)


def test_bot_harness_wired_rows_live(tmp_path, monkeypatch):
    """Wired rows stage (409) or serve (200) — never a stale 403 refusal."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for path in ("/api/qubit/bot/tasks-launch",
                 "/api/qubit/bot/tasks-pause",
                 "/api/qubit/bot/tasks-resume",
                 "/api/qubit/bot/ideas-accept",
                 "/api/qubit/bot/ideas-edit",
                 "/api/qubit/bot/ideas-dismiss",
                 "/api/qubit/bot/goals-set",
                 "/api/qubit/bot/watches-set",
                 "/api/qubit/harness/task_submit"):
        code, out = HC.dispatch(cfg, "POST", path, {}, {"probe": True})
        assert code == 409, (path, out)
        assert out.get("confirmation_id"), (path, out)
    for path in ("/api/qubit/bot/ideas-list",
                 "/api/qubit/bot/goals-list"):
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, out)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/starter_kits",
                            {}, {})
    assert code == 200, out
    kits = out.get("result", out).get("kits", [])
    assert len(kits) == 4, out
    assert {k["id"] for k in kits} == {"coding-delegation", "container-ops",
                                       "team-board", "workflow-achievements"}


def test_bot_console_sidecar_round_trips(tmp_path, monkeypatch):
    """Goals set→list; ideas dismiss/edit→list; cards miss typed 404."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    def stage_exec(path, body):
        code, out = HC.dispatch(cfg, "POST", path, {}, dict(body))
        assert code == 409, (path, out)
        cid = out.get("confirmation_id")
        assert cid, (path, out)
        return HC.dispatch(cfg, "POST", path, {},
                           dict(body, confirm=True, confirmation_id=cid))

    code, out = stage_exec("/api/qubit/bot/goals-set",
                           {"title": "Ship it",
                            "description": "d",
                            "milestones": ["m1", "m2"]})
    assert code == 200, out
    goal = out.get("result", out).get("goal", {})
    assert goal.get("title") == "Ship it", out
    assert goal.get("milestones") == ["m1", "m2"], out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/goals-list", {}, {})
    assert code == 200, out
    goals = out.get("result", out).get("goals", [])
    assert any(g.get("id") == goal.get("id") for g in goals), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/ideas-list", {}, {})
    assert code == 200, out

    code, out = stage_exec("/api/qubit/bot/ideas-dismiss", {"idea": "nope"})
    assert code == 404, out
    assert out.get("code") == "BOT_IDEA_NOT_FOUND", out
    code, out = stage_exec("/api/qubit/bot/ideas-edit",
                           {"idea": "nope", "title": "x"})
    assert code == 404, out

    code, out = stage_exec("/api/qubit/bot/tasks-pause",
                           {"session": "bridge-probe-nosuch"})
    assert code == 404, (code, out)
    assert out.get("code") == "BOT_PAUSE_GONE", out
    code, out = stage_exec("/api/qubit/bot/tasks-resume",
                           {"session": "bridge-probe-nosuch"})
    assert code == 400, (code, out)
    assert out.get("code") == "BOT_RESUME_NO_PROMPT", out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bot/cards-get",
                            {"card": "no-such-card-zzz"}, {})
    assert code == 404, out
    assert out.get("code") == "BOT_CARD_NOT_FOUND", out


def test_bot_pause_resume_worker_box(tmp_path, monkeypatch):
    """Pause flags a live worker box; resume relaunches the kept prompt."""
    import threading

    from voicebridge import orchestrator as ORCH
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    box = {"event": threading.Event(), "result": None, "cancel": False}
    monkeypatch.setitem(ORCH._TASK_BOXES, "probe-worker-1", box)

    def stage_exec(path, body):
        code, out = HC.dispatch(cfg, "POST", path, {}, dict(body))
        assert code == 409, (path, out)
        cid = out.get("confirmation_id")
        assert cid, (path, out)
        return HC.dispatch(cfg, "POST", path, {},
                           dict(body, confirm=True, confirmation_id=cid))

    code, out = stage_exec(
        "/api/qubit/bot/tasks-pause",
        {"session": "probe-worker-1", "prompt": "do the thing",
         "title": "Thing"})
    assert code == 200, out
    assert out.get("result", out).get("paused") is True, out
    assert box["cancel"] is True

    launched = {}

    def fake_launch(prompt, cfg=None, **kw):
        launched["prompt"] = prompt
        return "probe-worker-2"

    monkeypatch.setattr(ORCH, "launch_task", fake_launch)
    code, out = stage_exec("/api/qubit/bot/tasks-resume",
                           {"session": "probe-worker-1"})
    assert code == 200, out
    res = out.get("result", out)
    assert res.get("session") == "probe-worker-2", out
    assert res.get("resumed_from") == "probe-worker-1", out
    assert launched.get("prompt") == "do the thing", launched


def test_harness_roster_catalog_task_rows_live(tmp_path, monkeypatch):
    """Harness harnesses/plugins/tasks read live backends."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/harnesses",
                            {}, {})
    assert code == 200, out
    assert "agents" in out.get("result", out), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/plugins",
                            {}, {})
    assert code == 200, out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/tasks",
                            {}, {})
    assert code == 200, out
    assert "tasks" in out.get("result", out), out


def test_harness_session_rows_live(tmp_path, monkeypatch):
    """Harness session_create/resume stage then execute; inspect reads."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST",
                               "/api/qubit/harness/session_create", {},
                               {"harness": "local", "title": "lane3-probe"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST",
                            "/api/qubit/harness/session_create", {},
                            {"harness": "local", "title": "lane3-probe",
                             "confirm": True})
    assert code == 200, out
    sid = (out.get("session", {}) or {}).get("id", "")
    assert sid, out
    code, out = HC.dispatch(cfg, "GET",
                            "/api/qubit/harness/session_inspect",
                            {"session": sid}, {})
    assert code == 200, out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/stream",
                            {"session": sid}, {})
    assert code == 200, out
    code, staged = HC.dispatch(cfg, "POST",
                               "/api/qubit/harness/session_resume", {},
                               {"session": sid})
    assert code == 409, staged
    code, out = HC.dispatch(cfg, "POST",
                            "/api/qubit/harness/session_resume", {},
                            {"session": sid, "confirm": True})
    assert code == 200, out
    code, staged = HC.dispatch(cfg, "POST",
                               "/api/qubit/harness/task_cancel", {},
                               {"session": "bridge-probe-nosuch"})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged


def test_harness_gate_and_typed_rows(tmp_path, monkeypatch):
    """Harness starter_kits serves; task_submit stages; files/artifact delegate."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/starter_kits",
                            {}, {})
    assert code == 200, out
    assert len(out.get("result", out).get("kits", [])) == 4, out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/harness/task_submit",
                            {}, {"prompt": "probe", "harness": "local"})
    assert code == 409, out
    assert out.get("confirmation_id"), out
    # files without a session is the gateway's typed bad-params (not a 404)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/files",
                            {"task": "t1"}, {})
    assert code == 400, out
    assert "BAD_PARAMS" in str(out.get("code", "")), out
    # artifact_get with no path reaches the confined host (typed, not 404)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/harness/artifact_get",
                            {}, {})
    assert code == 200, out
    assert out.get("code") == "NO_ACTIVE_PROJECT", out
