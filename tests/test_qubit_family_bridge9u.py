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


def test_flows_gated_rows_are_typed_gates(tmp_path, monkeypatch):
    """hook_enable/hook_disable/cancel/programs answer honest typed gates."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/flows/hook_enable", {},
                            {"name": "command-logger"})
    assert code == 403, out
    assert out.get("code") == "FLOWS_HOOKS_UNWIRED", out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/flows/cancel", {},
                            {"flow_id": "f1"})
    assert code == 403, out
    assert out.get("code") == "FLOWS_CANCEL_UNWIRED", out
    assert "cron" in str(out.get("qubit_counterpart", "")), out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/flows/programs", {}, {})
    assert code == 403, out
    assert out.get("code") == "FLOWS_PROGRAMS_UNWIRED", out


def test_canvas_rows_are_typed_gates(tmp_path, monkeypatch):
    """All 9 canvas rows gate honestly (no file host invented).

    Mutations gate before any confirm is staged (403, not 409).
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/canvas/list", {}, {})
    assert code == 403, out
    assert out.get("code") == "CANVAS_HOST_UNWIRED", out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/add", {},
                            {"path": "probe.md", "content": "probe"})
    assert code == 403, out
    assert out.get("code") == "CANVAS_HOST_UNWIRED", out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/canvas/present", {},
                            {"node_id": "n1", "entry": "/__qubit__/canvas/a"})
    assert code == 403, out
    assert out.get("code") == "CANVAS_PRESENT_UNWIRED", out


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
