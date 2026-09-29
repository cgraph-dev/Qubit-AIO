"""Qubit-owned migration destinations use Qubit names (read-old/write-new).

The CLI-history import and the migration backup root used to write the
legacy-branded Qubit-owned paths ``<qubit_home>/.hermes_history`` and
``state_dir()/hermes-migration/``. They now write ``<qubit_home>/.qubit_history``
and ``state_dir()/qubit-migration/``. A legacy-named Qubit-owned file/dir is
mirrored once onto the new name when the new one does not exist, so no prior
data is lost; the legacy copy is never deleted.

The reference input ``~/.hermes/.hermes_history`` is a different, read-only
home and keeps its name. These tests are hermetic: every path resolves under a
temp ``HOME``/``QUBIT_HOME`` and the synthetic reference source is only ever
read.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SK_KEY = "sk-proj-AbCdEf0123456789ghijkl"


def _cfg(tmp_path: Path, monkeypatch, qubit_home: Path):
    from voicebridge import config as C

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(qubit_home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _source(tmp_path: Path, text: str) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / ".hermes_history").write_text(text, encoding="utf-8")
    return src


def test_default_destination_and_backup_dirs_are_qubit_named(
        tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")

    dest = HM._history_dest_path(cfg, None)
    assert dest.name == ".qubit_history", dest
    assert dest.parent == Path(os.environ["QUBIT_HOME"]), dest

    hist_backup = HM._history_backup_dir(cfg, None)
    assert hist_backup.parent.name == "qubit-migration", hist_backup
    assert hist_backup.name.startswith("history-"), hist_backup

    backup = HM._backup_dir(cfg, None, tmp_path)
    assert backup.parent.name == "qubit-migration", backup

    # An explicit backup dir is honored verbatim (no forced rename).
    explicit = tmp_path / "given"
    assert HM._history_backup_dir(cfg, explicit) == explicit


def test_legacy_owned_destination_is_migrated_without_data_loss(
        tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")
    qhome = Path(os.environ["QUBIT_HOME"])
    qhome.mkdir(parents=True, exist_ok=True)
    legacy = qhome / ".hermes_history"
    legacy.write_text("legacy qubit line\n", encoding="utf-8")
    legacy_before = legacy.read_bytes()

    src = _source(tmp_path, "from the reference\n")
    bdir = tmp_path / "backup"
    out = HM.import_cli_history_sanitized(
        cfg, home=src, confirm=True, backup_dir=bdir)

    assert out["state"] == "OK", out
    new = qhome / ".qubit_history"
    assert new.is_file()
    text = new.read_text(encoding="utf-8")
    # Both the legacy Qubit-owned history and the reference input survive.
    assert "legacy qubit line" in text
    assert "from the reference" in text
    # Non-destructive: the legacy file is preserved as its own backup.
    assert legacy.read_bytes() == legacy_before
    # The import backed up the destination it was about to change.
    assert (bdir / "destination" / new.name).is_file()


def test_legacy_destination_dry_run_writes_nothing(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")
    qhome = Path(os.environ["QUBIT_HOME"])
    qhome.mkdir(parents=True, exist_ok=True)
    legacy = qhome / ".hermes_history"
    legacy.write_text("legacy qubit line\n", encoding="utf-8")
    before = legacy.read_bytes()

    src = _source(tmp_path, "from the reference\n")
    bdir = tmp_path / "backup"
    out = HM.import_cli_history_sanitized(
        cfg, home=src, confirm=False, backup_dir=bdir)

    assert out["state"] == "DRY_RUN", out
    # The legacy destination is read (so the review is accurate) but neither
    # the new path nor a backup is written on a dry run.
    assert out["destination_existed"] is True, out
    assert out["destination_migrated_from"] == str(legacy), out
    assert not (qhome / ".qubit_history").exists()
    assert not bdir.exists()
    assert legacy.read_bytes() == before


def test_legacy_destination_migration_is_idempotent(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")
    qhome = Path(os.environ["QUBIT_HOME"])
    qhome.mkdir(parents=True, exist_ok=True)
    (qhome / ".hermes_history").write_text("legacy qubit line\n",
                                           encoding="utf-8")

    src = _source(tmp_path, "from the reference\n")
    bdir = tmp_path / "backup"
    first = HM.import_cli_history_sanitized(
        cfg, home=src, confirm=True, backup_dir=bdir)
    assert first["state"] == "OK", first
    new = qhome / ".qubit_history"
    after_first = new.read_bytes()

    second = HM.import_cli_history_sanitized(
        cfg, home=src, confirm=True, backup_dir=bdir)
    assert second["state"] == "NOOP", second
    assert new.read_bytes() == after_first


def test_reference_source_is_read_only_and_not_the_destination(
        tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")
    ref_home = Path(os.environ["HOME"]) / ".hermes"
    ref_home.mkdir(parents=True, exist_ok=True)
    src_file = ref_home / ".hermes_history"
    src_file.write_text(f"export KEY={SK_KEY}\nqubit doctor\n",
                        encoding="utf-8")
    before = src_file.read_bytes()
    mtime_ns = src_file.stat().st_mtime_ns

    bdir = tmp_path / "backup"
    out = HM.import_cli_history_sanitized(cfg, confirm=True, backup_dir=bdir)

    assert out["source"] == str(src_file), out
    assert out["state"] == "OK", out
    # The reference home is never written.
    assert src_file.read_bytes() == before
    assert src_file.stat().st_mtime_ns == mtime_ns
    assert not (ref_home / ".qubit_history").exists()
    # The destination is Qubit-owned and never carries the raw secret.
    new = Path(os.environ["QUBIT_HOME"]) / ".qubit_history"
    assert new.is_file()
    assert SK_KEY not in new.read_text(encoding="utf-8")


def test_legacy_backup_root_is_mirrored_read_old_write_new(
        tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")
    base = cfg.state_dir()
    legacy = base / "hermes-migration"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "old-backup.txt").write_text("keep me\n", encoding="utf-8")

    bdir = HM._history_backup_dir(cfg, None)

    new = base / "qubit-migration"
    assert bdir.parent == new, bdir
    # The prior backup was mirrored onto the new root, not lost.
    assert (new / "old-backup.txt").read_text(encoding="utf-8") == "keep me\n"
    # The legacy root is preserved (never deleted).
    assert (legacy / "old-backup.txt").is_file()


def test_explicit_dest_is_not_auto_migrated(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "qhome")
    qhome = Path(os.environ["QUBIT_HOME"])
    qhome.mkdir(parents=True, exist_ok=True)
    (qhome / ".hermes_history").write_text("legacy qubit line\n",
                                           encoding="utf-8")

    src = _source(tmp_path, "from the reference\n")
    explicit = tmp_path / "explicit" / "history.txt"
    out = HM.import_cli_history_sanitized(
        cfg, home=src, dest=explicit, confirm=True,
        backup_dir=tmp_path / "backup")

    assert out["state"] == "OK", out
    assert explicit.is_file()
    assert "legacy qubit line" not in explicit.read_text(encoding="utf-8")
    assert not (qhome / ".qubit_history").exists()
