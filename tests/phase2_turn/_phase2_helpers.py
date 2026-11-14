"""Shared hermetic fixtures for the P2-06 agent-turn tests.

Everything runs against a throwaway SQLite file under ``tmp_path``. There is
no network, no live service, no credential and no operator home state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from voicebridge.business import (
    BusinessStore,
    identity,
    otel,
    policy,
    runs,
)
from voicebridge.business.tools import (
    ExecutorResult,
    ToolGateway,
)
from voicebridge.business.agent_turn import (
    ModelPortError,
    ModelReply,
    ModelTimeout,
    ToolCall,
    TurnOrchestrator,
    business_approval_decider,
    business_approval_requester,
)

NOW = 1_900_000_000.0
EXPIRES = NOW + 3_600.0

READ_TOOL = "office.records.read"
READ_RESOURCE = "record:demo"
READ_ACTION = "read"

WRITE_TOOL = "etsy.listing.publish"
WRITE_RESOURCE = "etsy.listing"
WRITE_ACTION = "publish"


def catalog() -> policy.CapabilityCatalog:
    return policy.CapabilityCatalog(
        [
            policy.Capability(
                capability_id=READ_TOOL,
                category="data",
                resource=READ_RESOURCE,
                action=READ_ACTION,
                risk="L1",
                required_scopes=("records:read",),
                health="healthy",
                version="1.0.0",
            ),
            policy.Capability(
                capability_id=WRITE_TOOL,
                category="external",
                resource=WRITE_RESOURCE,
                action=WRITE_ACTION,
                risk="L4",
                required_scopes=("etsy:write",),
                approval_policy="required",
                budget_minor=10_000,
                health="healthy",
                version="2.0.0",
            ),
        ]
    )


def grants(subject: str, *, write: bool = True) -> tuple[policy.Grant, ...]:
    rows = [
        policy.Grant(
            subject=subject,
            capability=READ_TOOL,
            resource=READ_RESOURCE,
            action=READ_ACTION,
            data_class="internal",
            environment="production",
        )
    ]
    if write:
        rows.append(
            policy.Grant(
                subject=subject,
                capability=WRITE_TOOL,
                resource=WRITE_RESOURCE,
                action=WRITE_ACTION,
                data_class="internal",
                environment="production",
                approval="required",
            )
        )
    return tuple(rows)


def snapshot(
    tenant_id: str,
    subject: str,
    *,
    write: bool = True,
    revision: str = "p2-1",
    issued_at: float = NOW,
    expires_at: float | None = EXPIRES,
    cat: policy.CapabilityCatalog | None = None,
) -> policy.CapabilitySnapshot:
    return policy.compile_snapshot(
        tenant_id=tenant_id,
        subject_id=subject,
        policy_revision=revision,
        grants=grants(subject, write=write),
        catalog=cat or catalog(),
        issued_at=issued_at,
        expires_at=expires_at,
    )


class FakeExecutor:
    """A deterministic read + receipted write executor."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.calls.append(request)
        if request.tool == READ_TOOL:
            return {"records": [{"id": "demo-1", "value": 42}]}
        if request.tool == WRITE_TOOL:
            payload = request.payload if isinstance(request.payload, dict) else {}
            return ExecutorResult(
                result={
                    "receipt": {
                        "provider": "fake-etsy",
                        "request_id": "fake-request-1",
                        "status": "accepted",
                        "listing": payload.get("listing", "unset"),
                    }
                },
                executed=True,
            )
        raise AssertionError(f"unexpected tool {request.tool!r}")

    @property
    def tools(self) -> list[str]:
        return [call.tool for call in self.calls]


class ScriptedModel:
    """A provider fake that replays a fixed list of replies."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def respond(self, messages, tools, context) -> ModelReply:
        self.calls.append(
            {
                "messages": [dict(item) for item in messages],
                "tools": list(tools),
                "context": dict(context),
            }
        )
        if not self.replies:
            raise ModelPortError("the scripted model has no reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply(messages, tools, context)
        return reply


@dataclass
class Scene:
    store: BusinessStore
    tenant: Any
    business: Any
    owner: Any
    agent: Any
    task: Any
    catalog: policy.CapabilityCatalog
    gateway: ToolGateway
    executor: FakeExecutor
    tracer: otel.Tracer


def scene(tmp_path, *, write: bool = True, executor=None) -> Scene:
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("P2 Tenant")
    business = store.create_business(tenant.tenant_id, "P2 Business")
    owner = identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref="owner:p2"
    )
    agent = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref="agent:p2"
    )
    cat = catalog()
    task = runs.create_task(
        store,
        tenant.tenant_id,
        business.business_id,
        "P2-06 demo turn",
        objective="Read one record and publish one listing",
        owner_principal=owner.principal_id,
        assignee_principal=agent.principal_id,
        required_capabilities=[READ_TOOL, WRITE_TOOL],
        data_class="internal",
        actor=owner.principal_id,
        now=NOW,
    )
    fake = executor or FakeExecutor()
    gateway = ToolGateway(store, executor=fake)
    tracer = otel.Tracer(
        exporter=otel.InMemoryExporter(),
        capture_content=False,
        sampling=otel.SamplingPolicy(baseline_rate=1.0),
        clock=lambda: NOW,
    )
    return Scene(
        store=store,
        tenant=tenant,
        business=business,
        owner=owner,
        agent=agent,
        task=task,
        catalog=cat,
        gateway=gateway,
        executor=fake,
        tracer=tracer,
    )


def orchestrator(
    scene: Scene,
    model: Any,
    *,
    run_key: str = "turn-1",
    write: bool = True,
    revision: str = "p2-1",
    issued_at: float = NOW,
    expires_at: float | None = EXPIRES,
    approval_requester=None,
    approval_decider=None,
    **kwargs: Any,
) -> TurnOrchestrator:
    snap = snapshot(
        scene.tenant.tenant_id,
        scene.agent.principal_id,
        write=write,
        revision=revision,
        issued_at=issued_at,
        expires_at=expires_at,
        cat=scene.catalog,
    )
    requester = approval_requester
    decider = approval_decider
    if requester is None:
        requester = business_approval_requester(
            scene.store,
            scene.tenant.tenant_id,
            scene.business.business_id,
            requested_by=scene.agent.principal_id,
            run_id=run_key,
            now=NOW,
        )
    if decider is None:
        decider = business_approval_decider(
            scene.store,
            scene.tenant.tenant_id,
            decided_by=scene.owner.principal_id,
            now=NOW,
        )
    return TurnOrchestrator(
        store=scene.store,
        tenant_id=scene.tenant.tenant_id,
        business_id=scene.business.business_id,
        agent_id=scene.agent.principal_id,
        subject_id=scene.agent.principal_id,
        task_id=scene.task.task_id,
        session_id="session:p2",
        snapshot=snap,
        catalog=scene.catalog,
        gateway=scene.gateway,
        model=model,
        objective="Read one record and publish one listing",
        owner_id=scene.owner.principal_id,
        run_key=run_key,
        approval_requester=requester,
        approval_decider=decider,
        tracer=scene.tracer,
        clock=lambda: NOW,
        **kwargs,
    )


def read_call(value: str = "demo-1") -> ToolCall:
    return ToolCall(tool=READ_TOOL, arguments={"id": value}, call_id="call-read")


def write_call(listing: str = "p2-listing") -> ToolCall:
    return ToolCall(
        tool=WRITE_TOOL,
        arguments={"listing": listing},
        call_id="call-write",
    )


def reply_tools(*calls: ToolCall, text: str = "") -> ModelReply:
    return ModelReply(text=text, tool_calls=tuple(calls))


def reply_text(text: str) -> ModelReply:
    return ModelReply(text=text)


def timeout_reply(message: str = "provider timed out") -> ModelTimeout:
    return ModelTimeout(message)
