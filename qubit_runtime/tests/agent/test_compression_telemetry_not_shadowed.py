"""Regression test for the class-4 shadowed-definition fix in conversation_compression.

``agent/conversation_compression.py`` imports ``_emit_compression_attempt_telemetry``
from ``agent.conversation_compression_telemetry`` (upstream a036b13 does exactly
this and defines NO local copy). A stale local def at the old line 1471 shadowed
that import, so the module-global resolved to the older body -- which lacked the
``_own_class`` deferral guard for generic abort verdicts.

These tests fail on the pre-fix tree (the global was the local function; a generic
abort verdict overwrote a same-attempt failure_class) and pass after the local
duplicate is removed.
"""
import json
import logging
from types import SimpleNamespace

import agent.conversation_compression as cc
import agent.conversation_compression_telemetry as tel


def test_compression_module_uses_the_telemetry_extraction():
    """The name must resolve to the imported (upstream-intended) function, not a
    shadowing local def."""
    assert cc._emit_compression_attempt_telemetry is tel._emit_compression_attempt_telemetry


def _agent():
    compressor = SimpleNamespace(_last_compression_telemetry={})
    return SimpleNamespace(
        context_compressor=compressor,
        session_id="s",
        _compression_attempt_id="attempt-1",
    )


def test_generic_abort_verdict_does_not_clobber_same_attempt_failure_class(caplog):
    """The telemetry extraction defers a GENERIC abort verdict to a failure_class
    already recorded for THIS attempt; the shadowed local body did not."""
    agent = _agent()
    agent.context_compressor._last_compression_telemetry = {
        "attempt_id": "attempt-1",
        "failure_class": "provider_timeout",
    }
    with caplog.at_level(logging.INFO):
        cc._emit_compression_attempt_telemetry(
            agent, started_at=0.0, commit_status="aborted",
            split_status="aborted", failure_class="no_progress",
        )
    payload = json.loads(caplog.records[-1].getMessage().split(": ", 1)[1])
    assert payload["failure_class"] == "provider_timeout"
