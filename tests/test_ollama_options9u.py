"""9U — the model picker lists the Ollama local tier.

Defect (2026-09-30): ``GET /api/model/options`` showed only API providers
(moa/copilot/opencode-zen/opencode-go). The vendored builder only injects a
local row for the managed llamacpp runtime's staged GGUFs, so Qubit's Ollama
models (brain, Spark 1.7B/4B) were invisible and /qubit-chat could not
switch to a local model — the one surface that should live on the local
tier.

Contract pinned here:

* ``_ollama_options_row`` lists registered, enabled, chat-eligible LOCAL
  profiles (the exact set ``POST /api/model/set`` activates); ``None``
  when none qualify; the checkmark follows the canonical active profile.
* A local activation mirrors the runtime model slot (like the remote path),
  so vendored readers agree on the selection.

Hermetic: isolated state dirs, no network.
"""
from __future__ import annotations

from voicebridge import config as C
from voicebridge.providers import endpoints as EP
from voicebridge.providers import resolver as R


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


def _register_local(cfg, pid, *, enabled=True, roles=None):
    profile = R.ModelProfile(
        id=pid, kind=R.LOCAL, provider="ollama", model=pid,
        locality="local", egress_consent="deny", context_tokens=1_048_576,
        supports_streaming=True, roles=list(roles or ["chat"]),
        enabled=enabled, rollback_to=pid, provenance="ollama://local")
    out = R.upsert_model_profile(cfg, profile, confirm=True)
    assert out["state"] == "OK", out


def test_row_lists_local_chat_profiles(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _register_local(cfg, "qubit-spark17")
    _register_local(cfg, "qubit-spark4")
    row = QC._ollama_options_row(cfg)
    assert row is not None, "row missing with local profiles registered"
    assert row["slug"] == "ollama"
    # The shipped brain4 default always qualifies alongside the Spark tier.
    assert set(row["models"]) == {
        "qubit-spark17", "qubit-spark4", "qubit-brain4"}
    assert row["total_models"] == 3
    assert row["authenticated"] is True
    assert row["is_current"] is False


def test_row_checkmark_follows_active_profile(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _register_local(cfg, "qubit-spark17")
    _register_local(cfg, "qubit-brain4")
    assert QC._ollama_options_row(cfg)["is_current"] is False
    assert R.set_active_profile(
        cfg, "qubit-spark17", confirm=True)["state"] == "OK"
    assert QC._ollama_options_row(cfg)["is_current"] is True


def test_row_skips_ineligible_but_keeps_shipped_default(tmp_path,
                                                         monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    # The shipped brain4 default always qualifies, so the row is never
    # empty in practice; custom rows that are disabled or not chat-eligible
    # must not leak into it.
    row = QC._ollama_options_row(cfg)
    assert row is not None and row["models"] == ["qubit-brain4"], row
    _register_local(cfg, "no-chat", roles=["embedding"])
    _register_local(cfg, "off", enabled=False)
    row = QC._ollama_options_row(cfg)
    assert row is not None and row["models"] == ["qubit-brain4"], row


def test_local_activation_mirrors_runtime_slot(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _register_local(cfg, "qubit-spark4")
    out = EP.select_model(cfg, "ollama", "qubit-spark4", confirm=True)
    assert out["ok"] is True, out
    assert R.get_active_profile(cfg)["active"] == "qubit-spark4"
    from voicebridge import qubit_runtime

    runtime_cfg = qubit_runtime.runtime_home_for_cfg(cfg) / "config.yaml"
    text = runtime_cfg.read_text(encoding="utf-8")
    assert "qubit-spark4" in text and "ollama" in text
