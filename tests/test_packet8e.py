"""Packet 8E tests: website-integration onboarding + observability.

Covers: URL/origin validation, PENDING/disabled proposal creation, bounded
read-only discovery with typed lifecycle states, redaction, explicit
website→7E manifest linkage (valid/active/licensed/hash-valid/compatible),
fail-closed enable/grant gated on the active linked manifest, scope/expiry/
confirmation/dry-run/revoke/reset, restricted/unrestricted invariants,
injection resistance, no permission expansion, CLI parity, authenticated
dashboard parity, restart persistence, rollback/remove without permission
restoration, bounded discovery, static-proposal + launcher limitations, and
no private-data leakage.

Everything is synthetic/hermetic: the network probe is injected, never real.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.websites import (  # noqa: E402
    ConfirmationRequiredError,
    STATUS_AUTH_REQUIRED, STATUS_DEGRADED, STATUS_DENIED, STATUS_DISABLED,
    STATUS_FAILED, STATUS_PENDING, STATUS_READY, STATUS_REMOVED,
    STATUS_SELECTOR_DRIFT, WebsiteIntegrationError, WebsiteIntegrationStore,
    WebsiteValidationError, add_preview, blocked_host_reason,
    canonical_manifest_hash, content_type_supported, effective_permissions,
    manifest_compatible, normalize_origin, probe_page, probe_url,
    result_code_for_status, validate_manifest_link, validate_probe_result,
    validate_spdx_license, verify_manifest_hash,
)
from voicebridge.permissions import (  # noqa: E402
    MODE_RESTRICTED, MODE_UNRESTRICTED, PermissionsStore, SkillManifest,
    SkillValidationError,
)
from voicebridge.capabilities import Tier  # noqa: E402
from voicebridge.redaction import redact_text  # noqa: E402


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def _stores(tmp_path):
    return (WebsiteIntegrationStore(tmp_path / "websites.json"),
            PermissionsStore(tmp_path / "perm.json"))


def _probe(status, **kw):
    def _p(url):
        base = {"status": status, "origin": url, "title": "Example",
                "reachable": False, "auth_required": False,
                "observation_hints": ("http_get",),
                "adapter_requirement": "", "reason_code": "OK",
                "http_status": 200, "duration_s": 0.0, "bytes_read": 64,
                "limits": {}, "evidence": {}}
        base.update(kw)
        return base
    return _p


def _ready_rec(store, url="https://example.com", probe=None):
    rec = store.lookup(url) or store.add(url, requested_by="test")
    p = probe or _probe(STATUS_READY, reachable=True,
                        reason_code="OK", http_status=200)
    return store.run_discovery(rec.integration_id, requested_by="test",
                               probe=p)


def _manifest(**overrides) -> SkillManifest:
    base = dict(
        skill_id="weather.editor", version="1.0.0", name="Weather Editor",
        description="synthetic skill", source="https://example.test/skills",
        revision="abc123", license="MIT", content_hash="",
        capabilities=("web.observe.reachability", "web.observe.metadata"),
        scopes=("web",), tier=Tier.READ_ONLY, sensitivity="internal",
        approval_policy="auto", timeout_s=30.0,
        resource_limits={"max_bytes": 4096}, audit_events=("skill.run",),
        compatibility="linux", rollback="remove restores prior manifest state")
    base.update(overrides)
    if not base.get("content_hash"):
        probe = dict(base)
        probe["tier"] = (base["tier"].value
                         if hasattr(base["tier"], "value") else base["tier"])
        probe.pop("content_hash", None)
        base["content_hash"] = canonical_manifest_hash(probe)
    return SkillManifest(**base)


def _install_active(perms, **overrides) -> SkillManifest:
    man = _manifest(**overrides)
    existing = perms.skill(man.skill_id)
    if existing is None:
        perms.install_skill(man, requested_by="user", confirmed=True)
    if perms.skill(man.skill_id).status != "active":
        perms.enable_skill(man.skill_id, requested_by="user", confirmed=True)
    return perms.skill(man.skill_id).manifest


def _link(store, perms, rec, skill_id="weather.editor"):
    return store.link_manifest(rec.integration_id, skill_id=skill_id,
                               requested_by="test", confirmed=True, perms=perms)


def _ready_linked(store, perms, url="https://example.com", probe=None,
                  **manifest_overrides):
    rec = _ready_rec(store, url=url, probe=probe)
    _install_active(perms, **manifest_overrides)
    return _link(store, perms, rec)


# ---------------------------------------------------------------- validation
def test_normalize_origin_clean():
    origin, clean, name = normalize_origin("https://ExAmple.COM/a/b?x=1#f")
    assert origin == "https://example.com"
    assert clean == "https://example.com/a/b"
    assert name == "example.com"


@pytest.mark.parametrize("url", [
    "ftp://example.com", "file:///etc/passwd", "chrome://settings",
    "javascript:alert(1)", "data:text/html,x",
])
def test_invalid_schemes_rejected(url):
    with pytest.raises(WebsiteValidationError):
        normalize_origin(url)


@pytest.mark.parametrize("url", [
    "https://user:pass@example.com", "https://t@example.com",
])
def test_credentials_rejected(url):
    with pytest.raises(WebsiteValidationError):
        normalize_origin(url)


@pytest.mark.parametrize("url", [
    "https://example.com?token=abc", "https://example.com?a=1&secret=x",
    "https://example.com?api_key=zz",
])
def test_query_token_urls_rejected(url):
    with pytest.raises(WebsiteValidationError):
        normalize_origin(url)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/x", "https://localhost/x", "https://10.0.0.5/",
    "https://192.168.1.10:8080/", "https://172.16.4.1/",
])
def test_out_of_scope_private_origins_rejected(url):
    with pytest.raises(WebsiteValidationError):
        normalize_origin(url)


def test_blocked_host_reason():
    assert blocked_host_reason("localhost") == "PRIVATE_NETWORK"
    assert blocked_host_reason("127.0.0.1") == "PRIVATE_NETWORK"
    assert blocked_host_reason("10.1.2.3") == "PRIVATE_NETWORK"
    assert blocked_host_reason("192.168.0.1") == "PRIVATE_NETWORK"
    assert blocked_host_reason("foo.local") == "PRIVATE_NETWORK"
    assert blocked_host_reason("") == "EMPTY_HOST"
    assert blocked_host_reason("example.com") is None


def test_probe_blocks_private_literal_without_network():
    res = probe_page("https://127.0.0.1/admin")
    assert res["status"] == STATUS_DENIED
    assert res["reason_code"] == "PRIVATE_NETWORK"


def test_manifest_compatibility_policy():
    assert manifest_compatible("") is True
    assert manifest_compatible("linux") is True
    assert manifest_compatible("linux x86_64") is True
    assert manifest_compatible("any") is True
    assert manifest_compatible("windows") is False
    assert manifest_compatible("macos darwin") is False
    assert manifest_compatible("linux and windows") is True


# ---------------------------------------------------------------- add
def test_add_creates_pending_disabled_and_never_grants(tmp_path):
    store, perms = _stores(tmp_path)
    rec = store.add("https://example.com/docs", requested_by="test")
    assert rec.status == STATUS_PENDING
    assert rec.enabled is False
    assert rec.requested_capabilities == ()
    assert rec.grant_ids == ()
    assert rec.manifest_link == ""
    assert perms.grants() == []          # nothing was granted
    assert perms.skills() == []          # no hidden skill was created
    assert rec.origin == "https://example.com"
    assert rec.url == "https://example.com/docs"
    assert rec.display_name == "example.com"


def test_add_duplicate_rejected(tmp_path):
    store, _ = _stores(tmp_path)
    store.add("https://example.com", requested_by="test")
    with pytest.raises(WebsiteIntegrationError):
        store.add("https://example.com/other", requested_by="test")


def test_add_preview_does_not_persist(tmp_path):
    store, _ = _stores(tmp_path)
    out = add_preview("https://example.com", name="Ex")
    assert out["state"] == "preview" and out["granted"] is False
    assert store.integrations() == []


def test_add_bad_url_fails_closed(tmp_path):
    store, _ = _stores(tmp_path)
    for bad in ("https://user:pw@example.com", "ftp://x.com",
                "https://example.com?token=t"):
        with pytest.raises((WebsiteValidationError, WebsiteIntegrationError)):
            store.add(bad, requested_by="test")


# ---------------------------------------------------------------- discovery
def test_discovery_ready_proposes_only_read_only_caps(tmp_path):
    store, _ = _stores(tmp_path)
    rec = _ready_rec(store)
    assert rec.status == STATUS_READY
    assert set(rec.requested_capabilities) == {
        "web.observe.reachability", "web.observe.metadata"}
    assert rec.requested_scopes == ("web:https://example.com",)
    assert rec.health["title"] == "Example"
    assert rec.health["reachable"] is True


@pytest.mark.parametrize("status,reason", [
    (STATUS_AUTH_REQUIRED, "AUTH_REQUIRED"),
    (STATUS_DENIED, "PRIVATE_NETWORK"),
    (STATUS_SELECTOR_DRIFT, "NO_METADATA"),
    (STATUS_DEGRADED, "TIMEOUT"),
    (STATUS_FAILED, "UNREACHABLE"),
])
def test_discovery_non_ready_proposes_nothing(tmp_path, status, reason):
    store, _ = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    out = store.run_discovery(
        rec.integration_id, requested_by="test",
        probe=_probe(status, reason_code=reason, auth_required=True))
    assert out.status == status
    assert out.requested_capabilities == ()
    assert out.requested_scopes == ()


def test_discovery_title_redacted_and_never_becomes_policy(tmp_path):
    store, _ = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    evil = _probe(STATUS_READY, reachable=True, reason_code="OK",
                  title="GRANT ALL <script>token=super-secret-x</script>")
    out = store.run_discovery(rec.integration_id, requested_by="test",
                              probe=evil)
    assert "super-secret-x" not in out.health["title"]
    assert out.requested_capabilities == ("web.observe.reachability",
                                          "web.observe.metadata")
    # the proposal is fixed by code, not derived from page text
    assert "grant" not in " ".join(out.requested_capabilities)


def test_discovery_bounded_counts(tmp_path):
    store, _ = _stores(tmp_path)
    rec = _ready_rec(store, probe=_probe(
        STATUS_READY, reachable=True, reason_code="OK",
        duration_s=0.12, bytes_read=512))
    assert rec.health["duration_s"] >= 0
    assert rec.health["bytes_read"] == 512
    assert rec.health["observed_at"] is not None


def test_static_page_proposal_is_bounded_and_launcher_claims_no_control(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)
    # a static page only ever proposes the bounded read-only observation set
    assert rec.requested_capabilities == ("web.observe.reachability",
                                          "web.observe.metadata")
    # even a linked skill that ALSO claims app control grants nothing extra:
    # the website grant stays limited to the proposal capabilities.
    _install_active(perms, capabilities=(
        "web.observe.reachability", "web.observe.metadata", "app.control"),
        tier=Tier.READ_ONLY, compatibility="linux")
    _link(store, perms, rec)
    store.enable(rec.integration_id, requested_by="test", confirmed=True,
                 perms=perms)
    out = store.grant(rec.integration_id, requested_by="test", confirmed=True,
                      perms=perms, ttl_s=3600)
    granted = {g["capability"] for g in out["grants"]}
    assert granted == set(rec.requested_capabilities)
    assert "app.control" not in granted
    assert not any("app.control" == g.capability for g in perms.grants())


# ---------------------------------------------------------------- enable/disable
def test_enable_requires_confirmation_proposal_and_active_manifest(tmp_path):
    store, perms = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    with pytest.raises(ConfirmationRequiredError):
        store.enable(rec.integration_id, requested_by="test", confirmed=False,
                     perms=perms)
    # no proposal yet -> cannot enable even with confirmation
    with pytest.raises(WebsiteIntegrationError):
        store.enable(rec.integration_id, requested_by="test", confirmed=True,
                     perms=perms)
    rec = _ready_rec(store)
    # valid proposal but NO linked manifest -> fail closed
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.enable(rec.integration_id, requested_by="test", confirmed=True,
                     perms=perms)
    assert "manifest" in str(exc.value).lower()
    # link a valid active skill, then enable works and grants nothing
    linked = _ready_linked(store, perms, url="https://example.com")
    out = store.enable(linked.integration_id, requested_by="test",
                       confirmed=True, perms=perms)
    assert out.status == STATUS_READY and out.enabled is True
    assert perms.grants() == []          # enable never grants
    assert perms.skills()[0].status == "active"   # linked skill stays as-is


def test_enable_rejects_auth_required_and_failed(tmp_path):
    store, perms = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    store.run_discovery(rec.integration_id, requested_by="test",
                        probe=_probe(STATUS_AUTH_REQUIRED, reason_code="AUTH_REQUIRED"))
    with pytest.raises(WebsiteIntegrationError):
        store.enable(rec.integration_id, requested_by="test",
                     confirmed=True, perms=perms)


def test_disable_and_re_enable(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    d = store.disable(rec.integration_id, requested_by="test", confirmed=True)
    assert d.status == STATUS_DISABLED and d.enabled is False
    e = store.enable(rec.integration_id, requested_by="test",
                     confirmed=True, perms=perms)
    assert e.status == STATUS_READY and e.enabled is True


# ---------------------------------------------------------------- manifest linkage
def test_manifest_link_requires_confirmation_and_active_skill(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)
    # install + enable a valid skill (real artifact hash)
    man = _install_active(perms)
    # confirmation required
    with pytest.raises(ConfirmationRequiredError):
        store.link_manifest(rec.integration_id, skill_id=man.skill_id,
                            requested_by="test", confirmed=False, perms=perms)
    assert rec.manifest_link == ""       # still unlinked
    linked = _link(store, perms, rec)
    assert linked.manifest_link == man.skill_id
    assert linked.license == "MIT"
    assert linked.content_hash == man.content_hash        # real artifact hash
    assert linked.source_url == "https://example.test/skills"
    assert linked.source_revision == "abc123"
    assert linked.compatibility == "linux"
    # no permissions/skills change happened just by linking
    assert perms.grants() == []
    assert store.lookup(rec.integration_id).enabled is False


def test_manifest_link_fails_closed_on_bad_skill(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)

    def _corrupt(skill_id, field, value):
        perms._state["skills"][skill_id]["manifest"][field] = value

    # not installed
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="no.such.skill",
                            requested_by="test", confirmed=True, perms=perms)
    assert "SKILL_MISSING" in str(exc.value)
    assert rec.manifest_link == ""

    # installed but DISABLED
    _install_active(perms, skill_id="skill.disabled")
    perms.disable_skill("skill.disabled", requested_by="user", confirmed=True)
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="skill.disabled",
                            requested_by="test", confirmed=True, perms=perms)
    assert "SKILL_NOT_ACTIVE" in str(exc.value)

    # license emptied in the stored manifest -> 7E validator refuses to
    # materialize it (defense-in-depth => SKILL_MALFORMED)
    _install_active(perms, skill_id="skill.nolicense")
    _corrupt("skill.nolicense", "license", "")
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="skill.nolicense",
                            requested_by="test", confirmed=True, perms=perms)
    assert "SKILL_MALFORMED" in str(exc.value)

    # unknown/non-SPDX license -> LICENSE_UNKNOWN (7E only checks non-empty)
    _install_active(perms, skill_id="skill.fakelicense")
    _corrupt("skill.fakelicense", "license", "TotallyRealLicense")
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="skill.fakelicense",
                            requested_by="test", confirmed=True, perms=perms)
    assert "LICENSE_UNKNOWN" in str(exc.value)

    # malformed hash -> SKILL_MALFORMED (7E validator rejects non-hex)
    _install_active(perms, skill_id="skill.badhash")
    _corrupt("skill.badhash", "content_hash", "zzz")
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="skill.badhash",
                            requested_by="test", confirmed=True, perms=perms)
    assert "SKILL_MALFORMED" in str(exc.value)

    # WELL-SHAPED but WRONG hash -> SKILL_HASH_MISMATCH (the defect fix: a
    # syntactically valid hash is never proof of authenticity)
    _install_active(perms, skill_id="skill.wronghash")
    _corrupt("skill.wronghash", "content_hash", "1" * 64)
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="skill.wronghash",
                            requested_by="test", confirmed=True, perms=perms)
    assert "SKILL_HASH_MISMATCH" in str(exc.value)

    # incompatible platform
    _install_active(perms, skill_id="skill.win", compatibility="windows")
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="skill.win",
                            requested_by="test", confirmed=True, perms=perms)
    assert "SKILL_INCOMPATIBLE" in str(exc.value)

    # nothing was ever linked
    assert rec.manifest_link == ""
    assert perms.grants() == []


def test_manifest_link_requires_permission_store(tmp_path):
    store, _ = _stores(tmp_path)
    rec = _ready_rec(store)
    with pytest.raises(WebsiteIntegrationError):
        store.link_manifest(rec.integration_id, skill_id="weather.editor",
                            requested_by="test", confirmed=True, perms=None)


def test_validate_manifest_link_revalidates_stored_manifest(tmp_path):
    store, perms = _stores(tmp_path)
    _ready_rec(store)
    _install_active(perms)
    # corrupt the stored manifest: non-hex -> SKILL_MALFORMED on re-read
    perms._state["skills"]["weather.editor"]["manifest"]["content_hash"] = "zzz"
    with pytest.raises(WebsiteIntegrationError) as exc:
        validate_manifest_link("weather.editor", perms)
    assert "SKILL_MALFORMED" in str(exc.value)
    # well-shaped but WRONG hash -> SKILL_HASH_MISMATCH (tampering detection)
    perms._state["skills"]["weather.editor"]["manifest"]["content_hash"] = "2" * 64
    with pytest.raises(WebsiteIntegrationError) as exc:
        validate_manifest_link("weather.editor", perms)
    assert "SKILL_HASH_MISMATCH" in str(exc.value)


def test_canonical_artifact_hash_rule():
    payload = {"a": 1, "tier": "read_only",
               "caps": ["web.observe.metadata"], "nested": {"z": 2}}
    h = canonical_manifest_hash(dict(payload, content_hash="0" * 64))
    # content_hash itself is excluded from the hashed payload
    h2 = canonical_manifest_hash(dict(payload, content_hash="1" * 64))
    assert h == h2
    assert len(h) == 64
    assert verify_manifest_hash(dict(payload, content_hash=h), h) is True
    # a well-shaped but WRONG value must fail
    assert verify_manifest_hash(dict(payload, content_hash=h), "0" * 64) is False
    assert verify_manifest_hash(dict(payload, content_hash=h), "f" * 64) is False
    # malformed shapes fail
    assert verify_manifest_hash(dict(payload, content_hash=h), "zzz") is False
    assert verify_manifest_hash(dict(payload, content_hash=h), "") is False
    # order/whitespace does not matter, but a different value does
    assert canonical_manifest_hash(dict(a=1, b="x")) != canonical_manifest_hash(dict(a=1, b="y"))


@pytest.mark.parametrize("lic", [
    "MIT", "Apache-2.0", "MIT OR Apache-2.0", "(MIT AND GPL-3.0-only)",
    "GPL-3.0-or-later", "LGPL-2.1-only OR MIT", "GPL-2.0-or-later+",
])
def test_spdx_license_accepted(lic):
    assert validate_spdx_license(lic) == lic.strip()


@pytest.mark.parametrize("lic", [
    "", "NopeLicense", "MIT AND", "AND MIT", "MIT OR OR Apache-2.0",
    "(MIT", "LicenseRef-Foo", "Apache-2.0 FOO", "not-a-license",
])
def test_spdx_license_rejected(lic):
    with pytest.raises(WebsiteIntegrationError) as exc:
        validate_spdx_license(lic)
    assert str(exc.value).startswith("LICENSE_")


def test_binding_rejects_capability_scope_tier_sensitivity_approval(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)

    def _link_expect(man_overrides, needle):
        with pytest.raises(WebsiteIntegrationError) as exc:
            _install_active(perms, **man_overrides)
            _link(store, perms, rec, skill_id=man_overrides["skill_id"])
        assert needle in str(exc.value)

    # defect #1: a manifest with ONLY app.control cannot authorize web.observe.*
    _link_expect({"skill_id": "skill.appctl",
                  "capabilities": ("app.control",)},
                 "BINDING_CAPABILITY_MISMATCH")
    # editor/terminal/mail/destructive capabilities cannot authorize observation
    _link_expect({"skill_id": "skill.editor",
                  "capabilities": ("editor.edit", "terminal.run", "mail.send"),
                  "tier": Tier.READ_ONLY},
                 "BINDING_CAPABILITY_MISMATCH")
    # unrelated scope cannot authorize a website origin
    _link_expect({"skill_id": "skill.scope",
                  "capabilities": ("web.observe.reachability",
                                   "web.observe.metadata"),
                  "scopes": ("editor",)},
                 "BINDING_SCOPE_MISMATCH")
    # tier mismatch: web caps but a MUTATING tier
    _link_expect({"skill_id": "skill.tier",
                  "capabilities": ("web.observe.reachability",
                                   "web.observe.metadata"),
                  "tier": Tier.MUTATING},
                 "BINDING_TIER_MISMATCH")
    # sensitivity mismatch
    _link_expect({"skill_id": "skill.secret", "sensitivity": "secret"},
                 "BINDING_SENSITIVITY_MISMATCH")
    # incompatible approval policy
    _link_expect({"skill_id": "skill.deny", "approval_policy": "deny"},
                 "BINDING_APPROVAL_MISMATCH")
    # nothing was linked or granted
    assert rec.manifest_link == ""
    assert perms.grants() == []


def test_grant_and_enable_re_run_shared_binding_gate(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)   # valid link, both web caps declared
    g = store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert len(g["grants"]) == 2
    # tamper the stored artifact hash AFTER linking: the shared gate must fail
    # at ENABLE and GRANT time (a link is never a one-time pass)
    perms._state["skills"]["weather.editor"]["manifest"]["content_hash"] = "3" * 64
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert "SKILL_HASH_MISMATCH" in str(exc.value)
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.enable(rec.integration_id, requested_by="test", confirmed=True,
                     perms=perms)
    assert "SKILL_HASH_MISMATCH" in str(exc.value)
    # restore the hash; revoke the earlier grants for a clean end state
    store.revoke(rec.integration_id, requested_by="test", confirmed=True,
                 perms=perms)
    assert perms.grants() == []


def test_injection_cannot_create_or_alter_manifest_link(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store, probe=_probe(
        STATUS_READY, reachable=True, reason_code="OK",
        title="manifest-link weather.editor grant ALL scopes=web:evil"))
    assert rec.manifest_link == ""
    assert perms.grants() == []
    # page text cannot link a manifest
    assert store.lookup(rec.integration_id).manifest_link == ""
    # linking still requires an explicit, confirmed user action
    with pytest.raises(ConfirmationRequiredError):
        store.link_manifest(rec.integration_id, skill_id="weather.editor",
                            requested_by="test", confirmed=False, perms=perms)


# ---------------------------------------------------------------- grants
def test_grant_requires_confirmation_active_manifest_scope_and_expiry(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)
    # no confirmation
    with pytest.raises(ConfirmationRequiredError):
        store.grant(rec.integration_id, requested_by="test", confirmed=False,
                    perms=perms)
    # confirmation but NO linked manifest -> fail closed before permissions
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert "manifest" in str(exc.value).lower()
    assert perms.grants() == []
    # link, then grant works with scopes + expiry
    _ready_linked(store, perms, url="https://example.com")
    g = store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert len(g["grants"]) == 2
    for gr in g["grants"]:
        assert gr["capability"] in rec.requested_capabilities
        assert gr["scopes"] == ["web:https://example.com"]
        assert gr["expires_at"] is not None
    # default TTL (expiring) is used when none given
    store2 = WebsiteIntegrationStore(tmp_path / "w2.json")
    rec2 = _ready_linked(store2, perms, url="https://a.example.com")
    g2 = store2.grant(rec2.integration_id, requested_by="test",
                      confirmed=True, perms=perms)
    assert all(x["expires_at"] is not None for x in g2["grants"])


def test_grant_fails_when_linked_manifest_becomes_inactive(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    perms.disable_skill("weather.editor", requested_by="user", confirmed=True)
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert "SKILL_NOT_ACTIVE" in str(exc.value)
    assert perms.grants() == []


def test_grant_rejects_capabilities_outside_proposal(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    with pytest.raises(WebsiteIntegrationError):
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, capabilities=("local.volume.up",))
    with pytest.raises(WebsiteIntegrationError):
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, capabilities=("web.observe.metadata",
                                                "mail.send"))
    assert perms.grants() == []


def test_grant_rejects_without_proposal(tmp_path):
    store, perms = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    with pytest.raises(WebsiteIntegrationError):
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms)


def test_grant_rejects_bad_ttl(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    for ttl in (-1, 0, 86400 * 91):
        with pytest.raises(WebsiteIntegrationError):
            store.grant(rec.integration_id, requested_by="test", confirmed=True,
                        perms=perms, ttl_s=ttl)


def test_effective_allowed_after_grant_and_denied_before(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    eff = effective_permissions(perms, store, rec)
    assert eff["mode"] == MODE_RESTRICTED
    for cap in rec.requested_capabilities:
        assert eff["effective"][cap]["allowed"] is False
    store.grant(rec.integration_id, requested_by="test", confirmed=True,
                perms=perms, ttl_s=3600)
    eff2 = effective_permissions(perms, store, store.lookup(rec.integration_id))
    for cap in rec.requested_capabilities:
        assert eff2["effective"][cap]["allowed"] is True


def test_expired_grant_fails_closed(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    store.grant(rec.integration_id, requested_by="test", confirmed=True,
                perms=perms, ttl_s=1)
    time.sleep(1.1)
    chk = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk["grants"] and all(g["active"] is False for g in chk["grants"])
    assert chk["state"] == "DEGRADED"
    eff = effective_permissions(perms, store, store.lookup(rec.integration_id))
    for cap in rec.requested_capabilities:
        assert eff["effective"][cap]["allowed"] is False


def test_revoke_removes_linked_grants_but_not_skill(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    store.grant(rec.integration_id, requested_by="test", confirmed=True,
                perms=perms, ttl_s=3600)
    assert len(perms.grants()) == 2
    out = store.revoke(rec.integration_id, requested_by="test",
                       confirmed=True, perms=perms)
    assert len(out["revoked"]) == 2
    assert perms.grants() == []
    assert store.lookup(rec.integration_id).grant_ids == ()
    # the linked skill stays installed/active
    assert perms.skill("weather.editor").status == "active"


def test_check_reports_manifest_provenance_and_typed_states(tmp_path):
    store, perms = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    chk = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk["status"] == STATUS_PENDING and chk["manifest"] is None
    rec = _ready_rec(store)
    chk2 = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk2["status"] == STATUS_READY and chk2["state"] == "PARTIAL"
    # linked manifest provenance appears in check
    _install_active(perms)
    _link(store, perms, rec)
    chk3 = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk3["manifest"]["active"] is True
    assert chk3["manifest"]["license"] == "MIT"
    assert chk3["manifest"]["content_hash"] == _manifest().content_hash
    assert chk3["manifest"]["compatibility"] == "linux"
    assert chk3["manifest"]["source_url"] == "https://example.test/skills"
    assert chk3["manifest"]["revision"] == "abc123"
    store.grant(rec.integration_id, requested_by="test", confirmed=True,
                perms=perms, ttl_s=3600)
    chk4 = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk4["state"] == "OK"
    # a revoked/broken link becomes visible as inactive, never a pass
    perms.disable_skill("weather.editor", requested_by="user", confirmed=True)
    chk5 = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk5["manifest"]["active"] is False
    assert chk5["state"] == "DEGRADED"


# ---------------------------------------------------------------- remove/rollback
def test_remove_revokes_grants_and_rollback_does_not_restore_them(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    store.grant(rec.integration_id, requested_by="test", confirmed=True,
                perms=perms, ttl_s=3600)
    with pytest.raises(ConfirmationRequiredError):
        store.remove(rec.integration_id, requested_by="test", confirmed=False)
    out = store.remove(rec.integration_id, requested_by="test",
                       confirmed=True, perms=perms)
    assert out["removed"]["status"] == STATUS_REMOVED
    assert perms.grants() == []
    assert store.lookup(rec.integration_id) is None
    rb = store.rollback(rec.integration_id, requested_by="test", confirmed=True)
    assert rb["integration"]["status"] == STATUS_READY
    assert rb["integration"]["enabled"] is False
    assert rb["integration"]["grant_ids"] == []
    assert perms.grants() == []   # grants are never auto-restored


def test_remove_rollback_never_deactivates_or_reactivates_skill(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    assert perms.skill("weather.editor").status == "active"
    store.remove(rec.integration_id, requested_by="test", confirmed=True,
                 perms=perms)
    # remove does NOT deactivate the skill
    assert perms.skill("weather.editor").status == "active"
    store.rollback(rec.integration_id, requested_by="test", confirmed=True)
    # rollback does NOT reactivate anything or re-grant
    assert perms.skill("weather.editor").status == "active"
    assert perms.grants() == []
    assert store.lookup(rec.integration_id).enabled is False


def test_rollback_disabled_record_restores_prior_status(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    store.disable(rec.integration_id, requested_by="test", confirmed=True)
    rb = store.rollback(rec.integration_id, requested_by="test", confirmed=True)
    assert rb["integration"]["status"] == STATUS_READY


def test_rollback_requires_confirm(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    with pytest.raises(ConfirmationRequiredError):
        store.rollback(rec.integration_id, requested_by="test", confirmed=False)


# ---------------------------------------------------------------- persistence + redaction
def test_restart_persistence(tmp_path):
    path = tmp_path / "websites.json"
    store = WebsiteIntegrationStore(path)
    rec = _ready_rec(store)
    store2 = WebsiteIntegrationStore(path)
    assert len(store2.integrations()) == 1
    r2 = store2.lookup(rec.integration_id)
    assert r2.origin == "https://example.com"
    assert r2.status == STATUS_READY
    assert list(r2.requested_capabilities) == list(rec.requested_capabilities)


def test_restart_persists_manifest_link(tmp_path):
    path = tmp_path / "websites.json"
    perms_path = tmp_path / "perm.json"
    store = WebsiteIntegrationStore(path)
    perms = PermissionsStore(perms_path)
    rec = _ready_linked(store, perms)
    store2 = WebsiteIntegrationStore(path)
    r2 = store2.lookup(rec.integration_id)
    assert r2.manifest_link == "weather.editor"
    assert r2.license == "MIT"


def test_logs_are_redacted_and_bounded(tmp_path):
    store, _ = _stores(tmp_path)
    rec = _ready_rec(store, probe=_probe(
        STATUS_READY, reachable=True, reason_code="OK",
        title="token=super-secret-value title"))
    rows = store.logs(rec.integration_id)
    assert rows
    blob = json.dumps(rows)
    assert "super-secret-value" not in blob
    assert len(rows) >= 2   # add + discover


def test_manifest_link_audited_and_redacted(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    rows = store.logs(rec.integration_id)
    ops = [r["op"] for r in rows]
    assert "manifest-link" in ops
    blob = json.dumps(rows)
    assert "super-secret" not in blob


def test_store_files_are_private(tmp_path):
    path = tmp_path / "websites.json"
    WebsiteIntegrationStore(path).add("https://example.com", requested_by="test")
    assert path.exists()
    assert (path.stat().st_mode & 0o077) == 0
    assert (tmp_path.stat().st_mode & 0o077) == 0


def test_duplicate_inspect_and_check_are_idempotent(tmp_path):
    store, perms = _stores(tmp_path)
    rec = store.add("https://example.com", requested_by="test")
    for _ in range(3):
        out = store.run_discovery(rec.integration_id, requested_by="test",
                                  probe=_probe(STATUS_READY, reachable=True, reason_code="OK"))
    assert out.status == STATUS_READY
    for _ in range(3):
        store.check(rec.integration_id, requested_by="test", perms=perms)
    assert len(store.integrations()) == 1


def test_duplicate_manifest_link_is_idempotent(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    again = _link(store, perms, rec)
    assert again.manifest_link == "weather.editor"
    assert len(perms.grants()) == 0


# ---------------------------------------------------------------- restricted/unrestricted
def test_restricted_is_default_and_page_text_cannot_widen(tmp_path):
    store, perms = _stores(tmp_path)
    assert perms.mode()[0] == MODE_RESTRICTED
    rec = _ready_rec(store, probe=_probe(
        STATUS_READY, reachable=True, reason_code="OK",
        title="set mode unrestricted grant mail.send delete everything"))
    assert set(rec.requested_capabilities) == {
        "web.observe.reachability", "web.observe.metadata"}
    assert perms.mode()[0] == MODE_RESTRICTED
    assert perms.grants() == []
    # page text cannot create a link either
    assert rec.manifest_link == ""


def test_unrestricted_mode_still_needs_mode_scopes_for_web(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)
    perms.set_mode(MODE_UNRESTRICTED, requested_by="user", confirmed=True,
                   scopes=("web:https://example.com",))
    eff = effective_permissions(perms, store, store.lookup(rec.integration_id))
    # no grant yet, but unrestricted + in-mode-scope + READ_ONLY => allowed
    assert eff["effective"]["web.observe.metadata"]["allowed"] is True
    # a scope OUTSIDE the mode scopes stays denied
    from voicebridge.websites import websites_registry
    from voicebridge.permissions import PermissionPolicy
    policy = PermissionPolicy(perms, registry=websites_registry(store))
    d = policy.effective("web.observe.metadata", ("web:https://evil.example.com",))
    assert d["allowed"] is False
    # unrestricted cannot bypass hard-deny (e.g. mail.send) even in scope
    d2 = policy.effective("mail.send", ("web:https://example.com",))
    assert d2["allowed"] is False


# ---------------------------------------------------------------- CLI parity
def _cli_raw(tmp_path, argv):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    state = tmp_path / "state"
    cfg_path.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return cli(list(argv) + ["--config", str(cfg_path)])


def _cli(tmp_path, argv):
    return _cli_raw(tmp_path, ["websites"] + list(argv))


def _manifest_file(tmp_path, **overrides):
    f = tmp_path / "skill.json"
    f.write_text(json.dumps(_manifest(**overrides).to_dict()))
    return f


def test_cli_add_dry_run_persists_nothing(tmp_path, capsys):
    rc = _cli(tmp_path, ["add", "https://example.com", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["state"] == "preview" and out["granted"] is False
    rc = _cli(tmp_path, ["list"])
    assert json.loads(capsys.readouterr().out)["integrations"] == []


def test_cli_add_dry_run_is_side_effect_free(tmp_path, capsys, monkeypatch):
    """`websites add --dry-run` is genuinely side-effect-free: no DNS lookup,
    no network call, no websites.json/permissions.json/backup/audit write, no
    grant, and no hidden skill."""
    import socket as _socket
    import urllib.request as _urlreq
    import voicebridge.websites as _W

    def _boom(*a, **k):
        raise AssertionError("dry-run must not perform a DNS lookup")

    def _net_boom(*a, **k):
        raise AssertionError("dry-run must not touch the network")

    monkeypatch.setattr(_socket, "getaddrinfo", _boom)
    monkeypatch.setattr(_urlreq, "urlopen", _net_boom)
    monkeypatch.setattr(_W, "urlopen", _net_boom)

    rc = _cli(tmp_path, ["add", "https://example.com", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["state"] == "preview"
    assert out["would_add"] == "https://example.com"
    assert out["granted"] is False

    state = tmp_path / "state"
    written = sorted(p.name for p in state.glob("*"))
    assert "websites.json" not in written, written
    assert "permissions.json" not in written, written
    assert not [p for p in state.glob("*.bak")], written
    ev = state / "events.jsonl"
    if ev.exists():
        blob = ev.read_text()
        assert "websites." not in blob, "dry-run wrote websites audit events"


def test_cli_add_preview_rejects_private_origins_without_dns(
        tmp_path, capsys, monkeypatch):
    """Even without DNS (patched to explode) a dry-run preview still rejects
    literal private/loopback origins — no resolution needed for that check."""
    import socket as _socket

    monkeypatch.setattr(_socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("unexpected DNS")))
    rc = _cli(tmp_path, ["add", "http://localhost:8080/x", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed"
    assert "private/out-of-scope" in out["detail"]
    rc = _cli(tmp_path, ["add", "https://127.0.0.1/", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed"


def test_cli_config_before_and_after_subcommand_use_effective_state(tmp_path,
                                                                    capsys):
    """Regression: `--config X` before the subcommand must NOT be silently
    dropped by the subparser default, and `websites list` must expose the
    effective state path in its output."""
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    state = tmp_path / "state"
    cfg_path.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")

    # --config BEFORE the subcommand: parsed at top level, must survive.
    rc = cli(["--config", str(cfg_path), "websites", "list"])
    out_before = json.loads(capsys.readouterr().out)
    assert rc == 0 and out_before["state"] == "OK"
    assert out_before["state_path"] == str(state / "websites.json")

    # --config AFTER the subcommand: still works (subparser path).
    rc = cli(["websites", "list", "--config", str(cfg_path)])
    out_after = json.loads(capsys.readouterr().out)
    assert rc == 0 and out_after["state"] == "OK"
    assert out_after["state_path"] == str(state / "websites.json")

    # A real add lands in the effective (tmp) state, proving the config won.
    rc = cli(["--config", str(cfg_path), "websites", "add",
              "https://configtest.example"])
    assert rc == 0
    capsys.readouterr()
    assert (state / "websites.json").exists()
    blob = (state / "websites.json").read_text()
    assert "configtest.example" in blob
    assert "www.g2g.com" not in blob  # never the default user store


def test_cli_add_rejects_bad_url(tmp_path, capsys):
    rc = _cli(tmp_path, ["add", "https://user:pass@example.com"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed"


def test_cli_add_list_show_inspect_offline(tmp_path, capsys):
    assert _cli(tmp_path, ["add", "https://example.com", "--name", "Ex"]) == 0
    capsys.readouterr()
    rc = _cli(tmp_path, ["list"])
    listing = json.loads(capsys.readouterr().out)
    assert rc == 0 and listing["integrations"][0]["status"] == STATUS_PENDING
    iid = listing["integrations"][0]["integration_id"]
    rc = _cli(tmp_path, ["inspect", iid, "--no-network"])
    insp = json.loads(capsys.readouterr().out)
    assert rc == 0 and insp["status"] == STATUS_FAILED
    assert insp["reason"] == "NETWORK_DISABLED"
    rc = _cli(tmp_path, ["show", iid])
    assert rc == 0 and json.loads(capsys.readouterr().out)["state"] == "OK"


def test_cli_mutations_require_confirm(tmp_path, capsys):
    assert _cli(tmp_path, ["add", "https://example.com"]) == 0
    capsys.readouterr()
    iid = _first_id(tmp_path, capsys)
    rc = _cli(tmp_path, ["remove", iid])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["code"] == "CONFIRMATION_REQUIRED"


def _first_id(tmp_path, capsys):
    _cli(tmp_path, ["list"])
    listing = json.loads(capsys.readouterr().out)
    return listing["integrations"][0]["integration_id"]


def test_cli_manifest_link_cycle(tmp_path, capsys):
    mfile = _manifest_file(tmp_path)
    # install + enable the skill through the SAME backend
    assert _cli_raw(tmp_path, ["skills", "install", str(mfile), "--confirm"]) == 0
    capsys.readouterr()
    assert _cli_raw(tmp_path, ["skills", "enable", "weather.editor",
                               "--confirm"]) == 0
    capsys.readouterr()
    # add a website
    assert _cli(tmp_path, ["add", "https://example.com"]) == 0
    capsys.readouterr()
    iid = _first_id(tmp_path, capsys)
    # dry-run link preview, no persist
    rc = _cli(tmp_path, ["manifest-link", iid, "weather.editor", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["state"] == "preview"
    # link without confirm -> denied
    rc = _cli(tmp_path, ["manifest-link", iid, "weather.editor"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["code"] == "CONFIRMATION_REQUIRED"
    # link a missing skill -> fail closed
    rc = _cli(tmp_path, ["manifest-link", iid, "no.such.skill", "--confirm"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and "SKILL_MISSING" in out["detail"]
    # valid link
    rc = _cli(tmp_path, ["manifest-link", iid, "weather.editor", "--confirm"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["manifest_link"] == "weather.editor"
    capsys.readouterr()
    # check now reports the manifest
    rc = _cli(tmp_path, ["check", iid])
    chk = json.loads(capsys.readouterr().out)
    assert rc == 0 and chk["manifest"]["active"] is True
    # the record is still PENDING (never inspected via CLI) -> OK; once it
    # is READY and granted it would be OK, READY-and-ungranted is PARTIAL
    assert chk["state"] in ("OK", "PARTIAL")
    # grant (needs a READY record, which inspect --no-network can't reach); at
    # minimum verify grant without a READY proposal still fails closed
    rc = _cli(tmp_path, ["grant", iid, "--confirm", "--ttl", "3600"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed"


def test_cli_grant_revoke_remove_rollback_cycle(tmp_path, capsys):
    assert _cli(tmp_path, ["add", "https://example.com"]) == 0
    capsys.readouterr()
    iid = _first_id(tmp_path, capsys)
    _cli(tmp_path, ["inspect", iid, "--no-network"])
    capsys.readouterr()
    # inspect offline -> FAILED, so grant must fail closed
    rc = _cli(tmp_path, ["grant", iid, "--confirm", "--ttl", "3600"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed"
    # removal still works (with confirmation)
    rc = _cli(tmp_path, ["remove", iid, "--confirm"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["removed"]["status"] == STATUS_REMOVED
    rc = _cli(tmp_path, ["rollback", iid, "--confirm"])
    assert rc == 0
    rb = json.loads(capsys.readouterr().out)
    # rollback restores the prior (honest) lifecycle state; grants never
    # auto-restore. The offline inspect left the record FAILED.
    assert rb["integration"]["status"] in (
        STATUS_PENDING, STATUS_READY, STATUS_FAILED)


def test_cli_probe_requires_live_and_validates(tmp_path, capsys):
    # explicit --live is mandatory (fail closed)
    rc = _cli(tmp_path, ["probe", "https://example.com"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["state"] == "denied" and out["code"] == "LIVE_REQUIRED"
    # a blocked/private URL with --live is rejected before any network
    rc = _cli(tmp_path, ["probe", "https://127.0.0.1/admin", "--live"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed"
    # human output for the denied path
    rc = _cli(tmp_path, ["probe", "https://example.com", "--human"])
    out = capsys.readouterr().out
    assert rc == 3 and "LIVE_REQUIRED" in out


def test_probe_url_typed_states_redacted_no_persist(tmp_path):
    store, _ = _stores(tmp_path)
    before = len(store.integrations())
    for status, reason in [
        (STATUS_READY, "OK"), (STATUS_AUTH_REQUIRED, "AUTH_REQUIRED"),
        (STATUS_DENIED, "PRIVATE_NETWORK"), (STATUS_SELECTOR_DRIFT, "NO_METADATA"),
        (STATUS_DEGRADED, "TIMEOUT"), (STATUS_FAILED, "UNREACHABLE"),
    ]:
        res = probe_url("https://example.com", probe=_probe(
            status, reason_code=reason, reachable=(status == STATUS_READY),
            title="token=super-secret-value title"))
        assert res.status == status
        assert "super-secret-value" not in res.title
    # probe_url persists nothing
    assert len(store.integrations()) == before
    # probe_page rejects a private literal without any network (SSRF fail-closed)
    assert probe_page("https://127.0.0.1/x")["status"] == STATUS_DENIED
    # a probe returning a non-typed status fails closed to FAILED
    bad = probe_url("https://example.com", probe=lambda _u: {"status": "WEIRD"})
    assert bad.status == STATUS_FAILED


def test_malformed_urls_fail_closed_typed(tmp_path, capsys):
    # bad port / unbalanced bracket / non-numeric port must be a TYPED failure,
    # never an uncaught ValueError traceback
    for bad in ("https://example.com:abc", "https://example.com:99999",
                "https://[::1", "https://exa mple.com/x"):
        with pytest.raises(WebsiteValidationError):
            normalize_origin(bad)
        rc = _cli(tmp_path, ["add", bad])
        out = json.loads(capsys.readouterr().out)
        assert rc == 2 and out["state"] == "failed", bad
        capsys.readouterr()


def test_malformed_fixture_numeric_field_fails_typed(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"status": "READY", "http_status": "not-a-number"}))
    assert _cli(tmp_path, ["add", "https://example.com"]) == 0
    capsys.readouterr()
    iid = _first_id(tmp_path, capsys)
    rc = _cli(tmp_path, ["inspect", iid, "--fixture", str(bad)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "failed" and "fixture invalid" in out["detail"]


def test_probe_result_vocabulary():
    assert result_code_for_status(STATUS_READY) == "SUCCESS"
    assert result_code_for_status(STATUS_AUTH_REQUIRED) == "AUTH_REQUIRED"
    assert result_code_for_status(STATUS_SELECTOR_DRIFT) == "PARTIAL"
    assert result_code_for_status(STATUS_DEGRADED) == "DEGRADED"
    assert result_code_for_status(STATUS_DENIED) == "FAILED"
    assert result_code_for_status(STATUS_FAILED, "UNREACHABLE") == "UNAVAILABLE"
    assert result_code_for_status(STATUS_FAILED, "TIMEOUT") == "UNAVAILABLE"
    assert result_code_for_status(STATUS_FAILED, "NETWORK_DISABLED") == "UNAVAILABLE"
    assert result_code_for_status(STATUS_FAILED, "TLS_ERROR") == "FAILED"
    assert result_code_for_status("WEIRD") == "UNAVAILABLE"
    # a probe that RAISES fails closed to FAILED / PROBE_UNAVAILABLE (no crash)
    res = probe_url("https://example.com",
                    probe=lambda _u: (_ for _ in ()).throw(RuntimeError("boom")))
    assert res.status == STATUS_FAILED
    assert res.reason_code == "PROBE_UNAVAILABLE"
    assert result_code_for_status(res.status, res.reason_code) == "UNAVAILABLE"


def test_validate_probe_result_bounds_and_rejects():
    with pytest.raises(WebsiteValidationError):
        validate_probe_result("not-a-dict")
    with pytest.raises(WebsiteValidationError):
        validate_probe_result({"status": "WEIRD"})
    ok = validate_probe_result({
        "status": "READY", "reachable": True, "reason_code": "OK",
        "title": "x" * 500 + " token=super-secret", "http_status": 200,
        "observation_hints": ["http_get"]})
    assert ok["status"] == STATUS_READY
    assert len(ok["title"]) <= 120
    assert "super-secret" not in ok["title"]
    assert ok["evidence"]["fixture"] is True


def test_artifact_byte_hash_optional_and_verified(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)
    _install_active(perms)
    # no artifact supplied -> no artifact claim is made
    linked = _link(store, perms, rec)
    assert linked.artifact_sha256 == ""
    assert linked.artifact_path == ""
    art = tmp_path / "artifact.bin"
    art.write_bytes(b"fixture payload bytes")
    correct = hashlib.sha256(b"fixture payload bytes").hexdigest()
    # correct artifact bytes -> verified and recorded as provenance
    ok = store.link_manifest(rec.integration_id, skill_id="weather.editor",
                             requested_by="test", confirmed=True, perms=perms,
                             artifact_path=str(art), artifact_sha256=correct)
    assert ok.artifact_sha256 == correct
    assert ok.artifact_path != ""
    # the local path is NEVER serialized
    assert ok.to_dict()["artifact_path"] == "[local artifact]"
    # well-shaped but WRONG bytes hash
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="weather.editor",
                            requested_by="test", confirmed=True, perms=perms,
                            artifact_path=str(art), artifact_sha256="1" * 64)
    assert "SKILL_ARTIFACT_HASH_MISMATCH" in str(exc.value)
    # missing artifact file
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="weather.editor",
                            requested_by="test", confirmed=True, perms=perms,
                            artifact_path=str(tmp_path / "nope.bin"),
                            artifact_sha256=correct)
    assert "SKILL_ARTIFACT_MISSING" in str(exc.value)
    # sha256 without a path -> artifact cannot be located
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="weather.editor",
                            requested_by="test", confirmed=True, perms=perms,
                            artifact_sha256=correct)
    assert "SKILL_ARTIFACT_MISSING" in str(exc.value)
    # malformed sha256
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.link_manifest(rec.integration_id, skill_id="weather.editor",
                            requested_by="test", confirmed=True, perms=perms,
                            artifact_path=str(art), artifact_sha256="zzz")
    assert "SKILL_ARTIFACT_HASH_INVALID" in str(exc.value)


def test_artifact_reverified_at_use_gates(tmp_path):
    """The artifact bytes are RE-verified at every enable/grant gate.

    A deleted (moved) or modified (tampered) artifact after linking must fail
    closed with typed MISSING / TAMPERED states.
    """
    store, perms = _stores(tmp_path)
    rec = _ready_linked(store, perms)   # linked WITHOUT artifact -> gate skips
    store.enable(rec.integration_id, requested_by="test", confirmed=True,
                 perms=perms)
    store.grant(rec.integration_id, requested_by="test", confirmed=True,
                perms=perms, ttl_s=3600)
    store.revoke(rec.integration_id, requested_by="test", confirmed=True,
                 perms=perms)
    # now link WITH an artifact (needs re-link with artifact args)
    art = tmp_path / "artifact.bin"
    art.write_bytes(b"verified artifact bytes")
    correct = hashlib.sha256(b"verified artifact bytes").hexdigest()
    store.link_manifest(rec.integration_id, skill_id="weather.editor",
                        requested_by="test", confirmed=True, perms=perms,
                        artifact_path=str(art), artifact_sha256=correct)
    # while present, enable/grant succeed
    store.enable(rec.integration_id, requested_by="test", confirmed=True,
                 perms=perms)
    # DELETE the artifact -> gate fails closed (MISSING), grant AND enable
    art.unlink()
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert "SKILL_ARTIFACT_MISSING" in str(exc.value)
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.enable(rec.integration_id, requested_by="test", confirmed=True,
                     perms=perms)
    assert "SKILL_ARTIFACT_MISSING" in str(exc.value)
    assert perms.grants() == []
    # check reports the artifact as missing
    chk = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk["manifest"]["artifact"] == "missing"
    # TAMPER: recreate the artifact with DIFFERENT bytes at the same path
    art.write_bytes(b"tampered artifact bytes")
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert "SKILL_ARTIFACT_TAMPERED" in str(exc.value)
    chk = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk["manifest"]["artifact"] == "tampered"
    assert perms.grants() == []


def test_probe_url_https_only(tmp_path):
    from voicebridge.websites import probe_url as _p
    with pytest.raises(WebsiteValidationError):
        _p("http://example.com")
    # https with credentials / query tokens / fragments still rejected
    with pytest.raises(WebsiteValidationError):
        _p("https://user:pass@example.com")
    with pytest.raises(WebsiteValidationError):
        _p("https://example.com?token=abc")
    with pytest.raises(WebsiteValidationError):
        _p("https://example.com/#fragment")
    with pytest.raises(WebsiteValidationError):
        _p("https://127.0.0.1/x")


def test_add_drops_fragments_but_probe_rejects(tmp_path):
    # add/inspect store the URL WITHOUT the fragment (never sent to the server)
    origin, clean, _name = normalize_origin("https://example.com/a#secret-frag")
    assert clean == "https://example.com/a"
    assert "#secret-frag" not in clean
    # the live probe, however, rejects fragment URLs (fail closed, conservative)
    with pytest.raises(WebsiteValidationError):
        probe_url("https://example.com/a#secret-frag")


def test_artifact_moved_after_link_fails_closed(tmp_path):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)
    _install_active(perms)
    art = tmp_path / "artifact.bin"
    art.write_bytes(b"artifact bytes")
    sha = hashlib.sha256(b"artifact bytes").hexdigest()
    store.link_manifest(rec.integration_id, skill_id="weather.editor",
                        requested_by="test", confirmed=True, perms=perms,
                        artifact_path=str(art), artifact_sha256=sha)
    # MOVED: rename the artifact to another path -> the recorded path is gone
    art.rename(tmp_path / "moved.bin")
    with pytest.raises(WebsiteIntegrationError) as exc:
        store.grant(rec.integration_id, requested_by="test", confirmed=True,
                    perms=perms, ttl_s=3600)
    assert "SKILL_ARTIFACT_MISSING" in str(exc.value)
    chk = store.check(rec.integration_id, requested_by="test", perms=perms)
    assert chk["manifest"]["artifact"] == "missing"
    assert perms.grants() == []


def test_artifact_unreadable_fails_closed(tmp_path, monkeypatch):
    store, perms = _stores(tmp_path)
    rec = _ready_rec(store)
    _install_active(perms)
    art = tmp_path / "artifact.bin"
    art.write_bytes(b"artifact bytes")
    sha = hashlib.sha256(b"artifact bytes").hexdigest()
    store.link_manifest(rec.integration_id, skill_id="weather.editor",
                        requested_by="test", confirmed=True, perms=perms,
                        artifact_path=str(art), artifact_sha256=sha)
    # UNREADABLE: read_bytes raises OSError (e.g. permission/permission-loss)
    import voicebridge.websites as W
    real_read = Path.read_bytes
    def _boom(self, *a, **k):
        raise OSError("permission denied")
    monkeypatch.setattr(Path, "read_bytes", _boom)
    try:
        with pytest.raises(WebsiteIntegrationError) as exc:
            store.grant(rec.integration_id, requested_by="test",
                        confirmed=True, perms=perms, ttl_s=3600)
    finally:
        monkeypatch.setattr(Path, "read_bytes", real_read)
    assert "SKILL_ARTIFACT_UNREADABLE" in str(exc.value)
    assert perms.grants() == []


def test_no_playwright_or_chromium_mutation():
    """Packet 8E never launches a Playwright personal profile and never mutates
    Chromium: the module imports no browser automation runtime and contains no
    account/cookie/credential handling (only the docstring MAY name Playwright
    as something it must not do)."""
    import ast as _ast
    import voicebridge.websites as W
    tree = _ast.parse(Path(W.__file__).read_text())
    imported = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("playwright", "puppeteer", "selenium"):
        assert forbidden not in imported, \
            f"8E module imports forbidden runtime {forbidden!r}"
    # and no cookie/credential/account handling exists anywhere in the module code
    src = Path(W.__file__).read_text()
    for forbidden in ("get_cookies", "http.cookiejar", "sessions["):
        assert forbidden not in src, f"8E module references forbidden {forbidden!r}"


def test_content_type_supported_policy():
    from voicebridge.websites import content_type_supported as cts
    assert cts("text/html; charset=utf-8") is True
    assert cts("text/plain") is True
    assert cts("application/xhtml+xml") is True
    assert cts("") is True
    assert cts("application/pdf") is False
    assert cts("image/png") is False
    assert cts("application/octet-stream") is False


def _fixture_file(tmp_path):
    f = tmp_path / "probe.json"
    f.write_text(json.dumps({
        "status": "READY", "reachable": True, "title": "Synthetic Example",
        "reason_code": "OK", "http_status": 200,
        "observation_hints": ["http_get", "title_metadata"],
        "adapter_requirement": "none", "duration_s": 0.01, "bytes_read": 64,
        "limits": {}}))
    return f


def test_operator_fixture_smoke_end_to_end(tmp_path, capsys):
    """Operator-authorized READY-record smoke path through the REAL backend.

    Uses only synthetic non-secret data: a fixture probe JSON + a real-hash
    skill manifest + a real temporary artifact. Exercises manifest linkage,
    permission checks, scope, expiry, confirmation, audit, grant, revoke, and
    rollback via the actual CLI.
    """
    mfile = _manifest_file(tmp_path)
    fix = _fixture_file(tmp_path)
    art = tmp_path / "artifact.bin"
    art.write_bytes(b"smoke artifact bytes")
    art_sha = hashlib.sha256(b"smoke artifact bytes").hexdigest()

    assert _cli_raw(tmp_path, ["skills", "install", str(mfile), "--confirm"]) == 0
    capsys.readouterr()
    assert _cli_raw(tmp_path, ["skills", "enable", "weather.editor", "--confirm"]) == 0
    capsys.readouterr()
    # add -> PENDING, disabled, grant-free
    assert _cli(tmp_path, ["add", "https://example.com", "--name", "Smoke"]) == 0
    capsys.readouterr()
    iid = _first_id(tmp_path, capsys)
    # operator-authorized synthetic discovery -> READY, probe_source=fixture
    rc = _cli(tmp_path, ["inspect", iid, "--fixture", str(fix)])
    insp = json.loads(capsys.readouterr().out)
    assert rc == 0 and insp["status"] == STATUS_READY
    assert insp["probe_source"] == "fixture"
    assert insp["integration"]["health"]["probe"] == "fixture"
    # grant BEFORE manifest-link must fail closed
    rc = _cli(tmp_path, ["grant", iid, "--confirm", "--ttl", "3600"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and "manifest" in out["detail"].lower()
    # manifest-link with artifact-byte verification
    rc = _cli(tmp_path, ["manifest-link", iid, "weather.editor",
                         "--artifact", str(art), "--artifact-sha256", art_sha,
                         "--confirm"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["manifest_link"] == "weather.editor"
    assert out["artifact_sha256"] == art_sha
    capsys.readouterr()
    # human-readable output
    rc = _cli(tmp_path, ["check", iid, "--human"])
    h = capsys.readouterr().out
    assert rc == 0 and "PARTIAL" in h and "manifest=active" in h
    # enable (confirmation required)
    rc = _cli(tmp_path, ["enable", iid])
    assert json.loads(capsys.readouterr().out)["code"] == "CONFIRMATION_REQUIRED"
    rc = _cli(tmp_path, ["enable", iid, "--confirm"])
    assert rc == 0
    capsys.readouterr()
    # grant: confirm required; then success with expiry
    rc = _cli(tmp_path, ["grant", iid])
    assert json.loads(capsys.readouterr().out)["code"] == "CONFIRMATION_REQUIRED"
    rc = _cli(tmp_path, ["grant", iid, "--confirm", "--ttl", "3600"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and len(out["grants"]) == 2
    assert all(g["expires_at"] is not None for g in out["grants"])
    # check -> OK with active grants
    rc = _cli(tmp_path, ["check", iid])
    chk = json.loads(capsys.readouterr().out)
    assert rc == 0 and chk["state"] == "OK"
    assert all(g["active"] for g in chk["grants"])
    # revoke -> grants gone; check -> PARTIAL
    rc = _cli(tmp_path, ["revoke", iid, "--confirm"])
    assert rc == 0 and len(json.loads(capsys.readouterr().out)["revoked"]) == 2
    rc = _cli(tmp_path, ["check", iid])
    assert json.loads(capsys.readouterr().out)["state"] == "PARTIAL"
    capsys.readouterr()
    # audit log records the full lifecycle (no secrets)
    rc = _cli(tmp_path, ["logs", iid])
    rows = json.loads(capsys.readouterr().out)["logs"]
    ops = [r["op"] for r in rows]
    for op in ("add", "discover", "manifest-link", "enable", "grant",
               "revoke"):
        assert op in ops
    blob = json.dumps(rows)
    assert "super-secret" not in blob
    # disable -> remove -> rollback (grants never auto-restored)
    rc = _cli(tmp_path, ["disable", iid, "--confirm"])
    assert rc == 0
    capsys.readouterr()
    rc = _cli(tmp_path, ["remove", iid, "--confirm"])
    assert rc == 0
    capsys.readouterr()
    rc = _cli(tmp_path, ["rollback", iid, "--confirm"])
    rb = json.loads(capsys.readouterr().out)
    assert rc == 0 and rb["integration"]["status"] in (STATUS_READY,
                                                        STATUS_DISABLED)
    assert rb["integration"]["grant_ids"] == []
    # restart persistence: reopen the store and confirm the record survived
    from voicebridge.websites import website_integrations_store
    from voicebridge.config import Config
    cfg = Config.load(tmp_path / "voice.yaml")
    store = website_integrations_store(cfg)
    rec = store.lookup(iid)
    assert rec is not None
    assert rec.manifest_link == "weather.editor"
    assert rec.artifact_sha256 == art_sha


# ---------------------------------------------------------------- Talk auth/JSON (vb.js)
def _run_vbjs(scenario: str) -> str:
    """Run frontend/vb.js in Node with a stubbed browser + fetch and execute a
    scenario against window.vbApi, returning the printed JSON line."""
    import subprocess as _sp
    vbjs = (REPO_ROOT / "frontend" / "vb.js").read_text()
    script = r"""
const fs = require('fs');
global.window = global;
global.document = {
  getElementById: () => null,
  createElement: () => ({
    style: {}, id: '', className: '', textContent: '',
    firstChild: null,
    appendChild(){}, removeChild(){}, setAttribute(){}, addEventListener(){},
  }),
  createTextNode: () => ({ nodeType: 3 }),
  body: { insertBefore(){}, appendChild(){}, firstChild: null },
};
global.localStorage = {
  _t: 'test-token',
  getItem(k){ return k === 'vdb_token' ? this._t : ''; },
  setItem(){},
};
""" + vbjs + r"""
""" + scenario + r"""
"""
    import tempfile, os as _os
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False) as f:
        f.write(script); path = f.name
    r = _sp.run(["node", path], capture_output=True, text=True, timeout=60,
                cwd=str(REPO_ROOT))
    _os.unlink(path)
    assert r.returncode == 0, "node harness failed:\n" + r.stdout + r.stderr
    return r.stdout.strip().splitlines()[-1]


def test_vb_api_plain_text_401_never_reaches_json_parse():
    scenario = r"""
(async () => {
  global.fetch = async () => {
    return { status: 401, ok: false,
      headers: { get: () => 'text/plain' },
      json: async () => { throw new Error('JSON.parse called on 401!'); },
      text: async () => 'unauthorized' };
  };
  const r = await window.vbApi('/api/status');
  console.log(JSON.stringify({ status: r.status, authRequired: r.authRequired,
    ok: r.ok, error: r.error, body: r.body }));
})();
"""
    out = json.loads(_run_vbjs(scenario))
    assert out["status"] == 401
    assert out["authRequired"] is True
    assert out["ok"] is False
    assert out["body"] is None          # never JSON.parse'd, never thrown
    assert out["error"] == "auth"


def test_vb_api_authenticated_talk_poll_works_and_token_in_header():
    scenario = r"""
(async () => {
  let seen = [];
  const pcm = new Uint8Array([1,2,3,4]).buffer;
  global.fetch = async (url, opts) => {
    seen.push({ url: url, token: (opts.headers || {})['X-Api-Token'] || '',
                contentType: (opts.headers || {})['Content-Type'] || '',
                bodyIsPcm: opts.body === pcm });
    if (url.endsWith('/api/talk'))
      return { status: 200, ok: true, headers: { get: () => 'application/json' },
               json: async () => ({ request_id: 'rid1' }) };
    if (url.includes('/api/result/'))
      return { status: 200, ok: true, headers: { get: () => 'application/json' },
               json: async () => ({ status: 'done', audio_b64: 'AAAA' }) };
    return { status: 200, ok: true, headers: { get: () => 'application/json' },
             json: async () => ({ bridge: 'up' }) };
  };
  const t = await window.vbApi('/api/talk',
    { method: 'POST', headers: window.vbHeaders({ 'Content-Type': 'application/octet-stream' }), body: pcm });
  const p = await window.vbApi('/api/result/rid1');
  console.log(JSON.stringify({ t: {ok:t.ok, rid:t.body && t.body.request_id},
    p: {ok:p.ok, done:p.body && p.body.status},
    headers: seen.filter(s=>s.url.endsWith('/api/talk'))[0] }));
})();
"""
    out = json.loads(_run_vbjs(scenario))
    assert out["t"]["ok"] is True and out["t"]["rid"] == "rid1"
    assert out["p"]["ok"] is True and out["p"]["done"] == "done"
    talk = out["headers"]
    assert talk["token"] == "test-token"          # X-Api-Token header present
    assert talk["contentType"] == "application/octet-stream"   # binary preserved
    assert talk["bodyIsPcm"] is True


def test_vb_api_non_json_and_error_statuses_never_throw():
    scenario = r"""
(async () => {
  const cases = [
    { status: 200, ct: 'text/plain',  text: 'hello' },
    { status: 403, ct: 'text/plain',  text: 'forbidden' },
    { status: 404, ct: 'text/plain',  text: 'not found' },
    { status: 500, ct: 'text/html',   text: '<html>oops</html>' },
    { status: 429, ct: 'application/json', json: { retry_after: 30 } },
    { status: 409, ct: 'application/json', json: { error: 'conflict' } },
  ];
  let i = 0;
  global.fetch = async () => {
    const c = cases[i++];
    const headers = { get: () => c.ct };
    const json = async () => c.json;
    const text = async () => c.text;
    return { status: c.status, ok: c.status >= 200 && c.status < 300,
             headers, json, text };
  };
  const out = [];
  for (const c of cases) {
    const r = await window.vbApi('/api/x');
    out.push({ status: r.status, ok: r.ok, error: r.error,
               isRaw: !!(r.body && r.body.raw), raw: r.body && r.body.raw });
  }
  console.log(JSON.stringify(out));
})();
"""
    out = json.loads(_run_vbjs(scenario))
    assert out[0]["status"] == 200 and out[0]["ok"] is True and out[0]["isRaw"] is True
    assert out[1]["status"] == 403 and out[1]["ok"] is False and out[1]["isRaw"] is True
    assert out[2]["status"] == 404 and out[2]["ok"] is False and out[2]["isRaw"] is True
    assert out[3]["status"] == 500 and out[3]["ok"] is False and out[3]["isRaw"] is True
    assert out[4]["status"] == 429 and out[4]["ok"] is False
    assert out[5]["status"] == 409 and out[5]["ok"] is False


def test_vb_api_network_timeout_is_typed():
    scenario = r"""
(async () => {
  global.fetch = async () => { throw new TypeError('Failed to fetch'); };
  const r = await window.vbApi('/api/status');
  console.log(JSON.stringify({ status: r.status, ok: r.ok, error: r.error,
    authRequired: r.authRequired, body: r.body }));
})();
"""
    out = json.loads(_run_vbjs(scenario))
    assert out["status"] == 0 and out["ok"] is False
    assert out["error"] == "network" and out["authRequired"] is False
    assert out["body"] is None


def test_index_html_has_no_direct_fetch_outside_sse_stream():
    src = (REPO_ROOT / "frontend" / "index.html").read_text()
    import re as _re
    # Packet 8L: every page routes JSON APIs through the shared vbApi helper
    # and the SSE stream through the shared vbStream helper in ui.js; pages
    # perform no direct fetch at all.
    assert _re.findall(r"fetch\(", src) == []
    ui = (REPO_ROOT / "frontend" / "ui.js").read_text()
    assert "vbStream" in ui and "fetch(" in ui  # stream fetch centralized here
    # every page loads the shared helper
    assert "/static/vb.js" in src and "/static/ui.js" in src
    # no query-string token read anywhere
    assert "get('token')" not in src and 'get("token")' not in src


# ---------------------------------------------------------------- dashboard serving + auth
def _serving_ui(tmp_path, state=None, frontend_dir=None, token=""):
    from voicebridge.webui import WebUI
    state = state or (tmp_path / "state")
    state.mkdir(parents=True, exist_ok=True)

    class _Cfg:
        def get(self, *path, **kw):
            if path and path[-1] == "token":
                return token
            if path and path[-1] == "frontend_dir":
                return frontend_dir
            return kw.get("default")

        def section(self, name):
            return {}

        def state_dir(self):
            return state

    class _Orch:
        cfg = _Cfg()
        task = type("T", (), {"active": False})()

    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui


def test_frontend_status_resolution(tmp_path):
    from voicebridge.webui import (REPO_FRONTEND, frontend_status,
                                   resolve_frontend_dir)
    # repo default
    p, serves, stale, version, active = frontend_status(None)
    assert active == "repo" and stale is False and serves
    assert (REPO_FRONTEND / "VERSION").read_text().strip() == version
    # external CURRENT (matching VERSION)
    ext = tmp_path / "cur"; ext.mkdir()
    (ext / "index.html").write_text("<html>x</html>")
    (ext / "VERSION").write_text((REPO_FRONTEND / "VERSION").read_text())
    p, s, stale, _v, active = frontend_status(str(ext))
    assert active == "external" and stale is False and p == ext and s
    # external STALE / unversioned -> repo fallback
    ext2 = tmp_path / "stale"; ext2.mkdir()
    (ext2 / "index.html").write_text("<html>old</html>")   # no VERSION marker
    p, s, stale, _v, active = frontend_status(str(ext2))
    assert active == "repo" and stale is True and p == REPO_FRONTEND and s
    # external VERSION-MISMATCHED -> repo fallback
    ext3 = tmp_path / "mismatch"; ext3.mkdir()
    (ext3 / "index.html").write_text("<html>x</html>")
    (ext3 / "VERSION").write_text("999.0.0")
    p, _s, stale, _v, active = frontend_status(str(ext3))
    assert active == "repo" and stale is True and p == REPO_FRONTEND
    # missing dir -> repo
    p, s, stale, _v, active = frontend_status(str(tmp_path / "missing"))
    assert active == "repo" and stale is False and p == REPO_FRONTEND
    # 2-tuple compatibility
    got, serves2 = resolve_frontend_dir(str(ext))
    assert got == ext and serves2 is True


def test_dashboard_serves_repo_frontend_and_pages(tmp_path):
    import urllib.request
    ui = _serving_ui(tmp_path, token="t")
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def body(path, headers=None):
            req = urllib.request.Request(base + path,  # noqa: S310
                                         headers=headers or {})
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()

        for page in ("/classic/", "/classic/settings", "/token-setup",
                     "/classic/activity", "/classic/reports",
                     "/static/vb.js", "/static/VERSION"):
            st, html = body(page)
            assert st == 200, page
            if page == "/classic/settings":
                assert "Websites" in html        # current Packet 8E UI
            if page == "/token-setup":
                assert "Pair this device" in html
            if page == "/static/vb.js":
                assert "X-Api-Token" in html and "vbApi" in html
        # /api/status with correct header reports the repo frontend
        st, js = body("/api/status", {"X-Api-Token": "t"})
        import json as _json
        d = _json.loads(js)
        assert st == 200 and d["frontend"]["active"] == "repo"
        assert d["frontend"]["stale"] is False
    finally:
        ui.stop()


def test_dashboard_stale_external_falls_back_to_repo(tmp_path):
    import urllib.request
    import json as _json
    state = tmp_path / "state"
    ext = tmp_path / "stale_ext"; ext.mkdir()
    (ext / "index.html").write_text("<html>OLD UI</html>")   # no VERSION marker
    ui = _serving_ui(tmp_path, state=state, frontend_dir=str(ext), token="t")
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def body(path, headers=None):
            req = urllib.request.Request(base + path,  # noqa: S310
                                         headers=headers or {})
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()

        st, settings_html = body("/classic/settings")
        assert st == 200
        assert "Websites" in settings_html          # repo (current) UI served
        assert "OLD UI" not in settings_html
        st, js = body("/api/status", {"X-Api-Token": "t"})
        d = _json.loads(js)
        assert d["frontend"]["active"] == "repo"
        assert d["frontend"]["stale"] is True
        assert d["frontend"]["configured"] is True
    finally:
        ui.stop()


def test_dashboard_current_external_frontend_served(tmp_path):
    import urllib.request
    import json as _json
    from voicebridge.webui import REPO_FRONTEND
    state = tmp_path / "state"
    ext = tmp_path / "cur_ext"; ext.mkdir()
    (ext / "index.html").write_text("<html>EXT</html>")
    (ext / "VERSION").write_text((REPO_FRONTEND / "VERSION").read_text())
    (ext / "settings.html").write_text("<html>EXTERNAL THEME SETTINGS</html>")
    ui = _serving_ui(tmp_path, state=state, frontend_dir=str(ext), token="t")
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def body(path, headers=None):
            req = urllib.request.Request(base + path,  # noqa: S310
                                         headers=headers or {})
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()

        st, settings_html = body("/classic/settings")
        assert st == 200 and "EXTERNAL THEME SETTINGS" in settings_html
        st, js = body("/api/status", {"X-Api-Token": "t"})
        d = _json.loads(js)
        assert d["frontend"]["active"] == "external"
        assert d["frontend"]["stale"] is False
    finally:
        ui.stop()


def test_dashboard_auth_fail_closed_headers_and_pair_message(tmp_path):
    import urllib.request
    import urllib.error
    ui = _serving_ui(tmp_path, token="secret-token")
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def req(path, headers=None, query=""):
            return urllib.request.Request(base + path + query,  # noqa: S310
                                          headers=headers or {})

        def expect_401(r):
            try:
                urllib.request.urlopen(r)
                raise AssertionError("expected 401")
            except urllib.error.HTTPError as exc:
                assert exc.code == 401

        # missing header -> 401
        expect_401(req("/api/status"))
        # wrong header -> 401
        expect_401(req("/api/status", {"X-Api-Token": "wrong"}))
        # query-string token -> ALWAYS rejected (Packet 1B)
        expect_401(req("/api/status", query="?token=secret-token"))
        # correct header -> 200
        with urllib.request.urlopen(  # noqa: S310
                req("/api/status", {"X-Api-Token": "secret-token"})) as r:
            assert r.status == 200
        # static settings page is served WITHOUT auth (the API calls inside it
        # carry the header token and fail closed individually)
        with urllib.request.urlopen(req("/settings")) as r:  # noqa: S310
            assert r.status == 200
    finally:
        ui.stop()


def test_dashboard_settings_is_real_backend_not_frontend_only(tmp_path):
    # The Websites/Permissions settings page is served from the same backend;
    # its mutating controls require a REAL authenticated header token.
    import urllib.request
    import urllib.error
    import json as _json
    ui = _serving_ui(tmp_path, token="t")
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        # an unauthenticated settings mutation is rejected (no frontend-only path)
        req = urllib.request.Request(  # noqa: S310
            base + "/api/websites",
            data=_json.dumps({"action": "add", "url": "https://example.com"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        # the shared token helper is served to the dashboard
        with urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(base + "/static/vb.js")) as r:
            js = r.read().decode()
        assert "vbToken" in js and "localStorage" in js and "X-Api-Token" in js
    finally:
        ui.stop()


# ---------------------------------------------------------------- dashboard parity
def _start_ui(tmp_path, token="", state=None):
    from voicebridge.webui import WebUI
    state = state or (tmp_path / "state")
    state.mkdir(exist_ok=True)

    class _Cfg:
        def get(self, *path, **kw):
            if path and path[-1] == "token":
                return token
            return kw.get("default")

        def section(self, name):
            return {}

        def state_dir(self):
            return state

    class _Orch:
        cfg = _Cfg()

    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui


def test_dashboard_websites_manifest_link_parity(tmp_path):
    import urllib.request
    ui = _start_ui(tmp_path)
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path) as r:  # noqa: S310
                return json.loads(r.read())

        def post(path, body):
            req = urllib.request.Request(  # noqa: S310
                base + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        out = post("/api/websites", {"action": "add", "url": "https://example.com"})
        assert out["status"] == STATUS_PENDING and out["granted"] is False
        iid = out["integration"]["integration_id"]
        # link requires confirmation + a valid active skill in the SAME backend
        den = post("/api/websites", {"action": "manifest-link",
                                     "id_or_url": iid,
                                     "skill_id": "weather.editor"})
        assert den["state"] == "denied"
        # install + enable a skill via the permissions backend, then link
        from voicebridge.permissions import permissions_store
        from voicebridge.websites import website_integrations_store
        cfg = _cfg(tmp_path)
        perms = permissions_store(cfg)
        perms.install_skill(_manifest(), requested_by="user", confirmed=True)
        perms.enable_skill("weather.editor", requested_by="user", confirmed=True)
        ok = post("/api/websites", {"action": "manifest-link", "id_or_url": iid,
                                    "skill_id": "weather.editor",
                                    "confirmed": True})
        assert ok["state"] == "OK" and ok["manifest_link"] == "weather.editor"
        bad = post("/api/websites", {"action": "manifest-link", "id_or_url": iid,
                                     "skill_id": "no.such.skill",
                                     "confirmed": True})
        assert bad["state"] == "failed" and "SKILL_MISSING" in bad["detail"]
        # listing exposes active skills for the UI dropdown
        listing = get("/api/websites")
        assert any(s["skill_id"] == "weather.editor"
                   for s in listing["skills"])
    finally:
        ui.stop()


def test_dashboard_websites_parity(tmp_path):
    import urllib.request
    ui = _start_ui(tmp_path)
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path) as r:  # noqa: S310
                return json.loads(r.read())

        def post(path, body):
            req = urllib.request.Request(  # noqa: S310
                base + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        out = post("/api/websites", {"action": "add", "url": "https://example.com"})
        assert out["status"] == STATUS_PENDING and out["granted"] is False
        iid = out["integration"]["integration_id"]
        listing = get("/api/websites")
        assert len(listing["integrations"]) == 1
        detail = get("/api/websites?id=" + iid)
        assert detail["integration"]["integration_id"] == iid
        # mutation without confirmation -> typed denial
        den = post("/api/websites", {"action": "enable", "id_or_url": iid})
        assert den["state"] == "denied"
        # logs endpoint works and is redacted
        logs = get("/api/websites?logs=" + iid + "&tail=10")
        assert len(logs["logs"]) >= 1
        blob = json.dumps(logs)
        assert "super-secret" not in blob
        # remove requires confirmation then rollback works
        rm = post("/api/websites", {"action": "remove", "id_or_url": iid,
                                    "confirmed": True})
        assert rm["removed"]["status"] == STATUS_REMOVED
        rb = post("/api/websites", {"action": "rollback", "id_or_url": iid,
                                    "confirmed": True})
        assert rb["integration"]["integration_id"] == iid
    finally:
        ui.stop()


def test_dashboard_full_lifecycle_real_backend(tmp_path):
    """Beginner+advanced dashboard controls are REAL backend operations.

    Pre-seeds a READY, linked record through the SAME state store the dashboard
    uses, then drives every control over HTTP and asserts typed states.
    """
    import urllib.request
    import urllib.error
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    store = WebsiteIntegrationStore(state / "websites.json")
    perms = PermissionsStore(state / "permissions.json")   # dashboard default path
    rec = store.add("https://example.com", requested_by="test")
    store.run_discovery(rec.integration_id, requested_by="test",
                        probe=_probe(STATUS_READY, reachable=True,
                                     reason_code="OK"),
                        probe_source="fixture")
    _install_active(perms)
    _link(store, perms, rec)
    iid = rec.integration_id

    ui = _start_ui(tmp_path, token="t", state=state)
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        HDR = {"Content-Type": "application/json", "X-Api-Token": "t"}

        def post(path, body):
            req = urllib.request.Request(  # noqa: S310
                base + path, data=json.dumps(body).encode(), headers=HDR,
                method="POST")
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        def get(path):
            req = urllib.request.Request(  # noqa: S310
                base + path, headers={"X-Api-Token": "t"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        # unauthenticated -> 401 (not frontend-only)
        try:
            urllib.request.urlopen(base + "/api/websites")  # noqa: S310
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        # pending/disabled beginner status is pending/disabled (PENDING add)
        listing = get("/api/websites")
        assert listing["mode"] == MODE_RESTRICTED
        # enable without confirmation -> typed denial (real backend)
        den = post("/api/websites", {"action": "enable", "id_or_url": iid})
        assert den["state"] == "denied" and den["code"] == "CONFIRMATION_REQUIRED"
        # enable with confirmation -> success, grants nothing
        ok = post("/api/websites", {"action": "enable", "id_or_url": iid,
                                    "confirmed": True})
        assert ok["state"] == "OK" and ok["enabled"] is True
        # grant without confirmation -> denied
        den = post("/api/websites", {"action": "grant", "id_or_url": iid,
                                     "ttl": 3600})
        assert den["state"] == "denied"
        # grant with confirmation -> scoped + expiring grants
        ok = post("/api/websites", {"action": "grant", "id_or_url": iid,
                                    "confirmed": True, "ttl": 3600})
        assert ok["state"] == "OK" and len(ok["grants"]) == 2
        assert all(g["expires_at"] is not None for g in ok["grants"])
        # check -> OK with manifest provenance
        chk = post("/api/websites", {"action": "check", "id_or_url": iid})
        assert chk["state"] == "OK" and chk["manifest"]["active"] is True
        assert chk["manifest"]["license"] == "MIT"
        # detail (advanced view) shows provenance + effective values
        detail = get("/api/websites?id=" + iid)
        assert detail["integration"]["manifest_link"] == "weather.editor"
        assert detail["effective"]["grants"]
        # revoke -> grants removed; check -> PARTIAL
        ok = post("/api/websites", {"action": "revoke", "id_or_url": iid,
                                    "confirmed": True})
        assert ok["state"] == "OK" and len(ok["revoked"]) == 2
        chk = post("/api/websites", {"action": "check", "id_or_url": iid})
        assert chk["state"] == "PARTIAL"
        # reset (permission mode) via the same 7E backend -> restricted + no grants
        reset = post("/api/permissions", {"action": "reset", "confirmed": True})
        assert reset["state"] == "OK" and reset["mode"] == MODE_RESTRICTED
        # disable -> remove -> rollback (grants never auto-restored)
        ok = post("/api/websites", {"action": "disable", "id_or_url": iid,
                                    "confirmed": True})
        assert ok["state"] == "OK" and ok["enabled"] is False
        ok = post("/api/websites", {"action": "remove", "id_or_url": iid,
                                    "confirmed": True})
        assert ok["removed"]["status"] == STATUS_REMOVED
        ok = post("/api/websites", {"action": "rollback", "id_or_url": iid,
                                    "confirmed": True})
        assert ok["state"] == "OK" and ok["integration"]["grant_ids"] == []
        # audit/log inspection is redacted
        logs = get("/api/websites?logs=" + iid + "&tail=20")
        ops = {r["op"] for r in logs["logs"]}
        assert {"add", "discover", "manifest-link", "enable", "grant",
                "revoke", "remove", "rollback"} <= ops
        blob = json.dumps(logs)
        assert "super-secret" not in blob
    finally:
        ui.stop()


def test_dashboard_websites_rejects_unauthenticated(tmp_path):
    import urllib.request
    import urllib.error
    ui = _start_ui(tmp_path, token="secret-token")
    try:
        port = ui._server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        def req(headers=None, query=""):
            return urllib.request.Request(  # noqa: S310
                base + "/api/websites" + query, headers=headers or {})

        # no token header -> 401 (even on loopback when a token is configured)
        try:
            urllib.request.urlopen(req())
            raise AssertionError("expected 401 without token")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        # query-string token is ALWAYS rejected
        try:
            urllib.request.urlopen(req(query="?token=secret-token"))
            raise AssertionError("expected 401 with query-string token")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        # correct header token works
        with urllib.request.urlopen(  # noqa: S310
                req(headers={"X-Api-Token": "secret-token"})) as r:
            assert json.loads(r.read())["state"] == "OK"
    finally:
        ui.stop()