"""Packet 8T-F — auth inventory + sessions listing (read-only, redacted).

Proves Qubit `auth`/`sessions` behind Qubit contracts: credential
REFERENCES (never values) per provider, redacted recent sessions from the
receipt store, typed UNAVAILABLE states, and CLI command wiring.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, frontier=None):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(tmp_path / "state") + "'\n")
    cfg = C.Config.load(p)
    if frontier is not None:
        cfg.data.setdefault("intent", {})["frontier"] = frontier
    return cfg


def test_auth_status_lists_references_not_values(tmp_path, monkeypatch):
    from voicebridge.auth_status import auth_status

    monkeypatch.setenv("QUBIT_8TF_AUTH_PROBE_KEY", "super-secret-value")
    cfg = _cfg(tmp_path, monkeypatch, frontier={
        "enabled": True, "route": "frontier", "allowlist": ["zen/m1"],
        "providers": {"zen": {"base_url": "https://zen.example/v1",
                              "key_ref": "env:QUBIT_8TF_AUTH_PROBE_KEY"}},
        "max_tokens": 64, "max_context_chars": 4000,
        "max_response_chars": 4000, "timeout_s": 5.0, "max_retries": 1,
        "rpm": 10, "max_concurrency": 2, "daily_usd": 1.0, "monthly_usd": 10.0})
    out = auth_status(cfg)
    assert out["schema"] == "qubit-auth-status/1"
    assert out["frontier_enabled"] is True
    assert {"provider": "zen", "key_ref": "env", "configured": True,
            "allowlisted": True} in out["providers"]
    assert "super-secret-value" not in json.dumps(out)


def test_auth_status_missing_and_disabled(tmp_path, monkeypatch):
    from voicebridge.auth_status import auth_status

    monkeypatch.delenv("QUBIT_8TF_ABSENT_XYZ", raising=False)
    cfg = _cfg(tmp_path, monkeypatch, frontier={
        "enabled": False, "route": "local", "allowlist": [],
        "providers": {"zen": {"key_ref": "env:QUBIT_8TF_ABSENT_XYZ"}}})
    out = auth_status(cfg)
    assert out["frontier_enabled"] is False
    assert {"provider": "zen", "key_ref": "env", "configured": False,
            "allowlisted": False} in out["providers"]


def test_sessions_unavailable_without_store(tmp_path, monkeypatch):
    from voicebridge.auth_status import recent_sessions

    cfg = _cfg(tmp_path, monkeypatch)
    out = recent_sessions(cfg, 5)
    assert out["state"] == "UNAVAILABLE"
    assert out["sessions"] == []


def test_sessions_lists_redacted_receipts(tmp_path, monkeypatch):
    from voicebridge import actions as A
    from voicebridge.auth_status import recent_sessions
    from voicebridge.permissions import PermissionsStore

    cfg = _cfg(tmp_path, monkeypatch)
    (tmp_path / "d").mkdir(parents=True, exist_ok=True)
    import shutil
    (Path(cfg.state_dir())).mkdir(parents=True, exist_ok=True)
    store = A.ActionStore(Path(cfg.state_dir()) / "actions.json")
    perms = PermissionsStore(tmp_path / "perm.json")
    perms.grant("act.browser.draft", requested_by="user", confirmed=True,
                scopes=("web",))
    outcomes = A.load_fixture_outcomes(
        REPO_ROOT / "tests" / "fixtures" / "actions" / "outcomes.json")
    runner = A.ActionRunner(store=store, perms=perms,
                            executors=A.make_fixture_executors(outcomes))
    r = runner.execute("act.browser.draft",
                       args={"recipient": "b@example.com", "subject": "s",
                             "body": "secret-body-xyz"},
                       target="https://mail.google.com", scope="web")
    assert r["state"] == "OK"
    out = recent_sessions(cfg, 5)
    assert out["state"] == "OK" and out["count"] >= 1
    blob = json.dumps(out)
    assert "secret-body-xyz" not in blob
    assert r["receipt_id"] in blob


def test_cli_auth_and_sessions_commands(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CLI, "_cmd_status", lambda cfg: 0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_auth_status(cfg) == 0
    body = json.loads(buf.getvalue())
    assert body["schema"] == "qubit-auth-status/1"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_sessions_list(cfg, 3) == 0
    assert json.loads(buf.getvalue())["schema"] == "qubit-sessions/1"
    assert "auth" in CLI.build_parser().format_help()
    assert "sessions" in CLI.build_parser().format_help()
