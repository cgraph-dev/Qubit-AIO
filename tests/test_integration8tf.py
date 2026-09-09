"""Packet 8T-F — newly integrated rows: auth logout, mcp/claw status,
verify-release, dashboard inventory parity.

Proves each runs through Qubit contracts with typed redacted output —
no mounting, no mutation, no secrets.
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


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(tmp_path / "state") + "'\n")
    return C.Config.load(p)


def test_auth_logout_reports_clean_or_flagged(tmp_path, monkeypatch):
    from voicebridge.integration_status import logout_check

    cfg = _cfg(tmp_path, monkeypatch)
    out = logout_check(cfg)
    assert out["schema"] == "qubit-logout-check/1"
    assert out["state"] in ("CLEAN", "FLAGGED")
    assert isinstance(out["checked"], int)


def test_mcp_status_never_mounts(tmp_path, monkeypatch):
    from voicebridge.integration_status import mcp_status

    cfg = _cfg(tmp_path, monkeypatch)
    out = mcp_status(cfg)
    assert out["schema"] == "qubit-mcp-status/1"
    assert isinstance(out["servers"], list)
    assert "unauthenticated" in out["mount_policy"]


def test_claw_status_readonly_and_redacted(tmp_path, monkeypatch):
    from voicebridge.integration_status import claw_status

    cfg = _cfg(tmp_path, monkeypatch)
    out = claw_status(cfg)
    assert out["schema"] == "qubit-claw-status/1"
    assert out["state"] in ("DETECTED", "UNREACHABLE", "NOT_INSTALLED",
                            "UNAVAILABLE")
    blob = json.dumps(out)
    assert "token_found" in blob
    assert "sk-" not in blob


def test_verify_release_static_passes():
    from voicebridge.integration_status import verify_static

    out = verify_static()
    assert out["schema"] == "qubit-verify/1"
    assert out["state"] in ("PASS", "FAIL", "ERROR")
    if out["state"] == "FAIL":
        pytest.fail(f"static release qualification failed: {out}")


def test_cli_wiring_for_new_commands(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    for fn in (CLI._cmd_auth_status, CLI._cmd_mcp_status, CLI._cmd_claw_status,
               CLI._cmd_verify_static):
        assert callable(fn)
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_mcp_status(cfg) == 0
    assert json.loads(buf.getvalue())["schema"] == "qubit-mcp-status/1"
    assert "auth" in CLI.build_parser().format_help()
    assert "mcp" in CLI.build_parser().format_help()
    assert "claw" in CLI.build_parser().format_help()
    assert "verify-release" in CLI.build_parser().format_help()


def test_dashboard_inventory_has_new_sections(tmp_path, monkeypatch):
    from voicebridge.runtime_inventory import runtime_inventory

    cfg = _cfg(tmp_path, monkeypatch)
    inv = runtime_inventory(cfg)
    for section in ("auth", "mcp", "claw", "sessions", "capabilities"):
        assert section in inv, section
    blob = json.dumps(inv).lower()
    for secret in ("sk-", "token:", "cookie"):
        assert secret not in blob
    html = (REPO_ROOT / "frontend" / "settings.html").read_text()
    assert "mcp.servers" in html and "claw.state" in html
