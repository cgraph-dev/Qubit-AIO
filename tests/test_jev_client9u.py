"""Jev decision-layer client (OpenRouter alpha decisions) — mock-first.

Prep lane, NEW FILES ONLY. No live calls: no OPENROUTER_API_KEY exists on
this host, so every live-path test skips LOUDLY and the mock backend proves
the wire shape (endpoint / native body / answer parse) instead.

Conventions: ``sys.path`` insert like the other ``*9u`` suites; ``FakeCfg``
mirrors the repo's ``.get(*path, default=...)`` cfg read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.jev import client as J


class FakeCfg:
    def __init__(self, data):
        self._d = data

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def _check_q(qid="q1"):
    return {"id": qid, "kind": "check",
            "prompt": "is this destructive?"}


def _pick_q(qid="q2"):
    return {"id": qid, "kind": "pick",
            "prompt": "which tier?",
            "options": ["fast", "detail"]}


def _rate_q(qid="q3"):
    return {"id": qid, "kind": "rate",
            "prompt": "how risky (0-1)?"}


# ---------------------------------------------------------------------------
# 1. mock backend: deterministic canned verdicts (check / pick / rate)
# ---------------------------------------------------------------------------

def test_mock_check_verdict_shape():
    out = J.judge({"turn": "hi"}, [_check_q()], backend="mock")
    assert out["ok"] is True
    (ans,) = out["answers"]
    assert ans["id"] == "q1" and ans["kind"] == "check"
    assert isinstance(ans["answer"], bool)
    assert 0.0 <= ans["confidence"] <= 1.0
    assert ans["escalate"] is False


def test_mock_pick_returns_first_option():
    out = J.judge({"turn": "x"}, [_pick_q()], backend="mock")
    assert out["ok"] is True
    (ans,) = out["answers"]
    assert ans["answer"] == "fast"


def test_mock_rate_returns_midpoint():
    out = J.judge({"turn": "x"}, [_rate_q()], backend="mock")
    assert out["ok"] is True
    (ans,) = out["answers"]
    assert ans["answer"] == pytest.approx(0.5)


def test_mock_verdicts_are_deterministic():
    a = J.judge({"turn": "x"}, [_check_q(), _pick_q(), _rate_q()],
                backend="mock")
    b = J.judge({"turn": "x"}, [_check_q(), _pick_q(), _rate_q()],
                backend="mock")
    assert a == b


def test_mock_honors_requested_escalate():
    q = dict(_check_q(), escalate=True)
    out = J.judge({"turn": "x"}, [q], backend="mock")
    assert out["answers"][0]["escalate"] is True


def test_mock_env_switch_selects_mock_backend(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "mock")
    out = J.judge({"turn": "x"}, [_check_q()], cfg=FakeCfg({}))
    assert out["ok"] is True
    assert out["backend"] == "mock"


# ---------------------------------------------------------------------------
# 2. native wire shape: endpoint / body / parse (no network)
# ---------------------------------------------------------------------------

def test_native_body_carries_state_questions_model_and_no_key():
    body = J.to_native_body({"turn": "hi"}, [_check_q()], model="m1")
    assert body == {"model": "m1", "state": {"turn": "hi"},
                    "questions": [_check_q()]}
    assert "OPENROUTER_API_KEY" not in json.dumps(body)


def test_default_model_defined_once_and_used():
    assert isinstance(J.DEFAULT_MODEL, str) and "/" in J.DEFAULT_MODEL
    seen = {}

    def fake_http(url, body, key, timeout_s):
        seen.update(url=url, body=dict(body),
                    key_len=len(key), timeout_s=timeout_s)
        return {"answers": [{"id": "q1", "answer": True,
                             "confidence": 0.9}]}

    out = J.judge({"turn": "hi"}, [_check_q()], cfg=FakeCfg({}),
                  backend="live", http_post=fake_http,
                  api_key="sk-test-sentinel")
    assert out["ok"] is True
    assert seen["url"] == J.DECISIONS_URL
    assert seen["body"]["model"] == J.DEFAULT_MODEL  # single source


def test_explicit_model_overrides_default():
    seen = {}

    def fake_http(url, body, key, timeout_s):
        seen["model"] = body["model"]
        return {"answers": []}

    J.judge({}, [], model="typesafe/jev-router", cfg=FakeCfg({}),
            backend="live", http_post=fake_http, api_key="k")
    assert seen["model"] == "typesafe/jev-router"


def test_parse_native_answers_normalizes_confidence_and_escalate():
    answers, problems = J.parse_native_answers(
        {"answers": [
            {"id": "a", "answer": "fast", "confidence": 2.5},
            {"id": "b", "answer": None},
            {"id": "c"},  # missing answer -> escalate
        ]},
        [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    by_id = {a["id"]: a for a in answers}
    assert by_id["a"]["confidence"] == pytest.approx(1.0)  # clamped
    assert by_id["a"]["escalate"] is False
    assert by_id["b"]["escalate"] is True
    assert by_id["c"]["escalate"] is True
    assert problems == []


# ---------------------------------------------------------------------------
# 3. fail-CLOSED: transport error / timeout / malformed (never fabricated)
# ---------------------------------------------------------------------------

def _boom(exc):
    def fake_http(url, body, key, timeout_s):
        raise exc
    return fake_http


def test_transport_error_fails_closed_with_no_answers():
    out = J.judge({"t": 1}, [_check_q()], cfg=FakeCfg({}),
                  backend="live", api_key="k",
                  http_post=_boom(OSError("net down")))
    assert out["ok"] is False
    assert out["code"] == "JEV_TRANSPORT_ERROR"
    assert "answers" not in out  # never a fabricated judgment


def test_timeout_fails_closed():
    out = J.judge({"t": 1}, [_check_q()], cfg=FakeCfg({}),
                  backend="live", api_key="k",
                  http_post=_boom(TimeoutError("slow")))
    assert out["ok"] is False
    assert out["code"] == "JEV_TIMEOUT"


def test_malformed_payload_fails_closed():
    def fake_http(url, body, key, timeout_s):
        return {"not-answers": 42}

    out = J.judge({"t": 1}, [_check_q()], cfg=FakeCfg({}),
                  backend="live", api_key="k", http_post=fake_http)
    assert out["ok"] is False
    assert out["code"] == "JEV_MALFORMED"


# ---------------------------------------------------------------------------
# 4. key handling: absent -> loud refusal; live without key -> loud skip
# ---------------------------------------------------------------------------

def test_key_absent_is_a_loud_refusal_not_silence(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = J.judge({"t": 1}, [_check_q()], cfg=FakeCfg({}),
                  backend="live")
    assert out["ok"] is False
    assert out["code"] == "JEV_KEY_ABSENT"
    assert "OPENROUTER_API_KEY" in out["detail"]


def test_live_path_skips_loudly_without_key(monkeypatch):
    """No key exists on this host: the live path stays unproven, loudly."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    if J.key_present(FakeCfg({})):
        pytest.fail("key unexpectedly present; live test needs a key "
                    "rotation plan first")
    pytest.skip("OPENROUTER_API_KEY absent — live path unproven by design; "
                "mock backend covers the wire shape (never silent)")


def test_key_never_appears_in_logs_or_errors():
    sentinel = "sk-jkv-secret-sentinel-9u-abcdef123456"
    fp = J.key_fingerprint(sentinel)
    assert sentinel not in fp  # length-only
    assert "len=" in fp
    out = J.judge({"t": 1}, [_check_q()], cfg=FakeCfg({}),
                  backend="live", api_key=sentinel,
                  http_post=_boom(OSError("boom")))
    assert sentinel not in json.dumps(out)
    assert sentinel not in str(out)
