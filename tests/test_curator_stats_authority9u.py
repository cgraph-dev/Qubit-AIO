"""UI-audit 2026-09-29 — curator + host-version read authority.

Two served-surface split-brains fixed at the Qubit API boundary:

* ``GET /api/curator`` was fork-proxied while ``PUT /api/curator/paused`` wrote
  the compat store, so the pause badge reset on reload. Both must resolve
  through ``QUBIT_READ_AUTHORITY`` to the Qubit compat handler.
* ``GET /api/system/stats`` was fork-proxied and emitted only
  ``hermes_version`` (a legacy label) with no ``qubit_version``, so SystemPage's
  version row rendered blank and a legacy token reached the served surface.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_curator_and_stats_are_qubit_served(tmp_path, monkeypatch):
    from voicebridge import webui as W

    # ``qubit_read_authority`` receives the compat path (the ``/agent`` SPA
    # prefix is stripped before this check).
    for path in ("/api/curator", "/api/curator/paused", "/api/system/stats"):
        assert W.qubit_read_authority(path) is True, path


def test_curator_pause_reads_back_through_the_same_store(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.h_curator(cfg, {}, {}, {})
    assert code == 200 and out["paused"] is False, (code, out)

    code, out = HC.h_curator_paused(cfg, {}, {"paused": True}, {})
    assert code == 200 and out["paused"] is True, (code, out)

    code, out = HC.h_curator(cfg, {}, {}, {})
    assert code == 200 and out["paused"] is True, (code, out)


def test_system_stats_emits_qubit_version_not_legacy(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.h_system_stats(cfg, {}, {}, {})
    assert code == 200, (code, out)
    assert out.get("qubit_version"), out
    assert "hermes_version" not in out, out
