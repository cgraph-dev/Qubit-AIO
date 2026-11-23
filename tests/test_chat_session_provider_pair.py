"""Per-session provider+model picks (PATCH /api/qubit/chat/sessions/:id).

A bare model id re-enters first-match routing, so ``grok-4.7`` picked under
``cheapvibecode`` landed on ``opencode-go`` (measured live against :8890).
The pair form keeps the picker's own provider: the slug must name a catalog
row offering that model, and the row keeps the provider's endpoint.
"""
from __future__ import annotations

import sys
import types
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import qubit_compat as HC  # noqa: E402


PAYLOAD = {
    "providers": [
        {"slug": "cheapvibecode", "name": "CheapVibeCode",
         "models": ["grok-4.7", "deepseek-v4.1-flash"]},
        {"slug": "opencode-go", "name": "OpenCode Go",
         "models": ["grok-4.7"]},
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
        assert row["provider"] == "cheapvibecode"
        read_status, read = HC.dispatch(
            cfg, "GET", f"/api/qubit/chat/sessions/{sid}", {}, {})
        assert read_status == 200, read
        assert read["session"]["provider"] == "cheapvibecode"
        assert "base_url" not in read["session"] and "model_config" not in read["session"]
        # Route internals never reach the wire/store projections by design;
        # the raw state.db row must carry the provider's endpoint for the
        # next turn to bill it.
        import json
        import sqlite3
        dbs = list(Path(tmp_path).glob("**/state.db"))
        assert dbs, "no state.db under isolated XDG dirs"
        with closing(sqlite3.connect(str(dbs[0]))) as db:
            db.row_factory = sqlite3.Row
            raw = db.execute(
                "SELECT model, model_config FROM sessions WHERE id = ?",
                (sid,)).fetchone()
        assert raw is not None
        assert raw["model"] == "grok-4.7"
        route = json.loads(raw["model_config"] or "{}")
        assert route.get("provider") == "cheapvibecode", route

        code, changed = HC.dispatch(
            cfg, "PATCH", f"/api/qubit/chat/sessions/{sid}", {},
            {"model": "grok-4.7", "provider": "opencode-go"})
        assert code == 200, changed
        assert changed["session"]["provider"] == "opencode-go"
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


def _isolate_profile_host_state(monkeypatch):
    import qubit_constants
    from agent import secret_scope
    from tui_gateway import launch_profile_policy

    monkeypatch.setattr(launch_profile_policy, "_snapshot", None)
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
    monkeypatch.setattr(secret_scope, "_AUTO_PINNED_HOME", None)
    monkeypatch.setattr(qubit_constants, "_PINNED_PROCESS_HERMES_HOME", None)


def test_terminal_model_patch_uses_only_the_requested_profile(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.qubit_session_store import QubitSessionStore

    home = tmp_path / "runtime"
    profile = home / "profiles" / "secondary"
    profile.mkdir(parents=True)
    profile.joinpath("config.yaml").write_text(
        "providers:\n  cheapvibecode:\n    base_url: https://secondary.example.invalid/v1\n")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    qubit_runtime.ensure_path()
    _isolate_profile_host_state(monkeypatch)
    _install_inventory(monkeypatch)
    cfg = _cfg(tmp_path, monkeypatch)

    own = QubitSessionStore(cfg)
    secondary = QubitSessionStore(cfg, runtime_home=profile)
    sid = "same-id-in-two-stores"
    own._db.create_session(sid, "tui", model="own-model")
    secondary._db.create_session(sid, "tui", model="secondary-model")

    path = f"/api/qubit/chat/sessions/{sid}"
    body = {"model": "grok-4.7", "provider": "cheapvibecode"}
    status, result = HC.dispatch(cfg, "PATCH", path, {"profile": "secondary"}, body)
    assert status == 200, result
    assert result["session"]["provider"] == "cheapvibecode"
    assert own._db.get_session(sid)["model"] == "own-model"
    assert secondary._db.get_session(sid)["model"] == "grok-4.7"
    assert secondary._db.get_session_model_config_value(sid, "base_url") == "https://secondary.example.invalid/v1"
    assert HC.dispatch(cfg, "PATCH", path, {"profile": "missing-fixture"}, body)[0] == 404
    own.archive(sid)
    secondary.archive(sid)
    status, restored = HC.dispatch(
        cfg, "PATCH", path, {"profile": "secondary"}, {"archived": False})
    assert status == 200 and restored["session"]["archived"] is False
    assert secondary.show(sid)["state"] == "OK"
    assert own.show(sid)["state"] == "MISSING"



def test_model_picker_catalogue_uses_named_profile_and_isolates_its_cache(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    home = tmp_path / "runtime"
    profile = home / "profiles" / "secondary"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model:\n  default: scoped-choice\n")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setenv("QUBIT_READ_CACHE", "1")
    qubit_runtime.ensure_path()
    _isolate_profile_host_state(monkeypatch)
    cfg = _cfg(tmp_path, monkeypatch)
    from qubit_constants import get_qubit_home

    reads = []

    def read_options(*_args):
        path = get_qubit_home()
        reads.append(path)
        return 200, {"provider": path.name}

    monkeypatch.setattr(HC, "_h_model_options_uncached", read_options)
    assert HC.h_model_options(cfg, {}, {}, {})[1]["provider"] == "runtime"
    assert HC.h_model_options(cfg, {"profile": "secondary"}, {}, {})[1]["provider"] == "secondary"
    assert HC.h_model_options(cfg, {"profile": "secondary"}, {}, {})[1]["provider"] == "secondary"
    assert reads == [home, profile]
    assert HC.h_model_options(cfg, {"profile": "missing-fixture"}, {}, {})[0] == 404


def test_terminal_picker_queues_provider_without_relabeling_the_running_model(tmp_path, monkeypatch):
    from voicebridge import chat_transport
    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    qubit_runtime.ensure_path()
    _install_inventory(monkeypatch)
    monkeypatch.setattr("qubit_cli.model_selection_guards.combined_selection_warning", lambda *a, **k: None)
    store = chat_transport.store(cfg)
    sid = "terminal-next-turn"
    store._db.create_session(sid, "tui", model="grok-4.7")
    store.set_model(sid, "grok-4.7", provider="opencode-go")
    path = f"/api/qubit/chat/sessions/{sid}"
    status, reply = HC.dispatch(cfg, "PATCH", path, {}, {
        "model": "grok-4.7", "provider": "cheapvibecode", "terminal_next_turn": True,
    })
    assert status == 200, reply
    request = reply["terminal_model_request"]
    assert request["state"] == "queued"
    assert request["provider"] == "cheapvibecode"
    status, check = HC.dispatch(cfg, "GET", f"{path}/terminal-model")
    assert status == 200, check
    assert check["session"]["provider"] == "opencode-go"
    assert check["terminal_model_request"] == request
    assert "confirmed" not in request
    assert "min_context_tokens" not in request
