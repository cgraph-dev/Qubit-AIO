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


#: The operator's real home, captured at module import — BEFORE tests run and
#: before any fixture redirects ``HOME``.
REAL_HOME: "Path | None" = Path(os.path.expanduser("~")).resolve()


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

    The redirect covers every home resolver, not just ``Path.home()``:
    ``~/.hermes`` is checked through ``Path.home()`` (monkeypatched), but
    ``default_home()`` reads ``XDG_STATE_HOME`` and, when truthiness checks fail,
    ``os.path.expanduser("~")`` — which reads ``$HOME``.

    The REAL operator home is captured first. The vendored live-system guard
    resolves its production roots through ``os.path.expanduser("~")``
    (``qubit_runtime/qubit_state_guard.py``: "expanduser reads HOME/passwd, which
    the conftest never rewrites"), so once ``HOME`` points at the scratch
    identity the guard can no longer tell a scratch home from the operator's.
    ``REAL_HOME`` is what the test hands back to the guard for its check.

    Marked ``live_system_guard_bypass`` in spirit: this test's contract IS the
    open of ``$HOME/.qubit/state.db`` from an unset ``QUBIT_HOME``, so the guard
    reads a scratch ``~/.qubit`` exactly where it expects production and refuses
    it. Nothing real is touched — the scratch home is under ``tmp_path``.
    """
    scratch = tmp_path / "operator"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(scratch))
    monkeypatch.setattr(Path, "home", lambda: scratch)
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(scratch) + p[1:] if p.startswith("~") else p)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(scratch / ".local" / "state"))
    return scratch


def test_session_store_resolves_qubit_home_not_hermes(tmp_path, monkeypatch):
    """The canonical store starts fully from the Qubit-owned home.

    The production roots the guard checks are pinned to the operator's REAL
    home (``REAL_HOME``), not this test's scratch identity, so no live
    ``~/.qubit`` / ``~/.hermes`` path is ever opened.
    """
    # The guard's production roots are the REAL operator home's, not the scratch
    # identity's: pin them to the home pytest was launched with so the guard
    # keeps refusing the live ``~/.qubit``/``~/.hermes`` while this test opens
    # its scratch store. ``qubit_state`` binds the root resolvers at import time
    # (``from qubit_state_guard import ...``), so both spellings of the guard
    # module are patched.
    assert REAL_HOME is not None and REAL_HOME.is_absolute()
    import qubit_runtime.qubit_state_guard as _guard
    import qubit_state_guard as _guard_bare
    import qubit_runtime.qubit_state as _state

    for module in (_guard, _guard_bare, _state):
        monkeypatch.setattr(
            module, "_real_platform_state_root",
            lambda: (REAL_HOME / ".qubit").resolve())
        monkeypatch.setattr(
            module, "_legacy_platform_state_root",
            lambda: (REAL_HOME / ".hermes").resolve())

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


def test_pin_honours_hermes_home_as_read_old(tmp_path, monkeypatch):
    """A child launched with only ``HERMES_HOME`` keeps its caller's profile."""
    inherited = tmp_path / "legacy-home"
    inherited.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(inherited))

    home = QR.pin_qubit_home()

    assert QR._resolved(home) == QR._resolved(inherited)
    assert Path(os.environ["QUBIT_HOME"]).resolve() == QR._resolved(inherited)

    explicit = tmp_path / "explicit-qhome"
    explicit.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(explicit))
    assert QR.pin_qubit_home() == explicit


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
