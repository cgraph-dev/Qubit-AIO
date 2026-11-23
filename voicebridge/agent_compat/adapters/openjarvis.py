"""OpenJarvis skill and MCP configuration conventions."""

from ..framework import ForeignAdapter


ADAPTER = ForeignAdapter(
    name="openjarvis",
    detection_markers=("openjarvis",),
    skill_roots=("~/.openjarvis/skills",),
    skill_pipeline_filename="skill.toml",
    mcp_config_paths=("~/.openjarvis/config.toml",),
    mcp_config_keys=("tools.mcp.servers",),
    mcp_enabled_key="tools.mcp.enabled",
    mcp_named_list=True,
)
