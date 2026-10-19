"""The sudo/secret/CLI-turn cluster cannot be wired until the TurnRunner cut-over lands.

``sudo_prompt_scope`` (gateway/session_sudo_prompt.py), ``secret_prompt_scope``
(gateway/session_secret_prompt.py) and ``run_classic_cli_turn`` (gateway/session_cli_turn.py) are
never called from ``gateway/``. Upstream wires them in ``run_sync`` inside a ``try/finally`` whose
body also does ``local_viewer_events.restore`` / ``publish_usage`` / ``publish_settled_info``.

That block is NOT wireable in this tree yet, and this test pins WHY, so the cluster cannot be
"fixed" by pasting the call shape in and calling it done. Each assertion below is a precondition
the upstream call shape depends on; when the cut-over lands, this test fails and points at the
place the cluster can then be wired.
"""
from types import SimpleNamespace

from gateway.config import Platform


def test_turn_runner_has_the_approval_owner_store_the_cluster_reads():
    """``sudo_prompt_scope``/``secret_prompt_scope`` unpack ``turn._approval_owner``; every
    publish seam reads it. Upstream sets it in ``TurnRunner.__init__`` and declares a class-level
    ``None`` default. Ours has neither, so *every* turn here is "not a local viewer turn" and both
    scopes return an unmodified context manager."""
    from gateway.run_turn_runner import TurnRunner

    assert getattr(TurnRunner, "_approval_owner", None) is None, (
        "TurnRunner has no _approval_owner: sudo_prompt_scope/secret_prompt_scope no-op for every "
        "turn, and local_viewer_turn() is False everywhere it gates on it"
    )


def test_turn_runner_can_publish_execution_events():
    """The turn-end ``finally`` publishes ``session.usage`` and ``session.info``, and
    ``local_viewer_events.wire`` publishes through the same seam. ``_publish_execution`` is called
    from ``run_turn_progress.GatewayTurnProgressMixin`` but ``TurnRunner`` inherits no mixin and
    defines no such method."""
    from gateway.run_turn_runner import TurnRunner

    assert not callable(getattr(TurnRunner, "_publish_execution", None)), (
        "TurnRunner defines/inherits no _publish_execution: local_viewer_events.wire, "
        "publish_usage and publish_settled_info all raise AttributeError on a real runner"
    )


def test_local_viewer_turn_is_false_today():
    """The concrete consequence: a LOCAL turn is still not a local-viewer turn, so the sudo
    callback is never installed and the secret capture is never bound."""
    from gateway.run_turn_progress import local_viewer_turn
    from gateway.run_turn_runner import TurnRunner
    from gateway.turn_context import TurnContext

    ctx = TurnContext(source=SimpleNamespace(platform=Platform.LOCAL))
    runner = TurnRunner(SimpleNamespace(), ctx)
    assert local_viewer_turn(runner) is False


def test_wiring_the_cluster_as_is_would_crash():
    """Proof the upstream ``finally`` shape is not copy-pasteable yet: ``publish_usage`` swallows
    its own errors, but ``local_viewer_events.wire`` does not, so a local turn would raise."""
    from gateway import local_viewer_events
    from gateway.run_turn_runner import TurnRunner

    runner = TurnRunner(SimpleNamespace(), SimpleNamespace())
    agent = SimpleNamespace(session_output_tokens=0)
    try:
        local_viewer_events.wire(runner, agent)
    except AttributeError as exc:
        assert "_publish_execution" in str(exc)
    else:  # pragma: no cover - once the cut-over lands this is the signal to wire the cluster
        raise AssertionError(
            "local_viewer_events.wire succeeded: TurnRunner now publishes execution events, so "
            "sudo_prompt_scope / secret_prompt_scope / run_classic_cli_turn can be wired in run_sync"
        )
