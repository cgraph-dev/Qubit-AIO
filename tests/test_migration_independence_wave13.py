"""Wave 13 — Qubit has no runtime dependency on the reference home.

Proves the ``docs/DECISIONS.md`` #9 / ``docs/MIGRATION.md`` independence
invariant: with ``QUBIT_HOME`` unset and ``Path.home()`` redirected to a scratch
operator identity (so the platform-default ``~/.hermes`` is a throwaway path),
starting Qubit's canonical session authority must resolve and materialize a
Qubit-owned home — never ``~/.hermes``.

Regression this pins: the vendored ``qubit_state`` import resolves its home at
import time as ``QUBIT_HOME`` -> ``~/.hermes`` and *creates* it as an import
side effect. Before the fix, constructing ``QubitSessionStore`` with
``QUBIT_HOME`` unset materialized ``$HOME/.hermes`` (the real reference home in
production). ``voicebridge.qubit_runtime.pin_qubit_home`` now publishes the
Qubit-owned home first, fail-closed, and ``runtime_home_for_cfg`` keeps a
caller-supplied non-external home.

Hermetic: isolated ``XDG_*`` + scratch ``HOME``/``Path.home``; the operator's
real ``~/.hermes`` is never read, written, renamed, or moved.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from voicebridge import qubit_runtime as QR
from voicebridge import qubit_session_store as QSS


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


@pytest.fixture(autouse=True)
def _isolate_store(monkeypatch):
    """Isolate the process-global session DB cache/writer registry per test."""
    dbs: dict = {}
    monkeypatch.setattr(QSS, "_DBS", dbs)
    monkeypatch.setattr(QSS, "_WRITE_OWNERS", {})
    yield
    for db in list(dbs.values()):
        try:
            db.close()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _scratch_operator_home(tmp_path, monkeypatch):
    """Redirect the operator identity so ``~/.hermes`` is a scratch path.

    This is what makes the no-fallback proof non-destructive: the test names
    ``~/.hermes`` but resolves it under ``tmp_path``; the real reference home is
    never touched.
    """
    scratch = tmp_path / "operator"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(scratch))
    monkeypatch.setattr(Path, "home", lambda: scratch)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    return scratch


def test_session_store_resolves_qubit_home_not_hermes(tmp_path, monkeypatch):
    """The canonical store starts fully from the Qubit-owned home."""
    cfg = _cfg(tmp_path, monkeypatch)
    external = QR._external_default_home()
    assert external == Path.home() / ".hermes"
    assert not external.exists()

    store = QSS.QubitSessionStore(cfg)

    # The import-time side effect must NOT materialize the reference home.
    assert not external.exists()
    assert not (external / QSS.STATE_DB_FILENAME).exists()

    home = Path(os.environ["QUBIT_HOME"]).resolve()
    assert home != external.resolve()
    assert home.is_relative_to(tmp_path)
    assert Path(store._path) == QSS.qubit_state_db_path(cfg)
    assert Path(store._path).is_relative_to(tmp_path)


def test_fresh_scratch_home_produces_qubit_owned_paths(tmp_path, monkeypatch):
    """An unset home pins to an XDG Qubit path, never the operator's."""
    home = QR.pin_qubit_home()
    env_home = Path(os.environ["QUBIT_HOME"]).resolve()
    assert env_home == QR._resolved(home)
    assert env_home != QR._resolved(QR._external_default_home())
    assert env_home.is_relative_to(tmp_path)
    assert not QR._external_default_home().exists()


def test_pin_rejects_external_default_and_keeps_explicit_home(
        tmp_path, monkeypatch):
    external = Path.home() / ".hermes"
    monkeypatch.setenv("QUBIT_HOME", str(external))
    pinned = QR.pin_qubit_home()
    assert QR._resolved(pinned) != QR._resolved(external)
    assert QR._resolved(os.environ["QUBIT_HOME"]) != QR._resolved(external)
    assert not external.exists()

    explicit = tmp_path / "explicit-qhome"
    monkeypatch.setenv("QUBIT_HOME", str(explicit))
    assert QR.pin_qubit_home() == explicit
    assert os.environ["QUBIT_HOME"] == str(explicit)


def test_runtime_home_for_cfg_ignores_external_home(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    external = Path.home() / ".hermes"
    monkeypatch.setenv("QUBIT_HOME", str(external))

    home = QR.runtime_home_for_cfg(cfg)

    assert QR._resolved(home) != QR._resolved(external)
    assert QR._resolved(home).is_relative_to(tmp_path)
    assert QR._resolved(os.environ["QUBIT_HOME"]) == QR._resolved(home)
    assert not external.exists()


def test_external_state_db_is_refused_as_authority(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    external_db = Path.home() / ".hermes" / QSS.STATE_DB_FILENAME
    with pytest.raises(QSS.SessionDbWriteAuthorityError):
        QSS.refuse_external_state_db(external_db)
    canonical = QSS.qubit_state_db_path(cfg)
    assert QSS.refuse_external_state_db(canonical) == canonical
