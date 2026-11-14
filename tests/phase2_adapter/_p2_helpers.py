"""Shared hermetic fixtures for the Phase 2 agent-adapter tests.

No network, no live service, no store unless a test explicitly creates one.
"""
from __future__ import annotations

from voicebridge.business import agent_adapter as aa
from voicebridge.business import dynamic_catalog as dc

TENANT = "ten_p2"
SUBJECT = "agent:p2"
POLICY_REVISION = "pol-p2"
ISSUED = 1_500_000.0
EXPIRES = 1_600_000.0

ROUTE = aa.ModelRoute(
    provider="opencode-go", model="deepseek-v4.1-flash", reasoning="medium"
)
FALLBACK = (aa.ModelRoute(provider="local", model="qubit-brain"),)


def fake(*, engine: str = "fake", **kwargs):
    kwargs.setdefault("response", "hello")
    return aa.FakeBackend(engine=engine, **kwargs)


def registry(*backends, **kwargs) -> aa.AgentAdapterRegistry:
    reg = aa.AgentAdapterRegistry()
    for backend in backends or (fake(),):
        reg.register(backend, **kwargs)
    return reg


def turn(prompt: str = "do the thing", **overrides) -> aa.TurnInput:
    values = {
        "prompt": prompt,
        "session_id": "ses_p2",
        "tenant_id": TENANT,
        "subject_id": SUBJECT,
        "run_id": "run_p2",
    }
    values.update(overrides)
    return aa.TurnInput(**values)


def mcp_server(**overrides):
    row = {
        "name": "docs",
        "version": "2.4.0",
        "health": "healthy",
        "availability": "available",
        "allowlist": ["search"],
        "tools": [
            {
                "name": "search",
                "inputSchema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"hits": {"type": "array"}},
                },
            },
            {
                "name": "write",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
            },
        ],
    }
    row.update(overrides)
    return row


def tool_records(version: str = "2.1.0"):
    return (
        dc.tool_capability("search", version=version),
        dc.tool_capability(
            "read",
            version="1.0.0",
            risk="L1",
            required_scopes=("files:read",),
        ),
    )


def all_sources(*, tool_version: str = "2.1.0"):
    """One injected source per catalog domain, each a pure function."""
    return (
        dc.FunctionCatalogSource(
            "tools", lambda: tool_records(tool_version)
        ),
        dc.FunctionCatalogSource(
            "mcp", lambda: dc.mcp_server_capabilities(mcp_server())
        ),
        dc.FunctionCatalogSource(
            "plugins",
            lambda: (dc.plugin_capability("etsy", version="0.9.0"),),
        ),
        dc.FunctionCatalogSource(
            "connectors",
            lambda: (dc.connector_capability("gmail", version="1.3.0"),),
        ),
        dc.FunctionCatalogSource(
            "model_providers",
            lambda: (
                dc.model_provider_capability(
                    "opencode-go",
                    model="deepseek-v4.1-flash",
                    version="1.0.0",
                ),
            ),
        ),
        dc.FunctionCatalogSource(
            "skills",
            lambda: (),
            requirement_provider=lambda: (
                dc.skill_requirement(
                    "research",
                    version="1.2.0",
                    required_capabilities=("tool:search",),
                ),
            ),
        ),
    )


def catalog(*sources, **kwargs) -> dc.DynamicCapabilityCatalog:
    return dc.DynamicCapabilityCatalog(
        sources or all_sources(), **kwargs
    )
