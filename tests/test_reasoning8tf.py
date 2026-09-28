"""Packet 8T-F — reasoning-effort budgets (Hermes --reasoning parity).

Proves effort levels map to bounded per-request output caps only: runner
options (num_ctx, placement, keep-alive) are never touched, so selecting
a level cannot evict or reload the resident model. Unknown levels fail
closed to the default budget.
"""
from __future__ import annotations


def test_budget_mapping_bounded_and_closed():
    from voicebridge.local_llm import (DEFAULT_REASONING_BUDGET,
                                       REASONING_BUDGETS,
                                       resolve_reasoning_budget)

    assert resolve_reasoning_budget(None) == DEFAULT_REASONING_BUDGET
    assert resolve_reasoning_budget("nonsense-level") == DEFAULT_REASONING_BUDGET
    assert resolve_reasoning_budget("  HIGH ") == REASONING_BUDGETS["high"]
    budgets = [resolve_reasoning_budget(k) for k in REASONING_BUDGETS]
    assert budgets == sorted(budgets) and all(b <= 2048 for b in budgets)


def test_reasoning_kwarg_only_touches_num_predict(monkeypatch):
    import voicebridge.local_llm as L

    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json as _json
            return _json.dumps(
                {"message": {"content": "ok"}}).encode()

    def _fake_urlopen(req, timeout=None):
        import json as _json
        seen.update(_json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert L.chat_local("hi", reasoning="high") == "ok"
    assert seen["options"]["num_predict"] == L.REASONING_BUDGETS["high"]
    assert L.chat_local("hi") == "ok"
    assert seen["options"]["num_predict"] == L.DEFAULT_REASONING_BUDGET


def test_runner_options_untouched_by_reasoning(monkeypatch):
    import voicebridge.local_llm as L

    payloads = []
    import json as _json

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return _json.dumps({"message": {"content": "ok"}}).encode()

    def _fake_urlopen(req, timeout=None):
        payloads.append(_json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    L.chat_local("hi", reasoning="max")
    L.chat_local("hi", reasoning="none")
    first, second = payloads[0], payloads[1]
    assert first["model"] == second["model"]
    assert first["options"]["num_ctx"] == second["options"]["num_ctx"]
    assert first["keep_alive"] == second["keep_alive"] == -1
    assert first["options"]["num_predict"] != second["options"]["num_predict"]
