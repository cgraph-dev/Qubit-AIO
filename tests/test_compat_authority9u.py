"""Authority parity for ``voicebridge/qubit_compat.py`` (SP-07/08/09 + FIX-UPDATE/SESS).

Covers the audit-verified defects:

* **SP-07** ``/api/model/info`` must expose three distinct context authorities
  (auto-detected vs configured vs effective), not the single conflated ``ctx``.
* **SP-08** capabilities must be ``{}`` on a catalog miss — no fabricated
  ``supports_reasoning=False`` / ``max_output_tokens=1024``.
* **SP-09** ``/api/status`` booleans must be truthful (auth gate, update
  capability) and the schema must carry the derivable reference fields.
* **FIX-UPDATE residual** the GET update check must forward ``channel``/``tag``.
* **FIX-SESS residual** the merged sessions list must exclude archived rows by
  default.

Hermetic: temp XDG trees, stubbed vendored metadata/models.dev, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# hermetic config + profile helpers
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra, encoding="utf-8")
    return C.Config.load(p)


def _with_profile(cfg):
    """A resolvable high-context API profile (as the N29 authority test uses)."""
    from voicebridge.providers import resolver as R

    prof = R.ModelProfile(
        id="qubit-go", kind=R.API, provider="opencode-go",
        model="deepseek-v4-flash", base_url="https://opencode.ai/zen/go/v1",
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=200000)
    assert R.upsert_model_profile(cfg, prof, confirm=True).get("state") == "OK"
    assert R.set_active_profile(cfg, "qubit-go", confirm=True).get("state") == "OK"
    return cfg


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    yield
    R._RUNTIME_MODEL_CACHE.clear()


# ---------------------------------------------------------------------------
# SP-07 — distinct auto / config / effective context
# ---------------------------------------------------------------------------
def test_model_info_auto_config_effective_are_distinct(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    # Registry advertises 1M; the configured Qubit profile declares 200k.
    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: 1_000_000, raising=True)

    cfg = _cfg(tmp_path, monkeypatch,
               "intent:\n  frontier:\n    enabled: true\n"
               "    allowlist: ['opencode-go/deepseek-v4-flash']\n")
    _with_profile(cfg)

    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info
    assert info["auto_context_length"] == 1_000_000, info
    assert info["config_context_length"] == 200_000, info
    assert info["effective_context_length"] == 200_000, info
    # The three authorities are genuinely distinct — the exact SP-07 loss.
    assert info["auto_context_length"] != info["config_context_length"]
    assert info["effective_context_length"] == info["config_context_length"]


def test_model_info_config_zero_when_no_distinct_override(tmp_path, monkeypatch):
    """No distinct configured value ⇒ config=0 and effective==auto (reference shape)."""
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: 200_000, raising=True)

    cfg = _cfg(tmp_path, monkeypatch,
               "intent:\n  frontier:\n    enabled: true\n"
               "    allowlist: ['opencode-go/deepseek-v4-flash']\n")
    _with_profile(cfg)

    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info
    assert info["config_context_length"] == 0, info
    assert info["effective_context_length"] == 200_000, info
    assert info["auto_context_length"] == 200_000, info


# ---------------------------------------------------------------------------
# SP-08 — no fabricated capabilities on a catalog miss
# ---------------------------------------------------------------------------
def test_model_capabilities_empty_on_registry_miss(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    from agent import models_dev

    monkeypatch.setattr(models_dev, "get_model_capabilities",
                        lambda provider=None, model=None: None, raising=True)
    caps = HC._model_capabilities("ollama", "qubit-brain", 4096)
    # No fabricated capability booleans on a miss, but the N29 context
    # authority (context_window) is always reported so the dashboard context
    # badge does not vanish for a model models.dev does not know.
    assert caps == {"context_window": 4096}, caps
    assert "supports_reasoning" not in caps
    assert "max_output_tokens" not in caps
    assert "supports_tools" not in caps


def test_model_capabilities_are_real_on_registry_hit(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime

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
    caps = HC._model_capabilities("opencode-go", "deepseek-v4.1-flash", 200000)
    assert caps["supports_reasoning"] is True
    assert caps["max_output_tokens"] == 384000
    # The effective context remains the N29 authority, never the advertised one.
    assert caps["context_window"] == 200000


# ---------------------------------------------------------------------------
# SP-09 — truthful status booleans + derivable schema fields
# ---------------------------------------------------------------------------
def _stub_gateway(monkeypatch, gw):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "status", lambda cfg, **k: dict(gw), raising=True)


def test_status_auth_required_reflects_configured_token(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import runtime_inventory as RI

    monkeypatch.setattr(RI, "runtime_inventory", lambda cfg: {}, raising=True)
    _stub_gateway(monkeypatch, {"running": False, "pid": None,
                                "active_state": "inactive"})

    cfg_open = _cfg(tmp_path, monkeypatch)
    code, body = HC.h_status(cfg_open, {}, {}, {})
    assert code == 200, body
    assert body["auth_required"] is False

    cfg_gated = _cfg(tmp_path / "gated", monkeypatch, "webui:\n  token: sec\n")
    code, body = HC.h_status(cfg_gated, {}, {}, {})
    assert code == 200, body
    assert body["auth_required"] is True


def test_status_can_update_reflects_install_method(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import runtime_inventory as RI
    from voicebridge import update_manager as UM

    monkeypatch.setattr(RI, "runtime_inventory", lambda cfg: {}, raising=True)
    _stub_gateway(monkeypatch, {"running": False, "pid": None,
                                "active_state": "inactive"})
    cfg = _cfg(tmp_path, monkeypatch)

    monkeypatch.setattr(UM, "detect_install_method",
                        lambda cfg: {"supported_in_place": True}, raising=True)
    assert HC.h_status(cfg, {}, {}, {})[1]["can_update_qubit"] is True

    monkeypatch.setattr(UM, "detect_install_method",
                        lambda cfg: {"supported_in_place": False}, raising=True)
    assert HC.h_status(cfg, {}, {}, {})[1]["can_update_qubit"] is False


def test_status_carries_reference_schema_fields(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import runtime_inventory as RI

    monkeypatch.setattr(RI, "runtime_inventory", lambda cfg: {}, raising=True)
    _stub_gateway(monkeypatch, {
        "running": True, "pid": 4242, "state": "running", "failed": False,
        "service_unit": "qubit-gateway.service",
        "control": {"platforms": {"telegram": {"state": "connected"}},
                    "active_agents": 2},
    })
    cfg = _cfg(tmp_path, monkeypatch)

    code, body = HC.h_status(cfg, {}, {}, {})
    assert code == 200, body
    # Derived from the live daemon answer, not hardcoded {}.
    assert body["gateway_platforms"] == {"telegram": {"state": "connected"}}
    assert body["active_agents"] == 2
    assert body["gateway_busy"] is True
    assert body["gateway_drainable"] is True
    assert isinstance(body["components"], dict)
    assert body["components"]["platforms"]["connected"] == 1
    assert body["components"]["platforms"]["configured"] == 1
    assert body["overall"] in ("ok", "degraded")
    assert isinstance(body["profiles"], list)
    assert isinstance(body["gateway_platforms"], dict)


def test_status_no_platform_map_when_daemon_absent(tmp_path, monkeypatch):
    """No daemon answer ⇒ {} platforms (truthful), never invented names."""
    from voicebridge import qubit_compat as HC
    from voicebridge import runtime_inventory as RI

    monkeypatch.setattr(RI, "runtime_inventory", lambda cfg: {}, raising=True)
    _stub_gateway(monkeypatch, {"running": False, "pid": None,
                                "active_state": "inactive"})
    cfg = _cfg(tmp_path, monkeypatch)

    code, body = HC.h_status(cfg, {}, {}, {})
    assert code == 200, body
    assert body["gateway_platforms"] == {}
    assert body["active_agents"] == 0
    assert body["gateway_busy"] is False


# ---------------------------------------------------------------------------
# FIX-UPDATE residual — GET update check forwards channel/tag
# ---------------------------------------------------------------------------
def test_update_check_forwards_channel_and_tag(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import update_manager as UM

    seen = {}

    def _check(cfg, **kwargs):
        seen.update(kwargs)
        return {"state": "OK", "install_method": "git-checkout"}

    monkeypatch.setattr(UM, "check", _check, raising=True)
    monkeypatch.setattr(HC, "_update_resolve_source",
                        lambda cfg, source, channel, tag: "origin", raising=True)

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.h_qubit_update_check(
        cfg, {"channel": "canary", "tag": "v9.9.9"}, {}, {})
    assert code == 200 and body["ok"] is True, body
    assert seen.get("channel") == "canary"
    assert seen.get("tag") == "v9.9.9"
    assert seen.get("source") == "origin"


def test_update_check_bare_does_not_forward_empty_refs(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import update_manager as UM

    seen = {}

    def _check(cfg, **kwargs):
        seen.update(kwargs)
        return {"state": "OK"}

    monkeypatch.setattr(UM, "check", _check, raising=True)
    monkeypatch.setattr(HC, "_update_resolve_source",
                        lambda cfg, source, channel, tag: None, raising=True)

    cfg = _cfg(tmp_path, monkeypatch)
    code, _ = HC.h_qubit_update_check(cfg, {}, {}, {})
    assert code == 200
    assert "channel" not in seen and "tag" not in seen


# ---------------------------------------------------------------------------
# FIX-SESS residual — merged sessions list excludes archived by default
# ---------------------------------------------------------------------------
class _FakeReceiptStore:
    def __init__(self, rows):
        self._rows = rows
        self.list_kwargs = {}

    def list(self, limit=20, **kwargs):
        self.list_kwargs = kwargs
        include = kwargs.get("include_archived", True)
        rows = [r for r in self._rows if include or not r.get("archived")]
        return {"state": "OK", "sessions": rows[:limit]}

    def search(self, query, limit=20):
        return {"state": "OK", "sessions": self._rows[:limit]}


def _session_rows():
    chat = [
        {"id": "s-live", "source": "dashboard", "archived": False,
         "started_at": 10, "last_active": 10},
        {"id": "s-arch", "source": "dashboard", "archived": True,
         "started_at": 11, "last_active": 11},
    ]
    receipt = [
        {"id": "r-live", "receipt_id": "r-live", "archived": False,
         "created_at": 12},
        {"id": "r-arch", "receipt_id": "r-arch", "archived": True,
         "created_at": 13},
    ]
    return chat, receipt


@pytest.fixture
def _wire_sessions(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    chat, receipt = _session_rows()
    store = _FakeReceiptStore(receipt)
    monkeypatch.setattr(HC, "_chat_session_rows",
                        lambda cfg, **kw: {"sessions": list(chat),
                                           "total": len(chat)}, raising=True)
    monkeypatch.setattr(HC, "_receipt_session_store",
                        lambda cfg: store, raising=True)
    return HC, store


def test_sessions_list_excludes_archived_by_default(_wire_sessions, tmp_path,
                                                    monkeypatch):
    HC, _store = _wire_sessions
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.h_sessions_list(cfg, {}, {}, {})
    assert code == 200, body
    ids = {r["id"] for r in body["sessions"]}
    assert ids == {"s-live", "r-live"}, ids
    assert body["total"] == 2


def test_sessions_list_archived_include_and_only(_wire_sessions, tmp_path,
                                                 monkeypatch):
    HC, _store = _wire_sessions
    cfg = _cfg(tmp_path, monkeypatch)

    code, body = HC.h_sessions_list(cfg, {"archived": "include"}, {}, {})
    ids = {r["id"] for r in body["sessions"]}
    assert ids == {"s-live", "s-arch", "r-live", "r-arch"}, ids

    code, body = HC.h_sessions_list(cfg, {"archived": "only"}, {}, {})
    ids = {r["id"] for r in body["sessions"]}
    assert ids == {"s-arch", "r-arch"}, ids
