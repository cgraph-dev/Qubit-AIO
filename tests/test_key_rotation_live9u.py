"""Key rotation in the canonical runtime .env takes effect without a restart.

Live 2026-05-10 (2026-10-05 host clock): the owner replaced a rate-limited
OpenCode Go API key in ``<state_dir>/runtime/.env`` and *every* provider call
kept failing (402) until ``qubit-voicebridge`` was restarted. Root cause: the
vendored runtime imports dotenv, which copies the .env into ``os.environ`` at
startup, and ``resolve_key`` prefers ``os.environ`` — so the stale in-process
copy shadowed the rotated file value.

The dashboard Keys page writes through ``env_store.write_secret``; that path
(and ``remove_secret``) now keeps ``os.environ`` in step with the canonical
store the moment it changes, so a rotation applies immediately.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _Cfg:
    def __init__(self, state_dir: Path):
        self._state = state_dir

    def state_dir(self) -> Path:
        return self._state


def _cfg(tmp_path: Path) -> _Cfg:
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    return _Cfg(tmp_path)


def test_write_secret_refreshes_process_env(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    # Simulate the stale dotenv-loaded copy the vendored runtime leaves behind.
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-old-rate-limited")

    ES.write_secret(cfg, "OPENCODE_GO_API_KEY", "sk-new-rotated")
    assert os.environ["OPENCODE_GO_API_KEY"] == "sk-new-rotated"


def test_remove_secret_clears_process_env(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path)
    ES.write_secret(cfg, "OPENCODE_GO_API_KEY", "sk-to-remove")
    assert os.environ.get("OPENCODE_GO_API_KEY") == "sk-to-remove"
    ES.remove_secret(cfg, "OPENCODE_GO_API_KEY")
    assert "OPENCODE_GO_API_KEY" not in os.environ


def test_rotation_is_visible_to_resolve_key(tmp_path, monkeypatch):
    from voicebridge import env_store as ES
    from voicebridge.providers.credentials import resolve_key

    cfg = _cfg(tmp_path)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-old-rate-limited")
    monkeypatch.setattr(
        "voicebridge.providers.credentials._canonical_runtime_env_path",
        lambda: tmp_path / "runtime" / ".env")

    ES.write_secret(cfg, "OPENCODE_GO_API_KEY", "sk-new-rotated")
    key, code = resolve_key("opencode-go", "env:OPENCODE_GO_API_KEY")
    assert (key, code) == ("sk-new-rotated", "")
