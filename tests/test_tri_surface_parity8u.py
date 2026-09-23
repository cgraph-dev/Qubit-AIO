"""Tri-surface parity harness: CLI / dashboard / voice -> one canonical op.

Proves the single-operation contract for a representative read + write +
confirm + cancel + recovery + provider-selection + voice battery:

* CLI ``qubit ...`` (``cli._it_run_op`` -> ``dashboard_ops.run_op``),
* dashboard in-process canonical op (``dashboard_ops.run_op`` /
  ``run_registry_op`` / ``confirm_op`` / ``cancel_op``),
* voice (``voice_commands`` registry ``resolve`` -> the same
  ``dashboard_ops.run_op``),

all resolve to the *same* canonical operation ``(op_id, args, backend)`` and
share the one ``approvals.jsonl`` journal and ``events.jsonl`` audit receipt.

The engine lives in ``scripts/tri_surface_parity.py``; these tests are the
enforcement of its output.  Everything runs against an isolated HOME/XDG/state
tree (``make_env``) and never touches production state.

Deliberate CLI native adapters (``sessions``/``kanban``/``autonomy``) and the
voice guard entries are pinned as ``SAFE_EQUIVALENT`` rows with their
``file:line`` provenance, so a *new* divergence fails the ratchet instead of
hiding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import tri_surface_parity as T  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    cfgfile, cfg = T.make_env(tmp_path, monkeypatch=monkeypatch)
    rec = T.Recorder().install()
    try:
        yield cfgfile, cfg, rec, T.Surfaces(cfg, cfgfile, rec)
    finally:
        rec.uninstall()


def _result(out):
    if not isinstance(out, dict):
        return None
    receipt = out.get("receipt") or {}
    return receipt.get("result")


# ---------------------------------------------------------------- reads
@pytest.mark.parametrize("category,op_id,argv,dargs,voice_text",
                         T.EXECUTE_BATTERY)
def test_every_battery_op_resolves_to_one_canonical_operation(
        harness, category, op_id, argv, dargs, voice_text):
    """The three surfaces emit the identical (op_id, args, backend)."""
    _cfgfile, cfg, rec, surf = harness
    rows = T.build_execute_matrix(surf, rec)
    row = next(r for r in rows if r.op_id == op_id)
    assert row.verdict == T.PASS, row.detail
    identities = row.identity_strings()
    assert set(identities) == {"cli", "dashboard", "voice"}, identities
    assert len(set(identities.values())) == 1, identities
    identity = next(iter(identities.values()))
    assert identity[0] == op_id
    assert identity[2], f"op {op_id} has no backend"

    from voicebridge import voice_commands as VC

    assert identity[2] in VC._BACKENDS, identity[2]


@pytest.mark.parametrize("op_id,argv,dargs,voice_text", (
    ("tools", ("tools",), {}, "list tools"),
    ("reports_list", ("reports", "list"), {}, "list reports"),
    ("prompt_budget", ("prompt-budget",), {}, "prompt budget"),
))
def test_read_results_are_identical_across_surfaces(
        harness, op_id, argv, dargs, voice_text):
    """Same op, same arguments, same backend, same scrubbed result payload."""
    _cfgfile, cfg, rec, surf = harness
    _rc, cli_out = surf.cli(*argv)
    dash_out = surf.dash(op_id, dargs, session="parity")
    voice_out = surf.voice(voice_text, session="parity")

    cli_result = T.scrub(_result(cli_out))
    dash_result = T.scrub(_result(dash_out))
    voice_result = T.scrub(_result(voice_out))
    assert cli_result == dash_result, (op_id, cli_result, dash_result)
    assert cli_result == voice_result, (op_id, cli_result, voice_result)


# ------------------------------------------------- write + confirm
def test_write_confirmation_stages_identically_and_settles_across_surfaces(
        harness):
    """One journal: each surface stages, a *different* surface settles."""
    _cfgfile, cfg, rec, surf = harness

    # Stage the same write on all three surfaces.
    rc, cli_out = surf.cli("memory", "remember", "tri parity fact")
    assert rc == 3 and cli_out["state"] == "needs-confirm"
    dash_out = surf.dash("memory_remember", {"text": "tri parity fact"},
                         session="dash-sess")
    voice_out = surf.voice("remember tri parity fact", session="voice-sess")
    assert dash_out["state"] == "needs-confirm"
    assert voice_out["state"] == "needs-confirm"

    staged = {
        "cli": rec.by_surface("execute", "memory_remember")["cli"],
        "dashboard": rec.by_surface("execute", "memory_remember")["dashboard"],
        "voice": rec.by_surface("execute", "memory_remember")["voice"],
    }
    assert len({e.identity() for e in staged.values()}) == 1, staged

    # Settle each staged confirmation on a different surface than it came from.
    cli_id = cli_out["confirmation_id"]
    dash_id = dash_out["confirmation_id"]
    voice_id = voice_out["confirmation_id"]

    settled = surf.confirm_dash(cli_id, session="cli")
    assert settled["state"] == "executed", settled
    rc, out = surf.confirm_cli(dash_id)
    assert rc == 0 and out["state"] == "executed", (rc, out)
    outcome = surf.confirm_voice(voice_id, session="voice-sess")
    assert outcome.kind == "executed", outcome.kind

    # All three settle events land in the one audit journal.
    audit = T.read_audit(cfg)
    confirmed = {e.get("confirmation_id"): e.get("actor")
                 for e in audit if e.get("event") == "voice.confirmed_executed"}
    assert confirmed.get(cli_id) == "dashboard:user", confirmed
    assert confirmed.get(dash_id) == "cli:user", confirmed
    assert confirmed.get(voice_id) == "voice:user", confirmed

    # The write is persisted once and is readable from the other surfaces.
    dash_read = surf.dash("memory_search", {"query": "tri parity fact"},
                          session="parity")
    voice_read = surf.voice("what do you remember about tri parity fact",
                            session="parity")
    dash_texts = sorted(r["text"] for r in _result(dash_read)["results"])
    voice_texts = sorted(r["text"] for r in _result(voice_read)["results"])
    assert dash_texts == voice_texts, (dash_texts, voice_texts)
    assert "tri parity fact" in dash_texts, dash_texts


def test_confirmation_replay_is_settle_once(harness):
    """The shared journal refuses a second settle of a spent confirmation."""
    _cfgfile, cfg, rec, surf = harness
    _rc, cli_out = surf.cli("memory", "remember", "one shot")
    cid = cli_out["confirmation_id"]

    first = surf.confirm_dash(cid, session="cli")
    assert first["state"] == "executed", first
    replay = surf.confirm_dash(cid, session="cli")
    assert replay["state"] == "failed", replay
    assert replay.get("code") == "NO_CONFIRMATION", replay


# ---------------------------------------------------------------- cancel
def test_cancel_settles_the_shared_journal_from_another_surface(harness):
    _cfgfile, cfg, rec, surf = harness

    dash_staged = surf.dash("memory_remember", {"text": "cancel dash"},
                            session="dash-sess")
    voice_staged = surf.voice("remember cancel voice", session="voice-sess")
    _rc, cli_staged = surf.cli("memory", "remember", "cancel cli")

    # dashboard-staged cancelled by the CLI's deny path.
    rc, out = surf.cancel_cli(dash_staged["confirmation_id"])
    assert rc == 0 and out["state"] == "executed", (rc, out)
    assert out["receipt"]["denied"] == dash_staged["confirmation_id"]

    # voice-staged cancelled by voice.
    outcome = surf.cancel_voice(voice_staged["confirmation_id"],
                                session="voice-sess")
    assert outcome.kind == "executed", outcome.kind

    # cli-staged cancelled by dashboard.
    settled = surf.cancel_dash(cli_staged["confirmation_id"], session="cli")
    assert settled["state"] == "executed", settled

    from voicebridge import voice_commands as VC

    for session in ("dash-sess", "voice-sess", "cli"):
        ctx = VC.make_ctx(cfg, actor="voice:user", session=session,
                          source="user")
        assert not VC._pending_for(ctx, VC._journal(cfg)), session

    # Cancellation never executed the backend.
    audit = T.read_audit(cfg)
    assert any(e.get("event") == "voice.cancelled" for e in audit)
    cancelled_ids = {e.get("confirmation_id") for e in audit
                     if e.get("event") == "voice.confirmed_executed"}
    for cid in (dash_staged["confirmation_id"],
                voice_staged["confirmation_id"],
                cli_staged["confirmation_id"]):
        assert cid not in cancelled_ids, cid


# -------------------------------------------------------------- recovery
def test_recovery_op_resolves_to_one_canonical_operation(harness):
    """`ghost_action_recover` is the same canonical op on all three surfaces."""
    _cfgfile, cfg, rec, surf = harness
    rc, cli_out = surf.cli("ghost", "action-recover", "r1")
    dash_out = surf.dash("ghost_action_recover", {"receipt_id": "r1"},
                         session="dash-sess")
    voice_out = surf.voice("recover ghost action r1", session="voice-sess")

    assert rc == 3 and cli_out["state"] == "needs-confirm"
    assert dash_out["state"] == "needs-confirm"
    assert voice_out["state"] == "needs-confirm"

    by = rec.by_surface("execute", "ghost_action_recover")
    identities = {s: e.identity() for s, e in by.items()}
    assert set(identities) == {"cli", "dashboard", "voice"}, identities
    assert len(set(identities.values())) == 1, identities

    # A recovery staged by one surface is settleable by another.
    settled = surf.confirm_dash(voice_out["confirmation_id"],
                                session="voice-sess")
    assert settled["state"] in ("executed", "failed"), settled
    assert settled.get("receipt", {}).get("entry") == "ghost_action_recover"


# ----------------------------------------------------- provider selection
def test_provider_selection_uses_one_resolver(harness):
    """CLI / dashboard / voice all select providers through the resolver."""
    _cfgfile, cfg, rec, surf = harness
    spy = T.ResolverSpy(rec).install()
    try:
        calls, bindings = T.probe_provider_selection(surf, rec, spy)
    finally:
        spy.uninstall()

    assert len(calls) == 3, calls
    by_surface = {c[0]: c[1:] for c in calls}
    assert set(by_surface) == {"cli", "dashboard", "voice"}, by_surface
    assert len(set(by_surface.values())) == 1, by_surface
    session, profile_id, role, confirm = by_surface["cli"]
    assert session == "sess1" and profile_id and role == "chat"
    assert confirm is True

    # The registry binding the voice surface uses is the guarded profile op.
    assert bindings["voice"] == frozenset({"model_profile_local"})


# ----------------------------------------------------------- audit/state
def test_audit_receipt_is_one_shared_journal(harness):
    """Every surface writes its receipt to the same events.jsonl."""
    _cfgfile, cfg, rec, surf = harness
    audit_path = Path(cfg.state_dir()) / "events.jsonl"

    surf.cli("tools")
    surf.dash("tools", {}, session="dash")
    surf.voice("list tools", session="voice")

    audit = T.read_audit(cfg)
    assert audit_path.exists()
    actors = {e.get("actor") for e in audit if e.get("event") == "voice.executed"}
    assert {"cli:user", "dashboard:user", "voice:user"} <= actors, actors
    entries = {e.get("entry") for e in audit if e.get("event") == "voice.executed"}
    assert "tools" in entries, entries


# ---------------------------------------------------- pinned divergences
def test_safe_equivalent_adapters_match_the_pinned_bindings(harness):
    """Native CLI adapters/voice guards are reported, and cannot drift."""
    _cfgfile, cfg, rec, surf = harness
    rows = T.build_safe_equivalent_matrix(surf, rec)
    assert rows, "no safe-equivalent probes ran"
    for row in rows:
        assert row.verdict == T.SAFE_EQUIVALENT, (row.op_id, row.detail)
        assert row.provenance.count(":") >= 1, row.provenance
    assert {row.op_id for row in rows} == set(T.SAFE_EQUIVALENT_EXPECTED)


def test_matrix_reports_every_category(harness):
    """The per-op matrix covers read + write + recovery + voice."""
    _cfgfile, cfg, rec, surf = harness
    rows = T.build_execute_matrix(surf, rec)
    categories = {r.category for r in rows}
    assert {"read", "write", "recovery", "voice"} <= categories, categories
    assert all(r.verdict == T.PASS for r in rows), \
        [(r.op_id, r.detail) for r in rows if r.verdict != T.PASS]
