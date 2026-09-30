"""D5 — Qubit code-side actions row: ``actions_retry`` through the dispatcher.

Seeds a real FAILED action receipt with the synthetic adapter fixture, then
drives the production ``run_extra_op`` / ``confirm_op`` path and asserts the
retry lifecycle: same-args retry, stale-args refusal, unknown-receipt typed
failure, and confirmation/cancellation. The action contract, gate, receipts,
and retry guard are always real; only the adapter outcome is simulated.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "actions"
ARGS = {"selector": "#email", "value": "bob@example.com"}
TARGET = "https://example.com"
SCOPE = "web"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_failed_receipt(cfg):
    from voicebridge import actions as A
    from voicebridge.permissions import permissions_store

    permissions_store(cfg).grant("act.browser.fill", requested_by="user",
                                 confirmed=True, scopes=("web",))
    runner = A.action_runner(
        cfg, executors=A.make_fixture_executors(
            A.load_fixture_outcomes(FIXTURES / "fail.json")))
    first = runner.execute("act.browser.fill", args=dict(ARGS),
                           target=TARGET, scope=SCOPE)
    assert first["state"] == A.ST_FAILED, first
    return first["receipt_id"]


def _run_and_confirm(cfg, args, *, session="d5"):
    from voicebridge import dashboard_ops as OPS

    out = OPS.run_extra_op(cfg, "actions_retry", args, actor="dashboard:user",
                           session=session)
    if out.get("state") == "needs-confirm":
        out = OPS.confirm_op(cfg, out["confirmation_id"],
                             actor="dashboard:user", session=session)
    return out


def test_d5_actions_retry_reaches_real_backend_and_typed_guards(tmp_path,
                                                                monkeypatch):
    from voicebridge import actions as A

    cfg = _cfg(tmp_path, monkeypatch)
    rid = _seed_failed_receipt(cfg)

    same = _run_and_confirm(cfg, {"receipt_id": rid,
                                  "args_json": json.dumps(ARGS),
                                  "target": TARGET, "scope": SCOPE})
    assert same["state"] == "executed", same
    result = same["receipt"]["result"]
    # The dashboard runner carries no live adapter, so the retry is honestly
    # denied at the adapter gate (never a fabricated success) — but the real
    # retry path ran and preserved the original receipt id.
    assert result["state"] == A.ST_DENIED, result
    assert result.get("code") == A.ST_ADAPTER_DISABLED, result
    assert result.get("action_id") == "act.browser.fill", result

    stale = _run_and_confirm(cfg, {"receipt_id": rid,
                                   "args_json": json.dumps(
                                       {"selector": "#other", "value": "x"}),
                                   "target": TARGET, "scope": SCOPE})
    assert stale["state"] == "executed", stale
    assert stale["receipt"]["result"]["state"] == A.ST_STALE_TARGET, stale
    assert stale["receipt"]["result"]["code"] == "ARGS_MISMATCH", stale

    unknown = _run_and_confirm(cfg, {"receipt_id": "no-such-receipt",
                                     "args_json": "{}"})
    assert unknown["state"] == "failed", unknown
    assert unknown["code"] == "ActionError", unknown


def test_d5_actions_retry_confirmation_cancel_leaves_nothing(tmp_path,
                                                             monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    rid = _seed_failed_receipt(cfg)
    staged = OPS.run_extra_op(cfg, "actions_retry",
                              {"receipt_id": rid,
                               "args_json": json.dumps(ARGS),
                               "target": TARGET, "scope": SCOPE},
                              actor="dashboard:user", session="d5")
    assert staged["state"] == "needs-confirm", staged
    cancelled = OPS.cancel_op(cfg, staged["confirmation_id"],
                              actor="dashboard:user", session="d5")
    assert cancelled["state"] == "executed", cancelled
    after = OPS.confirm_op(cfg, staged["confirmation_id"],
                           actor="dashboard:user", session="d5")
    assert after["state"] == "failed", after
