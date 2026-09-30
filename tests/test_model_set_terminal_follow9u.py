"""9U — main-model switch moves /chat when the pick can run it.

Defect (2026-09-30): picking Muse in the MODEL card + reload left /chat on
deepseek. Two compounded causes:

1. ``POST /api/model/set`` (scope=main) never touched the pinned
   ``terminal.profile`` (``qubit-go``), so the reloaded PTY booted the old
   model while the UI copy promised "Reload now to apply it".
2. Freshly created endpoint profiles default to a 4096 declared window, so
   even a capable model (muse-spark, real window 1M) failed /chat's 64k
   context floor on stale data.

Contract pinned here:

* ``backfill_profile_context`` replaces ONLY the 4096 creation default with a
  genuinely resolved window (never the generic fallback); explicit windows
  and failed probes are untouched.
* ``h_model_set`` scope=main reports ``terminal_follows``: the pin follows
  the pick when the floor guard passes (disk + running config), and honestly
  stays — naming the kept profile and why — when the pick is below the floor.

Hermetic: isolated state dirs, no network (the probe is stubbed; the real
guard's hermetic path accepts the declared window when nothing resolves).
"""
from __future__ import annotations

from voicebridge import config as C
from voicebridge.providers import endpoints as EP
from voicebridge.providers import resolver as R


def _cfg(tmp_path, monkeypatch, terminal_profile="old-pin",
         overrides=None):
    from voicebridge import qubit_runtime

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    data = {
        "logging": {"jsonl": True, "jsonl_path": str(state)},
        "intent": {"frontier": {"profile_overrides": dict(overrides or {})}},
        "terminal": {"profile": terminal_profile},
    }
    cfg = C.Config(data=data, path=tmp_path / "voice.yaml")
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    (tmp_path / "qh").mkdir(parents=True, exist_ok=True)
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - hermetic home binding only
        pass
    return cfg


def _api_record(ctx=None):
    record = {"base_url": "https://api.example.com/v1", "model": "m-big",
              "key_ref": "env:EX_API_KEY"}
    if ctx is not None:
        record["context_length"] = ctx
    return record


# ------------------------------------------------------- context backfill
def test_backfill_replaces_only_the_creation_default(monkeypatch):
    monkeypatch.setattr(EP, "resolve_real_context_tokens", lambda *a: 1_000_000)
    defaulted = R.ModelProfile(id="n", kind=R.API, provider="p", model="m",
                               base_url="https://x/v1", key_ref="env:K",
                               locality="external", egress_consent="allow")
    assert defaulted.context_tokens == 4096
    assert EP.backfill_profile_context(defaulted) is True
    assert defaulted.context_tokens == 1_000_000

    explicit = R.ModelProfile(id="e", kind=R.API, provider="p", model="m",
                              base_url="https://x/v1", key_ref="env:K",
                              locality="external", egress_consent="allow",
                              context_tokens=200_000)
    assert EP.backfill_profile_context(explicit) is False
    assert explicit.context_tokens == 200_000


def test_backfill_keeps_default_when_probe_fails(monkeypatch):
    monkeypatch.setattr(EP, "resolve_real_context_tokens", lambda *a: None)
    profile = R.ModelProfile(id="n", kind=R.API, provider="p", model="m",
                             base_url="https://x/v1", key_ref="env:K",
                             locality="external", egress_consent="allow")
    assert EP.backfill_profile_context(profile) is False
    assert profile.context_tokens == 4096


def test_activate_records_explicit_window_without_probing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        EP, "resolve_real_context_tokens",
        lambda *a: calls.append(a) or 1_000_000)
    cfg = _cfg(tmp_path, monkeypatch)
    out = EP.activate_endpoint(cfg, "big-ep", _api_record(ctx=200_000),
                               confirm=True)
    assert out["ok"] is True, out
    assert calls == []
    assert R.get_profile(cfg, "big-ep").context_tokens == 200_000


# ------------------------------------------------------- local tier picks
def _register_local(cfg, pid, ctx=1_048_576):
    profile = R.ModelProfile(
        id=pid, kind=R.LOCAL, provider="ollama", model=pid,
        locality="local", egress_consent="deny", context_tokens=ctx,
        supports_streaming=True, roles=["chat"], enabled=True,
        rollback_to=pid, provenance="ollama://local")
    out = R.upsert_model_profile(cfg, profile, confirm=True)
    assert out["state"] == "OK", out


def test_local_pick_activates_without_endpoint(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _register_local(cfg, "qubit-spark17")
    body = {"scope": "main", "provider": "ollama", "model": "qubit-spark17",
            "confirm_expensive_model": True}
    code, out = QC.h_model_set(cfg, {}, body, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert R.get_active_profile(cfg)["active"] == "qubit-spark17"


def test_local_pick_moves_main_but_keeps_chat_pin(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    pid = "ex-big"
    cfg = _cfg(tmp_path, monkeypatch, terminal_profile=pid,
               overrides=_executable_overrides(pid))
    created = EP.activate_endpoint(cfg, pid, _api_record(ctx=200_000),
                                   confirm=True)
    assert created["ok"] is True, created
    _register_local(cfg, "qubit-spark4")

    body = {"scope": "main", "provider": "ollama", "model": "qubit-spark4",
            "confirm_expensive_model": True}
    code, out = QC.h_model_set(cfg, {}, body, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert R.get_active_profile(cfg)["active"] == "qubit-spark4"
    assert out["terminal_follows"] is False, out
    assert out["terminal_profile_id"] == pid, out
    assert "local model" in (out["terminal_detail"] or ""), out
    assert cfg.get("terminal", "profile", default="") == pid


def test_shipped_default_local_is_pickable(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"scope": "main", "provider": "ollama", "model": "qubit-brain4",
            "confirm_expensive_model": True}
    code, out = QC.h_model_set(cfg, {}, body, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert R.get_active_profile(cfg)["active"] == "qubit-brain4"
# ------------------------------------------------------- terminal follow
def _executable_overrides(pid):
    return {pid: {"allowlisted": True, "policy_enabled": True}}


def test_model_set_moves_terminal_pin_when_floor_met(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    pid = "ex-big"
    cfg = _cfg(tmp_path, monkeypatch, terminal_profile="old-pin",
               overrides=_executable_overrides(pid))
    created = EP.activate_endpoint(cfg, pid, _api_record(ctx=200_000),
                                   confirm=True)
    assert created["ok"] is True, created

    body = {"scope": "main", "provider": "ex-big", "model": "m-big",
            "base_url": "https://api.example.com/v1",
            "key_ref": "env:EX_API_KEY",
            "confirm_expensive_model": True}
    code, out = QC.h_model_set(cfg, {}, body, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["terminal_follows"] is True, out
    assert out["terminal_profile_id"] == pid, out
    assert cfg.get("terminal", "profile", default="") == pid
    import yaml

    on_disk = yaml.safe_load((tmp_path / "voice.yaml").read_text())
    assert on_disk["terminal"]["profile"] == pid


def test_model_set_keeps_pin_and_says_so_when_below_floor(tmp_path,
                                                          monkeypatch):
    from voicebridge import qubit_compat as QC

    pid = "ex-small"
    cfg = _cfg(tmp_path, monkeypatch, terminal_profile="old-pin",
               overrides=_executable_overrides(pid))
    created = EP.activate_endpoint(cfg, pid, _api_record(), confirm=True)
    assert created["ok"] is True, created
    assert R.get_profile(cfg, pid).context_tokens == 4096

    body = {"scope": "main", "provider": "ex-small", "model": "m-big",
            "base_url": "https://api.example.com/v1",
            "key_ref": "env:EX_API_KEY",
            "confirm_expensive_model": True}
    code, out = QC.h_model_set(cfg, {}, body, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["terminal_follows"] is False, out
    assert out["terminal_profile_id"] == "old-pin", out
    assert "old-pin" in (out["terminal_detail"] or ""), out
    assert cfg.get("terminal", "profile", default="") == "old-pin"
