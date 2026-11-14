"""P2-03 — dynamic capability catalog composition and pinning (hermetic)."""
from __future__ import annotations

import pytest

from voicebridge.business import dynamic_catalog as dc
from voicebridge.business import policy as pol
from voicebridge.business.errors import BusinessError
from _p2_helpers import (
    EXPIRES,
    ISSUED,
    POLICY_REVISION,
    SUBJECT,
    TENANT,
    catalog,
    mcp_server,
    tool_records,
)


# -- composition -----------------------------------------------------------
def test_composes_capabilities_from_all_injected_sources():
    cat = catalog()
    ids = {record.capability_id for record in cat.list()}
    assert ids == {
        "tool:search",
        "tool:read",
        "mcp:docs:search",
        "plugin:etsy",
        "connector:gmail",
        "model_provider:opencode-go:deepseek-v4.1-flash",
    }
    assert {record.source for record in cat.list()} == {
        "tools",
        "mcp",
        "plugins",
        "connectors",
        "model_providers",
    }
    # A skill is a requirement, not a capability record.
    assert [row.skill_id for row in cat.requirements()] == ["research"]
    assert cat.has("skill:research") is False


def test_empty_catalog_has_no_hardcoded_provider():
    cat = dc.DynamicCapabilityCatalog()
    assert len(cat) == 0
    assert cat.list() == ()
    assert cat.requirements() == ()
    assert cat.has("model_provider:perplexity") is False
    with pytest.raises(BusinessError) as exc:
        cat.get("model_provider:perplexity")
    assert exc.value.code == dc.CATALOG_UNKNOWN_CODE


def test_skill_cannot_grant_a_capability():
    cat = catalog()
    assert "tool:search" in cat.required_capabilities_for(["research"])
    # A grant naming the skill itself is an unknown capability at compile time.
    grant = pol.Grant(
        subject=SUBJECT,
        capability="skill:research",
        resource="research",
        action="invoke",
    )
    with pytest.raises(BusinessError) as exc:
        cat.snapshot_for(
            SUBJECT,
            (grant,),
            tenant_id=TENANT,
            issued_at=ISSUED,
            expires_at=EXPIRES,
        )
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE


def test_duplicate_capability_across_sources_is_refused():
    with pytest.raises(BusinessError) as exc:
        dc.DynamicCapabilityCatalog(
            [
                dc.FunctionCatalogSource("a", lambda: tool_records()),
                dc.FunctionCatalogSource("b", lambda: tool_records()),
            ]
        ).rebuild()
    assert exc.value.code == dc.CATALOG_DUPLICATE_CODE


# -- MCP schema pinning and allowlist --------------------------------------
def test_mcp_schema_hash_covers_input_output_and_annotations():
    base = dc.mcp_capability(
        "docs", "search", input_schema={"a": 1}, output_schema={"b": 2}
    )
    same = dc.mcp_capability(
        "docs", "search", input_schema={"a": 1}, output_schema={"b": 2}
    )
    other_output = dc.mcp_capability(
        "docs", "search", input_schema={"a": 1}, output_schema={"b": 3}
    )
    other_input = dc.mcp_capability(
        "docs", "search", input_schema={"a": 9}, output_schema={"b": 2}
    )
    annotated = dc.mcp_capability(
        "docs",
        "search",
        input_schema={"a": 1},
        output_schema={"b": 2},
        annotations={"readOnlyHint": True},
    )
    assert base.schema_hash == same.schema_hash
    assert base.input_schema_hash
    assert base.output_schema_hash
    assert base.schema_hash != other_output.schema_hash
    assert base.schema_hash != other_input.schema_hash
    assert base.schema_hash != annotated.schema_hash


def test_mcp_schema_change_invalidates_the_catalog_digest():
    server = mcp_server()
    source = dc.FunctionCatalogSource(
        "mcp", lambda: dc.mcp_server_capabilities(server)
    )
    cat = dc.DynamicCapabilityCatalog([source])
    first = cat.digest
    server["tools"][0]["outputSchema"] = {"type": "object", "changed": True}
    cat.rebuild()
    assert cat.digest != first


def test_mcp_allowlist_is_enforced():
    records = dc.mcp_server_capabilities(mcp_server())
    assert [row.capability_id for row in records] == ["mcp:docs:search"]
    cat = dc.DynamicCapabilityCatalog(
        [dc.FunctionCatalogSource("mcp", lambda: records)]
    )
    assert cat.has("mcp:docs:write") is False
    with pytest.raises(BusinessError) as exc:
        cat.get("mcp:docs:write")
    assert exc.value.code == dc.CATALOG_UNKNOWN_CODE
    # An explicit build of a non-allowlisted tool is a typed refusal too.
    with pytest.raises(BusinessError) as exc:
        dc.mcp_capability("docs", "write", allowlist=("search",))
    assert exc.value.code == dc.MCP_NOT_ALLOWLISTED_CODE

    open_server = mcp_server(allowlist=[])
    assert {
        row.capability_id for row in dc.mcp_server_capabilities(open_server)
    } == {"mcp:docs:search", "mcp:docs:write"}


# -- availability ----------------------------------------------------------
def _availability_catalog():
    records = (
        dc.tool_capability("ready", version="1.0.0"),
        dc.tool_capability(
            "slow",
            version="1.0.0",
            health="degraded",
            availability="degraded",
            availability_reason="RATE_LIMITED",
        ),
        dc.tool_capability(
            "legacy",
            version="0.1.0",
            health="unavailable",
            availability="unsupported",
            availability_reason="PLATFORM_UNSUPPORTED",
        ),
        dc.tool_capability(
            "needs_key",
            version="1.0.0",
            health="unavailable",
            availability="unconfigured",
            availability_reason="MISSING_CREDENTIAL",
        ),
    )
    return dc.DynamicCapabilityCatalog(
        [dc.FunctionCatalogSource("tools", lambda: records)]
    )


def test_availability_states_have_typed_reasons():
    cat = _availability_catalog()
    assert cat.resolve("tool:ready").availability == "available"
    degraded = cat.resolve("tool:slow")
    assert degraded.availability == "degraded"
    assert degraded.availability_reason == "RATE_LIMITED"
    with pytest.raises(BusinessError) as exc:
        cat.resolve("tool:legacy")
    assert exc.value.code == dc.CATALOG_UNSUPPORTED_CODE
    with pytest.raises(BusinessError) as exc:
        cat.resolve("tool:needs_key")
    assert exc.value.code == dc.CATALOG_UNAVAILABLE_CODE
    assert cat.health("tool:slow") == "degraded"

    with pytest.raises(BusinessError) as exc:
        dc.tool_capability("bare", availability="unsupported")
    assert exc.value.code == dc.CATALOG_INVALID_CODE


# -- digest + snapshot -----------------------------------------------------
def test_digest_is_deterministic_and_version_pinned():
    first = catalog()
    second = catalog()
    assert first.digest == second.digest
    assert first.version == second.version == "1"
    first.rebuild()
    assert first.digest == second.digest
    other = catalog(catalog_version="2")
    assert other.digest != first.digest


def test_health_and_version_changes_are_visible_in_the_digest():
    records = list(tool_records())
    cat = dc.DynamicCapabilityCatalog(
        [dc.FunctionCatalogSource("tools", lambda: tuple(records))]
    )
    baseline = cat.digest
    records[0] = dc.tool_capability(
        "search", version="2.1.0", health="degraded"
    )
    cat.rebuild()
    assert cat.digest != baseline
    assert cat.health("tool:search") == "degraded"
    assert cat.versions()["tool:search"] == "2.1.0"


def test_snapshot_compiles_through_the_one_policy_engine():
    cat = catalog()
    record = cat.get("tool:search")
    grant = pol.Grant(
        subject=SUBJECT,
        capability=record.capability_id,
        resource=record.resource,
        action=record.action,
    )
    snapshot = cat.snapshot_for(
        SUBJECT,
        (grant,),
        tenant_id=TENANT,
        policy_revision=POLICY_REVISION,
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )
    assert isinstance(snapshot, pol.CapabilitySnapshot)
    assert snapshot.tool_versions == cat.versions()
    assert snapshot.policy_revision == POLICY_REVISION
    decision = pol.evaluate(
        snapshot,
        record.capability_id,
        record.resource,
        record.action,
        now=ISSUED,
    )
    assert decision.allowed is True

    # Recompiling the same pinned inputs is byte-identical.
    again = cat.snapshot_for(
        SUBJECT,
        (grant,),
        tenant_id=TENANT,
        policy_revision=POLICY_REVISION,
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )
    assert again.digest == snapshot.digest
    assert again.snapshot_id == snapshot.snapshot_id


def test_snapshot_rejects_a_grant_for_an_unregistered_capability():
    cat = catalog()
    grant = pol.Grant(
        subject=SUBJECT,
        capability="tool:missing",
        resource="missing",
        action="invoke",
    )
    with pytest.raises(BusinessError) as exc:
        cat.snapshot_for(
            SUBJECT,
            (grant,),
            tenant_id=TENANT,
            issued_at=ISSUED,
            expires_at=EXPIRES,
        )
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE
