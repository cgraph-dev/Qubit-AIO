"""`opencode run --format json` — parse the REAL wire shape, and keep the cost.

WHY THIS EXISTS
---------------
`parse_run_output` was written against an imagined event shape: it looked for
``text``/``content``/``message`` at the TOP LEVEL of each event. The real
stream nests the payload under ``part``::

    {"type":"text","part":{"type":"text","text":"ok", ...}}
    {"type":"step_finish","part":{"type":"step-finish",
        "tokens":{"total":14913,"input":12863,"output":2,"reasoning":0,
                  "cache":{"read":2048,"write":0}},
        "cost":0.001936794}}

So a perfectly healthy run parsed as ``MALFORMED_OUTPUT`` (verified: the old
logic yields zero texts on the captured fixture), and the provider's own
measured cost — the figure that makes an office seat's ledger real instead of
a static-table guess — was thrown away.

The fixture ``run_json_real_capture.jsonl`` is an unedited capture from
``opencode run --format json -m opencode-go/deepseek-v4.1-flash``. It is the
authority for this file; the shapes below are copied from it, not invented.

D16 note: a missing cost must be ``None``, never ``0.0``. Zero is a claim that
the call was free, and that is exactly the bug class the office ledger exists
to prevent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = (Path(__file__).resolve().parent
           / "fixtures" / "opencode" / "run_json_real_capture.jsonl")


def _parse(text: str):
    from voicebridge.opencode.transport import parse_run_output
    return parse_run_output(text)


def test_a_real_capture_parses_as_success():
    """The regression: real output used to be MALFORMED_OUTPUT."""
    out = _parse(FIXTURE.read_text())
    assert out["state"] == "OK", out
    assert out["text"] == "ok"


def test_the_real_capture_yields_the_providers_own_cost():
    """The exact figures from the capture, not a recomputation."""
    out = _parse(FIXTURE.read_text())
    assert out["cost_usd"] == pytest.approx(0.001936794)
    assert out["tokens_in"] == 12863
    assert out["tokens_out"] == 2
    assert out["usage_measured"] is True


def test_cost_is_none_when_no_step_reported_one():
    """Absent cost is unknown, never free (D16)."""
    body = json.dumps({"type": "text",
                       "part": {"type": "text", "text": "hello"}})
    out = _parse(body)
    assert out["state"] == "OK"
    assert out["cost_usd"] is None
    assert out["usage_measured"] is False


def test_costs_are_summed_across_steps():
    """A multi-step run charges per step; the turn's cost is the sum."""
    def step(cost, tin, tout):
        return json.dumps({"type": "step_finish", "part": {
            "type": "step-finish", "cost": cost,
            "tokens": {"input": tin, "output": tout}}})
    text = json.dumps({"type": "text",
                       "part": {"type": "text", "text": "done"}})
    out = _parse("\n".join([text, step(0.001, 100, 5), step(0.002, 200, 7)]))
    assert out["cost_usd"] == pytest.approx(0.003)
    assert out["tokens_in"] == 300
    assert out["tokens_out"] == 12
    assert out["steps"] == 2


def test_a_top_level_text_event_still_parses():
    """Legacy/defensive shape: the chatty top-level key still works."""
    out = _parse(json.dumps({"type": "text", "text": "legacy"}))
    assert out["state"] == "OK"
    assert out["text"] == "legacy"


def test_non_json_output_is_still_a_typed_failure():
    """Unconformant output must never be reported as success."""
    out = _parse("just some prose, no json here")
    assert out["state"] == "failed"
    assert out["code"] == "MALFORMED_OUTPUT"


def test_empty_output_is_a_typed_failure():
    out = _parse("   \n\n ")
    assert out["state"] == "failed"
    assert out["code"] == "EMPTY_OUTPUT"


def test_a_boolean_cost_is_not_accepted_as_a_measurement():
    """`True` is an int in Python; it must not become a $1 cost."""
    text = json.dumps({"type": "text", "part": {"type": "text", "text": "x"}})
    step = json.dumps({"type": "step_finish",
                       "part": {"type": "step-finish", "cost": True}})
    out = _parse("\n".join([text, step]))
    assert out["cost_usd"] is None
    assert out["usage_measured"] is False
