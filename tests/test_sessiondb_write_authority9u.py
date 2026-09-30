"""9U — canonical ``state.db`` write-authority invariant.

The single writable chat-transcript store is the vendored
``qubit_state.SessionDB`` at ``<runtime_home>/state.db`` (C7), reached by every
native surface through ``voicebridge.qubit_session_store.QubitSessionStore``.
The terminal/TUI/agent is the vendored runtime's own single writer of the SAME
database. This module pins the Qubit-side invariant that:

* the canonical adapter is the ONE registered Qubit writer of that ``state.db``;
* a second writer that claims the canonical path is detected and refused with a
  typed error (``SESSIONDB_SECOND_WRITER_REFUSED``);
* a divergent ``state.db`` — in particular the operator's external
  ``~/.hermes/state.db`` — is refused as an authority; and
* the ``/chat`` PTY/TUI resolves the SAME canonical home, so a spawn cannot
  silently open a second session authority (``TERMINAL_STATE_HOME_DIVERGED``).

Hermetic: isolated XDG dirs + ``QUBIT_HOME``, no network, no live services, no
vendored PTY spawn.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from voicebridge import pty_ws
from voicebridge import qubit_session_store as QSS


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


@pytest.fixture(autouse=True)
def _fresh_write_authority(monkeypatch):
    """Isolate the process-global writer registry/cache per test."""
    dbs: dict = {}
    monkeypatch.setattr(QSS, "_DBS", dbs)
    monkeypatch.setattr(QSS, "_WRITE_OWNERS", {})
    yield
    for db in list(dbs.values()):
        try:
            db.close()
        except Exception:
            pass


# --------------------------------------------------- canonical adapter writer
def test_canonical_adapter_is_the_registered_writer(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = QSS.QubitSessionStore(cfg)
    canonical = QSS.qubit_state_db_path(cfg)

    assert QSS.STATE_DB_FILENAME == "state.db"
    assert Path(store._path) == canonical
    assert canonical.name == QSS.STATE_DB_FILENAME
    assert QSS.registered_state_db_writers() == {str(canonical): QSS.AUTHORITY}

    authority = QSS.session_db_write_authority(cfg)
    assert authority["writer"] == QSS.AUTHORITY
    assert authority["authority"] == QSS.AUTHORITY
    assert authority["backend"] == "qubit_state.SessionDB"
    assert authority["file"] == "state.db"
    assert Path(authority["home"]) == canonical.parent
    assert authority["external"] is False


def test_one_owner_and_one_cached_handle_per_path(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    a = QSS.QubitSessionStore(cfg)
    b = QSS.QubitSessionStore(cfg)
    canonical = str(QSS.qubit_state_db_path(cfg))

    # Same owner is idempotent; the second adapter shares the ONE handle.
    assert QSS.registered_state_db_writers() == {canonical: QSS.AUTHORITY}
    assert a._db is b._db


# ------------------------------------------------------- second-writer refusal
def test_second_writer_claim_is_detected_and_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    QSS.QubitSessionStore(cfg)
    canonical = str(QSS.qubit_state_db_path(cfg))

    with pytest.raises(QSS.SessionDbWriteAuthorityError) as err:
        QSS.claim_state_db_writer(canonical, owner="voicebridge.other_surface")

    assert err.value.code == "SESSIONDB_SECOND_WRITER_REFUSED"
    assert err.value.owner == "voicebridge.other_surface"
    assert err.value.path == canonical
    assert err.value.as_dict()["state"] == "REFUSED"
    # A refused claim never overwrites the canonical owner.
    assert QSS.registered_state_db_writers()[canonical] == QSS.AUTHORITY


def test_release_then_reclaim_by_a_new_owner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    QSS.QubitSessionStore(cfg)
    canonical = QSS.qubit_state_db_path(cfg)

    QSS.release_state_db_writer(canonical)
    assert canonical not in QSS.registered_state_db_writers()
    assert QSS.claim_state_db_writer(canonical, owner="rollback.seam") == \
        "rollback.seam"


# ------------------------------------------------ divergent / external refusal
def test_divergent_state_db_is_not_the_canonical_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    other = tmp_path / "elsewhere" / QSS.STATE_DB_FILENAME

    with pytest.raises(QSS.SessionDbWriteAuthorityError) as err:
        QSS.assert_canonical_state_db(other, cfg)
    assert err.value.code == "SESSIONDB_SECOND_WRITER_REFUSED"
    assert "canonical chat-transcript store" in err.value.detail


def test_external_hermes_state_db_is_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    external = Path.home() / ".hermes" / QSS.STATE_DB_FILENAME

    with pytest.raises(QSS.SessionDbWriteAuthorityError) as err:
        QSS.assert_canonical_state_db(external, cfg)
    assert "~/.hermes/state.db" in err.value.detail

    with pytest.raises(QSS.SessionDbWriteAuthorityError):
        QSS.refuse_external_state_db(external)

    # ``_db_for`` refuses the external store before constructing any handle.
    with pytest.raises(QSS.SessionDbWriteAuthorityError):
        QSS._db_for(external)

    # The canonical store never lives under the operator's external fork home.
    assert QSS.qubit_state_db_path(cfg).parent != Path.home() / ".hermes"


# --------------------------------------------------- /chat terminal home guard
def test_terminal_resolves_the_same_canonical_state_home(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    canonical = QSS.qubit_state_db_path(cfg)
    terminal_home = pty_ws.terminal_state_home(cfg)

    assert Path(terminal_home) == canonical.parent
    # The resolved home passes the pre-spawn invariant...
    pty_ws._assert_terminal_shares_canonical_store(terminal_home, cfg)
    # ...and the operator's external fork home is a typed refusal.
    with pytest.raises(pty_ws.TerminalContextError) as err:
        pty_ws._assert_terminal_shares_canonical_store(
            Path.home() / ".hermes", cfg)
    assert err.value.code == "TERMINAL_STATE_HOME_DIVERGED"


def test_default_spawn_enforces_the_guard_before_spawning(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    seen = {}

    def _boom(qubit_home, cfg=None):
        seen["home"] = str(qubit_home)
        raise pty_ws.TerminalContextError(
            {"ok": False, "code": "TERMINAL_STATE_HOME_DIVERGED",
             "detail": "diverged"})

    monkeypatch.setattr(pty_ws, "_assert_terminal_shares_canonical_store",
                        _boom)
    # No vendored import is reached: the guard fires first.
    with pytest.raises(pty_ws.TerminalContextError):
        pty_ws._default_spawn(80, 24, "", "", cfg=cfg)
    assert seen["home"] == str(Path(QSS.qubit_state_db_path(cfg).parent))
