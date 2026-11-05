"""9U — the model picker lists the Ollama local tier.

Defect (2026-09-30): ``GET /api/model/options`` showed only API providers
(moa/copilot/opencode-zen/opencode-go). The vendored builder only injects a
local row for the managed llamacpp runtime's staged GGUFs, so Qubit's Ollama
models (brain, Spark 1.7B/4B) were invisible and /qubit-chat could not
switch to a local model — the one surface that should live on the local
tier.

Contract pinned here:

* ``_ollama_options_row`` lists registered, enabled LOCAL profiles —
  chat-eligible ones plus the shipped default (whose roles deliberately omit
  ``chat``) — and any model the runtime config names; ``None`` when nothing
  local qualifies. The checkmark follows the canonical active profile, with
  the explicit runtime model as the pre-selection fallback.
* ``_merge_local_ollama_row`` adds that row only when the runtime config names
  a local Ollama provider/model, fills an empty vendored Ollama row, and
  never disturbs an explicitly configured current provider.
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
        "qubit-spark17", "qubit-spark4", "qubit-brain"}
    assert row["total_models"] == 3
    assert row["authenticated"] is True
    assert row["is_current"] is False


def test_row_checkmark_follows_active_profile(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _register_local(cfg, "qubit-spark17")
    _register_local(cfg, "qubit-brain")
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
    assert row is not None and row["models"] == ["qubit-brain"], row
    _register_local(cfg, "no-chat", roles=["embedding"])
    _register_local(cfg, "off", enabled=False)
    row = QC._ollama_options_row(cfg)
    assert row is not None and row["models"] == ["qubit-brain"], row


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


def test_runtime_slot_uses_single_key_convention(tmp_path, monkeypatch):
    import yaml

    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    # A stale Frankenstein slot (mixed keys from successive switches) is
    # rebuilt from scratch under the single-key convention: ``default`` only,
    # no ``model``/``name`` leftovers for the vendored normalizer to fight.
    path = qubit_runtime.runtime_home_for_cfg(cfg) / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(
        {"model": {"provider": "opencode-go", "model": "qubit-spark17",
                   "default": "muse-spark-1.3-contributor",
                   "base_url": "https://opencode.ai/zen/go/v1"}}),
        encoding="utf-8")
    assert EP._persist_runtime_model(
        cfg, provider="ollama", model="qubit-spark17",
        base_url="", key_ref="") is True
    node = yaml.safe_load(path.read_text(encoding="utf-8"))["model"]
    assert node == {"provider": "ollama",
                    "default": "qubit-spark17"}, node


def _write_runtime_config(cfg, body):
    from voicebridge import qubit_runtime

    path = qubit_runtime.runtime_home_for_cfg(cfg) / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _payload(*rows):
    return {"providers": list(rows), "model": "", "provider": ""}


def test_merge_adds_local_ollama_for_clean_default_config(tmp_path,
                                                          monkeypatch):
    """A fresh config naming only the local Ollama default still lists it.

    Regression: ``model: {provider: ollama, default: qubit-brain}`` with no
    ``providers.ollama`` block produced only Ollama Cloud / unrelated rows.
    The shipped default profile must appear as a selectable local provider.
    """
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _write_runtime_config(
        cfg, "model:\n  provider: ollama\n  default: qubit-brain\n")
    payload = _payload()

    QC._merge_local_ollama_row(payload, cfg)

    rows = {str(r.get("slug")): r for r in payload["providers"]}
    local = rows.get("ollama")
    assert local is not None, sorted(rows)
    assert local["models"] == ["qubit-brain"]
    assert local["total_models"] == 1
    assert local["is_current"] is True
    assert local["authenticated"] is True
    assert local["source"] == "qubit-ollama"


def test_merge_fills_local_row_from_configured_base_url(tmp_path,
                                                        monkeypatch):
    """``providers.ollama.base_url`` alone yields a selectable local model.

    The vendored builder creates the Ollama row but leaves it empty when no
    ``default_model`` is written; the Qubit-owned default fills it instead of
    duplicating or dropping the row.
    """
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _write_runtime_config(
        cfg,
        "providers:\n  ollama:\n"
        "    base_url: http://127.0.0.1:11434/v1\n")
    payload = _payload(
        {"slug": "ollama", "name": "ollama", "models": [],
         "total_models": 0, "is_current": False, "source": "user-config"})

    QC._merge_local_ollama_row(payload, cfg)

    rows = {str(r.get("slug")): r for r in payload["providers"]}
    local = rows["ollama"]
    assert local["models"] == ["qubit-brain"]
    assert local["total_models"] == 1
    assert local["authenticated"] is True
    # Filled in place, never duplicated.
    assert [r.get("slug") for r in payload["providers"]] == ["ollama"]


def test_merge_keeps_explicit_provider_current(tmp_path, monkeypatch):
    """An explicitly configured provider still wins over the local default."""
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _write_runtime_config(
        cfg,
        "model:\n  provider: opencode-go\n"
        "  default: deepseek-v4.1-flash\n"
        "  base_url: https://opencode.ai/zen/go/v1\n"
        "providers:\n  ollama:\n"
        "    base_url: http://127.0.0.1:11434/v1\n"
        "    default_model: qubit-brain\n")
    cloud = {"slug": "opencode-go", "name": "OpenCode Go",
             "models": ["deepseek-v4.1-flash"], "total_models": 1,
             "is_current": True, "source": "configured-current"}
    local = {"slug": "ollama", "name": "ollama",
             "models": ["qubit-brain"], "total_models": 1,
             "is_current": False, "source": "user-config"}
    payload = _payload(cloud, local)

    QC._merge_local_ollama_row(payload, cfg)

    assert payload["providers"][0] is cloud
    assert cloud["is_current"] is True
    assert cloud["models"] == ["deepseek-v4.1-flash"]
    assert local["is_current"] is False
    assert local["models"] == ["qubit-brain"]
    assert [r for r in payload["providers"] if r.get("is_current")] == [cloud]
