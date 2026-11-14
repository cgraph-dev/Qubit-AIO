#!/usr/bin/env python3
"""P2-06 — hermetic agent-turn demo over the P1 business contracts.

Runs four turns against a throwaway Business OS database:

* a read + approval-bound write that completes with an artifact, receipt,
  checkpoints, events and one OpenTelemetry trace;
* a denied write that leaves no artifact, receipt or ledger row;
* a provider timeout that stays ``unknown`` until an explicit reconciliation
  resumes it from the validated checkpoint;
* a duplicate tool call that executes once.

No live service, credential, operator home or network is used.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import (  # noqa: E402
    BusinessStore,
    approvals,
    durable,
    identity,
    jobs,
    ledger,
    otel,
    policy,
    runs,
)
from voicebridge.business.agent_turn import (  # noqa: E402
    COMPLETED,
    FAILED,
    UNKNOWN,
    ModelReply,
    ModelTimeout,
    ToolCall,
    TurnOrchestrator,
    business_approval_decider,
    business_approval_requester,
)
from voicebridge.business.tools import (  # noqa: E402
    ExecutorResult,
    ToolGateway,
)

NOW = 1_900_000_000.0
READ_TOOL = "office.records.read"
READ_RESOURCE = "record:demo"
READ_ACTION = "read"
WRITE_TOOL = "etsy.listing.publish"
WRITE_RESOURCE = "etsy.listing"
WRITE_ACTION = "publish"


def iso(moment: float) -> str:
    return (
        _dt.datetime.fromtimestamp(moment, _dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@contextlib.contextmanager
def network_block():
    original_socket = socket.socket
    original_create = socket.create_connection

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("QOFFICE_PHASE2_NETWORK_BLOCKED")

    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.create_connection = original_create  # type: ignore[assignment]


class FakeExecutor:
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
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def respond(self, messages, tools, context) -> ModelReply:
        self.calls += 1
        if not self.replies:
            raise RuntimeError("the scripted model has no reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


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


def grants(subject: str, *, write: bool) -> tuple[policy.Grant, ...]:
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
    cat: policy.CapabilityCatalog,
    *,
    write: bool,
    revision: str = "p2-demo",
    expires_at: float | None = NOW + 3600,
) -> policy.CapabilitySnapshot:
    return policy.compile_snapshot(
        tenant_id=tenant_id,
        subject_id=subject,
        policy_revision=revision,
        grants=grants(subject, write=write),
        catalog=cat,
        issued_at=NOW,
        expires_at=expires_at,
    )


class Scene:
    def __init__(self, work_root: Path) -> None:
        self.store = BusinessStore(work_root / "business.db")
        self.tenant = self.store.create_tenant("P2-06 Demo Tenant")
        self.business = self.store.create_business(
            self.tenant.tenant_id, "P2-06 Demo Business"
        )
        self.owner = identity.create_principal(
            self.store, self.tenant.tenant_id, kind="owner",
            external_ref="owner:p2-demo",
        )
        self.agent = identity.create_principal(
            self.store, self.tenant.tenant_id, kind="agent",
            external_ref="agent:p2-demo",
        )
        self.catalog = catalog()
        self.task = runs.create_task(
            self.store,
            self.tenant.tenant_id,
            self.business.business_id,
            "P2-06 demo turn",
            objective="Read one record and publish one listing",
            owner_principal=self.owner.principal_id,
            assignee_principal=self.agent.principal_id,
            required_capabilities=[READ_TOOL, WRITE_TOOL],
            data_class="internal",
            actor=self.owner.principal_id,
            now=NOW,
        )
        self.executor = FakeExecutor()
        self.gateway = ToolGateway(self.store, executor=self.executor)
        self.tracer = otel.Tracer(
            exporter=otel.InMemoryExporter(),
            capture_content=False,
            sampling=otel.SamplingPolicy(baseline_rate=1.0),
            clock=lambda: NOW,
        )

    def orchestrator(
        self,
        model: Any,
        *,
        run_key: str,
        write: bool = True,
        expires_at: float | None = NOW + 3600,
        approval_requester=None,
        approval_decider=None,
    ) -> TurnOrchestrator:
        snap = snapshot(
            self.tenant.tenant_id,
            self.agent.principal_id,
            self.catalog,
            write=write,
            expires_at=expires_at,
        )
        return TurnOrchestrator(
            store=self.store,
            tenant_id=self.tenant.tenant_id,
            business_id=self.business.business_id,
            agent_id=self.agent.principal_id,
            subject_id=self.agent.principal_id,
            task_id=self.task.task_id,
            session_id="session:p2-demo",
            snapshot=snap,
            catalog=self.catalog,
            gateway=self.gateway,
            model=model,
            objective="Read one record and publish one listing",
            owner_id=self.owner.principal_id,
            run_key=run_key,
            approval_requester=approval_requester
            or business_approval_requester(
                self.store,
                self.tenant.tenant_id,
                self.business.business_id,
                requested_by=self.agent.principal_id,
                run_id=run_key,
                now=NOW,
            ),
            approval_decider=approval_decider
            or business_approval_decider(
                self.store,
                self.tenant.tenant_id,
                decided_by=self.owner.principal_id,
                now=NOW,
            ),
            tracer=self.tracer,
            clock=lambda: NOW,
        )


def read_call(value: str = "demo-1") -> ToolCall:
    return ToolCall(tool=READ_TOOL, arguments={"id": value}, call_id="read")


def write_call(listing: str = "demo-listing") -> ToolCall:
    return ToolCall(
        tool=WRITE_TOOL, arguments={"listing": listing}, call_id="write"
    )


def run_demo(work_root: Path) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    scene = Scene(work_root)

    success = scene.orchestrator(
        ScriptedModel(
            [
                ModelReply(tool_calls=(read_call(),)),
                ModelReply(tool_calls=(write_call(),)),
                ModelReply(text="Published the listing."),
            ]
        ),
        run_key="demo-success",
    ).execute()

    denial = scene.orchestrator(
        ScriptedModel(
            [
                ModelReply(tool_calls=(write_call("denied"),)),
                ModelReply(text="unreachable"),
            ]
        ),
        run_key="demo-denial",
        write=False,
    ).execute()

    unknown = scene.orchestrator(
        ScriptedModel([ModelTimeout("provider did not answer")]),
        run_key="demo-timeout",
    ).execute()
    unknown_job_status = jobs.get(
        scene.store, scene.tenant.tenant_id, unknown.run_id
    ).status
    resumed = scene.orchestrator(
        ScriptedModel([ModelReply(text="Finished after reconciliation.")]),
        run_key="demo-timeout",
    ).resume(reconcile_unknown=True)

    duplicate = scene.orchestrator(
        ScriptedModel(
            [
                ModelReply(tool_calls=(read_call("same"), read_call("same"))),
                ModelReply(text="done"),
            ]
        ),
        run_key="demo-dedupe",
    ).execute()

    artifacts = scene.store.list_artifacts(scene.tenant.tenant_id)
    ledger_rows = ledger.list_entries(
        scene.store,
        scene.tenant.tenant_id,
        business_id=scene.business.business_id,
    )
    denial_job = jobs.get(
        scene.store, scene.tenant.tenant_id, denial.run_id
    )
    trace = success.trace or {}
    span_names = sorted({span.name for span in trace.get("spans", [])})
    approval_rows = approvals.list_approvals(
        scene.store, scene.tenant.tenant_id
    )

    checks = {
        "success_completed": success.status == COMPLETED,
        "success_receipt": bool(
            success.receipt
            and success.receipt.get("request_id") == "fake-request-1"
        ),
        "success_artifact": bool(success.artifact_id),
        "success_checkpoints": success.checkpoints == (1, 2, 3, 4, 5, 6),
        "success_events": "agent.turn.completed" in success.events,
        "success_trace": {"invoke_agent", "chat", "execute_tool",
                          "approval"} <= set(span_names),
        "approval_approved": any(
            row.status == "approved" for row in approval_rows
        ),
        "denial_failed": denial.status == FAILED,
        "denial_no_artifact": denial.artifact_id is None and len(artifacts) == 3,
        "denial_no_receipt": denial.receipt is None,
        "denial_job_failed": denial_job.status == "failed",
        "timeout_unknown": unknown.status == UNKNOWN,
        "timeout_job_unknown": unknown_job_status == "unknown",
        "resume_completed": resumed.status == COMPLETED,
        "resume_same_run": resumed.run_id == unknown.run_id,
        "resume_no_duplicate_read": scene.executor.tools.count(READ_TOOL) == 2,
        "dedupe_executes_once": duplicate.status == COMPLETED
        and duplicate.tool_calls == 2
        and "agent.turn.tool.deduped" in duplicate.events,
        "no_ledger_row": ledger_rows == [],
    }
    return {
        "schema": "qoffice-phase2-agent-turn/1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "generated_at": iso(NOW),
        "tenant": scene.tenant.tenant_id,
        "business": scene.business.business_id,
        "task": scene.task.task_id,
        "principal": {
            "owner": scene.owner.principal_id,
            "agent": scene.agent.principal_id,
        },
        "checks": checks,
        "success": {
            "run_id": success.run_id,
            "text": success.text,
            "artifact_id": success.artifact_id,
            "artifact_sha256": success.artifact_sha256,
            "receipt": success.receipt,
            "checkpoints": list(success.checkpoints),
            "events": list(success.events),
            "tool_calls": success.tool_calls,
            "steps": success.steps,
            "trace_spans": span_names,
            "trace_count": len(trace.get("traces", [])),
            "capture_content": all(
                span.capture_content is False
                for span in trace.get("spans", [])
            ),
        },
        "denial": {
            "run_id": denial.run_id,
            "status": denial.status,
            "error_code": denial.error_code,
            "job_status": denial_job.status,
            "artifact_id": denial.artifact_id,
        },
        "timeout": {
            "run_id": unknown.run_id,
            "initial_status": unknown.status,
            "error_code": unknown.error_code,
            "job_status": unknown_job_status,
            "resumed_status": resumed.status,
            "resumed_checkpoints": list(resumed.checkpoints),
        },
        "dedupe": {
            "run_id": duplicate.run_id,
            "tool_calls": duplicate.tool_calls,
            "events": list(duplicate.events),
        },
        "artifacts": [
            {
                "artifact_id": row.artifact_id,
                "kind": row.kind,
                "sha256": row.sha256,
                "name": row.name,
            }
            for row in artifacts
        ],
        "approvals": [
            {
                "approval_id": row.approval_id,
                "action": row.action,
                "status": row.status,
            }
            for row in approval_rows
        ],
        "checkpoints": {
            key: [row.sequence for row in durable.list_checkpoints(
                scene.store, scene.tenant.tenant_id, key
            )]
            for key in (
                success.run_id,
                unknown.run_id,
                duplicate.run_id,
            )
        },
        "network_blocked": True,
        "db_path": str(work_root / "business.db"),
    }


def _guard_home(work_root: Path) -> None:
    live = Path(os.environ.get("QUBIT_HOME", "")).expanduser() if os.environ.get(
        "QUBIT_HOME"
    ) else None
    if live is not None and live.exists() and (live / "state.db").exists():
        raise SystemExit(
            "phase2_agent_turn: refusing to run against a live QUBIT_HOME"
        )
    if work_root.exists() and any(work_root.iterdir()):
        raise SystemExit(
            "phase2_agent_turn: --work-root must be empty or absent"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    base = Path(args.work_root) if args.work_root else Path(
        tempfile.mkdtemp(prefix="qoffice-phase2-")
    )
    try:
        _guard_home(base)
        with network_block():
            report = run_demo(base)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"phase2_agent_turn: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    finally:
        if not args.keep and args.work_root is None:
            shutil.rmtree(base, ignore_errors=True)

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        print(text)
    else:
        print(f"phase2_agent_turn: {report['status']}")
        print(f"  run={report['success']['run_id']}")
        print(f"  artifact={report['success']['artifact_id']}")
        print(f"  denial={report['denial']['error_code']}")
        print(f"  timeout={report['timeout']['initial_status']}"
              f" -> {report['timeout']['resumed_status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
