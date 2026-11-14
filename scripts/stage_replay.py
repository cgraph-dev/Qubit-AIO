#!/usr/bin/env python3
"""P0-06 — disposable staging / replay harness for the Office Business OS.

This script proves the *control plane* can be exercised without a live home,
a real provider, a tool integration or a network connection:

* a throwaway ``QUBIT_HOME``, ``HOME``, XDG dirs and ``TMPDIR`` are created
  **under one work root** and the process environment is pointed at them;
* a synthetic tenant, business and agents are created through the shipped
  ``BusinessStore`` / ``AgentControlService``;
* a job is enqueued, paused for a durable human approval, approved, claimed
  and settled through a **fake** ``ToolGateway`` executor;
* the tool's ledger directive appends the money row exactly once (the gateway
  keys it by invocation id) and an artifact records the receipt;
* a second job is left with an expired lease, the store is reopened (a
  restart), ``jobs.recover`` marks it ``unknown`` and an explicit
  ``requeue_unknown`` + claim + finish reconciles it;
* the recorded job-1 event projection is rebuilt from ``bos_job_event`` on a
  fresh connection and its digest must equal the recorded digest.

Honesty notes (also emitted in the JSON report as ``limitations``):

* The report is a *deterministic semantic* projection. Generated ULIDs and
  wall-clock timestamps are normalised out; two clean runs over the same
  fixture must produce the same ``digest``/``replay_digest``.
* The executor is a Python callable. No request leaves the process; the
  network guard is installed before any product module is imported and its
  own probe must be refused for the run to pass.
* The harness does not implement the P1 checkpoint/outbox records; the
  existing shipped jobs/approvals/ledger/tools APIs are used unchanged. The
  durable-substrate lane owns those additional records.

Exit codes: ``0`` replay equal + network blocked + unknown reconciled,
``1`` replay/consistency mismatch, ``2`` usage or I/O refusal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import random
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TASK_ID = "P0-06"
SCHEMA = "qoffice-stage-replay/1"
FIXTURE = "synthetic-department"
FIXTURES = (FIXTURE,)
DEFAULT_SEED = 1337
MARKER_NAME = ".qoffice-stage-root.json"


class StageError(Exception):
    """A typed harness refusal. ``exit_code`` is 2 unless overridden."""

    exit_code = 2

    def __init__(self, code: str, detail: str, *, exit_code: int | None = None):
        self.code = code
        self.detail = detail
        if exit_code is not None:
            self.exit_code = exit_code
        super().__init__(f"{code}: {detail}")


class NetworkBlocked(StageError):
    code = "QOFFICE_STAGE_NETWORK_BLOCKED"
    exit_code = 1

    def __init__(self, detail: str = "network access is forbidden in the stage harness"):
        super().__init__(self.code, detail)


# --------------------------------------------------------------------- utils
def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return _sha256_bytes(_canonical(value).encode("utf-8"))


_ID_RE = re.compile(
    r"\b(?:ten|biz|job|jev|apr|tcl|lgr|art|agt|bud)_[0-9A-HJKMNP-TV-Z]{26}\b"
)


def _normalise_text(value: Any) -> Any:
    """Strip generated ULIDs from a human note so two runs compare equal."""
    if isinstance(value, str):
        return _ID_RE.sub(lambda m: "<id>", value)
    if isinstance(value, Mapping):
        return {str(k): _normalise_text(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise_text(v) for v in value]
    return value


# ------------------------------------------------------------------- network
def install_network_guard() -> Dict[str, Any]:
    """Refuse connect/sendto/getaddrinfo for the whole process.

    The guard is a subclass of the real socket so unrelated stdlib code still
    constructs sockets; only operations that could reach a network fail with
    :class:`NetworkBlocked`. ``socketpair`` (AF_UNIX) stays available because
    SQLite and local process plumbing may use it.
    """
    real_socket = socket.socket
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    class GuardedSocket(real_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NetworkBlocked("connect() refused by the stage network guard")

        def connect_ex(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NetworkBlocked("connect_ex() refused by the stage network guard")

        def sendto(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NetworkBlocked("sendto() refused by the stage network guard")

    def deny_connection(*args, **kwargs):  # noqa: ANN002, ANN003
        raise NetworkBlocked("create_connection() refused by the stage network guard")

    def deny_lookup(*args, **kwargs):  # noqa: ANN002, ANN003
        raise NetworkBlocked("getaddrinfo() refused by the stage network guard")

    socket.socket = GuardedSocket  # type: ignore[assignment]
    socket.create_connection = deny_connection  # type: ignore[assignment]
    socket.getaddrinfo = deny_lookup  # type: ignore[assignment]

    def restore() -> None:
        socket.socket = real_socket  # type: ignore[assignment]
        socket.create_connection = real_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]

    restore.restore = True  # type: ignore[attr-defined]
    return {"restore": restore}


def probe_network_guard() -> Dict[str, Any]:
    """Prove the guard is active by attempting one loopback connect."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except NetworkBlocked as exc:
        return {"blocked": True, "error": exc.code, "detail": exc.detail}
    try:
        sock.settimeout(0.05)
        sock.connect(("127.0.0.1", 9))
    except NetworkBlocked as exc:
        return {"blocked": True, "error": exc.code, "detail": exc.detail}
    except OSError as exc:  # pragma: no cover - only if the guard is broken
        return {"blocked": False, "error": "OSError", "detail": str(exc)}
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return {"blocked": False, "error": None, "detail": "connect unexpectedly succeeded"}


# -------------------------------------------------------------- live-home guard
def _real_home() -> Path:
    """The operator's home from the passwd database, not ``$HOME``."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):  # pragma: no cover - exotic host
        return Path.home()


def _live_home_roots() -> Tuple[Path, ...]:
    home = _real_home()
    return (home / ".qubit", home / ".hermes", home / ".local" / "state" / "qubit-voicebridge")


def _looks_live(path: Path, *, started_at: float) -> Optional[str]:
    """Return a reason when ``path`` looks like a real, pre-existing install."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:  # pragma: no cover - unreadable path
        return None
    for live in _live_home_roots():
        try:
            if resolved == live.resolve() or live.resolve() in resolved.parents:
                return f"path is inside the operator live root {live}"
        except OSError:  # pragma: no cover - unreadable path
            continue
    state_db = resolved / "state.db"
    if state_db.is_file():
        try:
            if state_db.stat().st_mtime < started_at:
                return f"{state_db} pre-dates this run"
        except OSError:  # pragma: no cover - stat race
            return f"{state_db} is present and unreadable"
    for leaf in (resolved / "voice" / "state", resolved / "voice" / "config"):
        if leaf.exists():
            return f"{leaf} exists (a live voice tree)"
    return None


def prepare_environment(
    work_root: Path, *, started_at: float
) -> Dict[str, Any]:
    """Create the throwaway tree and point the process environment at it."""
    incoming = os.environ.get("QUBIT_HOME")
    if incoming:
        reason = _looks_live(Path(incoming), started_at=started_at)
        if reason is not None:
            raise StageError(
                "QOFFICE_STAGE_LIVE_HOME_REFUSED",
                f"QUBIT_HOME={incoming!r} looks like a live install: {reason}",
            )

    for label, path in (
        ("work_root", work_root),
        ("home", work_root / "home"),
        ("tmp", work_root / "tmp"),
        ("xdg_config", work_root / "xdg" / "config"),
        ("xdg_data", work_root / "xdg" / "data"),
        ("xdg_state", work_root / "xdg" / "state"),
        ("xdg_cache", work_root / "xdg" / "cache"),
        ("qubit_home", work_root / "qubit-home"),
    ):
        path.mkdir(parents=True, exist_ok=True)

    env = {
        "HOME": work_root / "home",
        "TMPDIR": work_root / "tmp",
        "XDG_CONFIG_HOME": work_root / "xdg" / "config",
        "XDG_DATA_HOME": work_root / "xdg" / "data",
        "XDG_STATE_HOME": work_root / "xdg" / "state",
        "XDG_CACHE_HOME": work_root / "xdg" / "cache",
        "QUBIT_HOME": work_root / "qubit-home",
    }
    for key, path in env.items():
        resolved = str(path.resolve())
        if not resolved.startswith(str(work_root.resolve())):
            raise StageError(
                "QOFFICE_STAGE_ISOLATION_FAILED",
                f"{key}={resolved} escaped the work root",
            )
        os.environ[key] = resolved
    os.chdir(work_root)

    return {key: str(value.resolve()) for key, value in env.items()}


def write_marker(work_root: Path, *, started_at: float) -> None:
    marker = {
        "task_id": TASK_ID,
        "schema": SCHEMA,
        "work_root": str(work_root.resolve()),
        "started_at": started_at,
    }
    (work_root / MARKER_NAME).write_text(_canonical(marker) + "\n", encoding="utf-8")


def work_root_state(work_root: Path) -> bool:
    """True when ``work_root`` is safe to adopt: absent or an empty dir."""
    if not work_root.exists():
        return True
    if not work_root.is_dir():
        return False
    return not any(work_root.iterdir())


def cleanup_work_root(work_root: Path, *, keep: bool) -> Dict[str, Any]:
    if keep:
        return {"cleaned": False, "kept": True, "skipped": "keep requested"}
    marker = work_root / MARKER_NAME
    if not marker.is_file():
        return {"cleaned": False, "kept": False, "skipped": "stage marker absent"}
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cleaned": False, "kept": False, "skipped": "stage marker unreadable"}
    if payload.get("task_id") != TASK_ID:
        return {"cleaned": False, "kept": False, "skipped": "stage marker mismatch"}
    if payload.get("work_root") != str(work_root.resolve()):
        return {"cleaned": False, "kept": False, "skipped": "stage marker path mismatch"}
    shutil.rmtree(work_root)
    return {"cleaned": True, "kept": False, "skipped": None}


# ------------------------------------------------------------------ fixtures
def _fixture_payload(seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    amount = 1500 + rng.randrange(0, 750)
    return {
        "invoice_id": f"INV-{seed:04d}",
        "customer": "synthetic-customer",
        "amount_minor": amount,
        "currency": "USD",
        "line_items": [
            {"sku": "SYN-001", "quantity": 1, "unit_minor": amount},
        ],
    }


def _agent_specs() -> List[Dict[str, Any]]:
    return [
        {
            "role_id": "operator",
            "department": "operations",
            "display_name": "Synthetic Operator",
            "human_owner": "synthetic-owner",
            "provider": "fake-provider",
            "model": "fake-model-v1",
            "room": "ops-floor",
            "desk": "ops-1",
        },
        {
            "role_id": "bookkeeper",
            "department": "finance",
            "display_name": "Synthetic Bookkeeper",
            "human_owner": "synthetic-owner",
            "provider": "fake-provider",
            "model": "fake-model-v1",
            "room": "finance-floor",
            "desk": "fin-1",
        },
    ]


# ---------------------------------------------------------------- projection
def _event_rows(conn, job_ids: Sequence[str]) -> List[Dict[str, Any]]:
    if not job_ids:
        return []
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        "SELECT job_id, seq, kind, actor, detail_json FROM bos_job_event"
        f" WHERE job_id IN ({placeholders}) ORDER BY job_id, seq",
        tuple(job_ids),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except (TypeError, ValueError):  # pragma: no cover - stored by our code
            detail = {}
        if isinstance(detail, Mapping):
            detail = {
                str(key): detail[key]
                for key in ("from", "to", "note", "kind")
                if key in detail
            }
        out.append(
            {
                "seq": int(row["seq"]),
                "kind": str(row["kind"]),
                "actor": str(row["actor"]),
                "detail": _normalise_text(detail),
            }
        )
    return out


def project_job_events(store, job_ids: Sequence[str]) -> Dict[str, Any]:
    """Rebuild one job's history from ``bos_job_event`` only.

    The returned shape is deterministic: no ULIDs and no timestamps survive.
    """
    with store.read() as conn:
        events = _event_rows(conn, job_ids)

    status = None
    for event in events:
        if event["kind"].startswith("job."):
            target = event["kind"].split(".", 1)[1]
            detail = event.get("detail")
            if isinstance(detail, Mapping) and "to" in detail:
                status = str(detail["to"])
            else:
                status = target
    return {"events": events, "derived_status": status, "event_count": len(events)}


def live_state_projection(store, tenant_id: str, job_id: str) -> Dict[str, Any]:
    from voicebridge.business import approvals, jobs, tools

    job = jobs.get(store, tenant_id, job_id)
    invocation_rows = tools.ToolGateway(store).list_invocations(tenant_id)
    approval_rows = approvals.list_approvals(store, tenant_id)
    return {
        "status": job.status,
        "attempt": int(job.attempt),
        "output": _normalise_text(job.output),
        "last_error_code": job.last_error_code,
        "tool_invocations": [
            {
                "tool": row.tool,
                "status": row.status,
                "request_hash": row.request_hash,
                "dry_run": bool(row.dry_run),
                "cost_minor": int(row.cost_minor),
                "result": _normalise_text(row.result),
            }
            for row in invocation_rows
        ],
        "approvals": [
            {
                "action": row.action,
                "status": row.status,
                "request_hash": row.request_hash,
                "policy_version": row.policy_version,
                "decided_by": row.decided_by,
            }
            for row in approval_rows
        ],
    }


def build_projection(store, *, tenant_id: str, business_id: str, job_aliases: Mapping[str, str]) -> Dict[str, Any]:
    from voicebridge.business import AgentControlService, approvals, jobs, ledger, tools

    alias_to_job = {alias: job_id for job_id, alias in job_aliases.items()}
    job_rows = []
    for alias in sorted(alias_to_job):
        job = jobs.get(store, tenant_id, alias_to_job[alias])
        job_rows.append(
            {
                "alias": alias,
                "kind": job.kind,
                "status": job.status,
                "attempt": int(job.attempt),
                "request_hash": job.request_hash,
                "output": _normalise_text(job.output),
                "last_error_code": job.last_error_code,
                "events": _job_events(store, alias_to_job[alias]),
            }
        )
    agent_rows = []
    for agent in AgentControlService(store).list(tenant_id, business_id=business_id):
        agent_rows.append(
            {
                "role_id": agent.role_id,
                "department": agent.department,
                "display_name": agent.display_name,
                "provider": agent.provider,
                "model": agent.model,
                "status": agent.status,
            }
        )
    invoice_rows = [
        {
            "tool": row.tool,
            "status": row.status,
            "request_hash_present": bool(row.request_hash),
            "payload_hash": _digest(
                {"tool": row.tool, "payload": _normalise_text(row.request)}
            ),
            "dry_run": bool(row.dry_run),
            "cost_minor": int(row.cost_minor),
            "result": _normalise_text(row.result),
        }
        for row in tools.ToolGateway(store).list_invocations(
            tenant_id, business_id=business_id
        )
    ]
    approval_rows = [
        {
            "action": row.action,
            "status": row.status,
            "request_hash_present": bool(row.request_hash),
            "payload_hash": _digest(
                {"action": row.action, "payload": _normalise_text(row.payload)}
            ),
            "policy_version": row.policy_version,
            "decided_by": row.decided_by,
        }
        for row in approvals.list_approvals(store, tenant_id, business_id=business_id)
    ]
    ledger_rows = [
        {
            "account": row.account,
            "direction": row.direction,
            "amount_minor": int(row.amount_minor),
            "currency": row.currency,
            "memo": row.memo,
        }
        for row in ledger.list_entries(store, tenant_id, business_id=business_id)
    ]
    artifact_rows = [
        {
            "kind": row.kind,
            "name": row.name,
            "media_type": row.media_type,
            "sha256": row.sha256,
            "size_bytes": int(row.size_bytes),
        }
        for row in store.list_artifacts(tenant_id, business_id=business_id)
    ]
    audit_rows = [
        {"action": row.action, "resource_type": row.resource_type, "outcome": row.outcome}
        for row in store.list_audit(tenant_id, business_id=business_id, limit=10_000)
    ]
    return {
        "agents": sorted(agent_rows, key=lambda item: item["display_name"]),
        "jobs": job_rows,
        "tool_invocations": invoice_rows,
        "approvals": approval_rows,
        "ledger": ledger_rows,
        "artifacts": artifact_rows,
        "audit": sorted(
            audit_rows, key=lambda item: (item["action"], item["resource_type"], item["outcome"])
        ),
    }


def _job_events(store, job_id: str) -> List[Dict[str, Any]]:
    with store.read() as conn:
        return _event_rows(conn, [job_id])


# --------------------------------------------------------------------- flow
def run_fixture(
    *,
    fixture: str,
    work_root: Path,
    seed: int,
    now: Optional[float],
    started_at: float,
) -> Dict[str, Any]:
    if fixture not in FIXTURES:
        raise StageError(
            "QOFFICE_STAGE_UNKNOWN_FIXTURE",
            f"unknown fixture {fixture!r}; expected one of {', '.join(FIXTURES)}",
        )

    from voicebridge.business import (
        AgentControlService,
        ExecutorResult,
        Policy,
        approvals,
        jobs,
        ledger,
        tools,
    )
    from voicebridge.business.store import BusinessStore

    payload = _fixture_payload(seed)
    base_now = float(now if now is not None else 1_750_000_000.0)
    db_path = work_root / "qubit-home" / "state" / "business" / "business.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store = BusinessStore(db_path)
    tenant = store.create_tenant(
        "Synthetic Tenant",
        tenant_id="ten_synthetic_department",
        metadata={"fixture": fixture, "seed": seed},
    )
    business = store.create_business(
        tenant.tenant_id,
        "Synthetic Department",
        slug="synthetic-department",
        currency="USD",
        metadata={"fixture": fixture},
    )
    store.set_business_status(
        tenant.tenant_id, business.business_id, "active", actor="stage"
    )
    control = AgentControlService(store)
    agents = []
    for index, spec in enumerate(_agent_specs()):
        agent, _created = control.create(
            tenant.tenant_id,
            business.business_id,
            idempotency_key=f"agent-{index}",
            actor="stage",
            **spec,
        )
        agents.append(agent)

    job, created = jobs.create(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="invoice.send",
        idempotency_key="job-invoice-send",
        input=payload,
        actor="stage",
    )
    if not created:
        raise StageError(
            "QOFFICE_STAGE_FIXTURE_NOT_CLEAN",
            "the fixture job already existed; the work root was not clean",
        )
    jobs.enqueue(store, tenant.tenant_id, job.job_id, actor="stage")
    jobs.claim(
        store,
        tenant.tenant_id,
        job.job_id,
        worker="stage-worker-1",
        lease_seconds=120.0,
        now=base_now,
        actor="stage",
    )

    approval, _approval_created = approvals.request(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=f"tool:{job.kind}",
        requested_by="stage-worker-1",
        idempotency_key="approval-invoice-send",
        job_id=job.job_id,
        payload={"invoice_id": payload["invoice_id"]},
        policy_version="v1",
        # No expiry: this scenario proves the durable approval *wait*, not the
        # expiry timer (which has its own test elsewhere).
        expires_at=None,
    )
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="synthetic-owner",
        reason="stage approval",
    )
    jobs.claim(
        store,
        tenant.tenant_id,
        job.job_id,
        worker="stage-worker-1",
        lease_seconds=120.0,
        now=base_now + 1.0,
        actor="stage",
    )

    executed_calls: List[str] = []

    def fake_executor(request):
        executed_calls.append(request.idempotency_key)
        return ExecutorResult(
            result={
                "provider_receipt": f"fake-receipt-{payload['invoice_id']}",
                "invoice_id": payload["invoice_id"],
                "echo": dict(request.payload),
            },
            executed=True,
            ledger={
                "account": "accounts_receivable",
                "direction": "credit",
                "amount_minor": payload["amount_minor"],
                "currency": payload["currency"],
                "memo": f"fake {request.tool} {payload['invoice_id']}",
            },
        )

    policy = Policy(
        allowed_tools=frozenset({job.kind, "crm.upsert"}),
        autonomy="L4",
        max_cost_minor=0,
        approval_required=True,
        policy_version="v1",
    )
    gateway = tools.ToolGateway(store, executor=fake_executor)
    outcome = gateway.invoke(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="stage-worker-1",
        tool=job.kind,
        idempotency_key="tool-invoice-send",
        policy=policy,
        payload=payload,
        cost_minor=0,
        currency="USD",
        approval_id=approval.approval_id,
    )
    if not outcome.executed or outcome.invocation.status != "success":
        raise StageError(
            "QOFFICE_STAGE_TOOL_NOT_EXECUTED",
            f"fake tool did not execute cleanly (status={outcome.invocation.status!r})",
        )
    jobs.finish(
        store,
        tenant.tenant_id,
        job.job_id,
        "completed",
        actor="stage-worker-1",
        output={
            "receipt": f"fake-receipt-{payload['invoice_id']}",
            "invoice_id": payload["invoice_id"],
        },
    )
    artifact = store.record_artifact(
        tenant.tenant_id,
        business.business_id,
        kind="invoice_receipt",
        name=f"{payload['invoice_id']}-receipt.txt",
        body=(
            f"invoice={payload['invoice_id']} amount_minor={payload['amount_minor']}"
            f" currency={payload['currency']} status=PAID\n"
        ),
        job_id=job.job_id,
        media_type="text/plain",
    )

    recorded_job1 = project_job_events(store, [job.job_id])
    recorded_digest = _digest(recorded_job1)

    # ---- restart: reopen the same DB, leave a job with an expired lease ----
    restarted = BusinessStore(db_path)
    recovery_job, _ = jobs.create(
        restarted,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        kind="crm.upsert",
        idempotency_key="job-recovery",
        input={"contact_id": f"C-{seed}", "field": "last_invoice"},
        actor="stage",
    )
    jobs.enqueue(restarted, tenant.tenant_id, recovery_job.job_id, actor="stage")
    jobs.claim(
        restarted,
        tenant.tenant_id,
        recovery_job.job_id,
        worker="stage-worker-2",
        lease_seconds=1.0,
        now=base_now - 100.0,
        actor="stage",
    )
    recovered = jobs.recover(restarted, now=base_now, actor="stage")
    if [item.job_id for item in recovered] != [recovery_job.job_id]:
        raise StageError(
            "QOFFICE_STAGE_RECOVERY_MISMATCH",
            f"recover() returned {[item.job_id for item in recovered]!r}",
        )
    unknown_job = jobs.get(restarted, tenant.tenant_id, recovery_job.job_id)
    if unknown_job.status != "unknown":
        raise StageError(
            "QOFFICE_STAGE_UNKNOWN_NOT_RECORDED",
            f"recovered job status is {unknown_job.status!r}, expected 'unknown'",
        )
    jobs.requeue_unknown(
        restarted,
        tenant.tenant_id,
        recovery_job.job_id,
        actor="synthetic-owner",
        note="reconciled after restart: provider receipt confirmed",
    )
    jobs.claim(
        restarted,
        tenant.tenant_id,
        recovery_job.job_id,
        worker="stage-worker-2",
        lease_seconds=120.0,
        now=base_now + 2.0,
        actor="stage",
    )
    jobs.finish(
        restarted,
        tenant.tenant_id,
        recovery_job.job_id,
        "completed",
        actor="stage-worker-2",
        output={"contact_id": f"C-{seed}", "reconciled": True},
    )

    # ---- replay the recorded job-1 projection from bos_job_event ----
    replayed_job1 = project_job_events(restarted, [job.job_id])
    replay_digest = _digest(replayed_job1)

    live_job1 = jobs.get(restarted, tenant.tenant_id, job.job_id)
    live_state = {
        "status": live_job1.status,
        "attempt": int(live_job1.attempt),
        "output": _normalise_text(live_job1.output),
        "artifact_sha256": artifact.sha256,
    }
    consistency = {
        "event_status_matches_store": replayed_job1["derived_status"] == live_job1.status,
        "live_state": live_state,
    }

    job_aliases = {job.job_id: "job-invoice-send", recovery_job.job_id: "job-recovery"}
    projection = build_projection(
        restarted,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        job_aliases=job_aliases,
    )

    event_history = {
        "schema": SCHEMA,
        "fixture": fixture,
        "seed": seed,
        "jobs": [
            {
                "alias": "job-invoice-send",
                "events": recorded_job1["events"],
                "derived_status": recorded_job1["derived_status"],
            },
            {
                "alias": "job-recovery",
                "events": project_job_events(restarted, [recovery_job.job_id])["events"],
                "derived_status": "completed",
            },
        ],
    }
    event_history_path = work_root / "event-history.json"
    event_history_path.write_text(
        json.dumps(event_history, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    digest = _digest(
        {
            "schema": SCHEMA,
            "fixture": fixture,
            "seed": seed,
            "tenant": {"name": tenant.name, "metadata": tenant.metadata},
            "business": {
                "name": business.name,
                "slug": business.slug,
                "currency": business.currency,
            },
            "projection": projection,
        }
    )

    counts = {
        "tenants": len(store.list_tenants()),
        "businesses": len(store.list_businesses(tenant.tenant_id)),
        "agents": len(control.list(tenant.tenant_id, business_id=business.business_id)),
        "jobs": len(jobs.list_jobs(restarted, tenant.tenant_id, business_id=business.business_id)),
        "job_events": int(
            _scalar(
                restarted,
                "SELECT COUNT(*) FROM bos_job_event WHERE tenant_id=?",
                (tenant.tenant_id,),
            )
        ),
        "approvals": len(
            approvals.list_approvals(restarted, tenant.tenant_id, business_id=business.business_id)
        ),
        "tool_invocations": len(
            tools.ToolGateway(restarted).list_invocations(
                tenant.tenant_id, business_id=business.business_id
            )
        ),
        "ledger_entries": len(
            ledger.list_entries(restarted, tenant.tenant_id, business_id=business.business_id)
        ),
        "artifacts": len(
            store.list_artifacts(tenant.tenant_id, business_id=business.business_id)
        ),
    }

    ledger_summary = ledger.summary(restarted, tenant.tenant_id, business_id=business.business_id)
    unknown_reconciled = 1 if recovered else 0

    return {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "fixture": fixture,
        "seed": seed,
        "clock": {"base_epoch": base_now, "source": "fixed" if now is not None else "fixture-default"},
        "work_root": str(work_root.resolve()),
        "db_path": str(db_path.resolve()),
        "counts": counts,
        "ledger_summary": _normalise_text(ledger_summary),
        "events": {
            "recorded_digest": recorded_digest,
            "replay_digest": replay_digest,
            "recorded_events": recorded_job1["event_count"],
            "replayed_events": replayed_job1["event_count"],
            "event_history_path": str(event_history_path.resolve()),
            "event_history_sha256": _sha256_file(event_history_path),
        },
        "digest": digest,
        "projection_parts": {
            key: _digest(value) for key, value in projection.items()
        },
        "replay_digest": replay_digest,
        "equal": replay_digest == recorded_digest,
        "consistency": consistency,
        "unknown_reconciled": unknown_reconciled,
        "executed_calls": executed_calls,
    }


def _scalar(store, query: str, params: Tuple[Any, ...]) -> Any:
    with store.read() as conn:
        return conn.execute(query, params).fetchone()[0]


# ---------------------------------------------------------------------- main
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "P0-06 staging/replay harness: throwaway home, synthetic tenant,"
            " fake tool executor, durable approval and restart reconciliation."
        )
    )
    parser.add_argument(
        "--fixture",
        default=FIXTURE,
        choices=list(FIXTURES),
        help=f"fixture to stage (default: {FIXTURE})",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="assert the process network guard is active (it is always installed)",
    )
    parser.add_argument(
        "--work-root",
        default=None,
        help="parent for the throwaway home; default: a fresh temp directory",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the work root after the run (default: remove a marked root)",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="fixture seed (default: 1337)"
    )
    parser.add_argument(
        "--now",
        type=float,
        default=None,
        help="fixed epoch seconds for lease arithmetic (default: 1750000000.0)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the JSON report to stdout"
    )
    return parser.parse_args(argv)


def _emit(text: str, *, stream=sys.stdout) -> None:
    stream.write(text if text.endswith("\n") else text + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    started_at = __import__("time").time()
    args = _parse_args(argv)

    external_root = Path(args.work_root).expanduser() if args.work_root else None
    if external_root is not None:
        if not work_root_state(external_root):
            _emit(
                "QOFFICE_STAGE_WORK_ROOT_NOT_EMPTY: "
                f"{external_root} exists and is not empty",
                stream=sys.stderr,
            )
            return 2
        reason = _looks_live(external_root, started_at=started_at)
        if reason is not None:
            _emit(
                "QOFFICE_STAGE_LIVE_HOME_REFUSED: "
                f"work root {external_root} looks live: {reason}",
                stream=sys.stderr,
            )
            return 2
        work_root = external_root
        work_root.mkdir(parents=True, exist_ok=True)
    else:
        work_root = Path(tempfile.mkdtemp(prefix="qoffice-stage-"))

    write_marker(work_root, started_at=started_at)
    guard = install_network_guard()
    probe = probe_network_guard()
    report: Dict[str, Any] = {}
    exit_code = 2
    try:
        isolation = prepare_environment(work_root, started_at=started_at)
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        import time as _time

        run_started = _time.perf_counter()
        result = run_fixture(
            fixture=args.fixture,
            work_root=work_root,
            seed=args.seed,
            now=args.now,
            started_at=started_at,
        )
        result["isolation"] = isolation
        result["network_blocked"] = bool(probe["blocked"])
        result["network_probe"] = probe
        result["no_network_flag"] = bool(args.no_network)
        result["limitations"] = [
            "the report digest is a deterministic semantic projection: ULIDs and"
            " wall-clock timestamps are normalised out",
            "the tool executor is an in-process Python callable; no provider or"
            " external system is contacted",
            "P1 checkpoint/outbox records are not implemented here; the shipped"
            " jobs/approvals/ledger/tool APIs are used unchanged",
            "the restart is simulated by reopening the SQLite file and expiring a"
            " lease in-process, not by killing and restarting an OS process",
        ]
        report = result
        ok = (
            result["equal"]
            and result["network_blocked"]
            and result["unknown_reconciled"] == 1
            and result["consistency"]["event_status_matches_store"]
        )
        exit_code = 0 if ok else 1
        report["status"] = "PASS" if ok else "FAIL"
        report_path = work_root / "stage-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        report["report_path"] = str(report_path.resolve())
        report["report_sha256"] = _sha256_file(report_path)
        report["elapsed_seconds"] = round(_time.perf_counter() - run_started, 6)
    except StageError as exc:
        report = {
            "schema": SCHEMA,
            "task_id": TASK_ID,
            "status": "FAIL",
            "error": {"code": exc.code, "detail": exc.detail},
            "work_root": str(work_root.resolve()),
        }
        exit_code = exc.exit_code
        _emit(f"{exc.code}: {exc.detail}", stream=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - typed at the CLI boundary
        report = {
            "schema": SCHEMA,
            "task_id": TASK_ID,
            "status": "FAIL",
            "error": {"code": "QOFFICE_STAGE_INTERNAL_ERROR", "detail": str(exc)},
            "work_root": str(work_root.resolve()),
        }
        exit_code = 2
        _emit(f"QOFFICE_STAGE_INTERNAL_ERROR: {exc}", stream=sys.stderr)
    finally:
        guard["restore"]()
        cleanup = cleanup_work_root(work_root, keep=args.keep)
        report["cleanup"] = cleanup

    if args.json:
        _emit(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _emit(
            f"{report.get('status', 'FAIL')} {TASK_ID} fixture={report.get('fixture')} "
            f"equal={report.get('equal')} network_blocked={report.get('network_blocked')} "
            f"unknown_reconciled={report.get('unknown_reconciled')} "
            f"work_root={report.get('work_root')}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
