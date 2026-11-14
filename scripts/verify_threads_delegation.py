#!/usr/bin/env python3
"""Independent verifier for P4-02 threads and P4-03 bounded delegation.

It drives the real modules through their public seams in throwaway state — an
in-memory A2A event log for threads and a throwaway SQLite file for delegation
— and recomputes the acceptance claims rather than comparing a module to
itself:

Threads: open idempotency, owner↔agent and agent↔agent/room traffic, per-thread
ordering, duplicate/conflict handling, offline replay, participant ACLs, mention
authorization, tenant isolation, no authority transfer, bounds and retention.

Delegation: bounded depth/wave, one-winner claims, concurrency and lease
ceilings, lease-expiry stops, no-progress and retry caps, single-writer
worktrees, immutable artifact versions, artifact-preserving handoff and
idempotent delegate/complete.

Hermetic: no network, no live service, no credentials. ``--json`` prints one
machine-readable report.

Exit codes:
    0  every check passed
    1  a check failed
    2  usage / internal error
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business import (  # noqa: E402
    AgentControlService,
    BusinessStore,
    a2a,
    delegation,
    eventlog,
    identity,
    runs,
    sessions,
    threads,
)
from voicebridge.business.errors import BusinessError  # noqa: E402

VERIFICATION_SCHEMA = "qubit-office-threads-delegation-verification/1"
TENANT = "tenant-alpha"
OTHER_TENANT = "tenant-beta"
OWNER = "owner"
AGENT_A = "agent:a"
AGENT_B = "agent:b"
AGENT_C = "agent:c"


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _refused(call, code: str) -> tuple[bool, str]:
    try:
        call()
    except BusinessError as exc:
        return exc.code == code, exc.code
    return False, "no refusal"


def _thread_checks() -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    log = a2a.InMemoryEventLog()

    first, created = threads.open_thread(
        log, tenant_id=TENANT, kind="owner_agent",
        participants=[OWNER, AGENT_A], owner=OWNER, thread_id="thr-owner")
    again, created_again = threads.open_thread(
        log, tenant_id=TENANT, kind="owner_agent",
        participants=[OWNER, AGENT_A], owner=OWNER, thread_id="thr-owner")
    checks.append(_check(
        "thread_open_idempotent",
        created is True and created_again is False
        and first.thread_id == again.thread_id,
        f"created={created} repeat={created_again}"))

    message, made = threads.post(
        log, tenant_id=TENANT, thread_id="thr-owner",
        sender=OWNER, body="Please summarise.", idempotency_key="v-1")
    owner_read = threads.read(
        log, tenant_id=TENANT, thread_id="thr-owner", principal=OWNER)
    agent_read = threads.read(
        log, tenant_id=TENANT, thread_id="thr-owner", principal=AGENT_A)
    acked = threads.ack(
        log, tenant_id=TENANT, thread_id="thr-owner", principal=AGENT_A,
        message_id=message.message_id)
    checks.append(_check(
        "owner_agent_roundtrip",
        made is True and [m.message_id for m in owner_read] == [message.message_id]
        and [m.message_id for m in agent_read] == [message.message_id]
        and acked.delivery_state == "acked",
        f"sequence={message.sequence} state={acked.delivery_state}"))

    duplicate, made_again = threads.post(
        log, tenant_id=TENANT, thread_id="thr-owner",
        sender=OWNER, body="Please summarise.", idempotency_key="v-1")
    ok_conflict, code = _refused(
        lambda: threads.post(
            log, tenant_id=TENANT, thread_id="thr-owner",
            sender=OWNER, body="different", idempotency_key="v-1"),
        "A2A_IDEMPOTENCY_CONFLICT")
    checks.append(_check(
        "duplicate_and_conflict",
        made_again is False and duplicate.message_id == message.message_id
        and ok_conflict,
        f"repeat_created={made_again} conflict={code}"))

    ordered = []
    for index in range(3):
        item, _ = threads.post(
            log, tenant_id=TENANT, thread_id="thr-owner", sender=OWNER,
            body=f"queued {index}", idempotency_key=f"v-order-{index}")
        ordered.append(item)
    backlog = threads.replay(log, tenant_id=TENANT, principal=AGENT_A)
    sequences = [item.sequence for item in ordered]
    checks.append(_check(
        "ordering_and_replay",
        sequences == sorted(sequences)
        and [item.body for item in backlog[-3:]] == [
            "queued 0", "queued 1", "queued 2"],
        f"sequences={sequences} backlog={len(backlog)}"))

    ok_acl, acl_code = _refused(
        lambda: threads.read(
            log, tenant_id=TENANT, thread_id="thr-owner", principal=AGENT_C),
        threads.THREAD_NOT_PARTICIPANT_CODE)
    checks.append(_check(
        "participant_acl",
        ok_acl and threads.list_threads(
            log, tenant_id=TENANT, principal=AGENT_C) == [],
        f"refusal={acl_code}"))

    threads.open_thread(
        log, tenant_id=TENANT, kind="room",
        participants=[OWNER, AGENT_A, AGENT_B], thread_id="thr-room")

    def can_mention(sender: str, mention: str) -> bool:
        return (sender, mention) in {(OWNER, AGENT_B), (OWNER, threads.ROOM_MENTION)}

    room_message, _ = threads.post(
        log, tenant_id=TENANT, thread_id="thr-room", sender=OWNER,
        body="@agent:b pair please", mentions=["@agent:b", threads.ROOM_MENTION],
        can_mention=can_mention, idempotency_key="v-room")
    ok_mention, mention_code = _refused(
        lambda: threads.post(
            log, tenant_id=TENANT, thread_id="thr-room", sender=OWNER,
            body="@agent:a hi", mentions=["@agent:a"],
            can_mention=can_mention, idempotency_key="v-room-2"),
        threads.THREAD_MENTION_REFUSED_CODE)
    checks.append(_check(
        "room_mentions",
        room_message.mentions == (AGENT_B, threads.ROOM_MENTION) and ok_mention,
        f"mentions={room_message.mentions} refusal={mention_code}"))

    ok_tenant, tenant_code = _refused(
        lambda: threads.get_thread(
            log, tenant_id=OTHER_TENANT, thread_id="thr-owner"),
        threads.THREAD_NOT_FOUND_CODE)
    checks.append(_check(
        "tenant_isolation",
        ok_tenant and threads.replay(
            log, tenant_id=OTHER_TENANT, principal=AGENT_A) == [],
        f"refusal={tenant_code}"))

    ok_authority, authority_code = _refused(
        lambda: threads.post(
            log, tenant_id=TENANT, thread_id="thr-owner", sender=OWNER,
            body="you own this", idempotency_key="v-auth",
            payload={"transfer_ownership": AGENT_A}),
        threads.THREAD_AUTHORITY_REFUSED_CODE)
    threads.post(
        log, tenant_id=TENANT, thread_id="thr-owner", sender=OWNER,
        body="I transfer ownership to agent:b", idempotency_key="v-natural")
    unchanged = threads.get_thread(
        log, tenant_id=TENANT, thread_id="thr-owner").participants == (
            OWNER, AGENT_A)
    checks.append(_check(
        "no_authority_transfer",
        ok_authority and unchanged, f"refusal={authority_code} unchanged={unchanged}"))

    ok_body, body_code = _refused(
        lambda: threads.post(
            log, tenant_id=TENANT, thread_id="thr-owner", sender=OWNER,
            body="x" * 100, idempotency_key="v-big", max_body_bytes=10),
        threads.THREAD_BODY_TOO_LARGE_CODE)
    expired, _ = threads.post(
        log, tenant_id=TENANT, thread_id="thr-owner", sender=OWNER,
        body="short", idempotency_key="v-exp", expires_at=200.0, now=100.0)
    swept = threads.sweep(log, tenant_id=TENANT, now=201.0)
    checks.append(_check(
        "bounds_and_retention",
        ok_body and expired.message_id in swept["dead_lettered"],
        f"body_refusal={body_code} dead_lettered={swept['dead_lettered']}"))
    return checks


def _fixture(workdir: Path):
    store = BusinessStore(workdir / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref=OWNER,
        principal_id=OWNER)
    identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=AGENT_A,
        principal_id=AGENT_A)
    identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=AGENT_B,
        principal_id=AGENT_B)
    svc = AgentControlService(store)
    agent, _ = svc.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="agent-verify",
        security_principal_id=AGENT_A,
    )
    session = sessions.create_session(
        store, tenant.tenant_id, business.business_id, agent.agent_id,
        owner_principal=OWNER, actor="owner")
    return store, tenant, business, session


def _caps(**overrides) -> delegation.DelegationCaps:
    values = dict(
        max_depth=2, max_handoffs=2, max_attempts=2, max_wave_size=2,
        max_concurrency=50, no_progress_attempts=2, lease_ttl_seconds=30.0,
    )
    values.update(overrides)
    return delegation.DelegationCaps(**values)


def _delegation_checks(workdir: Path) -> List[Dict[str, Any]]:
    store, tenant, business, session = _fixture(workdir)
    checks: List[Dict[str, Any]] = []
    root = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Root",
        objective="coordinate", acceptance=["done"], budget={"usd": 1},
        required_capabilities=["cap:research"], actor=OWNER)
    child = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Child",
        parent_task_id=root.task.task_id, actor=OWNER, caps=_caps())
    ok_depth, depth_code = _refused(
        lambda: delegation.delegate(
            store, tenant.tenant_id, business.business_id, title="Deep",
            parent_task_id=child.task.task_id, actor=OWNER,
            caps=_caps(max_depth=1)),
        delegation.DELEGATION_DEPTH_EXCEEDED_CODE)
    ok_wave, wave_code = _refused(
        lambda: delegation.delegate(
            store, tenant.tenant_id, business.business_id, title="Sibling",
            parent_task_id=root.task.task_id, wave_id=child.wave_id,
            actor=OWNER, caps=_caps(max_wave_size=1)),
        delegation.DELEGATION_WAVE_EXCEEDED_CODE)
    checks.append(_check(
        "bounded_tree",
        root.depth == 0 and child.depth == 1
        and child.root_task_id == root.task.task_id and ok_depth and ok_wave,
        f"depth_refusal={depth_code} wave_refusal={wave_code}"))

    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Other",
        actor=OWNER, caps=_caps(max_concurrency=2))
    first = delegation.claim_next(
        store, tenant.tenant_id, principal=AGENT_A, caps=_caps())
    second = delegation.claim_next(
        store, tenant.tenant_id, principal=AGENT_B, caps=_caps())
    ok_concurrency, concurrency_code = _refused(
        lambda: delegation.claim_next(
            store, tenant.tenant_id, principal=AGENT_B,
            caps=_caps(max_concurrency=1)),
        delegation.DELEGATION_CONCURRENCY_EXCEEDED_CODE)
    checks.append(_check(
        "one_winner_and_concurrency",
        first is not None and second is not None
        and first.task.task_id != second.task.task_id
        and first.lease.holder == AGENT_A and ok_concurrency,
        f"first={first.task.task_id if first else None}"
        f" second={second.task.task_id if second else None}"
        f" refusal={concurrency_code}"))

    ok_lease, lease_code = _refused(
        lambda: delegation.complete(
            store, tenant.tenant_id, first.task.task_id, principal=AGENT_A,
            lease_id=first.lease.lease_id, fencing_token=first.lease.fencing_token,
            now=first.lease.expires_at + 1),
        runs.LEASE_LOST_CODE)
    checks.append(_check(
        "lease_expiry_stops",
        ok_lease
        and runs.get_task(store, tenant.tenant_id, first.task.task_id).status
        == "claimed",
        f"refusal={lease_code}"))

    delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Progress",
        actor=OWNER, caps=_caps(no_progress_attempts=2, max_attempts=3))
    moving = delegation.claim_next(
        store, tenant.tenant_id, principal=AGENT_A,
        caps=_caps(no_progress_attempts=2, max_attempts=3))
    delegation.record_attempt(
        store, tenant.tenant_id, moving.task.task_id, principal=AGENT_A,
        caps=_caps(no_progress_attempts=2, max_attempts=3))
    stuck = delegation.record_attempt(
        store, tenant.tenant_id, moving.task.task_id, principal=AGENT_A,
        caps=_caps(no_progress_attempts=2, max_attempts=3))
    checks.append(_check(
        "no_progress_blocks",
        stuck.code == delegation.DELEGATION_NO_PROGRESS_CODE
        and stuck.blocked is True and stuck.task.status == "blocked",
        f"code={stuck.code} status={stuck.task.status}"))

    retry = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Retry",
        actor=OWNER, caps=_caps(max_attempts=2, no_progress_attempts=2))
    retry_claim = delegation.claim_next(
        store, tenant.tenant_id, principal=AGENT_A,
        caps=_caps(max_attempts=2, no_progress_attempts=2))
    attempt_caps = _caps(max_attempts=2, no_progress_attempts=2)
    delegation.record_attempt(
        store, tenant.tenant_id, retry.task.task_id, principal=AGENT_A,
        progress={"n": 1}, caps=attempt_caps)
    delegation.record_attempt(
        store, tenant.tenant_id, retry.task.task_id, principal=AGENT_A,
        progress={"n": 2}, caps=attempt_caps)
    exhausted = delegation.record_attempt(
        store, tenant.tenant_id, retry.task.task_id, principal=AGENT_A,
        progress={"n": 3}, caps=attempt_caps)
    checks.append(_check(
        "retry_cap_blocks",
        retry_claim.task.task_id == retry.task.task_id
        and exhausted.code == delegation.DELEGATION_ATTEMPTS_EXCEEDED_CODE
        and exhausted.blocked is True,
        f"code={exhausted.code} status={exhausted.task.status}"))

    coding = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Coding",
        actor=OWNER, caps=_caps())
    claimed = delegation.claim_next(
        store, tenant.tenant_id, principal=AGENT_A, caps=_caps())
    while claimed.task.task_id != coding.task.task_id:
        claimed = delegation.claim_next(
            store, tenant.tenant_id, principal=AGENT_A, caps=_caps())
        if claimed is None:
            raise RuntimeError("could not claim the coding task")
    workspace, _ = delegation.bind_worktree(
        store, tenant.tenant_id, coding.task.task_id,
        session_id=session.session_id,
        repository_ref="repo://acme/app", worktree_ref="wt/verify", actor=OWNER)
    one = delegation.write_artifact(
        store, tenant.tenant_id, coding.task.task_id, principal=AGENT_A,
        lease_id=claimed.lease.lease_id, fencing_token=claimed.lease.fencing_token,
        name="report.md", kind="document", body="v1")
    same = delegation.write_artifact(
        store, tenant.tenant_id, coding.task.task_id, principal=AGENT_A,
        lease_id=claimed.lease.lease_id, fencing_token=claimed.lease.fencing_token,
        name="report.md", kind="document", body="v1")
    two = delegation.write_artifact(
        store, tenant.tenant_id, coding.task.task_id, principal=AGENT_A,
        lease_id=claimed.lease.lease_id, fencing_token=claimed.lease.fencing_token,
        name="report.md", kind="document", body="v2")
    ok_writer, writer_code = _refused(
        lambda: delegation.write_artifact(
            store, tenant.tenant_id, coding.task.task_id, principal=AGENT_B,
            lease_id=claimed.lease.lease_id,
            fencing_token=claimed.lease.fencing_token,
            name="other.md", kind="document", body="no"),
        delegation.DELEGATION_SINGLE_OWNER_CODE)
    checks.append(_check(
        "single_writer_and_immutable_versions",
        workspace.current_revision == "1" and one.version == 1
        and same.created is False and two.version == 2
        and two.artifact.artifact_id != one.artifact.artifact_id
        and store.get_artifact(tenant.tenant_id, one.artifact.artifact_id).body
        == "v1" and ok_writer,
        f"v1={one.artifact.artifact_id} v2={two.artifact.artifact_id}"
        f" refusal={writer_code}"))

    record = delegation.handoff(
        store, tenant.tenant_id, session.session_id, coding.task.task_id,
        from_principal=AGENT_A, to_principal=AGENT_B, reason="shift",
        artifact_ids=[two.artifact.artifact_id], caps=_caps(), actor=AGENT_A)
    accepted = delegation.accept_handoff(
        store, tenant.tenant_id, record.handoff_id, principal=AGENT_B)
    accepted_events = [
        event for event in eventlog.read_events(
            store, tenant.tenant_id, coding.task.task_id)
        if event.type == delegation.DELEGATION_HANDOFF_ACCEPTED
    ]
    checks.append(_check(
        "handoff_preserves_artifacts",
        accepted.status == "accepted"
        and accepted.artifact_ids == (two.artifact.artifact_id,)
        and accepted_events
        and accepted_events[-1].payload["artifact_ids"] == [
            two.artifact.artifact_id],
        f"handoff={record.handoff_id} artifacts={accepted.artifact_ids}"))

    idem = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Idem",
        idempotency_key="verify-idem", actor=OWNER, caps=_caps())
    idem_again = delegation.delegate(
        store, tenant.tenant_id, business.business_id, title="Idem",
        idempotency_key="verify-idem", actor=OWNER, caps=_caps())
    idem_claim = delegation.claim_next(
        store, tenant.tenant_id, principal=AGENT_A, caps=_caps())
    while idem_claim.task.task_id != idem.task.task_id:
        idem_claim = delegation.claim_next(
            store, tenant.tenant_id, principal=AGENT_A, caps=_caps())
    done = delegation.complete(
        store, tenant.tenant_id, idem.task.task_id, principal=AGENT_A,
        lease_id=idem_claim.lease.lease_id,
        fencing_token=idem_claim.lease.fencing_token, summary="ok")
    repeat = delegation.complete(
        store, tenant.tenant_id, idem.task.task_id, principal=AGENT_A,
        lease_id=idem_claim.lease.lease_id,
        fencing_token=idem_claim.lease.fencing_token, summary="ok")
    completions = [
        event for event in eventlog.read_events(
            store, tenant.tenant_id, idem.task.task_id)
        if event.type == delegation.DELEGATION_COMPLETED
    ]
    checks.append(_check(
        "idempotent_delegate_and_complete",
        idem_again.created is False and idem_again.task.task_id
        == idem.task.task_id and done.created is True
        and repeat.created is False and len(completions) == 1,
        f"delegate_repeat={idem_again.created} complete_repeat={repeat.created}"
        f" events={len(completions)}"))

    escalated = delegation.escalate(
        store, tenant.tenant_id, child.task.task_id, principal=AGENT_A,
        reason="blocked on missing data")
    checks.append(_check(
        "escalation_blocks",
        escalated.blocked is True and escalated.task.status == "blocked",
        f"code={escalated.code} status={escalated.task.status}"))
    return checks


def run_verification(workdir: Path | str | None = None) -> Dict[str, Any]:
    tmp = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="qubit-threads-delegation-verify-"))
    tmp.mkdir(parents=True, exist_ok=True)
    checks = _thread_checks() + _delegation_checks(tmp / "delegation")
    (tmp / "delegation").mkdir(parents=True, exist_ok=True)
    failed = sum(1 for check in checks if not check["ok"])
    import datetime

    return {
        "schema": VERIFICATION_SCHEMA,
        "ok": failed == 0,
        "mode": "self-check",
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Qubit Office P4-02 threads and P4-03 delegation.")
    parser.add_argument("--json", action="store_true",
                        help="print one machine-readable JSON report")
    args = parser.parse_args(argv)
    try:
        report = run_verification()
    except Exception as exc:  # noqa: BLE001 - never a pass
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "schema": VERIFICATION_SCHEMA}))
        else:
            print(f"verify_threads_delegation: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
