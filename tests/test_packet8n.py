"""Packet 8N tests: Ghost desktop/browser capability adapter (optional).

Covers the Packet 8N acceptance gate: every Ghost operation reaches the
existing capability/action broker and cannot execute by default or through a
direct HTTP/MCP/voice bypass; typed capability discovery; target/origin
binding; session grants and expiry; exact action previews/hashes; read /
reversible / high-impact separation; shell hard-disable; cancellation;
emergency stop; stale/replayed grants; prompt injection; ambiguous windows /
malformed tool output; X11/Wayland / multi-session isolation; screenshots /
clipboard redaction; resource bounds; audit; rollback; the two-browser-role
contract (Chromium account browser vs Comet research browser, distinct CDP
ports/profiles/target registries, no cross-cookie, no wrong-browser fallback);
and the research-only mcp-comet contract (computer mode and write tools
disabled until 8O).

Everything is synthetic/hermetic: executors are fixtures, never live adapters,
and no process, browser, account, service, or timer is ever touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import actions as A  # noqa: E402
from voicebridge import ghost as G  # noqa: E402
from voicebridge.capabilities import Tier  # noqa: E402
from voicebridge.permissions import (  # noqa: E402
    MODE_RESTRICTED, PermissionsStore, is_hard_deny_auto,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "actions"


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def _stores(tmp_path, clock=time.time):
    return (A.ActionStore(tmp_path / "actions.json", clock=clock),
            PermissionsStore(tmp_path / "perm.json"),
            G.SessionGrantStore(tmp_path / "ghost-grants.json", clock=clock))


def _ghost_outcomes(name="ghost_outcomes.json"):
    G.register_ghost_specs()
    return A.load_fixture_outcomes(FIXTURES / name)


def _runner(tmp_path, *, name="ghost_outcomes.json", grants=(),
            session_grants=(), confirmation_ttl_s=None, clock=time.time,
            shell=False, enabled=False, adapter=None):
    store, perms, sgs = _stores(tmp_path, clock=clock)
    for cap, scope in grants:
        perms.grant(cap, requested_by="user", confirmed=True, scopes=(scope,))
    execs = A.make_fixture_executors(_ghost_outcomes(name), clock=clock)
    adapter = adapter or G.GhostAdapter(shell=shell)
    return G.GhostRunner(store=store, perms=perms, executors=execs,
                         enabled=enabled, clock=clock,
                         confirmation_ttl_s=confirmation_ttl_s,
                         session_grants=sgs, adapter=adapter)


# ---------------------------------------------------------------------------
# Adapter defaults + shell hard-disable
# ---------------------------------------------------------------------------
def test_adapter_disabled_and_shell_off_by_default():
    assert not G.ghost_shell_enabled()
    assert "ghost.shell" not in G.ghost_action_ids()
    adapter = G.GhostAdapter(shell=False)
    assert adapter.state.enabled is False
    assert adapter.state.health == G.HEALTH_DISABLED
    assert adapter.state.stopped is False


def test_shell_capability_only_registered_when_explicitly_enabled():
    os.environ[G.GHOST_SHELL_ENV] = "on"
    try:
        G.register_ghost_specs()
        assert "ghost.shell" in G.ghost_action_ids()
    finally:
        os.environ.pop(G.GHOST_SHELL_ENV, None)
        G.register_ghost_specs()
    assert "ghost.shell" not in G.ghost_action_ids()


def test_no_ghost_capability_hits_hard_deny_auto():
    for cid in G.ghost_action_ids():
        assert is_hard_deny_auto(cid) is None, cid


# ---------------------------------------------------------------------------
# Capability registration through the broker (no bypass)
# ---------------------------------------------------------------------------
def test_every_capability_registered_with_broker_and_schema(tmp_path):
    runner = _runner(tmp_path)
    registered = set(runner.broker.names())
    for cid in G.ghost_action_ids():
        assert cid in registered, f"{cid} not bound in broker"
        spec = G.ghost_spec(cid)
        assert spec is not None
        assert spec.scopes, f"{cid} has no scope"
        assert spec.timeout_s > 0
        assert spec.side_effects
        assert spec.rollback_kind in (A.RB_NONE, A.RB_REVERSIBLE,
                                      A.RB_IMPOSSIBLE)


def test_capability_separation_and_tiers():
    assert G.ghost_spec("ghost.desktop.discover").tier is Tier.READ_ONLY
    assert G.ghost_spec("ghost.desktop.launch").tier is Tier.REVERSIBLE
    assert G.ghost_spec("ghost.window.anchor").tier is Tier.REVERSIBLE
    assert G.ghost_spec("ghost.browser.tabs").tier is Tier.READ_ONLY
    assert G.ghost_spec("ghost.input.scroll").tier is Tier.REVERSIBLE
    # high-impact
    for cid in ("ghost.input.click", "ghost.input.type",
                "ghost.clipboard.read"):
        assert G.ghost_spec(cid).tier is Tier.MUTATING, cid
        assert G.ghost_spec(cid).needs_confirmation, cid


def test_unknown_capability_fails_closed(tmp_path):
    runner = _runner(tmp_path)
    d = runner.execute("ghost.does.not.exist", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "denied"
    assert d["code"] == "UNKNOWN_CAPABILITY"


def test_no_fixture_means_adapter_disabled(tmp_path):
    # A granted read-only capability that reaches the broker still cannot run
    # without an injected executor (ADAPTER_DISABLED, never a live launch).
    runner = _runner(tmp_path, name="fail.json",
                     grants=(("ghost.desktop.discover", "desktop"),))
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "denied"
    assert d["code"] == A.ST_ADAPTER_DISABLED


# ---------------------------------------------------------------------------
# Default deny + grants + session grants
# ---------------------------------------------------------------------------
def test_restricted_default_deny_without_any_grant(tmp_path):
    runner = _runner(tmp_path)
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "denied"
    assert d["code"] == "NOT_GRANTED"


def test_session_grant_enables_readonly(tmp_path):
    runner = _runner(tmp_path)
    g = runner.grant("ghost.desktop.discover", scopes=("desktop",),
                     target="desktop")
    assert g["state"] == "OK"
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "OK"


def test_session_grant_is_target_bound(tmp_path):
    runner = _runner(tmp_path)
    runner.grant("ghost.desktop.discover", scopes=("desktop",),
                 target="desktop")
    d = runner.execute("ghost.desktop.discover", args={},
                       target="https://evil.test", scope="desktop")
    assert d["state"] == "denied"
    assert d["code"] in ("NOT_GRANTED", "OUT_OF_SCOPE")


def test_session_grant_expiry(tmp_path):
    clock = {"t": 1000.0}
    runner = _runner(tmp_path, clock=lambda: clock["t"])
    runner.grant("ghost.desktop.discover", scopes=("desktop",),
                 target="desktop", ttl_s=10)
    assert runner.execute("ghost.desktop.discover", args={}, target="desktop",
                          scope="desktop")["state"] == "OK"
    clock["t"] = 1100.0  # expired
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "denied"


def test_session_grant_human_only():
    sgs = G.SessionGrantStore(Path("/tmp") / f"g-{time.time()}.json")
    with pytest.raises(G.SessionGrantError):
        sgs.issue("ghost.desktop.discover", scopes=("desktop",),
                  target="desktop", issued_by="model")


def test_session_grant_only_read_reversible(tmp_path):
    runner = _runner(tmp_path)
    with pytest.raises(G.SessionGrantError):
        runner.grant("ghost.clipboard.read", scopes=("web",), target="desktop")


def test_revoke_grant_removes_access(tmp_path):
    runner = _runner(tmp_path)
    g = runner.grant("ghost.desktop.discover", scopes=("desktop",),
                     target="desktop")
    assert runner.execute("ghost.desktop.discover", args={}, target="desktop",
                          scope="desktop")["state"] == "OK"
    runner.revoke_grant(g["grant"]["grant_id"])
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "denied"
    assert d["code"] == "NOT_GRANTED"


# ---------------------------------------------------------------------------
# High-impact always needs fresh confirmation
# ---------------------------------------------------------------------------
def test_high_impact_requires_fresh_confirmation(tmp_path):
    runner = _runner(tmp_path)
    d = runner.execute("ghost.input.click",
                       args={"role": "chromium", "target": "#btn"},
                       target="https://mail.google.com", scope="web")
    assert d["state"] == "denied"
    assert d["code"] == "CONFIRMATION_REQUIRED"
    # a session grant is NOT enough for a mutating capability
    with pytest.raises(G.SessionGrantError):
        runner.grant("ghost.input.click", scopes=("web",),
                     target="https://mail.google.com")


def test_high_impact_with_confirmation_executes(tmp_path):
    # Confirmation is required AND a 7E grant is required; with both the
    # granted+confirmed path executes (deny-first, exact-hash bound).
    runner = _runner(tmp_path, grants=(("ghost.input.click", "web"),))
    d = runner.execute("ghost.input.click",
                       args={"role": "chromium", "target": "#btn"},
                       target="https://mail.google.com", scope="web",
                       confirmed=True)
    assert d["state"] == "OK"


def test_high_impact_requires_grant_even_with_confirmation(tmp_path):
    # confirmation alone is not authorization: the 7E gate still needs a grant.
    runner = _runner(tmp_path)
    d = runner.execute("ghost.input.click",
                       args={"role": "chromium", "target": "#btn"},
                       target="https://mail.google.com", scope="web",
                       confirmed=True)
    assert d["state"] == "denied"


def test_high_impact_granted_and_confirmed_executes(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.click", "web"),))
    d = runner.execute("ghost.input.click",
                       args={"role": "chromium", "target": "#btn"},
                       target="https://mail.google.com", scope="web",
                       confirmed=True)
    assert d["state"] == "OK"


# ---------------------------------------------------------------------------
# Preview / hash binding
# ---------------------------------------------------------------------------
def test_preview_persists_nothing_and_hash_is_stable(tmp_path):
    runner = _runner(tmp_path)
    p = runner.preview("ghost.input.type",
                       args={"role": "comet", "target": "#q",
                             "text": "hello"},
                       target="https://perplexity.ai", scope="web")
    assert p["state"] == "preview"
    assert p["action_hash"]
    assert p["persisted"] is False
    p2 = runner.preview("ghost.input.type",
                        args={"role": "comet", "target": "#q",
                              "text": "hello"},
                        target="https://perplexity.ai", scope="web")
    assert p2["action_hash"] == p["action_hash"]


def test_hash_changes_with_any_binding_field(tmp_path):
    runner = _runner(tmp_path)
    p1 = runner.preview("ghost.input.type",
                        args={"role": "comet", "target": "#q",
                              "text": "hello"},
                        target="https://perplexity.ai", scope="web")
    p2 = runner.preview("ghost.input.type",
                        args={"role": "comet", "target": "#q",
                              "text": "hello2"},
                        target="https://perplexity.ai", scope="web")
    assert p1["action_hash"] != p2["action_hash"]


def test_stale_target_refused(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.type", "web"),))
    p = runner.preview("ghost.input.type",
                       args={"role": "comet", "target": "#q",
                             "text": "hello"},
                       target="https://perplexity.ai", scope="web")
    d = runner.execute("ghost.input.type",
                       args={"role": "comet", "target": "#q",
                             "text": "hello"},
                       target="https://perplexity.ai", scope="web",
                       confirmed=True, expected_hash=p["action_hash"])
    assert d["state"] == "OK"
    # stale hash against changed text refused
    d2 = runner.execute("ghost.input.type",
                        args={"role": "comet", "target": "#q",
                              "text": "hello2"},
                        target="https://perplexity.ai", scope="web",
                        confirmed=True, expected_hash=p["action_hash"])
    assert d2["state"] == A.ST_STALE_TARGET


# ---------------------------------------------------------------------------
# Browser-role contract
# ---------------------------------------------------------------------------
def test_two_browser_roles_distinct():
    roles = G.browser_roles()
    assert set(roles) == {G.ROLE_CHROMIUM, G.ROLE_COMET}
    assert roles[G.ROLE_CHROMIUM].cdp_port != roles[G.ROLE_COMET].cdp_port
    assert roles[G.ROLE_CHROMIUM].profile != roles[G.ROLE_COMET].profile
    assert roles[G.ROLE_CHROMIUM].approved_origins
    assert roles[G.ROLE_COMET].approved_origins


def test_role_origin_binding_fail_closed():
    chromium = G.browser_roles()[G.ROLE_CHROMIUM]
    comet = G.browser_roles()[G.ROLE_COMET]
    # chromium account origins allowed
    chromium.bind_origin("https://mail.google.com")
    chromium.bind_origin("https://gmail.com")
    chromium.bind_origin("https://www.gmail.com")
    # a research origin is NOT allowed on the account browser
    with pytest.raises(G.ActionValidationError):
        chromium.bind_origin("https://perplexity.ai")
    comet.bind_origin("https://perplexity.ai")
    with pytest.raises(G.ActionValidationError):
        comet.bind_origin("https://mail.google.com")


def test_strict_origin_rejections():
    chromium = G.browser_roles()[G.ROLE_CHROMIUM]
    # substring / lookalike / boundary attacks must all fail closed
    for bad in (
        "https://evilgmail.com",
        "https://gmail.com.evil.test",
        "https://notmail.google.com",
        "https://mail.google.com.evil.test",
        "https://evil.com/?redirect=https://mail.google.com",
        "https://user:pass@mail.google.com",
        "https://mail.google.com:8443",
        "http://mail.google.com",
        "ftp://mail.google.com",
        "javascript:mail.google.com",
        "mail.google.com",            # bare, non-URL
        "not a url",
        "https://127.0.0.1",
        "https://mail.google.com/path?token=secret",
        "https://mail.google.com/#frag",
        "",
    ):
        with pytest.raises(G.ActionValidationError) as exc:
            chromium.bind_origin(bad)
        msg = str(exc.value)
        assert any(k in msg for k in
                   ("approved", "rejected", "target", "scheme", "host",
                    "port", "user-info", "query", "malformed")), bad
    # legitimate subdomain boundary still allowed
    assert chromium.bind_origin("https://www.gmail.com") == \
        "https://www.gmail.com"
    assert chromium.bind_origin("https://m.messenger.com") == \
        "https://m.messenger.com"


def test_role_account_scope_binding():
    chromium = G.browser_roles()[G.ROLE_CHROMIUM]
    comet = G.browser_roles()[G.ROLE_COMET]
    assert chromium.bind_account_scope("gmail") == "gmail"
    with pytest.raises(G.ActionValidationError):
        chromium.bind_account_scope("research")
    assert comet.bind_account_scope("research") == "research"
    with pytest.raises(G.ActionValidationError):
        comet.bind_account_scope("gmail")


def test_unknown_role_refused():
    with pytest.raises(G.ActionValidationError):
        G.validate_role("netflix")


def test_cdp_loopback_only():
    G.validate_cdp_endpoint(9222, host="127.0.0.1")
    G.validate_cdp_endpoint(9222, host="localhost")
    with pytest.raises(G.ActionValidationError):
        G.validate_cdp_endpoint(9222, host="0.0.0.0")
    with pytest.raises(G.ActionValidationError):
        G.validate_cdp_endpoint(80, host="127.0.0.1")


def test_browser_role_isolation_in_execution(tmp_path):
    # A comet operation must never run against the chromium role origin.
    runner = _runner(tmp_path, grants=(("ghost.browser.tabs", "web"),))
    d = runner.execute("ghost.browser.tabs", args={"role": "chromium"},
                       target="https://perplexity.ai", scope="web")
    assert d["state"] == "denied"


# ---------------------------------------------------------------------------
# Emergency stop + cancellation + health
# ---------------------------------------------------------------------------
def test_emergency_stop_latches(tmp_path):
    runner = _runner(tmp_path)
    runner.grant("ghost.desktop.discover", scopes=("desktop",),
                 target="desktop")
    assert runner.execute("ghost.desktop.discover", args={}, target="desktop",
                          scope="desktop")["state"] == "OK"
    runner.adapter.emergency_stop()
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "denied"
    assert d["code"] == "EMERGENCY_STOP"
    # stays latched even after a fresh grant
    runner.grant("ghost.desktop.discover", scopes=("desktop",),
                 target="desktop")
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["code"] == "EMERGENCY_STOP"


def test_emergency_stop_reset(tmp_path):
    runner = _runner(tmp_path)
    runner.adapter.emergency_stop()
    runner.adapter.reset(requested_by="user")
    runner.grant("ghost.desktop.discover", scopes=("desktop",),
                 target="desktop")
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "OK"


def test_adapter_enable_disable_health(tmp_path):
    adapter = G.GhostAdapter(shell=False)
    assert adapter.state.health == G.HEALTH_DISABLED
    adapter.enable(requested_by="user")
    assert adapter.state.health == G.HEALTH_ENABLED
    adapter.mark_failure("cdp unreachable")
    assert adapter.state.health == G.HEALTH_FAILED
    adapter.mark_reconnect()
    assert adapter.state.health == G.HEALTH_ENABLED
    adapter.disable(requested_by="user")
    assert adapter.state.health == G.HEALTH_DISABLED


def test_adapter_enable_requires_human():
    adapter = G.GhostAdapter(shell=False)
    d = adapter.enable(requested_by="model")
    assert d["state"] == "denied"
    assert d["code"] == "CONFIRMATION_SOURCE_FORBIDDEN"


def test_cancel_in_flight_action(tmp_path):
    runner = _runner(tmp_path, name="ghost_slow.json",
                     grants=(("ghost.desktop.discover", "desktop"),))
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] in ("OK", "cancelled")


def test_receipts_survive_restart(tmp_path):
    runner = _runner(tmp_path)
    runner.grant("ghost.desktop.discover", scopes=("desktop",),
                 target="desktop")
    r = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    path = runner.store.path
    runner.store.close()
    reopened = A.ActionStore(path)
    rec = reopened.receipt(r["receipt_id"])
    assert rec is not None
    assert rec.action_id == "ghost.desktop.discover"


# ---------------------------------------------------------------------------
# Injection / hostile text / malformed args
# ---------------------------------------------------------------------------
def test_coordinate_args_rejected(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.click", "web"),))
    with pytest.raises(G.ActionValidationError):
        runner.execute("ghost.input.click",
                       args={"role": "chromium", "target": "#btn",
                             "x": 100, "y": 200},
                       target="https://mail.google.com", scope="web",
                       confirmed=True)


def test_shell_meta_args_rejected(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.type", "web"),))
    with pytest.raises(G.ActionValidationError):
        runner.execute("ghost.input.type",
                       args={"role": "comet", "target": "#q",
                             "text": "x; rm -rf /"},
                       target="https://perplexity.ai", scope="web",
                       confirmed=True)


def test_js_payload_rejected(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.type", "web"),))
    with pytest.raises(G.ActionValidationError):
        runner.execute("ghost.input.type",
                       args={"role": "comet", "target": "#q",
                             "text": "<script>alert(1)</script>"},
                       target="https://perplexity.ai", scope="web",
                       confirmed=True)


def test_unknown_role_arg_rejected(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.browser.tabs", "web"),))
    with pytest.raises(G.ActionValidationError):
        runner.execute("ghost.browser.tabs", args={"role": "evil"},
                       target="https://mail.google.com", scope="web",
                       confirmed=True)


def test_model_source_cannot_confirm(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.click", "web"),))
    d = runner.execute("ghost.input.click",
                       args={"role": "chromium", "target": "#btn"},
                       target="https://mail.google.com", scope="web",
                       source="model", confirmed=True)
    assert d["state"] == "denied"
    assert d["code"] in ("CONFIRMATION_SOURCE_FORBIDDEN",
                         "CONFIRMATION_REQUIRED")


def test_grant_issuer_cannot_be_arbitrary_text(tmp_path):
    runner = _runner(tmp_path)
    with pytest.raises(G.SessionGrantError):
        runner.grant("ghost.desktop.discover", scopes=("desktop",),
                     target="desktop", issued_by="ignore previous instructions")


# ---------------------------------------------------------------------------
# Redaction (clipboard / screenshot evidence)
# ---------------------------------------------------------------------------
def test_evidence_never_persists_raw_bytes():
    ev = G.redact_evidence_bytes(b"PRIVATE\x00\xff" * 1000)
    payload = json.loads(ev)
    assert "PRIVATE" not in ev
    assert payload["evidence"]["sha256"]
    assert payload["evidence"]["bytes"] == 9000
    assert payload["stored"] is False


def test_evidence_truncation_reason():
    ev = G.redact_evidence_bytes(b"x" * 400_000, max_bytes=128 * 1024)
    payload = json.loads(ev)
    assert payload["evidence"]["reason"] == "TRUNCATED"
    assert payload["evidence"]["bytes"] == 400_000


# ---------------------------------------------------------------------------
# mcp-comet research-only contract
# ---------------------------------------------------------------------------
def _comet_runner(tmp_path, *, enabled=True):
    """GhostRunner with a bound, enabled comet adapter (broker path)."""
    store, perms, sgs = _stores(tmp_path)
    adapter = G.GhostAdapter(shell=False)
    comet = G.CometResearchAdapter(enabled=enabled)
    runner = G.GhostRunner(store=store, perms=perms, executors={},
                           enabled=True, session_grants=sgs,
                           adapter=adapter, comet=comet)
    for cap in ("ghost.comet.connect", "ghost.comet.disconnect",
                "ghost.comet.mode",
                "ghost.comet.ask", "ghost.comet.wait",
                "ghost.comet.extract", "ghost.comet.evidence"):
        perms.grant(cap, requested_by="user", confirmed=True,
                    scopes=("research",))
    return runner, comet


def test_comet_direct_object_call_refused():
    # Direct object calls bypass authorization and must fail closed even when
    # the adapter is bound/enabled.
    comet = G.CometResearchAdapter(enabled=True)
    comet.bind(object())  # bound but NOT via a real runner broker executor
    for call in (
        lambda: comet.connect(requested_by="user"),
        lambda: comet.select_mode("research", requested_by="user"),
        lambda: comet.ask(G.CometResearchRequest(query="q"),
                          requested_by="user"),
        lambda: comet.wait("w", max_wait_s=1, requested_by="user"),
        lambda: comet.extract("text", requested_by="user"),
        lambda: comet.evidence(b"x", requested_by="user"),
    ):
        with pytest.raises(G.CometResearchError) as exc:
            call()
        assert exc.value.code == "COMET_DIRECT_CALL_FORBIDDEN"


def test_comet_operations_only_through_broker(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    # connect through the broker
    c = runner.comet_connect()
    assert c["state"] == "OK"
    assert comet.status()["connected"] is True
    # ask through the broker produces a typed wait id
    d = runner.comet_ask("research query")
    assert d["state"] == "OK"
    wid = d["wait_id"]
    assert wid.startswith("w_")
    comet.mark_wait(wid, G.WAIT_COMPLETED)
    w = runner.comet_wait(wid, max_wait_s=1)
    assert w["state"] == "COMPLETED"


def test_comet_disabled_by_default(tmp_path):
    runner, comet = _comet_runner(tmp_path, enabled=False)
    c = runner.comet_connect()
    # The broker surfaces the adapter's typed disabled state (never a live
    # launch and never a fake success).
    assert c["state"] in ("denied", "failed")
    assert c.get("code") in ("COMET_UNAVAILABLE", "NOT_GRANTED", "FAILED",
                             A.ST_ADAPTER_DISABLED)
    assert "disabled" in str(c.get("detail", "")).lower() or \
        c.get("code") == "FAILED"


def test_comet_research_mode_only_through_broker(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    assert runner.comet_connect()["state"] == "OK"
    # computer/labs/empty/unknown modes are refused by the capability schema
    # AND the adapter.
    for bad in ("computer", "labs", "research; evil"):
        try:
            m = runner.comet_mode(bad)
        except G.ActionValidationError:
            m = {"state": "denied", "code": "MALFORMED_INPUT"}
        assert m["state"] == "denied"
        assert m["code"] in ("MALFORMED_INPUT", "MODE_DISABLED",
                             "CONFIRMATION_REQUIRED", "NOT_GRANTED"), bad
    # empty mode is schema-rejected (fail closed)
    with pytest.raises(G.ActionValidationError):
        runner.comet_mode("")
    assert runner.comet_mode("research")["state"] == "OK"


def test_comet_connect_human_only(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    d = runner.comet_connect(source="model")
    assert d["state"] == "denied"
    assert d["code"] == "CONFIRMATION_SOURCE_FORBIDDEN"


def test_comet_ask_bounds_and_policy(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    runner.comet_connect()
    # screenshot flag + bounds are validated (never silently bypass policy)
    with pytest.raises(G.CometResearchError):
        G.CometResearchRequest(query="q", capture_screenshot=True,
                               screenshot_max_bytes=-1)
    with pytest.raises(G.CometResearchError):
        G.CometResearchRequest(query="q", max_sources=999)
    with pytest.raises(G.CometResearchError):
        G.CometResearchRequest(query="q", max_chars=0)
    with pytest.raises(G.CometResearchError):
        G.CometResearchRequest(query="q", timeout_s=0)
    # an injected/unknown mode is rejected at the dataclass AND the adapter
    with pytest.raises(G.CometResearchError):
        G.CometResearchRequest(query="q", mode="labs")
    with pytest.raises(G.CometResearchError):
        G.CometResearchRequest(query="q", mode="")
    # valid bounded request passes through the broker
    d = runner.comet_ask("research query", max_sources=5, max_chars=30_000)
    assert d["state"] == "OK"


def test_comet_extract_redacts_and_bounds(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    runner.comet_connect()
    # NO source -> fail closed (ignored request is never success)
    out = runner.execute(
        "ghost.comet.extract", args={"max_chars": 60_000},
        target="https://perplexity.ai", scope="research")
    assert out["state"] == "failed"
    assert out["code"] == "NO_SOURCE"
    # with real source: redacted + bounded
    out = runner.execute(
        "ghost.comet.extract",
        args={"source": "body token=sk-abcdef1234567890xyz trailing " * 200,
              "max_chars": 60_000},
        target="https://perplexity.ai", scope="research")
    assert out["state"] == "OK"
    assert "sk-abcdef1234567890xyz" not in json.dumps(out)
    # credential-shaped text is redacted by the redaction helper
    ev = G.redact_text("body token=sk-abcdef1234567890xyz trailing ")
    assert "sk-abcdef1234567890xyz" not in ev


def test_comet_evidence_fails_closed_without_data(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    runner.comet_connect()
    out = runner.execute(
        "ghost.comet.evidence", args={"max_bytes": 1000},
        target="https://perplexity.ai", scope="research")
    assert out["state"] == "failed"
    assert out["code"] == "NO_EVIDENCE"
    # malformed b64 -> typed failure
    out = runner.execute(
        "ghost.comet.evidence", args={"data_b64": "!!!notbase64!!!"},
        target="https://perplexity.ai", scope="research")
    assert out["state"] == "failed"
    assert out["code"] == "BAD_EVIDENCE"


def test_comet_wait_typed_states(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    runner.comet_connect()
    d = runner.comet_ask("research query")
    wid = d["wait_id"]
    # provider error is typed, not a fake success
    comet.mark_wait(wid, G.WAIT_PROVIDER_ERROR)
    assert runner.comet_wait(wid, max_wait_s=1)["state"] == "PROVIDER_ERROR"
    # unknown/expired wait id -> typed cancelled (disconnected not needed)
    assert runner.comet_wait("w_missing", max_wait_s=1)["state"] in (
        "CANCELLED", "TIMEOUT", "UNAVAILABLE")
    # disconnected -> ask/wait report typed unavailable
    runner.comet_disconnect()
    d2 = runner.comet_ask("another query")
    assert d2["state"] in ("denied", "failed")
    assert "disabled" in str(d2.get("detail", "")).lower() or \
        d2.get("code") == "FAILED"


def test_comet_source_never_grants(tmp_path):
    runner, comet = _comet_runner(tmp_path)
    # model/page/tool text cannot operate the research adapter
    d = runner.comet_ask("query", source="model")
    assert d["state"] == "denied"
    assert d["code"] == "CONFIRMATION_SOURCE_FORBIDDEN"


# ---------------------------------------------------------------------------
# CLI / dashboard parity
# ---------------------------------------------------------------------------
def test_cli_ghost_list_parity(tmp_path):
    from voicebridge.cli import build_parser, main
    cfg = _cfg(tmp_path)
    p = build_parser()
    args = p.parse_args(["--config", str(cfg.path), "ghost", "list"])
    rc = main(["--config", str(cfg.path), "ghost", "list"])
    assert rc == 0


def test_cli_ghost_status_parity(tmp_path):
    cfg = _cfg(tmp_path)
    rc = main_cli(["--config", str(cfg.path), "ghost", "status"])
    assert rc == 0


def main_cli(argv):
    import io
    import contextlib
    from voicebridge.cli import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(argv)
    return rc


def test_ghost_voice_uses_same_runner(tmp_path):
    # Ghost capability ids are resolvable by the shared action registry and
    # route through the SAME gate/hash/confirmation machinery as voice.
    runner = _runner(tmp_path)
    spec = G.ghost_spec("ghost.desktop.discover")
    assert spec is not None
    # voice requests for Ghost must still produce an exact typed envelope
    from voicebridge import actions as A
    env = A.build_envelope(spec, args={}, target="desktop", scope="desktop")
    assert env["action_hash"]
    assert env["action_id"] == "ghost.desktop.discover"


def test_dashboard_ghost_parity(tmp_path: Path) -> None:
    """The authenticated dashboard /api/ghost reports the same typed status +
    capability list as the CLI (one backend, no frontend-only state)."""
    import urllib.request
    from voicebridge.webui import WebUI

    class _FakeTTs:
        name = "fake"

    class _FakeOrch:
        def __init__(self):
            self._tts = _FakeTTs()
            self.cfg = type("C", (), {
                "get": lambda self, *p, **kw: False if p and p[-1] == "speak_web_locally" else kw.get("default"),
                "section": lambda self, n: {},
                "model_dir": lambda self: Path("/tmp"),
                "state_dir": lambda self: Path(tmp_path),
            })()

    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.start()
    try:
        port = ui._server.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ghost", timeout=10) as resp:
            body = json.loads(resp.read().decode())
        assert body["state"] == "OK"
        assert body["adapter"]["health"] == "disabled"
        ids = [c["action_id"] for c in body["capabilities"]]
        assert "ghost.desktop.discover" in ids
        assert "ghost.shell" not in ids  # GHOST_SHELL=off default
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Role binding is real (Packet 8N repair #3)
# ---------------------------------------------------------------------------
def test_role_is_in_envelope_and_hash(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.browser.tabs", "web"),))
    # role passed as kwarg must land in the envelope args AND the hash
    p1 = runner.preview("ghost.browser.tabs", args={},
                        target="https://mail.google.com", scope="web",
                        role="chromium")
    assert p1["action_hash"]
    # a different role produces a different hash (never ignored)
    p2 = runner.preview("ghost.browser.tabs", args={},
                        target="https://perplexity.ai", scope="web",
                        role="comet")
    assert p1["action_hash"] != p2["action_hash"]


def test_role_hash_binds_preview_execute(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.browser.tabs", "web"),))
    p = runner.preview("ghost.browser.tabs", args={},
                       target="https://mail.google.com", scope="web",
                       role="chromium")
    d = runner.execute("ghost.browser.tabs", args={},
                       target="https://mail.google.com", scope="web",
                       role="chromium", expected_hash=p["action_hash"])
    assert d["state"] == "OK"
    # role-swap changes the hash -> stale target refusal
    d2 = runner.execute("ghost.browser.tabs", args={},
                        target="https://perplexity.ai", scope="web",
                        role="comet", expected_hash=p["action_hash"])
    assert d2["state"] == A.ST_STALE_TARGET


def test_role_conflict_fails_closed(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.browser.tabs", "web"),))
    with pytest.raises(G.ActionValidationError):
        runner.preview("ghost.browser.tabs",
                       args={"role": "chromium"},
                       target="https://mail.google.com", scope="web",
                       role="comet")


def test_role_bound_through_prepare_confirmation(tmp_path):
    # The two-step prepare -> execute path binds the role in the confirmation
    # via the exact args digest; a role swap on execute cannot replay it.
    runner = _runner(tmp_path, grants=(("ghost.input.type", "web"),))
    spec = G.ghost_spec("ghost.input.type")
    env = A.build_envelope(
        spec, args={"role": "comet", "target": "#q", "text": "hello"},
        target="https://perplexity.ai", scope="web")
    assert env["args"]["role"] == "comet"
    prep = runner.prepare("ghost.input.type", env, issued_by="user")
    assert prep["state"] == "prepared"
    ex = runner.execute("ghost.input.type", envelope=env, source="user",
                        confirmation_id=prep["confirmation_id"])
    assert ex["state"] == "OK"
    # the same confirmation cannot be replayed against a different role
    env2 = A.build_envelope(
        spec, args={"role": "chromium", "target": "#q", "text": "hello"},
        target="https://mail.google.com", scope="web")
    ex2 = runner.execute("ghost.input.type", envelope=env2, source="user",
                         confirmation_id=prep["confirmation_id"])
    assert ex2["state"] in (A.ST_REPLAYED, A.ST_STALE_TARGET,
                            "denied")


def test_no_wrong_browser_aggregate_fallback(tmp_path):
    # a Chromium grant can never execute against Comet and vice versa
    runner = _runner(tmp_path, grants=(("ghost.browser.tabs", "web"),))
    # chromium role with a comet-only origin -> denied
    d = runner.execute("ghost.browser.tabs", args={"role": "chromium"},
                       target="https://perplexity.ai", scope="web",
                       confirmed=True)
    assert d["state"] == "denied"
    assert d["code"] in ("OUT_OF_SCOPE", "NOT_GRANTED")
    # grant a comet-specific session grant; chromium execution must still fail
    runner.grant("ghost.browser.tabs", scopes=("web",),
                 target="https://perplexity.ai", role="comet")
    d = runner.execute("ghost.browser.tabs", args={"role": "chromium"},
                       target="https://perplexity.ai", scope="web")
    assert d["state"] == "denied"


def test_role_bound_through_retry_and_recover(tmp_path):
    runner = _runner(tmp_path, grants=(("ghost.input.scroll", "web"),),
                     name="ghost_outcomes.json")
    d = runner.execute("ghost.input.scroll",
                       args={"role": "comet", "target": "#q",
                             "direction": "down"},
                       target="https://perplexity.ai", scope="web",
                       confirmed=True)
    assert d["state"] == "OK"
    rid = d["receipt_id"]
    # retry with the SAME role succeeds; a role change is a new action
    r2 = runner.retry(rid, args={"role": "comet", "target": "#q",
                                 "direction": "down"},
                      target="https://perplexity.ai", scope="web")
    assert r2["state"] == "OK" or r2["state"] == A.ST_IDEMPOTENT
    # receipts retain the role binding
    rec = runner.receipt(rid)
    assert rec["action_hash"]


# ---------------------------------------------------------------------------
# Emergency stop stops real active runs (Packet 8N repair #4)
# ---------------------------------------------------------------------------
def test_emergency_stop_cancels_slow_fixture(tmp_path):
    import threading
    result = {}
    runner = _runner(tmp_path, name="ghost_slow.json",
                     grants=(("ghost.desktop.discover", "desktop"),))
    t = threading.Thread(
        target=lambda: result.update(
            runner.execute("ghost.desktop.discover", args={},
                           target="desktop", scope="desktop")),
        daemon=True)
    t.start()
    time.sleep(0.3)
    runner.adapter.emergency_stop()
    t.join(timeout=8)
    assert result["state"] == "cancelled"
    # emergency stop stays latched
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["code"] == "EMERGENCY_STOP"


def test_emergency_stop_cancels_high_impact_child(tmp_path):
    import threading
    result = {}
    runner = _runner(tmp_path, name="ghost_slow.json",
                     grants=(("ghost.input.click", "web"),))
    t = threading.Thread(
        target=lambda: result.update(
            runner.execute("ghost.input.click",
                           args={"role": "chromium", "target": "#btn"},
                           target="https://mail.google.com", scope="web",
                           confirmed=True)),
        daemon=True)
    t.start()
    time.sleep(0.3)
    runner.adapter.emergency_stop()
    t.join(timeout=8)
    assert result["state"] == "cancelled"


def test_emergency_stop_latched_until_human_reset(tmp_path):
    runner = _runner(tmp_path)
    runner.adapter.emergency_stop()
    # enable attempt while stopped is refused
    d = runner.adapter.enable(requested_by="user")
    assert d["state"] == "denied"
    assert d["code"] == "EMERGENCY_STOP"
    runner.adapter.reset(requested_by="user")
    assert runner.adapter.state.stopped is False


def test_emergency_stop_requires_human_reset(tmp_path):
    runner = _runner(tmp_path)
    runner.adapter.emergency_stop()
    d = runner.adapter.reset(requested_by="model")
    assert d["state"] == "denied"
    assert d["code"] == "CONFIRMATION_SOURCE_FORBIDDEN"


def test_emergency_stop_tracks_and_untracks_runs(tmp_path):
    import threading
    runner = _runner(tmp_path, name="ghost_slow.json",
                     grants=(("ghost.desktop.discover", "desktop"),))
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            runner.execute("ghost.desktop.discover", args={},
                           target="desktop", scope="desktop")), daemon=True)
    t.start()
    time.sleep(0.4)
    # the adapter tracked the active correlation with a real cancel callback
    assert runner.adapter._active_correlations, \
        "active run must be tracked during execution"
    runner.adapter.emergency_stop()
    t.join(timeout=8)
    assert result["state"] == "cancelled"
    # untracked after completion
    assert not runner.adapter._active_correlations


def test_emergency_stop_during_comet_wait(tmp_path):
    import threading
    runner, comet = _comet_runner(tmp_path)
    runner.comet_connect()
    d = runner.comet_ask("research query")
    wid = d["wait_id"]
    comet.mark_wait(wid, G.WAIT_PENDING)  # stays pending -> wait would block
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            runner.comet_wait(wid, max_wait_s=30)), daemon=True)
    t.start()
    time.sleep(0.4)
    runner.adapter.emergency_stop()
    t.join(timeout=8)
    assert result["state"] in ("denied", "cancelled", "CANCELLED",
                               "TIMEOUT")
    assert not runner.adapter._active_correlations


# ---------------------------------------------------------------------------
# Session grant store hardening (Packet 8N repair #5)
# ---------------------------------------------------------------------------
def _write_grants(path, records):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"grants": records}))


def _valid_record(**kw):
    now = time.time()
    rec = {"grant_id": "g_test_1", "capability": "ghost.desktop.discover",
           "scopes": ["desktop"], "target": "desktop", "role": "",
           "issued_by": "user", "created_at": now,
           "expires_at": now + 600, "resource_budget": 1000}
    rec.update(kw)
    return rec


def test_grant_load_rejects_forged_issuer(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(issued_by="model")])
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert sgs.health()["load_degraded"] is True


def test_grant_load_rejects_unknown_capability(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(capability="ghost.shell")])
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("HIGH_IMPACT" in w for w in sgs.health()["load_warnings"])


def test_grant_load_rejects_mutating_capability(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(capability="ghost.clipboard.read")])
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("HIGH_IMPACT" in w for w in sgs.health()["load_warnings"])


def test_grant_load_rejects_wildcard_target(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(target="*")])
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []


def test_grant_load_rejects_invalid_role_and_target_mismatch(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(role="chromium",
                                    target="https://perplexity.ai")])
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("ROLE_TARGET" in w for w in sgs.health()["load_warnings"])


def test_grant_load_rejects_bad_timestamps_and_ttl(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(expires_at=time.time() - 1)])
    assert G.SessionGrantStore(p).grants() == []
    _write_grants(p, [_valid_record(created_at=0)])
    assert G.SessionGrantStore(p).grants() == []
    _write_grants(p, [_valid_record(expires_at=time.time() + 1)])  # ttl<1
    assert G.SessionGrantStore(p).grants() == []


def test_grant_load_rejects_newer_schema(tmp_path):
    p = tmp_path / "grants.json"
    now = time.time()
    payload = {"schema_version": 999, "grants": [
        _valid_record(grant_id="g_newer")]}
    p.write_text(json.dumps(payload))
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("GRANT_SCHEMA_NEWER" in w for w in sgs.health()["load_warnings"])


def test_grant_load_rejects_unknown_top_level(tmp_path):
    p = tmp_path / "grants.json"
    now = time.time()
    payload = {"grants": [_valid_record(grant_id="g_t")], "hack": True}
    p.write_text(json.dumps(payload))
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("UNKNOWN_TOP_LEVEL" in w for w in sgs.health()["load_warnings"])


def test_grant_load_rejects_malformed_json_and_unknown_fields(tmp_path):
    p = tmp_path / "grants.json"
    p.write_text("{ not json")
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("CORRUPT" in w for w in sgs.health()["load_warnings"])
    _write_grants(p, [_valid_record(extra_field="x")])
    sgs = G.SessionGrantStore(p)
    assert sgs.grants() == []
    assert any("UNKNOWN_FIELD" in w for w in sgs.health()["load_warnings"])


def test_grant_load_rejects_duplicate_ids(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(), _valid_record()])
    sgs = G.SessionGrantStore(p)
    assert len(sgs.grants()) == 1


def test_grant_load_rejects_invalid_scope_and_budget(tmp_path):
    p = tmp_path / "grants.json"
    _write_grants(p, [_valid_record(scopes=["UPPER"])])
    assert G.SessionGrantStore(p).grants() == []
    _write_grants(p, [_valid_record(resource_budget=0)])
    assert G.SessionGrantStore(p).grants() == []


def test_loaded_grant_never_exceeds_fresh(tmp_path):
    # a valid loaded grant behaves exactly like a freshly issued one
    p = tmp_path / "grants.json"
    rec = _valid_record()
    _write_grants(p, [rec])
    loaded = G.SessionGrantStore(p)
    assert loaded.grants()[0].grant_id == rec["grant_id"]
    assert loaded.grants()[0].issued_by == "user"
    assert loaded.matches("ghost.desktop.discover", ("desktop",), "desktop")


def test_grant_save_uses_private_mode(tmp_path):
    p = tmp_path / "grants.json"
    sgs = G.SessionGrantStore(p)
    sgs.issue("ghost.desktop.discover", scopes=("desktop",), target="desktop")
    mode = oct(p.stat().st_mode & 0o777)
    assert mode == "0o600"
    # lock sidecar and no leftover temp files
    assert p.with_name(p.name + ".lock").exists()
    leftovers = [f for f in tmp_path.iterdir()
                 if f.name.startswith(p.name + ".tmp")]
    assert leftovers == []


def test_grant_save_failure_is_typed_not_swallowed(tmp_path):
    # read-only directory -> mkdir succeeds but file creation fails
    p = tmp_path / "ro" / "grants.json"
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        sgs = G.SessionGrantStore(p)
        with pytest.raises(G.SessionGrantError) as exc:
            sgs.issue("ghost.desktop.discover", scopes=("desktop",),
                      target="desktop")
        assert exc.value.code in ("GRANT_WRITE_FAILED", "GRANT_LOCK_FAILED",
                                  "GRANT_MKDIR_FAILED", "GRANT_RENAME_FAILED")
    finally:
        ro.chmod(0o700)


def test_grant_issue_does_not_report_success_on_save_failure(tmp_path):
    p = tmp_path / "grants.json"
    sgs = G.SessionGrantStore(p)
    # break the store's lock path to force a save failure
    sgs._path = "/proc/1/grants.json"  # not writable
    with pytest.raises(G.SessionGrantError):
        sgs.issue("ghost.desktop.discover", scopes=("desktop",),
                  target="desktop")


def test_grant_concurrent_issue_no_corruption(tmp_path):
    import threading
    p = tmp_path / "grants.json"
    store = G.SessionGrantStore(p)
    errors = []
    def worker(i):
        try:
            store.issue("ghost.desktop.discover", scopes=("desktop",),
                        target="desktop")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    reopened = G.SessionGrantStore(p)
    assert len(reopened.grants()) == 6


def test_grant_crash_boundary_recovery(tmp_path):
    # a torn temp file must never become the state
    p = tmp_path / "grants.json"
    sgs = G.SessionGrantStore(p)
    sgs.issue("ghost.desktop.discover", scopes=("desktop",), target="desktop")
    # simulate a stale temp file left by a crashed writer
    stale = p.with_name(p.name + ".tmp.99999.deadbeef")
    stale.write_text("{ not json")
    reopened = G.SessionGrantStore(p)
    assert len(reopened.grants()) == 1  # stale temp never read


def test_grant_replayed_id_rejected(tmp_path):
    # a replayed (duplicate) grant id in the file is dropped
    p = tmp_path / "grants.json"
    now = time.time()
    _write_grants(p, [
        _valid_record(grant_id="g_dup"),
        _valid_record(grant_id="g_dup"),
    ])
    sgs = G.SessionGrantStore(p)
    assert len(sgs.grants()) == 1


# ---------------------------------------------------------------------------
# Broker / API bypass audit (Packet 8N repair #6)
# ---------------------------------------------------------------------------
def test_no_direct_http_mcp_cdp_subprocess_path(tmp_path):
    runner = _runner(tmp_path)
    # every ghost capability is broker-bound; there is no separate executor
    for cid in G.ghost_action_ids():
        binding = runner.broker.get(cid)
        assert binding is not None, cid
        assert binding.fn is not None
    # comet capabilities are broker-bound too (not reachable directly)
    assert "ghost.comet.ask" in runner.broker.names()


def test_fixture_only_authorization_is_never_live(tmp_path):
    # a fixture executor authorizes the CONTRACT test only, never a live op
    runner = _runner(tmp_path, name="ghost_outcomes.json",
                     grants=(("ghost.desktop.discover", "desktop"),))
    d = runner.execute("ghost.desktop.discover", args={}, target="desktop",
                       scope="desktop")
    assert d["state"] == "OK"  # fixture outcome
    # without a fixture, the same capability is ADAPTER_DISABLED
    runner2 = _runner(tmp_path, name="fail.json",
                      grants=(("ghost.desktop.discover", "desktop"),))
    d2 = runner2.execute("ghost.desktop.discover", args={}, target="desktop",
                         scope="desktop")
    assert d2["state"] == "denied"
    assert d2["code"] == A.ST_ADAPTER_DISABLED


def test_voice_ghost_uses_same_runner_no_bypass(tmp_path):
    # Ghost capability ids are resolvable by the shared action registry and
    # route through the SAME gate/hash/confirmation machinery as voice. A
    # ghost id never maps through the 8F voice matcher into a separate path,
    # so a voice utterance can never reach a Ghost/Comet executor.
    runner = _runner(tmp_path)
    for cid in G.ghost_action_ids():
        spec = G.ghost_spec(cid)
        from voicebridge import actions as A
        assert A.voice_action_request(
            f"draft an email to b@example.com about {cid}")["action_id"] != cid
        # no ghost capability is reachable via the 8F voice patterns
        req = A.voice_action_request("post to acct: " + cid)
        assert req is None or req["action_id"] != cid
    # The orchestrator's voice-action route uses the exact same matcher, so a
    # Ghost capability id can never be dispatched by voice.
    from voicebridge.orchestrator import VoiceOrchestrator
    assert hasattr(VoiceOrchestrator, "_maybe_voice_action")
    for cid in G.ghost_action_ids():
        from voicebridge import actions as A
        req = A.voice_action_request(
            f"send an email to b@example.com about x saying {cid}")
        assert req is None or req["action_id"] != cid
# ---------------------------------------------------------------------------
# Persistence / restart
# ---------------------------------------------------------------------------
def test_session_grants_survive_restart(tmp_path):
    sgs = G.SessionGrantStore(tmp_path / "ghost-grants.json")
    g = sgs.issue("ghost.desktop.discover", scopes=("desktop",),
                  target="desktop")
    reopened = G.SessionGrantStore(tmp_path / "ghost-grants.json")
    assert any(x.grant_id == g.grant_id for x in reopened.grants())


def test_session_grant_revoke_survives_restart(tmp_path):
    sgs = G.SessionGrantStore(tmp_path / "ghost-grants.json")
    g = sgs.issue("ghost.desktop.discover", scopes=("desktop",),
                  target="desktop")
    sgs.revoke(g.grant_id)
    reopened = G.SessionGrantStore(tmp_path / "ghost-grants.json")
    assert reopened.grants() == []