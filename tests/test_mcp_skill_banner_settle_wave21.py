"""MCP + skill mutations settle through the pending-approvals banner.

Compat MCP/skill writes stage through ``_compat_confirm`` (409 +
``confirmation_id``). The shared banner's id-only ``POST /api/ops/confirm``
must execute the SAME canonical backend exactly once — not fail
``CONFIRM_UNSUPPORTED``. This pins the ``dashboard_ops._compat_runner``
branches and the staged payloads (``skills_update`` carries the new content;
MCP create carries the validated, reference-only server fields).

Hermetic: temp XDG_*/QUBIT_HOME only; no live service, network, or subprocess.
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

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)
    from voicebridge import qubit_runtime

    qubit_runtime.use_qubit_home(cfg)
    return cfg


def test_compat_runner_has_mcp_and_skill_mutation_branches():
    from voicebridge import dashboard_ops as OPS

    for action in ("skills_update", "skills_delete", "mcp_server_create",
                   "mcp_server_delete", "mcp_server_enabled",
                   "mcp_server_auth"):
        assert OPS._compat_runner(action) is not None, action


def _seed_skill(tmp_path) -> Path:
    import os

    d = Path(os.environ["QUBIT_HOME"]) / "skills" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: x.\n---\n\nOld body.\n")
    return d


def _settle(cfg, out):
    from voicebridge import dashboard_ops as OPS

    assert out.get("code") == "CONFIRM_REQUIRED", out
    cid = out.get("confirmation_id")
    assert cid, out
    return OPS.confirm_op(cfg, cid, actor="dashboard:user",
                          session="dashboard")


def test_skills_update_settles_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import qubit_compat as HC

    d = _seed_skill(cfg)
    new = "---\nname: demo\ndescription: x.\n---\n\nNew body.\n"
    code, staged = HC.skill_update(cfg, {}, {"name": "demo", "content": new},
                                   {})
    assert code == 409
    # nothing applied before confirmation
    assert "Old body." in (d / "SKILL.md").read_text()

    out = _settle(cfg, staged)
    assert out["state"] == "executed", out
    assert (d / "SKILL.md").read_text() == new

    # settle-once: a replay cannot double-apply
    from voicebridge import dashboard_ops as OPS

    again = OPS.confirm_op(cfg, staged["confirmation_id"],
                           actor="dashboard:user", session="dashboard")
    assert again["state"] == "failed", again


def test_skills_delete_settles_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import qubit_compat as HC

    d = _seed_skill(cfg)
    code, staged = HC.skill_delete(cfg, {"name": "demo"}, {"name": "demo"}, {})
    assert code == 409
    assert d.exists()

    out = _settle(cfg, staged)
    assert out["state"] == "executed", out
    assert not d.exists()


def test_mcp_create_enable_delete_settle_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    code, staged = HC.mcp_create(
        cfg, {}, {"name": "probe", "transport": "stdio", "command": "echo",
                  "args": ["hi"], "capabilities": ["ping"]}, {})
    assert code == 409
    assert MS.list_servers(cfg)["servers"] == []

    out = _settle(cfg, staged)
    assert out["state"] == "executed", out
    rows = {s["name"]: s for s in MS.list_servers(cfg)["servers"]}
    assert rows["probe"]["enabled"] is False
    assert rows["probe"]["capabilities"] == ["ping"]

    code, staged = HC.mcp_set_enabled(cfg, {}, {"enabled": True},
                                      {"id": "probe"})
    assert code == 409
    out = _settle(cfg, staged)
    assert out["state"] == "executed", out
    rows = {s["name"]: s for s in MS.list_servers(cfg)["servers"]}
    assert rows["probe"]["enabled"] is True

    code, staged = HC.mcp_delete(cfg, {}, {"name": "probe"}, {"id": "probe"})
    assert code == 409
    out = _settle(cfg, staged)
    assert out["state"] == "executed", out
    assert MS.list_servers(cfg)["servers"] == []


def test_mcp_auth_reference_settles_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    code, out = HC.mcp_create(
        cfg, {}, {"name": "authsrv", "transport": "http",
                  "url": "https://example.test/mcp", "confirm": True}, {})
    assert code == 200, out

    code, staged = HC.mcp_auth(cfg, {}, {"name": "authsrv",
                                        "reference": "env:MCP_TOKEN"},
                               {"id": "authsrv"})
    assert code == 409, staged

    out = _settle(cfg, staged)
    assert out["state"] == "executed", out
    rec = [s for s in MS.list_servers(cfg)["servers"]
           if s["name"] == "authsrv"][0]
    assert rec["auth"] != "none"
