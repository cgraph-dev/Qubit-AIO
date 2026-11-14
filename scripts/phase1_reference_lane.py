#!/usr/bin/env python3
"""P1-10 — hermetic reference execution lane.

Owner -> authenticated session -> scoped context -> tool read -> approval-gated
fake external write -> artifact/provider receipt -> checkpoint -> restart and
reconciliation, with one OpenTelemetry GenAI trace. Everything runs against a
throwaway Business OS database; no live service, credential or network is used.
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
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import (  # noqa: E402
    a2a,
    approvals,
    durable,
    eventlog,
    identity,
    jobs,
    ledger,
    otel,
    policy,
    runs,
)
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.store import BusinessStore  # noqa: E402
from voicebridge.business.tools import (  # noqa: E402
    ExecutorResult,
    Policy,
    ToolGateway,
    ToolRequest,
)

NOW = 1_750_000_000.0
TENANT_NAME = "Phase 1 Reference Tenant"
BUSINESS_NAME = "Reference Department"


def iso(moment: float) -> str:
    return (
        _dt.datetime.fromtimestamp(moment, _dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@contextlib.contextmanager
def network_block():
    """Fail any socket connect/creation for the duration of the lane."""
    original_socket = socket.socket
    original_create = socket.create_connection

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("QOFFICE_PHASE1_NETWORK_BLOCKED")

    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.create_connection = original_create  # type: ignore[assignment]


class BusinessEventLog:
    """A2A EventLog adapter over the authoritative Business OS event log."""

    def __init__(self, store: BusinessStore, tenant_id: str, business_id: str,
                 now: float) -> None:
        self._store = store
        self._tenant = tenant_id
        self._business = business_id
        self._now = now

    def append(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = dict(record)
        kind = str(payload.get("record_type") or "record")
        thread = str(payload.get("thread_id") or "a2a")
        key = str(
            payload.get("idempotency_key")
            or f"a2a:{kind}:{payload.get('message_id')}:"
            f"{payload.get('delivery_state')}:{payload.get('at')}"
        )
        envelope = eventlog.append_event(
            self._store,
            self._tenant,
            thread,
            f"a2a.{kind}",
            payload,
            actor=str(payload.get("actor") or "system"),
            idempotency_key=f"a2a:{key}",
            business_id=self._business,
            now=self._now,
        )
        stored = dict(payload)
        stored["event_id"] = envelope.event_id
        stored["sequence"] = envelope.sequence
        return stored

    def read(self, tenant_id: str, *, thread_id: str | None = None
             ) -> Sequence[Mapping[str, Any]]:
        envelopes = eventlog.list_events(self._store, tenant_id, limit=10_000)
        rows: list[dict[str, Any]] = []
        for envelope in envelopes:
            if not envelope.type.startswith("a2a."):
                continue
            if thread_id is not None and envelope.aggregate_id != thread_id:
                continue
            payload = dict(envelope.payload)
            payload["event_id"] = envelope.event_id
            payload["sequence"] = envelope.sequence
            rows.append(payload)
        rows.sort(key=lambda item: (str(item.get("thread_id") or ""),
                                    int(item.get("sequence") or 0)))
        return rows


def fake_executor(request: ToolRequest) -> Any:
    if request.tool == "read.records":
        return {"records": [{"id": "synthetic-42", "value": 42}],
                "scopes": ["internal"]}
    if request.tool == "write.provider":
        payload = request.payload if isinstance(request.payload, Mapping) else {}
        receipt = {
            "provider": "fake-etsy",
            "request_id": "fake-provider-request-1",
            "status": "accepted",
            "listing": payload.get("listing", "unset"),
        }
        return ExecutorResult(result={"receipt": receipt}, executed=True)
    raise BusinessError("TOOL_NOT_ALLOWED", f"unexpected fake tool {request.tool!r}")


def run_lane(work_root: Path, *, now: float = NOW) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    db_path = work_root / "business.db"
    store = BusinessStore(db_path)
    tenant = store.create_tenant(TENANT_NAME)
    tid = tenant.tenant_id
    business = store.create_business(tid, BUSINESS_NAME, currency="USD")
    bid = business.business_id

    owner = identity.create_principal(store, tid, kind="owner",
                                      external_ref="owner:reference")
    agent = identity.create_principal(store, tid, kind="agent",
                                      external_ref="agent:reference")
    for subject_id in (owner.principal_id, agent.principal_id):
        identity.grant(store, tid, subject_id=subject_id,
                       capability="read.records",
                       resource="record:synthetic-42", action="read",
                       data_class="internal", environment="staging")
        identity.grant(store, tid, subject_id=subject_id,
                       capability="write.provider",
                       resource="provider:fake-etsy", action="publish",
                       data_class="internal", environment="staging",
                       approval_required=True)

    token, auth_session = identity.issue_session_token(
        store, tid, owner.principal_id, audience="phase1-reference", now=now)
    verified = identity.verify_session_token(
        store, token, audience="phase1-reference", tenant_id=tid, now=now + 1)
    wrong_audience = False
    try:
        identity.verify_session_token(store, token, audience="other",
                                      now=now + 1)
    except BusinessError:
        wrong_audience = True

    catalog = policy.CapabilityCatalog([
        policy.Capability(
            capability_id="read.records", category="data",
            resource="record:synthetic-42", action="read", risk="L1",
            version="1"),
        policy.Capability(
            capability_id="write.provider", category="external",
            resource="provider:fake-etsy", action="publish", risk="L4",
            required_scopes=("provider.write",), approval_policy="required",
            version="1"),
    ])
    grants = []
    for row in identity.list_grants(store, tid, subject_id=agent.principal_id):
        grants.append(policy.Grant(
            subject=agent.principal_id, capability=row.capability,
            resource=row.resource, action=row.action,
            data_class=row.data_class, environment=row.environment,
            expires_at=row.expires_at,
            approval="required" if row.approval_required else "none"))
    snapshot = policy.compile_snapshot(
        tenant_id=tid, subject_id=agent.principal_id,
        policy_revision="p1-reference", grants=grants, catalog=catalog,
        issued_at=now, expires_at=now + 3600)

    task = runs.create_task(
        store, tid, bid, "Publish one reference listing",
        objective="Prove the reference execution boundary",
        owner_principal=owner.principal_id,
        assignee_principal=agent.principal_id,
        required_capabilities=["read.records", "write.provider"],
        data_class="internal", actor=owner.principal_id, now=now)
    task = runs.claim_task(store, tid, task.task_id,
                           principal=agent.principal_id, now=now + 1)
    workspace = runs.create_workspace(
        store, tid, task_id=task.task_id,
        session_id=auth_session.auth_session_id,
        actor=agent.principal_id, now=now + 1)

    session_event = eventlog.append_event(
        store, tid, f"session:{auth_session.auth_session_id}",
        "session.opened",
        {"owner": owner.principal_id, "agent": agent.principal_id,
         "memory_scopes": ["office/reference"]},
        actor=owner.principal_id, idempotency_key="ref-session-opened",
        business_id=bid, now=now + 1)

    log = BusinessEventLog(store, tid, bid, now + 2)
    message, created = a2a.send(
        log, tenant_id=tid, sender_principal=owner.principal_id,
        recipient_principal=agent.principal_id,
        payload={"task_id": task.task_id, "brief": "publish the reference item"},
        idempotency_key="ref-a2a-1", kind="task",
        capability_subset=["read.records", "write.provider"],
        task_id=task.task_id, correlation_id="ref-thread",
        verify_sender=lambda principal: principal in {
            owner.principal_id, agent.principal_id},
        can_delegate=lambda principal, capability: (
            principal == owner.principal_id
            and identity.evaluate(
                store, tid, principal, capability,
                "record:synthetic-42" if capability == "read.records"
                else "provider:fake-etsy",
                "read" if capability == "read.records" else "publish",
                data_class="internal", environment="staging",
                now=now + 2).allowed
        ),
        now=now + 2)
    received = a2a.receive(log, tenant_id=tid, principal=agent.principal_id,
                           message_id=message.message_id, now=now + 2)
    acked = a2a.ack(log, tenant_id=tid, principal=agent.principal_id,
                    message_id=message.message_id, now=now + 2)

    gateway = ToolGateway(store, executor=fake_executor)
    read_policy = Policy(allowed_tools=frozenset({"read.records"}),
                         autonomy="L1", policy_version="p1-reference")
    read = gateway.invoke(
        tenant_id=tid, business_id=bid, actor=agent.principal_id,
        tool="read.records", idempotency_key="ref-read-1",
        policy=read_policy, payload={"query": "synthetic-42"},
        snapshot=snapshot, catalog=catalog,
        subject_id=agent.principal_id, resource="record:synthetic-42",
        action="read", data_class="internal", environment="staging",
        now=now + 3)

    job, _ = jobs.create(
        store, tenant_id=tid, business_id=bid, kind="provider.publish",
        idempotency_key="ref-write-job", input={"listing": "reference"},
        actor=agent.principal_id)
    jobs.enqueue(store, tid, job.job_id, actor=agent.principal_id)
    job = jobs.claim(store, tid, job.job_id, worker="reference-worker",
                     lease_seconds=60.0, now=now + 4)

    write_payload = {"listing": "synthetic-reference-listing"}
    intent = policy.normalized_intent(
        tool="write.provider", resource="provider:fake-etsy", action="publish",
        parameters=write_payload, currency="USD", amount_minor=2500,
        data_class="internal", environment="staging")
    approval, _ = approvals.request(
        store, tenant_id=tid, business_id=bid, action="tool:write.provider",
        requested_by=agent.principal_id, idempotency_key="ref-approval-1",
        job_id=job.job_id, payload=intent, policy_version="p1-reference",
        expires_at=now + 3600)
    approval = approvals.decide(
        store, tenant_id=tid, approval_id=approval.approval_id,
        decision="approved", decided_by=owner.principal_id,
        reason="owner approved the exact reference intent", now=now + 5)

    job = jobs.claim(store, tid, job.job_id, worker="reference-worker",
                     lease_seconds=60.0, now=now + 5)

    write_policy = Policy(allowed_tools=frozenset({"write.provider"}),
                          autonomy="L4", approval_required=True,
                          policy_version="p1-reference")
    write = gateway.invoke(
        tenant_id=tid, business_id=bid, actor=agent.principal_id,
        tool="write.provider", idempotency_key="ref-write-1",
        policy=write_policy, payload=write_payload, cost_minor=2500,
        approval_id=approval.approval_id, snapshot=snapshot, catalog=catalog,
        subject_id=agent.principal_id, resource="provider:fake-etsy",
        action="publish", data_class="internal", environment="staging",
        now=now + 6)
    receipt = (write.invocation.result or {}).get("receipt", {})

    confusion_denied = False
    try:
        gateway.invoke(
            tenant_id=tid, business_id=bid, actor=agent.principal_id,
            tool="write.provider", idempotency_key="ref-write-confused",
            policy=write_policy,
            payload={"listing": "changed-after-approval"}, cost_minor=2500,
            approval_id=approval.approval_id, snapshot=snapshot,
            catalog=catalog, subject_id=agent.principal_id,
            resource="provider:fake-etsy", action="publish",
            data_class="internal", environment="staging", now=now + 6)
    except BusinessError as exc:
        confusion_denied = str(exc.code) in {
            "BUSINESS_APPROVAL_REQUIRED", "BUSINESS_APPROVAL_EXPIRED",
            "APPROVAL_REQUIRED", "APPROVAL_EXPIRED",
        } or "approval" in str(exc.code).lower()

    artifact = store.record_artifact(
        tid, bid, kind="provider_receipt", name="reference-receipt.json",
        media_type="application/json", job_id=job.job_id,
        body=json.dumps(receipt, sort_keys=True))
    ledger.append(store, tenant_id=tid, business_id=bid,
                  account="expense:provider", direction="debit",
                  amount_minor=2500, currency="USD",
                  idempotency_key="ref-ledger-debit", job_id=job.job_id,
                  memo="reference provider cost", actor=owner.principal_id)
    ledger.append(store, tenant_id=tid, business_id=bid,
                  account="cash:operating", direction="credit",
                  amount_minor=2500, currency="USD",
                  idempotency_key="ref-ledger-credit", job_id=job.job_id,
                  memo="reference provider funding", actor=owner.principal_id)

    durable.record_checkpoint(
        store, tid, bid, job.job_id, 1,
        state={"stage": "effect-planned", "task": task.task_id,
               "workspace": workspace.workspace_id,
               "workspace_revision": workspace.current_revision},
        pending_actions=[{"tool": "write.provider", "status": "pending"}],
        policy_revision="p1-reference", actor=agent.principal_id,
        job_id=job.job_id)
    durable.record_checkpoint(
        store, tid, bid, job.job_id, 2,
        state={"stage": "effect-receipted", "receipt": receipt},
        tool_receipts=[receipt], artifact_ids=[artifact.artifact_id],
        workspace_revision=workspace.current_revision,
        policy_revision="p1-reference", actor=agent.principal_id,
        job_id=job.job_id)

    with store.read() as conn:
        outbox_row = conn.execute(
            "SELECT outbox_id FROM bos_outbox WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tid, "event:ref-session-opened"),
        ).fetchone()
    outbox_id = outbox_row["outbox_id"]
    durable.claim_outbox(store, tid, outbox_id, worker="reference-worker",
                         lease_seconds=60.0, now=now + 7)
    durable.mark_outbox_sent(store, tid, outbox_id, worker="reference-worker",
                             provider_receipt={"status": "delivered",
                                               "request_id": "fake-event-1"},
                             now=now + 8)

    recovered = jobs.recover(store, now=now + 120)
    unknown = any(item.job_id == job.job_id and item.status == "unknown"
                  for item in recovered)
    jobs.requeue_unknown(store, tid, job.job_id, actor=owner.principal_id,
                         note="provider receipt reconciled after restart")
    jobs.claim(store, tid, job.job_id, worker="reference-worker-2",
               lease_seconds=60.0, now=now + 121)
    reconciled = jobs.finish(store, tid, job.job_id, "completed",
                             actor=owner.principal_id,
                             output={"receipt": receipt},
                             error_code=None, error_detail=None)

    exporter = otel.InMemoryExporter()
    counter = {"n": 0}

    def next_id(kind: str) -> str:
        counter["n"] += 1
        return f"{kind}-{counter['n']:04d}"

    tracer = otel.Tracer(
        exporter=exporter, capture_content=False,
        sampling=otel.SamplingPolicy(baseline_rate=1.0),
        clock=lambda: now + 9, id_factory=next_id)
    with tracer.start_run(
        name=otel.SPAN_INVOKE_AGENT, run_id=job.job_id, tenant_id=tid,
        owner_id=owner.principal_id, agent_id=agent.principal_id,
        task_id=task.task_id, session_id=auth_session.auth_session_id,
        approval_id=approval.approval_id) as trace:
        with tracer.span(otel.SPAN_CHAT, attributes={"gen_ai.request.model":
                                                     "fake-model"}) as chat:
            chat.set_attribute("gen_ai.usage.input_tokens", 12)
            chat.set_attribute("gen_ai.usage.output_tokens", 5)
        with tracer.span(otel.SPAN_RETRIEVAL) as retrieval:
            retrieval.set_attribute("qubit.memory.scope", "office/reference")
        with tracer.span(otel.SPAN_EXECUTE_TOOL,
                         attributes={"gen_ai.tool.name": "read.records"}
                         ) as read_span:
            tracer.record_evaluation(read_span, "read_correctness", passed=True,
                                     score=1.0)
        with tracer.span(otel.SPAN_APPROVAL,
                         attributes={"qubit.approval.id":
                                     approval.approval_id}) as approval_span:
            approval_span.set_attribute("qubit.approval.payload_hash",
                                        approval.request_hash)
        with tracer.span(otel.SPAN_EXECUTE_TOOL,
                         attributes={"gen_ai.tool.name": "write.provider"}
                         ) as write_span:
            write_span.set_attribute("qubit.tool.receipt",
                                     str(receipt.get("request_id")))
        with tracer.span(otel.SPAN_EXTERNAL_RECEIPT) as receipt_span:
            receipt_span.set_attribute("qubit.provider.request.id",
                                       str(receipt.get("request_id")))
        with tracer.span(otel.SPAN_RECONCILIATION) as recon_span:
            recon_span.set_attribute("qubit.outcome", "reconciled")
        tracer.record_metric("qubit.run.tokens", 17, unit="token")
    exported = exporter.snapshot()

    task_final = runs.get_task(store, tid, task.task_id)
    task_final = runs.transition_task(
        store, tid, task.task_id, "completed", actor=owner.principal_id,
        note="reference lane receipt reconciled", now=now + 130)
    workspace_final = runs.get_workspace(store, tid, workspace.workspace_id)

    store2 = BusinessStore(db_path)
    events = eventlog.replay(store2, tid,
                             f"session:{auth_session.auth_session_id}")
    checkpoint_list = durable.list_checkpoints(store2, tid, job.job_id)

    report = {
        "schema": "qoffice-phase1-reference/1",
        "status": "PASS" if all([
            wrong_audience, acked.delivery_state == "acked",
            read.executed, write.executed, confusion_denied,
            unknown, reconciled.status == "completed",
            len(events) == 1, len(checkpoint_list) == 2,
        ]) else "FAIL",
        "tenant": tid,
        "business": bid,
        "principal": {"owner": owner.principal_id,
                      "agent": agent.principal_id},
        "session": {"auth_session": auth_session.auth_session_id,
                    "verified": verified.auth_session_id ==
                    auth_session.auth_session_id,
                    "wrong_audience_denied": wrong_audience,
                    "memory_scope": "office/reference"},
        "a2a": {"message_id": message.message_id,
                "delivery_state": received.delivery_state,
                "acked_state": acked.delivery_state,
                "created": created},
        "task": {"task_id": task.task_id, "status": task_final.status,
                 "workspace": workspace_final.workspace_id,
                 "workspace_revision": workspace_final.current_revision},
        "read": {"executed": read.executed,
                 "invocation": read.invocation.invocation_id},
        "approval": {"approval_id": approval.approval_id,
                     "status": approval.status,
                     "payload_hash": approval.request_hash,
                     "confusion_denied": confusion_denied},
        "write": {"executed": write.executed,
                  "receipt": receipt},
        "artifact": {"artifact_id": artifact.artifact_id,
                     "sha256": artifact.sha256},
        "checkpoints": [item.sequence for item in checkpoint_list],
        "event": {"event_id": session_event.event_id,
                  "sequence": session_event.sequence,
                  "replayed": len(events)},
        "outbox": {"outbox_id": outbox_id, "delivered": True},
        "restart": {"unknown_after_recover": unknown,
                    "requeued": True,
                    "final_status": reconciled.status},
        "otel": {"capture_content": False,
                 "traces": len(exported["traces"]),
                 "spans": len(exported["spans"]),
                 "events": len(exported["events"]),
                 "metrics": len(exported["metrics"]),
                 "span_names": sorted({span.name
                                       for span in trace.spans})},
        "network_blocked": True,
        "db_path": str(db_path),
    }
    return report


def _guard_home(work_root: Path) -> None:
    live = Path(os.environ.get("QUBIT_HOME", "")).expanduser() if os.environ.get(
        "QUBIT_HOME") else None
    if live is not None:
        try:
            if live.exists() and (live / "state.db").exists():
                raise SystemExit(
                    "phase1_reference_lane: refusing to run against a live "
                    "QUBIT_HOME")
        except OSError:
            pass
    if work_root.exists() and any(work_root.iterdir()):
        raise SystemExit(
            "phase1_reference_lane: --work-root must be empty or absent")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", type=float, default=NOW)
    args = parser.parse_args(argv)

    base = Path(args.work_root) if args.work_root else Path(
        tempfile.mkdtemp(prefix="qoffice-phase1-"))
    try:
        _guard_home(base)
        with network_block():
            report = run_lane(base, now=args.now)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except BusinessError as exc:
        print(f"phase1_reference_lane: {exc.code}: {exc.detail}",
              file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"phase1_reference_lane: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep and args.work_root is None:
            shutil.rmtree(base, ignore_errors=True)

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        print(text)
    else:
        print(f"phase1_reference_lane: {report['status']}")
        print(f"  session={report['session']['auth_session']}")
        print(f"  approval={report['approval']['status']} "
              f"write_executed={report['write']['executed']}")
        print(f"  restart_unknown={report['restart']['unknown_after_recover']} "
              f"final={report['restart']['final_status']}")
        print(f"  otel_spans={report['otel']['spans']} "
              f"content_capture={report['otel']['capture_content']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
