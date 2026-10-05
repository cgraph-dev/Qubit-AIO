"""9U — dead-main voice fallback resolves dynamically, not one hardcoded id.

Lane 2 hardcoded ``_FALLBACK_MODEL = "deepseek-v4-flash"`` for dead-main
voice turns. Owner directive: the fallback must work for ANY API model, not
one hardcoded id.

Contract pinned here (``chat_transport._turn_task`` fallback selection):

* dead-main + healthy alt-API present -> THIS turn runs on the ALT
  (audited, receipt naming both models + the chosen profile, zero slot
  writes) — NOT the hardcoded deepseek;
* same-provider family preferred (dead opencode-go/* prefers another
  opencode-go profile over a bigger-windowed foreign provider);
* below-floor profiles never qualify (a 4096-ctx alt is skipped);
* only-deepseek-healthy -> deepseek (today's behavior preserved);
* registry unreadable -> the deepseek literal is the last resort (honest,
  still audited, still this-turn-only);
* none healthy -> LOUD ``TASK_MODEL_UNAVAILABLE`` (unchanged), zero launches.

Hermetic: store/e-stop/resolver/load_profiles/dead-record/launch/wait are
all doubles — no model, no worker thread, no network, no slot writes.
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

_ALT_PID = "opencode-go-alt-model"
_ALT_MODEL = "alt-model-9u"

_DONE_OK = {"status": "done",
            "reply": "Created VoiceTest.txt with the greeting page.",
            "steps": 1, "tool_used": "local.files_upload"}


class _FakeStore:
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


def _eff(provider, model, pid, ctx=1_000_000):
    return types.SimpleNamespace(
        provider=provider, model=model, profile_id=pid, kind="api",
        route="frontier", executable=True, egress_required=True,
        egress_allowed=True, policy_enabled=True, allowlisted=True,
        source="test", context_tokens=ctx)


def _prof(pid, provider, model, ctx=1_000_000, kind="api", enabled=True):
    return types.SimpleNamespace(
        id=pid, kind=kind, provider=provider, model=model,
        base_url="https://example.com/v1", key_ref="env:EX",
        context_tokens=ctx, enabled=enabled, roles=["chat"])


def _dead_record(*pairs):
    return {(p.lower(), m.lower()): {"provider": p, "model": m,
                                     "reason": "MODEL_UNAVAILABLE_TURN"}
            for p, m in pairs}


def _hermetic(monkeypatch, fake, *, default_eff, dead_pairs, profiles,
              executable_pids=None):
    """Stub store/e-stop/resolve/load_profiles/dead-record/slot writers."""
    from voicebridge import chat_transport as CT
    from voicebridge.providers import dead_models as DM

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)

    pids = {p.id: p for p in profiles}
    ok_pids = set(executable_pids) if executable_pids is not None \
        else set(pids)

    def _resolve(cfg=None, *args, **kwargs):
        pid = kwargs.get("profile_id")
        role = kwargs.get("role", "chat")
        if args:
            raise R.ResolverError("BAD_CALL", "positional resolve unsupported")
        if pid in (None, "") and str(role or "chat") == "chat":
            return default_eff
        if pid in ok_pids and pid in pids:
            p = pids[pid]
            return _eff(p.provider, p.model, p.id,
                        ctx=int(p.context_tokens or 0))
        raise R.ResolverError("UNKNOWN_PROFILE", f"no profile {pid!r}")

    monkeypatch.setattr(R, "resolve", _resolve)
    monkeypatch.setattr(R, "load_profiles", lambda cfg: list(profiles))

    def _is_dead(cfg, provider, model):
        rec = dead_pairs.get((str(provider or "").strip().lower(),
                              str(model or "").strip().lower()))
        return dict(rec) if isinstance(rec, dict) else None

    monkeypatch.setattr(DM, "is_dead", _is_dead)

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
        return "worker-dynfb-9u"

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


def _base_profiles():
    return [
        _prof(_DEAD_PID, _DEAD_PROVIDER, _DEAD_MODEL, ctx=1_048_576),
        _prof(_FB_PID, _FB_PROVIDER, _FB_MODEL, ctx=200_000),
    ]


# ------------------------------------------------------- dynamic selection
def test_dead_main_prefers_alt_api_over_hardcoded_deepseek(monkeypatch):
    """Dead muse + healthy same-family alt -> the turn runs on the ALT."""
    from voicebridge import chat_transport as CT

    profiles = _base_profiles() + [
        _prof(_ALT_PID, _DEAD_PROVIDER, _ALT_MODEL, ctx=1_000_000)]
    fake = _FakeStore()
    _hermetic(monkeypatch, fake,
              default_eff=_eff(_DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID,
                               ctx=1_048_576),
              dead_pairs=_dead_record((_DEAD_PROVIDER, _DEAD_MODEL)),
              profiles=profiles)
    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-dynalt-9u")
    assert out.get("ok") is True, out
    receipt = out.get("receipt") or {}
    assert receipt.get("model") == _ALT_MODEL, receipt
    fb = receipt.get("model_fallback") or {}
    assert fb.get("to_model") == _ALT_MODEL, receipt
    assert fb.get("to_profile") == _ALT_PID, receipt
    assert fb.get("from_model") == _DEAD_MODEL, receipt
    assert seen == [_ALT_MODEL], seen


def test_same_provider_family_beats_bigger_foreign_window(monkeypatch):
    """Family first: opencode-go 500k beats opencode-zen 1M for a dead
    opencode-go main."""
    from voicebridge import chat_transport as CT

    profiles = [
        _prof(_DEAD_PID, _DEAD_PROVIDER, _DEAD_MODEL, ctx=1_048_576),
        _prof("zen-big", "opencode-zen", "zen-big-9u", ctx=1_000_000),
        _prof("go-mid", _DEAD_PROVIDER, "go-mid-9u", ctx=500_000),
    ]
    fake = _FakeStore()
    _hermetic(monkeypatch, fake,
              default_eff=_eff(_DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID,
                               ctx=1_048_576),
              dead_pairs=_dead_record((_DEAD_PROVIDER, _DEAD_MODEL)),
              profiles=profiles)
    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-dynfamily-9u")
    assert out.get("ok") is True, out
    receipt = out.get("receipt") or {}
    assert receipt.get("model") == "go-mid-9u", receipt
    assert seen == ["go-mid-9u"], seen


def test_below_floor_alt_is_skipped(monkeypatch):
    """A 4096-ctx alt never qualifies -> falls through to deepseek."""
    from voicebridge import chat_transport as CT

    profiles = _base_profiles() + [
        _prof(_ALT_PID, _DEAD_PROVIDER, _ALT_MODEL, ctx=4096)]
    fake = _FakeStore()
    _hermetic(monkeypatch, fake,
              default_eff=_eff(_DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID,
                               ctx=1_048_576),
              dead_pairs=_dead_record((_DEAD_PROVIDER, _DEAD_MODEL)),
              profiles=profiles)
    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-dynfloor-9u")
    assert out.get("ok") is True, out
    receipt = out.get("receipt") or {}
    assert receipt.get("model") == _FB_MODEL, receipt
    assert seen == [_FB_MODEL], seen


def test_only_deepseek_healthy_keeps_deepseek(monkeypatch):
    """No alt present -> the deepseek literal path is preserved."""
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake,
              default_eff=_eff(_DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID,
                               ctx=1_048_576),
              dead_pairs=_dead_record((_DEAD_PROVIDER, _DEAD_MODEL)),
              profiles=_base_profiles())
    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-dynonlyds-9u")
    assert out.get("ok") is True, out
    receipt = out.get("receipt") or {}
    assert receipt.get("model") == _FB_MODEL, receipt
    assert (receipt.get("model_fallback") or {}).get("to_profile") \
        == _FB_PID, receipt
    assert seen == [_FB_MODEL], seen


def test_literal_last_resort_when_registry_unreadable(monkeypatch):
    """load_profiles explodes -> the deepseek literal still serves."""
    from voicebridge import chat_transport as CT
    from voicebridge.providers import dead_models as DM
    from voicebridge.providers import endpoints as EP

    fake = _FakeStore()
    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)

    def _resolve(cfg=None, *args, **kwargs):
        pid = kwargs.get("profile_id")
        if pid in (None, ""):
            return _eff(_DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID,
                        ctx=1_048_576)
        if pid == _FB_PID:
            return _eff(_FB_PROVIDER, _FB_MODEL, _FB_PID, ctx=200_000)
        raise R.ResolverError("UNKNOWN_PROFILE", f"no profile {pid!r}")

    monkeypatch.setattr(R, "resolve", _resolve)

    def _boom_profiles(cfg):
        raise OSError("registry unreadable")

    monkeypatch.setattr(R, "load_profiles", _boom_profiles)

    pairs = _dead_record((_DEAD_PROVIDER, _DEAD_MODEL))
    monkeypatch.setattr(
        DM, "is_dead",
        lambda cfg, p, m: dict(pairs[(p.lower(), m.lower())])
        if (p.lower(), m.lower()) in pairs else None)

    def _boom(*a, **k):
        raise AssertionError("slot writer must not run on a fallback turn")

    monkeypatch.setattr(EP, "select_model", _boom)
    monkeypatch.setattr(EP, "activate_endpoint", _boom)
    monkeypatch.setattr(R, "set_active_profile", _boom)

    seen = []
    _stub_worker(monkeypatch, waits=[_DONE_OK], seen_models=seen)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-dynliteral-9u")
    assert out.get("ok") is True, out
    assert (out.get("receipt") or {}).get("model") == _FB_MODEL, out
    assert seen == [_FB_MODEL], seen


def test_no_healthy_api_fails_loud_with_zero_launches(monkeypatch):
    """Dead main + dead deepseek + dead alt -> TASK_MODEL_UNAVAILABLE."""
    from voicebridge import chat_transport as CT

    profiles = _base_profiles() + [
        _prof(_ALT_PID, _DEAD_PROVIDER, _ALT_MODEL, ctx=1_000_000)]
    pairs = _dead_record((_DEAD_PROVIDER, _DEAD_MODEL),
                         (_FB_PROVIDER, _FB_MODEL),
                         (_DEAD_PROVIDER, _ALT_MODEL))
    fake = _FakeStore()
    _hermetic(monkeypatch, fake,
              default_eff=_eff(_DEAD_PROVIDER, _DEAD_MODEL, _DEAD_PID,
                               ctx=1_048_576),
              dead_pairs=pairs, profiles=profiles)

    def _boom(task, session=None, **kw):
        raise AssertionError("all-dead must launch zero workers")

    import voicebridge.orchestrator as ORC

    monkeypatch.setattr(ORC, "launch_task", _boom)
    out = CT.turn({}, "in chat, create VoiceTest.txt with hello",
                  session="s-dynalldead-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "TASK_MODEL_UNAVAILABLE", out
    assert _DEAD_MODEL in str(out.get("detail", "")), out
    assert _FB_MODEL in str(out.get("detail", "")), out
    assert out.get("route") == "task", out
