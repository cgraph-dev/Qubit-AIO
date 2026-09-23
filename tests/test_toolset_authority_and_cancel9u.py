"""Regression: toolset enable authority agreement + staged-toolset cancel.

D-1 (HIGH, false success): enabling a platform-disabled toolset returned
``{ok: true, state: executed, already: true}`` while the list kept reporting
``enabled: false``, because the mutation authority defaulted a missing record to
enabled while ``h_toolsets`` served the vendored platform-derived state.

D-2 (MED): a staged ``toolset.mutation`` could not be cancelled -- ``POST
/api/ops/cancel`` answered ``NOTHING_PENDING`` and the journal entry stayed
``in_progress``.

Hermetic: tmp XDG state only; no network.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _read_rows(cfg):
    from voicebridge import qubit_compat as QC

    _code, rows = QC.h_toolsets(cfg, {}, {}, {})
    return {row["name"]: row["enabled"] for row in rows}


def test_enabling_list_disabled_toolset_makes_read_path_report_enabled(
        tmp_path, monkeypatch):
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    before = _read_rows(cfg)
    disabled = [name for name, on in before.items() if not on]
    assert disabled, "expected at least one platform-disabled toolset"
    name = disabled[0]

    # The mutation authority must agree with the read authority.
    assert TS.effective_toolset_enabled(cfg, name) is False

    staged = TS.stage_toolset_mutation(cfg, name, True,
                                       actor="dashboard:user",
                                       session="dashboard")
    assert staged["state"] == "needs-confirm", staged
    assert staged["code"] == "CONFIRM_REQUIRED"
    assert staged["confirmation_id"]
    assert staged.get("already") is not True   # the old false success

    done = TS.settle_toolset_mutation(cfg, staged["confirmation_id"],
                                      actor="dashboard:user",
                                      session="dashboard")
    assert done["state"] == "executed" and done["enabled"] is True

    assert _read_rows(cfg)[name] is True
    assert TS.effective_toolset_enabled(cfg, name) is True


def test_toolset_toggle_route_stages_instead_of_false_success(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    name = next(n for n, on in _read_rows(cfg).items() if not on)

    code, out = QC.h_toolset_toggle(cfg, {}, {"enabled": True}, {"id": name})
    assert code == 409, out
    assert out["state"] == "needs-confirm" and out["code"] == "CONFIRM_REQUIRED"
    assert out["confirmation_id"]


def test_staged_toolset_mutation_is_cancellable(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = TS.stage_toolset_mutation(cfg, "web", False,
                                       actor="dashboard:user",
                                       session="dashboard")
    assert staged["state"] == "needs-confirm"
    cid = staged["confirmation_id"]
    assert [p["id"] for p in TS.pending_toolset_mutations(cfg)] == [cid]

    cancelled = OPS.cancel_op(cfg, cid, actor="dashboard:user",
                              session="dashboard")
    assert cancelled["state"] == "executed", cancelled
    assert cancelled["receipt"]["cancelled"] == 1

    # journal entry settled (terminal), so it is no longer in_progress/pending
    assert TS.pending_toolset_mutations(cfg) == []
    # and the mutation did NOT apply
    assert TS.toolset_enabled(cfg, "web") is True

    # settle-after-cancel is typed, never applies
    replay = TS.settle_toolset_mutation(cfg, cid)
    assert replay["ok"] is False
    assert replay["code"] == TS.REPLAYED_CODE


def test_ops_cancel_route_settles_toolset_journal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = TS.stage_toolset_mutation(cfg, "web", False,
                                       actor="dashboard:user",
                                       session="dashboard")
    cid = staged["confirmation_id"]

    code, out = QC.h_ops_cancel(cfg, {}, {"confirmation_id": cid}, {})
    assert code == 200, out
    assert out["state"] == "executed"
    assert out["receipt"]["cancelled"] == 1
    assert TS.pending_toolset_mutations(cfg) == []


def test_cancel_all_includes_toolset_pending(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = TS.stage_toolset_mutation(cfg, "web", False,
                                       actor="dashboard:user",
                                       session="dashboard")
    assert staged["confirmation_id"]

    cancelled = OPS.cancel_op(cfg, None, actor="dashboard:user",
                              session="dashboard")
    assert cancelled["state"] == "executed", cancelled
    assert cancelled["receipt"]["cancelled"] >= 1
    assert TS.pending_toolset_mutations(cfg) == []
