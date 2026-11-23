"""PicoClaw workspace skills and MCP configuration conventions."""

from ..framework import ForeignAdapter


ADAPTER = ForeignAdapter(
    name="picoclaw",
    skill_roots=("~/.picoclaw/workspace/skills",),
    mcp_config_paths=("~/.picoclaw/config.json",),
    mcp_config_keys=("tools.mcp.servers",),
    mcp_enabled_key="tools.mcp.enabled",
)
