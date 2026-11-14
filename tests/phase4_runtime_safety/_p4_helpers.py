"""Hermetic fixtures for the Phase 4 runtime + safety tests.

No network, no live service, no credentials: a throwaway SQLite store under
``tmp_path``, recording adapters and a fixed clock.
"""
from __future__ import annotations

import copy
from typing import Any

from voicebridge.business import BusinessStore
from voicebridge.business import policy as pol
from voicebridge.business import sandbox as sandbox_mod
from voicebridge.business.capability_runtime import CapabilityRuntime
from voicebridge.business.tools import Policy

NOW = 1_500_000.0
ISSUED = 1_000_000.0
EXPIRES = 2_000_000.0
SUBJECT = "agent:test"

READ = "tool:read"
WRITE = "tool:publish"
CONNECTOR = "connectors:gmail"
TERMINAL = "terminal:run"
BROWSER = "browser:fetch"
FILES = "files:read"
MESSAGES = "messages:send"
MEMORY = "memory:search"
MCP = "mcp:docs"
MODEL = "model:chat"
UNSUPPORTED = "payments:charge"


def store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def scope(bos: BusinessStore, *, name: str = "Acme", slug: str = "acme"):
    tenant = bos.create_tenant(f"{name} Holdings")
    business = bos.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def catalog() -> pol.CapabilityCatalog:
    return pol.CapabilityCatalog(
        [
            pol.Capability(READ, "tool", "shop", "read", risk="L1", version="1"),
            pol.Capability(
                WRITE,
                "tool",
                "listing",
                "publish",
                risk="L4",
                approval_policy="required",
                budget_minor=100_000,
                version="1",
            ),
            pol.Capability(CONNECTOR, "connectors", "gmail", "send", risk="L2"),
            pol.Capability(TERMINAL, "terminal", "shell", "run", risk="L1"),
            pol.Capability(BROWSER, "browser", "web", "fetch", risk="L1"),
            pol.Capability(FILES, "files", "fs", "read", risk="L0"),
            pol.Capability(MESSAGES, "messages", "chat", "send", risk="L2"),
            pol.Capability(MEMORY, "memory", "memory", "search", risk="L0"),
            pol.Capability(MCP, "mcp", "docs", "search", risk="L1"),
            pol.Capability(MODEL, "model", "llm", "chat", risk="L1"),
            pol.Capability(UNSUPPORTED, "payments", "stripe", "charge", risk="L5"),
        ]
    )


def grant(
    capability: str,
    *,
    subject: str = SUBJECT,
    approval: str = "none",
    expires_at: float | None = None,
) -> pol.Grant:
    cap = catalog().get(capability)
    return pol.Grant(
        subject=subject,
        capability=cap.capability_id,
        resource=cap.resource,
        action=cap.action,
        data_class="internal",
        environment="production",
        expires_at=expires_at,
        approval=approval,
    )


def snapshot(tenant_id: str, *, capabilities=(READ,), denials=(), subject=SUBJECT):
    grants = tuple(
        grant(capability, subject=subject) for capability in capabilities
    )
    return pol.compile_snapshot(
        tenant_id=tenant_id,
        subject_id=subject,
        policy_revision="pol-p4",
        grants=grants,
        denials=denials,
        issued_at=ISSUED,
        expires_at=EXPIRES,
        catalog=catalog(),
    )


def policy(*tools: str) -> Policy:
    return Policy(allowed_tools=frozenset(tools), autonomy="L2")


def sandbox(tmp_path, **kwargs) -> sandbox_mod.SandboxProfile:
    kwargs.setdefault("allow_network", True)
    kwargs.setdefault("egress_allowlist", ("api.example.com",))
    kwargs.setdefault("command_allowlist", ("git",))
    profile = sandbox_mod.SandboxProfile(
        tmp_path / "workspace", sandbox_mod.SandboxPolicy(**kwargs)
    )
    profile.workspace.mkdir(parents=True, exist_ok=True)
    return profile


class Recorder:
    def __init__(self, result: Any = None) -> None:
        self.calls: list[tuple[Any, Any]] = []
        self.result = {"ok": True} if result is None else result

    def __call__(self, request, capability):
        self.calls.append((request, capability))
        return copy.deepcopy(self.result)


def runtime(
    tmp_path,
    bos: BusinessStore,
    *,
    capabilities=(READ,),
    adapters: dict[str, Recorder] | None = None,
    snap=None,
    sandbox_profile=None,
    resolver=None,
) -> CapabilityRuntime:
    if adapters is None:
        adapters = {name: Recorder({"value": name}) for name in capabilities}
    rt = CapabilityRuntime(
        bos,
        catalog(),
        snapshot=snap,
        sandbox=sandbox_profile if sandbox_profile is not None else sandbox(tmp_path),
        adapters=adapters,
        resolver=resolver,
        clock=lambda: NOW,
    )
    return rt


def resolver(mapping):
    def resolve(host, port):
        return mapping.get(host, [])

    return resolve
