"""D15/D16 — a seat's turn must be priced from the provider, not assumed free.

THE BUG CLASS
-------------
`office.runner.run_turn` settles a turn's receipt from
`result["receipt"]["usage"]` alone. `chat_transport`'s own turns always carried
one; the vendored-agent seat path built its receipt with **no usage block at
all** (`voicebridge/agent.py`). The runner read that as "subscription turn",
booked `unmetered=1, settled_cents=0`, and reported success — so every office
seat ran real provider calls that the ledger recorded as free work, while the
org's caps, balances and runway were derived from a book that under-counted
every seat.

`unmetered` is a *claim about the route* (D16: flat-rate, no marginal charge),
never a fallback for a missing measurement. This suite pins the three-way split
the ledger needs, driven through the real `run_turn` with a stub executor so
the accounting is exercised end to end without a provider call:

| what the turn returned                      | what the runner must do    |
|---------------------------------------------|----------------------------|
| no usage block at all                       | UNKNOWN_SPEND, not unmetered |
| usage, price established (`actual`/`est.`)   | settle the REAL cents, metered |
| usage, price unestablishable (`unknown`)     | UNKNOWN_SPEND (never 0)    |
| usage, flat-rate (`included`)                | unmetered 0 cents, recorded |

Hermetic: tmp XDG state only; a stub executor stands in for the model.
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
    """One live, funded seat, with the caps cleared so the caps never mask the
    result. Funding is a `revenue` row (owner capital), the documented way a
    seat gets a positive balance — admission refuses on a zero balance, which is
    D1 working correctly rather than something to route around."""
    from voicebridge.office import caps as CAPS, ledger, org as O

    assert CAPS.set_cap(cfg, "perRun", 0, confirmed=True,
                        requested_by="test")["state"] == "OK"
    assert CAPS.set_cap(cfg, "perDay", 0, confirmed=True,
                        requested_by="test")["state"] == "OK"
    seat_id = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    ledger.record(cfg, "revenue", 5000, agent_id=seat_id, note="owner capital")
    return seat_id


def _usage(**kw):
    """A usage block shaped like the one the vendored agent now returns."""
    block = {"provider": "test", "model": "test-model",
             "prompt_tokens": 1000, "completion_tokens": 200,
             "api_calls": 1, "identity": "qubit-vendored-agent",
             "cost_status": "actual"}
    block.update(kw)
    return block


def _settled(cfg):
    from voicebridge.office import ledger
    h = ledger.health(cfg)
    return h["cost_cents"], h["unmetered_entries"]


def _run(cfg, agent_id, *, executor, unmetered=True):
    from voicebridge.office import runner

    return runner.run_turn(cfg, agent_id, "a real task",
                           executor=executor, unmetered=unmetered)


# ------------------------------------------------- the missing-measurementbug
def test_a_turn_with_no_usage_is_unknown_spend_not_free(
        tmp_path, monkeypatch):
    """The exact production shape: the seat ran, the receipt says nothing.

    Booking this as `unmetered` 0 cents is the bug — the seat ran a real
    provider call and the ledger would show it as free work.
    """
    from voicebridge.office import ledger

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)

    def executor(_cfg, _aid, _text):
        return {"state": "OK", "reply": "did the thing", "session_id": "s",
                "receipt": {"runtime": "qubit-vendored-agent",
                            "model": "test-model", "tools": [],
                            "session_id": "s"}}        # NO "usage" key

    out = _run(cfg, agent_id, executor=executor)
    assert out["state"] == "UNKNOWN_SPEND", "a real call must never be free"
    assert out["unknown_spend"] is True
    assert ledger.health(cfg)["complete"] is False, "admission must refuse"
    assert _settled(cfg) == (0, 0), "nothing was booked as free work"


def test_a_priced_unknown_turn_is_unknown_spend(tmp_path, monkeypatch):
    """Usage present, price unestablishable — still not free.

    This is the case `unmetered=True` used to swallow: the turn DID return a
    usage block, so the runner's old `if ... and not usage` guard skipped the
    pricing branch and the caller's flag filed it as a subscription.
    """
    from voicebridge.office import ledger

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)

    def executor(_cfg, _aid, _text):
        return {"state": "OK", "reply": "did the thing",
                "receipt": {"usage": _usage(cost_status="unknown"),
                            "model": "test-model"}}

    out = _run(cfg, agent_id, executor=executor)
    assert out["state"] == "UNKNOWN_SPEND"
    assert "could not be established" in out["reason"]
    assert ledger.health(cfg)["complete"] is False
    assert _settled(cfg) == (0, 0)


# --------------------------------------------------------- the priced cases
def test_a_priced_turn_is_settled_at_its_real_cents(tmp_path, monkeypatch):
    """A measured price is booked as metered money, not filed as free."""
    from voicebridge.office import ledger

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)

    def executor(_cfg, _aid, _text):
        return {"state": "OK", "reply": "did the thing",
                "receipt": {"usage": _usage(cost_usd_est=0.12,
                                           cost_status="actual"),
                            "model": "test-model"}}

    out = _run(cfg, agent_id, executor=executor)
    assert out["state"] == "OK"
    assert out["cents"] == 12, "0.12 USD books as 12 cents"
    assert out["unmetered"] is False, "priced work is metered work"
    assert _settled(cfg) == (12, 0)
    assert ledger.health(cfg)["complete"] is True


def test_an_included_turn_is_recorded_unmetered(tmp_path, monkeypatch):
    """The ONE case unmetered means: a flat-rate route the engine priced.

    Not a missing measurement — the pricing engine looked and said the route is
    subscription-included. The turn is recorded; it is not counted as dollars.
    """
    from voicebridge.office import ledger

    cfg = _cfg(tmp_path, monkeypatch)
    agent_id = _seat(cfg)

    def executor(_cfg, _aid, _text):
        return {"state": "OK", "reply": "did the thing",
                "receipt": {"usage": _usage(cost_usd_est=0.0,
                                           cost_status="included"),
                            "model": "test-model"}}

    out = _run(cfg, agent_id, executor=executor)
    assert out["state"] == "OK"
    assert out["cents"] == 0
    assert out["unmetered"] is True
    assert _settled(cfg) == (0, 1), "recorded, and excluded from the meter"
    # An unmetered row is still a row: the org can see the work happened.
    assert ledger.health(cfg)["complete"] is True


# ------------------------------------- the provider routes that resolve to it
@pytest.mark.parametrize("provider,base_url", [
    ("opencode-go", "https://opencode.ai/zen/go/v1"),
    ("opencode_go", ""),                       # the underscore alias
    ("opencode-go-sub", ""),                   # the subscription alias
    ("", "https://opencode.ai/zen/go/v1"),     # resolved from the URL alone
])
def test_an_opencode_route_prices_as_included(provider, base_url):
    """A flat-rate subscription must resolve to `included`, not `unknown`.

    OpenCode Go is $10/month. Its own usage endpoint
    (`/zen/go/v1/usage`, read by `OpenCodeGoProfile.fetch_account_usage`)
    answers percent-of-window values — rolling/weekly/monthly `percent` and
    `resetsAt` — and carries no dollar figure at all, because a plan you have
    already paid for has no per-message price to report. Before this, the route
    fell through to `billing_mode="unknown"`, so every opencode turn was priced
    as unmeasurable: the ledger showed the org's real work as unpriced, and
    admission refused on a cost that does not exist.

    Asserting the ROUTE (not a model name) is deliberate: the provider fronts
    many models and none of them are individually priced, so the subscription
    status has to attach to the route for any of them to settle.
    """
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    from agent.usage_pricing import (estimate_usage_cost, get_pricing_entry,
                                     resolve_billing_route)

    for model in ("grok-4.7", "deepseek-v4.1-flash", "claude-sonnet-4-6"):
        route = resolve_billing_route(model, provider=provider, base_url=base_url)
        assert route.billing_mode == "subscription_included", (
            f"{model} on {provider or base_url!r} must be flat-rate")
        assert get_pricing_entry(model, provider=provider, base_url=base_url), (
            "an `included` route still needs a price entry (a zero one)")
        cost = estimate_usage_cost(
            model, _usage_bucket(), provider=provider, base_url=base_url)
        assert cost.status == "included"
        assert cost.amount_usd == 0


def test_an_unpriceable_route_is_still_unknown():
    """The fix must not turn "unknown" into "free" everywhere.

    A provider Qubit has no price for (a third-party relay whose rates nobody
    can know) must keep reporting `unknown` — that is what makes the runner
    refuse rather than book a fabricated zero. Only a route that actually
    declares itself flat-rate gets `included`.
    """
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    from agent.usage_pricing import estimate_usage_cost, resolve_billing_route

    route = resolve_billing_route("grok-4.7", provider="cheapvibecode",
                                  base_url="https://cheapvibecode.ru/v1")
    assert route.billing_mode != "subscription_included"
    cost = estimate_usage_cost("grok-4.7", _usage_bucket(), provider="cheapvibecode",
                               base_url="https://cheapvibecode.ru/v1")
    assert cost.status == "unknown", "a relay with no published rate stays unknown"


def _usage_bucket():
    from agent.usage_pricing import CanonicalUsage

    return CanonicalUsage(input_tokens=319446, output_tokens=5349, request_count=18)

