#!/usr/bin/env python3
"""P4-04 / P4-06 — hermetic runtime + safety verification.

Runs a bounded set of behavioral checks against a throwaway store in a
temporary directory: no network, no live service, no credentials. Each check
exercises the real modules (``CapabilityRuntime``, ``ToolGateway``,
``SandboxProfile``, ``SafetyService``, ``OutOfBandKillSwitch``); nothing is
mocked at the boundary under test.

Exit 0 when every check passes, 1 when any check fails. ``--json`` emits a
machine-readable report; the prose mode prints one line per check.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

from voicebridge.business import AgentControlService, approvals
from voicebridge.business import policy as pol
from voicebridge.business import runs
from voicebridge.business import sandbox as sandbox_mod
from voicebridge.business.capability_runtime import (
    CAPABILITY_RUNTIME_HALTED_CODE,
    CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE,
    CAPABILITY_RUNTIME_RATE_LIMITED_CODE,
    CapabilityRuntime,
    untrusted_data,
)
from voicebridge.business.errors import (
    APPROVAL_REQUIRED_CODE,
    BUDGET_EXCEEDED_CODE,
    TOOL_NOT_ALLOWED_CODE,
    BusinessError,
)
from voicebridge.business.safety import (
    KILL_SWITCH_SURFACES,
    SAFETY_QUARANTINED_CODE,
    BehaviorBaseline,
    BehaviorSample,
    OutOfBandKillSwitch,
    SafetyService,
    lease_stopper,
    runtime_stopper,
)
from voicebridge.business.store import BusinessStore
from voicebridge.business.tools import Policy

NOW = 1_500_000.0
ISSUED = 1_000_000.0
EXPIRES = 2_000_000.0
SUBJECT = "agent:verify"

READ = "tool:read"
WRITE = "tool:publish"
BROWSER = "browser:fetch"


class Recorder:
    def __init__(self, result: Any = None) -> None:
        self.calls: list[Any] = []
        self.result = {"ok": True} if result is None else result

    def __call__(self, request, capability):
        self.calls.append((request, capability))
        return self.result


def catalog() -> pol.CapabilityCatalog:
    return pol.CapabilityCatalog(
        [
            pol.Capability(READ, "tool", "shop", "read", risk="L1"),
            pol.Capability(
                WRITE, "tool", "listing", "publish", risk="L4",
                approval_policy="required", budget_minor=100_000,
            ),
            pol.Capability(BROWSER, "browser", "web", "fetch", risk="L1"),
        ]
    )


def snapshot(tenant_id: str, *capabilities: str, subject: str = SUBJECT):
    cat = catalog()
    grants = tuple(
        pol.Grant(
            subject=subject,
            capability=name,
            resource=cat.get(name).resource,
            action=cat.get(name).action,
        )
        for name in capabilities
    )
    return pol.compile_snapshot(
        tenant_id=tenant_id,
        subject_id=subject,
        policy_revision="pol-verify",
        grants=grants,
        issued_at=ISSUED,
        expires_at=EXPIRES,
        catalog=cat,
    )


def sandbox(root: Path, *, allowlist=("api.example.com",)) -> sandbox_mod.SandboxProfile:
    profile = sandbox_mod.SandboxProfile(
        root,
        sandbox_mod.SandboxPolicy(
            allow_network=True, egress_allowlist=allowlist, command_allowlist=("git",)
        ),
    )
    profile.workspace.mkdir(parents=True, exist_ok=True)
    return profile


def resolver(mapping):
    def resolve(host, port):
        return mapping.get(host, [])

    return resolve


def _expect_refusal(code: str, fn: Callable[[], Any]) -> None:
    try:
        fn()
    except BusinessError as exc:
        if exc.code != code:
            raise AssertionError(f"expected {code}, got {exc.code}: {exc.detail}")
        return
    raise AssertionError(f"expected {code}, call succeeded")


def run_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a failed check is data
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc().splitlines()[-3:],
                }
            )
        else:
            checks.append({"name": name, "ok": True})

    def env(suffix: str):
        store = BusinessStore(root / f"{suffix}.db")
        tenant = store.create_tenant(f"Verify {suffix}")
        business = store.create_business(tenant.tenant_id, "Verify", slug=suffix)
        return store, tenant, business

    # -- P4-04 -----------------------------------------------------
    def allowed_read() -> None:
        store, tenant, business = env("read")
        rec = Recorder({"value": "hello"})
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, READ),
            sandbox=sandbox(root / "read-ws"), adapters={READ: rec},
            clock=lambda: NOW,
        )
        outcome = rt.call(
            tenant_id=tenant.tenant_id, business_id=business.business_id,
            subject_id=SUBJECT, actor=SUBJECT, capability_id=READ,
            policy=Policy(allowed_tools=frozenset({READ}), autonomy="L2"),
            idempotency_key="read-1", snapshot=None,
        )
        assert outcome.executed is True
        assert len(rec.calls) == 1
        assert outcome.invocation.result["untrusted"] is True
        assert untrusted_data(outcome) == {"value": "hello"}

    record("p4-04.allowed_read_executes", allowed_read)

    def denied_tool() -> None:
        store, tenant, business = env("deny")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, READ),
            sandbox=sandbox(root / "deny-ws"), adapters={READ: rec, WRITE: rec},
            clock=lambda: NOW,
        )
        _expect_refusal(
            TOOL_NOT_ALLOWED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=WRITE,
                policy=Policy(allowed_tools=frozenset({WRITE}), autonomy="L2"),
                idempotency_key="deny-1",
            ),
        )
        assert rec.calls == []

    record("p4-04.denied_tool_cannot_bypass", denied_tool)

    def egress_private() -> None:
        store, tenant, business = env("egress")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, BROWSER),
            sandbox=sandbox(root / "egress-ws"),
            adapters={BROWSER: rec},
            resolver=resolver({"api.example.com": ["127.0.0.1"]}),
            clock=lambda: NOW,
        )
        _expect_refusal(
            sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=BROWSER,
                policy=Policy(allowed_tools=frozenset({BROWSER}), autonomy="L2"),
                idempotency_key="egress-1",
                payload={"url": "https://api.example.com/x"},
            ),
        )
        assert rec.calls == []

    record("p4-04.egress_private_blocked", egress_private)

    def egress_allowed() -> None:
        store, tenant, business = env("egress-ok")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, BROWSER),
            sandbox=sandbox(root / "egress-ok-ws"),
            adapters={BROWSER: rec},
            resolver=resolver({"api.example.com": ["93.184.216.34"]}),
            clock=lambda: NOW,
        )
        rt.call(
            tenant_id=tenant.tenant_id, business_id=business.business_id,
            subject_id=SUBJECT, actor=SUBJECT, capability_id=BROWSER,
            policy=Policy(allowed_tools=frozenset({BROWSER}), autonomy="L2"),
            idempotency_key="egress-ok-1",
            payload={"url": "https://api.example.com/x"},
        )
        assert len(rec.calls) == 1

    record("p4-04.egress_allowlisted_passes", egress_allowed)

    def budget() -> None:
        store, tenant, business = env("budget")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, READ),
            sandbox=sandbox(root / "budget-ws"), adapters={READ: rec},
            clock=lambda: NOW,
        )
        _expect_refusal(
            BUDGET_EXCEEDED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=READ,
                policy=Policy(
                    allowed_tools=frozenset({READ}), autonomy="L2",
                    max_cost_minor=1,
                ),
                idempotency_key="budget-1", cost_minor=10,
            ),
        )
        assert rec.calls == []

    record("p4-04.budget_cap_enforced", budget)

    def rate() -> None:
        cat = pol.CapabilityCatalog(
            [pol.Capability(READ, "tool", "shop", "read", risk="L1", rate_limit=1)]
        )
        store = BusinessStore(root / "rate.db")
        tenant = store.create_tenant("Rate")
        business = store.create_business(tenant.tenant_id, "Rate", slug="rate")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, cat, snapshot=snapshot(tenant.tenant_id, READ),
            sandbox=sandbox(root / "rate-ws"), adapters={READ: rec},
            clock=lambda: NOW,
        )
        rt.call(
            tenant_id=tenant.tenant_id, business_id=business.business_id,
            subject_id=SUBJECT, actor=SUBJECT, capability_id=READ,
            policy=Policy(allowed_tools=frozenset({READ}), autonomy="L2"),
            idempotency_key="rate-1",
        )
        _expect_refusal(
            CAPABILITY_RUNTIME_RATE_LIMITED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=READ,
                policy=Policy(allowed_tools=frozenset({READ}), autonomy="L2"),
                idempotency_key="rate-2",
            ),
        )

    record("p4-04.rate_limit_enforced", rate)

    def approval() -> None:
        store, tenant, business = env("approval")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, WRITE),
            sandbox=sandbox(root / "approval-ws"), adapters={WRITE: rec},
            clock=lambda: NOW,
        )
        payload = {"listing_id": "lst-1"}
        _expect_refusal(
            APPROVAL_REQUIRED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=WRITE,
                policy=Policy(allowed_tools=frozenset({WRITE}), autonomy="L2"),
                idempotency_key="approval-1", payload=payload, cost_minor=5,
            ),
        )
        intent = pol.normalized_intent(
            tool=WRITE, resource="listing", action="publish",
            parameters=payload, currency="USD", amount_minor=5,
        )
        approval_row, _ = approvals.request(
            store, tenant_id=tenant.tenant_id,
            business_id=business.business_id, action=f"tool:{WRITE}",
            requested_by="owner", idempotency_key="apr-verify",
            payload=intent, expires_at=NOW + 10_000,
        )
        approvals.decide(
            store, tenant_id=tenant.tenant_id,
            approval_id=approval_row.approval_id, decision="approved",
            decided_by="owner", now=NOW,
        )
        rt.call(
            tenant_id=tenant.tenant_id, business_id=business.business_id,
            subject_id=SUBJECT, actor=SUBJECT, capability_id=WRITE,
            policy=Policy(allowed_tools=frozenset({WRITE}), autonomy="L2"),
            idempotency_key="approval-2", payload=payload, cost_minor=5,
            approval_id=approval_row.approval_id,
        )
        assert len(rec.calls) == 1

    record("p4-04.approval_binding", approval)

    def output_bound() -> None:
        store, tenant, business = env("output")
        deep: Any = "leaf"
        for _ in range(20):
            deep = {"nested": deep}
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, READ),
            sandbox=sandbox(root / "output-ws"), adapters={READ: Recorder(deep)},
            clock=lambda: NOW,
        )
        _expect_refusal(
            CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=READ,
                policy=Policy(allowed_tools=frozenset({READ}), autonomy="L2"),
                idempotency_key="output-1",
            ),
        )

    record("p4-04.output_bounded_untrusted", output_bound)

    # -- P4-06 -----------------------------------------------------
    def baseline() -> None:
        base = BehaviorBaseline(
            [BehaviorSample(turns=2, tool_calls=2, spend_minor=2) for _ in range(3)]
        )
        report = base.classify(
            BehaviorSample(turns=40, tool_calls=40, unknown_outcomes=30, spend_minor=9_000)
        )
        assert report.severity == "critical"
        names = {item.metric for item in report.anomalies}
        assert "spend_minor_per_minute" in names
        assert "unknown_outcome_rate" in names

    record("p4-06.baseline_anomaly", baseline)

    def make_agent(store, tenant, business, key="agent-verify"):
        control = AgentControlService(store)
        agent, _ = control.create(
            tenant.tenant_id, business.business_id, role_id="role-ops",
            department="operations", display_name="Ops", human_owner="owner",
            provider="local", model="qubit-brain", idempotency_key=key,
            permissions={"tools": [READ]}, safety={"autonomy": "L2"},
        )
        return control, agent

    def quarantine() -> None:
        store, tenant, business = env("quarantine")
        control, agent = make_agent(store, tenant, business)
        safety = SafetyService(store, control=control, clock=lambda: NOW)
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(),
            snapshot=snapshot(tenant.tenant_id, READ, subject=agent.agent_id),
            sandbox=sandbox(root / "quarantine-ws"), adapters={READ: rec},
            clock=lambda: NOW,
        )
        rt.set_guard(safety.guard_for_runtime())
        record_row = safety.quarantine(
            tenant.tenant_id, agent.agent_id, "anomalous spend", idempotency_key="q-1"
        )
        assert record_row.paused is True
        assert record_row.audit_id is not None
        _expect_refusal(
            SAFETY_QUARANTINED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=agent.agent_id, actor=agent.agent_id,
                capability_id=READ,
                policy=Policy(allowed_tools=frozenset({READ}), autonomy="L2"),
                idempotency_key="q-call",
            ),
        )
        assert rec.calls == []

    record("p4-06.quarantine_blocks_calls", quarantine)

    def kill_switch() -> None:
        store = BusinessStore(root / "kill.db")
        tenant = store.create_tenant("Kill")
        counts = {name: 0 for name in KILL_SWITCH_SURFACES}

        def stopper(name):
            def stop():
                counts[name] += 1
                return {"surface": name}
            return stop

        switch = OutOfBandKillSwitch(
            store, {name: stopper(name) for name in KILL_SWITCH_SURFACES},
            clock=lambda: NOW,
        )
        report = switch.engage(tenant_id=tenant.tenant_id, reason="verify")
        assert report.status == "STOPPED"
        again = switch.engage(tenant_id=tenant.tenant_id, reason="again")
        assert again.already_engaged is True
        assert all(counts[name] == 1 for name in KILL_SWITCH_SURFACES)
        assert len(store.list_audit(tenant.tenant_id, action="safety.kill_switch")) == 1

    record("p4-06.kill_switch_idempotent", kill_switch)

    def partial() -> None:
        store = BusinessStore(root / "partial.db")
        tenant = store.create_tenant("Partial")

        def boom():
            raise RuntimeError("scheduler offline")

        switch = OutOfBandKillSwitch(
            store,
            {
                "scheduler": boom,
                "gateway": lambda: True,
                "leases": lambda: True,
                "egress": lambda: True,
            },
            clock=lambda: NOW,
        )
        report = switch.engage(tenant_id=tenant.tenant_id, reason="partial")
        assert report.status == "PARTIAL"
        assert report.surfaces_failed == ("scheduler",)
        assert set(report.surfaces_stopped) == {"gateway", "leases", "egress"}

    record("p4-06.partial_failure_recorded", partial)

    def stoppers() -> None:
        store, tenant, business = env("stoppers")
        rec = Recorder()
        rt = CapabilityRuntime(
            store, catalog(), snapshot=snapshot(tenant.tenant_id, READ),
            sandbox=sandbox(root / "stoppers-ws"), adapters={READ: rec},
            clock=lambda: NOW,
        )
        lease = runs.acquire_lease(
            store, tenant.tenant_id, "workspace", "ws-1", holder=SUBJECT
        )
        switch = OutOfBandKillSwitch(
            store,
            {
                "scheduler": lambda: True,
                "gateway": runtime_stopper(rt),
                "leases": lease_stopper(store, tenant.tenant_id),
                "egress": lambda: True,
            },
            clock=lambda: NOW,
        )
        report = switch.engage(tenant_id=tenant.tenant_id, reason="stop all")
        assert report.status == "STOPPED"
        assert rt.halted is True
        assert runs.get_lease(store, tenant.tenant_id, lease.lease_id).released_at
        _expect_refusal(
            CAPABILITY_RUNTIME_HALTED_CODE,
            lambda: rt.call(
                tenant_id=tenant.tenant_id, business_id=business.business_id,
                subject_id=SUBJECT, actor=SUBJECT, capability_id=READ,
                policy=Policy(allowed_tools=frozenset({READ}), autonomy="L2"),
                idempotency_key="after-kill",
            ),
        )

    record("p4-06.stoppers_stop_real_surfaces", stoppers)

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="qubit-p4-verify-") as tmp:
        checks = run_checks(Path(tmp))
    failed = [check for check in checks if not check["ok"]]
    report = {
        "status": "PASS" if not failed else "FAIL",
        "checks_total": len(checks),
        "checks_passed": len(checks) - len(failed),
        "checks_failed": len(failed),
        "checks": checks,
        "limitations": [
            "hermetic: throwaway SQLite store, recording adapters, no network",
            "does not prove a live provider, live scheduler or live service stop",
            "complements, and does not replace, the pytest suites",
        ],
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in checks:
            mark = "PASS" if check["ok"] else "FAIL"
            detail = "" if check["ok"] else f" — {check['error']}"
            print(f"{mark} {check['name']}{detail}")
        print(f"\n{report['status']}: {report['checks_passed']}/{report['checks_total']} checks")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
