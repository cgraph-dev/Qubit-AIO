"""Unified slash catalog for /qubit-chat (GET /api/chat/slash-commands).

The ``/chat`` terminal resolves its slash palette through the canonical
``qubit_cli.commands.COMMAND_REGISTRY`` plus the gateway
``GatewaySlashCommandsMixin`` family; the voicebridge chat path had no
equivalent catalog endpoint. This drives the real compat dispatcher
(:func:`voicebridge.qubit_compat.dispatch` — the exact function
``voicebridge.webui`` calls) with a hermetic ``Config`` and asserts the
terminal-known commands appear, plus one aggregated entry per source
(skill / plugin / mcp / toolset) when that source is present.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_route_registered_and_reaches_handler():
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("GET", "/api/chat/slash-commands")
    assert route is not None
    assert route["handler"] == "h_chat_slash_commands"
    assert "h_chat_slash_commands" in HC.HANDLERS
    # Read-only: no write verb may resolve to the catalog.
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert HC.match(method, "/api/chat/slash-commands")[0] is None


def test_terminal_known_commands_appear(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/chat/slash-commands", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert isinstance(out["commands"], list) and out["commands"]
    for entry in out["commands"]:
        assert {"name", "description", "source"} <= set(entry.keys()), entry
    names = {c["name"] for c in out["commands"]}
    # Terminal-known commands from the canonical registry / gateway mixins.
    assert "/model" in names
    assert "/skills" in names
    assert "/help" in names


def test_aggregates_skill_plugin_mcp_toolset(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    from voicebridge import skill_catalog
    monkeypatch.setattr(
        skill_catalog, "catalog_entries",
        lambda cfg: [{"name": "demo-skill",
                      "description": "Does demo things"}])
    monkeypatch.setattr(
        HC, "h_plugins",
        lambda cfg, q, b, p: (200, [{"name": "demo-plugin",
                                    "description": "Demo plugin"}]))

    from voicebridge import mcp_servers as MS
    monkeypatch.setattr(
        MS, "list_servers",
        lambda cfg: {"servers": [{"name": "demo-mcp",
                                 "transport": "stdio"}]})

    from voicebridge import toolsets as TS
    monkeypatch.setattr(TS, "toolset_states",
                        lambda cfg: {"demo-toolset": True})

    code, out = HC.dispatch(cfg, "GET", "/api/chat/slash-commands", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    by_name = {c["name"]: c for c in out["commands"]}
    assert by_name["/skill-demo-skill"]["source"] == "skill"
    assert "demo" in by_name["/skill-demo-skill"]["description"].lower()
    assert by_name["/plugin-demo-plugin"]["source"] == "plugin"
    assert by_name["/mcp-demo-mcp"]["source"] == "mcp"
    assert by_name["/toolset-demo-toolset"]["source"] == "toolset"
    # The terminal registry still contributes alongside the aggregations.
    assert "/model" in by_name
