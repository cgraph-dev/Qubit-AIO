"""P4-04 — every capability crosses the one gateway (hermetic)."""
from __future__ import annotations

import pytest

from voicebridge.business import approvals
from voicebridge.business import policy as pol
from voicebridge.business import sandbox as sandbox_mod
from voicebridge.business.capability_runtime import (
    CAPABILITY_RUNTIME_HALTED_CODE,
    CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE,
    CAPABILITY_RUNTIME_POLICY_REQUIRED_CODE,
    CAPABILITY_RUNTIME_RATE_LIMITED_CODE,
    CAPABILITY_RUNTIME_UNSUPPORTED_CODE,
    CAPABILITY_RUNTIME_WILDCARD_CODE,
    untrusted_data,
)
from voicebridge.business.errors import (
    APPROVAL_REQUIRED_CODE,
    BUDGET_EXCEEDED_CODE,
    TOOL_NOT_ALLOWED_CODE,
    BusinessError,
)
from voicebridge.business.tools import Policy
from _p4_helpers import (
    BROWSER,
    CONNECTOR,
    FILES,
    NOW,
    READ,
    SUBJECT,
    TERMINAL,
    UNSUPPORTED,
    WRITE,
    Recorder,
    policy,
    resolver,
    runtime,
    sandbox,
    scope,
    snapshot,
    store,
)


def _call(rt, tenant, business, capability=READ, *, payload=None, snap=None,
          pol_override=None, **kwargs):
    values = {
        "tenant_id": tenant.tenant_id,
        "business_id": business.business_id,
        "subject_id": SUBJECT,
        "actor": SUBJECT,
        "capability_id": capability,
        "policy": pol_override or policy(capability),
        "idempotency_key": kwargs.pop("key", f"key-{capability}"),
        "payload": payload,
        "snapshot": snap,
    }
    values.update(kwargs)
    return rt.call(**values)


def test_allowed_read_executes_through_the_gateway(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder({"value": "hello"})
    rt = runtime(tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id))
    outcome = _call(rt, tenant, business)
    assert outcome.executed is True
    assert len(rec.calls) == 1
    assert rec.calls[0][1].capability_id == READ
    assert outcome.invocation.result["untrusted"] is True
    assert untrusted_data(outcome) == {"value": "hello"}


def test_denied_tool_is_refused_and_never_reaches_the_adapter(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, capabilities=(READ, WRITE), adapters={READ: rec, WRITE: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(READ,)),
    )
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, WRITE)
    assert exc.value.code == TOOL_NOT_ALLOWED_CODE
    assert rec.calls == []


def test_denied_connector_is_refused_even_when_adapter_is_bound(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={CONNECTOR: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(READ,)),
    )
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, CONNECTOR, payload={"url": "https://api.example.com/send"})
    assert exc.value.code == TOOL_NOT_ALLOWED_CODE
    assert rec.calls == []


def test_unknown_and_unsupported_capabilities_are_typed_refusals(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rt = runtime(
        tmp_path, bos, snap=snapshot(tenant.tenant_id, capabilities=(READ,))
    )
    with pytest.raises(BusinessError) as unknown:
        _call(rt, tenant, business, "tool:missing")
    assert unknown.value.code == pol.CAPABILITY_UNKNOWN_CODE
    with pytest.raises(BusinessError) as unsupported:
        rt.register(UNSUPPORTED, Recorder())
    assert unsupported.value.code == CAPABILITY_RUNTIME_UNSUPPORTED_CODE


def test_no_wildcard_registration_or_call(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rt = runtime(tmp_path, bos, snap=snapshot(tenant.tenant_id))
    with pytest.raises(BusinessError) as bad:
        rt.register("*", Recorder())
    assert bad.value.code == CAPABILITY_RUNTIME_WILDCARD_CODE
    with pytest.raises(BusinessError) as call_bad:
        _call(rt, tenant, business, "*")
    assert call_bad.value.code == CAPABILITY_RUNTIME_WILDCARD_CODE


def test_policy_is_required_and_never_manufactured(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rt = runtime(tmp_path, bos, snap=snapshot(tenant.tenant_id))
    with pytest.raises(BusinessError) as exc:
        rt.call(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            subject_id=SUBJECT,
            actor=SUBJECT,
            capability_id=READ,
            policy=None,
            idempotency_key="no-policy",
        )
    assert exc.value.code == CAPABILITY_RUNTIME_POLICY_REQUIRED_CODE


def test_egress_private_and_metadata_are_blocked(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    profile = sandbox(tmp_path)
    rt = runtime(
        tmp_path, bos, adapters={BROWSER: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(BROWSER,)),
        sandbox_profile=profile,
        resolver=resolver({"api.example.com": ["127.0.0.1"]}),
    )
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, BROWSER, payload={"url": "https://api.example.com/x"})
    assert exc.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE
    assert rec.calls == []


def test_egress_metadata_ip_literal_is_blocked(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    profile = sandbox(tmp_path, egress_allowlist=("169.254.169.254",))
    rt = runtime(
        tmp_path, bos, adapters={BROWSER: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(BROWSER,)),
        sandbox_profile=profile,
    )
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, BROWSER, payload={"url": "http://169.254.169.254/latest/meta-data"})
    assert exc.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE
    assert rec.calls == []


def test_egress_allowlisted_public_host_reaches_the_adapter(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    profile = sandbox(tmp_path)
    rt = runtime(
        tmp_path, bos, adapters={BROWSER: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(BROWSER,)),
        sandbox_profile=profile,
        resolver=resolver({"api.example.com": ["93.184.216.34"]}),
    )
    _call(rt, tenant, business, BROWSER, payload={"url": "https://api.example.com/x"})
    assert len(rec.calls) == 1
    assert rec.calls[0][0].payload["url"] == "https://api.example.com/x"


def test_terminal_argv_is_validated_and_shell_string_is_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={TERMINAL: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(TERMINAL,)),
    )
    _call(rt, tenant, business, TERMINAL, payload={"argv": ["git", "status"]})
    assert rec.calls[0][0].payload["argv"] == ["git", "status"]
    with pytest.raises(BusinessError) as exc:
        _call(
            rt, tenant, business, TERMINAL, payload=["git", "status; rm -rf /"],
            key="second",
        )
    assert exc.value.code == sandbox_mod.SANDBOX_ARGUMENT_FORBIDDEN_CODE
    assert len(rec.calls) == 1


def test_files_path_escape_is_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={FILES: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(FILES,)),
    )
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, FILES, payload={"path": "../etc/passwd"})
    assert exc.value.code in (
        sandbox_mod.SANDBOX_PATH_ESCAPE_CODE,
        sandbox_mod.SANDBOX_PATH_FORBIDDEN_CODE,
    )
    assert rec.calls == []


def test_budget_cap_is_enforced_before_the_adapter(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id)
    )
    with pytest.raises(BusinessError) as exc:
        _call(
            rt, tenant, business, READ, cost_minor=10,
            pol_override=Policy(
                allowed_tools=frozenset({READ}), autonomy="L2", max_cost_minor=1
            ),
        )
    assert exc.value.code == BUDGET_EXCEEDED_CODE
    assert rec.calls == []


def test_rate_limit_is_enforced(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    cat = pol.CapabilityCatalog(
        [pol.Capability(READ, "tool", "shop", "read", risk="L1", rate_limit=1)]
    )
    from voicebridge.business.capability_runtime import CapabilityRuntime

    rec = Recorder()
    rt = CapabilityRuntime(
        bos, cat, snapshot=snapshot(tenant.tenant_id), sandbox=sandbox(tmp_path),
        adapters={READ: rec}, clock=lambda: NOW,
    )
    _call(rt, tenant, business, READ, key="rate-1")
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, READ, key="rate-2")
    assert exc.value.code == CAPABILITY_RUNTIME_RATE_LIMITED_CODE
    assert len(rec.calls) == 1


def test_approval_is_bound_to_the_exact_intent(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={WRITE: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(WRITE,)),
    )
    payload = {"listing_id": "lst-1"}
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, WRITE, payload=payload, cost_minor=5)
    assert exc.value.code == APPROVAL_REQUIRED_CODE
    assert rec.calls == []

    intent = pol.normalized_intent(
        tool=WRITE,
        resource="listing",
        action="publish",
        parameters=payload,
        currency="USD",
        amount_minor=5,
        data_class="internal",
        environment="production",
    )
    approval, _ = approvals.request(
        bos,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=f"tool:{WRITE}",
        requested_by="owner",
        idempotency_key="apr-p4-1",
        payload=intent,
        expires_at=NOW + 10_000,
    )
    approvals.decide(
        bos,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
        now=NOW,
    )
    outcome = _call(
        rt, tenant, business, WRITE, payload=payload, cost_minor=5,
        approval_id=approval.approval_id, key="write-approved",
    )
    assert outcome.executed is True
    assert len(rec.calls) == 1


def test_dry_run_never_invokes_the_adapter(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id))
    outcome = _call(rt, tenant, business, READ, dry_run=True)
    assert outcome.executed is False
    assert rec.calls == []


def test_one_adapter_failure_does_not_bypass_another(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    def boom(request, capability):
        raise RuntimeError("adapter exploded")

    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={READ: boom, BROWSER: rec},
        snap=snapshot(tenant.tenant_id, capabilities=(READ, BROWSER)),
        resolver=resolver({"api.example.com": ["93.184.216.34"]}),
    )
    with pytest.raises(BusinessError):
        _call(rt, tenant, business, READ)
    # The failed call left neither an authorization bypass nor a broken gateway.
    _call(rt, tenant, business, BROWSER, payload={"url": "https://api.example.com/x"},
          key="after-failure")
    assert len(rec.calls) == 1


def test_adapters_have_no_public_direct_call_path(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id))
    for name in ("model", "tool", "mcp", "terminal", "browser", "files",
                 "messages", "memory", "connectors", "adapter", "adapters"):
        assert not hasattr(rt, name)
    assert rt.registered_capabilities() == [READ]


def test_output_is_bounded_and_labelled_untrusted(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    deep: object = "leaf"
    for _ in range(20):
        deep = {"nested": deep}
    rec = Recorder(deep)
    rt = runtime(tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id))
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, READ)
    assert exc.value.code == CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE


def test_halted_runtime_refuses_every_capability(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id))
    receipt = rt.halt("operator test")
    assert receipt["halted"] is True
    with pytest.raises(BusinessError) as exc:
        _call(rt, tenant, business, READ)
    assert exc.value.code == CAPABILITY_RUNTIME_HALTED_CODE
    assert rec.calls == []


def test_every_declared_category_is_served_by_the_runtime(tmp_path):
    from _p4_helpers import MEMORY, MESSAGES, MCP, MODEL

    bos = store(tmp_path)
    tenant, business = scope(bos)
    capabilities = (READ, MODEL, MCP, MEMORY, MESSAGES)
    rec = Recorder({"ok": True})
    rt = runtime(
        tmp_path, bos,
        adapters={name: rec for name in capabilities},
        snap=snapshot(tenant.tenant_id, capabilities=capabilities),
    )
    for name in capabilities:
        outcome = _call(rt, tenant, business, name, key=f"cat-{name}")
        assert outcome.executed is True, name
    assert len(rec.calls) == len(capabilities)


def test_availability_reports_typed_states(tmp_path):
    bos = store(tmp_path)
    tenant, _ = scope(bos)
    rt = runtime(
        tmp_path, bos, adapters={READ: Recorder()},
        snap=snapshot(tenant.tenant_id),
    )
    assert rt.availability(READ).state == "available"
    assert rt.availability(BROWSER).state == "unavailable"
    assert rt.availability("tool:missing").state == "unknown"
    assert rt.availability(WRITE).state == "unavailable"
    register = rt.register
    with pytest.raises(BusinessError):
        register(UNSUPPORTED, Recorder())
