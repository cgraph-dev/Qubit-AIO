"""Hermetic helpers for the P8-02 security matrix.

No network, no live service, no credentials and no operator state: every test
builds a throwaway SQLite store under ``tmp_path`` and recording adapters.
This module is intentionally local to ``tests/phase8_security`` (it never
imports a conftest) so the matrix can run against production modules without
sharing another lane's fixtures.
"""
from __future__ import annotations

from typing import Any

from voicebridge.business import BusinessStore, Policy, ToolGateway
from voicebridge.business import policy as pol
from voicebridge.business import sandbox as sandbox_mod
from voicebridge.business.capability_runtime import CapabilityRuntime

NOW = 1_500_000.0
ISSUED = 1_000_000.0
EXPIRES = 2_000_000.0
SUBJECT = "agent:test"

READ = "p8.read"
WRITE = "p8.write"
MCP = "p8.mcp"
TERMINAL = "p8.terminal"
FILES = "p8.files"
BROWSER = "p8.browser"
MODEL = "p8.model"
MEMORY = "p8.memory"
RATE = "p8.rate"


def store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def scope(bos: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = bos.create_tenant(f"{name} Holdings")
    business = bos.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def catalog() -> pol.CapabilityCatalog:
    return pol.CapabilityCatalog(
        [
            pol.Capability(READ, "tool", "read", "read", risk="L1", version="1"),
            pol.Capability(
                WRITE,
                "tool",
                "write",
                "publish",
                risk="L4",
                approval_policy="required",
                budget_minor=1_000,
                version="1",
            ),
            pol.Capability(MCP, "mcp", "docs", "search", risk="L1", version="1"),
            pol.Capability(TERMINAL, "terminal", "shell", "run", risk="L1"),
            pol.Capability(FILES, "files", "fs", "read", risk="L0"),
            pol.Capability(BROWSER, "browser", "web", "fetch", risk="L1"),
            pol.Capability(MODEL, "model", "llm", "chat", risk="L1"),
            pol.Capability(MEMORY, "memory", "memory", "search", risk="L0"),
            pol.Capability(
                RATE, "tool", "rate", "read", risk="L1", rate_limit=2
            ),
        ]
    )


def grant(
    capability: str,
    resource: str,
    action: str,
    *,
    subject: str = SUBJECT,
    approval: str = "none",
    expires_at: float | None = None,
) -> pol.Grant:
    return pol.Grant(
        subject=subject,
        capability=capability,
        resource=resource,
        action=action,
        data_class="internal",
        environment="production",
        expires_at=expires_at,
        approval=approval,
    )


def snapshot(
    tenant_id: str,
    *,
    grants=(),
    denials=(),
    subject: str = SUBJECT,
    revision: str = "pol-1",
    issued_at: float = ISSUED,
    expires_at: float | None = EXPIRES,
):
    return pol.compile_snapshot(
        tenant_id=tenant_id,
        subject_id=subject,
        policy_revision=revision,
        grants=grants,
        denials=denials,
        issued_at=issued_at,
        expires_at=expires_at,
        catalog=catalog(),
    )


def policy_for(*tools: str) -> Policy:
    return Policy(allowed_tools=frozenset(tools), autonomy="L2")


def sandbox_profile(tmp_path, **kwargs) -> sandbox_mod.SandboxProfile:
    policy = sandbox_mod.SandboxPolicy(**kwargs)
    return sandbox_mod.SandboxProfile(tmp_path / "workspace", policy)


def runtime(
    tmp_path,
    bos: BusinessStore,
    *,
    adapters: dict[str, Any] | None = None,
    snap: pol.CapabilitySnapshot | None = None,
    sandbox: sandbox_mod.SandboxProfile | None = None,
    resolver=None,
) -> CapabilityRuntime:
    return CapabilityRuntime(
        bos,
        catalog(),
        snapshot=snap,
        sandbox=sandbox,
        adapters=adapters or {},
        resolver=resolver,
        clock=lambda: NOW,
    )


class Recorder:
    def __init__(self, result: Any = None) -> None:
        self.calls: list[Any] = []
        self.result = {"ok": True} if result is None else result

    def __call__(self, request, capability=None):
        self.calls.append((request, capability))
        return self.result

    @property
    def count(self) -> int:
        return len(self.calls)


def gateway(bos: BusinessStore, executor=None) -> ToolGateway:
    return ToolGateway(bos, executor)


def invoke(
    tool_gateway: ToolGateway,
    tenant,
    business,
    tool: str,
    *,
    policy: Policy | None = None,
    payload=None,
    key: str = "key-1",
    cost_minor: int = 0,
    dry_run: bool = False,
    approval_id: str | None = None,
    actor: str = "agent:p8",
):
    return tool_gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor=actor,
        tool=tool,
        idempotency_key=key,
        policy=policy or policy_for(tool),
        payload=payload,
        cost_minor=cost_minor,
        dry_run=dry_run,
        approval_id=approval_id,
    )
