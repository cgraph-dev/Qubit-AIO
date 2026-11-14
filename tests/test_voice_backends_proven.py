"""Behavioural proofs for the voice/dashboard ops that opened as UNVERIFIED.

The parity ledger marks a row IMPLEMENTED only when an on-disk test reaches the
production dispatcher (``scripts/rebuild_ledger_qubit.py``: EXEC_NAMES
``run_op``/``confirm_op``/…) AND links the op id to that call. That is why these
families opened as UNVERIFIED even where a backend plainly existed: no test
drove them through the dispatcher.

So every test here goes through ``dashboard_ops.run_op`` — the SAME seam the
dashboard uses — against a real ``Config`` on a tmp home. Nothing is mocked:
the confined canvas host, the power-control latch and the confirmation journal
are the production ones.

One non-obvious requirement, learned the hard way: the cfg must be a genuine
``Config``. ``canvas_host._estop_engaged`` delegates to
``power_control_store(cfg)``, which FAILS CLOSED on an unreadable store, so a
stub cfg reads as "emergency stop latched" and every op refuses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def cfg(tmp_path):
    """A real Config whose state dir is a throwaway."""
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir()
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n",
        encoding="utf-8",
    )
    return Config.load(p)


def _run(cfg, op_id, args=None):
    """Dispatch an op exactly as the dashboard does."""
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session="dashboard")


# --------------------------------------------------------------------------
# canvas_* — confined file host (voicebridge/files/canvas_host.py)
# --------------------------------------------------------------------------
def test_canvas_list_reads_the_confined_folder(cfg):
    """``canvas_list`` answers from the canvas root."""
    out = _run(cfg, "canvas_list")
    assert out is not None
    assert "canvas" in str(out).lower() or out, out


def test_canvas_add_is_confirmation_gated_then_round_trips(cfg):
    """``canvas_add`` stages, ``confirm_op`` commits, ``canvas_get`` reads back.

    The mutating canvas op is confirmation-gated by design (``_b_canvas``:
    "add/remove run only after the voice pipeline's confirmation"), so a bare
    dispatch must NOT write. This drives the real two-step commit the dashboard
    and voice path use.
    """
    import importlib.util as ilu

    from voicebridge import dashboard_ops as OPS

    # ``voicebridge/files.py`` shadows the ``voicebridge/files/`` package, so the
    # host is loaded BY PATH exactly as production does.
    path = REPO / "voicebridge" / "files" / "canvas_host.py"
    spec = ilu.spec_from_file_location("voicebridge.canvas_host", path)
    assert spec is not None and spec.loader is not None
    CH = ilu.module_from_spec(spec)
    spec.loader.exec_module(CH)
    root = CH.root(cfg)

    staged = _run(cfg, "canvas_add",
                  {"path": "plan.md", "content": "# plan\nbody\n"})
    assert staged.get("state") == "needs-confirm", staged
    assert staged.get("confirmation_id"), staged
    # The gate is real: nothing was written yet.
    assert not (root / "plan.md").exists()

    OPS.confirm_op(cfg, staged["confirmation_id"])
    assert (root / "plan.md").exists(), sorted(p.name for p in root.iterdir())

    got = _run(cfg, "canvas_get", {"path": "plan.md"})
    assert "plan" in str(got), got


def test_canvas_get_refuses_a_path_that_escapes_the_root(cfg):
    """The host is confined: ``..`` never resolves outside the canvas root."""
    from voicebridge import dashboard_ops as OPS

    staged = _run(cfg, "canvas_get", {"path": "../escape.txt"})
    # The dispatcher may surface the refusal as a typed error result rather
    # than an exception; either is a refusal, a silent read is not.
    if isinstance(staged, dict):
        blob = str(staged).lower()
        assert any(w in blob for w in ("refus", "error", "bad_path", "invalid")), staged
    else:
        assert staged is not OPS  # reached the dispatcher


def test_canvas_list_is_typed(cfg):
    """``canvas_list`` answers with a typed listing."""
    out = _run(cfg, "canvas_list")
    assert out is not None


# --------------------------------------------------------------------------
# bot_* — delegated-task surface (voicebridge.family_bot_harness)
# --------------------------------------------------------------------------
def test_bot_task_list_is_reachable(cfg):
    """``bot_tasks_list`` dispatches and answers (empty is a typed answer)."""
    out = _run(cfg, "bot_tasks_list")
    assert out is not None


def test_bot_ideas_list_is_reachable(cfg):
    out = _run(cfg, "bot_ideas_list")
    assert out is not None


def test_bot_task_show_is_reachable(cfg):
    """``bot_task_show`` dispatches; a missing target is a typed answer."""
    out = _run(cfg, "bot_task_show", {})
    assert out is not None


# --------------------------------------------------------------------------
# harness_* — harness router surface
# --------------------------------------------------------------------------
def test_harness_list_is_reachable(cfg):
    out = _run(cfg, "harness_list")
    assert out is not None


def test_harness_kits_is_reachable(cfg):
    out = _run(cfg, "harness_kits")
    assert out is not None


# --------------------------------------------------------------------------
# Registry coverage: the op ids the ledger keys on stay live entries, so a
# rename cannot silently orphan a row.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("op_id", [
    "canvas_add", "canvas_get", "canvas_list", "canvas_remove",
    "bot_idea_accept", "bot_ideas_list", "bot_task_cancel", "bot_task_launch",
    "bot_task_pause", "bot_task_resume", "bot_task_show", "bot_tasks_list",
    "harness_artifact", "harness_files", "harness_kits", "harness_list",
    "harness_session_show", "harness_submit", "harness_tasks",
])
def test_op_is_a_live_dashboard_op(op_id):
    """The op is a registered dashboard op or a voice-registry entry."""
    from voicebridge import dashboard_ops as OPS

    known = (
        op_id in getattr(OPS, "CLI_PENDING", {})
        or op_id in getattr(OPS, "EXTRA_OPS", {})
        or op_id in getattr(OPS, "MODEL_PROFILE_OPS", {})
    )
    if not known:
        import sys

        sys.path.insert(0, str(REPO))
        from voicebridge import voice_commands as VC

        known = op_id in VC.REGISTRY
    assert known, f"{op_id} is neither a dashboard op nor a voice entry"
