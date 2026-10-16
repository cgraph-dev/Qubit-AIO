"""Hermetic receipts: local Office memory is not external backend selection."""
import json
from types import SimpleNamespace

import pytest
from test_office_agent_memory import _cfg, _org, _kinds


@pytest.mark.parametrize("receipt", [None, {}, True, {"state": "FAILED", "stored": False},
    {"state": "EMPTY", "stored": False}, {"state": "OK", "stored": False},
    {"state": "FAILED", "stored": True}, {"status": "submitted", "message_status": "accepted"}])
def test_autowork_rejects_incomplete_receipts(monkeypatch, receipt):
    from voicebridge.office import autowork, agent_memory
    monkeypatch.setattr(autowork, "_installed", lambda name: True)
    monkeypatch.setattr(agent_memory, "remember", lambda *a, **k: receipt)
    assert autowork._remember(None, 1, text="outcome") is False


def test_autowork_real_local_write_and_failure(tmp_path, monkeypatch):
    from voicebridge import context_store as cs
    from voicebridge.office import autowork, agent_memory
    cfg = _cfg(tmp_path, monkeypatch)
    aid = _org(cfg)["w1"]
    monkeypatch.setattr(autowork, "_installed", lambda name: True)
    # Provider environment does not select Office's context_store backend.
    monkeypatch.setenv("OPENVIKING_URL", "http://must-not-connect.invalid:1933")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    assert autowork._remember(cfg, aid, text="local-receipt-proof") is True
    assert "local-receipt-proof" in (cs.root(cfg) / "user/memories.md").read_text()
    assert not (tmp_path / "vault").exists()
    before = _kinds(cfg, aid)
    monkeypatch.setattr(cs, "add_memory", lambda *a, **k: {"state": "FAILED"})
    assert autowork._remember(cfg, aid, text="failed-receipt-proof") is False
    assert _kinds(cfg, aid) == before
    assert all("failed-receipt-proof" not in m["text"] for m in agent_memory.recall(cfg, aid)["memories"])


def test_obsidian_explicit_write_route_uses_discovered_vault(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as compat
    calls = []
    def proxy(cfg, method, path, query, body):
        calls.append((method, path))
        if path.endswith("/vaults"):
            return 200, [{"name": "isolated", "path": str(tmp_path)}]
        if method == "PUT":
            return None  # New note: the compatibility writer creates it.
        return 200, {"ok": True}
    monkeypatch.setattr(compat, "_obsidian_plugin_ready", lambda cfg: True)
    monkeypatch.setattr(compat, "_obsidian_proxy", proxy)
    monkeypatch.setattr(compat, "_audit", lambda *a, **k: None)
    code, receipt = compat.h_plugin_obsidian_note_put(None, {}, {"content": "# Isolated handoff\n"}, {"name": "agent-notes/receipt.md"})
    assert code == 200 and receipt["created"] is True
    assert (tmp_path / "agent-notes/receipt.md").read_text() == "# Isolated handoff\n"
    assert ("POST", "/api/plugins/obsidian-memory/refresh") in calls


def test_openviking_typed_submission_is_not_durable_office_receipt(monkeypatch):
    # Exercise the real provider implementation with transport replaced; no network.
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "qubit_runtime"))
    from plugins.memory.openviking import OpenVikingMemoryProvider
    from voicebridge.office import autowork, agent_memory
    calls = []
    def post(path, payload):
        calls.append((path, payload))
        return {"result": {"status": "accepted", "task_id": "isolated-task"}}
    provider = OpenVikingMemoryProvider.__new__(OpenVikingMemoryProvider)
    monkeypatch.setattr(provider, "_ensure_client", lambda: SimpleNamespace(post=post))
    monkeypatch.setattr(provider, "_user_space", lambda client: "isolated")
    receipt = json.loads(provider._tool_remember({"content": "isolated submission"}))
    assert receipt["status"] == "submitted"
    assert receipt["message_status"] == "accepted"
    assert receipt["task_id"] == "isolated-task"
    assert "stored" not in receipt and "memory_uri" not in receipt
    assert calls[0][0].endswith("/messages") and calls[1][0].endswith("/commit")
    monkeypatch.setattr(autowork, "_installed", lambda name: True)
    monkeypatch.setattr(agent_memory, "remember", lambda *a, **k: receipt)
    assert autowork._remember(None, 1, text="submission only") is False
