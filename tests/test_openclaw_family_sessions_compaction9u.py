"""9U — OpenClaw compaction/lineage/companion sessions family.

Three independent proofs:

1. static (AST) — every registered method is reached by the manifest's behavior
   proof; each typed SAFE_EQUIVALENT handler emits its declared code and every
   IMPLEMENTED handler does real backend work;
2. manifest — the regenerated disposition rows match this family;
3. runtime — real compaction over the canonical store (soft-archive +
   ``archive_and_compact``), checkpoint branch/restore/switch, companion
   ask/state/reset, confirmation, e-stop, invalid input and recovery.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

FAMILY_MODULE = ("voicebridge/gateway/openclaw_handlers/"
                 "sessions_compaction.py")

SAFE_EQUIVALENT = {
    "sessions.branches.switch": "SESSIONS_BRANCH_SWITCH_LOCAL_RESTORE",
    "sessions.compaction.branch": "SESSIONS_COMPACTION_BRANCH_LOCAL",
    "sessions.compaction.restore": "SESSIONS_COMPACTION_RESTORE_LOCAL",
}

IMPLEMENTED = (
    "sessions.compact",
    "sessions.companion.ask",
    "sessions.companion.state",
    "sessions.companion.reset",
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


def _session(cfg, turns=10):
    from voicebridge import chat_transport as CT

    reg = _registry()
    sid = reg.handle(cfg, "sessions.create", {"name": "cmp"})["session"]["id"]
    st = CT.store(cfg)
    for i in range(turns):
        st.append(sid, "user", f"question {i} about the deploy pipeline")
        st.append(sid, "assistant", f"answer {i} with rollback detail")
    return reg, st, sid


# ---------------------------------------------------------------- static proof
def test_registered_implementations_pass_manifest_behavior_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items()
            if origin.endswith("openclaw_handlers/sessions_compaction.py")}
    assert ours == set(IMPLEMENTED) | set(SAFE_EQUIVALENT), sorted(ours)
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        if method in SAFE_EQUIVALENT:
            assert OUM._emits_code(node, funcs, SAFE_EQUIVALENT[method],
                                   consts), (
                f"{method} never emits {SAFE_EQUIVALENT[method]!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} does no backend work")


def test_manifest_dispositions_match_family():
    import json

    manifest = json.loads((REPO_ROOT / "docs" / "archive" / "2026-09-21"
                           / "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
                           ).read_text())
    rows = manifest["pinned_surface_disposition"]["methods"]
    for method in IMPLEMENTED:
        assert rows[method]["status"] == "IMPLEMENTED", rows[method]
    for method, code in SAFE_EQUIVALENT.items():
        assert rows[method]["status"] == "SAFE_EQUIVALENT", rows[method]
        assert rows[method]["code"] == code, rows[method]


# --------------------------------------------------------------- runtime proof
def test_compact_is_confirmation_gated_and_soft_archives(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg)

    staged = reg.handle(cfg, "sessions.compact", {"sessionKey": sid})
    assert staged["state"] == "CONFIRM_REQUIRED", staged

    before = st.messages(sid)["total"]
    out = reg.handle(cfg, "sessions.compact",
                     {"sessionKey": sid, "maxLines": 4, "confirm": True})
    assert out["state"] == "OK" and out["compacted"] is True, out
    assert out["reason"] == "manual_trim" and out["before"] == 20
    assert out["after"] == 4, out
    assert out["checkpoint"]["messageCount"] == 20
    # The originals are soft-archived (still on disk, recoverable), not dropped.
    all_rows = st._db.get_messages(sid, include_inactive=True)
    assert len(all_rows) > before, (len(all_rows), before)


def test_invalid_input_and_estop(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg)

    with pytest.raises(GatewayError):
        reg.handle(cfg, "sessions.compact",
                   {"sessionKey": sid, "maxLines": "nope", "confirm": True})
    with pytest.raises(GatewayError):
        reg.handle(cfg, "sessions.compaction.branch",
                   {"sessionKey": sid, "confirm": True})

    monkeypatch.setattr("voicebridge.broker.estop_engaged",
                        lambda cfg=None: True)
    estop = reg.handle(cfg, "sessions.compact",
                       {"sessionKey": sid, "confirm": True})
    assert estop["state"] == "UNAVAILABLE"
    assert estop["code"] == "EMERGENCY_STOP"


def test_model_compact_path_persists_through_store(tmp_path, monkeypatch):
    """The model-backed path is wired to the vendored summariser; here we
    inject a deterministic summary list to prove the persistence/counters
    contract (production calls ContextCompressor)."""
    from voicebridge import session_compaction as SC

    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg)

    def _summary(_store, _rec, messages, _focus):
        return list(messages)[:3]

    monkeypatch.setattr(SC, "_vendored_summarize", _summary)
    out = reg.handle(cfg, "sessions.compact",
                     {"sessionKey": sid, "confirm": True})
    assert out["state"] == "OK" and out["compacted"] is True, out
    assert out["reason"] == "model" and out["after"] == 3, out


def test_checkpoint_branch_restore_round_trip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg)
    compacted = reg.handle(cfg, "sessions.compact",
                           {"sessionKey": sid, "maxLines": 4, "confirm": True})
    ckpt = compacted["checkpoint"]["id"]

    staged = reg.handle(cfg, "sessions.compaction.branch",
                        {"sessionKey": sid, "checkpointId": ckpt})
    assert staged["state"] == "CONFIRM_REQUIRED", staged

    made = reg.handle(cfg, "sessions.compaction.branch",
                      {"sessionKey": sid, "checkpointId": ckpt,
                       "confirm": True})
    assert made["state"] == "OK", made
    assert made["code"] == "SESSIONS_COMPACTION_BRANCH_LOCAL"
    branch_sid = made["sessionId"]
    branch_msgs = st._db.get_messages(branch_sid)
    assert len(branch_msgs) == 20, len(branch_msgs)
    # branch lineage is recorded on the canonical row
    rec = st._db.get_session(branch_sid)
    assert "_branched_from" in (rec.get("model_config") or "")

    missing = reg.handle(cfg, "sessions.compaction.branch",
                         {"sessionKey": sid, "checkpointId": "nope",
                          "confirm": True})
    assert missing["state"] == "MISSING", missing

    restored = reg.handle(cfg, "sessions.compaction.restore",
                          {"sessionKey": sid, "checkpointId": ckpt,
                           "confirm": True})
    assert restored["state"] == "OK", restored
    assert restored["code"] == "SESSIONS_COMPACTION_RESTORE_LOCAL"
    assert restored["restored"] == 20, restored
    # recovery: the restored transcript is active again in the canonical store
    assert len(st._db.get_messages(sid)) == 20


def test_branches_switch_restores_branch_in_place(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg)
    compacted = reg.handle(cfg, "sessions.compact",
                           {"sessionKey": sid, "maxLines": 4, "confirm": True})
    ckpt = compacted["checkpoint"]["id"]
    branch_sid = reg.handle(cfg, "sessions.compaction.branch",
                            {"sessionKey": sid, "checkpointId": ckpt,
                             "confirm": True})["sessionId"]

    staged = reg.handle(cfg, "sessions.branches.switch",
                        {"sessionKey": sid, "leafEntryId": branch_sid})
    assert staged["state"] == "CONFIRM_REQUIRED", staged

    out = reg.handle(cfg, "sessions.branches.switch",
                     {"sessionKey": sid, "leafEntryId": branch_sid,
                      "confirm": True})
    assert out["state"] == "OK", out
    assert out["code"] == "SESSIONS_BRANCH_SWITCH_LOCAL_RESTORE"
    assert out["switchedTo"] == branch_sid
    assert len(st._db.get_messages(sid)) == 20


def test_companion_ask_state_reset(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg, turns=1)

    initial = reg.handle(cfg, "sessions.companion.state", {"sessionKey": sid})
    assert initial["state"] == "OK" and initial["active"] is False
    assert initial["companionSessionId"] == f"{sid}-companion"

    def _fake_turn(_cfg, text, *, session=None, model=None, timeout_s=None,
                   cancel=None, role="chat", actor="dashboard:user"):
        store = CT.store(_cfg)
        store.append(session, "user", text)
        store.append(session, "assistant", f"answer: {text}")
        return {"ok": True, "reply": f"answer: {text}", "session": session,
                "message_count": 2}

    monkeypatch.setattr(CT, "turn", _fake_turn)
    asked = reg.handle(cfg, "sessions.companion.ask",
                       {"sessionKey": sid, "question": "why?"})
    assert asked["state"] == "OK" and asked["answer"] == "answer: why?", asked
    assert asked["companionSessionId"] == f"{sid}-companion"

    after = reg.handle(cfg, "sessions.companion.state", {"sessionKey": sid})
    assert after["active"] is True and after["messageCount"] == 2, after

    staged = reg.handle(cfg, "sessions.companion.reset", {"sessionKey": sid})
    assert staged["state"] == "CONFIRM_REQUIRED", staged
    reset = reg.handle(cfg, "sessions.companion.reset",
                       {"sessionKey": sid, "confirm": True})
    assert reset["state"] == "OK" and reset["reset"] is True, reset
    assert reset["cleared"] == 2, reset
    final = reg.handle(cfg, "sessions.companion.state", {"sessionKey": sid})
    assert final["messageCount"] == 0, final


def test_companion_ask_requires_question(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg, st, sid = _session(cfg, turns=1)
    with pytest.raises(GatewayError):
        reg.handle(cfg, "sessions.companion.ask", {"sessionKey": sid})
