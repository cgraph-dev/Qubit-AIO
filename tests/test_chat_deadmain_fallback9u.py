"""Lane 2 (9U) — voice "in chat" must not die on the dead main model.

Journal-proof (2026-10-03 08:56:24): "In chat, create VoiceTest.txt…"
-> route-word:in-chat -> worker.dispatch ran 2.9s
-> ``full_turn_unok MODEL_ERROR: model opencode-go/muse-spark-1.3-contributor
is unavailable at turn time (ModelProtocolUnsupported)``
-> fell back to brain WORDS "(confirm before acting)" with no staged task.

The owner keeps muse as main for interactive /chat (works there via
streaming); voice headless turns use the gateway non-streaming shape that
muse rejects. Voice must not inherit a dead main.

Contract pinned here (chat_transport._turn_task only):

* dead-main (recorded) -> fallback turn OK on deepseek-v4-flash/qubit-go
  for THIS turn only, audited, receipt naming both models, no slot
  mutation, explicit user model choice untouched elsewhere;
* healthy-main -> no fallback (today's path byte-identical);
* turn-time dead (no pre-record; first wait fails MODEL_ERROR naming the
  dead model, record appears) -> one retry via fallback, then OK;
* both-dead -> LOUD typed failure (never ok:true, never brain-narration
  masquerading as action, zero worker launches when pre-recorded).

Hermetic: store/e-stop/resolver/dead-record/launch/wait/summarizer are all
doubles — no model, no worker thread, no network, no slot writes.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.providers import resolver as R  # noqa: E402

_DEAD_PROVIDER = "opencode-go"
_DEAD_MODEL = "muse-spark-1.3-contributor"
_DEAD_PID = "opencode-go-muse-spark-1.3-contributor"
_FB_PROVIDER = "opencode-go"
_FB_MODEL = "deepseek-v4-flash"
_FB_PID = "qubit-go"

_DONE_OK = {"status": "done",
            "reply": "Created VoiceTest.txt with the greeting page.",
            "steps": 1, "tool_used": "local.files_upload"}


class _FakeStore:
    """Minimal chat_transport store double (ensure/append only)."""

    def __init__(self):
        self.rows = {}
        self.n = 0

    def ensure(self, sid, *, model=None, identity="dashboard:user"):
        sid = (sid or "").strip() or "dashboard-chat"
        self.rows.setdefault(sid, [])
        return {"state": "OK", "session": {"id": sid}, "messages": []}

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.setdefault(sid, []).append(
            {"role": role, "content": content, "meta": meta or {}})
        self.n += 1
        return {"state": "OK",
                "message": {"role": role, "content": content},
                "message_count": self.n}


def _eff(provider, model, pid):
    return types.SimpleNamespace(
        provider=provider, model=model, profile_id=pid, kind="api",
        route="frontier", executable=True, egress_required=True,
        egress_allowed=True, policy_enabled=True, allowlisted=True,
        source="test", context_tokens=200000)


def _hermetic(monkeypatch, fake, *, default_eff, dead_pairs):
    """Stub store/e-stop/resolver/dead-record/select-guard.

    ``dead_pairs`` maps (provider, model-lower) -> record dict.
    """
    from voicebridge import chat_transport as CT
    from voicebridge.providers import dead_models as DM

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)

    def _resolve(cfg=None, *args, **kwargs):
        pid = kwargs.get("profile_id")
        role = kwargs.get("role", "chat")
        if args:
            raise R.ResolverError("BAD_CALL", "positional resolve unsupported")
        if pid in (None, "") and str(role or "chat") == "chat":
            return default_eff
        if pid == _FB_PID:
            return _eff(_FB_PROVIDER, _FB_MODEL, _FB_PID)
        raise R.ResolverError("UNKNOWN_PROFILE", f"no profile {pid!r}")

    monkeypatch.setattr(R, "resolve", _resolve)

    def _is_dead(cfg, provider, model):
        key = (str(provider or "").strip().lower(),
               str(model or "").strip().lower())
        rec = dead_pairs.get(key)
        return dict(rec) if isinstance(rec, dict) else None

    monkeypatch.setattr(DM, "is_dead", _is_dead)

    # The slot writers must never run on the fallback path (THIS turn only,
    # in-memory shim — the owner's live slots stay byte-identical).
    from voicebridge.providers import endpoints as EP

    def _boom(*a, **k):
        raise AssertionError("slot writer must not run on a fallback turn")

    monkeypatch.setattr(EP, "select_model", _boom)
    monkeypatch.setattr(EP, "activate_endpoint", _boom)
    monkeypatch.setattr(R, "set_active_profile", _boom)


def _stub_worker(monkeypatch, *, waits, seen_models):
    from voicebridge import agent as AG
    from voicebridge import orchestrator as ORC

    def _launch(task, session=None, **kw):
        try:
            seen_models.append(AG._model(kw.get("cfg", {})))
        except Exception as exc:  # noqa: BLE001 - record, never hide
            seen_models.append(f"<model-error: {exc}>")
        return "worker-deadmain-9u"

    it = {"n": 0}

    def _wait(key, timeout_s=300, **kw):
        idx = min(it["n"], len(waits) - 1)
        it["n"] += 1
        res = waits[idx]
        if callable(res):
            return res()
        return dict(res)

    monkeypatch.setattr(ORC, "launch_task", _launch)
    monkeypatch.setattr(ORC, "wait_task", _wait)
    monkeypatch.setattr("voicebridge.summarizer.summarize",
                        lambda reply, cfg=None: "Created VoiceTest.txt.")
    return it


def _dead_record():
    return {(_DEAD_PROVIDER.lower(), _DEAD_MODEL.lower()):
            {"provider": _DEAD_PROVIDER, "model": _DEAD_MODEL,
             "reason": "MODEL_UNAVAILABLE_TURN"}}


def test_dead_main_falls_back_this_turn_only(monkeypatch):
    """Recorded-dead main -> fallback turn OK, receipt names both models."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake, default_eff=_eff(
        _DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID), dead_pairs=_dead_record())
    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-deadmain-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "task", out
    receipt = out.get("receipt") or {}
    assert receipt.get("model") == _FB_MODEL, receipt
    fb = receipt.get("model_fallback") or {}
    assert fb.get("to_model") == _FB_MODEL, receipt
    assert fb.get("to_profile") == _FB_PID, receipt
    assert fb.get("from_model") == _DEAD_MODEL, receipt
    # The worker actually ran under the fallback model (THIS turn only).
    assert seen == [_FB_MODEL], seen


def test_healthy_main_has_no_fallback(monkeypatch):
    """No dead record -> today's path byte-identical, no fallback marker."""
    from voicebridge import chat_transport as CT

    healthy = _eff(_FB_PROVIDER, _FB_MODEL, _FB_PID)
    fake = _FakeStore()
    _hermetic(monkeypatch, fake, default_eff=healthy, dead_pairs={})
    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-healthymain-9u")
    assert out.get("ok") is True, out
    receipt = out.get("receipt") or {}
    assert "model_fallback" not in receipt, receipt
    assert seen == [_FB_MODEL], seen


def test_turn_time_dead_retries_once_via_fallback(monkeypatch):
    """No pre-record; first wait dies naming the dead model -> one retry OK.

    Mirrors the journal: the gateway records the dead model on the failed
    turn (MODEL_UNAVAILABLE_TURN), so the retry sees the record.
    """
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    pairs = {}
    _hermetic(monkeypatch, fake, default_eff=_eff(
        _DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID), dead_pairs=pairs)
    seen = []

    def _die_first():
        # The failed turn records the dead model (gateway behavior) before
        # the retry's pre-check runs.
        pairs.update(_dead_record())
        return {"status": "error", "reply": "",
                "code": "MODEL_ERROR",
                "detail": f"model {_DEAD_PROVIDER}/{_DEAD_MODEL} is "
                          "unavailable at turn time "
                          "(ModelProtocolUnsupported)"}

    calls = _stub_worker(monkeypatch, waits=[_die_first, _DONE_OK],
                         seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-turntimedead-9u")
    assert out.get("ok") is True, out
    assert calls["n"] == 2, calls
    receipt = out.get("receipt") or {}
    assert receipt.get("model") == _FB_MODEL, receipt
    assert (receipt.get("model_fallback") or {}).get("from_model") \
        == _DEAD_MODEL, receipt
    assert seen[0] == _DEAD_MODEL and seen[1] == _FB_MODEL, seen


def test_both_dead_fails_loud_with_zero_launches(monkeypatch):
    """Dead main + dead fallback -> LOUD typed failure, nothing launched."""
    from voicebridge import chat_transport as CT

    pairs = dict(_dead_record())
    pairs[(_FB_PROVIDER.lower(), _FB_MODEL.lower())] = {
        "provider": _FB_PROVIDER, "model": _FB_MODEL,
        "reason": "MODEL_UNAVAILABLE_TURN"}
    fake = _FakeStore()
    _hermetic(monkeypatch, fake, default_eff=_eff(
        _DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID), dead_pairs=pairs)

    def _boom(task, session=None, **kw):
        raise AssertionError("both-dead must launch zero workers")

    import voicebridge.orchestrator as ORC

    monkeypatch.setattr(ORC, "launch_task", _boom)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-bothdead-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_MODEL_UNAVAILABLE", out
    assert _DEAD_MODEL in str(out.get("detail", "")), out
    assert _FB_MODEL in str(out.get("detail", "")), out
    assert out.get("route") == "task", out
