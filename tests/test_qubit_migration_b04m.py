"""B-04M — Hermes migration is fidelity-complete, resumable, and reachable.

Builds a synthetic source ``~/.hermes`` with the vendored ``SessionDB`` schema
(reasoning rows, tool rows, full session columns, a referenced system prompt)
plus auxiliary stores, then drives the real migration apply/rollback and reads
the result back through the canonical Qubit surfaces (chat store, CLI, voice).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, dest_home):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(dest_home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _make_source(home: Path):
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    import qubit_state_common as HSC

    home.mkdir(parents=True, exist_ok=True)
    db = home / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(HSC.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO system_prompts(hash, prompt) VALUES(?, ?)",
        ("prompt-hash-1", "You are Hermes."))
    # Two sessions: rowid order differs from started_at order (the second was
    # inserted later but backdated), so timestamp ordering would scramble them.
    base = {
        "source": "telegram", "user_id": "u-1", "session_key": "tg:u-1",
        "chat_id": "chat-9", "chat_type": "private", "thread_id": "th-7",
        "display_name": "Migrated One", "model": "claude-3",
        "system_prompt_hash": "prompt-hash-1", "profile_name": "default",
        "input_tokens": 111, "output_tokens": 222, "cache_read_tokens": 33,
        "reasoning_tokens": 44, "tool_call_count": 2, "message_count": 4,
        "estimated_cost_usd": 0.012, "title": "Migrated One",
        "title_source": "auto", "billing_provider": "anthropic",
        "started_at": 1000.0, "ended_at": 2000.0,
        "last_activity_at": 2000.0, "archived": 0, "pinned": 1, "hidden": 0,
        "cwd": "/work/a", "rewind_count": 0,
    }
    cols = list(base)
    placeholders = ",".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO sessions(id,{','.join(cols)}) VALUES(?,{placeholders})",
        ("s-first", *[base[c] for c in cols]))
    second = dict(base, source="cli", user_id="u-2", chat_id="chat-2",
                  display_name="Migrated Two", title="Migrated Two",
                  profile_name="work", input_tokens=5, output_tokens=6,
                  started_at=10.0, ended_at=20.0, last_activity_at=20.0,
                  pinned=0, hidden=1, system_prompt_hash=None)
    conn.execute(
        f"INSERT INTO sessions(id,{','.join(cols)}) VALUES(?,{placeholders})",
        ("s-second", *[second[c] for c in cols]))
    msgs = [
        ("s-first", "user", "hello", 1.0, None, None, None, None, None, None),
        ("s-first", "assistant", "hi there", 2.0, "because reasons", None,
         None, 7, "stop", "pm-1"),
        # reasoning-only assistant row (blank content) must survive.
        ("s-first", "assistant", "", 3.0, "chain of thought", None, None,
         9, "stop", "pm-2"),
        ("s-first", "tool", "result", 4.0, None, "search", "tc-1", None,
         None, "pm-3"),
        ("s-second", "user", "second session", 5.0, None, None, None, None,
         None, None),
    ]
    for sid, role, content, ts, reasoning, tool, tcid, tokens, finish, pid in msgs:
        conn.execute(
            "INSERT INTO messages(session_id, role, content, timestamp, "
            "reasoning, tool_name, tool_call_id, token_count, finish_reason, "
            "platform_message_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, role, content, ts, reasoning, tool, tcid, tokens, finish, pid))
    conn.commit()
    conn.close()
    # Auxiliary canonical stores.
    pj = sqlite3.connect(home / "projects.db")
    pj.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT)")
    pj.execute("INSERT INTO projects VALUES('p1','Alpha')")
    pj.commit()
    pj.close()
    sh = sqlite3.connect(home / "shared-state.db")
    sh.execute("CREATE TABLE hosted_rooms(id TEXT PRIMARY KEY)")
    sh.commit()
    sh.close()
    (home / "auth.json").write_text(json.dumps({"providers": {"x": "secret"}}))
    (home / "cron").mkdir(exist_ok=True)
    (home / "cron" / "jobs.json").write_text(json.dumps(
        {"jobs": [{"id": "job-1", "name": "nightly"}]}))
    return db


def test_migration_preserves_message_and_session_fidelity(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_migration as HM

    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src"
    _make_source(src)

    # Pre-seed destination so rollback has a captured prior state.
    pre = CT.store(cfg)
    pre.create("Pre-existing", session_id="keep-me")

    out = HM.apply(cfg, confirm=True, home=src)
    assert out["state"] in ("OK", "PARTIAL"), out
    assert out["imported_sessions"] == 2, out
    assert out["skipped_roles"] == 0, out

    # Journal records rowid/insertion order (not timestamp order).
    journal = json.loads(
        (Path(out["backup_dir"]) / "journal.json").read_text())
    assert journal["imported_ids"] == ["s-first", "s-second"], journal

    store = CT.store(cfg)
    shown = store.show("s-first")
    assert shown["state"] == "OK", shown
    roles = [m["role"] for m in shown["messages"]]
    assert roles == ["user", "assistant", "assistant", "tool"], roles
    # Prompt identity/message id preserved and surfaced.
    assert shown["messages"][1]["message_id"] == "pm-1", shown["messages"][1]
    assert shown["messages"][1]["meta"]["reasoning"] == "because reasons"
    # The reasoning-only row survived with its reasoning intact.
    assert shown["messages"][2]["content"] == ""
    assert shown["messages"][2]["meta"]["reasoning"] == "chain of thought"
    assert shown["messages"][2]["message_id"] == "pm-2"

    raw = store._session("s-first")
    assert raw["source"] == "telegram", raw
    assert raw["user_id"] == "u-1"
    assert raw["chat_id"] == "chat-9" and raw["chat_type"] == "private"
    assert raw["thread_id"] == "th-7"
    assert raw["profile_name"] == "default"
    assert int(raw["pinned"]) == 1 and int(raw["hidden"]) == 0
    assert int(raw["input_tokens"]) == 111
    assert int(raw["output_tokens"]) == 222
    assert int(raw["reasoning_tokens"]) == 44
    # The vendored append bumps the per-session aggregate counters once per
    # inserted row; the migration must restore the exact source counters, not
    # source + imported (the B-04M counter-doubling regression).
    assert int(raw["message_count"]) == 4, raw["message_count"]
    assert int(raw["tool_call_count"]) == 2, raw["tool_call_count"]
    assert float(raw["estimated_cost_usd"]) == 0.012
    assert raw["title"] == "Migrated One"
    assert raw["system_prompt_hash"] == "prompt-hash-1"
    assert float(raw["started_at"]) == 1000.0
    raw2 = store._session("s-second")
    assert raw2["profile_name"] == "work"
    assert int(raw2["hidden"]) == 1

    # Auxiliary stores and typed credential disposition are recorded.
    assert (dest / "projects.db").is_file()
    assert (dest / "shared-state.db").is_file()
    assert out["credentials_audit"]["code"] == "CREDENTIALS_NOT_MIGRATED", out
    assert json.loads((dest / "cron" / "jobs.json").read_text())["jobs"]

    # Canonical reachability: CLI and voice read the same store.
    import voicebridge.cli as CLI
    import voicebridge.voice_commands as VC
    import argparse
    ns = argparse.Namespace(chat_sessions_command="list", limit=20,
                            archived=False)
    assert CLI._cmd_chat_sessions(cfg, ns) == 0
    voice = VC._b_chat_sessions(
        type("C", (), {"cfg": cfg})(), {"action": "list"})
    assert any(s["id"] == "s-first" for s in voice["sessions"]["sessions"])

    # Rollback restores the prior destination and removes created aux stores.
    rb = HM.rollback(cfg, backup_dir=Path(out["backup_dir"]), confirm=True)
    assert rb["state"] == "OK", rb
    assert not (dest / "projects.db").exists()
    assert not (dest / "shared-state.db").exists()
    after = CT.store(cfg)
    assert after.show("keep-me")["state"] == "OK"
    assert after.show("s-first")["state"] == "MISSING"


def test_migration_is_idempotent_and_resumes_partial(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_migration as HM

    dest = tmp_path / "dest2"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src2"
    _make_source(src)

    first = HM.apply(cfg, confirm=True, home=src)
    assert first["imported_sessions"] == 2, first
    second = HM.apply(cfg, confirm=True, home=src)
    assert second["imported_sessions"] == 0, second
    assert second["skipped_sessions"] == 2, second
    # Repair path: a partially imported session (fewer rows than the source
    # expected) is repaired, never skipped forever.
    store = CT.store(cfg)
    store._db._write_sql(
        "DELETE FROM messages WHERE id = (SELECT max(id) FROM messages "
        "WHERE session_id = ?)", ("s-first",))
    assert len(CT.store(cfg).show("s-first")["messages"]) == 3
    partial = HM.apply(cfg, confirm=True, home=src)
    assert partial["imported_sessions"] == 1, partial
    assert len(CT.store(cfg).show("s-first")["messages"]) == 4


def test_migration_imports_compression_closed_session(tmp_path, monkeypatch):
    """PRA-01: a session the source closed by compression still imports.

    Real Hermes homes contain sessions with ``ended_at`` set and
    ``end_reason='compression'``. Restoring those terminal columns before the
    transcript tripped the vendored ``CompressionSessionClosedError`` guard and
    silently dropped every message in the session. The import must append the
    history first, then restore the terminal state.
    """
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_migration as HM
    import sqlite3

    dest = tmp_path / "dest3"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src3"
    _make_source(src)
    # Mark the first session closed by compression, exactly like a real home.
    conn = sqlite3.connect(src / "state.db")
    conn.execute(
        "UPDATE sessions SET ended_at = ?, end_reason = 'compression' "
        "WHERE id = ?", (2000.0, "s-first"))
    conn.commit()
    conn.close()

    out = HM.apply(cfg, confirm=True, home=src)
    assert out["state"] == "OK", out
    assert not out.get("errors"), out.get("errors")
    assert out["imported_sessions"] == 2, out
    assert out["imported_messages"] == 5, out

    store = CT.store(cfg)
    shown = store.show("s-first")
    assert shown["state"] == "OK", shown
    roles = [m["role"] for m in shown["messages"]]
    assert roles == ["user", "assistant", "assistant", "tool"], roles
    # The compression-closed terminal state is restored only after import.
    raw = store._session("s-first")
    assert raw["end_reason"] == "compression", raw
    assert float(raw["ended_at"]) == 2000.0, raw
    # Idempotent re-run does not error on the compression-closed session.
    again = HM.apply(cfg, confirm=True, home=src)
    assert again["imported_sessions"] == 0, again
    assert not again.get("errors"), again.get("errors")
