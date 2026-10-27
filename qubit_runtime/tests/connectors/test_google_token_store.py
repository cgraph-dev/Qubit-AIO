"""Atomic Google token writes: crash safety, mode, and concurrent refresh.

The writer is stdlib-only and lives with the standalone skill scripts, so the
tests load that exact file rather than a copy.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

TOKEN_STORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "productivity"
    / "google-workspace"
    / "scripts"
    / "_token_store.py"
)


@pytest.fixture
def token_store():
    spec = importlib.util.spec_from_file_location("google_token_store_test", TOKEN_STORE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _temp_files(directory: Path) -> list[Path]:
    return list(directory.glob(".google_token.json.*.tmp"))


def test_temp_file_is_0600_from_creation_and_replaced_atomically(token_store, tmp_path, monkeypatch):
    target = tmp_path / "google_token.json"
    seen: dict[str, int] = {}
    real_replace = token_store.os.replace

    def inspect(src, dst):
        seen["mode"] = stat.S_IMODE(os.stat(src).st_mode)
        real_replace(src, dst)

    old_umask = os.umask(0)
    try:
        monkeypatch.setattr(token_store.os, "replace", inspect)
        token_store.atomic_write_json(target, {"token": "ya29.ok"})
    finally:
        os.umask(old_umask)

    assert seen["mode"] == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == {"token": "ya29.ok"}
    assert _temp_files(tmp_path) == []


def test_failed_replace_leaves_the_original_and_removes_the_temp(token_store, tmp_path, monkeypatch):
    target = tmp_path / "google_token.json"
    token_store.atomic_write_json(target, {"token": "old"})
    original = target.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(token_store.os, "replace", boom)
    with pytest.raises(OSError):
        token_store.atomic_write_json(target, {"token": "new"})

    assert target.read_text(encoding="utf-8") == original
    assert _temp_files(tmp_path) == []


def test_failed_fsync_is_a_failed_write_with_the_original_intact(token_store, tmp_path, monkeypatch):
    target = tmp_path / "google_token.json"
    token_store.atomic_write_json(target, {"token": "old"})
    original = target.read_text(encoding="utf-8")

    def boom(fd):
        raise OSError("I/O error")

    monkeypatch.setattr(token_store.os, "fsync", boom)
    with pytest.raises(OSError):
        token_store.atomic_write_json(target, {"token": "new"})

    assert target.read_text(encoding="utf-8") == original
    assert _temp_files(tmp_path) == []


def test_persist_failure_returns_false_without_raising_or_echoing_the_token(
        token_store, tmp_path, monkeypatch, caplog):
    class Credentials:
        def to_json(self):
            return json.dumps({"token": "ya29.SECRET_VALUE"})

    def boom(src, dst):
        raise OSError("no space left")

    monkeypatch.setattr(token_store.os, "replace", boom)
    target = tmp_path / "google_token.json"
    with caplog.at_level("DEBUG"):
        assert token_store.persist_authorized_user_credentials(target, Credentials()) is False

    assert not target.exists()
    assert "ya29.SECRET_VALUE" not in caplog.text
    assert "ya29.SECRET_VALUE" not in repr(target)


def test_persist_adds_the_authorized_user_type(token_store, tmp_path):
    class Credentials:
        def to_json(self):
            return json.dumps({"token": "ya29.ok", "refresh_token": "r"})

    target = tmp_path / "google_token.json"
    assert token_store.persist_authorized_user_credentials(target, Credentials()) is True

    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "authorized_user"


def test_advisory_lock_serialises_read_modify_write_across_processes(token_store, tmp_path):
    target = tmp_path / "counter.json"
    token_store.atomic_write_json(target, {"n": 0})
    script = f"""
import importlib.util, json, time
spec = importlib.util.spec_from_file_location("ts", {str(TOKEN_STORE_PATH)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for _ in range(25):
    with module.token_file_lock({str(target)!r}):
        value = json.loads(open({str(target)!r}, encoding="utf-8").read())["n"]
        time.sleep(0.001)
        module.atomic_write_json({str(target)!r}, {{"n": value + 1}})
"""
    processes = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(4)]
    for process in processes:
        assert process.wait(timeout=30) == 0

    assert json.loads(target.read_text(encoding="utf-8"))["n"] == 100
    assert _temp_files(tmp_path) == []


def test_connectors_load_the_same_writer():
    from connectors import _google_token_store as bridge

    assert bridge.atomic_write_json is not None
    assert bridge.persist_authorized_user_credentials is not None
    assert bridge.token_file_lock is not None
