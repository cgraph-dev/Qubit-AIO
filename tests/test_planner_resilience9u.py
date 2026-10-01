"""Lane A1: planner bounded resilience (hermetic, no network/services).

Proven-live problem: headless builds hit planner 8s PROVIDER_OUTAGE timeouts
under load (local Ollama slow when GPU busy) -> steps=0 promise-only ->
typed TASK_NO_BUILD. Planner had no retry; one slow call killed the build.

Contract under test (planner.py internals only):
- one bounded retry on transient transport codes (TIMEOUT/PROVIDER_OUTAGE);
- backoff between attempts; retry uses a load-aware (longer) timeout;
- persistent outage -> typed no-decision (None), never raises;
- non-retryable failures -> no retry, immediate None;
- honest no-decision semantics preserved (unknown tools/undeclared args None);
- max_tokens 1024 + static PLANNER_SCHEMA shape unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.planner import (  # noqa: E402
    PLANNER_SCHEMA,
    PLANNER_TOOLS,
    TypedProposal,
    plan,
)
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.providers.contracts import (  # noqa: E402
    ProviderResult,
    UsageRecord,
)


def _ok_result(tool="local.open_app", args=None, conf=0.9):
    args = args if args is not None else {"app": "calculator"}
    return ProviderResult(
        ok=True,
        text=json.dumps(
            {"tool": tool, "arguments": args, "confidence": conf}),
        provider="local", model="qubit-brain4",
        identity="local/qubit-brain4",
        usage=UsageRecord(provider="local", model="qubit-brain4",
                          identity="local/qubit-brain4", latency_ms=1))


def _fail_result(code):
    return ProviderResult(
        ok=False, text="", provider="local", model="qubit-brain4",
        identity="local/qubit-brain4", code=code, message=code.lower(),
        usage=UsageRecord(provider="local", model="qubit-brain4",
                          identity="local/qubit-brain4", latency_ms=1))


def _scripted(monkeypatch, results, timeouts=None, sleeps=None):
    """Stub GW.run_role with a script of ProviderResults; record kwargs."""
    calls = []

    def fake(prompt, cfg, *args, **kwargs):
        calls.append(kwargs)
        idx = min(len(calls) - 1, len(results) - 1)
        return results[idx]

    monkeypatch.setattr(GW, "run_role", fake)
    if timeouts is not None:
        timeouts.extend([])  # placeholder (timeouts read from calls)
    if sleeps is not None:
        import voicebridge.planner as PL

        def fake_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr(PL.time, "sleep", fake_sleep)
    return calls


def test_retry_on_timeout_then_ok_returns_proposal(monkeypatch):
    sleeps = []
    calls = _scripted(
        monkeypatch,
        [_fail_result("TIMEOUT"), _ok_result()],
        sleeps=sleeps)
    p = plan("open the calculator please")
    assert isinstance(p, TypedProposal)
    assert p.tool == "local.open_app"
    assert p.arguments == {"app": "calculator"}
    assert len(calls) == 2  # exactly one retry
    assert sleeps and len(sleeps) == 1  # one bounded backoff


def test_retry_on_provider_outage_then_ok(monkeypatch):
    sleeps = []
    calls = _scripted(
        monkeypatch,
        [_fail_result("PROVIDER_OUTAGE"), _ok_result()],
        sleeps=sleeps)
    p = plan("open the calculator please")
    assert isinstance(p, TypedProposal)
    assert p.tool == "local.open_app"
    assert len(calls) == 2


def test_persistent_outage_is_typed_no_decision_never_raises(monkeypatch):
    sleeps = []
    calls = _scripted(
        monkeypatch,
        [_fail_result("PROVIDER_OUTAGE"), _fail_result("TIMEOUT")],
        sleeps=sleeps)
    assert plan("anything") is None  # typed no-decision, no exception
    assert len(calls) == 2  # bounded: never more than one retry
    assert len(sleeps) == 1


def test_non_retryable_failure_does_not_retry(monkeypatch):
    sleeps = []
    calls = _scripted(
        monkeypatch, [_fail_result("AUTH_MISSING")], sleeps=sleeps)
    assert plan("anything") is None
    assert len(calls) == 1  # no retry on config/auth failures
    assert sleeps == []


def test_retry_timeout_is_load_aware_longer(monkeypatch):
    sleeps = []
    calls = _scripted(
        monkeypatch,
        [_fail_result("TIMEOUT"), _ok_result()],
        sleeps=sleeps)
    plan("open the calculator please", timeout_s=8.0)
    assert len(calls) == 2
    first, second = calls[0]["timeout_s"], calls[1]["timeout_s"]
    assert first == 8.0
    assert second > first  # retry gives the slow local model more room


def test_honest_no_decision_preserved_after_retry(monkeypatch):
    # Unknown tool on the retry success still fails closed.
    sleeps = []
    calls = _scripted(
        monkeypatch,
        [_fail_result("TIMEOUT"),
         _ok_result(tool="local.delete_everything", args={})],
        sleeps=sleeps)
    assert plan("do anything at all") is None
    assert len(calls) == 2

    # Undeclared args still fail closed (no retry on validation: success
    # path returns None from _validate, not from transport).
    calls2 = _scripted(
        monkeypatch,
        [_ok_result(tool="local.volume_up", args={"percent": 80})])
    assert plan("set volume higher") is None
    assert len(calls2) == 1


def test_planner_contract_unchanged_schema_tokens():
    # Static schema shape for existing importers; max_tokens 1024 kept at
    # the single run_role call site (Lane 3 base64 budget).
    import inspect

    import voicebridge.planner as PL

    assert set(PLANNER_SCHEMA["properties"]["tool"]["enum"]) == (
        set(PLANNER_TOOLS) | {"none"})
    assert PLANNER_SCHEMA["required"] == ["tool", "confidence"]
    assert PLANNER_SCHEMA["additionalProperties"] is False
    src = inspect.getsource(PL.plan)
    assert "max_tokens=1024" in src
