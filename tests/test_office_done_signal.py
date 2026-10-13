"""C17 — "done" is never the model's own verdict.

StarNet's `MISTAKES.md` records a full-access loop that *"ran all night with
dryStreak at 0 and no cap in its way"*, because its only done-reads were the
model's own claims — 98 passes of *verified the files exist*, each
auto-approved.

The org runs unattended. The same failure would burn a night of inference and
report success. So a completion signal must be **machine-checkable** — an exit
condition the runtime evaluates against real state.

Hermetic: tmp XDG state only; the executor is a stub, and the "real state" it
is checked against is a real file on disk.
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


def _seat(cfg):
    from voicebridge.office import ledger, org as O
    ledger.health(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    w = O.hire(cfg, "worker-1", seat_type="worker", supervisor_id=ceo["id"],
               confirmed=True)
    ledger.record(cfg, "revenue", 100_000, agent_id=w["id"])
    return w["id"]


def _claiming_stub(text: str):
    def _call(cfg, agent_id, task_text):
        return {"state": "OK", "reply": text,
                "receipt": {"model": "stub", "identity": "stub/stub",
                            "usage": {"model": "stub", "identity": "stub/stub",
                                      "prompt_tokens": 10,
                                      "completion_tokens": 5,
                                      "cost_usd_est": 0.0001}}}
    return _call


def test_the_models_own_claim_is_not_a_completion_signal(tmp_path, monkeypatch):
    """The StarNet failure, asserted directly."""
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)
    out = runner.run_turn(
        cfg, agent_id, "write report.md",
        executor=_claiming_stub("verified the files exist"),
        # no checker: nothing can verify the claim, so it is not done
    )
    assert out["done"] is False


def test_a_filesystem_assertion_is_a_completion_signal(tmp_path, monkeypatch):
    """…and the same turn IS done once real state can be checked."""
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)
    target = tmp_path / "report.md"

    def checker(_result):
        return target.is_file()

    out = runner.run_turn(
        cfg, agent_id, "write report.md",
        executor=_claiming_stub("verified the files exist"),
        checker=checker)
    assert out["done"] is False          # the claim alone changed nothing…

    target.write_text("the report\n")    # …now the real state exists
    out2 = runner.run_turn(
        cfg, agent_id, "write report.md",
        executor=_claiming_stub("verified the files exist"),
        checker=checker)
    assert out2["done"] is True          # the assertion is what decided it


def test_a_blank_reply_is_never_done(tmp_path, monkeypatch):
    """An empty turn must not render as a clean delivery (40-RUNTIME.md)."""
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)
    out = runner.run_turn(cfg, agent_id, "work",
                          executor=_claiming_stub(""))
    assert out["done"] is False


def test_an_incomplete_run_says_so_on_the_event_stream(tmp_path, monkeypatch):
    """The renderer needs to distinguish a delivery from an attempt (D14)."""
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)
    runner.run_turn(cfg, agent_id, "work",
                    executor=_claiming_stub("verified the files exist"))
    t = runner.trace(cfg, agent_id)
    kinds = [e["kind"] for e in t["runs"][0]["events"]]
    # the turn settled (it ran, and it cost money)…
    assert "run_settled" in kinds
    # …and the TASK is separately reported as unverified (C17)
    assert "task_unverified" in kinds
    assert "task_done" not in kinds
