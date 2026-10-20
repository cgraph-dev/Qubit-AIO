"""A local /reset advances the ledger lineage instead of only swapping the in-memory entry."""

from contextlib import closing
from dataclasses import asdict

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionEntry, SessionSource, SessionStore
from qubit_state import SessionDB
import qubit_state_runtime as rt


def _local_session(db, epoch, cwd):
    from qubit_state_local import commit_local_session
    from gateway.session_lifecycle import _now
    from gateway.session_local_recovery import local_identity
    from gateway.session_policy import build_policy

    sid = local_identity("profile", "human", "r")
    source = SessionSource(platform=Platform.LOCAL, chat_id=sid, user_id="human", chat_type="dm")
    now = _now()
    entry = SessionEntry("local:" + sid, sid, now, now, origin=source, platform=Platform.LOCAL)
    policy = build_policy({"source": "cli", "cwd": str(cwd), "model": "m", "toolsets": []},
                          {"platform_toolsets": {"cli": []}}, private_secrets={})
    commit_local_session(db, epoch=epoch, receipt={
        "profile_id": "profile", "principal_id": "human", "request_id": "r", "session_id": sid,
        "route": entry.session_key, "entry": entry.to_dict(), "policy": asdict(policy)})
    return sid


def test_reset_session_advances_the_local_lineage_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr("qubit_state.DEFAULT_DB_PATH", tmp_path / "ambient.db")
    store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    with closing(SessionDB(tmp_path / "state.db")) as db:
        store._db = db  # pin so _db_for_key resolves this store
        epoch = rt.begin_runtime_epoch(db, instance_id="owner")
        sid = _local_session(db, epoch, tmp_path)
        from qubit_state_local import local_receipt

        old = SessionEntry.from_dict(local_receipt(db, sid)["entry"])
        store._entries[old.session_key] = old
        store._loaded = True
        store._local_authority_epoch = epoch

        fresh = store.reset_session(old.session_key)

        assert fresh is not None
        assert fresh.session_id != old.session_id
        assert fresh.is_fresh_reset
        assert store._entries[old.session_key] is fresh
        assert local_receipt(db, sid)["entry"]["session_id"] == fresh.session_id
        assert db.get_session(old.session_id)["end_reason"] == "session_reset"
