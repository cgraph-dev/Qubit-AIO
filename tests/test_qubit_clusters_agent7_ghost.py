"""Agent 7 — Ghost cluster, real-module proofs for the PARTIAL rows.

Exercises ``voicebridge.ghost`` directly (contract specs, the grant gate, the
real session-grant store, preview/retry/recover/cancel, receipts, audit) with
fixture executors only — the simulated adapter outcome is synthetic, but every
contract/gate/hash/receipt/audit path is real.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import actions as A
from voicebridge import ghost as G

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "actions"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _runner(cfg, *, grants=(), session_grants=(), execs=None,
            name="ghost_outcomes.json"):
    from voicebridge.permissions import PermissionsStore

    G.register_ghost_specs()
    store = A.ActionStore(Path(cfg.state_dir()) / "actions.json")
    perms = PermissionsStore(Path(cfg.state_dir()) / "perm.json")
    for cap, scope in grants:
        perms.grant(cap, requested_by="user", confirmed=True, scopes=(scope,))
    sgs = G.SessionGrantStore(Path(cfg.state_dir()) / "ghost-grants.json")
    if execs is None:
        execs = A.make_fixture_executors(
            A.load_fixture_outcomes(FIXTURES / name))
    adapter = G.GhostAdapter(shell=False)
    run = G.GhostRunner(store=store, perms=perms, executors=execs,
                        enabled=True, session_grants=sgs, adapter=adapter)
    run.adapter.enable(requested_by="user")
    for cap, scope, target in session_grants:
        run.grant(cap, scopes=(scope,), target=target)
    return run


# --------------------------------------------------------------- ghost_action_preview
def test_ghost_action_preview_no_side_effect(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    before = list(run.store.receipts())
    out = run.preview("ghost.input.click",
                      {"role": "chromium", "target": "#submit"},
                      target="https://mail.google.com", scope="web",
                      role="chromium")
    assert out["state"] == "preview" or "preview" in out
    assert out.get("action_id") == "ghost.input.click"
    assert "#submit" in json.dumps(out)
    assert list(run.store.receipts()) == before, "preview writes nothing"


# -------------------------------------------------------------------- ghost_preview
def test_ghost_preview_unknown_capability_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    with pytest.raises(A.ActionValidationError):
        run.preview("ghost.nope", {}, target="desktop", scope="desktop")


# ----------------------------------------------------------------------- ghost_click
def test_ghost_click_grant_bound_and_exact(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    denied = run.execute("ghost.input.click",
                         args={"role": "chromium", "target": "#submit"},
                         target="https://mail.google.com", scope="web",
                         source="user", confirmed=True)
    assert denied["state"] == "denied"
    assert denied["code"] in ("NOT_GRANTED", "CONFIRMATION_REQUIRED")
    run = _runner(cfg, grants=(("ghost.input.click", "web"),))
    ok = run.execute("ghost.input.click",
                     args={"role": "chromium", "target": "#submit"},
                     target="https://mail.google.com", scope="web",
                     source="user", confirmed=True)
    assert ok["state"] == "OK", ok
    # A grant bound to one origin never authorizes a different browser origin.
    other = run.execute("ghost.input.click",
                        args={"role": "chromium", "target": "#submit"},
                        target="https://evil.test", scope="web",
                        source="user", confirmed=True)
    assert other["state"] == "denied"


# ------------------------------------------------------------------------ ghost_type
def test_ghost_type_grant_bound_and_schema_bounded(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, grants=(("ghost.input.type", "web"),))
    ok = run.execute("ghost.input.type",
                     args={"role": "chromium", "target": "#to",
                           "text": "hello"},
                     target="https://mail.google.com", scope="web",
                     source="user", confirmed=True)
    assert ok["state"] == "OK", ok
    with pytest.raises(A.ActionValidationError):
        run.execute("ghost.input.type",
                    args={"role": "chromium", "target": "#to",
                          "text": "x" * 5000},
                    target="https://mail.google.com", scope="web",
                    source="user", confirmed=True)


# -------------------------------------------------------------------- ghost_read_tab
def test_ghost_read_tab_grant_gated_and_origin_bound(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    run.grant("ghost.browser.tabs", scopes=("web",),
              target="https://mail.google.com", role="chromium")
    ok = run.execute("ghost.browser.tabs", args={"role": "chromium"},
                     target="https://mail.google.com", scope="web",
                     source="user")
    assert ok["state"] == "OK", ok
    wrong = run.execute("ghost.browser.tabs", args={"role": "chromium"},
                        target="https://evil.test", scope="web",
                        source="user")
    assert wrong["state"] == "denied"
    model = run.execute("ghost.browser.tabs", args={"role": "chromium"},
                        target="https://mail.google.com", scope="web",
                        source="model")
    assert model["state"] == "denied"


# --------------------------------------------------------------- ghost_action_recover
def test_ghost_action_recover_reversible_and_impossible(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg,
                  grants=(("ghost.window.close", "desktop"),),
                  session_grants=(("ghost.input.cursor", "desktop",
                                   "desktop"),))
    cur = run.execute("ghost.input.cursor", args={"target": "panel"},
                      target="desktop", scope="desktop", source="user")
    assert cur["state"] == "OK", cur
    rec = run.recover(cur["receipt_id"], source="user", confirmed=True)
    assert rec["state"] == "recovered", rec

    close = run.execute("ghost.window.close", args={"window": "old"},
                        target="desktop", scope="desktop", source="user",
                        confirmed=True)
    assert close["state"] == "OK", close
    impossible = run.recover(close["receipt_id"], source="user",
                             confirmed=True)
    assert impossible["state"] == "ROLLBACK_IMPOSSIBLE", impossible


# ----------------------------------------------------------------- ghost_action_retry
def test_ghost_action_retry_same_args_target_scope(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    flag = tmp_path / "attempted"

    def flaky(env):
        # Runs in a killable child process, so cross-process state is on disk.
        if not flag.exists():
            flag.write_text("1")
            raise RuntimeError("first attempt failed")
        return json.dumps({"state": "OK", "detail": "second attempt"})

    execs = {"ghost.input.click": flaky}
    run = _runner(cfg, execs=execs,
                  grants=(("ghost.input.click", "web"),))
    first = run.execute("ghost.input.click",
                        args={"role": "chromium", "target": "#submit"},
                        target="https://mail.google.com", scope="web",
                        source="user", confirmed=True)
    assert first["state"] == "failed", first
    ok = run.retry(first["receipt_id"],
                   args={"role": "chromium", "target": "#submit"},
                   target="https://mail.google.com", scope="web",
                   source="user", confirmed=True)
    assert ok["state"] == "OK", ok
    stale = run.retry(first["receipt_id"], args={"role": "chromium",
                                                 "target": "#other"},
                      target="https://mail.google.com", scope="web",
                      source="user", confirmed=True)
    assert stale["state"] == "stale_target"
    assert stale["code"] == "ARGS_MISMATCH"


# ------------------------------------------------------------------- ghost_receipts
def test_ghost_receipts_persist_across_reopen(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, session_grants=(("ghost.desktop.discover", "desktop",
                                        "desktop"),))
    out = run.execute("ghost.desktop.discover", args={}, target="desktop",
                      scope="desktop", source="user")
    assert out["state"] == "OK", out
    rid = out["receipt_id"]
    reopened = _runner(cfg, session_grants=(("ghost.desktop.discover",
                                             "desktop", "desktop"),))
    assert reopened.receipt(rid)["receipt_id"] == rid


# --------------------------------------------------------------------- ghost_status
def test_ghost_status_and_inventory_are_real(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, grants=(("ghost.input.click", "web"),),
                  session_grants=(("ghost.desktop.discover", "desktop",
                                   "desktop"),))
    status = run.ghost_status()
    assert status["state"] == "OK"
    assert status["adapter"]["enabled"] is True
    assert "chromium" in status["roles"] and "comet" in status["roles"]
    assert any(g["capability"] == "ghost.desktop.discover"
               for g in status["grants"])
    listed = {a["action_id"]: a for a in run.ghost_actions()}
    assert listed["ghost.desktop.discover"]["executor"] == "fixture"
    assert listed["ghost.desktop.discover"]["grantable"] is True
    assert listed["ghost.desktop.discover"]["effective"]["allowed"] is True
    assert listed["ghost.input.click"]["grantable"] is False
    assert listed["ghost.input.click"]["tier"] == "mutating"
