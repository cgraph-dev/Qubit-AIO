"""9U — model select refuses dead upstream models (slot unchanged).

Defect (proven live 4+ times): the main model slot drifts back to dead
upstream models (muse-spark ``ModelProtocolUnsupported``, deepseek outage)
via ``POST /api/model/set`` — including dashboard-initiated picks. Every
drift breaks all default turns until someone hand-repairs the slot.

Contract pinned here:

* Before a REMOTE pick is persisted, ``select_model``/``activate_endpoint``
  runs the existing bounded liveness/context probe
  (``resolver._probe_real_context_window`` — reused, not invented).
* Explicit negative evidence — ``probe-timeout`` or ``probe-failed:*``
  (where ``ModelProtocolUnsupported``/unavailable/timeout surfaces) or a
  probe that raises — fails closed with typed ``MODEL_UNAVAILABLE`` and the
  slot byte-unchanged (no profile registered, active untouched, runtime
  ``config.yaml`` untouched).
* Inconclusive probe results (``resolver-unavailable``/``unresolved``/
  ``fallback`` — e.g. hermetic/offline) are NOT negative evidence: the
  pick proceeds as before.
* Local-tier picks skip the probe entirely (no credentials needed).

Hermetic: isolated state dirs, mocked probe, no network.
"""
from __future__ import annotations

from voicebridge import config as C
from voicebridge.providers import endpoints as EP
from voicebridge.providers import resolver as R

_DEAD_PROVIDER = "opencode-go"
_DEAD_MODEL = "muse-spark-1.3-contributor"
_DEAD_BASE = "https://opencode.ai/zen/go/v1"
_DEAD_KEY = "env:OC_GO_API_KEY"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    data = {
        "logging": {"jsonl": True, "jsonl_path": str(state)},
        "intent": {"frontier": {"profile_overrides": {}}},
    }
    cfg = C.Config(data=data, path=tmp_path / "voice.yaml")
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    (tmp_path / "qh").mkdir(parents=True, exist_ok=True)
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - hermetic home binding only
        pass
    return cfg


def _register_local(cfg, pid):
    profile = R.ModelProfile(
        id=pid, kind=R.LOCAL, provider="ollama", model=pid,
        locality="local", egress_consent="deny", context_tokens=1_048_576,
        supports_streaming=True, roles=["chat"],
        enabled=True, rollback_to=pid, provenance="ollama://local")
    out = R.upsert_model_profile(cfg, profile, confirm=True)
    assert out["state"] == "OK", out


def _working_slot(cfg):
    """Establish a healthy local slot via the sanctioned writer; snapshot it."""
    from voicebridge import qubit_runtime

    _register_local(cfg, "qubit-spark17")
    out = EP.select_model(cfg, "ollama", "qubit-spark17", confirm=True)
    assert out["ok"] is True, out
    runtime_cfg = qubit_runtime.runtime_home_for_cfg(cfg) / "config.yaml"
    return {
        "active": R.get_active_profile(cfg)["active"],
        "runtime_bytes": runtime_cfg.read_bytes(),
        "runtime_path": runtime_cfg,
    }


def _slot_now(cfg):
    from voicebridge import qubit_runtime

    runtime_cfg = qubit_runtime.runtime_home_for_cfg(cfg) / "config.yaml"
    return {
        "active": R.get_active_profile(cfg)["active"],
        "runtime_bytes": runtime_cfg.read_bytes()
        if runtime_cfg.is_file() else None,
    }


def _dead_kwargs():
    return {"base_url": _DEAD_BASE, "key_ref": _DEAD_KEY, "confirm": True}


def test_dead_remote_refused_and_slot_unchanged(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    before = _working_slot(cfg)
    monkeypatch.setattr(
        R, "_probe_real_context_window",
        lambda *a, **k: (None, "probe-failed:ModelProtocolUnsupported"))

    out = EP.select_model(cfg, _DEAD_PROVIDER, _DEAD_MODEL, **_dead_kwargs())

    assert out["ok"] is False, out
    assert out["code"] == "MODEL_UNAVAILABLE", out
    # The refusal names the requirement and the still-working slot.
    assert "qubit-spark17" in str(out.get("detail", "")), out
    # Slot byte-unchanged: no profile, same active, same runtime file.
    assert R.get_profile(cfg, EP._slug_model_id(
        _DEAD_PROVIDER, _DEAD_MODEL)) is None
    after = _slot_now(cfg)
    assert after["active"] == before["active"] == "qubit-spark17"
    assert after["runtime_bytes"] == before["runtime_bytes"]


def test_healthy_remote_accepted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _working_slot(cfg)
    monkeypatch.setattr(
        R, "_probe_real_context_window",
        lambda *a, **k: (1048576, "resolved"))

    out = EP.select_model(
        cfg, _DEAD_PROVIDER, "deepseek-v4-flash",
        base_url=_DEAD_BASE, key_ref=_DEAD_KEY, confirm=True)

    assert out["ok"] is True, out
    assert R.get_active_profile(cfg)["active"] == out["profile_id"]
    eff = R.resolve_model(cfg)
    assert eff["ok"] is True and eff["model"] == "deepseek-v4-flash"


def test_local_pick_skips_probe(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _register_local(cfg, "qubit-spark4")

    def _boom(*a, **k):
        raise AssertionError("probe must not run for local picks")

    monkeypatch.setattr(R, "_probe_real_context_window", _boom)

    out = EP.select_model(cfg, "ollama", "qubit-spark4", confirm=True)
    assert out["ok"] is True, out
    assert R.get_active_profile(cfg)["active"] == "qubit-spark4"


def test_probe_error_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    before = _working_slot(cfg)

    def _raises(*a, **k):
        raise TimeoutError("black-holed endpoint")

    monkeypatch.setattr(R, "_probe_real_context_window", _raises)

    out = EP.select_model(cfg, _DEAD_PROVIDER, _DEAD_MODEL, **_dead_kwargs())

    assert out["ok"] is False, out
    assert out["code"] == "MODEL_UNAVAILABLE", out
    after = _slot_now(cfg)
    assert after["active"] == before["active"]
    assert after["runtime_bytes"] == before["runtime_bytes"]


def test_probe_timeout_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    before = _working_slot(cfg)
    monkeypatch.setattr(
        R, "_probe_real_context_window",
        lambda *a, **k: (None, "probe-timeout"))

    out = EP.select_model(cfg, _DEAD_PROVIDER, _DEAD_MODEL, **_dead_kwargs())

    assert out["ok"] is False, out
    assert out["code"] == "MODEL_UNAVAILABLE", out
    after = _slot_now(cfg)
    assert after["active"] == before["active"]
    assert after["runtime_bytes"] == before["runtime_bytes"]


def test_inconclusive_probe_still_accepts(tmp_path, monkeypatch):
    # No negative evidence (offline/hermetic) must not change today's
    # accept behavior — the existing repair/select tests rely on it.
    cfg = _cfg(tmp_path, monkeypatch)
    _working_slot(cfg)
    monkeypatch.setattr(
        R, "_probe_real_context_window",
        lambda *a, **k: (None, "resolver-unavailable:ModuleNotFoundError"))

    out = EP.select_model(
        cfg, _DEAD_PROVIDER, "deepseek-v4-flash",
        base_url=_DEAD_BASE, key_ref=_DEAD_KEY, confirm=True)
    assert out["ok"] is True, out
