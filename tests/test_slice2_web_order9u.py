"""Slice 2 (voice front-door): web order + hardening.

New order (``intent.web_paid_first: true``): sonar-pro API (retry +
sonar degrade) -> browser-Pro (CDP-healthy only, 25 s bound) -> DDG
last resort. 10-minute identical-query cache. CDP health pre-flight
with a loud degrade, never silent.

TDD: these pin the new helpers before they exist (red), then the
implementation must turn them green without touching the
classifier/route-word functions (Agent 1's region).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeChatStore:
    def __init__(self):
        self.rows = []

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.append({"sid": sid, "role": role, "content": content,
                          "meta": meta or {}})
        return {"state": "OK",
                "message": {"role": role, "content": content},
                "message_count": len(self.rows)}


def _paid_ok(answer="Sonar-pro answer", sources=None):
    return {"ok": True, "answer": answer,
            "sources": sources if sources is not None
            else ["https://example/sonar"],
            "code": "OK"}


def test_cdp_healthy_false_when_down(monkeypatch):
    """CDP DOWN (this host): pre-flight says False, loudly, never raises."""
    from voicebridge import chat_transport as CT
    from voicebridge import cdp_supervisor as CDPS

    import urllib.request

    def _refused(*a, **k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(CDPS, "read_health", lambda *a, **k: None)
    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    assert CT._cdp_healthy() is False


def test_cdp_healthy_true_when_up(monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import cdp_supervisor as CDPS

    import urllib.request

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b"[]"

    monkeypatch.setattr(CDPS, "read_health", lambda *a, **k: None)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _Resp())
    assert CT._cdp_healthy() is True


def test_cdp_healthy_short_circuits_on_fresh_supervisor_verdict(monkeypatch):
    """Consolidation pin: fresh supervisor verdict cdp_up=True wins even when
    :9222 refuses (health file short-circuits the direct probe)."""
    from voicebridge import chat_transport as CT
    from voicebridge import cdp_supervisor as CDPS
    import urllib.request

    def _refused(*a, **k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    monkeypatch.setattr(CDPS, "read_health",
                        lambda *a, **k: {"cdp_up": True,
                                         "verdict": "healthy", "ts": 1e12})
    assert CT._cdp_healthy() is True


def test_browser_pro_bound_and_loud_on_cdp_down(monkeypatch):
    """Browser layer: 25 s bound, CDP-down degrades loud (None + audit)."""
    from voicebridge import chat_transport as CT

    assert CT._BROWSER_PRO_TIMEOUT_S <= 25
    monkeypatch.setattr(CT, "_cdp_healthy", lambda *a, **k: False)
    out = CT._browser_pro_web_search("latest node version")
    assert out is None


def test_paid_retry_degrades_sonar_pro_to_sonar(monkeypatch):
    """Simulated 429: first attempt fails, retry wins on the sonar model."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    calls = []

    def _flaky(cfg, text, **kw):
        try:
            from voicebridge import chat_transport as _CT
            model = _CT._paid_model_name(cfg)
        except Exception:
            model = "?"
        calls.append(model)
        if len(calls) == 1:
            return {"ok": False, "code": "WEB_UNAVAILABLE",
                    "answer": "429 too many requests", "sources": []}
        return {"ok": True, "answer": "degraded answer",
                "sources": ["https://example/sonar"], "code": "OK"}

    monkeypatch.setattr(ORC, "answer_web_query", _flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = CT._paid_api_with_retry(
        {"intent": {"perplexity_model": "sonar-pro"}}, "some web question")
    assert out.get("ok") is True, out
    assert out.get("answer") == "degraded answer", out
    assert len(calls) == 2, calls
    assert "sonar-pro" in str(calls[0]), calls
    assert calls[1] == "sonar", calls


def test_paid_retry_gives_up_typed(monkeypatch):
    """Both attempts down -> typed WEB_UNAVAILABLE, nothing fabricated."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    monkeypatch.setattr(
        ORC, "answer_web_query",
        lambda cfg, text, **kw: {"ok": False, "code": "WEB_UNAVAILABLE",
                                 "answer": "down", "sources": []})
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = CT._paid_api_with_retry(
        {"intent": {"perplexity_model": "sonar-pro"}}, "some web question")
    assert out.get("ok") is False, out
    assert out.get("code") == "WEB_UNAVAILABLE", out


def test_identical_query_cache_skips_layers(monkeypatch):
    """Second identical _turn_web hits the 10-min cache: zero layer calls."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    CT._web_cache_clear()
    paid_calls: list = []
    ddg_calls: list = []

    def _paid(cfg, text, **kw):
        paid_calls.append(text)
        return _paid_ok()

    monkeypatch.setattr(ORC, "answer_web_query", _paid)
    monkeypatch.setattr(CT, "_ddg_web_fallback",
                        lambda q: ddg_calls.append(q) or None)
    monkeypatch.setattr(CT, "_cdp_healthy", lambda *a, **k: False)
    cfg = {"intent": {"web_paid_first": True}}
    fake = _FakeChatStore()
    q = "slice2 cache probe question about Aion 2 release"
    first = CT._turn_web(cfg, fake, "cache-9u", q)
    assert first.get("ok") is True, first
    assert len(paid_calls) == 1, paid_calls
    second = CT._turn_web(cfg, fake, "cache-9u", q)
    assert second.get("ok") is True, second
    assert second.get("text") == first.get("text"), (first, second)
    assert len(paid_calls) == 1, paid_calls
    assert ddg_calls == [], ddg_calls


def test_paid_first_order_browser_middle(monkeypatch):
    """web_paid_first=true: paid miss -> browser tried -> DDG last resort."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    CT._web_cache_clear()
    order: list = []
    monkeypatch.setattr(
        ORC, "answer_web_query",
        lambda cfg, text, **kw: order.append("paid") or {
            "ok": False, "code": "WEB_UNAVAILABLE",
            "answer": "paid down", "sources": []})
    monkeypatch.setattr(
        CT, "_browser_pro_web_search",
        lambda q, **kw: order.append("browser") or None)
    monkeypatch.setattr(
        CT, "_ddg_web_fallback",
        lambda q: order.append("ddg") or {
            "answer": "DDG last resort answer",
            "sources": ["https://example/ddg"]})
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake = _FakeChatStore()
    out = CT._turn_web({"intent": {"web_paid_first": True}}, fake,
                       "order-9u", "slice2 order probe question")
    assert out.get("ok") is True, out
    assert out.get("text") == "DDG last resort answer", out
    assert order[0] == "paid", order
    assert "browser" in order, order
    assert order[-1] == "ddg", order


def test_paid_win_skips_browser_and_ddg(monkeypatch):
    """Paid hit: browser and DDG never run (no wasted quota/time)."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    CT._web_cache_clear()

    def _boom_browser(q, **kw):
        raise AssertionError("browser must not run when paid wins")

    def _boom_ddg(q):
        raise AssertionError("DDG must not run when paid wins")

    monkeypatch.setattr(ORC, "answer_web_query",
                        lambda cfg, text, **kw: _paid_ok())
    monkeypatch.setattr(CT, "_browser_pro_web_search", _boom_browser)
    monkeypatch.setattr(CT, "_ddg_web_fallback", _boom_ddg)
    fake = _FakeChatStore()
    out = CT._turn_web({"intent": {"web_paid_first": True}}, fake,
                       "paidwin-9u", "slice2 paid win probe question")
    assert out.get("ok") is True, out
    assert out.get("sources") == ["https://example/sonar"], out


def test_legacy_free_first_untouched(monkeypatch):
    """No flag: legacy free-first order byte-identical (DDG win, no paid)."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    CT._web_cache_clear()

    def _boom(cfg, text, **kw):
        raise AssertionError("paid must not run when DDG wins (legacy)")

    monkeypatch.setattr(ORC, "answer_web_query", _boom)
    monkeypatch.setattr(
        CT, "_ddg_web_fallback",
        lambda query: {"answer": "legacy DDG answer",
                       "sources": ["https://example/legacy"]})
    fake = _FakeChatStore()
    out = CT._turn_web({}, fake, "legacy-9u", "slice2 legacy probe question")
    assert out.get("ok") is True, out
    assert out.get("text") == "legacy DDG answer", out


def test_paid_win_receipt_names_paid_model(monkeypatch):
    """Consolidation pin: paid-layer win names the paid model on the receipt
    (never the chat/default model)."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    CT._web_cache_clear()
    monkeypatch.setattr(
        ORC, "answer_web_query",
        lambda cfg, text, **kw: {"ok": True, "answer": "paid answer",
                                 "sources": ["https://example/paid"],
                                 "code": "OK"})
    fake = _FakeChatStore()
    cfg = {"intent": {"web_paid_first": True,
                      "perplexity_model": "sonar-pro"}}
    out = CT._turn_web(cfg, fake, "consol-paid-1",
                       "consolidation paid probe question, perplexity")
    assert out.get("ok") is True, out
    model = (out.get("receipt") or {}).get("model")
    assert model == "sonar-pro", out.get("receipt")
    assert "muse" not in str(model).lower(), out.get("receipt")


def test_paid_degrade_receipt_names_fallback(monkeypatch):
    """Consolidation pin: sonar-pro miss -> sonar win names the fallback."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    CT._web_cache_clear()
    calls = []

    def _flaky(cfg, text, **kw):
        calls.append(CT._paid_model_name(cfg))
        if len(calls) == 1:
            return {"ok": False, "code": "WEB_UNAVAILABLE",
                    "answer": "429", "sources": []}
        return {"ok": True, "answer": "degraded answer",
                "sources": ["https://example/sonar"], "code": "OK"}

    monkeypatch.setattr(ORC, "answer_web_query", _flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake = _FakeChatStore()
    cfg = {"intent": {"web_paid_first": True,
                      "perplexity_model": "sonar-pro"}}
    out = CT._turn_web(cfg, fake, "consol-paid-2",
                       "consolidation degrade probe question, perplexity")
    assert out.get("ok") is True, out
    model = (out.get("receipt") or {}).get("model")
    assert model == "sonar", out.get("receipt")


def test_web_search_browser_probe_is_bound():
    """Slice 2: the CDP browser probe never holds a turn past 25 s."""
    import scripts.web_search as WS

    assert WS.BROWSER_PRO_TIMEOUT_S <= 25


def test_web_search_perplexity_loud_on_cdp_down(monkeypatch, capsys):
    """rc=2 (CDP down) degrades to "" with the reason on stderr — never
    silent."""
    import subprocess

    import scripts.web_search as WS

    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "error: Chromium CDP not reachable on 127.0.0.1:9222"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert WS.perplexity_answer("slice2 probe question", 500) == ""
    err = capsys.readouterr().err.lower()
    assert "cdp" in err and "browser search unavailable" in err, err


def test_web_search_perplexity_loud_on_timeout(monkeypatch, capsys):
    """A stalled probe hits the bound and says so on stderr."""
    import subprocess

    import scripts.web_search as WS

    def _hang(*a, **k):
        assert k.get("timeout", 10**9) <= 25, k
        raise subprocess.TimeoutExpired(cmd="probe", timeout=k.get("timeout"))

    monkeypatch.setattr(subprocess, "run", _hang)
    assert WS.perplexity_answer("slice2 probe question", 500) == ""
    err = capsys.readouterr().err.lower()
    assert "timed out" in err, err
