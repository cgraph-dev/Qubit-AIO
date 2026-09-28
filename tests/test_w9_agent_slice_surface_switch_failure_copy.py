"""W9 agent/ vendor-sync slice — ported ``agent.surface_switch`` + ``agent.turn_failure_copy``.

The vendored ``qubit_runtime/agent/`` was missing two upstream 0.21.4 modules
that already had local callers (``cron.scheduler_failure_copy`` and
``qubit_cli.cli_chat_error_copy`` import ``agent.turn_failure_copy``). They are
ported from pin ``d337b736`` with the hermes->qubit rename map applied
(``hermes_constants``/``display_hermes_home``/``hermes_cli`` -> qubit, and
user-facing ``hermes <cmd>`` copy -> ``qubit <cmd>``).

This file pins the inert, dependency-local behavior of both modules plus the
rename, so a future sync cannot silently regress the port.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

PORTED = (
    RUNTIME_ROOT / "agent" / "turn_failure_copy.py",
    RUNTIME_ROOT / "agent" / "surface_switch.py",
)


def test_ported_modules_import():
    import agent.surface_switch as ss
    import agent.turn_failure_copy as tfc

    assert callable(ss.stage_surface_switch_note)
    assert isinstance(tfc.SITE_FAILURE_CODES, frozenset)


def test_failover_reason_enum_carries_ported_members():
    from agent.error_classifier import FailoverReason

    # Members the ported copy tables reference; absent before this slice.
    for name in ("upstream_blocked", "model_entitlement", "incomplete_response", "role_alternation"):
        assert hasattr(FailoverReason, name), name


# ---- agent.turn_failure_copy -------------------------------------------------


def test_stamp_and_failed_turn_notice():
    import agent.turn_failure_copy as tfc

    result = {}
    assert tfc.stamp_failure(result, "loop_error", True) is result
    assert result == {"failure_reason": "loop_error", "failure_retryable": True}

    assert tfc.failed_turn_notice([]) == tfc.FAILED_TURN_NOTICE
    assert tfc.failed_turn_notice([{"role": "tool"}]) == tfc.PARTIAL_FAILED_TURN_NOTICE
    assert (
        tfc.failed_turn_notice([{"role": "assistant", "tool_calls": [{}]}])
        == tfc.PARTIAL_FAILED_TURN_NOTICE
    )


def test_exit_reason_failure_verdicts():
    import agent.turn_failure_copy as tfc

    adv = tfc.exit_reason_failure("empty_response_exhausted")
    assert adv == tfc.ExitFailure("empty_response", True, False)
    assert tfc.exit_reason_failure("local_processing_error(detail)").fails_turn is False
    assert tfc.exit_reason_failure("context_compression_timeout").reason == "context_overflow"
    assert tfc.exit_reason_failure("something_else") is None


def test_failure_cause_gloss_and_site_copy_are_qubit_branded():
    import agent.turn_failure_copy as tfc

    assert tfc.failure_cause_gloss("timeout")
    assert "Qubit" in tfc.failure_cause_gloss("timeout") or "service" in tfc.failure_cause_gloss("timeout")
    assert tfc.failure_cause_gloss("no_such_reason") is None

    overflow = tfc.site_copy("context_overflow", model="m")
    assert "Qubit" in overflow and "/new" in overflow
    assert "Hermes" not in overflow


def test_invalid_response_failure_reason_maps_status():
    import agent.turn_failure_copy as tfc

    assert tfc.invalid_response_failure_reason(None) == "invalid_response"
    resp = SimpleNamespace(error=SimpleNamespace(code=429))
    assert tfc.invalid_response_failure_reason(resp) == "rate_limit"
    assert tfc.invalid_response_failure_reason(SimpleNamespace(error=SimpleNamespace(code="bogus"))) == "invalid_response"


def test_limit_reset_copy():
    import agent.turn_failure_copy as tfc

    assert tfc.limit_reset_copy(2_000_000_000, now=1_999_999_000)
    assert tfc.limit_reset_copy(1, now=2) == ""


def test_cron_caller_now_resolves_gloss():
    """The previously broken local caller (ModuleNotFoundError) now works."""
    import cron.scheduler_failure_copy as sfc

    assert sfc._provider_failure_cause("timeout")


# ---- agent.surface_switch ----------------------------------------------------


def test_split_runtime_boundary_and_identity_line():
    import agent.surface_switch as ss
    from agent.prompt_builder import RUNTIME_ENVIRONMENT_END, RUNTIME_ENVIRONMENT_HEADING

    prompt = (
        "IDENTITY BLOCK\nPlatform: cli\n\n"
        f"{RUNTIME_ENVIRONMENT_HEADING}\n\nRUNTIME BODY {RUNTIME_ENVIRONMENT_END}"
    )
    identity, marker, runtime = ss.split_runtime_boundary(prompt)
    assert "Platform: cli" in identity
    assert marker == f"\n\n{RUNTIME_ENVIRONMENT_HEADING}\n\n"
    assert runtime.strip() == f"RUNTIME BODY {RUNTIME_ENVIRONMENT_END}"
    assert ss.identity_line_value(prompt, "Platform") == "cli"

    # A prompt that does not END in the marker is all identity.
    assert ss.split_runtime_boundary("no boundary here") == ("no boundary here", "", "")


def test_stage_surface_switch_short_circuits():
    import agent.surface_switch as ss

    prompt = "IDENTITY\nPlatform: cli"
    assert ss.stage_surface_switch_note(SimpleNamespace(provider="moa", api_mode="chat", platform="tui"), prompt, []) is False
    assert ss.stage_surface_switch_note(
        SimpleNamespace(provider="openrouter", api_mode="chat", platform=""), prompt, []
    ) is False


def test_ported_files_carry_no_legacy_brand():
    for path in PORTED:
        text = path.read_text(encoding="utf-8")
        assert "hermes" not in text.lower(), path.name
        assert "Nous" not in text, path.name
