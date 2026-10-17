"""A seat turn must not report unknown spend merely because the agent never said.

THE LOOP THIS PINS
------------------
`run_agent.AIAgent` initialises ``session_cost_status = "unknown"``
(run_agent.py:406) and NOTHING updates it on an ordinary turn — only the
delegate tool paths do (delegate_tool_results.py:388). So every seat turn
reported "unknown" even when it ran on a flat-rate route:

    turn -> `_vendor_usage` sees "unknown" -> `runner.run_turn` returns
    UNKNOWN_SPEND -> the receipt stays open -> D15 refuses admission for the
    WHOLE org until a human settles that one run by hand.

That is exactly what wedged the office repeatedly: four consecutive runs
(`run_137a0197c5094875`, `run_bde1a9a18af64649`, `run_eaf62ce07c684398`,
`run_2de4223c1c5b4efb`) all recorded ``cost_status: "unknown"`` with the reason
"the provider returned usage but its price could not be established".

The pricing layer already resolves the route from provider+model and knows
opencode-go/zen are `subscription_included`. An absent verdict is not evidence
of unknown spend — it is evidence of no verdict, so we ask the pricing layer.

The honest case is preserved: a route that is neither flat-rate nor priceable
still reports "unknown", and a price the agent DID measure is never overridden.
"""
from __future__ import annotations

import pytest


class _Agent:
    """The shape `_vendor_usage` reads, with the real default status."""

    session_total_tokens = 12865
    session_prompt_tokens = 12863
    session_completion_tokens = 2
    session_api_calls = 1

    def __init__(self, status="unknown", cost=None, base_url=""):
        self.session_cost_status = status
        self.session_estimated_cost_usd = cost
        self.base_url = base_url


def _usage(agent, provider, model):
    from voicebridge.agent import _vendor_usage

    return _vendor_usage(agent, provider, model)


def test_a_flat_rate_route_is_not_unknown_spend():
    """The regression: opencode-go normally ran, usage was real, price was
    'unknown', and the whole org refused admission."""
    out = _usage(_Agent(base_url="https://opencode.ai/zen/go/v1"),
                 "opencode-go", "deepseek-v4.1-flash")
    assert out["cost_status"] == "included", out
    assert out["cost_usd_est"] == 0.0
    assert out["prompt_tokens"] == 12863
    assert out["completion_tokens"] == 2


def test_an_unknowable_route_stays_unknown():
    """D15 is not weakened: a real call whose price cannot be established must
    remain unknown rather than being assumed free."""
    out = _usage(_Agent(base_url="http://127.0.0.1:20128/v1"),
                 "omniroute", "a-model-nobody-priced")
    assert out["cost_status"] == "unknown", out
    assert "cost_usd_est" not in out


def test_a_measured_price_from_the_agent_wins():
    """When the agent DID measure a price, the fallback must not touch it."""
    out = _usage(_Agent(status="actual", cost=0.25, base_url=""),
                 "whatever", "whatever")
    assert out["cost_status"] == "actual"
    assert out["cost_usd_est"] == pytest.approx(0.25)


def test_an_included_verdict_from_the_agent_is_kept():
    out = _usage(_Agent(status="included"), "opencode-go", "x")
    assert out["cost_status"] == "included"
    assert out["cost_usd_est"] == 0.0


def test_a_turn_with_no_token_counters_is_still_empty():
    """No measurement at all stays empty, so the runner can say 'no usage'
    rather than inventing zero."""
    class _NoCounters:
        session_total_tokens = None

    from voicebridge.agent import _vendor_usage

    assert _vendor_usage(_NoCounters(), "opencode-go", "x") == {}
