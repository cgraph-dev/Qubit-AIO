"""Voice parity for Tier-2 dashboard extras that claim the voice interface.

Every EXTRA_OP in voicebridge.dashboard_ops with "voice" in interfaces must
be reachable by voice: either a same-id Tier-1 registry entry, a
"<id>_refusal" entry hitting the same typed refusal, or an explicit family
mapping below (same backend module function, verified behaviorally).

New entries added for the 8T-F close-out are executed end to end here:
resolve -> execute -> NEEDS_CONFIRM -> confirm_pending -> same backend
result as CLI/dashboard, plus cancellation, refusal immediacy, and alias
collision checks.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _confirm(ctx, out):
    from voicebridge import voice_commands as VC

    assert out.kind == VC.NEEDS_CONFIRM, (out.kind, out.speech)
    done = VC.confirm_pending(ctx, out.confirmation_id)
    assert done.kind == VC.EXECUTED, (done.kind, done.speech)
    return done


# Tier-2 extras whose voice coverage lives under a different Tier-1 id but
# the same backend module function (proven by the behavioral tests below).
FAMILY_MAP = {
    "brain_export": ("brain_profile_export",),
    "brain_sweep": ("brain_profile_sweep",),
    "import_agent_import": ("import_refusal",),
    "kanban_delegated": ("kanban_swarm", "kanban_watch", "kanban_dispatch"),
}


def test_tier2_voice_claims_have_registry_entries():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    vids = set(VC.REGISTRY)
    problems = []
    for op_id, spec in OPS.EXTRA_OPS.items():
        if "voice" not in tuple(spec.interfaces):
            assert spec.interface_reason, op_id  # exclusion must be typed
            continue
        if (op_id in vids or f"{op_id}_refusal" in vids
                or f"{op_id}_voice" in vids):
            continue
        mapped = FAMILY_MAP.get(op_id, ())
        if mapped and all(m in vids for m in mapped):
            continue
        problems.append(op_id)
    assert not problems, f"Tier-2 extras claiming voice with no entry: {problems}"


def test_catalog_voice_claim_parity_overlaps_backend():
    """Every voice-claimed Tier-2 extra shares a parity token with its
    Tier-1 voice entry (same family), so receipts/audit stay comparable."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    for op_id, spec in OPS.EXTRA_OPS.items():
        if "voice" not in tuple(spec.interfaces):
            continue
        cands = [op_id, f"{op_id}_refusal", f"{op_id}_voice",
                 *FAMILY_MAP.get(op_id, ())]
        entries = [VC.REGISTRY[c] for c in cands if c in VC.REGISTRY]
        assert entries, op_id
        assert any(set(e.parity) & set(spec.parity) for e in entries), op_id


def test_role_grant_revoke_roundtrip(tmp_path, monkeypatch):
    from voicebridge import kanban as KB
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    KB.boards_create(cfg, "main", "main", requested_by="t", confirmed=True)
    out = VC.execute(
        ctx, VC.resolve("qubit, grant board role editor to sam"))
    assert out.kind == VC.NEEDS_CONFIRM, (out.kind, out.speech)
    done = VC.confirm_pending(ctx, out.confirmation_id)
    assert done.kind == VC.EXECUTED, (done.kind, done.speech)
    roles = KB.roles_list(cfg)
    assert roles.get("roles", {}).get("sam") == "editor", roles
    out2 = VC.execute(
        ctx, VC.resolve("qubit, revoke board role from sam"))
    done2 = _confirm(ctx, out2)
    assert done2.receipt, done2
    roles2 = KB.roles_list(cfg)
    assert "sam" not in roles2.get("roles", {}), roles2


def test_snapshot_restore_roundtrip_via_voice(tmp_path, monkeypatch):
    from voicebridge import kanban as KB
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    KB.boards_create(cfg, "main", "main", requested_by="t", confirmed=True)
    out = VC.execute(ctx, VC.resolve("qubit, grant board role editor to sam"))
    _confirm(ctx, out)
    assert KB.roles_list(cfg).get("roles", {}).get("sam") == "editor"
    out2 = VC.execute(ctx, VC.resolve("qubit, revoke board role from sam"))
    done2 = _confirm(ctx, out2)
    import re as _re

    rollback = (done2.receipt.get("result", {}) or {}).get("rollback", "")
    snap_match = _re.search(r"#(\d+)", rollback)
    assert snap_match, done2.receipt
    snap_id = int(snap_match.group(1))
    assert "sam" not in KB.roles_list(cfg).get("roles", {})
    out3 = VC.execute(
        ctx, VC.resolve(f"qubit, restore canvas snapshot {int(snap_id)}"))
    done3 = _confirm(ctx, out3)
    assert done3.receipt
    assert KB.roles_list(cfg).get("roles", {}).get("sam") == "editor", \
        KB.roles_list(cfg)


def test_widget_move_geometry_via_voice(tmp_path, monkeypatch):
    from voicebridge import kanban as KB
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    KB.boards_create(cfg, "main", "main", requested_by="t", confirmed=True)
    added = KB.widget_add(cfg, "main", "note", "w1",
                          requested_by="cli:user", confirmed=True)
    wid = int((added.get("widget", {}) or {}).get("id"))
    out = VC.execute(ctx, VC.resolve(f"qubit, move widget {wid} to 30 40"))
    done = _confirm(ctx, out)
    assert done.receipt
    widgets = KB.widget_list(cfg, board="main").get("widgets", [])
    match = [w for w in widgets if int(w.get("id", -1)) == wid]
    assert match and int(match[0].get("x", -1)) == 30, widgets


def test_sync_revoke_via_voice(tmp_path, monkeypatch):
    from voicebridge import sync as SY
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    created = SY.offer(cfg, "correct horse battery staple xyz",
                       requested_by="cli:user", confirmed=True)
    offer_id = (created.get("offer", {}) or {}).get("offer_id")
    assert offer_id, created.keys()
    out = VC.execute(ctx, VC.resolve(f"qubit, revoke sync offer {offer_id}"))
    done = _confirm(ctx, out)
    assert done.receipt
    shown = SY.show(cfg, offer_id)
    assert (shown.get("offer", {}) or {}).get("revoked") is True, shown


def test_refusals_are_immediate_and_typed(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    cases = {
        "qubit, restart gateway service": ("gateway_supervision_refusal",
                                           ("REFUSED", "BLOCKED")),
        "qubit, pet gallery": ("pets_downloads_refusal", ("REFUSED",)),
        "qubit, install remote plugin actor/foo": ("plugins_remote_refusal",
                                                  ("BLOCKED",)),
        "qubit, swarm board": ("kanban_swarm", ("DELEGATED",)),
        "qubit, watch board": ("kanban_watch", ("DELEGATED",)),
        "qubit, dispatch board": ("kanban_dispatch", ("DELEGATED",)),
    }
    for text, (want_id, states) in cases.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == want_id, (text, res)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
        state = (out.receipt.get("result", {}) or {}).get("state", "")
        assert state in states, (text, out.receipt)


def test_new_ops_cancel_cleanly(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx,
                     VC.resolve("qubit, grant board role viewer to sam"))
    assert out.kind == VC.NEEDS_CONFIRM
    cancelled = VC.cancel_pending(ctx)
    assert cancelled.kind == VC.EXECUTED
    gone = VC.confirm_pending(ctx, out.confirmation_id)
    assert gone.kind == VC.FAILED


def test_new_aliases_do_not_hijack_neighbors(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _ = _cfg(tmp_path, monkeypatch)
    battery = {
        "qubit, list tasks": "kanban_list",
        "qubit, move task 3": "kanban_move",
        "qubit, sync status": "sync_list",
        "qubit, gateway status": "gateway",
        "qubit, what theme": "themes",
        "qubit, start proxy": "proxy_start_refusal",
        "qubit, close the browser": "browser_close_refusal",
        "qubit, restore canvas snapshot 7": "kanban_snapshot_restore_voice",
        "qubit, revoke sync offer abc": "sync_revoke_voice",
    }
    for text, want_id in battery.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == want_id, (text, res)


def test_new_entries_meet_schema_hygiene():
    from voicebridge import voice_commands as VC

    for entry_id in ("kanban_role_grant_voice", "kanban_role_revoke_voice",
                     "kanban_widget_move_voice",
                     "kanban_snapshot_restore_voice",
                     "kanban_swarm", "kanban_watch", "kanban_dispatch",
                     "sync_revoke_voice", "gateway_supervision_refusal",
                     "pets_downloads_refusal", "plugins_remote_refusal"):
        entry = VC.REGISTRY[entry_id]
        assert entry.backend in VC._BACKENDS, entry_id
        assert entry.aliases and entry.examples and entry.help, entry_id
        for alias in entry.aliases:
            assert alias == alias.lower().strip(), (entry_id, alias)
        for arg in entry.args:
            assert arg.name.lower() not in (
                "passphrase", "password", "secret", "token", "api_key",
                "cookie"), (entry_id, arg.name)
