"""9U — OpenClaw advanced sessions-family handlers are real canonical delegates.

Two independent proofs:

1. static (AST) — every method this family registers is reached by the
   manifest's own behavior proof; each typed ``SAFE_EQUIVALENT`` handler emits
   its declared code and every ``IMPLEMENTED`` handler does real backend work;
2. runtime — the live handlers run in-process against the canonical
   ``QubitSessionStore`` and their real persistence/state behavior is asserted.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

FAMILY_MODULE = "voicebridge/gateway/openclaw_handlers/sessions_advanced.py"

#: The typed SAFE_EQUIVALENT codes this family emits (method -> code).
SAFE_EQUIVALENT = {
    "sessions.subscribe": "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY",
    "sessions.messages.subscribe": "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY",
    "sessions.messages.unsubscribe": "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY",
    "sessions.viewers.set": "SESSIONS_VIEWERS_LOCAL_REGISTRY",
}

#: The real IMPLEMENTED handlers this family adds.
IMPLEMENTED = (
    "sessions.goal.update", "sessions.goal.clear",
    "session.suggestions.list", "session.suggestions.add",
    "session.suggestions.resolve", "session.typing",
    "sessions.assignOwner", "sessions.pluginPatch",
    "sessions.reset", "sessions.rewind", "sessions.cleanup",
    "sessions.groups.list", "sessions.groups.defaults",
    "sessions.groups.put", "sessions.groups.rename",
    "sessions.groups.update", "sessions.groups.delete",
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway import rpc as R
    return R.default_registry()


def _new_session(cfg, name="adv"):
    reg = _registry()
    out = reg.handle(cfg, "sessions.create", {"name": name})
    assert out["state"] == "OK", out
    return out["session"]["id"]


# ---------------------------------------------------------------- static proof
def test_registered_implementations_pass_manifest_behavior_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items()
            if origin.endswith("openclaw_handlers/sessions_advanced.py")}
    assert ours == set(IMPLEMENTED) | set(SAFE_EQUIVALENT), sorted(ours)
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        if method in SAFE_EQUIVALENT:
            assert OUM._emits_code(node, funcs, SAFE_EQUIVALENT[method],
                                   consts), (
                f"{method} never emits its declared code "
                f"{SAFE_EQUIVALENT[method]!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} is registered as an implementation but does no "
                "backend work")


def test_family_is_composed_into_default_registry():
    from voicebridge.gateway.openclaw_handlers import FAMILY_MODULES
    assert "sessions_advanced" in FAMILY_MODULES
    names = set(_registry().names())
    assert set(IMPLEMENTED) <= names


# --------------------------------------------------------------- runtime proof
def test_goal_and_owner_and_typing_round_trip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    sid = _new_session(cfg)
    reg = _registry()

    up = reg.handle(cfg, "sessions.goal.update",
                    {"sessionKey": sid, "goal": "ship parity"})
    assert up["state"] == "OK" and up["meta"]["goal"] == "ship parity", up

    got = reg.handle(cfg, "sessions.goal.update",
                     {"sessionKey": sid, "goal": "ship parity"})
    assert got["meta"]["goal"] == "ship parity"

    cleared = reg.handle(cfg, "sessions.goal.clear", {"sessionKey": sid})
    assert cleared["state"] == "OK" and "goal" not in cleared["meta"], cleared

    owner = reg.handle(cfg, "sessions.assignOwner",
                       {"sessionKey": sid, "userId": "operator"})
    assert owner["meta"]["owner"] == "operator", owner

    typing = reg.handle(cfg, "session.typing",
                        {"sessionKey": sid, "state": "typing"})
    assert typing["state"] == "OK"
    assert typing["meta"]["typing"]["active"] is True, typing


def test_suggestions_and_plugin_patch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    sid = _new_session(cfg)
    reg = _registry()

    added = reg.handle(cfg, "session.suggestions.add",
                       {"sessionKey": sid, "text": "try again"})
    assert added["state"] == "OK", added
    sug_id = added["suggestion"]["id"]

    listed = reg.handle(cfg, "session.suggestions.list", {"sessionKey": sid})
    assert listed["total"] == 1 and listed["suggestions"][0]["id"] == sug_id

    resolved = reg.handle(cfg, "session.suggestions.resolve",
                          {"sessionKey": sid, "suggestionId": sug_id,
                           "status": "accepted"})
    assert resolved["suggestion"]["status"] == "accepted", resolved

    missing = reg.handle(cfg, "session.suggestions.resolve",
                         {"sessionKey": sid, "suggestionId": "nope"})
    assert missing["state"] == "MISSING", missing

    patched = reg.handle(cfg, "sessions.pluginPatch",
                         {"sessionKey": sid, "patch": {"accent": "blue"}})
    assert patched["state"] == "OK"
    assert patched["meta"]["plugin"] == {"accent": "blue"}, patched


def test_group_catalog_crud(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    put = reg.handle(cfg, "sessions.groups.put",
                     {"names": ["Work", "Personal"],
                      "sectionOrder": ["Work", "Personal"]})
    assert put["state"] == "OK" and put["total"] == 2, put

    listing = reg.handle(cfg, "sessions.groups.list", {})
    assert {g["id"] for g in listing["groups"]} == {"Work", "Personal"}
    assert listing["sectionOrder"] == ["Work", "Personal"], listing

    renamed = reg.handle(cfg, "sessions.groups.rename",
                         {"name": "Personal", "to": "Home"})
    assert renamed["state"] == "OK" and renamed["group"]["name"] == "Home"

    updated = reg.handle(cfg, "sessions.groups.update",
                         {"name": "Work", "cwd": "/tmp/work"})
    assert updated["state"] == "OK" and updated["group"]["cwd"] == "/tmp/work"

    defaults = reg.handle(cfg, "sessions.groups.defaults", {})
    assert defaults["state"] == "OK", defaults

    deleted = reg.handle(cfg, "sessions.groups.delete", {"name": "Home"})
    assert deleted["state"] == "OK" and deleted["deleted"] == "Home"

    gone = reg.handle(cfg, "sessions.groups.delete", {"name": "Home"})
    assert gone["state"] == "MISSING", gone


def test_subscriptions_and_viewers_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    sid = _new_session(cfg)
    reg = _registry()
    from voicebridge import session_subscriptions as SS

    SS.REGISTRY.reset()
    sub = reg.handle(cfg, "sessions.subscribe", {"sessionKey": sid})
    assert sub["state"] == "OK"
    assert sub["code"] == "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY", sub
    sub_id = sub["subscription"]["id"]

    msub = reg.handle(cfg, "sessions.messages.subscribe",
                      {"sessionKey": sid})
    assert msub["subscription"]["kind"] == "messages"

    viewers = reg.handle(cfg, "sessions.viewers.set",
                         {"sessionKey": sid, "viewers": ["me", "me", "you"]})
    assert viewers["code"] == "SESSIONS_VIEWERS_LOCAL_REGISTRY"
    assert viewers["viewers"] == ["me", "you"], viewers

    removed = reg.handle(cfg, "sessions.messages.unsubscribe",
                         {"subscriptionId": sub_id})
    assert removed["state"] == "OK", removed
    assert SS.REGISTRY.list(sid) and all(
        s["kind"] != "session" for s in SS.REGISTRY.list(sid))
    SS.REGISTRY.reset()


def test_reset_rewind_cleanup_are_confirmation_gated(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    sid = _new_session(cfg)
    reg = _registry()
    st = __import__("voicebridge.chat_transport", fromlist=["store"]).store(cfg)

    st.append(sid, "user", "hello")
    st.append(sid, "assistant", "hi")
    msg_id = st.messages(sid)["messages"][0]["meta"]["id"]

    staged = reg.handle(cfg, "sessions.reset", {"sessionKey": sid})
    assert staged["state"] == "CONFIRM_REQUIRED", staged
    assert st.messages(sid)["total"] == 2

    rw_staged = reg.handle(cfg, "sessions.rewind",
                           {"sessionKey": sid, "messageId": msg_id})
    assert rw_staged["state"] == "CONFIRM_REQUIRED", rw_staged

    rw = reg.handle(cfg, "sessions.rewind",
                    {"sessionKey": sid, "messageId": msg_id, "confirm": True})
    assert rw["state"] == "OK", rw
    assert rw["rewound_count"] == 2, rw

    clean_staged = reg.handle(cfg, "sessions.cleanup", {})
    assert clean_staged["state"] == "CONFIRM_REQUIRED", clean_staged

    reset = reg.handle(cfg, "sessions.reset",
                       {"sessionKey": sid, "confirm": True})
    assert reset["state"] == "OK" and reset["cleared"] == 2, reset
    assert st.messages(sid)["total"] == 0
