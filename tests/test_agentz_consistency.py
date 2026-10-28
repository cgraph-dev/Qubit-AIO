"""Agent Z consistency fixes: model resolver, reasoning ladder, plugin dirs.

Three verified inconsistencies, each fixed by porting the real vendored
behavior into the owned modules:

* **Model id.** ``/agent/api/model/info`` reported the configured default while
  the vendored ``/chat`` terminal resumes one long-lived session and honours
  that session's stored model. ``_resolved_model`` now reads the same session
  row the terminal resumes, so the reported model equals a real ``/chat`` turn.
* **Reasoning ladder.** ``voicebridge.local_llm.REASONING_BUDGETS`` accepted
  fewer levels than the canonical ``qubit_constants.VALID_REASONING_EFFORTS``
  ladder the dashboard picker mirrors; the missing ``xhigh``/``ultra`` are now
  real. ``model/info`` reports ``supports_reasoning`` truthfully from the
  vendored models.dev catalog instead of a hardcoded ``False``.
* **Legacy plugin dirs.** ``QUBIT_HOME/plugins/hermes-achievements`` and
  ``plugin-data/hermes-achievements`` are migrated once to the canonical
  ``qubit-achievements`` with a non-destructive read-old/write-new pass and a
  timestamped backup (never deleted).

Hermetic: temp XDG trees, stubbed vendored metadata, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _api_profile(pid, provider, model, *, base_url, ctx):
    from voicebridge.providers import resolver as R

    return R.ModelProfile(
        id=pid, kind=R.API, provider=provider, model=model, base_url=base_url,
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=ctx)


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    yield
    R._RUNTIME_MODEL_CACHE.clear()


def _go_cfg(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               "intent:\n  frontier:\n    enabled: true\n"
               "    allowlist: ['opencode-go/deepseek-v4-flash']\n"
               "terminal:\n  profile: qubit-go\n")
    R.upsert_model_profile(cfg, _api_profile(
        "qubit-go", "opencode-go", "deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1", ctx=200000), confirm=True)
    R.set_active_profile(cfg, "qubit-go", confirm=True)
    return cfg


# ---------------------------------------------------------------------------
# Item 1 — model/info reports the resolved default, never a stale session row
# ---------------------------------------------------------------------------
def test_model_info_ignores_stale_canonical_session_model(tmp_path,
                                                          monkeypatch):
    from voicebridge import agent as AG
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg = _go_cfg(tmp_path, monkeypatch)
    # A fossil canonical session stores a model that differs from the live
    # default (the pre-fresh-/chat world, where the terminal always resumed
    # it). model/info must report the resolved selection anyway.
    store = CT.store(cfg)
    assert store.ensure(AG.canonical_session_id(cfg),
                        model="deepseek-v4.1-flash")["state"] == "OK"

    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info
    assert info["model"] == "deepseek-v4-flash"
    assert info["effective_context_length"] == 200000
    # N29 authority is preserved — never the advertised 1M registry value.
    assert info["capabilities"]["context_window"] == 200000


def test_model_info_without_session_uses_resolved_default(tmp_path,
                                                          monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _go_cfg(tmp_path, monkeypatch)
    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info
    assert info["model"] == "deepseek-v4-flash"


# ---------------------------------------------------------------------------
# Item 2 — the reasoning ladder matches the canonical vendored vocabulary
# ---------------------------------------------------------------------------
def test_reasoning_budgets_match_canonical_ladder():
    from voicebridge.local_llm import REASONING_BUDGETS

    import qubit_constants

    expected = set(qubit_constants.VALID_REASONING_EFFORTS) | {"none"}
    assert set(REASONING_BUDGETS) == expected


def test_every_offered_level_is_accepted_and_unknown_rejected():
    from voicebridge.local_llm import (
        REASONING_BUDGETS, ReasoningInvalidError, normalize_reasoning_level,
        resolve_reasoning_budget,
    )

    # Every level the picker offers (the canonical ladder) is accepted, and
    # `none` (thinking off) is too. An unsupported level fails typed.
    for level in (*sorted(REASONING_BUDGETS), "XHigh", "  ULTRA  "):
        assert normalize_reasoning_level(level) in REASONING_BUDGETS
    assert normalize_reasoning_level(None) is None
    with pytest.raises(ReasoningInvalidError):
        normalize_reasoning_level("bogus")
    # Budgets stay monotonic along the ladder.
    ordered = ["none", "minimal", "low", "medium", "high", "xhigh", "max",
               "ultra"]
    budgets = [resolve_reasoning_budget(lvl) for lvl in ordered]
    assert budgets == sorted(budgets)


def test_model_info_reports_reasoning_capability_truthfully(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime

    cfg = _go_cfg(tmp_path, monkeypatch)
    qubit_runtime.ensure_path()
    from agent import models_dev

    class _MC:
        supports_tools = True
        supports_vision = False
        supports_reasoning = True
        model_family = "deepseek-flash"
        max_output_tokens = 384000

    monkeypatch.setattr(models_dev, "get_model_capabilities",
                        lambda provider=None, model=None: _MC(), raising=True)
    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info
    assert info["capabilities"]["supports_reasoning"] is True
    assert info["capabilities"]["supports_tools"] is True
    # The effective context stays the N29 authority.
    assert info["capabilities"]["context_window"] == 200000

    # A model the catalog does not know (the local brain) is not advertised as
    # reasoning-capable.
    monkeypatch.setattr(models_dev, "get_model_capabilities",
                        lambda provider=None, model=None: None, raising=True)
    code, local = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, local
    assert local is not None
    # Catalog miss: no fabricated reasoning capability, but the N29 context
    # authority is still reported.
    miss = HC._model_capabilities("ollama", "qubit-brain", 4096)
    assert "supports_reasoning" not in miss
    assert miss["context_window"] == 4096


# ---------------------------------------------------------------------------
# Item 3 — legacy-named plugin dirs migrate once to the canonical name
# ---------------------------------------------------------------------------
def _qh(tmp_path, monkeypatch):
    home = tmp_path / "qh"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return home


def test_migrate_legacy_plugin_dirs_copies_and_backs_up(tmp_path, monkeypatch):
    from voicebridge import qubit_plugins as QP

    home = _qh(tmp_path, monkeypatch)
    (home / "plugins" / "hermes-achievements").mkdir(parents=True)
    (home / "plugins" / "hermes-achievements" / "state.json").write_text(
        '{"legacy": 1}', encoding="utf-8")
    (home / "plugin-data" / "hermes-achievements").mkdir(parents=True)
    (home / "plugin-data" / "hermes-achievements" / "scan_snapshot.json").write_text(
        '{"old": 1}', encoding="utf-8")

    result = QP.migrate_legacy_plugin_dirs(None)
    assert result["state"] == "OK"
    canonical = home / "plugin-data" / "qubit-achievements"
    assert (canonical / "state.json").read_text(encoding="utf-8") == '{"legacy": 1}'
    assert (canonical / "scan_snapshot.json").read_text(encoding="utf-8") == '{"old": 1}'
    # No legacy-named dir remains in the canonical plugin path...
    assert not (home / "plugins" / "hermes-achievements").exists()
    assert not (home / "plugin-data" / "hermes-achievements").exists()
    # ...and both were backed up, not deleted.
    assert len(result["backups"]) == 2
    for backup in result["backups"]:
        assert Path(backup).is_dir()
    assert (home / ".plugin-dir-migration.json").is_file()
    # Idempotent: a second pass is a no-op.
    assert QP.migrate_legacy_plugin_dirs(None)["migrated"] == []


def test_migrate_never_overwrites_live_canonical_state(tmp_path, monkeypatch):
    from voicebridge import qubit_plugins as QP

    home = _qh(tmp_path, monkeypatch)
    canonical = home / "plugin-data" / "qubit-achievements"
    canonical.mkdir(parents=True)
    (canonical / "state.json").write_text('{"live": true}', encoding="utf-8")
    (home / "plugins" / "hermes-achievements").mkdir(parents=True)
    (home / "plugins" / "hermes-achievements" / "state.json").write_text(
        '{"stale": true}', encoding="utf-8")

    QP.migrate_legacy_plugin_dirs(None)
    assert (canonical / "state.json").read_text(encoding="utf-8") == '{"live": true}'
    assert not (home / "plugins" / "hermes-achievements").exists()


def test_achievements_data_dir_is_canonical(tmp_path, monkeypatch):
    from voicebridge import qubit_plugins as QP

    home = _qh(tmp_path, monkeypatch)
    assert QP._plugin_data_dir(None, "qubit-achievements") == (
        home / "plugin-data" / "qubit-achievements")
