"""Plugin enabled/disabled state survives migration (bug fix, coverage §3.1).

Regression this pins: ``_import_plugin_state`` used to read the source
``plugins/<name>/state.json`` and then hard-return ``state="UNSUPPORTED"`` with
``recorded: []`` — so a plugin **disabled** in the reference home migrated its
files but its disabled bit was silently dropped, and the plugin arrived
ENABLED in Qubit.

The contract now proven here, all against synthetic temp homes:

* a source plugin whose ``state.json`` says DISABLED is unioned into the
  destination ``<runtime_home>/config.yaml`` ``plugins.disabled`` deny-list (the
  vendored ``_get_disabled_plugins`` gate);
* migration is one-directional — it never writes ``plugins.enabled``, so it can
  never mark ENABLED anything the source had disabled;
* an ENABLED / UNKNOWN source plugin is *not* added to the deny-list;
* a re-run adds nothing and changes nothing (idempotent);
* an existing destination ``plugins.disabled`` entry is preserved (additive);
* the write goes through the comment-preserving ``atomic_config_write`` seam,
  preserving unrelated destination config keys.

Hermetic: the destination is a scratch ``QUBIT_HOME`` and the source is passed
explicitly as ``home=``; the operator's real ``~/.hermes`` is never opened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

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


def _make_source(home: Path, states: dict) -> None:
    """A source home with one ``plugins/<name>/state.json`` per entry."""
    home.mkdir(parents=True, exist_ok=True)
    for name, payload in states.items():
        pdir = home / "plugins" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "state.json").write_text(json.dumps(payload))


def _dest_plugins(dest: Path) -> dict:
    cfg_path = dest / "config.yaml"
    if not cfg_path.is_file():
        return {}
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    plugins = data.get("plugins") or {}
    return plugins if isinstance(plugins, dict) else {}


def test_disabled_plugin_stays_disabled_after_migration(tmp_path, monkeypatch):
    """THE BUG: a source-disabled plugin must not arrive enabled."""
    from voicebridge import qubit_migration as QM

    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src"
    _make_source(src, {
        "hermes-achievements": {"enabled": False},
        "hermes-notes": {"enabled": True},
        "mystery-plugin": {"note": "no explicit state"},
    })

    report = QM._import_plugin_state(cfg, src)

    assert report["state"] == "OK", report
    assert report["source_disabled"] == ["hermes-achievements"], report
    plugins = _dest_plugins(dest)
    # The disabled plugin is recorded disabled...
    assert plugins.get("disabled") == ["hermes-achievements"], plugins
    # ...and the deny-list wins over any allow-list (never touched).
    assert "enabled" not in plugins, plugins


def test_migration_never_writes_enabled_allowlist(tmp_path, monkeypatch):
    """Asymmetry: an ENABLED source plugin is not positively enabled here."""
    from voicebridge import qubit_migration as QM

    dest = tmp_path / "dest-enabled"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src-enabled"
    _make_source(src, {"only-enabled": {"enabled": True}})

    report = QM._import_plugin_state(cfg, src)

    # Nothing DISABLED at source -> no-op, no config written at all.
    assert report["state"] == "NOOP", report
    assert not (dest / "config.yaml").is_file(), "no config should be created"


def test_plugin_state_import_is_idempotent_and_additive(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as QM

    dest = tmp_path / "dest-idem"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src-idem"
    _make_source(src, {"plugin-a": {"disabled": True}})

    first = QM._import_plugin_state(cfg, src)
    assert first["state"] == "OK", first
    assert first["added"] == ["plugin-a"], first
    after_first = (dest / "config.yaml").read_text(encoding="utf-8")

    # Pre-seed an unrelated destination disabled entry + an unrelated key.
    data = yaml.safe_load(after_first) or {}
    data["plugins"]["disabled"] = sorted(
        set(data["plugins"]["disabled"]) | {"already-here"})
    data["session_reset"] = {"mode": "keep"}
    from qubit_cli.config import atomic_config_write

    atomic_config_write(dest / "config.yaml", data)

    second = QM._import_plugin_state(cfg, src)
    assert second["state"] == "NOOP", second
    assert second["already_present"] == ["plugin-a"], second

    final = yaml.safe_load((dest / "config.yaml").read_text(encoding="utf-8"))
    assert final["plugins"]["disabled"] == ["already-here", "plugin-a"], final
    # The unrelated destination key survived the additive union.
    assert final["session_reset"] == {"mode": "keep"}, final


def test_disabled_state_reaches_destination_through_apply(tmp_path, monkeypatch):
    """End-to-end: ``apply()`` drives the plugin-state import (safe mode too)."""
    from voicebridge import qubit_migration as QM
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    import sqlite3

    import qubit_state_common as HSC

    dest = tmp_path / "dest-apply"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src-apply"
    src.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(src / "state.db")
    conn.executescript(HSC.SCHEMA_SQL)
    conn.commit()
    conn.close()
    _make_source(src, {"hermes-achievements": {"enabled": False}})

    out = QM.apply(cfg, confirm=True, home=src, safe=True)
    assert out["state"] in ("OK", "PARTIAL"), out
    plugin_state = out.get("plugin_state") or {}
    assert plugin_state.get("state") == "OK", plugin_state
    assert plugin_state.get("added") == ["hermes-achievements"], plugin_state

    plugins = _dest_plugins(dest)
    assert plugins.get("disabled") == ["hermes-achievements"], plugins
    assert "enabled" not in plugins, plugins
