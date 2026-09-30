"""9u — migration apply is destination-only, journaled, reversible, idempotent.

Hermetic: every test builds a *synthetic* source home under ``tmp_path`` and
passes it explicitly as ``home=`` so the operator's real ``~/.hermes`` is never
opened. The three contracts proven here are the ones the apply workstream
owns:

* apply writes only into Qubit-owned storage and never mutates the source;
* apply creates a timestamped backup directory plus a ``journal.json``;
* a second apply is a true no-op (no imported rows, no destination file
  rewritten), and :func:`rollback` restores the pre-import destination.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

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


def _make_source(home: Path) -> Path:
    """A minimal but real synthetic source home (state.db + config + .env)."""
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    import qubit_state_common as HSC

    home.mkdir(parents=True, exist_ok=True)
    db = home / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(HSC.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO sessions(id, source, display_name, title, started_at, "
        "last_activity_at, archived, message_count) "
        "VALUES(?,?,?,?,?,?,0,?)",
        ("mig-1", "cli", "Migrated", "Migrated", 1000.0, 1000.0, 2))
    conn.execute(
        "INSERT INTO messages(session_id, role, content, timestamp) "
        "VALUES(?,?,?,?)", ("mig-1", "user", "hello", 1.0))
    conn.execute(
        "INSERT INTO messages(session_id, role, content, timestamp) "
        "VALUES(?,?,?,?)", ("mig-1", "assistant", "hi there", 2.0))
    conn.commit()
    conn.close()
    # A model section + matching .env value: the profile importer records only
    # the ``env:`` reference, never the secret value.
    (home / ".env").write_text("OPENCODE_GO_API_KEY=sk-test-value\n")
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: opencode-go\n"
        "  default: deepseek-v4.1-flash\n"
        "  base_url: https://api.example.invalid/v1\n"
        "  api_key: sk-test-value\n")
    return db


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree(root: Path) -> dict:
    """Recursive path -> (sha256, size) for every regular file under ``root``."""
    out: dict = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (_sha(p), p.stat().st_size)
    return out


def _watched(cfg, dest: Path) -> list:
    state = cfg.state_dir()
    return [
        dest / "state.db",
        dest / "config.yaml",
        dest / "cron" / "jobs.json",
        state / "profiles.json",
        state / "mcp_servers.json",
    ]


def _fingerprint(paths: list) -> dict:
    out: dict = {}
    for p in paths:
        if p.is_file():
            out[str(p)] = (_sha(p), p.stat().st_size, p.stat().st_mtime_ns)
    return out


def test_apply_is_destination_only_journaled_and_reversible(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_migration as HM

    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src"
    _make_source(src)

    # A pre-existing Qubit session so rollback has a captured prior state.
    CT.store(cfg).create("Pre-existing", session_id="keep-me")

    source_before = _tree(src)
    dry = HM.apply(cfg, confirm=False, home=src)
    assert dry["state"] == "DRY_RUN", dry
    assert dry["dry_run"] is True, dry
    # A dry run never writes into the destination either.
    assert CT.store(cfg).show("mig-1")["state"] == "MISSING"

    out = HM.apply(cfg, confirm=True, home=src)
    assert out["state"] in ("OK", "PARTIAL"), out
    assert out["imported_sessions"] == 1, out
    assert out["imported_messages"] == 2, out
    assert out["skipped_sessions"] == 0, out

    # (1) Destination-only: the synthetic source tree is byte-identical.
    assert _tree(src) == source_before
    # The backup lives under Qubit-owned state, never under the source.
    bdir = Path(out["backup_dir"])
    assert bdir.is_dir() and bdir != src
    assert str(bdir).startswith(str(cfg.state_dir())), bdir
    assert not str(bdir).startswith(str(src))

    # (2) Backup + journal created with the recorded rollback command.
    journal_path = bdir / "journal.json"
    assert journal_path.is_file()
    assert (bdir / "source" / "state.db").is_file()
    assert (bdir / "destination" / "state.db").is_file()
    journal = json.loads(journal_path.read_text())
    assert journal["imported_sessions"] == 1
    assert journal["imported_messages"] == 2
    assert journal["backup_dir"] == str(bdir)
    assert journal["rollback"].endswith(f"--backup-dir {bdir} --apply"), journal

    # The profile importer stored only a reference, never the raw value.
    profiles = json.loads((cfg.state_dir() / "profiles.json").read_text())
    recs = profiles["model_profiles"]
    assert any(r.get("key_ref") == "env:OPENCODE_GO_API_KEY" for r in recs)
    assert "sk-test-value" not in json.dumps(profiles)

    # Imported history is reachable through the canonical store.
    assert CT.store(cfg).show("mig-1")["state"] == "OK"

    # (3) Rollback restores the pre-import destination and removes created files.
    rb = HM.rollback(cfg, backup_dir=bdir, confirm=True)
    assert rb["state"] == "OK", rb
    after = CT.store(cfg)
    assert after.show("keep-me")["state"] == "OK"
    assert after.show("mig-1")["state"] == "MISSING"
    assert not (cfg.state_dir() / "profiles.json").exists()


def test_second_apply_is_a_noop(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_migration as HM

    dest = tmp_path / "dest2"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src2"
    _make_source(src)

    first = HM.apply(cfg, confirm=True, home=src)
    assert first["imported_sessions"] == 1, first
    assert first["model_profile"]["profiles"][0]["state"] == "OK", first

    watched = _watched(cfg, dest)
    before = _fingerprint(watched)

    second = HM.apply(cfg, confirm=True, home=src)
    assert second["state"] == "OK", second
    assert second["imported_sessions"] == 0, second
    assert second["skipped_sessions"] == 1, second
    assert second["imported_messages"] == 0, second
    assert second["added_files"] == 0, second
    # The profile guard reports EXISTS instead of rewriting the registry.
    assert second["model_profile"]["profiles"][0]["state"] == "EXISTS", second

    after = _fingerprint(watched)
    assert after == before, {
        "before": before, "after": after,
        "changed": sorted(k for k in set(before) | set(after)
                          if before.get(k) != after.get(k)),
    }
    # And exactly one imported session remains (no duplication).
    assert len(CT.store(cfg).list(limit=100)["sessions"]) == 1
