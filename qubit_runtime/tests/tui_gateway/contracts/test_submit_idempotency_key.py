"""Ink sends ``submission_id`` with every prompt.submit: the messaging gateway
requires it as the admission idempotency key. The TUI-dialect contract must
accept (and ignores) it, so one client submits to both gateway dialects."""

from tui_gateway.contracts.registry import METHODS, canonical_param_problems


def test_prompt_submit_accepts_submission_id():
    contract = METHODS['prompt.submit']
    assert canonical_param_problems(
        contract,
        {'session_id': 's', 'text': 'hi', 'submission_id': '550e8400-e29b-41d4-a716-446655440000'},
    ) == []
