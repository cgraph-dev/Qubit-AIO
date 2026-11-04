"""Fresh-install configuration guidance names OAuth explicitly.

A remote OAuth MCP server (Airtable, Notion, …) needs both the ``auth: oauth``
marker and a one-time ``qubit mcp login <name>``. The shipped config example
must show both so a user does not copy a URL-only entry that can only ever 401.
The Airtable catalog manifest is the reviewed source for its own transport, so
it must carry the marker and the Qubit-branded login command.
"""

from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = RUNTIME_ROOT / "cli-config.yaml.example"


def test_example_documents_the_oauth_marker_and_login_command():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "auth: oauth" in text
    assert "qubit mcp login <name>" in text

    # The Notion sample is a hosted OAuth server, not an API-key server.
    block = text.split("notion:", 1)[1].split("github:", 1)[0]
    assert "url: https://mcp.notion.com/mcp" in block
    assert "auth: oauth" in block


def test_shipped_airtable_manifest_is_an_oauth_entry_with_qubit_guidance():
    from qubit_cli import mcp_catalog

    entry = mcp_catalog.get_entry("airtable")
    assert entry is not None, "the reviewed Airtable catalog entry is missing"
    assert entry.auth.type == "oauth"
    cfg = mcp_catalog._build_server_config(entry, None)
    assert cfg == {"url": "https://mcp.airtable.com/mcp", "auth": "oauth"}

    guidance = entry.post_install.lower()
    assert "qubit mcp login airtable" in guidance
    assert "hermes mcp login" not in guidance
