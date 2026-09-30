"""MCP catalog install settles through the pending-approvals banner.

The catalog Install control stages via ``_compat_confirm`` (payload = entry
name + declared env + enable flag), and the banner's id-only
``POST /api/ops/confirm`` must execute the real registry backend exactly
once — not fail ``CONFIRM_UNSUPPORTED`` (the bug: no ``_compat_runner``
branch existed for ``mcp.catalog_install``, so Confirm did nothing).

Hermetic: temp XDG/QUBIT_HOME only; no live service or network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_compat_runner_has_mcp_catalog_install():
    from voicebridge import dashboard_ops as OPS

    assert OPS._compat_runner("mcp.catalog_install") is not None


def test_banner_confirm_executes_catalog_install_once(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/catalog/install", {},
                            {"name": "alltrails"})
    assert code == 409, out
    cid = out.get("confirmation_id")
    assert cid, out
    res = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                         session="dashboard")
    assert res.get("state") == "executed", res
    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert [s["name"] for s in out.get("servers", [])] == ["alltrails"]
    replay = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                            session="dashboard")
    assert replay.get("state") == "failed", replay


def test_banner_cancel_drops_catalog_install(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/mcp/catalog/install", {},
                            {"name": "alltrails"})
    assert code == 409, out
    res = OPS.cancel_op(cfg, out["confirmation_id"], actor="dashboard:user",
                        session="dashboard")
    assert res.get("state") in ("executed", "cancelled", "denied"), res
    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert out.get("servers", []) == []
