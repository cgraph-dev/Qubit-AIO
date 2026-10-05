"""Dead-model explicit-pick warning (repair lane, TDD).

Contract:
- Explicit user picks (select_model/activate_endpoint with explicit=True)
  for a recorded-dead model PROCEED with a plain-language warning
  (slot changes, no refusal).
- Silent paths (gate default explicit=False, terminal follow) keep the
  MODEL_UNAVAILABLE refusal with the slot unchanged.
- The model-options picker never renders a refusal as a model row and
  never shows internal jargon on screen.

Hermetic: isolated state dirs, stubbed probe, no network/secrets.
"""
from __future__ import annotations

from voicebridge.providers import dead_models as DM
from voicebridge.providers import endpoints as EP


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C
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


_DEAD_PROVIDER = "opencode-go"
_DEAD_MODEL = "muse-spark-1.3-contributor"
_DEAD_BASE = "https://opencode.ai/zen/go/v1"
_DEAD_KEY = "env:OC_GO_API_KEY"

_JARGON = ("turn-time", "turn_time", "dead-model record", "dead_record",
           "dead_models.json", "MODEL_UNAVAILABLE_TURN")


def _assert_plain(text: str):
    low = str(text or "").lower()
    for token in _JARGON:
        assert token.lower() not in low, f"jargon {token!r} on screen: {text!r}"


def test_explicit_gate_proceeds_with_warning(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    DM.record_dead(cfg, _DEAD_PROVIDER, _DEAD_MODEL,
                   "ModelProtocolUnsupported")
    monkeypatch.setattr(
        R, "_probe_real_context_window",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("explicit dead-record must not probe")))
    out = EP._gate_remote_liveness(
        cfg=cfg, provider=_DEAD_PROVIDER, model=_DEAD_MODEL,
        base_url=_DEAD_BASE, explicit=True)
    assert out is not None, "explicit gate must return a proceed-warning"
    assert out.get("ok") is True, out
    assert out.get("proceed_over_dead_record") is True, out
    assert str(out.get("warning") or ""), out
    _assert_plain(str(out.get("warning")))
    assert _DEAD_MODEL in str(out.get("warning")), out


def test_silent_gate_still_refuses(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    DM.record_dead(cfg, _DEAD_PROVIDER, _DEAD_MODEL,
                   "ModelProtocolUnsupported")
    monkeypatch.setattr(
        R, "_probe_real_context_window",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("silent dead-record must not probe")))
    out = EP._gate_remote_liveness(
        cfg=cfg, provider=_DEAD_PROVIDER, model=_DEAD_MODEL,
        base_url=_DEAD_BASE)
    assert out is not None and out.get("code") == "MODEL_UNAVAILABLE", out
    _assert_plain(str(out.get("detail")))


def test_explicit_select_moves_slot_with_warning(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    local = R.ModelProfile(
        id="qubit-spark17", kind=R.LOCAL, provider="ollama",
        model="qubit-spark17", locality="local", egress_consent="deny",
        context_tokens=1_048_576, supports_streaming=True, roles=["chat"],
        enabled=True, rollback_to="qubit-spark17",
        provenance="ollama://local")
    assert R.upsert_model_profile(cfg, local, confirm=True)["state"] == "OK"
    assert EP.select_model(
        cfg, "ollama", "qubit-spark17", confirm=True)["ok"] is True
    DM.record_dead(cfg, _DEAD_PROVIDER, _DEAD_MODEL,
                   "ModelProtocolUnsupported")
    monkeypatch.setattr(
        R, "_probe_real_context_window", lambda *a, **k: (1048576, "resolved"))
    out = EP.select_model(
        cfg, _DEAD_PROVIDER, _DEAD_MODEL,
        base_url=_DEAD_BASE, key_ref=_DEAD_KEY, confirm=True)
    assert out["ok"] is True, out
    assert str(out.get("warning") or ""), out
    _assert_plain(str(out.get("warning")))
    assert R.get_active_profile(cfg)["active"] == out["profile_id"]
    runtime_cfg = qubit_runtime.runtime_home_for_cfg(cfg) / "config.yaml"
    assert _DEAD_MODEL in runtime_cfg.read_text(encoding="utf-8")


def test_options_sanitizer_drops_error_rows_and_jargon():
    from voicebridge import qubit_compat as QC

    rows = [
        {"slug": "opencode-go", "name": "OpenCode Go",
         "models": ["deepseek-v4-flash", "MODEL_UNAVAILABLE: turn-time "
                    "dead-model record (dead_models.json)"],
         "total_models": 2, "is_current": False, "authenticated": True,
         "warning": "turn-time dead-model record MODEL_UNAVAILABLE_TURN"},
        {"code": "MODEL_UNAVAILABLE",
         "detail": "turn-time dead-model record for muse-spark"},
        "not-a-row",
        {"slug": "", "name": "empty", "models": []},
    ]
    clean = QC.sanitize_model_options_providers(rows)
    assert isinstance(clean, list)
    for row in clean:
        assert isinstance(row, dict), row
        assert str(row.get("slug") or ""), row
        assert isinstance(row.get("models"), list), row
        for m in row["models"]:
            assert isinstance(m, str) and m, row
            _assert_plain(m)
        _assert_plain(str(row.get("warning") or ""))
    slugs = [r.get("slug") for r in clean]
    assert "opencode-go" in slugs
    assert len(clean) == 1, clean
    kept = clean[0]
    assert kept["models"] == ["deepseek-v4-flash"], kept
    assert kept["total_models"] == 1, kept
