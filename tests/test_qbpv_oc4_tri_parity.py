"""QBPV Agent 4 (E) — CLI / dashboard / voice tri-parity.

Proves that the three surfaces reach the *same* backend operation with
equivalent arguments and results, and share the one confirmation/cancellation
journal (no private execution path, no fabricated success). It exercises the
real production callers: ``voicebridge.cli.main``, ``dashboard_ops.run_op`` and
the voice registry, not mocks.

Also pins the catalog-honesty invariant the F3 wave established: an op may only
declare a ``voice`` interface when a real spoken registry entry exists (directly
or through its declared/conventional guard); a no-voice op must name a real
guard entry.
"""
from __future__ import annotations

import contextlib
import io
import json

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _dash(cfg, op_id, args=None, session="dashboard"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session=session)


def _say(cfg, text, session="voice"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _cli_json(*argv):
    from voicebridge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, json.loads(buf.getvalue())


# ------------------------------------------------------- catalog honesty
def test_catalog_voice_ops_have_a_real_voice_entry():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    registry = set(VC.REGISTRY)
    gaps = []
    for item in OPS.catalog():
        if "voice" not in item["interfaces"]:
            continue
        op_id = item["id"]
        guard = item.get("voice_guard") or ""
        linked = (op_id in registry
                  or (guard and guard in registry)
                  or f"{op_id}_refusal" in registry
                  or f"{op_id}_voice" in registry)
        if not linked:
            gaps.append((op_id, guard))
    assert gaps == [], gaps


def test_no_voice_ops_name_a_real_guard():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    registry = set(VC.REGISTRY)
    gaps = []
    for item in OPS.catalog():
        if "voice" in item["interfaces"]:
            continue
        op_id = item["id"]
        candidates = (item.get("voice_guard")
                      or "", f"{op_id}_refusal", f"{op_id}_voice",
                      f"guard_{op_id}")
        if not item.get("interface_reason"):
            gaps.append((op_id, "no interface_reason"))
        elif not any(c and c in registry for c in candidates):
            gaps.append((op_id, candidates))
    assert gaps == [], gaps


# ------------------------------------------------------- behavior parity
def test_reports_list_tri_parity_same_backend_result(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    dash = _dash(cfg, "reports_list")
    assert dash["state"] == "executed", dash
    dash_result = dash["receipt"]["result"]

    said = _say(cfg, "qubit, list reports")
    assert said["state"] == "executed", said
    assert said["receipt"]["result"] == dash_result

    rc, cli = _cli_json("reports", "list")
    assert rc == 0, (rc, cli)
    assert cli["receipt"]["result"] == dash_result
    assert cli["state"] == dash["state"]


# Genuinely live hardware gauges that can move between two reads of the SAME
# backend operation (they are sampled from the host, not from Qubit state).
# Only these named fields may differ across surfaces; every other field is a
# substantive result and must match exactly.
_LIVE_METRIC_KEYS = frozenset({"free_vram_mib"})


def _strip_live_metrics(value):
    if isinstance(value, dict):
        return {k: _strip_live_metrics(v) for k, v in value.items()
                if k not in _LIVE_METRIC_KEYS}
    if isinstance(value, list):
        return [_strip_live_metrics(v) for v in value]
    return value


def _key_shape(value):
    """The keys/containers of a payload, ignoring the leaf values."""
    if isinstance(value, dict):
        return {k: _key_shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_key_shape(v) for v in value]
    return None


def test_status_read_tri_parity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    dash = _dash(cfg, "status")
    said = _say(cfg, "qubit, system status")
    assert dash["state"] == "executed" and said["state"] == "executed"
    dash_result = dash["receipt"]["result"]
    said_result = said["receipt"]["result"]
    # Same schema on both surfaces: no subtree/key is dropped or renamed.
    assert _key_shape(said_result) == _key_shape(dash_result)
    # The shared backend result is identical on both surfaces for every
    # substantive field; only the named live hardware gauge may drift.
    assert _strip_live_metrics(said_result) == \
        _strip_live_metrics(dash_result)


def test_confirm_lifecycle_shared_between_dashboard_and_voice(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    # Stage on the dashboard; settle from voice (shared one journal).
    staged = _dash(cfg, "kanban_create", {"title": "oc4 tri parity"},
                   session="tri")
    assert staged["state"] == "needs-confirm", staged
    cid = staged["confirmation_id"]
    assert cid

    from voicebridge import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="voice:user", session="tri", source="user")
    done = VC.confirm_pending(ctx, cid)
    assert done.kind == "executed", (done.kind, done.speech)
    titles = [t.get("title") for t in KB.list_tasks(cfg, board="main")
              .get("tasks", [])]
    assert "oc4 tri parity" in titles, titles

    # Replay: the same id is gone (single-use), a typed failure not success.
    replay = OPS.confirm_op(cfg, cid, actor="voice:user", session="tri")
    assert replay["state"] == "failed", replay
    assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE"), replay


def test_cancel_from_dashboard_settles_voice_staged_confirmation(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, create task cancel me", session="tri-c")
    assert said["state"] == "needs-confirm", said
    cid = said["confirmation_id"]
    assert cid

    cancelled = OPS.cancel_op(cfg, cid, actor="dashboard:user",
                              session="tri-c")
    assert cancelled["state"] == "executed", cancelled
    gone = OPS.confirm_op(cfg, cid, actor="voice:user", session="tri-c")
    assert gone["state"] == "failed", gone


def test_cli_integration_ops_are_real_catalog_ops():
    from voicebridge import cli
    from voicebridge import dashboard_ops as OPS

    catalog = {o["id"] for o in OPS.catalog()}
    op_ids = {op for (_fam, _sub, op, _help, _specs) in cli._OP_CLI}
    assert op_ids, "no catalog-op CLI bindings declared"
    assert op_ids <= catalog, sorted(op_ids - catalog)


def _codes(obj, out=None):
    """Collect every ``code`` value in a possibly nested result."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        code = obj.get("code")
        if isinstance(code, str) and code:
            out.append(code)
        for value in obj.values():
            _codes(value, out)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _codes(value, out)
    return out


def test_extra_refusal_runs_on_all_declared_surfaces(tmp_path, monkeypatch):
    """A typed refusal extra is refused with the same code on both surfaces."""
    cfg = _cfg(tmp_path, monkeypatch)
    dash = _dash(cfg, "proxy_start")
    assert dash["state"] == "executed", dash
    said = _say(cfg, "qubit, start proxy")
    assert said["state"] == "executed", said
    assert "PROXY_NO_SERVER" in _codes(dash["receipt"]["result"]), dash
    assert "PROXY_NO_SERVER" in _codes(said["receipt"]["result"]), said
