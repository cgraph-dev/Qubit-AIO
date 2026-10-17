"""Per-session provider+model picks (PATCH /api/qubit/chat/sessions/:id).

A bare model id re-enters first-match routing, so ``grok-4.7`` picked under
``cheapvibecode`` landed on ``opencode-go`` (measured live against :8890).
The pair form keeps the picker's own provider: the slug must name a catalog
row offering that model, and the row keeps the provider's endpoint.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import qubit_compat as HC  # noqa: E402


PAYLOAD = {
    "providers": [
        {"slug": "cheapvibecode", "name": "CheapVibeCode",
         "models": ["grok-4.7", "deepseek-v4.1-flash"]},
        {"slug": "openrouter", "name": "OpenRouter",
         "models": ["x-ai/grok-4.7", "anthropic/claude-fable-5.1"]},
    ],
}


def _install_inventory(monkeypatch):
    mod = types.ModuleType("qubit_cli.inventory")
    mod.build_models_payload = lambda ctx: PAYLOAD
    mod.load_picker_context = lambda: {}
    monkeypatch.setitem(sys.modules, "qubit_cli.inventory", mod)
    monkeypatch.setattr(HC, "_vendored_runtime", lambda cfg: None)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_pair_helper_accepts_bare_and_prefixed_spellings():
    class C:
        def get(self, *path, default=None):
            node = {"providers": {"cheapvibecode": {
                "base_url": "https://cheapvibecode.ru/v1",
                "api_mode": "chat_completions"}}}
            for p in path:
                if not isinstance(node, dict) or p not in node:
                    return default
                node = node[p]
            return node

    ok = HC._chat_session_provider_pair(C(), "cheapvibecode", "grok-4.7",
                                        _payload=PAYLOAD)
    assert ok["ok"] is True, ok
    assert (ok["provider"], ok["model"]) == ("cheapvibecode", "grok-4.7")
    assert ok["base_url"] == "https://cheapvibecode.ru/v1"
    assert ok["api_mode"] == "chat_completions"

    prefixed = HC._chat_session_provider_pair(C(), "cheapvibecode",
                                              "cheapvibecode/grok-4.7",
                                              _payload=PAYLOAD)
    assert prefixed["ok"] is True, prefixed
    assert prefixed["model"] == "grok-4.7"

    namespaced = HC._chat_session_provider_pair(C(), "openrouter",
                                                "anthropic/claude-fable-5.1",
                                                _payload=PAYLOAD)
    assert namespaced["ok"] is True, namespaced
    assert namespaced["model"] == "anthropic/claude-fable-5.1"


def test_pair_helper_refuses_unknown_provider_and_foreign_model():
    class C:
        def get(self, *path, default=None):
            return default

    bad_provider = HC._chat_session_provider_pair(C(), "nope", "grok-4.7",
                                                  _payload=PAYLOAD)
    assert bad_provider["ok"] is False
    assert bad_provider["code"] == "UNKNOWN_PROVIDER"

    foreign = HC._chat_session_provider_pair(C(), "cheapvibecode",
                                             "anthropic/claude-fable-5.1",
                                             _payload=PAYLOAD)
    assert foreign["ok"] is False
    assert foreign["code"] == "MODEL_NOT_IN_PROVIDER"


def test_patch_pair_stores_provider_route(tmp_path, monkeypatch):
    _install_inventory(monkeypatch)
    cfg = _cfg(tmp_path, monkeypatch)
    code, created = HC.dispatch(cfg, "POST", "/api/qubit/chat/sessions", {},
                                 {"name": "pair-probe"})
    assert code == 200, created
    sid = created["session"]["id"]
    try:
        code, out = HC.dispatch(
            cfg, "PATCH", f"/api/qubit/chat/sessions/{sid}", {},
            {"model": "grok-4.7", "provider": "cheapvibecode"})
        assert code == 200, out
        row = out["session"]
        assert row["model"] == "grok-4.7"
        # Route internals never reach the wire/store projections by design;
        # the raw state.db row must carry the provider's endpoint for the
        # next turn to bill it.
        import json
        import sqlite3
        dbs = list(Path(tmp_path).glob("**/state.db"))
        assert dbs, "no state.db under isolated XDG dirs"
        db = sqlite3.connect(str(dbs[0]))
        db.row_factory = sqlite3.Row
        raw = db.execute(
            "SELECT model, model_config FROM sessions WHERE id = ?",
            (sid,)).fetchone()
        assert raw is not None
        assert raw["model"] == "grok-4.7"
        route = json.loads(raw["model_config"] or "{}")
        assert route.get("provider") == "cheapvibecode", route
    finally:
        HC.dispatch(cfg, "DELETE", f"/api/qubit/chat/sessions/{sid}",
                     {"confirm": "1"}, {})


def test_patch_pair_refuses_foreign_model(tmp_path, monkeypatch):
    _install_inventory(monkeypatch)
    cfg = _cfg(tmp_path, monkeypatch)
    code, created = HC.dispatch(cfg, "POST", "/api/qubit/chat/sessions", {},
                                 {"name": "pair-refuse-probe"})
    assert code == 200, created
    sid = created["session"]["id"]
    try:
        code, out = HC.dispatch(
            cfg, "PATCH", f"/api/qubit/chat/sessions/{sid}", {},
            {"model": "anthropic/claude-fable-5.1",
             "provider": "cheapvibecode"})
        assert code == 400, out
        assert out["code"] == "MODEL_NOT_IN_PROVIDER", out
    finally:
        HC.dispatch(cfg, "DELETE", f"/api/qubit/chat/sessions/{sid}",
                     {"confirm": "1"}, {})
