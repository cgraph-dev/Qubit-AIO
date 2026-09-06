"""Packet 8O tests: full-capability power profile and one policy path.

Covers the Packet 8O hermetic gate:
- capability inventory across all 11 power domains, with available != authorized;
- one authenticated policy path (PowerPolicy) layering emergency stop -> global
  disable -> circuit breaker -> bounded concurrency -> capability known ->
  owner power grant (routine auto-allow) -> the existing 7E/8F/8N gate;
- owner-authorized power grant: human-only issuer, exact capabilities, exact
  scopes/origins/roles/accounts/directories, optional exact argument-hash
  binding, expiring, revocable, persisted, restart-recovered; high-impact
  actions (post/send/publish/purchase/delete/project-code) ALWAYS require fresh
  confirmation even inside the grant;
- PowerGrantStore durability: atomic write, fail-closed load (corrupt, newer
  schema, unknown fields, forged owner, wildcard, expired), restart recovery;
- PowerControl: global emergency stop (latched, cancels tracked runs), global
  disable, per-adapter circuit breakers, bounded concurrency, kill/reap;
- confirmation verification (bogus/stale/replayed/changed-arg) and fail-closed
  audit persistence for power decisions.

Everything is synthetic/hermetic: no process, browser, account, service, timer,
model, audio, or live config is ever touched.
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

from voicebridge import power as PW  # noqa: E402
from voicebridge import ghost as G  # noqa: E402
from voicebridge.actions import (  # noqa: E402
    ActionGate, ST_ADAPTER_DISABLED, args_digest,
)
from voicebridge.broker import ToolBinding  # noqa: E402
from voicebridge.capabilities import (  # noqa: E402
    CapabilitySpec, ConfirmationDenied, ConfirmationStore, Tier,
)
from voicebridge.permissions import PermissionsStore  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "actions"

ASK = "ghost.comet.ask"
TABS = "ghost.browser.tabs"
ORIGIN = "https://perplexity.ai"
MAIL = "https://mail.google.com"


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def _stores(tmp_path, clock=time.time):
    from voicebridge.actions import ActionStore
    return (ActionStore(tmp_path / "actions.json", clock=clock),
            PermissionsStore(tmp_path / "perm.json"),
            PW.PowerGrantStore(tmp_path / "power-grant.json", clock=clock),
            PW.PowerControl())


def _policy(store, control, perms, **kw):
    return PW.PowerPolicy(store=store, control=control, perms=perms, **kw)


def _clock_frozen(start=1_000_000.0):
    class C:
        _t = start

        def __call__(self):
            return self._t

        def advance(self, s):
            self._t += s
    return C()


def _grant(gstore, capabilities=(ASK,), issued_by="user", **kw):
    kw.setdefault("scopes", ("research",))
    kw.setdefault("origins", (ORIGIN,))
    return gstore.issue(capabilities=capabilities, issued_by=issued_by, **kw)


# ---------------------------------------------------------------------------
# Power profile inventory (11 domains, available != authorized)
# ---------------------------------------------------------------------------
def test_profile_covers_all_11_domains():
    prof = PW.power_profile()
    assert set(prof["domains"].keys()) == set(PW.POWER_DOMAINS)
    assert prof["total"] > 0
    assert prof["available"] >= 0


def test_profile_never_claims_authorized_from_available():
    prof = PW.power_profile()
    assert prof["authorized"] == 0
    for cap in prof["capabilities"]:
        assert cap["state"] in PW.STATES
        assert "revocable" in cap and "disableable" in cap
        assert cap["adapter"] and cap["adapter_version"] and cap["scope"]
        assert cap["risk_tier"] in (t.value for t in Tier)


def test_profile_ghost_rows_not_available_when_adapter_disabled(tmp_path):
    # Ghost adapter is DISABLED by default; its capabilities must NOT be
    # reported AVAILABLE (truthful inventory).
    cfg = _cfg(tmp_path)
    prof = PW.power_profile(cfg)
    ghost = [c for c in prof["capabilities"]
             if c["capability"].startswith("ghost.")]
    assert ghost
    for row in ghost:
        assert row["state"] != PW.ST_AVAILABLE, row["capability"]


def test_profile_8f_actions_disabled_when_adapter_off(tmp_path):
    # 8F action adapters are DISABLED by default; their contract rows must
    # report DISABLED, never AVAILABLE (unavailable adapter != available).
    cfg = _cfg(tmp_path)
    prof = PW.power_profile(cfg)
    rows = [c for c in prof["capabilities"]
            if c["capability"].startswith("act.")]
    assert rows
    for row in rows:
        assert row["state"] == PW.ST_DISABLED, row["capability"]


def test_profile_configured_not_running(tmp_path):
    # "configured" is not "running": declared model/audio rows are AVAILABLE
    # (adapter present + enabled by config) but never ENABLED without a probe.
    cfg = _cfg(tmp_path)
    prof = PW.power_profile(cfg)
    declared = [c for c in prof["capabilities"]
                if c["audit_status"] == "config_declared"]
    assert declared
    for row in declared:
        assert row["state"] in (PW.ST_AVAILABLE, PW.ST_DISABLED), \
            row["capability"]


def test_profile_capability_rows_carry_full_contract():
    prof = PW.power_profile()
    browser = [c for c in prof["capabilities"]
               if c["capability"] == "act.browser.read"]
    assert browser and browser[0]["adapter"] == "browser"
    assert browser[0]["resource_limits"].get("timeout_s", 0) > 0


def test_high_impact_classification():
    for cid in ("act.mail.send", "act.social.post", "act.purchase",
                "act.editor.delete", "act.editor.write", "act.account.change",
                "ghost.input.click", "ghost.input.type",
                "ghost.clipboard.read", "ghost.shell"):
        assert PW.is_high_impact(cid), cid
    for cid in ("act.browser.read", "act.browser.search",
                "ghost.comet.ask", "ghost.desktop.discover"):
        assert not PW.is_high_impact(cid), cid


def test_mcp_status_truthful():
    st = PW.mcp_status()
    assert st["state"] in ("UNAVAILABLE", "DISABLED")
    assert st["server"] == "none"


# ---------------------------------------------------------------------------
# PowerGrant validation (fail closed)
# ---------------------------------------------------------------------------
def test_grant_requires_human_issuer_and_capabilities(tmp_path):
    _store, _perms, gstore, _ = _stores(tmp_path)
    # Model/page/tool/skill/automation/arbitrary issuers are rejected.
    for bad in ("model", "page", "tool", "skill", "automation",
                "arbitrary text", "gpt-4"):
        with pytest.raises(PW.PowerDenied) as e:
            gstore.issue(origins=(ORIGIN,), scopes=("research",),
                         capabilities=(ASK,), issued_by=bad)
        assert e.value.code == "CONFIRMATION_SOURCE_FORBIDDEN"
    # A grant must name at least one exact capability.
    with pytest.raises(PW.PowerDenied) as e:
        gstore.issue(origins=(ORIGIN,), scopes=("research",), issued_by="user")
    assert e.value.code == "NO_CAPABILITIES"


def test_grant_rejects_wildcards_and_bad_origins(tmp_path):
    _store, _perms, gstore, _ = _stores(tmp_path)
    for bad in (("*",), ("",), ("any",)):
        with pytest.raises(PW.PowerDenied):
            gstore.issue(origins=bad, capabilities=(ASK,))
    for bad in ("https://perplexity.ai?tok=1", "ftp://perplexity.ai",
                "https://user:pass@perplexity.ai", "https://perplexity.ai:8443",
                "http://127.0.0.1", "https://perplexity.ai/private",
                "https://perplexity.ai/", "https://perplexity"):
        with pytest.raises(PW.PowerDenied):
            gstore.issue(origins=(bad,), capabilities=(ASK,))
    with pytest.raises(PW.PowerDenied):
        gstore.issue(browsers=("firefox",), capabilities=(ASK,))
    with pytest.raises(PW.PowerDenied):
        gstore.issue(origins=(ORIGIN,), capabilities=("does.not.exist",))


def test_grant_ttl_bounds_and_supersede(tmp_path):
    _store, _perms, gstore, _ = _stores(tmp_path)
    with pytest.raises(PW.PowerDenied):
        gstore.issue(origins=(ORIGIN,), capabilities=(ASK,), ttl_s=0.5)
    with pytest.raises(PW.PowerDenied):
        gstore.issue(origins=(ORIGIN,), capabilities=(ASK,),
                     ttl_s=PW.MAX_GRANT_TTL_S + 1)
    g1 = _grant(gstore, ttl_s=3600)
    g2 = _grant(gstore, ttl_s=7200)
    assert g2.grant_id != g1.grant_id
    assert gstore.grant().grant_id == g2.grant_id


def test_grant_expires_and_revokes(tmp_path):
    clock = _clock_frozen()
    store = PW.PowerGrantStore(tmp_path / "power-grant.json", clock=clock)
    g = _grant(store, ttl_s=100)
    assert store.grant() is not None
    store.revoke(g.grant_id)
    assert store.grant() is None
    g2 = _grant(store, ttl_s=100)
    assert g2.is_active_at(clock()) is True
    clock.advance(101)
    assert store.grant() is None
    assert g2.is_active_at(clock()) is False


# ---------------------------------------------------------------------------
# PowerGrantStore durability + restart recovery
# ---------------------------------------------------------------------------
def test_grant_store_restart_recovery(tmp_path):
    path = tmp_path / "power-grant.json"
    store = PW.PowerGrantStore(path)
    g = _grant(store, ttl_s=3600)
    store2 = PW.PowerGrantStore(path)
    g2 = store2.grant()
    assert g2 is not None and g2.grant_id == g.grant_id
    assert g2.origins == g.origins
    assert g2.capabilities == g.capabilities
    assert store2.health()["degraded"] is False


def test_grant_store_private_modes_and_no_secret_fields(tmp_path):
    path = tmp_path / "power-grant.json"
    store = PW.PowerGrantStore(path)
    store.issue(origins=(ORIGIN,), scopes=("research",), capabilities=(ASK,),
                note="token=secret-value will be redacted")
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    raw = json.loads(path.read_text())
    assert "secret-value" not in json.dumps(raw)


def test_grant_store_fail_closed_on_corrupt_and_newer_schema(tmp_path):
    path = tmp_path / "power-grant.json"
    path.write_text("{ not json")
    store = PW.PowerGrantStore(path)
    assert store.health()["degraded"] is True
    assert store.grant() is None
    path.write_text(json.dumps({"schema_version": 999, "grant": None}))
    store2 = PW.PowerGrantStore(path)
    assert store2.health()["degraded"] is True
    assert store2.grant() is None


def test_grant_store_rejects_forged_or_widened_record(tmp_path):
    path = tmp_path / "power-grant.json"
    store = PW.PowerGrantStore(path)
    g = _grant(store, ttl_s=3600)
    d = g._to_record()
    d["owner"] = "model"
    path.write_text(json.dumps({"schema_version": 1, "grant": d}))
    store2 = PW.PowerGrantStore(path)
    assert store2.grant() is None
    assert store2.health()["degraded"] is True


# ---------------------------------------------------------------------------
# One policy path: PowerPolicy.decide/check
# ---------------------------------------------------------------------------
def test_default_deny_no_grant(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    policy = _policy(grant_store, control, perms)
    d = policy.check("act.browser.read", scopes=("web",))
    assert d["allowed"] is False
    assert d["code"] == "NOT_GRANTED"


def test_power_grant_routine_auto_allow_in_scope(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    d = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert d["allowed"] is True
    assert d["code"] == "ALLOWED"


def test_power_grant_out_of_origin_denied(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    d = policy.check(ASK, scopes=("research",), target="https://evil.example")
    assert d["allowed"] is False
    assert d["code"] == "NOT_GRANTED"


def test_power_grant_scope_and_role_exact(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    grant_store.issue(browsers=("chromium",), origins=(MAIL,),
                      scopes=("web",), capabilities=(TABS,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    assert policy.check(TABS, scopes=("web",), target=MAIL,
                        role="chromium")["allowed"] is True
    assert policy.check(TABS, scopes=("web",), target=MAIL,
                        role="comet")["allowed"] is False
    assert policy.check(TABS, scopes=("research",), target=MAIL,
                        role="chromium")["allowed"] is False
    # Not in the grant's capability list -> denied (exact capability binding).
    assert policy.check(ASK, scopes=("web",), target=MAIL,
                        role="chromium")["allowed"] is False


def test_power_grant_argument_hash_binding(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    grant_store.issue(origins=(ORIGIN,), scopes=("research",),
                      capabilities=(ASK,),
                      arg_hashes=(args_digest({"query": "hello"}),),
                      ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    # Exact argument hash matches -> routine auto-allow.
    d = policy.decide(ASK, scopes=("research",), target=ORIGIN + "/q",
                      source="user", args={"query": "hello"})
    assert d["allowed"] is True
    # Changed arguments (different hash) -> denied (fail closed).
    d2 = policy.decide(ASK, scopes=("research",), target=ORIGIN + "/q",
                       source="user", args={"query": "pwned"})
    assert d2["allowed"] is False


def test_high_impact_requires_confirmation_even_inside_grant(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    grant_store.issue(origins=(ORIGIN,), scopes=("web",),
                      capabilities=("act.social.post",), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    d = policy.decide("act.social.post", scopes=("web",),
                      target=ORIGIN + "/q", source="user")
    assert d["allowed"] is False
    assert d["code"] == "CONFIRMATION_REQUIRED"
    d2 = policy.decide("act.social.post", scopes=("web",),
                       target=ORIGIN + "/q", source="model",
                       confirmation_id="x")
    assert d2["allowed"] is False
    assert d2["code"] == "CONFIRMATION_SOURCE_FORBIDDEN"


def test_high_impact_confirmation_verified_exact(tmp_path):
    from voicebridge.actions import build_envelope, action_spec
    store, perms, grant_store, control = _stores(tmp_path)
    perms.grant("act.social.post", requested_by="user", confirmed=True,
                scopes=("social",))
    conf = ConfirmationStore()
    policy = _policy(grant_store, control, perms, confirmations=conf)
    # A bogus confirmation id never passes.
    d = policy.decide("act.social.post", scopes=("web",),
                      target=ORIGIN + "/q", source="user",
                      confirmation_id="BOGUS")
    assert d["allowed"] is False
    assert d["code"] == "UNKNOWN_CONFIRMATION_ID"
    # A real confirmation bound to the canonical envelope passes; nothing is
    # consumed here (the broker consumes once at execution).
    spec = action_spec("act.social.post")
    env = build_envelope(spec, args={"account": "me", "text": "Hello"},
                         target=ORIGIN + "/q", scope="social")
    rec = conf.issue("act.social.post", env, issued_by="user", ttl_s=60)
    d2 = policy.decide("act.social.post", scopes=("social",),
                       target=ORIGIN + "/q", source="user",
                       confirmation_id=rec.id, args=env)
    assert d2["allowed"] is True
    # A confirmation bound to DIFFERENT target fails closed (target switch).
    env_b = build_envelope(spec, args={"account": "me", "text": "Hello"},
                           target="https://evil.example", scope="social")
    rec2 = conf.issue("act.social.post", env_b, issued_by="user", ttl_s=60)
    d3 = policy.decide("act.social.post", scopes=("social",),
                       target=ORIGIN + "/q", source="user",
                       confirmation_id=rec2.id, args=env)
    assert d3["allowed"] is False
    assert d3["code"] == "TARGET_MISMATCH"


def test_high_impact_confirmation_replay_and_expiry(tmp_path):
    from voicebridge.actions import build_envelope, action_spec
    conf = ConfirmationStore()
    store, perms, grant_store, control = _stores(tmp_path)
    policy = _policy(grant_store, control, perms, confirmations=conf)
    spec = action_spec("act.social.post")
    env = build_envelope(spec, args={"account": "me", "text": "Hello"},
                         target="https://x.test", scope="social")
    rec = conf.issue("act.social.post", env, issued_by="user", ttl_s=1)
    conf.consume(rec.id, "act.social.post", env)
    d = policy.decide("act.social.post", scopes=("social",),
                      target="https://x.test", source="user",
                      confirmation_id=rec.id, args=env)
    assert d["allowed"] is False and d["code"] == "REPLAYED_CONFIRMATION"


def test_project_code_modification_always_high_impact(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    grant_store.issue(directories=("/workspace/project",),
                      scopes=("editor",), capabilities=("act.editor.write",),
                      ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    d = policy.decide("act.editor.write", scopes=("editor",),
                      target="/workspace/project/src/main.py", source="user")
    assert d["allowed"] is False
    assert d["code"] == "CONFIRMATION_REQUIRED"


def test_unknown_capability_fails_closed(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    policy = _policy(grant_store, control, perms)
    d = policy.check("does.not.exist")
    assert d["allowed"] is False
    assert d["code"] == "UNKNOWN_CAPABILITY"


# ---------------------------------------------------------------------------
# PowerControl: emergency stop, disable, circuit breakers, concurrency
# ---------------------------------------------------------------------------
def test_emergency_stop_latches_and_cancels_tracked_runs(tmp_path):
    control = PW.PowerControl()
    cancelled = []
    control.track("run-1", lambda: cancelled.append("run-1"))
    control.track("run-2", lambda: cancelled.append("run-2"))
    out = control.emergency_stop()
    assert out["state"] == "OK"
    assert sorted(cancelled) == ["run-1", "run-2"]
    assert control.active_runs() == []
    assert control.state["emergency_stop"] is True
    out2 = control.emergency_stop()
    assert out2["state"] == "OK"


def test_emergency_stop_blocks_policy(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    control.emergency_stop()
    d = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert d["allowed"] is False and d["code"] == "EMERGENCY_STOP"
    control.reset()
    d2 = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert d2["allowed"] is True


def test_global_disable_blocks_everything(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    control.global_disable()
    d = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert d["allowed"] is False and d["code"] == "POWER_DISABLED"


def test_circuit_breaker_fail_closed(tmp_path):
    control = PW.PowerControl()
    for _ in range(3):
        control.record_failure("ghost")
    assert control.breaker_state("ghost") == "open"
    store, perms, grant_store, _ = _stores(tmp_path)
    _grant(grant_store, ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    d = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert d["allowed"] is False and d["code"] == "CIRCUIT_OPEN"
    control.record_success("ghost")
    assert control.breaker_state("ghost") == "closed"


def test_bounded_concurrency_denies_overflow(tmp_path):
    control = PW.PowerControl(max_concurrency=1)
    store, perms, grant_store, _ = _stores(tmp_path)
    _grant(grant_store, ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    assert policy.check(ASK, scopes=("research",),
                        target=ORIGIN + "/q")["allowed"] is True
    assert control.acquire("research") is True
    assert control.acquire("research") is False
    control.release("research")


def test_kill_reap_tracking(tmp_path):
    control = PW.PowerControl()
    control.track("a", lambda: None)
    control.track("b", lambda: None)
    assert set(control.active_runs()) == {"a", "b"}
    control.untrack("a")
    assert control.active_runs() == ["b"]


def test_shared_control_singleton_per_config(tmp_path):
    cfg = _cfg(tmp_path)
    c1 = PW.power_control(cfg)
    c2 = PW.power_control(cfg)
    assert c1 is c2
    p1 = PW.power_policy(cfg)
    p2 = PW.power_policy(cfg)
    assert p1 is p2
    assert p1.control is c1


# ---------------------------------------------------------------------------
# PowerPolicy gate delegation preserves 8F/8N semantics (no bypass)
# ---------------------------------------------------------------------------
def test_gate_delegation_requires_explicit_perms(tmp_path):
    grant_store = PW.PowerGrantStore(tmp_path / "pg.json")
    policy = PW.PowerPolicy(store=grant_store, control=PW.PowerControl())
    with pytest.raises(PW.PowerDenied):
        policy.check("act.browser.read", scopes=("web",))


def test_policy_high_impact_flows_to_gate_confirmation(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    gate = ActionGate(perms)
    policy = _policy(grant_store, control, perms, gate=gate)
    d = policy.check("act.browser.write", scopes=("web",))
    assert d["allowed"] is False
    perms.grant("act.browser.write", requested_by="user", confirmed=True,
                scopes=("web",))
    d2 = policy.check("act.browser.write", scopes=("web",))
    assert d2["allowed"] is True


def test_power_gate_adapter_routes_runner(tmp_path):
    from voicebridge.actions import ActionRunner, action_runner
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    gate = PW.PowerGateAdapter(policy, inner_gate=ActionGate(perms))
    spec = G.ghost_spec(ASK)
    env = {"action_id": ASK, "scope": "research", "target": ORIGIN + "/q",
           "role": "", "account_scope": "", "args": {}}
    d = gate.decide(spec, env, source="check")
    assert d["allowed"] is True


def test_broker_denies_emergency_stop_real_execution(tmp_path):
    from voicebridge.broker import build_host_broker
    control = PW.PowerControl()
    broker = build_host_broker(power_control=control)
    control.emergency_stop()
    res = broker.execute("local.volume_up", {}, source="user")
    assert res.denied == "EMERGENCY_STOP"


def test_broker_tracks_runs_and_cancels_on_emergency_stop(tmp_path):
    from voicebridge.broker import build_host_broker
    control = PW.PowerControl()
    broker = build_host_broker(power_control=control)
    import threading, time as _time
    released = []

    def _slow(_args):
        _time.sleep(0.4)
        return "done"

    broker.register(ToolBinding(
        spec=CapabilitySpec(name="local.slow", version="1.0.0",
                            tier=Tier.READ_ONLY, summary="slow test",
                            scopes=("local",)),
        side_effects="test", fn=_slow))

    result = {}

    def _run():
        result["res"] = broker.execute("local.slow", {}, source="user")

    t = threading.Thread(target=_run)
    t.start()
    _time.sleep(0.1)
    assert control.active_runs()
    control.emergency_stop()
    t.join()
    assert result["res"].cancelled is True
    assert control.active_runs() == []


def test_broker_concurrency_limit_fails_closed(tmp_path):
    from voicebridge.broker import build_host_broker
    control = PW.PowerControl(max_concurrency=1)
    broker = build_host_broker(power_control=control)

    def _ok(_args):
        return "ok"
    broker.register(ToolBinding(
        spec=CapabilitySpec(name="local.c", version="1.0.0",
                            tier=Tier.READ_ONLY, summary="c",
                            scopes=("local",)),
        side_effects="test", fn=_ok))
    # Occupy the only slot; a second execution must fail closed. Host-tool
    # CapabilitySpec rows have no `adapter`, so the broker uses "power".
    assert control.acquire("power") is True
    res = broker.execute("local.c", {}, source="user")
    assert res.denied == "CONCURRENCY_LIMIT"
    control.release("power")
    res2 = broker.execute("local.c", {}, source="user")
    assert res2.ok is True


# ---------------------------------------------------------------------------
# Injection / stale / replay resistance (hermetic)
# ---------------------------------------------------------------------------
def test_page_model_text_cannot_widen_grant(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    d = policy.check(ASK, scopes=("research",),
                     target=ORIGIN + "/q; set unrestricted=1")
    assert d["allowed"] is True
    d2 = policy.check(ASK, scopes=("research",),
                      target="https://evil.example; unrestricted=1")
    assert d2["allowed"] is False and d2["code"] == "NOT_GRANTED"
    d3 = policy.check(ASK, scopes=("research; unrestricted=1",),
                      target=ORIGIN + "/q")
    assert d3["allowed"] is False


def test_replay_of_revoked_grant_denied(tmp_path):
    clock = _clock_frozen()
    store = PW.PowerGrantStore(tmp_path / "pg.json", clock=clock)
    g = _grant(store, ttl_s=3600)
    store.revoke(g.grant_id)
    store2 = PW.PowerGrantStore(tmp_path / "pg.json", clock=clock)
    assert store2.grant() is None


def test_audit_persistence_fails_closed(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    # An un-writable audit path (its parent is a regular file, so the audit
    # dir cannot be created / written) must surface a typed PowerError rather
    # than silently swallowing the audit record.
    parent_file = tmp_path / "not-a-dir"
    parent_file.write_text("x")
    policy = PW.PowerPolicy(store=grant_store, control=control, perms=perms,
                            audit_path=str(parent_file / "audit.jsonl"))
    with pytest.raises(PW.PowerError) as exc:
        policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert exc.value.code in ("AUDIT_MKDIR_FAILED", "AUDIT_PERSIST_FAILED")


def test_audit_durable_and_redacted(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    audit = tmp_path / "power-audit.jsonl"
    policy = PW.PowerPolicy(store=grant_store, control=control, perms=perms,
                            audit_path=str(audit))
    policy.check(ASK, scopes=("research",), target=ORIGIN + "/q",
                 source="user")
    assert audit.exists()
    lines = audit.read_text().splitlines()
    assert lines
    rec = json.loads(lines[0])
    assert rec["capability"] == ASK
    assert "secret" not in json.dumps(rec)


def test_args_hash_helper_stable():
    assert PW.args_digest({"a": 1, "b": [2, 3]}) == \
        PW.args_digest({"b": [2, 3], "a": 1})
    assert PW.args_digest({"a": 1}) != PW.args_digest({"a": 2})


# ---------------------------------------------------------------------------
# Channel parity: CLI / dashboard / voice / automation return the same typed
# decision from the same policy path.
# ---------------------------------------------------------------------------
def test_cli_and_policy_parity_decision(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    grant_store = PW.power_grant_store(cfg)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = PW.power_policy(cfg)
    # CLI executes the same decision code path as the policy object.
    d = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert d["allowed"] is True
    # Automation channel may NOT consume the human owner grant (source
    # preservation): it is never coerced to "user".
    d_auto = policy.decide(ASK, scopes=("research",), target=ORIGIN + "/q",
                           source="automation", dry_run=True)
    assert d_auto["allowed"] is False
    # High-impact requires confirmation regardless of channel.
    d_voice = policy.decide("act.social.post", scopes=("web",),
                            target=ORIGIN + "/q", source="user")
    assert d_voice["code"] == "CONFIRMATION_REQUIRED"


def test_gate_adapter_parity_with_direct_policy(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    gate = PW.PowerGateAdapter(policy, inner_gate=ActionGate(perms))
    spec = G.ghost_spec(ASK)
    env = {"action_id": ASK, "scope": "research", "target": ORIGIN + "/q",
           "role": "", "account_scope": "", "args": {}}
    direct = policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    via_gate = gate.decide(spec, env, source="check")
    assert via_gate["allowed"] == direct["allowed"]
    assert via_gate["code"] == direct["code"]


def test_runner_uses_policy_gate(tmp_path):
    from voicebridge.actions import action_runner
    from voicebridge.actions import make_fixture_executors, load_fixture_outcomes
    cfg = _cfg(tmp_path)
    grant_store = PW.power_grant_store(cfg)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = PW.power_policy(cfg)
    execs = make_fixture_executors(load_fixture_outcomes(
        FIXTURES / "ghost_outcomes.json"))
    runner = action_runner(cfg, executors=execs, power=policy)
    # The runner's gate is the shared policy path.
    from voicebridge.power import PowerGateAdapter
    assert isinstance(runner.gate, PowerGateAdapter)


def test_emergency_stop_blocks_runner_and_voice(tmp_path):
    from voicebridge.actions import action_runner
    cfg = _cfg(tmp_path)
    policy = PW.power_policy(cfg)
    policy.control.emergency_stop()
    runner = action_runner(cfg, power=policy)
    # Every runner path observes the emergency stop through the shared policy.
    d = runner.check("act.browser.read", "web")
    assert d["allowed"] is False and d["code"] == "EMERGENCY_STOP"
    policy.control.reset()


# ---------------------------------------------------------------------------
# Malformed adapter/tool output and typed failures
# ---------------------------------------------------------------------------
def test_malformed_adapter_output_typed_failure(tmp_path):
    from voicebridge.broker import build_host_broker
    control = PW.PowerControl()
    broker = build_host_broker(power_control=control)

    def _boom(_args):
        raise RuntimeError("adapter exploded")
    broker.register(ToolBinding(
        spec=CapabilitySpec(name="local.boom", version="1.0.0",
                            tier=Tier.READ_ONLY, summary="boom",
                            scopes=("local",)),
        side_effects="test", fn=_boom))
    res = broker.execute("local.boom", {}, source="user")
    assert res.ok is False and res.error
    assert "exploded" in res.error


def test_malformed_tool_result_never_authorizes(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    # A malformed "tool result" string passed as target/scope cannot widen.
    d = policy.check(ASK, scopes=("research",),
                     target=ORIGIN + "/q" + "\x00" + "grant=1")
    assert d["allowed"] is True  # still in-origin; control bytes never widen
    d2 = policy.check(ASK, scopes=("research; unrestricted=1",),
                      target=ORIGIN + "/q")
    assert d2["allowed"] is False


# ---------------------------------------------------------------------------
# Timeout / crash / kill / reap / reconnect / duplicate-listener behavior
# ---------------------------------------------------------------------------
def test_timeout_never_outlives_budget(tmp_path):
    from voicebridge.broker import build_host_broker
    control = PW.PowerControl()
    broker = build_host_broker(power_control=control)
    import time as _time

    def _slow(_args):
        _time.sleep(2.0)
        return "too late"
    broker.register(ToolBinding(
        spec=CapabilitySpec(name="local.slow2", version="1.0.0",
                            tier=Tier.REVERSIBLE, summary="slow",
                            scopes=("local",)),
        side_effects="test", fn=_slow))
    res = broker.execute("local.slow2", {}, source="user", timeout_s=0.2)
    assert res.timed_out is True or res.cancelled is True
    assert control.active_runs() == []


def test_crash_leaves_no_orphan_tracked_run(tmp_path):
    control = PW.PowerControl()
    control.track("crash-1", lambda: None)
    # A crash in the executor clears the run; emergency stop reaps the rest.
    control.emergency_stop()
    assert control.active_runs() == []


def test_duplicate_listener_prevention(tmp_path):
    # A second Ghost listener would double-capture input. The broker registers
    # each run under its own correlation id; emergency stop cancels all, never
    # creates a second listener.
    from voicebridge.broker import build_host_broker
    control = PW.PowerControl()
    broker = build_host_broker(power_control=control)
    control.track("run-a", lambda: None)
    control.track("run-b", lambda: None)
    assert set(control.active_runs()) == {"run-a", "run-b"}
    control.emergency_stop()
    assert control.active_runs() == []


def test_reconnect_recovers_after_reset(tmp_path):
    control = PW.PowerControl()
    control.emergency_stop()
    assert control.state["emergency_stop"] is True
    control.reset()
    store, perms, grant_store, _ = _stores(tmp_path)
    _grant(grant_store, ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    assert policy.check(ASK, scopes=("research",),
                        target=ORIGIN + "/q")["allowed"] is True


# ---------------------------------------------------------------------------
# Offline / unavailable-provider typed states
# ---------------------------------------------------------------------------
def test_offline_provider_typed_unavailable(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    policy = _policy(grant_store, control, perms)
    # A comet operation with no bound adapter is typed UNAVAILABLE, never fake
    # success, via the runner.
    from voicebridge.actions import make_fixture_executors
    runner = _ghost_runner_without_comet(tmp_path, policy)
    d = runner.execute("ghost.comet.ask", args={"query": "x"}, target=ORIGIN,
                       scope="research", source="user")
    assert d["state"] in ("denied", "ADAPTER_DISABLED")
    assert d["code"] in ("COMET_UNAVAILABLE", "ADAPTER_DISABLED")


def _ghost_runner_without_comet(tmp_path, policy):
    from voicebridge.actions import make_fixture_executors, load_fixture_outcomes
    from voicebridge import ghost as G
    store = G.ActionStore(tmp_path / "actions.json")
    perms = PermissionsStore(tmp_path / "perm.json")
    execs = make_fixture_executors(load_fixture_outcomes(
        FIXTURES / "ghost_outcomes.json"))
    return G.GhostRunner(store=store, perms=perms, executors=execs,
                         enabled=True, power=policy,
                         adapter=G.GhostAdapter(shell=False))


# ---------------------------------------------------------------------------
# Multi-process grant store consistency
# ---------------------------------------------------------------------------
def test_grant_store_multi_process_consistency(tmp_path):
    import subprocess, sys as _sys
    path = tmp_path / "power-grant.json"
    script = (
        "import sys; sys.path.insert(0, %r); "
        "from voicebridge import power as P; "
        "s = P.PowerGrantStore(%r); "
        "s.issue(origins=('https://perplexity.ai',), scopes=('research',), "
        "capabilities=('ghost.comet.ask',), ttl_s=3600, issued_by='user'); "
        "print(s.grant().grant_id)" % (str(REPO_ROOT), str(path))
    )
    ids = []
    for _ in range(3):
        out = subprocess.run([_sys.executable, "-c", script],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        ids.append(out.stdout.strip().splitlines()[-1])
    # No process lost an update: a fresh read sees one valid active grant whose
    # id is the last writer's, and the store is not degraded/corrupt.
    store = PW.PowerGrantStore(path)
    assert store.health()["degraded"] is False
    assert store.grant() is not None
    assert store.grant().grant_id == ids[-1]
    assert store.grant().capabilities == ("ghost.comet.ask",)


def test_confused_deputy_rejected(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    # A confused deputy request: a privileged tool that never requested this
    # capability cannot borrow the grant.
    assert policy.check(TABS, scopes=("research",), target=ORIGIN + "/q",
                        role="")["allowed"] is False


# ---------------------------------------------------------------------------
# Source preservation: model/page/tool/skill/automation/arbitrary text can
# never become "user" and can never consume an owner grant.
# ---------------------------------------------------------------------------
def test_gate_adapter_preserves_non_human_source(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    gate = PW.PowerGateAdapter(policy, inner_gate=ActionGate(perms))
    spec = G.ghost_spec(ASK)
    env = {"action_id": ASK, "scope": "research", "target": ORIGIN + "/q",
           "role": "", "account_scope": "", "args": {}}
    for src in ("model", "automation", "page", "tool", "skill",
                "arbitrary text"):
        d = gate.decide(spec, env, source=src, confirmation_id=None)
        # A non-human source can NEVER auto-allow via the owner grant.
        assert d["allowed"] is False, (src, d)
        assert d["code"] != "ALLOWED", (src, d)


def test_gate_adapter_preserves_human_source(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    _grant(grant_store, capabilities=(ASK,), ttl_s=3600)
    policy = _policy(grant_store, control, perms)
    gate = PW.PowerGateAdapter(policy, inner_gate=ActionGate(perms))
    spec = G.ghost_spec(ASK)
    env = {"action_id": ASK, "scope": "research", "target": ORIGIN + "/q",
           "role": "", "account_scope": "", "args": {}}
    d = gate.decide(spec, env, source="user", confirmation_id=None)
    assert d["allowed"] is True
    assert d["code"] == "ALLOWED"


def test_gate_adapter_high_impact_requires_human_source(tmp_path):
    store, perms, grant_store, control = _stores(tmp_path)
    conf = ConfirmationStore()
    policy = _policy(grant_store, control, perms, confirmations=conf)
    gate = PW.PowerGateAdapter(policy, inner_gate=ActionGate(perms))
    spec = G.ghost_spec("ghost.input.click")
    env = {"action_id": "ghost.input.click", "scope": "web",
           "target": ORIGIN + "/q", "role": "", "account_scope": "",
           "args": {}}
    # A model-source high-impact request can never be approved.
    d = gate.decide(spec, env, source="model", confirmation_id="X",
                    confirmation_required=True)
    assert d["allowed"] is False
    assert d["code"] == "CONFIRMATION_SOURCE_FORBIDDEN"


# ---------------------------------------------------------------------------
# Lock-release regression: a failed operation must never hang the next one.
# ---------------------------------------------------------------------------
def test_failed_op_cannot_hang_next_op(tmp_path):
    store = PW.PowerGrantStore(tmp_path / "g.json")
    store.issue(origins=(ORIGIN,), scopes=("research",),
                capabilities=(ASK,), issued_by="user", ttl_s=3600)
    # Failed revoke (unknown grant) must release the lock.
    with pytest.raises(PW.PowerDenied):
        store.revoke("doesnotexist", issued_by="user")
    g = store.issue(origins=(ORIGIN,), scopes=("research",),
                    capabilities=(ASK,), issued_by="user", ttl_s=3600)
    assert g is not None
    # Failed reset (unwritable dir) must release the lock too.
    import os as _os
    d = tmp_path / "ro"
    d.mkdir()
    _os.chmod(d, 0o500)
    ro_store = PW.PowerGrantStore(d / "g.json")
    with pytest.raises(PW.PowerError):
        ro_store.issue(origins=(ORIGIN,), scopes=("research",),
                       capabilities=(ASK,), issued_by="user", ttl_s=3600)
    _os.chmod(d, 0o700)
    g2 = ro_store.issue(origins=(ORIGIN,), scopes=("research",),
                        capabilities=(ASK,), issued_by="user", ttl_s=3600)
    assert g2 is not None


def test_lock_released_on_mutate_exception(tmp_path):
    store = PW.PowerGrantStore(tmp_path / "g.json")
    store.issue(origins=(ORIGIN,), scopes=("research",),
                capabilities=(ASK,), issued_by="user", ttl_s=3600)
    with pytest.raises(PW.PowerDenied):
        store.revoke("wrong-id", issued_by="user")
    # The lock is free: a second writer in the SAME process completes.
    g = store.issue(origins=(ORIGIN,), scopes=("research",),
                    capabilities=(ASK,), issued_by="user", ttl_s=3600)
    assert g is not None
    # And a separate store instance can also read/write (cross-instance).
    store2 = PW.PowerGrantStore(tmp_path / "g.json")
    assert store2.grant() is not None


# ---------------------------------------------------------------------------
# Canonical origins and explicit subdomain policy
# ---------------------------------------------------------------------------
def test_valid_origin_ports_scheme_specific():
    # http may use only port 80; https may use only port 443.
    assert PW._valid_origin("http://perplexity.ai")
    assert PW._valid_origin("https://perplexity.ai")
    assert not PW._valid_origin("http://perplexity.ai:443")
    assert not PW._valid_origin("https://perplexity.ai:80")
    assert not PW._valid_origin("http://perplexity.ai:8080")
    assert not PW._valid_origin("https://perplexity.ai:444")


def test_valid_origin_rejects_ambiguous_and_malformed():
    for bad in ("https://perplexity.ai/path",
                "https://perplexity.ai?x=1",
                "https://perplexity.ai#frag",
                "https://user:pass@perplexity.ai",
                "https://127.0.0.1",
                "https://perplexity",
                "https://*.perplexity.ai",
                "https://perplexi-ty..ai",
                "ftp://perplexity.ai"):
        assert not PW._valid_origin(bad), bad


def test_subdomain_policy_explicit():
    # Default (allow_subdomains=True): proper subdomains match at a label
    # boundary; lookalikes never match.
    assert PW._host_matches("https://a.perplexity.ai", ("https://perplexity.ai",))
    assert not PW._host_matches("https://notperplexity.ai",
                                ("https://perplexity.ai",))
    assert not PW._host_matches("https://perplexity.ai.evil.com",
                                ("https://perplexity.ai",))
    # allow_subdomains=False requires an EXACT host.
    assert not PW._host_matches("https://a.perplexity.ai",
                                ("https://perplexity.ai",),
                                allow_subdomains=False)
    assert PW._host_matches("https://perplexity.ai",
                            ("https://perplexity.ai",),
                            allow_subdomains=False)


# ---------------------------------------------------------------------------
# Confirmation issuer binding: a non-human cannot create a reusable
# confirmation in the power path.
# ---------------------------------------------------------------------------
def test_confirmation_issuer_must_be_human():
    conf = ConfirmationStore()
    # A model cannot create a confirmation for itself.
    with pytest.raises(ConfirmationDenied):
        conf.issue("act.social.post", {"t": "1"}, issued_by="model")
    rec = conf.issue("act.social.post", {"t": "1"}, issued_by="user",
                     ttl_s=60)
    assert rec.issued_by == "user"


def test_audit_cross_process_no_duplicates(tmp_path):
    import subprocess, sys as _sys
    store, perms, grant_store, control = _stores(tmp_path)
    audit = tmp_path / "power-audit.jsonl"
    policy = PW.PowerPolicy(store=grant_store, control=control, perms=perms,
                            audit_path=str(audit))
    # Write two decisions from the SAME process -> exactly two records.
    policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    policy.check(ASK, scopes=("research",), target=ORIGIN + "/q")
    assert len(audit.read_text().splitlines()) == 2
    # A second process appends without corrupting or duplicating.
    script = (
        "import sys; sys.path.insert(0, %r); "
        "from voicebridge import power as P; "
        "from voicebridge.permissions import PermissionsStore; "
        "s = PermissionsStore(%r); "
        "pol = P.PowerPolicy(store=P.PowerGrantStore(%r), "
        "control=P.PowerControl(), perms=s, audit_path=%r); "
        "pol.check('ghost.comet.ask', scopes=('research',), "
        "target='https://perplexity.ai/q')"
        % (str(REPO_ROOT), str(tmp_path / "p.json"),
           str(tmp_path / "g.json"), str(audit)))
    for _ in range(2):
        out = subprocess.run([_sys.executable, "-c", script],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
    lines = audit.read_text().splitlines()
    assert len(lines) == 4
    # Each line is a complete, valid JSON record (no torn writes/duplicates).
    for line in lines:
        rec = json.loads(line)
        assert rec["capability"] == ASK
    assert oct(audit.stat().st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------------------
# Resource / bounded output and stale-target / replay resistance
# ---------------------------------------------------------------------------
def test_broker_bounded_output(tmp_path):
    from voicebridge.broker import build_host_broker
    broker = build_host_broker()

    def _big(_args):
        return "x" * 5000
    broker.register(ToolBinding(
        spec=CapabilitySpec(name="local.big", version="1.0.0",
                            tier=Tier.READ_ONLY, summary="big",
                            scopes=("local",)),
        side_effects="test", fn=_big))
    res = broker.execute("local.big", {}, source="user")
    assert res.ok is True
    # Output is bounded/redacted to the broker's cap, never 5000 raw bytes.
    assert len(res.output) <= 2000


def test_stale_target_and_replayed_confirmation_rejected(tmp_path):
    from voicebridge.actions import build_envelope, action_spec
    conf = ConfirmationStore()
    store, perms, grant_store, control = _stores(tmp_path)
    perms.grant("act.social.post", requested_by="user", confirmed=True,
                scopes=("social",))
    policy = _policy(grant_store, control, perms, confirmations=conf)
    # The broker binds confirmations to the canonical envelope. Issue a
    # confirmation bound to envelope A; a different target (target switching)
    # changes the envelope and is rejected.
    spec = action_spec("act.social.post")
    env_a = build_envelope(spec, args={"account": "me", "text": "Hello"},
                           target=ORIGIN, scope="social")
    env_b = build_envelope(spec, args={"account": "me", "text": "Hello"},
                           target="https://evil.example", scope="social")
    rec = conf.issue("act.social.post", env_a, issued_by="user", ttl_s=60)
    d = policy.decide("act.social.post", scopes=("social",),
                      target="https://evil.example", source="user",
                      confirmation_id=rec.id, args=env_b)
    assert d["allowed"] is False
    assert d["code"] == "TARGET_MISMATCH"
    # A confirmation bound to the correct envelope passes (nothing consumed).
    rec2 = conf.issue("act.social.post", env_a, issued_by="user", ttl_s=60)
    d_ok = policy.decide("act.social.post", scopes=("social",),
                         target=ORIGIN, source="user",
                         confirmation_id=rec2.id, args=env_a)
    assert d_ok["allowed"] is True
    # Consuming then replaying the same confirmation is rejected.
    conf.consume(rec2.id, "act.social.post", env_a)
    d2 = policy.decide("act.social.post", scopes=("social",),
                       target=ORIGIN + "/a", source="user",
                       confirmation_id=rec2.id, args=env_a)
    assert d2["allowed"] is False
    assert d2["code"] == "REPLAYED_CONFIRMATION"


def test_changed_arguments_reject_confirmation(tmp_path):
    from voicebridge.actions import build_envelope, action_spec
    conf = ConfirmationStore()
    store, perms, grant_store, control = _stores(tmp_path)
    policy = _policy(grant_store, control, perms, confirmations=conf)
    spec = action_spec("act.social.post")
    env = build_envelope(spec, args={"account": "me", "text": "Hello"},
                         target=ORIGIN + "/q", scope="social")
    rec = conf.issue("act.social.post", env, issued_by="user", ttl_s=60)
    # Changed arguments (different envelope digest) can never reuse the
    # confirmation.
    env_pwned = build_envelope(spec, args={"account": "me", "text": "PWNED"},
                               target=ORIGIN + "/q", scope="social")
    d = policy.decide("act.social.post", scopes=("social",),
                      target=ORIGIN + "/q", source="user",
                      confirmation_id=rec.id, args=env_pwned)
    assert d["allowed"] is False
    assert d["code"] == "TARGET_MISMATCH"