"""Office store read-old adoption.

The voice-layout change moved the canonical office DB to
``$QUBIT_HOME/voice/state/office/office.db``. Existing installs kept their data
at the pre-layout XDG path, so a reinstall/layout reset can leave an empty
canonical DB beside a populated legacy one and appear to lose the org. These
tests pin the adoption contract:

1. legacy-only populated -> copied into the canonical path and readable;
2. empty canonical + populated legacy -> adopted, the empty file backed up;
3. populated canonical + populated legacy -> canonical wins, no overwrite;
4. neither -> a normal new (empty) store, as before;
5. the legacy DB is never moved, deleted, or mutated;
6. adoption is idempotent (a second open creates no second backup).

Hermetic: tmp XDG and ``QUBIT_HOME`` only; no network, no live service.
"""
from __future__ import annotations

import hashlib
import sqlite3
from types import SimpleNamespace

from voicebridge.office import store


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qubit-home"))


def _cfg(tmp_path, monkeypatch):
    """A real Config with the new voice tree already present.

    Creating ``$QUBIT_HOME/voice`` pins ``voice_layout().mode`` to ``"new"``,
    so the canonical store is the QUBIT_HOME path even while the legacy XDG
    tree exists — the exact state a reinstall leaves behind. Without it the
    resolver correctly reports ``read-old`` and reads the legacy file in place
    (no adoption needed).
    """
    _isolate(tmp_path, monkeypatch)
    (tmp_path / "qubit-home" / "voice").mkdir(parents=True, exist_ok=True)
    from voicebridge import config as C

    path = tmp_path / "voice.yaml"
    path.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(path)


def _legacy_path():
    from voicebridge.paths import read_old_voice_dirs

    return read_old_voice_dirs()["state"] / "office" / "office.db"


def _legacy_state_dir():
    return _legacy_path().parent.parent


def _seed(state_dir, slug):
    """Create a migrated DB under ``state_dir`` with one department + agent."""
    cfg = SimpleNamespace(state_dir=lambda: state_dir)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_department(slug, name, division_kind,"
            " created_at) VALUES(?, ?, 'product', 0.0)",
            (slug, slug.upper()))
        conn.execute(
            "INSERT INTO office_agent(slug, name, seat_type, created_at)"
            " VALUES(?, 'CEO', 'ceo', 0.0)", (f"{slug}-ceo",))
        conn.commit()
    finally:
        conn.close()


def _empty_db_at(path):
    """A schema-only DB (migrated, zero rows) at ``path`` without adoption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        store.migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _departments(path):
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute(
            "SELECT slug FROM office_department ORDER BY slug")]
    finally:
        conn.close()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backups(canonical):
    return sorted(canonical.parent.glob("office.db.pre-adopt-*.bak"))


def test_legacy_only_populated_is_adopted_and_readable(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(_legacy_state_dir(), "legacy")
    canonical = store.db_path(cfg)
    legacy = _legacy_path()
    assert not canonical.exists()
    legacy_sha = _sha(legacy)

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        slugs = [row[0] for row in conn.execute(
            "SELECT slug FROM office_department ORDER BY slug")]
    finally:
        conn.close()

    assert slugs == ["legacy"]
    assert _departments(canonical) == ["legacy"]
    # the legacy store is read-only input: untouched and still populated
    assert legacy.exists()
    assert _sha(legacy) == legacy_sha
    assert _departments(legacy) == ["legacy"]


def test_empty_canonical_is_backed_up_before_adoption(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(_legacy_state_dir(), "legacy")
    canonical = store.db_path(cfg)
    _empty_db_at(canonical)
    assert _departments(canonical) == []
    legacy_sha = _sha(_legacy_path())

    store.init(cfg)

    assert _departments(canonical) == ["legacy"]
    backups = _backups(canonical)
    assert len(backups) == 1
    # the pre-adoption canonical was empty; its backup preserves that fact
    assert _departments(backups[0]) == []
    assert _sha(_legacy_path()) == legacy_sha


def test_populated_canonical_wins_over_populated_legacy(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    canonical = store.db_path(cfg)
    _seed(cfg.state_dir(), "canon")
    _seed(_legacy_state_dir(), "legacy")
    assert _departments(canonical) == ["canon"]

    store.init(cfg)

    assert _departments(canonical) == ["canon"]
    assert _departments(_legacy_path()) == ["legacy"]
    assert _backups(canonical) == []


def test_neither_store_creates_a_normal_new_db(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    canonical = store.db_path(cfg)
    legacy = _legacy_path()
    assert not canonical.exists()
    assert not legacy.exists()

    store.init(cfg)

    assert canonical.exists()
    assert _departments(canonical) == []
    assert not legacy.exists()
    assert _backups(canonical) == []


def test_empty_legacy_is_not_adopted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _empty_db_at(_legacy_path())
    canonical = store.db_path(cfg)

    store.init(cfg)

    assert _departments(canonical) == []
    assert _backups(canonical) == []
    # the empty legacy store is left alone too
    assert _departments(_legacy_path()) == []


def test_adoption_is_idempotent(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(_legacy_state_dir(), "legacy")
    canonical = store.db_path(cfg)

    store.init(cfg)
    assert _departments(canonical) == ["legacy"]
    assert _backups(canonical) == []

    # A second open sees a populated canonical store and does nothing.
    store.init(cfg)
    assert _departments(canonical) == ["legacy"]
    assert _backups(canonical) == []
