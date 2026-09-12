"""Hermes-dashboard compatibility backend (owner amendment 2026-09-11).

Serves the vendored Hermes dashboard (``frontend/hermes-web/``, byte-identical
at pinned hermes-agent@2237be3) from the Qubit backend. Every route calls the
SAME Qubit operation modules as the CLI (``voicebridge/cli.py``) and voice
(``voicebridge/voice_commands.py``): same stores, same permission broker, same
confirmation journal, same audit/redaction, same emergency stop.

Unsafe upstream actions (raw PTY spawn, file mkdir/upload-stream, secret
reveal, credential pool, config-raw dump, debug-share state dump, gateway
process supervision, remote hub fetch, external update/portal, OAuth token
dance) stay behind Qubit policy and return typed unavailable/refused states
(amendment point 9) — never fabricated success.

Vendored frontend files are NEVER modified here; Qubit additions (brand
theme, Qubit Chat, OpenClaw tabs) are served as plugin manifests + bundles
from ``frontend/hermes-qubit/`` (overlay).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
# Overlay (theme + plugins) resolution: package data first (clean wheel
# install), then the checkout. Keeps an isolated install self-contained.
from . import resources as _resources  # noqa: E402
from . import hermes_plugins as _hermes_plugins  # noqa: E402
OVERLAY_DIR = _resources.resource_path("frontend/hermes-qubit")

Handler = Callable[..., Tuple[int, Dict[str, Any]]]


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text
        return redact_text(str(text))
    except Exception:
        return str(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"hermes_compat.{op}",
                  {"actor": actor, **{k: _redact(v)[:160] for k, v in detail.items()}})
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    try:
        from .broker import estop_engaged
        return bool(estop_engaged(cfg))
    except Exception:
        try:
            from . import power as PW
            return bool(PW.estop_engaged(cfg))
        except Exception:
            return False


def _gate(code: str, detail: str, counterpart: str,
          http: int = 403, owner: str = "qubit-policy",
          rollback: str = "nothing changed; nothing to roll back",
          **extra: Any) -> Tuple[int, Dict[str, Any]]:
    out: Dict[str, Any] = {"ok": False, "state": "UNAVAILABLE", "code": code,
                           "detail": detail, "qubit_counterpart": counterpart,
                           "owner": owner, "rollback": rollback}
    if extra:
        out.update(extra)
    return http, out


def _ok(**fields: Any) -> Tuple[int, Dict[str, Any]]:
    out = {"ok": True}
    out.update(fields)
    return 200, out


def _staged(detail: str, confirmation_id: str,
            extra: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """Staged-confirmation response: HTTP 409, never 2xx.

    The vendored Hermes UI treats any 2xx as executed success (the Skills
    page flips its toggle and toasts success on resolve), so a staged
    confirmation answered 202 would render fabricated success. 409 forces
    the UI error path; the typed envelope plus the pending-approvals
    banner (GET/POST /api/ops/approvals|confirm|cancel, same journal as
    CLI/voice/classic) is the real completion path. One journal, one path.
    """
    out: Dict[str, Any] = {
        "ok": False, "state": "NEEDS_CONFIRMATION",
        "code": "CONFIRM_REQUIRED", "detail": detail,
        "confirmation_id": confirmation_id,
        "qubit_counterpart": "pending-approvals banner "
                             "(POST /api/ops/confirm) + CLI "
                             "`qubit approvals respond` + voice yes",
    }
    if extra:
        out.update(extra)
    return 409, out


def _res(out: Dict[str, Any]) -> Any:
    """Unwrap a dashboard_ops envelope to its structured result.

    run_op returns voice-pipeline outcomes whose ``receipt`` nests the real
    payload under ``result`` ({entry, session, result}); handlers adapt the
    inner result to Hermes wire shapes, not the speech envelope.
    """
    rec = out.get("receipt")
    if isinstance(rec, dict) and "result" in rec:
        return rec["result"]
    return rec


def _fail(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    return 500, {"ok": False, "state": "FAILED", "code": "EXECUTOR_ERROR",
                 "detail": _redact(str(exc))[:300]}


# ---------------------------------------------------------------------------
# Readers (shared Qubit modules — same functions CLI/voice call)
# ---------------------------------------------------------------------------

def h_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_inventory as RI
    inv = RI.inventory(cfg) if hasattr(RI, "inventory") else {}
    if not isinstance(inv, dict):
        inv = {}
    try:
        from . import status as ST
        core = ST.status(cfg) if hasattr(ST, "status") else {}
    except Exception:
        core = {}
    if not isinstance(core, dict):
        core = {}
    model = ""
    try:
        from . import agent as AG
        model = AG._model(cfg)
    except Exception:
        model = ""
    try:
        state_dir = str(Path(cfg.state_dir()))
    except Exception:
        state_dir = ""
    try:
        from . import distribution as D
        version = str(getattr(D, "VERSION", "1.0.0"))
    except Exception:
        version = "1.0.0"
    sessions_count = 0
    try:
        from .auth_status import recent_sessions as _rs
        sessions_count = int(_rs(cfg, 100).get("count", 0))
    except Exception:
        sessions_count = 0
    # StatusResponse wire shape (web/src/lib/api.ts): every required field
    # present; gateway-process fields are honest negatives (single event path;
    # supervision refused), Qubit values elsewhere.
    return _ok(schema="qubit-hermes-status/1",
               model=model, qubit=core,
               active_sessions=sessions_count,
               auth_required=False, auth_providers=[], auth_flows=[],
               can_update_hermes=False,
               config_path=state_dir, config_version=1, env_path=state_dir,
               gateway_exit_reason=None, gateway_health_url=None,
               gateway_pid=None, gateway_platforms={},
               gateway_running=False, gateway_state="not-supervised",
               gateway_updated_at=None,
               hermes_home=state_dir, latest_config_version=1,
               release_date="2026-09-11", version="qubit-" + version,
               detail="Qubit single-authority backend; gateway process "
                      "supervision is refused by policy (GATEWAY_NO_SUPERVISION)")


def h_health(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Lightweight liveness (HealthResponse wire shape): ok/version/auth_required.

    Backed by the real installation-health inventory
    (``voicebridge.lifecycle.health_inventory``) — the same module
    ``qubit setup health`` calls. A DEGRADED aggregate is reported verbatim;
    liveness itself is never fabricated READY when the probe raises.
    """
    code, status_out = h_status(cfg, q, b, p)
    if code != 200:
        return code, status_out
    state = "READY"
    detail = "Qubit backend process is live"
    try:
        from .lifecycle import health_inventory
        inv = health_inventory(cfg)
        if isinstance(inv, dict) and inv.get("state"):
            state = str(inv["state"])
    except Exception as exc:
        state = "DEGRADED"
        detail = "installation-health inventory unavailable: " + \
            _redact(str(exc))[:160]
    return _ok(version=status_out.get("version", "qubit-1.0.0"),
               auth_required=False, state=state, detail=detail)


def h_auth_me(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    # AuthMeResponse wire shape: local loopback operator identity.
    return _ok(user_id="local-operator", email="", display_name="Qubit Operator",
               org_id="local", provider="loopback", expires_at=0,
               authenticated=True, actor="dashboard:user", mode="loopback-token",
               detail="loopback session-token auth; no OAuth dance")


def h_events(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        from .webui import WebUI  # noqa
    except Exception:
        pass
    items: List[Dict[str, Any]] = []
    try:
        state_dir = Path(cfg.state_dir())
        for name in ("webui_events.jsonl", "events.jsonl", "audit.jsonl"):
            fp = state_dir / name
            if not fp.exists():
                continue
            lines = fp.read_text(errors="replace").splitlines()[-100:]
            for line in lines:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    items.append({k: _redact(v)[:300] if isinstance(v, str) else v
                                  for k, v in ev.items()})
            if items:
                break
    except Exception as exc:
        return _fail(exc)
    return _ok(events=items[-int(q.get("limit", "100") or "100"):],
               count=len(items))


def h_pub(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST/WEBSOCKET /api/pub -> publish into the single Qubit event journal.

    The upstream ``/api/pub`` is a WebSocket channel broadcaster. Qubit keeps
    one event path and never starts a second WS server, so the Qubit-safe
    equivalent appends the redacted, validated event to the same
    ``<state_dir>/typed-events.jsonl`` journal the dashboard feed and
    communications runtime read. It returns the durable ``seq``.
    """
    channel = str(b.get("channel", b.get("name", "dashboard")) or "dashboard")
    message = b.get("data", b.get("message", b.get("text", "")))
    try:
        from .events.journal import EventJournal
        journal = EventJournal(_store_path(cfg, "typed-events.jsonl"))
        record = journal.append("CUSTOM", name=channel, value=message)
    except Exception as exc:
        return _fail(exc)
    _audit("pub", "dashboard:user", {"channel": channel,
                                     "seq": record.get("seq")})
    return _ok(ok=True, channel=channel, seq=record.get("seq"),
               published=True,
               detail="published to the single Qubit event journal")


def h_sessions_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .auth_status import recent_sessions, session_store
    try:
        limit = max(1, min(int(q.get("limit", "50") or "50"), 100))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except ValueError:
        offset = 0
    query = str(q.get("q", q.get("query", "")) or "").strip()
    if query:
        res = session_store(cfg).search(query, limit + offset)
        all_items = res.get("sessions", [])
    else:
        res = recent_sessions(cfg, limit + offset)
        all_items = res.get("sessions", [])
    # PaginatedSessions wire shape (web/src/lib/api.ts): sessions, total,
    # limit, offset; SessionInfo rows carry source/model/title/message_count.
    sessions = []
    for r in all_items[offset:offset + limit]:
        rid = str(r.get("receipt_id") or r.get("action_id") or r.get("id", ""))
        sessions.append({
            "id": rid,
            "source": "qubit-receipts",
            "model": None,
            "title": (r.get("name") or (r.get("detail") or "")[:80] or rid) or None,
            "name": r.get("name"),
            "archived": bool(r.get("archived", False)),
            "started_at": r.get("created_at") or 0,
            "ended_at": None,
            "last_active": r.get("created_at") or 0,
            "is_active": False,
            "message_count": 0,
            "tool_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "preview": (r.get("detail") or "")[:200],
            "state": r.get("state"),
            "code": r.get("code"),
            "receipt_id": r.get("receipt_id"),
            "action_id": r.get("action_id"),
        })
    return _ok(sessions=sessions, total=len(all_items), limit=limit,
               offset=offset, store_state=res.get("state", "OK"))


def h_sessions_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .auth_status import recent_sessions
    res = recent_sessions(cfg, 100)
    items = res.get("sessions", [])
    by_state: Dict[str, int] = {}
    for r in items:
        by_state[str(r.get("state", "?"))] += 1
    total = len(items)
    # SessionStoreStats wire shape (web/src/lib/api.ts): total, active_store,
    # archived, messages, by_source. Receipts are append-only: archived=0.
    return _ok(total=total, active_store=total, archived=0, messages=total,
               by_source={"qubit-receipts": total}, by_state=by_state)


def h_session_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .auth_status import session_store
    rid = p.get("id", "")
    try:
        res = session_store(cfg).show(rid)
    except Exception as exc:
        return _fail(exc)
    if str(res.get("state")) != "OK" or not isinstance(res.get("session"), dict):
        return 404, {"ok": False, "code": res.get("code", "NOT_FOUND"),
                     "detail": res.get("detail", f"no session {rid!r}")}
    r = res["session"]
    detail = str(r.get("detail", "") or "")
    try:
        created = float(r.get("created_at", 0) or 0)
    except (TypeError, ValueError):
        created = 0.0
    # SessionInfo wire shape (web/src/lib/api.ts).
    return _ok(id=rid, source="qubit-receipts", model=None,
               title=(r.get("name") or detail[:80] or rid) or None,
               name=r.get("name"), archived=bool(r.get("archived", False)),
               started_at=created, ended_at=None, last_active=created,
               is_active=False, message_count=0, tool_call_count=0,
               input_tokens=0, output_tokens=0,
               preview=detail[:200] or None,
               state=str(r.get("state", "") or ""))


def h_session_patch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Rename / archive a session (shared SessionStore)."""
    from .auth_status import session_store
    rid = p.get("id", "")
    store = session_store(cfg)
    try:
        if "name" in b:
            res = store.rename(rid, str(b.get("name", "")))
        elif b.get("archived") is not None:
            res = (store.archive(rid) if b.get("archived")
                   else store.resume(rid))
        else:
            return 400, {"ok": False, "code": "MALFORMED",
                         "detail": "patch needs name or archived"}
    except Exception as exc:
        return _fail(exc)
    ok = str(res.get("state")) == "OK"
    return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                  "code": res.get("code"),
                                  "detail": _redact(
                                      res.get("detail", ""))[:200],
                                  "session": res.get("session")}


def h_session_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Delete a session tombstone; destructive and recoverable via resume."""
    from .auth_status import session_store
    rid = p.get("id", "")
    confirm = bool(b.get("confirm", False))
    try:
        res = session_store(cfg).delete(rid, confirm=confirm)
    except Exception as exc:
        return _fail(exc)
    if not confirm:
        return 409, {"ok": False, "state": "CONFIRM_REQUIRED",
                     "code": res.get("code", "CONFIRM_REQUIRED"),
                     "detail": res.get("detail", ""),
                     "qubit_counterpart": "re-DELETE with {\"confirm\": true}; "
                                          "recover with POST resume"}
    ok = str(res.get("state")) == "OK"
    return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                  "code": res.get("code"),
                                  "detail": _redact(
                                      res.get("detail", ""))[:200],
                                  "session": res.get("session")}


def h_session_resume(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .auth_status import session_store
    try:
        res = session_store(cfg).resume(p.get("id", ""))
    except Exception as exc:
        return _fail(exc)
    ok = str(res.get("state")) == "OK"
    return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                  "code": res.get("code"),
                                  "detail": _redact(
                                      res.get("detail", ""))[:200]}


def h_session_messages(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    code, out = h_session_show(cfg, q, b, p)
    if code != 200:
        return code, out
    rid = p.get("id", "")
    # SessionMessagesResponse wire shape; receipts hold no chat transcript.
    return _ok(session_id=rid,
               messages=[{"role": "system",
                          "content": _redact(json.dumps(out))[:2000],
                          "timestamp": out.get("last_active", 0)}],
               pagination={"limit": 500, "offset": 0, "order": "latest",
                           "returned": 1},
               detail="Qubit sessions are audit receipts, not chat transcripts; "
                      "live conversation runs in Qubit Chat (agent.turn)")


def h_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "config_profiles", {}, actor="dashboard:user",
                     session="dashboard")
    if out.get("state") != "executed":
        return 200, {"ok": False, "code": out.get("code", "FAILED"),
                     "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}
    # getConfig/getDefaults/getSchema expect Record<string, unknown>.
    return _ok(qubit_config_profiles=_res(out),
               note="read-only inventory; writes stage through the Qubit "
                    "operations console with confirmation")


def h_egress_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Egress proxy status (EgressStatusResponse wire shape: ``text``).

    Backed by ``voicebridge.proxy_status`` (the proxy inventory) plus the
    frontier network policy (``voicebridge.providers.policy``). No forwarding
    server runs, so the text is the real typed policy, never a fake "up".
    """
    from . import proxy_status as PS
    try:
        proxy = PS.status()
    except Exception as exc:
        return _fail(exc)
    frontier: Dict[str, Any] = {}
    try:
        from .providers.policy import FrontierPolicy
        pol = FrontierPolicy.from_config(cfg)
        frontier = {"enabled": bool(pol.enabled), "route": pol.route,
                    "allowlist_count": len(pol.allowlist),
                    "privacy_tier": pol.privacy_tier}
    except Exception:
        frontier = {}
    text = (f"proxy: {proxy.get('state', 'UNKNOWN')}; "
            f"frontier: {'enabled' if frontier.get('enabled') else 'disabled'}; "
            f"{proxy.get('policy', '')}")
    return _ok(text=text, proxy=proxy, frontier=frontier)


def _qubit_model(cfg: Any) -> Dict[str, Any]:
    from . import agent as AG
    from . import dashboard_ops as OPS
    name = ""
    try:
        name = AG._model(cfg)
    except Exception:
        name = "qubit-brain4"
    ctx = 4096
    try:
        out = OPS.run_op(cfg, "prompt_budget", {}, actor="dashboard:user",
                         session="dashboard")
        res = _res(out) or {}
        ctx = int((res.get("prompt_budget", {}) or {}).get("num_ctx", 4096))
    except Exception:
        pass
    return {"name": name or "qubit-brain4", "ctx": ctx}


def h_model_info(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    m = _qubit_model(cfg)
    # ModelInfoResponse wire shape.
    return _ok(model=m["name"], provider="ollama-local",
               auto_context_length=m["ctx"], config_context_length=m["ctx"],
               effective_context_length=m["ctx"],
               capabilities={"supports_tools": True, "supports_vision": False,
                             "supports_reasoning": False,
                             "context_window": m["ctx"],
                             "max_output_tokens": 1024,
                             "model_family": "qwen3-4b"})


def h_model_options(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    m = _qubit_model(cfg)
    out = OPS.run_op(cfg, "config_profiles", {}, actor="dashboard:user",
                     session="dashboard")
    res = _res(out) or {}
    profiles = res.get("profiles", []) if isinstance(res, dict) else []
    brains = [pr for pr in profiles
              if isinstance(pr, dict) and pr.get("kind") == "brain"]
    models = [str(pr.get("id", "")) for pr in brains if pr.get("id")]
    # ModelOptionsResponse wire shape.
    return _ok(model=m["name"], provider="ollama-local",
               providers=[{
                   "name": "local", "slug": "local",
                   "models": models or [m["name"]],
                   "total_models": len(models or [m["name"]]),
                   "is_current": True, "is_user_defined": False,
                   "source": "qubit-config-profiles",
                   "authenticated": False}])


def h_model_recommended_default(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Recommended default model (RecommendedDefaultResponse wire shape).

    Resolved through the same Qubit model info path as ``h_model_info`` /
    ``h_model_options`` (``agent._model`` + the config profiles brain). Qubit
    ships a single local brain, so the recommendation is that resolved model; a
    requested cloud provider slug is echoed honestly with an empty model since
    no cloud curation exists — never a silently-picked paid default.
    """
    m = _qubit_model(cfg)
    requested = str(q.get("provider", "") or "").strip().lower()
    local_slugs = {"", "local", "ollama", "ollama-local", "qubit-local"}
    if requested not in local_slugs:
        return _ok(provider=requested, model="", free_tier=None,
                   detail="Qubit routes through the local configured brain; "
                          "no cloud provider curation is performed")
    provider = "ollama-local"
    return _ok(provider=provider, model=m["name"], free_tier=None,
               detail="recommended default = configured Qubit brain profile")


def h_model_auxiliary(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    m = _qubit_model(cfg)
    # AuxiliaryModelsResponse wire shape; no aux pins exist.
    return _ok(tasks=[], main={"provider": "ollama-local",
                               "model": m["name"]})


def h_model_moa(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    # MoaConfigResponse wire shape; MoA is superseded by the ordered-fallback
    # gateway (8Q): no reference/aggregator fan-out exists to configure.
    slot = {"provider": "", "model": "", "enabled": False}
    return _ok(default_preset="none", active_preset="none", presets={},
               reference_models=[], aggregator=slot,
               reference_temperature=0.0, aggregator_temperature=0.0,
               reference_timeout=None, degraded_reference_policy="loud",
               enabled=False,
               detail="MoA fan-out is not shipped; ordered provider fallback "
                      "(8Q gateway) is the Qubit counterpart")


def h_model_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    scope = str(b.get("scope", "main") or "main")
    if scope != "main":
        return 400, {"ok": False, "code": "AUX_PIN_REFUSED",
                     "detail": "auxiliary task pins are not offered; the main "
                               "profile owns routing"}
    model = str(b.get("model") or "")
    if not model:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "model assignment needs a model name"}
    if b.get("confirm_expensive_model") or b.get("confirmed"):
        # Upstream retry shape: the Models dialog re-POSTs with the flag
        # after showing confirm_required. Settle the staged journal
        # approval for the same model instead of staging a second one
        # (one confirmation path, settle-once journal).
        try:
            from .approvals import ApprovalJournal
            journal = ApprovalJournal(
                Path(cfg.state_dir()) / "approvals.jsonl",
                default_ttl_s=120.0)
            cands = [a for a in journal.pending()
                     if a.capability == "voice.profile_select"
                     and (a.meta or {}).get("session") == "dashboard"
                     and (a.meta or {}).get("args", {}).get("profile") == model]
        except Exception:
            cands = []
        if cands:
            from . import dashboard_ops as OPS
            latest = max(cands, key=lambda a: a.created_at)
            out = OPS.confirm_op(cfg, latest.id, actor="dashboard:user",
                                 session="dashboard")
            ok = out.get("state") == "executed"
            return (200 if ok else 400), {
                "ok": ok, "scope": "main", "provider": "ollama-local",
                "model": model,
                "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}
        # No staged approval (expired or restarted since): fall through and
        # stage a fresh one below so the dialog has something to confirm.
    out = OPS.run_op(cfg, "profile_select",
                     {"kind": "brain", "profile": model},
                     actor="dashboard:user", session="dashboard")
    if out.get("confirmation_id"):
        # ModelAssignmentResponse confirm flow (409, never 2xx: see _staged).
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"],
                       {"confirm_required": True,
                        "confirm_message": _redact(out.get("speech", ""))[:300]})
    ok = out.get("state") == "executed"
    # ModelAssignmentResponse wire shape.
    return (200 if ok else 400), {
        "ok": ok, "scope": "main", "provider": "ollama-local", "model": model,
        "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def h_profiles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    from .permissions import permissions_store
    out = OPS.run_op(cfg, "config_profiles", {}, actor="dashboard:user",
                     session="dashboard")
    res = _res(out) or {}
    profiles = res.get("profiles", []) if isinstance(res, dict) else []
    try:
        skill_count = len(permissions_store(cfg).skills())
    except Exception:
        skill_count = 0
    default_id = ""
    for pr in profiles:
        if isinstance(pr, dict) and pr.get("default"):
            default_id = str(pr.get("id", ""))
            break
    if not default_id and profiles:
        default_id = str(profiles[0].get("id", ""))
    persona = _persona_active(cfg)
    if persona:
        default_id = persona
    kind = (p.get("kind") or "").strip()
    if kind == "active":
        # ActiveProfileInfo wire shape.
        return _ok(active=default_id, current=default_id)
    items = []
    for pr in profiles:
        if not isinstance(pr, dict):
            continue
        pid = str(pr.get("id", ""))
        items.append({
            "name": pid, "path": "", "is_default": pid == default_id,
            "model": pid if pr.get("kind") == "brain" else None,
            "provider": "ollama-local" if pr.get("kind") == "brain" else None,
            "has_env": False, "skill_count": skill_count,
            "gateway_running": False,
            "description": str(pr.get("source", "") or "")[:200],
            "description_auto": False,
            "display_name": pid, "distribution_name": None,
            "distribution_version": None, "distribution_source": None,
            "has_alias": False,
        })
    # {profiles: ProfileInfo[]} wire shape.
    return _ok(profiles=items)


def h_profile_soul(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import brain as B
    name = str(p.get("name", "") or "")
    try:
        res = B.profile_show(cfg, name)
    except Exception as exc:
        return _fail(exc)
    if isinstance(res, dict) and res.get("ok") is False:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no profile {name!r}"}
    text = ""
    if isinstance(res, dict):
        text = str(res.get("profile", res.get("soul", "")) or "")
    return _ok(content=text, exists=bool(text))


def h_profile_setup_command(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET setup-command -> real command for the registered principal."""
    from . import brain as B
    name = str(p.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required",
                     "qubit_counterpart": "CLI `qubit brain principal-add`"}
    try:
        state = B._load(cfg)
        principals = state.get("principals", {})
    except Exception as exc:
        return _fail(exc)
    if name not in principals:
        return _ok(command=f"qubit brain principal-add {name} --kind person "
                           f"--label {name}",
                   principal=name, registered=False,
                   detail="profile is not a registered Qubit principal",
                   qubit_counterpart="CLI `qubit brain principal-add`")
    return _ok(command=f"qubit brain profile-show {name}",
               principal=name, registered=True,
               qubit_counterpart="CLI `qubit brain principal-add`")


def h_skills(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Any]:
    from .permissions import permissions_store
    try:
        store = permissions_store(cfg)
        recs = store.skills()
    except Exception as exc:
        return _fail(exc)
    # SkillInfo[] wire shape (web/src/lib/api.ts).
    out = []
    for rec in recs:
        d = rec.to_dict() if hasattr(rec, "to_dict") else {}
        man = d.get("manifest", {}) if isinstance(d, dict) else {}
        out.append({
            "name": str(d.get("skill_id", "")),
            "description": str(man.get("description", "") or d.get("status", "")),
            "category": str(man.get("category", "") or "qubit"),
            "enabled": str(d.get("status", "")) == "enabled",
        })
    return 200, out


def h_skill_content(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    name = str(q.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill name required"}
    try:
        rec = permissions_store(cfg).skill(name)
    except Exception as exc:
        return _fail(exc)
    if rec is None:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no skill {name!r}"}
    d = rec.to_dict()
    man = d.get("manifest", {})
    content = str(man.get("instructions", "") or man.get("description", "") or "")
    # SkillContent wire shape.
    return _ok(name=name, content=content,
               path=str(d.get("source_path", "") or ""))


def h_skills_toggle(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    name = str(b.get("name") or b.get("skill") or "")
    enable = bool(b.get("enabled", b.get("enable", True)))
    out = OPS.run_op(cfg, "skills_enable" if enable else "skills_disable",
                     {"skill_id": name} if name else {},
                     actor="dashboard:user", session="dashboard")
    if out.get("confirmation_id"):
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    ok = out.get("state") == "executed"
    return (200 if ok else 400), {"ok": ok,
                                  "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def h_toolsets(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "tools", {}, actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    tools = res.get("tools", res) if isinstance(res, dict) else {}
    names = tools.get("names", []) if isinstance(tools, dict) else []
    # ToolsetInfo[] wire shape; Qubit tools are broker capabilities, one
    # entry per tool (platform "qubit").
    return 200, [{
        "name": n, "label": n, "description": "Qubit broker capability",
        "platform": "qubit", "platform_label": "Qubit",
        "enabled": True, "configured": True, "tools": [n],
    } for n in names]


def _cron_shape(j: Dict[str, Any]) -> Dict[str, Any]:
    # CronJob wire shape (web/src/lib/api.ts); Qubit schedules are the store.
    return {
        "id": str(j.get("id", "")), "profile": None, "profile_name": None,
        "hermes_home": None, "is_default_profile": True,
        "name": j.get("name"), "prompt": j.get("task_text"),
        "script": None, "skills": None,
        "schedule": {"kind": str(j.get("kind", "")),
                     "expr": str(j.get("interval_min") or j.get("at_ts") or ""),
                     "display": str(j.get("kind", ""))},
        "schedule_display": str(j.get("kind", "")),
        "repeat": None, "enabled": bool(j.get("enabled", False)),
        "state": str(j.get("last_state", "") or ""),
        "deliver": j.get("deliver_to") or None,
        "model": None, "provider": None, "base_url": None,
        "no_agent": None, "context_from": None, "enabled_toolsets": None,
        "workdir": None,
        "last_run_at": j.get("last_run_at"), "next_run_at": None,
        "last_status": j.get("last_state"), "last_error": None,
        "last_delivery_error": j.get("last_delivery_error"),
    }


def h_cron_jobs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    from . import schedules as S
    try:
        res = S.list_jobs(cfg)
    except Exception as exc:
        return _fail(exc)
    jobs = res.get("jobs", res) if isinstance(res, dict) else res
    return 200, [_cron_shape(j) for j in (jobs or [])]


def h_cron_job_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    try:
        res = S.show(cfg, int(p.get("id", "0")))
    except Exception as exc:
        return _fail(exc)
    if isinstance(res, dict) and res.get("ok") is False:
        return 404, {"ok": False, "code": res.get("code", "NOT_FOUND"),
                     "detail": _redact(res.get("error", ""))[:200]}
    job = res.get("job", res) if isinstance(res, dict) else {}
    return 200, _cron_shape(job if isinstance(job, dict) else {})


def h_cron_job_runs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Run history for one schedule ({"runs": CronRun[], "limit": int}).

    Backed by ``voicebridge.schedules.history`` (the same SQLite run log
    ``qubit schedules history`` reads). Schedule ids are numeric; a non-numeric
    upstream job id honestly reports no runs instead of scanning every job.
    """
    from . import schedules as S
    raw = str(p.get("id", "") or "")
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 100))
    except ValueError:
        limit = 20
    try:
        job_id = int(raw)
    except ValueError:
        job_id = 0
    if not job_id:
        return _ok(runs=[], limit=limit, count=0,
                   detail=f"schedule {raw!r} has no numeric id; no runs")
    try:
        res = S.history(cfg, job_id, limit)
    except Exception as exc:
        return _fail(exc)
    runs = res.get("runs", []) if isinstance(res, dict) else []
    now = time.time()
    shaped = []
    for r in runs if isinstance(runs, list) else []:
        started = r.get("started_at") or 0
        finished = r.get("finished_at") or 0
        shaped.append({
            "id": r.get("id"), "job_id": r.get("job_id"),
            "started_at": started, "ended_at": finished or None,
            "finished_at": finished, "state": str(r.get("state", "") or ""),
            "code": str(r.get("code", "") or ""),
            "detail": str(r.get("detail", "") or ""),
            "is_active": not finished and (now - started) < 300,
            "archived": False,
        })
    return _ok(runs=shaped, limit=limit, count=len(shaped),
               state=str(res.get("state", "OK") if isinstance(res, dict)
                         else "OK"))


def _cron_mutate(fn_name: str, cfg: Any, p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    try:
        if fn_name == "run_due":
            res = S.run_due(cfg, requested_by="dashboard:user")
            return 200, {"ok": True, "result": res}
        if fn_name in ("pause", "resume"):
            res = getattr(S, fn_name)(cfg, int(p.get("id", "0")),
                                      requested_by="dashboard:user",
                                      confirmed=True)
        else:
            res = getattr(S, fn_name)(cfg, int(p.get("id", "0")),
                                      requested_by="dashboard:user")
    except Exception as exc:
        return _fail(exc)
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res}


def h_logs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    try:
        limit = max(1, min(int(q.get("limit", q.get("lines", "100")) or "100"), 500))
    except ValueError:
        limit = 100
    out = OPS.run_op(cfg, "logs", {"limit": limit},
                     actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    rows = res.get("logs", []) if isinstance(res, dict) else []
    lines = []
    for r in rows if isinstance(rows, list) else []:
        lines.append(r if isinstance(r, str) else _redact(json.dumps(r))[:500])
    # LogsResponse wire shape (web/src/lib/api.ts).
    try:
        fname = str(Path(cfg.state_dir()) / "webui_events.jsonl")
    except Exception:
        fname = "webui_events.jsonl"
    return _ok(file=fname, lines=lines)


def h_system_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    import os
    import platform as _pl
    try:
        from . import distribution as D
        qv = str(getattr(D, "VERSION", "1.0.0"))
    except Exception:
        qv = "1.0.0"
    # SystemStats wire shape; hermes_version carries the Qubit product
    # version (wire-compat label, honest value + provenance in detail).
    return _ok(os=_pl.system(), os_release=_pl.release(),
               os_version=_pl.version(), platform=_pl.system().lower(),
               arch=_pl.machine(), hostname=_pl.node(),
               python_version=_pl.python_version(),
               python_impl=_pl.python_implementation(),
               hermes_version="qubit-" + qv, cpu_count=os.cpu_count(),
               psutil=False,
               detail="Qubit single-authority host; no Hermes gateway process")


def h_mcp(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    # {servers: McpServer[]} wire shape; Qubit inventories but never mounts,
    # so the server list is honestly empty with the policy attached.
    return _ok(servers=[],
               mount_policy="no unauthenticated MCP mount; inventory only",
               detail="inventory + mount policy; Qubit never mounts MCP servers")


def h_mcp_servers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/servers -> real configured MCP inventory (names only).

    Reads the same ``voicebridge.integration_status.mcp_status`` the CLI and
    voice surfaces use. Qubit never mounts a server, so each row is
    inventory-only with the mount policy attached; no command, URL, or
    credential reference is exposed.
    """
    from . import integration_status as IS
    try:
        status = IS.mcp_status(cfg)
    except Exception as exc:
        return _fail(exc)
    servers = []
    for name in status.get("servers", []):
        servers.append({
            "name": str(name), "enabled": False, "connected": False,
            "status": "inventory-only", "source": "qubit-config",
            "policy": status.get("mount_policy", ""),
        })
    return _ok(servers=servers, state=str(status.get("state", "OK")),
               mount_policy=status.get("mount_policy", ""),
               detail="configured inventory (names only); Qubit never mounts "
                      "MCP servers")


def h_mcp_catalog(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _ok(entries=[],
               diagnostics=[{
                   "name": "catalog",
                   "level": "info",
                   "message": "remote catalog fetch refused; install local "
                              "reviewed manifests only"}])


def h_mcp_catalog_live(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/catalog -> real local MCP inventory as the catalog.

    No remote catalog is fetched (network probe refused by policy); the
    catalog is the configured local inventory from
    ``integration_status.mcp_status``, each entry flagged as self-hosted and
    inventory-only, with the refusal recorded as a diagnostic.
    """
    from . import integration_status as IS
    try:
        status = IS.mcp_status(cfg)
    except Exception as exc:
        return _fail(exc)
    entries = [{
        "name": str(name), "title": str(name), "source": "local-config",
        "installed": True, "on_disk": True, "install_action": None,
        "auth": "none", "policy": "inventory-only; never mounted",
    } for name in status.get("servers", [])]
    return _ok(entries=entries, total=len(entries),
               diagnostics=[{
                   "name": "catalog",
                   "level": "info",
                   "message": "remote catalog fetch refused; serving the "
                              "reviewed local inventory only"}],
               mount_policy=status.get("mount_policy", ""))


def h_mcp_test_local(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str], ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/servers/{name}/test -> local inventory test (no probe).

    Qubit never mounts or connects to an MCP server, so there is no network
    probe to run. This reports the real local inventory state: an unknown
    server is a typed 404; a configured server is reported as inventory-only
    with no tools, explicitly not connected. It never fabricates a live
    connection.
    """
    from . import integration_status as IS
    name = str(p.get("name", p.get("id", "")) or "")
    try:
        status = IS.mcp_status(cfg)
    except Exception as exc:
        return _fail(exc)
    configured = [str(s) for s in status.get("servers", [])]
    if not name or name not in configured:
        return 404, {"ok": False, "state": "NOT_FOUND",
                     "code": "MCP_NOT_FOUND",
                     "detail": f"MCP server {_redact(name)!r} is not in the "
                               "Qubit inventory",
                     "qubit_counterpart": "CLI `qubit mcp status`"}
    return _ok(ok=True, name=name, tools=[], connected=False,
               probe="local-inventory", network_probe=False,
               detail="inventory-only: Qubit never mounts or connects to MCP "
                      "servers")


def h_pairing_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import pairing as P
    try:
        res = P.list_codes(cfg)
    except Exception as exc:
        return _fail(exc)
    codes = res.get("codes", []) if isinstance(res, dict) else []
    pending, approved = [], []
    for c in codes if isinstance(codes, list) else []:
        if not isinstance(c, dict):
            continue
        user = {"platform": str(c.get("account", "") or "qubit"),
                "user_id": str(c.get("code_id", c.get("id", ""))),
                "user_name": str(c.get("label", "") or ""),
                "request_id": str(c.get("code_id", "") or "")}
        (approved if c.get("approved") else pending).append(user)
    # PairingResponse wire shape.
    return _ok(pending=pending, approved=approved)


def h_pairing_mutate(fn_name: str, cfg: Any, b: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    from . import pairing as P
    try:
        if fn_name == "create":
            res = P.create(cfg, str(b.get("label", "dashboard")),
                           list(b.get("scopes", ["chat"])),
                           account=str(b.get("account", "")),
                           requested_by="dashboard:user", confirmed=True)
        elif fn_name == "approve":
            res = P.approve(cfg, str(b.get("code", "")),
                            requested_by="dashboard:user", confirmed=True)
        elif fn_name == "revoke":
            res = P.revoke(cfg, str(b.get("code_id", b.get("id", ""))),
                           requested_by="dashboard:user", confirmed=True)
        else:
            res = {"ok": True, "cleared": True}
    except Exception as exc:
        return _fail(exc)
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res}


def h_pairing_clear_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/pairing/clear-pending -> revoke every live pending code.

    Uses the same ``pairing`` store the CLI/voice use: each non-approved,
    unused, unrevoked code is tombstoned (confirmed + audited), so pending
    device requests are cleared without touching approved grants.
    """
    from . import pairing as P
    try:
        codes = P.list_codes(cfg).get("codes", [])
    except Exception as exc:
        return _fail(exc)
    revoked: List[str] = []
    for rec in codes if isinstance(codes, list) else []:
        if not isinstance(rec, dict):
            continue
        if rec.get("approved") or rec.get("used") or rec.get("revoked"):
            continue
        code_id = str(rec.get("id", "") or "")
        if not code_id:
            continue
        try:
            P.revoke(cfg, code_id, requested_by="dashboard:user",
                     confirmed=True)
            revoked.append(code_id)
        except Exception:
            continue
    return _ok(ok=True, cleared=len(revoked), revoked=revoked)


def h_webhooks_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import webhooks as W
    try:
        res = W.list_subs(cfg)
    except Exception as exc:
        return _fail(exc)
    subs = res.get("subscriptions", []) if isinstance(res, dict) else []
    routes = []
    for s in subs if isinstance(subs, list) else []:
        if not isinstance(s, dict):
            continue
        routes.append({
            "name": str(s.get("sub_id", s.get("id", ""))),
            "description": str(s.get("label", "") or ""),
            "events": list(s.get("events", []) or []),
            "deliver": "hmac-freshness-replay-store",
            "deliver_only": True,
            "prompt": "",
            "skills": [],
            "created_at": None,
            "url": str(s.get("url", "")),
            "secret_set": bool(s.get("secret_ref") or s.get("secret")),
            "enabled": bool(s.get("enabled", True)),
        })
    # WebhooksResponse wire shape; secret values never leave the vault.
    try:
        store_enabled = W.is_enabled(cfg)
    except Exception:
        store_enabled = True
    return _ok(enabled=store_enabled, base_url="", subscriptions=routes)


def h_webhooks_mutate(fn_name: str, cfg: Any, b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import webhooks as W
    try:
        if fn_name == "add":
            res = W.add(cfg, str(b.get("id", b.get("sub_id", ""))),
                        str(b.get("url", "")), list(b.get("events", [])),
                        str(b.get("secret_ref", "")),
                        requested_by="dashboard:user", confirmed=True)
        elif fn_name == "remove":
            res = W.remove(cfg, str(b.get("id", p.get("id", ""))),
                           requested_by="dashboard:user", confirmed=True)
        elif fn_name == "test":
            res = W.test_plan(cfg, str(b.get("id", p.get("id", ""))))
        else:
            res = W.show(cfg, str(p.get("id", "")))
    except Exception as exc:
        return _fail(exc)
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res}


def h_platforms(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import messaging as M
    try:
        res = M.account_list(cfg)
    except Exception as exc:
        return _fail(exc)
    accounts = res.get("accounts", []) if isinstance(res, dict) else []
    # Link-state -> wire state. Never report a fabricated "connected": an
    # account with no credential is unconfigured, and a channel with no Qubit
    # transport adapter is honestly unavailable (typed code attached).
    link_map = {
        "LINKED": ("connected", True, None, None),
        "DEGRADED": ("degraded", True, "LINK_DEGRADED", None),
        "NO_CREDENTIAL": ("unconfigured", False, "NO_CREDENTIAL",
                          "credential reference unresolvable"),
        "NO_ACCOUNT": ("unconfigured", False, "NO_ACCOUNT", None),
        "UNAVAILABLE": ("unavailable", True, M.UNAVAILABLE_CODE,
                        "no Qubit transport adapter for this channel"),
    }
    platforms = []
    for a in accounts if isinstance(accounts, list) else []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("account_id", a.get("id", "")))
        provider = str(a.get("provider", ""))
        try:
            linked = M.link(cfg, aid)
        except Exception:  # noqa: BLE001 — typed degraded, never fabricated
            linked = {"state": "UNAVAILABLE", "code": M.UNAVAILABLE_CODE}
        st, configured, code, msg = link_map.get(
            str(linked.get("state", "")),
            ("unavailable", False, linked.get("code") or M.UNAVAILABLE_CODE,
             linked.get("detail")))
        platforms.append({
            "id": aid, "name": str(a.get("label", aid) or aid),
            "description": f"{provider} account reference (vault-only credential)",
            "docs_url": "", "enabled": bool(a.get("enabled", True)),
            "configured": configured, "gateway_running": False,
            "state": st,
            "error_code": code,
            "error_message": (str(msg)[:160] if msg else None),
            "updated_at": None, "home_channel": None,
            "whatsapp_setup": None, "env_vars": [],
        })
    return _ok(env_path="", gateway_start_command="", platforms=platforms)


def h_memory(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/memory -> live local memory store status (MemoryStatus shape)."""
    from . import active_memory as AM
    from . import brain as B
    try:
        status = AM.status(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        principals = len(B.principal_list(cfg).get("principals", []))
    except Exception:
        principals = 0
    state = str(status.get("state", "OK"))
    memories = status.get("memories")
    return _ok(active="qubit-local",
               providers=[{
                   "name": "qubit-local",
                   "description": "local-only SQLite/FTS5 MemoryStore "
                                  "(core/working/recall/archive tiers)",
                   "available": state in ("OK", "EMPTY", "DEGRADED"),
                   "configured": True,
                   "status": ("ready" if state == "OK" else
                              "empty" if state == "EMPTY" else "degraded"),
                   "detail": str(status.get("store_state", "") or "")}],
               builtin_files={"memory": int(memories or 0),
                              "user": principals},
               store_state=status.get("store_state"),
               store_path=status.get("store_path"),
               enabled=bool(status.get("enabled", True)),
               status=status)


def h_analytics(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "insights", {}, actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    ins = res.get("insights", {}) if isinstance(res, dict) else {}
    sessions = int(ins.get("recent_sessions", 0) or 0)
    states = ins.get("states", {}) if isinstance(ins, dict) else {}
    calls = sum(int(v) for v in states.values()) if isinstance(states, dict) else 0
    totals = {"total_input": 0, "total_output": 0, "total_cache_read": 0,
              "total_reasoning": 0, "total_estimated_cost": 0.0,
              "total_actual_cost": 0.0, "total_sessions": sessions,
              "total_api_calls": calls}
    # AnalyticsResponse wire shape; the local runner has no token economy,
    # so token/cost series are honest zeros (no fabricated billing).
    if p.get("kind") == "models":
        try:
            days = max(1, min(int(q.get("days", "30") or "30"), 365))
        except ValueError:
            days = 30
        return _ok(models=[], totals={**totals, "distinct_models": 0},
                   period_days=days,
                   detail="local runner: usage/cost records per provider call; "
                          "no token-billing series")
    return _ok(daily=[], by_model=[], totals=totals,
               skills={"summary": {"total_skills": 0, "enabled": 0},
                       "top_skills": []})


def _files_root(cfg: Any) -> str:
    try:
        from . import projects as PJ
        active = PJ.active(cfg) if hasattr(PJ, "active") else {}
        if isinstance(active, dict):
            for key in ("folder", "path", "root"):
                if active.get(key):
                    return str(active[key])
    except Exception:
        pass
    return ""


def h_files(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import files as FI
    import base64
    if q.get("path") or p.get("kind") == "read":
        path = str(q.get("path", b.get("path", "")) or "")
        try:
            res = FI.read_file(cfg, path)
        except Exception as exc:
            code = getattr(exc, "code", "FILES_ERROR")
            return 400, {"ok": False, "code": code,
                         "detail": _redact(str(exc))[:300]}
        text = res.get("text", res.get("content", "")) if isinstance(res, dict) else ""
        data_url = ("data:text/plain;base64,"
                    + base64.b64encode(str(text).encode("utf-8",
                                                        "replace")).decode())
        # ManagedFileReadResponse wire shape.
        return _ok(name=Path(path).name, path=path,
                   size=len(str(text).encode("utf-8", "replace")),
                   mime_type="text/plain", data_url=data_url,
                   root=_files_root(cfg) or None, locked_root=None,
                   can_change_path=False)
    folder = str(q.get("path", "") or "") or _files_root(cfg)
    if not folder:
        return _ok(root=None, path="", parent=None, locked_root=None,
                   can_change_path=False, entries=[],
                   detail="no active project folder; register one with "
                          "`qubit project add-folder`")
    try:
        res = FI.list_dir(cfg, folder)
    except Exception as exc:
        code = getattr(exc, "code", "FILES_ERROR")
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    raw_entries = res.get("entries", []) if isinstance(res, dict) else []
    entries = []
    for en in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(en, dict):
            continue
        entries.append({
            "name": str(en.get("name", "")),
            "path": str(en.get("path", "")),
            "is_directory": bool(en.get("is_dir", en.get("is_directory", False))),
            "size": en.get("size"), "mtime": en.get("mtime", 0),
            "mime_type": en.get("mime_type"),
        })
    # ManagedFilesResponse wire shape.
    return _ok(root=folder, path=folder, parent=None, locked_root=folder,
               can_change_path=False, entries=entries)


def h_gateway(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "gateway", {}, actor="dashboard:user", session="dashboard")
    return _ok(gateway=_res(out), state=out.get("state"),
               supervision="GATEWAY_NO_SUPERVISION: process start/stop/restart "
                           "is refused; the operator supervises processes")


def h_gateway_drain(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/gateway/drain -> real reversible Qubit drain marker.

    Mirrors the upstream drain control channel without a second listener or
    process supervision: ``action: "drain"`` atomically writes a redacted
    ``<state_dir>/gateway_drain.json`` marker (audited); ``action: "cancel"``
    removes it. It never starts/stops a process (supervision stays
    GATEWAY_NO_SUPERVISION) and is idempotent.
    """
    action = str(b.get("action", "drain") or "drain").strip().lower()
    if action not in ("drain", "cancel"):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": f"unknown drain action {action!r}; expected "
                               "'drain' or 'cancel'",
                     "qubit_counterpart": "CLI `qubit gateway` + operator"}
    path = _store_path(cfg, "gateway_drain.json")
    if action == "cancel":
        existed = False
        try:
            existed = path.exists()
            if existed:
                path.unlink()
        except OSError as exc:
            return _fail(exc)
        _audit("gateway_drain", "dashboard:user", {"action": "cancel",
                                                   "existed": existed})
        return _ok(ok=True, action="cancel", was_draining=existed,
                   draining=False)
    payload = {"schema": "qubit-gateway-drain/1",
               "requested_at": time.time(), "requested_by": "dashboard:user",
               "suppress_notification": bool(b.get("suppress_notification",
                                                   False))}
    try:
        _save_json_store(path, payload)
    except Exception as exc:
        return _fail(exc)
    _audit("gateway_drain", "dashboard:user", {"action": "drain"})
    return _ok(ok=True, action="drain", requested_at=payload["requested_at"],
               draining=True,
               suppress_notification=payload["suppress_notification"])


def h_gateway_supervision(action: str) -> Handler:
    """POST /api/gateway/{start,stop} -> real approval-gated supervision.

    The single bridge event path may only transition through the same
    ``gateway_peers.supervision_request`` the CLI/voice use: without a
    gateway id it is the real module's typed ``GATEWAY_NO_SUPERVISION``
    refusal; with one it stages an operator approval (409
    ``GATEWAY_SUPERVISION_APPROVAL_REQUIRED``) or performs the audited,
    revertible transition once authorized. Never a second server, never a
    fabricated success.
    """
    counterpart = ("CLI `qubit gateway supervision-request <id> <action> "
                   "--confirm` + operator approval (gateway.supervise grant)")

    def _handler(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        from . import gateway_peers as GW
        gw_id = str(b.get("gateway_id", b.get("id",
                                              q.get("gateway_id", ""))) or "").strip()
        if not gw_id:
            out = GW.refuse_supervision(action)
            body = {"ok": False, "qubit_counterpart": counterpart,
                    "owner": "qubit-policy"}
            body.update(out)
            return 403, body
        try:
            out = GW.supervision_request(
                cfg, gw_id, action,
                approval_id=str(b.get("approval_id", "") or ""),
                requested_by="dashboard:user",
                confirmed=bool(b.get("confirm")))
        except GW.GatewayError as exc:
            code = getattr(exc, "code", None) or "GATEWAY_ERROR"
            http = {"GW_CONFIRM_REQUIRED": 409, "GW_ESTOP": 403,
                    "GW_NOT_FOUND": 404, "GW_BAD_INPUT": 400}.get(code, 403)
            return http, {
                "ok": False, "code": code,
                "detail": _redact(getattr(exc, "detail", str(exc)))[:300],
                "qubit_counterpart": counterpart, "owner": "qubit-policy",
                "rollback": "no process touched; nothing to roll back"}
        except Exception as exc:
            return _fail(exc)
        state = str(out.get("state", ""))
        body = {"ok": state == "OK", "qubit_counterpart": counterpart}
        body.update(out)
        if state == "OK":
            return 200, body
        if state == "APPROVAL_REQUIRED":
            return 409, body
        return 403, body

    return _handler


def h_curator(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "curator", {}, actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    cur = res.get("curator", {}) if isinstance(res, dict) else {}
    # CuratorStatus wire shape; curation is on-demand (no daemon).
    return _ok(enabled=True, paused=False, interval_hours=None,
               last_run_at=None, min_idle_hours=None, stale_after_days=None,
               archive_after_days=None, summary=cur)


def h_env(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "auth_status", {}, actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    # Record<string, EnvVarInfo> wire shape: references only, never values.
    env: Dict[str, Any] = {}
    providers = res.get("providers", []) if isinstance(res, dict) else []
    for prov in providers if isinstance(providers, list) else []:
        if not isinstance(prov, dict):
            continue
        name = str(prov.get("provider", ""))
        if not name:
            continue
        env[f"{name.upper()}_KEY_REF"] = {
            "is_set": bool(prov.get("configured", False)),
            "redacted_value": None,
            "description": f"{name} credential reference "
                           f"({prov.get('key_ref', 'vault')})",
            "url": None, "category": "providers",
            "is_password": True, "tools": [], "advanced": False,
            "channel_managed": False, "custom": False,
        }
    return 200, env


def h_ops(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    name = p.get("name", "")
    mapping = {
        "doctor": ("doctor", {}), "dump": ("diagnostics", {}),
        "backup": ("backup_create", {}),
        "prompt-size": ("prompt_budget", {}), "security-audit": ("security", {}),
        "import": ("backup_restore", {}),
        "config-migrate": ("product_migrate", {}),
        "kanban": ("kanban_list", {}), "boards": ("kanban_boards", {}),
        "boards-create": ("kanban_boards_create", {}),
        "cards-create": ("kanban_create", {}),
        "cards-move": ("kanban_move", {}),
        "cards-complete": ("kanban_complete", {}),
        "gateways": ("gateway", {}),
        "gateway-add": ("gateway_add", {}),
        "gateway-show": ("gateway_show", {}),
        "gateway-link": ("gateway_link", {}),
        "gateway-remove": ("gateway_remove", {}),
        "msg-accounts": ("msg_accounts", {}),
        "msg-account-add": ("msg_account_add", {}),
        "msg-account-show": ("msg_account_show", {}),
        "msg-account-remove": ("msg_account_remove", {}),
        "runtime": ("runtime_status", {}),
    }
    if name not in mapping:
        return _gate("OPS_UNKNOWN", f"unknown ops endpoint {name!r}",
                     "use the Qubit operations console (/ops) or CLI", 404)
    op_id, args = mapping[name]
    if b and name in ("checkpoints", "hooks", "import", "backup",
                      "boards-create",
                      "cards-create", "cards-move", "cards-complete",
                      "kanban", "boards", "gateways",
                      "gateway-add", "gateway-show", "gateway-link",
                      "gateway-remove", "msg-accounts",
                      "msg-account-add", "msg-account-show",
                      "msg-account-remove"):
        args = {k: v for k, v in b.items() if isinstance(v, (str, int, float, bool))}
    if q and name in ("kanban", "boards", "gateways",
                      "gateway-show", "gateway-link", "msg-accounts",
                      "msg-account-show"):
        # Filtered reads: ?board= / ?gateway_id= / ?account_id= (validated
        # by run_op against the voice arg specs; unknown keys fail typed).
        for k, v in q.items():
            if isinstance(v, str) and v:
                args[k] = v
    out = OPS.run_op(cfg, op_id, args, actor="dashboard:user", session="dashboard")
    ok = out.get("state") == "executed"
    if out.get("confirmation_id") and not ok:
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    return (200 if ok else 400), {"ok": ok, "result": _res(out),
                                  "state": out.get("state"), "code": out.get("code"),
                                  "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def h_ops_catalog(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Machine-readable operations catalog (same registry as CLI/voice).

    Backs the Hermes-shell operations console: every entry carries its
    group, args schema, and confirmation flag so the React page can render
    real forms and settle staged mutations on the shared journal.
    """
    from . import dashboard_ops as OPS
    try:
        ops = OPS.catalog()
    except Exception as exc:
        return _fail(exc)
    return _ok(ops=ops, count=len(ops))


def _ops_result(out: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    ok = out.get("state") == "executed"
    if out.get("confirmation_id") and not ok:
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    return (200 if ok else 400), {
        "ok": ok, "result": _res(out), "state": out.get("state"),
        "code": out.get("code"),
        "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def h_ops_run(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Run any catalog operation on the shared broker (same path as CLI/voice).

    Body: ``{"id": <op-id>, "args": {...}}``. Reads execute immediately;
    confirmed mutations stage a single-use confirmation on the shared
    journal and answer 409 (never a fabricated 2xx).
    """
    from . import dashboard_ops as OPS
    op_id = str(b.get("id", "") or "").strip()
    if not op_id:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "id required"}
    raw_args = b.get("args")
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "args must be an object"}
    args = {k: v for k, v in raw_args.items()
            if isinstance(v, (str, int, float, bool))}
    try:
        out = OPS.run_op(cfg, op_id, args, actor="dashboard:user",
                         session="dashboard")
    except Exception as exc:
        return _fail(exc)
    return _ops_result(out)


def h_kanban_events(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "kanban_log", {"limit": 50},
                     actor="dashboard:user", session="dashboard")
    return _ok(events=_res(out), state=out.get("state"))


def h_approvals_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Pending confirmations on the SHARED journal (same records CLI
    `qubit approvals`, voice yes/no, and the classic dashboard settle).
    The pending-approvals banner polls this; confirm/cancel below settle
    through the same OPS journal path — one permission/confirmation path.
    """
    from .approvals import ApprovalJournal
    try:
        journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                                  default_ttl_s=120.0)
        items = [{"id": a.id, "capability": str(a.capability or ""),
                  "summary": _redact(a.summary)[:300],
                  "created_at": a.created_at, "expires_at": a.expires_at,
                  "state": a.state}
                 for a in journal.pending()]
    except Exception as exc:
        return _fail(exc)
    return _ok(approvals=items, count=len(items))


def _confirm_payload(out: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    ok = out.get("state") == "executed"
    return (200 if ok else 400), {
        "ok": ok, "state": out.get("state"), "code": out.get("code"),
        "detail": _redact(out.get("speech", out.get("detail", "")))[:400],
        "receipt": out.get("receipt")}


def h_ops_confirm(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    cid = str(b.get("confirmation_id", b.get("id", "")) or "")
    if not cid:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "confirmation_id required"}
    sensitive = b.get("sensitive")
    out = OPS.confirm_op(
        cfg, cid, actor="dashboard:user", session="dashboard",
        sensitive=dict(sensitive) if isinstance(sensitive, dict) else None)
    return _confirm_payload(out)


def h_ops_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    cid = str(b.get("confirmation_id", b.get("id", "")) or "") or None
    out = OPS.cancel_op(cfg, cid, actor="dashboard:user",
                        session="dashboard")
    return _confirm_payload(out)


def h_chat_turn(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Qubit Chat (overlay): one native agent turn, same module as CLI/voice."""
    from . import agent as AG
    if _estop(cfg):
        return 403, {"ok": False, "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; agent turn refused"}
    text = str(b.get("text", "") or "")[:4000]
    session = str(b.get("session", "dashboard-chat") or "dashboard-chat")[:80]
    if not text.strip():
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "turn needs non-empty text"}
    try:
        res = AG.turn(cfg, text, session=session)
    except AG.AgentDenied as exc:
        _audit("chat_turn_denied", "dashboard:user", {"code": exc.code})
        return 403, {"ok": False, "code": exc.code,
                     "detail": _redact(exc.detail)[:300]}
    except Exception as exc:
        return _fail(exc)
    _audit("chat_turn", "dashboard:user", {"session": session,
                                           "ok": bool(res.get("ok"))})
    return (200 if res.get("ok") else 502), res


# ---------------------------------------------------------------------------
# Packet 8U read surfaces: scoped Git / Files / Audio (audit §1 absent reads)
#
# Read-only compat routes backed by the SAME modules the CLI/voice call:
# ``git_scope`` (status/log/diff/show), ``files`` (list/tree/stat/read), and
# ``audio.settings`` (mic/output status + device inventory). Binary streams
# and mutating verbs stay typed-gated; nothing is fabricated 2xx.
# ---------------------------------------------------------------------------

_FS_LANGUAGE_BY_EXT = {
    ".c": "c", ".conf": "ini", ".cpp": "cpp", ".css": "css", ".csv": "csv",
    ".go": "go", ".h": "c", ".hpp": "cpp", ".html": "html", ".java": "java",
    ".js": "javascript", ".json": "json", ".jsx": "jsx", ".kt": "kotlin",
    ".lua": "lua", ".md": "markdown", ".mjs": "javascript", ".py": "python",
    ".rb": "ruby", ".rs": "rust", ".sh": "shell", ".sql": "sql",
    ".svg": "xml", ".toml": "toml", ".ts": "typescript", ".tsx": "tsx",
    ".txt": "text", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".zsh": "shell",
}
_FS_MIME_BY_EXT = {
    ".css": "text/css", ".csv": "text/csv", ".html": "text/html",
    ".js": "application/javascript", ".json": "application/json",
    ".md": "text/markdown", ".py": "text/x-python", ".sh": "text/x-shellscript",
    ".ts": "application/typescript", ".xml": "application/xml",
    ".yaml": "text/yaml", ".yml": "text/yaml",
}


def _active_folder(cfg: Any) -> str:
    """First folder of the active Qubit project (the scoped read root)."""
    try:
        from . import projects as PJ
        active = PJ.active_project(cfg)
        if active.get("state") == "OK" and active.get("project"):
            folders = active["project"].get("folders", []) or []
            if folders:
                return str(folders[0])
    except Exception:
        pass
    return ""


def _query_text(q: Dict[str, str], b: Dict[str, Any], *keys: str) -> str:
    for src in (q, b):
        if not isinstance(src, dict):
            continue
        for key in keys:
            value = src.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _git_query_root(q: Dict[str, str], b: Dict[str, Any]) -> str:
    return _query_text(q, b, "root", "repo", "path")


def _git_query_file(q: Dict[str, str], b: Dict[str, Any]) -> str:
    return _query_text(q, b, "file", "file_path")


def _git_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    from . import git_scope as GI
    pub = GI.public_error(exc)
    return 400, {"ok": False, "state": "FAILED", "code": pub["code"],
                 "detail": pub["detail"],
                 "qubit_counterpart": "CLI `qubit git status|log|diff|show`"}


def _git_no_project(**extra: Any) -> Tuple[int, Dict[str, Any]]:
    """Honest empty read when no active project root is registered."""
    out: Dict[str, Any] = {
        "schema": "qubit-hermes-git/1", "root": "", "git": None,
        "state": "EMPTY", "code": "NO_ACTIVE_PROJECT",
        "detail": "no active project folder; set one with "
                  "`qubit project use <slug> --confirm`",
    }
    out.update(extra)
    return _ok(**out)


def _git_ready(cfg: Any, root: str) -> bool:
    return bool(root) or bool(_active_folder(cfg))


def h_git_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/status -> scoped read-only work-tree status."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(status="")
    try:
        res = GI.status(cfg, root, requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-hermes-git/1", root=res.get("root", ""),
               status=res.get("status", ""),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_log(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/log -> bounded commit log."""
    from . import git_scope as GI
    try:
        limit = max(1, min(int(q.get("limit", "10") or "10"), GI.MAX_LOG_COUNT))
    except (TypeError, ValueError):
        limit = 10
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(entries=[])
    try:
        res = GI.log(cfg, root, limit, requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-hermes-git/1", root=res.get("root", ""),
               entries=res.get("entries", []),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_diff(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/diff -> bounded unstaged diff (tree or one file)."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(path="", stat="", patch="")
    try:
        res = GI.diff(cfg, root, _git_query_file(q, b),
                      requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-hermes-git/1", root=res.get("root", ""),
               path=res.get("path", ""), stat=res.get("stat", ""),
               patch=res.get("patch", ""),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/show -> one committed file version at HEAD/HEAD~N."""
    from . import git_scope as GI
    ref = str(q.get("ref", b.get("ref", "HEAD")) or "HEAD")
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(ref=ref, path="", content="")
    try:
        res = GI.show(cfg, root, ref, _git_query_file(q, b),
                      requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-hermes-git/1", root=res.get("root", ""),
               ref=res.get("ref", ref), path=res.get("path", ""),
               content=res.get("content", ""),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_file_diff(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/diff|file-diff -> working-tree-vs-HEAD diff."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(path="", diff="", stat="")
    try:
        res = GI.diff(cfg, root, _git_query_file(q, b),
                      requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-hermes-git/1", root=res.get("root", ""),
               path=res.get("path", ""), diff=res.get("patch", ""),
               stat=res.get("stat", ""),
               truncated=bool(res.get("truncated", False)), git=res)


# ---------------------------------------------------------------------------
# Git review reads (audit §1 git.py reads): branches / worktrees / review list /
# commit context / rev-parse. All execute ``git`` ONLY through git_scope's
# existing fixed-argv, root-confined ``_run`` (no shell, no new subprocess path,
# no network). Mutating verbs and the credentialed ``gh`` surfaces stay gated.
# ---------------------------------------------------------------------------

_REV_PARSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@~^{}:+-]{0,199}$")


def _git_line(root: Path, argv: List[str]) -> str:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, argv)
    except GI.GitError:
        return ""
    return text.splitlines()[0].strip() if text.strip() else ""


def _git_origin_head(root: Path) -> str:
    return _git_line(root,
                     ["symbolic-ref", "--quiet", "--short",
                      "refs/remotes/origin/HEAD"])


def _git_origin_head_abbrev(root: Path) -> str:
    return _git_line(root, ["rev-parse", "--abbrev-ref", "origin/HEAD"])


def _git_ref_exists(root: Path, ref: str) -> bool:
    from . import git_scope as GI
    try:
        GI._run(root, ["rev-parse", "--verify", "--quiet", ref])
        return True
    except GI.GitError:
        return False


def _git_default_branch(root: Path) -> str:
    head = _git_origin_head_abbrev(root)
    if head and head != "origin/HEAD":
        return head.split("/", 1)[-1]
    for ref in ("refs/heads/main", "refs/heads/master",
                "refs/remotes/origin/main", "refs/remotes/origin/master"):
        if _git_ref_exists(root, ref):
            return ref.split("/")[-1]
    return ""


def _git_ref_names(root: Path, *patterns: str) -> List[str]:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(
            root, ["for-each-ref", "--format=%(refname:short)",
                   "--sort=-committerdate", *patterns])
    except GI.GitError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _git_worktree_rows(root: Path) -> List[Dict[str, Any]]:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, ["worktree", "list", "--porcelain"])
    except GI.GitError:
        return []
    trees: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("worktree "):
            trees.append({"path": line[9:].strip(), "branch": None,
                          "isMain": not trees, "detached": False,
                          "locked": False})
        elif not trees:
            continue
        elif line.startswith("branch "):
            trees[-1]["branch"] = line[7:].strip().replace(
                "refs/heads/", "", 1)
        elif line == "detached":
            trees[-1]["detached"] = True
        elif line.startswith("locked"):
            trees[-1]["locked"] = True
    return trees


def _resolve_rename_path(raw: str) -> str:
    path = str(raw or "").strip()
    if " => " not in path:
        return path
    head, _, tail = path.partition("{")
    if tail and "}" in tail:
        inner, _, suffix = tail.partition("}")
        _, _, to = inner.partition(" => ")
        return f"{head}{to}{suffix}".replace("//", "/")
    return path.split(" => ")[-1].strip()


def _git_numstat(root: Path, *args: str) -> Dict[str, Tuple[int, int]]:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, ["diff", "--numstat", *args])
    except GI.GitError:
        return {}
    counts: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            added = 0 if parts[0] == "-" else int(parts[0] or 0)
            removed = 0 if parts[1] == "-" else int(parts[1] or 0)
            counts[_resolve_rename_path(parts[2])] = (added, removed)
    return counts


def _git_untracked_insertions(root: Path, rel: str) -> int:
    try:
        base = Path(root).resolve()
        target = (base / rel).resolve()
        target.relative_to(base)
        if not target.is_file() or target.stat().st_size > 2_000_000:
            return 0
        data = target.read_bytes()
        if b"\0" in data:
            return 0
        lines = data.count(b"\n")
        return lines + 1 if data and not data.endswith(b"\n") else lines
    except (OSError, ValueError):
        return 0


def _git_status_raw(root: Path) -> str:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, ["status", "--porcelain=v2", "-z"])
    except GI.GitError:
        return ""
    return text


def _porcelain_entries(raw: str):
    records = iter(raw.split("\0"))
    for rec in records:
        if not rec:
            continue
        tag = rec[0]
        if tag == "?":
            yield "?", "??", rec[2:]
        elif tag == "u":
            parts = rec.split(" ")
            yield "u", parts[1] if len(parts) > 1 else ".", parts[-1]
        elif tag in ("1", "2"):
            parts = rec.split(" ")
            xy = parts[1] if len(parts) > 1 else ".."
            path = (rec.split(" ", 8)[-1] if tag == "1"
                    else rec.split(" ", 9)[-1])
            if tag == "2":
                next(records, None)
            yield tag, xy, _resolve_rename_path(path)


def _git_status_letter(tag: str, xy: str) -> str:
    if tag in ("?", "u"):
        return tag.upper()
    code = xy[0] if xy[0] != "." else (xy[1] if len(xy) > 1 else ".")
    return (code if code != "." else "M").upper()


def _git_branch_base(root: Path) -> str:
    from . import git_scope as GI
    head = _git_origin_head_abbrev(root)
    candidates = ([head] if head else []) + [
        "origin/main", "origin/master", "main", "master"]
    for ref in candidates:
        try:
            text, _trunc = GI._run(root, ["merge-base", "HEAD", ref])
        except GI.GitError:
            continue
        line = text.splitlines()[0].strip() if text.strip() else ""
        if line:
            return line
    return ""


def _git_resolved_root(cfg: Any, q: Dict[str, str],
                       b: Dict[str, Any]):
    """(resolved_root, error_response) for a scoped repo read."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return None, None
    try:
        resolved = GI.resolve_root(cfg, root)
        GI._check_repo(resolved)
    except GI.GitError as exc:
        return None, _git_error(exc)
    return resolved, None


def _review_files(root: Path, files: List[Dict[str, Any]], base: Optional[str],
                  scope: str) -> Tuple[int, Dict[str, Any]]:
    files.sort(key=lambda f: f["path"])
    for f in files:
        if f["status"] == "?" and f["added"] == 0 and f["removed"] == 0:
            f["added"] = _git_untracked_insertions(root, f["path"])
    return _ok(schema="qubit-hermes-git/1", root=str(root), files=files,
               base=base, scope=scope)


def h_git_worktrees(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/worktrees -> scoped ``git worktree list`` (read-only)."""
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(worktrees=[])
    return _ok(schema="qubit-hermes-git/1", root=str(resolved),
               worktrees=_git_worktree_rows(resolved))


def h_git_branches(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/branches -> local + remote-tracking branch picker rows."""
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(branches=[])
    locals_ = _git_ref_names(resolved, "refs/heads")
    if not locals_:
        return _ok(schema="qubit-hermes-git/1", root=str(resolved),
                   branches=[])
    path_by_branch = {t["branch"]: t["path"]
                      for t in _git_worktree_rows(resolved) if t.get("branch")}
    trunk = _git_default_branch(resolved)
    local_set = set(locals_)
    remotes = [name for name in _git_ref_names(resolved, "refs/remotes")
               if not name.endswith("/HEAD")
               and name.split("/", 1)[-1] not in local_set]
    branches = [
        {"name": name, "checkedOut": name in path_by_branch,
         "isDefault": bool(trunk and name == trunk), "isRemote": False,
         "worktreePath": path_by_branch.get(name)} for name in locals_]
    branches += [
        {"name": name, "checkedOut": False, "isDefault": False,
         "isRemote": True, "worktreePath": None} for name in remotes]
    return _ok(schema="qubit-hermes-git/1", root=str(resolved),
               branches=branches)


def h_git_base_branches(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/base-branches -> base-branch picker rows (trunk flagged)."""
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(branches=[])
    names = _git_ref_names(resolved, "refs/heads", "refs/remotes")
    default = _git_origin_head(resolved) or _git_default_branch(resolved)
    branches = [{"name": name, "isRemote": name.startswith("origin/"),
                 "isDefault": bool(default and name == default)}
                for name in names]
    return _ok(schema="qubit-hermes-git/1", root=str(resolved),
               branches=branches)


def h_git_review_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/list -> changed files for a review scope."""
    from . import git_scope as GI
    scope = str(q.get("scope", b.get("scope", "uncommitted"))
                or "uncommitted")
    base_ref = _query_text(q, b, "base")
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(files=[], base=None, scope=scope)
    if scope in ("branch", "lastTurn"):
        base = _git_branch_base(resolved) if scope == "branch" else base_ref
        if scope == "lastTurn" and base and (
                base.startswith("-") or not _REV_PARSE_RE.match(base)):
            return _git_error(GI.GitError(GI.INPUT_CODE, "bad base ref"))
        if not base:
            return _ok(schema="qubit-hermes-git/1", root=str(resolved),
                       files=[], base=None, scope=scope)
        rng = f"{base}...HEAD" if scope == "branch" else base
        counts = _git_numstat(resolved, rng)
        files = [{"path": path, "added": a, "removed": r,
                  "status": "M", "staged": False}
                 for path, (a, r) in counts.items()]
        if scope == "lastTurn":
            seen = {f["path"] for f in files}
            for tag, _xy, path in _porcelain_entries(
                    _git_status_raw(resolved)):
                if tag == "?" and path not in seen:
                    files.append({"path": path, "added": 0, "removed": 0,
                                  "status": "?", "staged": False})
        return _review_files(resolved, files, base, scope)
    raw = _git_status_raw(resolved)
    staged = _git_numstat(resolved, "--cached")
    unstaged = _git_numstat(resolved)
    files = []
    for tag, xy, path in _porcelain_entries(raw):
        sa, sr = staged.get(path, (0, 0))
        ua, ur = unstaged.get(path, (0, 0))
        files.append({
            "path": path, "added": sa + ua, "removed": sr + ur,
            "status": _git_status_letter(tag, xy),
            "staged": tag in ("1", "2") and xy[0] not in (".", "?")})
    return _review_files(resolved, files, None, scope)


def h_git_review_commit_context(cfg: Any, q: Dict[str, str],
                                b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/commit-context -> staged/worktree diff + recents."""
    from . import git_scope as GI
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(diff="", recent="")
    raw = _git_status_raw(resolved)
    entries = list(_porcelain_entries(raw))
    staged = any(tag in ("1", "2") and xy[0] not in (".", "?")
                 for tag, xy, _path in entries)
    try:
        diff, _trunc = GI._run(
            resolved, ["diff", "--cached"] if staged else ["diff", "HEAD"])
    except GI.GitError:
        diff = ""
    maxchars = 20000
    if len(diff) > maxchars:
        omitted = len(diff) - maxchars
        diff = diff[:maxchars] + \
            f"\n# diff truncated: {omitted} chars omitted\n"
    untracked = [path for tag, _xy, path in entries if tag == "?"]
    if untracked:
        visible = untracked[:50]
        diff += "\n# New (untracked) files:\n" + \
            "".join(f"#   {path}\n" for path in visible)
        if len(untracked) > len(visible):
            diff += f"#   ... {len(untracked) - len(visible)} more omitted\n"
    recent = _git_line(resolved,
                       ["log", "-n", "10", "--pretty=format:%s"])
    return _ok(schema="qubit-hermes-git/1", root=str(resolved),
               diff=diff or "", recent=recent)


def h_git_review_rev_parse(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/rev-parse -> resolved object name for one ref."""
    from . import git_scope as GI
    ref = str(q.get("ref", b.get("ref", "HEAD")) or "HEAD").strip()
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(ref=ref, sha=None)
    if not _REV_PARSE_RE.match(ref) or ref.startswith("-"):
        return _git_error(GI.GitError(GI.INPUT_CODE, "bad ref"))
    try:
        text, _trunc = GI._run(
            resolved, ["rev-parse", "--verify", "--quiet", ref])
    except GI.GitError:
        return _ok(schema="qubit-hermes-git/1", root=str(resolved), ref=ref,
                   sha=None)
    sha = text.splitlines()[0].strip() if text.strip() else None
    return _ok(schema="qubit-hermes-git/1", root=str(resolved), ref=ref,
               sha=sha)


def _files_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    from . import files as FI
    return 400, {"ok": False, "state": "FAILED",
                 "code": getattr(exc, "code", FI.BAD_INPUT_CODE),
                 "detail": _redact(str(exc))[:300],
                 "qubit_counterpart":
                     "CLI `qubit files list|read` (scoped project folders)"}


def _fs_entries(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in (res.get("entries", []) if isinstance(res, dict) else []):
        if not isinstance(entry, dict):
            continue
        out.append({"name": entry.get("name", ""),
                    "path": entry.get("rel", ""),
                    "isDirectory": bool(entry.get("dir", False)),
                    "size": entry.get("size")})
    return out


def h_fs_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/list -> project-root directory listing (read-only)."""
    from . import files as FI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return _ok(entries=[], folder=None, truncated=False,
                   detail="no active project folder; register one with "
                          "`qubit project add-folder`")
    try:
        res = FI.list_dir(cfg, path)
    except FI.FilesError as exc:
        return _files_error(exc)
    return _ok(entries=_fs_entries(res), folder=res.get("folder", path),
               truncated=bool(res.get("truncated", False)))


def h_fs_tree(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/tree -> bounded directory tree (depth 1..4)."""
    from . import files as FI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    try:
        depth = max(1, min(int(q.get("depth", "2") or "2"), FI.MAX_DEPTH))
    except (TypeError, ValueError):
        depth = 2
    if not path:
        return _ok(entries=[], folder=None, depth=depth, truncated=False,
                   detail="no active project folder; register one with "
                          "`qubit project add-folder`")
    try:
        res = FI.list_dir(cfg, path, depth=depth)
    except FI.FilesError as exc:
        return _files_error(exc)
    return _ok(entries=_fs_entries(res), folder=res.get("folder", path),
               depth=depth, truncated=bool(res.get("truncated", False)))


def h_fs_stat(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/stat -> inspect a folder listing or file metadata."""
    from . import files as FI
    try:
        res = FI.inspect(cfg, _query_text(q, b, "path"))
    except FI.FilesError as exc:
        return _files_error(exc)
    return _ok(stat=res)


def _fs_kind(path: str) -> Tuple[str, str]:
    suffix = Path(str(path or "")).suffix.lower()
    return (_FS_LANGUAGE_BY_EXT.get(suffix, "text"),
            _FS_MIME_BY_EXT.get(suffix, "text/plain"))


def h_fs_read_text(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/read-text -> bounded, redacted text file read."""
    from . import files as FI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return 400, {"ok": False, "code": FI.BAD_INPUT_CODE,
                     "detail": "a file path is required",
                     "qubit_counterpart": "CLI `qubit files read <path>`"}
    try:
        res = FI.read_file(cfg, path)
    except FI.FilesError as exc:
        if getattr(exc, "code", "") == FI.BINARY_CODE:
            language, mime = _fs_kind(path)
            return _ok(binary=True, byteSize=0, language=language,
                       mimeType=mime, path=str(path), text="",
                       truncated=False,
                       detail="binary file; content not shown")
        return _files_error(exc)
    target = str(res.get("file", path) or path)
    language, mime = _fs_kind(target)
    return _ok(binary=False, byteSize=int(res.get("size", 0) or 0),
               language=language, mimeType=mime, path=target,
               text=res.get("text", ""),
               truncated=bool(res.get("clipped", False)))


def h_fs_read_data_url(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/read-data-url -> text file as a redacted data URL."""
    from . import files as FI
    import base64
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return 400, {"ok": False, "code": FI.BAD_INPUT_CODE,
                     "detail": "a file path is required",
                     "qubit_counterpart": "CLI `qubit files read <path>`"}
    try:
        res = FI.read_file(cfg, path)
    except FI.FilesError as exc:
        return _files_error(exc)
    target = str(res.get("file", path) or path)
    _language, mime = _fs_kind(target)
    encoded = base64.b64encode(
        str(res.get("text", "")).encode("utf-8", "replace")).decode("ascii")
    return _ok(dataUrl=f"data:{mime};base64,{encoded}", path=target,
               mimeType=mime)


def _find_git_root(start: Path) -> Optional[Path]:
    try:
        directory = start if start.is_dir() else start.parent
    except OSError:
        return None
    for _ in range(50):
        try:
            if (directory / ".git").exists():
                return directory
        except OSError:
            return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent
    return None


def h_fs_git_root(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/git-root -> git root discovery inside an approved root."""
    from . import git_scope as GI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return _ok(root=None)
    try:
        resolved = GI.resolve_root(cfg, path)
    except GI.GitError:
        return _ok(root=None)
    found = _find_git_root(Path(resolved))
    return _ok(root=(str(found) if found else None))


def _git_branch(cfg: Any, root: str) -> str:
    from . import git_scope as GI
    try:
        res = GI.status(cfg, root, requested_by="dashboard:user")
    except GI.GitError:
        return ""
    for line in str(res.get("status", "")).splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            return "" if head == "(detached)" else head
    return ""


def h_fs_default_cwd(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/default-cwd -> active project folder + its branch."""
    cwd = _active_folder(cfg)
    if not cwd:
        try:
            cwd = str(Path.cwd())
        except Exception:
            cwd = ""
    return _ok(cwd=cwd, branch=(_git_branch(cfg, cwd) if cwd else ""))


def h_audio_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/status -> real mic/output status (audio.settings)."""
    from .audio import settings as AS
    try:
        st = AS.status(cfg)
    except AS.AudioSettingsError as exc:
        miss = {"state": "MISSING", "detail": _redact(str(exc))[:200]}
        return _ok(mic=miss, output=dict(miss), schema="qubit-hermes-audio/1")
    return _ok(schema="qubit-hermes-audio/1",
               mic=st.get("mic", {}), output=st.get("output", {}))


def _device_rows(devices: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for dev in (devices if isinstance(devices, list) else []):
        rows.append({"stable_id": getattr(dev, "stable_id", ""),
                     "name": getattr(dev, "name", ""),
                     "backend": getattr(dev, "backend", ""),
                     "is_default": bool(getattr(dev, "is_default", False))})
    return rows


def h_audio_devices(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/list|devices -> real device inventory (audio.settings)."""
    from .audio import settings as AS
    try:
        devs = AS.list_devices()
    except AS.AudioSettingsError as exc:
        return _ok(inputs=[], outputs=[], state="MISSING",
                   detail=_redact(str(exc))[:200])
    return _ok(inputs=_device_rows(devs.get("inputs", [])),
               outputs=_device_rows(devs.get("outputs", [])))


# ---------------------------------------------------------------------------
# Audio HTTP actions (audit §1A audio.py reads): voice-config (profile refs),
# transcribe (local ASR over a bounded upload), speak (local TTS -> data URL).
# Streaming/WS, external catalogs, and desktop leases stay typed-gated.
# ---------------------------------------------------------------------------

_AUDIO_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_AUDIO_MIME_EXT = {
    "audio/aac": ".aac", "audio/flac": ".flac", "audio/m4a": ".m4a",
    "audio/mp3": ".mp3", "audio/mp4": ".mp4", "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/wave": ".wav",
    "audio/webm": ".webm", "audio/x-m4a": ".m4a", "audio/x-wav": ".wav",
    "video/webm": ".webm",
}


def _asr_from_cfg(cfg: Any):
    """Build the configured ASR provider (same selection as the orchestrator)."""
    from .asr.sherpa import SherpaASR
    from .asr.whisper import FasterWhisperASR
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    provider = asr_cfg.get("provider", "sherpa")
    if provider == "personaplex":
        provider = "sherpa"
    model_dir = cfg.model_dir()
    if provider == "sherpa":
        return SherpaASR(engine=asr_cfg.get("engine", "moonshine"),
                         model_dir=model_dir,
                         provider=asr_cfg.get("provider_ep", "cpu"),
                         num_threads=int(asr_cfg.get("num_threads", 4)),
                         language=asr_cfg.get("language"),
                         hotwords=asr_cfg.get("hotwords", ""),
                         hotwords_score=float(
                             asr_cfg.get("hotwords_score", 1.5)))
    return FasterWhisperASR(model=asr_cfg.get("model", "base"),
                            device=asr_cfg.get("device", "auto"),
                            compute_type=asr_cfg.get("compute_type",
                                                     "float16"),
                            language=asr_cfg.get("language"),
                            beam_size=asr_cfg.get("beam_size", 1),
                            model_dir=model_dir)


def _tts_from_cfg(cfg: Any):
    """Build the configured TTS provider (same selection as the orchestrator)."""
    from .tts.piper import PiperTTS
    provider = cfg.get("tts", "provider", default="piper")
    if provider == "edge":
        from .tts.edge import EdgeTTS
        edge = EdgeTTS(
            voice=cfg.get("tts", "edge", "voice",
                          default="en-US-JennyNeural"),
            rate=cfg.get("tts", "edge", "rate", default="+0%"))
        if edge.available():
            return edge
    if provider == "hybrid":
        from .tts.hybrid import HybridTTS
        h = cfg.get("tts", "hybrid", default={})
        h = h if isinstance(h, dict) else {}
        return HybridTTS(
            model_dir=cfg.model_dir(),
            piper_voice=h.get("piper_voice",
                              cfg.get("tts", "voice",
                                      default="en_US-amy-medium")),
            openai_voice=h.get("openai_voice", "nova"),
            openai_model=h.get("openai_model", "gpt-4o-mini-tts"),
            key_env=cfg.get("tts", "openai", "key_env",
                            default="~/.config/qubit-voicebridge/.env"),
            instructions=cfg.get("tts", "openai", "instructions", default=""),
            threshold_words=int(h.get("threshold_words", 18)))
    if provider == "openai":
        from .tts.openai_tts import OpenAITTS
        tts = OpenAITTS(
            voice=cfg.get("tts", "openai", "voice", default="nova"),
            model=cfg.get("tts", "openai", "model",
                          default="gpt-4o-mini-tts"),
            key_env=cfg.get("tts", "openai", "key_env",
                            default="~/.config/qubit-voicebridge/.env"),
            instructions=cfg.get("tts", "openai", "instructions", default=""))
        if tts.available():
            return tts
    if provider == "kokoro":
        from .tts.kokoro import KokoroTTS
        tts = KokoroTTS(model_dir=cfg.model_dir(),
                        voice_sid=int(cfg.get("tts", "kokoro", "voice_sid",
                                              default=0)))
        if tts.available():
            return tts
    return PiperTTS(model_dir=cfg.model_dir(),
                    voice=cfg.get("tts", "voice", default="en_US-amy-medium"))


def h_audio_voice_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/voice-config -> active STT/TTS profile + audio refs.

    Resolves the active profile per component from ``voicebridge.profiles`` and
    the live mic/output contract from ``voicebridge.audio.settings``. Qubit
    providers run host-side, so every provider resolves to ``mode: relay`` and
    NO provider key or credential is ever exported (references only).
    """
    from . import profiles as PR
    from .audio import settings as AS
    try:
        current = PR.current_profiles(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        status = AS.status(cfg)
    except AS.AudioSettingsError as exc:
        miss = {"state": "MISSING", "detail": _redact(str(exc))[:200]}
        status = {"mic": dict(miss), "output": dict(miss)}

    def _relay(entry: Dict[str, Any], kind: str) -> Dict[str, Any]:
        value = entry.get("value")
        provider = value if isinstance(value, (str, int, float)) else ""
        return {
            "mode": "relay", "kind": kind,
            "profile": str(entry.get("id", "") or ""),
            "state": str(entry.get("state", "") or ""),
            "provider": str(provider or ""),
            "detail": "Qubit runs this provider host-side; the client keeps "
                      "the local audio relay and no provider key is exported",
        }

    voice = current.get("voice", {}) or {}
    return _ok(schema="qubit-hermes-audio/1", mode="relay",
               stt=_relay(current.get("stt", {}), "stt"),
               tts=_relay(current.get("tts", {}), "tts"),
               voice={"profile": str(voice.get("id", "") or ""),
                      "name": str(voice.get("value", "") or "")},
               audio=status,
               detail="STT/TTS resolve to host-side Qubit providers; no "
                      "provider credential is served")


def h_audio_transcribe(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/audio/transcribe -> local ASR over a bounded data URL body.

    Strict caps: audio/* (or video/webm), valid base64, non-empty, <= 25 MiB;
    the temp file is always removed. A missing model is a typed
    ``AUDIO_ASR_UNAVAILABLE`` refusal, never a fabricated transcript.
    """
    import base64
    import binascii
    import os
    import tempfile
    from .asr.base import ASRUnavailable
    counterpart = "Qubit Chat voice (local mic capture) + CLI `qubit asr-test`"
    data_url = str(b.get("data_url", b.get("dataUrl", "")) or "").strip()
    if not data_url:
        return _gate("AUDIO_PAYLOAD_REQUIRED",
                     "transcription needs a base64 audio data URL",
                     counterpart, 400)
    if not data_url.startswith("data:") or "," not in data_url:
        return _gate("AUDIO_BAD_PAYLOAD", "invalid audio payload",
                     counterpart, 400)
    header, encoded = data_url.split(",", 1)
    if ";base64" not in header:
        return _gate("AUDIO_BAD_PAYLOAD",
                     "audio payload must be base64 encoded", counterpart, 400)
    mime = str(b.get("mime_type", b.get("mimeType", ""))
               or header[5:].split(";", 1)[0] or "audio/webm").strip()
    norm = mime.split(";", 1)[0].lower()
    if not (norm.startswith("audio/") or norm == "video/webm"):
        return _gate("AUDIO_BAD_PAYLOAD",
                     "payload must be an audio recording", counterpart, 400)
    # Cap the ENCODED length before decoding so an oversized body cannot force
    # a huge base64 allocation (25 MiB decoded ~= 33.4 MiB encoded).
    if len(encoded) > (_AUDIO_MAX_UPLOAD_BYTES // 3) * 4 + 8:
        return _gate("AUDIO_TOO_LARGE", "audio recording is too large",
                     counterpart, 413)
    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return _gate("AUDIO_BAD_PAYLOAD",
                     "audio payload is not valid base64", counterpart, 400)
    if not audio_bytes:
        return _gate("AUDIO_BAD_PAYLOAD", "audio recording is empty",
                     counterpart, 400)
    if len(audio_bytes) > _AUDIO_MAX_UPLOAD_BYTES:
        return _gate("AUDIO_TOO_LARGE", "audio recording is too large",
                     counterpart, 413)
    temp_path = ""
    try:
        suffix = _AUDIO_MIME_EXT.get(norm, ".webm")
        with tempfile.NamedTemporaryFile(
                prefix="qubit-hermes-voice-", suffix=suffix,
                delete=False) as tmp:
            tmp.write(audio_bytes)
            temp_path = tmp.name
        asr = _asr_from_cfg(cfg)
        asr.load()
        transcript = asr.transcribe(temp_path)
        if getattr(transcript, "empty", False):
            return _ok(transcript="", provider=transcript.provider,
                       detail="no speech detected")
        return _ok(transcript=str(transcript.text),
                   provider=transcript.provider,
                   language=transcript.language,
                   confidence=transcript.confidence,
                   duration_s=transcript.duration_s)
    except ASRUnavailable as exc:
        return _gate("AUDIO_ASR_UNAVAILABLE",
                     "local ASR model is unavailable: "
                     + _redact(str(exc))[:160],
                     "install the configured sherpa/whisper model + CLI "
                     "`qubit asr-test`")
    except Exception as exc:
        return _fail(exc)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def h_audio_speak(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/audio/speak -> local TTS rendered as a base64 data URL.

    Runs the SAME provider chain the orchestrator selects. An unavailable
    provider is a typed ``AUDIO_TTS_UNAVAILABLE`` refusal; the temp file is
    always removed and no on-disk path is returned.
    """
    import base64
    import os
    import tempfile
    from .tts.base import TTSUnavailable
    counterpart = "Qubit Chat voice + CLI `qubit tts-test`"
    text = str(b.get("text", "") or "").strip()
    if not text:
        return _gate("AUDIO_TEXT_REQUIRED", "text is required",
                     counterpart, 400)
    out = Path(tempfile.gettempdir()) / (
        f"qubit-hermes-tts-{os.getpid()}-{abs(hash(text)) % 10 ** 8}.wav")
    try:
        tts = _tts_from_cfg(cfg)
        tts.load()
        tts.synthesize(text[:4000], out)
        data = out.read_bytes()
        mime = ("audio/wav" if out.suffix.lower() == ".wav"
                else "audio/mpeg")
        encoded = base64.b64encode(data).decode("ascii")
        return _ok(data_url=f"data:{mime};base64,{encoded}", mime_type=mime,
                   provider=str(getattr(tts, "name", "") or ""))
    except TTSUnavailable as exc:
        return _gate("AUDIO_TTS_UNAVAILABLE",
                     "local TTS provider is unavailable: "
                     + _redact(str(exc))[:160],
                     "install the configured piper voice + CLI `qubit tts-test`")
    except Exception as exc:
        return _fail(exc)
    finally:
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Packet 8U closure: local models / custom endpoints / profiles / tools /
# learning (audit §1A absent reads) + remaining wrong-verb gates (audit §1B).
#
# Reads execute the SAME Qubit modules the CLI uses (``local_models``,
# ``providers.policy``, ``projects``, ``learning``, ``capability_catalog``,
# ``computer_use``). Writes never fabricate success: local-model verbs reuse
# the module's typed refusal, and pure policy boundaries are typed gates.
# ---------------------------------------------------------------------------

_LOCAL_MODEL_COUNTERPART = (
    "CLI `qubit runtime status|install <image> --confirm` + supervised "
    "opencode path (pinned, confined, audited)")

_PROVIDER_ENDPOINT_COUNTERPART = (
    "dashboard Settings (classic) + CLI config profiles")


def h_local_models_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/status -> real local-runtime state."""
    from . import local_models as LM
    return _ok(**LM.status(cfg))


def h_local_models_hardware(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/hardware -> real GPU/VRAM gauges."""
    from . import local_models as LM
    return _ok(**LM.hardware(cfg))


def h_local_models_catalog(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/catalog -> configured/observed local models."""
    from . import local_models as LM
    return _ok(**LM.catalog(cfg))


def h_local_models_jobs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/jobs -> typed-empty install job history."""
    from . import local_models as LM
    return _ok(**LM.jobs(cfg))


def h_local_models_job_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/jobs/{id} -> one job or an honest miss."""
    from . import local_models as LM
    base = LM.jobs(cfg)
    jobs = base.get("jobs", []) if isinstance(base, dict) else []
    jobs = jobs if isinstance(jobs, list) else []
    want = str(p.get("id", "") or "")
    found = None
    for job in jobs:
        if isinstance(job, dict) and str(job.get("id", "")) == want:
            found = job
            break
    return _ok(jobs=jobs, job=found, count=len(jobs),
               state=base.get("state", "EMPTY"),
               code=base.get("code"), detail=base.get("detail", ""),
               rollback=base.get("rollback",
                                 "read-only; nothing to roll back"))


def _local_model_refusal(res: Any) -> Tuple[int, Dict[str, Any]]:
    out: Dict[str, Any] = {"ok": False}
    out.update(res if isinstance(res, dict) else {})
    out.setdefault("state", "REFUSED")
    out.setdefault("code", "LOCAL_MODEL_BROKERED")
    out.setdefault("owner", "qubit-policy")
    out.setdefault("guidance", _BROKERED_GUIDANCE_LOCAL)
    out.setdefault("rollback", "nothing was downloaded, started, ejected, "
                               "activated, or written; nothing to roll back")
    out.setdefault("qubit_counterpart", _LOCAL_MODEL_COUNTERPART)
    state = str(out.get("state", ""))
    http = 200 if state in ("OK", "AVAILABLE") and not out.get("code") else 403
    return http, out


_BROKERED_GUIDANCE_LOCAL = (
    "local-model runtime management is brokered in Qubit; use "
    "`qubit runtime status` and `qubit runtime install <image> --confirm` "
    "(pinned, confined, digest-verified), or the supervised opencode path. "
    "No download is performed or queued by this surface")


def h_local_models_runtime_install(cfg: Any, q: Dict[str, str],
                                   b: Dict[str, Any],
                                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    return _local_model_refusal(
        LM.runtime_install(cfg, backend=b.get("backend"),
                           requested_by="dashboard:user",
                           confirmed=bool(b.get("confirm"))))


def h_local_models_download(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model", "model_id", "id")
    return _local_model_refusal(
        LM.download(cfg, model=model, requested_by="dashboard:user",
                    confirmed=bool(b.get("confirm"))))


def h_local_models_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = str(p.get("model_id", "") or "")
    if not model:
        return _local_model_refusal(
            LM.public_error(LM.BAD_INPUT_CODE, "model_id is required"))
    return _local_model_refusal(
        LM.public_error(LM.BROKERED_CODE,
                        f"removing local model {model!r} is brokered; nothing "
                        "was deleted"))


def h_local_models_quickstart(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model") or None
    return _local_model_refusal(
        LM.quickstart(cfg, model=model, requested_by="dashboard:user",
                      confirmed=bool(b.get("confirm"))))


def h_local_models_server(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    action = _query_text(q, b, "action") or "start"
    return _local_model_refusal(
        LM.server(cfg, action, requested_by="dashboard:user",
                  confirmed=bool(b.get("confirm"))))


def h_local_models_eject(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model", "model_id")
    return _local_model_refusal(
        LM.eject(cfg, model=model, requested_by="dashboard:user",
                 confirmed=bool(b.get("confirm"))))


def h_local_models_activate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model", "model_id")
    return _local_model_refusal(
        LM.activate(cfg, model=model, requested_by="dashboard:user",
                    confirmed=bool(b.get("confirm"))))


def h_local_models_sideload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    path = _query_text(q, b, "path")
    return _local_model_refusal(
        LM.sideload(cfg, path=path, requested_by="dashboard:user",
                    confirmed=bool(b.get("confirm"))))


def h_local_models_search(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    return _local_model_refusal(
        LM.public_error(LM.UNAVAILABLE_CODE,
                        "remote local-model search is not performed; Qubit "
                        "downloads nothing from a model hub"))


def h_local_models_search_files(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_local_models_search(cfg, q, b, p)


def h_local_models_download_browsed(cfg: Any, q: Dict[str, str],
                                    b: Dict[str, Any],
                                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    return _local_model_refusal(
        LM.public_error(LM.BROKERED_CODE,
                        "browsed model download is brokered; nothing was "
                        "downloaded or queued"))


def h_custom_endpoints(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/providers/custom-endpoints -> configured + staged endpoints."""
    from .providers.policy import FrontierPolicy
    try:
        policy = FrontierPolicy.from_config(cfg)
    except Exception as exc:
        return _fail(exc)
    endpoints: List[Dict[str, Any]] = []
    providers = getattr(policy, "providers", {}) or {}
    for provider_id, pcfg in providers.items():
        if not isinstance(pcfg, dict):
            continue
        base = str(pcfg.get("base_url", pcfg.get("url", "")) or "")
        models = pcfg.get("models")
        models = models if isinstance(models, list) else []
        endpoints.append({
            "id": str(provider_id), "name": str(pcfg.get("name", provider_id)
                                                or provider_id),
            "base_url": _redact(base),
            "model": str(pcfg.get("model", "") or ""),
            "models": [str(m) for m in models],
            "context_length": pcfg.get("context_length"),
            "discover_models": bool(pcfg.get("discover_models", True)),
            "has_api_key": bool(pcfg.get("key_ref") or pcfg.get("api_key")),
            "api_key_preview": None,
            "is_current": False, "source": "intent.frontier.providers",
        })
    staged = _load_json_store(_custom_endpoints_path(cfg))
    seen = {row["id"] for row in endpoints}
    for ident in sorted(staged):
        rec = staged[ident]
        if not isinstance(rec, dict) or ident in seen:
            continue
        endpoints.append({
            "id": ident, "name": str(rec.get("name", ident) or ident),
            "base_url": _redact(str(rec.get("base_url", rec.get("url", "")))),
            "model": str(rec.get("model", "") or ""),
            "models": [str(m) for m in (rec.get("models") or [])
                       if isinstance(m, str)],
            "context_length": rec.get("context_length"),
            "discover_models": bool(rec.get("discover_models", True)),
            "has_api_key": False, "api_key_preview": None,
            "is_current": bool(rec.get("active", False)),
            "source": "qubit-settings",
        })
    active = str(staged.get("_active", "") or "")
    return _ok(endpoints=endpoints,
               current={"provider": active or str(getattr(policy, "route", "")
                                                  or ""),
                        "model": "", "base_url": ""})


def _custom_endpoints_path(cfg: Any) -> Path:
    return _store_path(cfg, "hermes_custom_endpoints.json")


def _custom_endpoint_id(b: Dict[str, Any], p: Dict[str, str]) -> str:
    raw = str(p.get("id", "") or b.get("id", b.get("endpoint_id",
                                                     b.get("name", ""))) or "")
    return re.sub(r"[^a-z0-9_.-]+", "-", raw.strip().lower()).strip("-")[:64]


def h_custom_endpoints_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/providers/custom-endpoints -> real settings-console write.

    Persists the endpoint into the same state-dir settings store the config
    console uses (atomic backup + audit). Confirm-gated; a malformed body or
    missing id is a typed 400. Staged endpoints are visible via the GET above
    and are never auto-activated or probed.
    """
    if not isinstance(b, dict) or not b:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint body required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    ident = _custom_endpoint_id(b, p)
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint id/name required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    if not b.get("confirm"):
        return _staged(
            f"custom endpoint {ident!r} is a settings write: re-POST with "
            "{\"confirm\": true} plus a fresh approval", "",
            {"qubit_counterpart": "CLI `qubit config` profiles + dashboard "
                                  "Settings (classic)"})
    values = {k: v for k, v in b.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    values["id"] = ident
    path = _custom_endpoints_path(cfg)
    try:
        store = _load_json_store(path)
        previous = store.get(ident)
        store[ident] = values
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, id=ident, endpoint=values,
               replaced=isinstance(previous, dict), count=len(store),
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def h_custom_endpoints_activate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST .../{id}/activate -> real confirm-gated active-endpoint selection."""
    ident = _custom_endpoint_id(b, p)
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint id required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    if not b.get("confirm"):
        return _staged(
            f"activating custom endpoint {ident!r} requires confirmation: "
            "re-POST with {\"confirm\": true}", "")
    path = _custom_endpoints_path(cfg)
    store = _load_json_store(path)
    if ident not in store:
        return 404, {"ok": False, "code": "ENDPOINT_NOT_FOUND",
                     "detail": f"no staged endpoint {ident!r}",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    try:
        store["_active"] = ident
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, id=ident, active=ident,
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def h_custom_endpoints_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/providers/custom-endpoints/{id} -> real confirm-gated remove."""
    ident = _custom_endpoint_id(b, p)
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint id required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    if not b.get("confirm"):
        return _staged(
            f"removing custom endpoint {ident!r} requires confirmation: "
            "re-POST with {\"confirm\": true}", "")
    path = _custom_endpoints_path(cfg)
    try:
        store = _load_json_store(path)
        existed = ident in store
        store.pop(ident, None)
        if store.get("_active") == ident:
            store.pop("_active", None)
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, id=ident, removed=existed, count=len(store),
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def h_profiles_sessions(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/sessions -> unified receipts list (one profile)."""
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 500))
    except (TypeError, ValueError):
        limit = 20
    code, out = h_sessions_list(cfg, {"limit": str(limit)}, b, p)
    if code != 200:
        return code, out
    total = int(out.get("total", 0) or 0)
    return _ok(sessions=out.get("sessions", []), total=total,
               profile_totals={"default": total}, limit=limit,
               offset=int(out.get("offset", 0) or 0), errors=[])


def h_profiles_sessions_sidebar(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/sessions/sidebar -> recents slice (real receipts)."""
    try:
        recents_limit = max(1, min(int(q.get("recents_limit", "20") or "20"),
                                   500))
    except (TypeError, ValueError):
        recents_limit = 20
    code, out = h_sessions_list(cfg, {"limit": str(recents_limit)}, b, p)
    sessions = out.get("sessions", []) if code == 200 else []
    return _ok(recents={"sessions": sessions, "profiles_truncated": {},
                        "profiles_usage": {}},
               cron={"sessions": []},
               messaging={"sessions": [], "total": 0},
               errors=[])


def h_profiles_projects_tree(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/projects/tree -> real Qubit project folders."""
    from . import projects as PJ
    try:
        res = PJ.list_projects(cfg)
    except Exception as exc:
        return _fail(exc)
    projects = []
    for rec in res.get("projects", []) if isinstance(res, dict) else []:
        if not isinstance(rec, dict):
            continue
        folders = rec.get("folders", []) or []
        projects.append({
            "id": str(rec.get("slug", "")),
            "name": str(rec.get("name", "") or ""),
            "path": str(folders[0]) if folders else None,
            "isAuto": False, "repos": [], "sessionCount": 0,
            "totalTokens": 0, "totalCostUsd": 0.0,
            "lastActive": rec.get("updated_at"), "previewSessions": [],
        })
    active = res.get("active_slug") if isinstance(res, dict) else None
    return _ok(projects=projects, active_id=active, scoped_session_ids=[],
               errors=[])


def h_profile_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/profiles -> real confirm-gated principal registration.

    Registers the profile in the same ``brain`` principal registry the CLI
    (``qubit brain principal-add``) and voice use. Without ``confirm`` it
    stages a typed 409 on the shared journal; with it the principal is created
    (audited) and a duplicate is a typed 400. It never fabricates success.
    """
    from . import brain as B
    name = str(b.get("name", "") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required",
                     "qubit_counterpart": "CLI `qubit brain principal-add`"}
    if not b.get("confirm"):
        return _staged(
            f"profile {_redact(name)!r} creation stages through the Qubit "
            "principal registry; re-POST with {\"confirm\": true}", "")
    kind = str(b.get("kind", "person") or "person")
    label = str(b.get("label", name) or name)
    try:
        res = B.principal_add(cfg, name, kind, label,
                              requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit brain principal-add`"}
    return _ok(ok=True, name=name, principal=res.get("principal"),
               qubit_counterpart="CLI `qubit brain principal-add`")


def h_profile_export(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/profiles/{name}/export -> real confirm-gated Brain export.

    Reads the scoped profile data held by the same ``brain`` store the CLI
    (``qubit brain export``) and dashboard /ops use. Confirm-gated and typed:
    an unknown principal is a 404, never a fabricated export.
    """
    from . import brain as B
    name = str(p.get("name", "") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required",
                     "qubit_counterpart": "CLI `qubit brain export <principal>`"}
    if not b.get("confirm"):
        return _staged(
            f"profile {_redact(name)!r} export reads scoped profile data and "
            "requires confirmation: re-POST with {\"confirm\": true}", "")
    try:
        res = B.profile_export(cfg, name,
                               identity=str(b.get("identity", "") or ""))
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit brain export <principal>`"}
    return _ok(ok=True, principal=res.get("principal", name),
               profiles=res.get("profiles", {}),
               qubit_counterpart="CLI `qubit brain export <principal>`")


def h_terminal_backends(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/tools/terminal/backends -> real local backend inventory."""
    from . import capability_catalog as CC
    try:
        local = CC.check("terminal")
    except Exception:
        local = {"state": "UNKNOWN", "explanation": ""}
    ready = str(local.get("state", "")) in ("IMPLEMENTED", "NOT_UPSTREAM")
    backends = [{
        "name": "local", "label": "Qubit local (brokered sandbox)",
        "description": str(local.get("explanation", "")
                           or "Qubit local command execution")[:200],
        "active": True, "status": "ready" if ready else "needs_setup",
        "detail": "single Qubit sandbox backend; no remote terminal pool",
    }]
    return _ok(active="local", backends=backends)


def h_terminal_backend_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/tools/terminal/backend -> real confirm-gated capability grant.

    Qubit runs exactly one local brokered sandbox; the requested backend is
    recorded as a capability grant (``terminal.backend``) in the same
    ``PermissionsStore`` CLI/voice use, with audit and confirmation. It never
    starts a remote terminal pool or fabricates an active switch.
    """
    from .permissions import permissions_store
    backend = str(b.get("backend", b.get("name", "local")) or "local").strip()
    if not backend:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "backend required",
                     "qubit_counterpart": "CLI `qubit permissions`"}
    if not b.get("confirm"):
        return _staged(
            f"terminal backend {backend!r} selection requires confirmation: "
            "re-POST with {\"confirm\": true}", "")
    try:
        grant = permissions_store(cfg).grant(
            "terminal.backend", requested_by="dashboard:user", confirmed=True,
            scopes=(backend,),
            note="dashboard terminal backend selection (local sandbox only)")
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit permissions`"}
    return _ok(ok=True, active="local", backend=backend, grant_id=grant.id,
               qubit_counterpart="CLI `qubit permissions` + `qubit setup` "
                                 "(single local sandbox backend)")


def h_computer_use_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/tools/computer-use/status -> real readiness probes."""
    from . import computer_use as CU
    try:
        res = CU.status()
    except Exception as exc:
        return _fail(exc)
    return _ok(**res)


def h_computer_use_grant(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/tools/computer-use/permissions/grant -> real audited grant.

    Records a desktop-control capability grant (``computer_use.access``) in
    the shared ``PermissionsStore`` the CLI/voice use, confirm-gated and
    audited. It never downloads an unpinned driver (``computer_use`` keeps
    that a separate typed refusal).
    """
    from . import computer_use as CU
    from .permissions import permissions_store
    scope = str(b.get("scope", b.get("capability", "desktop")) or "desktop")
    if not b.get("confirm"):
        return _staged(
            "desktop-control permission grant requires confirmation: re-POST "
            "with {\"confirm\": true}", "")
    try:
        grant = permissions_store(cfg).grant(
            "computer_use.access", requested_by="dashboard:user",
            confirmed=True, scopes=(scope,),
            note="dashboard computer-use permission grant (accessibility)")
        try:
            status = CU.status()
        except Exception:
            status = {}
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit computer-use permissions`"}
    return _ok(ok=True, grant_id=grant.id, scope=scope, status=status,
               qubit_counterpart="CLI `qubit computer-use permissions` "
                                 "(Packet 8V accessibility)")



def h_learning_graph(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/learning/graph -> lessons ledger as a graph."""
    from . import learning as L
    try:
        lessons = L.LearningLedger(Path(cfg.state_dir())).lessons()
    except Exception as exc:
        return _fail(exc)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_evidence: set = set()
    for lesson in lessons if isinstance(lessons, list) else []:
        if not isinstance(lesson, dict):
            continue
        lid = str(lesson.get("lesson_id", "") or "")
        if not lid:
            continue
        nodes.append({"id": lid, "type": "lesson",
                      "label": str(lesson.get("text", ""))[:80],
                      "kind": lesson.get("kind"),
                      "confidence": lesson.get("confidence"),
                      "source": lesson.get("source")})
        for ev in lesson.get("evidence_ids", []) or []:
            ev = str(ev)
            if ev and ev not in seen_evidence:
                seen_evidence.add(ev)
                nodes.append({"id": ev, "type": "evidence", "label": ev})
            if ev:
                edges.append({"source": ev, "target": lid, "kind": "supports"})
        for other in lesson.get("conflict_with", []) or []:
            edges.append({"source": lid, "target": str(other),
                          "kind": "conflicts"})
    lessons_n = sum(1 for n in nodes if n["type"] == "lesson")
    evidence_n = sum(1 for n in nodes if n["type"] == "evidence")
    return _ok(nodes=nodes, edges=edges,
               stats={"lessons": lessons_n, "evidence": evidence_n,
                      "nodes": len(nodes), "edges": len(edges)})


def h_learning_node(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/learning/node -> one lesson's stored provenance."""
    from . import learning as L
    lid = str(q.get("id", b.get("id", "")) or "").strip()
    if not lid:
        return _ok(found=False, id="", node=None,
                   detail="learning node id required",
                   qubit_counterpart="CLI `qubit lessons show <id>`")
    try:
        detail = L.LearningLedger(Path(cfg.state_dir())).explain(lid)
    except Exception as exc:
        return _fail(exc)
    if detail is None:
        return _ok(found=False, id=lid, node=None,
                   detail=f"no learning node {lid!r}",
                   qubit_counterpart="CLI `qubit lessons` (same LearningLedger)")
    return _ok(found=True, id=lid, node={"id": lid, **detail})


def h_learning_node_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/learning/node -> real confirm-gated lesson promotion.

    Lessons are append-only; the only in-place mutation is promoting a staged
    review item to authoritative (the same ``LearningLedger.promote`` the CLI
    ``qubit lessons promote`` calls). Without ``confirm`` it stages a typed
    409; an unknown id is a typed 404 -- never a fabricated write.
    """
    from . import learning as L
    lid = str(q.get("id", b.get("id", "")) or "").strip()
    if not lid:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "learning node id required",
                     "qubit_counterpart": "CLI `qubit lessons show <id>`"}
    if not b.get("confirm"):
        return _staged(
            "learning node promotion stages through the Qubit lessons ledger "
            "with confirmation; nothing was written", "")
    try:
        promoted = L.LearningLedger(Path(cfg.state_dir())).promote(lid)
    except Exception as exc:
        return _fail(exc)
    if promoted is None:
        return 404, {"ok": False, "code": "LESSON_NOT_FOUND",
                     "detail": f"no learning node {lid!r}",
                     "qubit_counterpart": "CLI `qubit lessons`"}
    return _ok(ok=True, id=lid, node={"id": lid, **promoted},
               qubit_counterpart="CLI `qubit lessons promote <id>`")


def h_learning_node_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/learning/node -> real confirm-gated lesson tombstone.

    Runs the same ``LearningLedger.remove`` the CLI ``qubit lessons forget``
    calls (tombstone + audit; history retained). Confirm-gated; an unknown id
    is a typed 404, never a false success.
    """
    from . import learning as L
    lid = str(q.get("id", b.get("id", "")) or "").strip()
    if not lid:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "learning node id required",
                     "qubit_counterpart": "CLI `qubit lessons forget <id>`"}
    if not b.get("confirm"):
        return _staged(
            "learning node deletion stages through the Qubit lessons ledger "
            "with confirmation; nothing was removed", "")
    try:
        removed = L.LearningLedger(Path(cfg.state_dir())).remove(lid)
    except Exception as exc:
        return _fail(exc)
    if not removed:
        return 404, {"ok": False, "code": "LESSON_NOT_FOUND",
                     "detail": f"no learning node {lid!r}",
                     "qubit_counterpart": "CLI `qubit lessons`"}
    return _ok(ok=True, id=lid, removed=True,
               qubit_counterpart="CLI `qubit lessons forget <id>`")


# ---------------------------------------------------------------------------
# Qubit-owned dashboard auth: loopback identity + RFC 8252 native handshake.
#
# The vendored dashboard ships an OAuth round trip
# (qubit_hermes/hermes_cli/dashboard_auth/routes.py). Qubit has no external
# IDP: its dashboard identity is the loopback operator with a loopback
# session token (voicebridge/webui.py:token_ok). These handlers are the
# Qubit-safe local equivalent: provider listing and manual-handoff guidance
# come from voicebridge.auth_status / voicebridge.auth_login, and the native
# loopback handshake (RFC 8252 + PKCE S256, single-use codes, short TTL,
# hashed at rest) is served locally by Qubit itself. No outbound request, no
# external credential capture, no query-string token, no second listener.
# Raw tokens are returned once; only their SHA-256 hash is persisted (0600).
# ---------------------------------------------------------------------------

_AUTH_SCHEMA = "qubit-dashboard-auth/1"
_AUTH_CODE_TTL = 300.0
_AUTH_ACCESS_TTL = 12 * 3600.0
_AUTH_REFRESH_TTL = 30 * 86400.0
_AUTH_LOOPBACK_HOSTS = ("127.0.0.1", "::1")


def _auth_store_path(cfg: Any) -> Path:
    return _store_path(cfg, "hermes_dashboard_auth.json")


def _auth_store(cfg: Any) -> Dict[str, Any]:
    state = _load_json_store(_auth_store_path(cfg))
    state.setdefault("schema", _AUTH_SCHEMA)
    for key in ("pending", "codes", "session", "refresh"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def _auth_flush(cfg: Any, state: Dict[str, Any]) -> None:
    path = _auth_store_path(cfg)
    _save_json_store(path, state)
    try:
        import os as _os
        _os.chmod(path, 0o600)
    except OSError:
        pass


def _auth_hash(value: Any) -> str:
    import base64 as _b64
    import hashlib as _hl
    return _b64.urlsafe_b64encode(
        _hl.sha256(str(value).encode("utf-8")).digest()).decode("ascii")


def _auth_ct_eq(a: Any, b: Any) -> bool:
    import hmac as _hmac
    return bool(a) and bool(b) and _hmac.compare_digest(str(a), str(b))


def _auth_new_token(nbytes: int = 32) -> str:
    import secrets as _secrets
    return _secrets.token_urlsafe(nbytes)


def _auth_prune(state: Dict[str, Any], now: Optional[float] = None) -> None:
    moment = time.time() if now is None else now
    for key in ("pending", "codes"):
        recs = state.get(key) or {}
        for rid in list(recs):
            rec = recs.get(rid) or {}
            if float(rec.get("expires_at", 0) or 0) < moment:
                recs.pop(rid, None)


def _auth_issue(state: Dict[str, Any], provider: str, now: float,
                user_id: str = "local-operator") -> Tuple[str, str]:
    access = _auth_new_token()
    refresh = _auth_new_token()
    state["session"] = {"token_hash": _auth_hash(access), "provider": provider,
                        "user_id": user_id, "issued_at": now,
                        "expires_at": now + _AUTH_ACCESS_TTL}
    state["refresh"] = {"token_hash": _auth_hash(refresh), "provider": provider,
                        "user_id": user_id, "issued_at": now,
                        "expires_at": now + _AUTH_REFRESH_TTL}
    return access, refresh


def _auth_bearer(access: str, refresh: str, provider: str,
                 now: float) -> Dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh,
            "token_type": "Bearer", "expires_at": now + _AUTH_ACCESS_TTL,
            "provider": provider, "user_id": "local-operator"}


def _auth_providers(cfg: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        from .auth_status import auth_status as _inv
        for rec in _inv(cfg).get("providers", []):
            name = str(rec.get("provider", "") or "")
            if not name:
                continue
            rows.append({"name": name, "display_name": name,
                         "supports_password": False,
                         "configured": bool(rec.get("configured")),
                         "key_ref": str(rec.get("key_ref", ""))})
    except Exception:
        pass
    return rows


def h_auth_providers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/auth/providers -> real local provider inventory.

    Reports the loopback identity plus every configured frontier provider
    (names + credential-reference kind only, never values). External
    provider sign-in is manual-handoff, so ``supports_password`` is False.
    """
    return _ok(providers=_auth_providers(cfg),
               identity="local-operator", mode="loopback-token",
               detail="Qubit loopback dashboard identity; external provider "
                      "sign-in is manual-handoff (no OAuth token dance)")


def h_login_page(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /login -> real Qubit loopback login bootstrap (no IDP redirect)."""
    return _ok(ok=True, identity="local-operator", mode="loopback-token",
               loopback=True, providers=_auth_providers(cfg),
               next=str(q.get("next", "") or ""),
               detail="Qubit loopback login; no external IDP redirect")


def h_auth_login(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /auth/login -> real manual-handoff enrollment plan for a provider."""
    from .auth_login import login_plan
    provider = str(q.get("provider", "") or "")
    plan = login_plan(cfg, provider)
    if plan.get("state") != "OK":
        return 404, {
            "ok": False, "code": plan.get("code", "AUTH_UNKNOWN_PROVIDER"),
            "detail": _redact(plan.get("detail", "unknown provider"))[:300],
            "qubit_counterpart": "CLI `qubit auth login --provider`",
            "known_providers": plan.get("known_providers", []),
        }
    return _ok(ok=True, provider=plan.get("provider", provider),
               next=str(q.get("next", "") or ""), handoff=plan,
               detail="manual-handoff enrollment; Qubit never performs the "
                      "provider login or captures the credential")


def h_auth_native_authorize(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /auth/native/authorize -> Qubit-local RFC 8252 + PKCE S256 start.

    Only ``http://`` loopback redirect URIs are accepted (an open redirect
    would leak a live code), the challenge method must be S256, and the
    one-time code/verifier challenge is stored hashed with a short TTL.
    """
    from urllib.parse import urlencode, urlparse
    method = str(q.get("code_challenge_method", "") or "").upper()
    challenge = str(q.get("code_challenge", "") or "")
    redirect_uri = str(q.get("redirect_uri", "") or "")
    if method != "S256":
        return 400, {"ok": False, "code": "AUTH_BAD_CHALLENGE_METHOD",
                     "detail": "code_challenge_method must be S256",
                     "qubit_counterpart": "GET /login (loopback session)"}
    if not challenge:
        return 400, {"ok": False, "code": "AUTH_BAD_CHALLENGE",
                     "detail": "code_challenge required",
                     "qubit_counterpart": "GET /login (loopback session)"}
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or \
            (parsed.hostname or "").lower() not in _AUTH_LOOPBACK_HOSTS:
        return 400, {"ok": False, "code": "AUTH_BAD_REDIRECT",
                     "detail": "native redirect_uri must be http:// on the "
                               "loopback interface (127.0.0.1 / ::1)",
                     "qubit_counterpart": "GET /login (loopback session)"}
    provider = str(q.get("provider", "") or "loopback").strip() or "loopback"
    client_state = str(q.get("state", "") or "")
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    broker_state = _auth_new_token(16)
    code = _auth_new_token(24)
    state["codes"][_auth_hash(code)] = {
        "challenge": challenge, "redirect_uri": redirect_uri,
        "client_state": client_state, "provider": provider,
        "broker_state": broker_state, "expires_at": now + _AUTH_CODE_TTL,
        "used": False,
    }
    _auth_flush(cfg, state)
    sep = "&" if "?" in redirect_uri else "?"
    authorize_url = redirect_uri + sep + urlencode(
        {"code": code, "state": broker_state})
    _audit("auth_authorize", "dashboard:user",
           {"provider": provider, "redirect_host": parsed.hostname or ""})
    return _ok(ok=True, broker_state=broker_state, provider=provider,
               redirect_uri=redirect_uri, authorize_url=authorize_url,
               code=code, expires_in=_AUTH_CODE_TTL,
               detail="Qubit-local loopback authorization "
                      "(RFC 8252 + PKCE S256); no external IDP")


def h_auth_callback(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /auth/callback -> redeem a Qubit-local code into a session."""
    raw = str(q.get("code", "") or "")
    state_param = str(q.get("state", "") or "")
    if not raw:
        return 400, {"ok": False, "code": "AUTH_MISSING_CODE",
                     "detail": "Missing authorization code",
                     "qubit_counterpart": "GET /login"}
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    key = _auth_hash(raw)
    rec = state["codes"].get(key)
    if not rec or rec.get("used"):
        return 400, {"ok": False, "code": "AUTH_BAD_CODE",
                     "detail": "Invalid or expired authorization code",
                     "qubit_counterpart": "GET /login"}
    if state_param and rec.get("broker_state") and \
            not _auth_ct_eq(state_param, rec["broker_state"]):
        return 400, {"ok": False, "code": "AUTH_STATE_MISMATCH",
                     "detail": "OAuth state mismatch (CSRF check failed)",
                     "qubit_counterpart": "GET /login"}
    rec["used"] = True
    provider = str(rec.get("provider", "loopback") or "loopback")
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_callback", "dashboard:user", {"provider": provider})
    return _ok(ok=True, next="/", provider=provider,
               session=_auth_bearer(access, refresh, provider, now))


def h_auth_native_token(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/native/token -> verify PKCE and exchange the code for tokens.

    The code is consumed on every path (no verifier oracle, no replay); any
    failure is a generic typed 400. Tokens go in the JSON body.
    """
    raw = str(b.get("code", "") or "")
    verifier = str(b.get("code_verifier", "") or "")
    if not raw or not verifier:
        return 400, {"ok": False, "code": "AUTH_MISSING_CODE",
                     "detail": "Invalid or expired authorization code.",
                     "qubit_counterpart": "GET /login"}
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    key = _auth_hash(raw)
    rec = state["codes"].get(key)
    if not rec or rec.get("used"):
        return 400, {"ok": False, "code": "AUTH_BAD_CODE",
                     "detail": "Invalid or expired authorization code.",
                     "qubit_counterpart": "GET /login"}
    state["codes"].pop(key, None)
    if not _auth_ct_eq(_auth_hash(verifier), rec.get("challenge", "")):
        _auth_flush(cfg, state)
        return 400, {"ok": False, "code": "AUTH_BAD_CODE",
                     "detail": "Invalid or expired authorization code.",
                     "qubit_counterpart": "GET /login"}
    provider = str(rec.get("provider", "loopback") or "loopback")
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_native_token", "dashboard:user", {"provider": provider})
    return _ok(ok=True, **_auth_bearer(access, refresh, provider, now))


def h_auth_native_refresh(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/native/refresh -> rotate a desktop-held refresh token."""
    raw = str(b.get("refresh_token", "") or "")
    provider_hint = str(b.get("provider", "") or "")
    if not raw:
        return 400, {"ok": False, "code": "AUTH_MISSING_REFRESH",
                     "detail": "refresh_token required",
                     "qubit_counterpart": "GET /login"}
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    rec = state.get("refresh") or {}
    valid = (rec and float(rec.get("expires_at", 0) or 0) >= now
             and _auth_ct_eq(_auth_hash(raw), rec.get("token_hash", ""))
             and (not provider_hint or provider_hint == rec.get("provider", "")))
    if not valid:
        return 401, {"ok": False, "state": "UNAUTHORIZED",
                     "code": "AUTH_SESSION_EXPIRED",
                     "detail": "Refresh token expired or invalid; start a new "
                               "sign-in.",
                     "qubit_counterpart": "GET /login"}
    provider = str(rec.get("provider", "loopback") or "loopback")
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_native_refresh", "dashboard:user", {"provider": provider})
    return _ok(ok=True, **_auth_bearer(access, refresh, provider, now))


def h_auth_password_login(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/password-login -> verify the Qubit loopback credential.

    Qubit keeps no password store. The local equivalent accepts either a
    token previously issued by the loopback native flow or the configured
    dashboard token (``webui.token_ok``, constant-time, never a query-string
    token). Unknown/mismatched credentials are a generic 401.
    """
    supplied = str(b.get("token", b.get("password", "")) or "")
    provider = str(b.get("provider", "") or "loopback") or "loopback"
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    sess = state.get("session") or {}
    ok = bool(supplied) and _auth_ct_eq(_auth_hash(supplied),
                                        sess.get("token_hash", ""))
    if not ok and supplied:
        try:
            from .webui import token_ok
            cfg_token = str(getattr(cfg, "api_token", "") or "")
            if cfg_token:
                ok, _reason = token_ok(cfg_token, supplied, False)
        except Exception:
            ok = False
    if not ok:
        return 401, {"ok": False, "state": "UNAUTHORIZED",
                     "code": "AUTH_INVALID_CREDENTIALS",
                     "detail": "Invalid credentials",
                     "qubit_counterpart": "GET /login (loopback session)"}
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_password_login", "dashboard:user", {"provider": provider})
    return _ok(ok=True, next="/", **_auth_bearer(access, refresh, provider,
                                                now))


def h_auth_logout(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/logout -> revoke the loopback session + credential check."""
    from .integration_status import logout_check
    state = _auth_store(cfg)
    revoked = bool(state.get("session") or state.get("refresh")
                   or state.get("codes") or state.get("pending"))
    state["session"] = {}
    state["refresh"] = {}
    state["pending"] = {}
    state["codes"] = {}
    _auth_flush(cfg, state)
    _audit("auth_logout", "dashboard:user", {"revoked": revoked})
    return _ok(ok=True, logged_out=True, revoked=revoked, next="/login",
               check=logout_check(cfg),
               detail="loopback session revoked; no server-side cookie")


# ---------------------------------------------------------------------------
# Route table: (method, regex, handler, disposition)
# disposition is implemented|gated; gated rows carry code+reason+counterpart.
# ---------------------------------------------------------------------------

Route = Tuple[str, str, str]

ROUTES: List[Dict[str, Any]] = []

def _r(method: str, pattern: str, handler: str, disp: str = "implemented",
       gate: Optional[Dict[str, str]] = None) -> None:
    ROUTES.append({"method": method, "pattern": pattern, "handler": handler,
                   "disposition": disp, "gate": gate or {}})


def _g(code: str, detail: str, counterpart: str,
       owner: str = "qubit-policy",
       rollback: str = "read-only; nothing to roll back") -> Dict[str, str]:
    return {"code": code, "detail": detail, "counterpart": counterpart,
            "owner": owner, "rollback": rollback}


_NO_PTY = _g("PTY_NO_SHELL",
             "Qubit forbids raw shell/PTY spawn; the xterm mirror cannot attach. "
             "Use Qubit Chat (streaming agent turns) or the TUI live board.",
             "overlay Qubit Chat tab (POST /api/qubit/chat) + CLI `qubit tui`")
_NO_WRITE = _g("WRITE_BROKERED",
               "Direct file writes are refused; writes run only as brokered, "
               "confirmed argv actions with audit receipts.",
               "CLI `qubit opencode` / projects + dashboard /ops + approvals")
_NO_SECRET = _g("SECRET_NEVER_SERVED",
                "Secret values never leave the vault; only references are listed.",
                "CLI `qubit auth status` (names only)")
_NO_SUPERVISION = _g("GATEWAY_NO_SUPERVISION",
                     "Qubit keeps a single event path; process supervision stays "
                     "with the operator.",
                     "operator systemd units + `qubit gateway` records")
_NO_REMOTE = _g("BLOCKED_REMOTE_SOURCE",
                "Remote fetch is refused; only local reviewed manifests install "
                "with explicit confirmation.",
                "CLI `qubit skills install <local-manifest>` + 7E validation")
_NO_OAUTH = _g("OAUTH_MANUAL_HANDOFF",
               "No live OAuth token dance is performed; enrollment is a manual "
               "handoff in the user's own browser.",
               "CLI `qubit auth login` (manual-handoff guided enrollment)")
_NO_UPDATE = _g("UPDATE_OPERATOR_OWNED",
                "In-place self-update from the dashboard is refused; upgrades are "
                "versioned, staged, and reversible.",
                "CLI `qubit product_*` / package + dashboard Setup page")
_NO_GIT_READ = _g("GIT_READ_SCOPED",
                  "Qubit git is a scoped read surface (status/log/diff/show); "
                  "this review verb has no dedicated backend here.",
                  "CLI `qubit git status|log|diff|show` + `qubit project`")
_NO_GH_AUTH = _g("GH_AUTH_OPERATOR",
                 "GitHub CLI auth status is never probed from the dashboard: "
                 "`gh auth status` shells out to a credentialed CLI and can "
                 "hang on a prompt. Qubit performs no network auth probe and "
                 "serves no token.",
                 "operator-run `gh auth status` + CLI `qubit auth status` "
                 "(names only)")
_NO_GIT_SHIP = _g("GIT_SHIP_GH_EXTERNAL",
                  "PR ship info needs the GitHub CLI (credentialed network); "
                  "Qubit never shells out to `gh` from the dashboard. Shipping "
                  "runs through the operator's own authenticated gh session.",
                  "operator `gh pr view` + CLI `qubit opencode` worktree flow")
_NO_CRON_FIRE = _g("CRON_FIRE_EXTERNAL",
                   "Managed-cron fire is an external NAS→agent webhook and is "
                   "not exposed through the dashboard; Qubit schedules run on "
                   "an estop-checked, audited tick.",
                   "CLI `qubit schedules run-due` + dashboard /ops "
                   "schedules_run_due",
                   owner="qubit-policy",
                   rollback="nothing fired; nothing to roll back")
_NO_PROFILE_PR = _g("GH_PR_EXTERNAL",
                    "Session PR association needs the GitHub CLI (credentialed "
                    "network); Qubit never shells out to `gh` from the "
                    "dashboard and serves no PR cache.",
                    "operator `gh pr list/view` + CLI `qubit sessions`",
                    owner="qubit-policy",
                    rollback="read-only; nothing to roll back")
_NO_GIT_WRITE = _g("GIT_WRITE_BROKERED",
                   "Qubit git is read-only; staging, commits, pushes, "
                   "worktrees, and branch switches run through the brokered "
                   "opencode worktree flow with confirmation.",
                   "CLI `qubit opencode` / `qubit project` + approvals")
_NO_MEDIA = _g("MEDIA_NOT_SERVED",
               "Gateway-local binary media data URLs are not served through "
               "the JSON compat API; Qubit file reads return bounded, redacted "
               "text only.",
               "CLI `qubit files read` + Qubit Chat attachment vision")
_NO_BINARY = _g("FILES_BINARY_STREAM",
                "Binary file download/stream responses are not served through "
                "the JSON compat dispatcher; managed files are read as bounded "
                "text or data URLs.",
                "GET /api/files/read + GET /api/fs/read-text")
_NO_AUDIO_RELAY = _g("AUDIO_RELAY_ONLY",
                     "Client-direct STT/TTS provider keys are never exported; "
                     "voice audio relays through the local Qubit pipeline.",
                     "classic /api/audio/status + Qubit Chat voice")
_NO_AUDIO_EXTERNAL = _g("PROVIDER_EXTERNAL",
                        "External voice catalogs (ElevenLabs) are not fetched; "
                        "TTS runs through the local provider chain.",
                        "local TTS providers (piper/edge/kokoro)")
_NO_AUDIO_ACTION = _g("AUDIO_ACTION_LOCAL",
                      "Audio synthesis/transcription runs only through the "
                      "local voice pipeline, never as a raw compat action.",
                      "Qubit Chat voice + CLI `qubit voice`")
_NO_AUDIO_LEASE = _g("AUDIO_LEASE_DESKTOP",
                     "TTS engine warm-up/release leases are desktop-only; the "
                     "local pipeline manages model residency itself.",
                     "Qubit voice pipeline (lazy model load)")
_NO_AUDIO_STREAM = _g("AUDIO_STREAM_LOCAL",
                      "Streaming PCM TTS is served by the local voice "
                      "pipeline, not as a compat WebSocket.",
                      "classic Qubit voice playback + Qubit Chat")
_NO_NETWORK_PROBE = _g("NETWORK_PROBE_BLOCKED",
                       "Live endpoint/credential probes open outbound sockets "
                       "from the dashboard; Qubit verifies providers through "
                       "the local governed gateway instead.",
                       "CLI `qubit auth status` + Qubit provider gateway "
                       "(audited, budgeted)")
_GATE_PROFILE_IMPORT = _g("PROFILE_IMPORT_REVIEW",
                          "Profile archives enter quarantine for review; a "
                          "reviewed archive imports via the Qubit principal "
                          "registry, never straight from the dashboard.",
                          "CLI `qubit import-state inspect` + `qubit brain "
                          "principal-add`")
_GATE_DESKTOP_OVERLAY = _g("DESKTOP_OVERLAY_ONLY",
                           "Desktop appearance overlays are a desktop-client "
                           "feature; the Qubit dashboard ships one theme and "
                           "serves no per-profile overlay.",
                           "Qubit theme (GET /api/dashboard/themes)")
_GATE_SESSIONS_RECEIPT = {
    **_g("SESSIONS_RECEIPT_STORE",
         "Qubit sessions are append-only audit receipts; owner back-fill is "
         "not offered. Memory tombstones/corrections own forgetting.",
         "CLI/voice memory_forget/correct (same stores)"),
    "http": 501,
}

_r("GET", r"/api/status", "h_status")
_r("GET", r"/api/health", "h_health")
_r("GET", r"/api/auth/me", "h_auth_me")
_r("GET", r"/api/events", "h_events")
_r("GET", r"/api/sessions", "h_sessions_list")
_r("GET", r"/api/sessions/stats", "h_sessions_stats")
_r("GET", r"/api/sessions/search", "h_sessions_list")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/export", "h_session_show")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/latest-descendant", "h_session_show")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/messages", "h_session_messages")
# Negative lookahead keeps the dedicated /api/sessions/shares collection from
# being captured as a session id by the generic show route below.
_r("GET", r"/api/sessions/(?!shares$)(?P<id>[^/]+)", "h_session_show")
_r("GET", r"/api/config", "h_config")
_r("GET", r"/api/config/schema", "h_config")
_r("GET", r"/api/config/defaults", "h_config")
_r("GET", r"/api/egress/status", "h_egress_status")
_r("GET", r"/api/model/info", "h_model_info")
_r("GET", r"/api/model/recommended-default", "h_model_recommended_default")
_r("GET", r"/api/model/options", "h_model_options")
_r("GET", r"/api/model/moa", "h_model_moa")
_r("PUT", r"/api/model/moa", "moa_put")
_r("GET", r"/api/model/auxiliary", "h_model_auxiliary")
_r("POST", r"/api/model/set", "h_model_set")
_r("GET", r"/api/profiles", "h_profiles")
_r("GET", r"/api/profiles/active", "h_profiles_active")
_r("POST", r"/api/profiles/active", "profiles_active_set")
_r("GET", r"/api/profiles/(?P<name>[^/]+)/setup-command", "h_profile_setup_command")
_r("GET", r"/api/profiles/(?P<name>[^/]+)/soul", "h_profile_soul")
_r("PUT", r"/api/profiles/(?P<name>[^/]+)/soul", "profile_soul_set")
_r("PUT", r"/api/profiles/(?P<name>[^/]+)/description", "profile_desc")
_r("POST", r"/api/profiles/(?P<name>[^/]+)/describe-auto", "profile_describe_auto")
_r("PUT", r"/api/profiles/(?P<name>[^/]+)/model", "profile_model")
_r("PATCH", r"/api/profiles/(?P<name>[^/]+)", "profile_rename")
_r("DELETE", r"/api/profiles/(?P<name>[^/]+)", "profile_delete")
_r("GET", r"/api/skills", "h_skills")
_r("GET", r"/api/skills/content", "h_skill_content")
_r("POST", r"/api/skills", "skill_create")
_r("PUT", r"/api/skills/content", "skill_update")
_r("PUT", r"/api/skills/toggle", "h_skills_toggle")
_r("GET", r"/api/tools/toolsets", "h_toolsets")
_r("GET", r"/api/cron/jobs", "h_cron_jobs")
_r("POST", r"/api/cron/jobs", "cron_create")
_r("PUT", r"/api/cron/jobs/(?P<id>[^/]+)", "cron_update")
_r("DELETE", r"/api/cron/jobs/(?P<id>[^/]+)", "cron_delete")
_r("GET", r"/api/cron/jobs/(?P<id>[^/]+)/runs", "h_cron_job_runs")
_r("GET", r"/api/cron/jobs/(?P<id>[^/]+)", "h_cron_job_show")
_r("POST", r"/api/cron/jobs/(?P<id>[^/]+)/pause", "cron_pause")
_r("POST", r"/api/cron/jobs/(?P<id>[^/]+)/resume", "cron_resume")
_r("POST", r"/api/cron/jobs/(?P<id>[^/]+)/trigger", "cron_trigger")
_r("GET", r"/api/logs", "h_logs")
_r("GET", r"/api/system/stats", "h_system_stats")
_r("GET", r"/api/mcp", "h_mcp")
_r("GET", r"/api/mcp/servers", "h_mcp_servers")
_r("POST", r"/api/mcp/servers", "mcp_create")
_r("DELETE", r"/api/mcp/servers/(?P<id>[^/]+)", "mcp_delete")
_r("GET", r"/api/mcp/catalog", "h_mcp_catalog_live")
_r("POST", r"/api/pub", "h_pub")
_r("WEBSOCKET", r"/api/pub", "h_pub")
_r("GET", r"/api/pairing", "h_pairing_list")
_r("POST", r"/api/pairing", "pairing_create")
_r("POST", r"/api/pairing/approve", "pairing_approve")
_r("POST", r"/api/pairing/revoke", "pairing_revoke")
_r("POST", r"/api/pairing/clear-pending", "pairing_clear_pending")
_r("GET", r"/api/webhooks", "h_webhooks_list")
_r("POST", r"/api/webhooks", "webhooks_add")
_r("POST", r"/api/webhooks/enable", "webhooks_enable")
_r("PUT", r"/api/webhooks/(?P<id>[^/]+)/enabled", "webhooks_enabled")
_r("DELETE", r"/api/webhooks/(?P<id>[^/]+)", "webhooks_delete")
_r("GET", r"/api/webhooks/(?P<id>[^/]+)", "webhooks_show")
_r("POST", r"/api/webhooks/(?P<id>[^/]+)", "webhooks_mutate")
_r("GET", r"/api/messaging/platforms", "h_platforms")
_r("PUT", r"/api/messaging/platforms/(?P<id>[^/]+)", "msg_update")
_r("GET", r"/api/memory", "h_memory")
_r("GET", r"/api/memory/provider", "h_memory")
_r("GET", r"/api/analytics", "h_analytics")
_r("GET", r"/api/analytics/usage", "h_analytics")
_r("GET", r"/api/analytics/models", "h_analytics_models")
_r("GET", r"/api/files", "h_files")
_r("GET", r"/api/files/read", "h_files_read")
_r("DELETE", r"/api/files", "files_delete")
_r("GET", r"/api/gateway", "h_gateway")
_r("POST", r"/api/gateway/drain", "h_gateway_drain")
_r("GET", r"/api/curator", "h_curator")
_r("GET", r"/api/env", "h_env")
_r("GET", r"/api/ops/hooks", "h_hooks")
_r("POST", r"/api/ops/hooks", "h_hooks_create")
_r("GET", r"/api/ops/checkpoints", "h_ckpt")
_r("GET", r"/api/ops", "h_ops_catalog")
_r("GET", r"/api/ops/approvals", "h_approvals_list")
_r("POST", r"/api/ops/run", "h_ops_run")
_r("POST", r"/api/ops/confirm", "h_ops_confirm")
_r("POST", r"/api/ops/cancel", "h_ops_cancel")
_r("GET", r"/api/ops/(?P<name>[a-z-]+)", "h_ops")
_r("POST", r"/api/ops/(?P<name>[a-z-]+)", "h_ops")
_r("DELETE", r"/api/ops/hooks", "hooks_delete")
_r("GET", r"/api/plugins/kanban/events", "h_kanban_events")
# Vendored dashboard plugin fleet (hermes-achievements + kanban). Real
# backends live in voicebridge/hermes_plugins.py and read the same local
# session history / kanban DB the Qubit-owned Hermes runtime uses. Read routes
# precede the GATED kanban write wildcard below so they are never shadowed.
_r("GET", r"/api/plugins/hermes-achievements/achievements",
   "h_plugin_achievements")
_r("GET", r"/api/plugins/hermes-achievements/scan-status",
   "h_plugin_achievements_scan_status")
_r("GET", r"/api/plugins/hermes-achievements/recent-unlocks",
   "h_plugin_achievements_recent_unlocks")
_r("GET", r"/api/plugins/hermes-achievements/sessions/(?P<session_id>[^/]+)/badges",
   "h_plugin_achievements_session_badges")
_r("POST", r"/api/plugins/hermes-achievements/rescan",
   "h_plugin_achievements_rescan")
_r("POST", r"/api/plugins/hermes-achievements/reset-state",
   "h_plugin_achievements_reset_state")
_r("GET", r"/api/plugins/kanban/board", "h_plugin_kanban_board")
_r("GET", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)", "h_plugin_kanban_task")
_r("GET", r"/api/plugins/kanban/stats", "h_plugin_kanban_stats")
_r("GET", r"/api/plugins/kanban/assignees", "h_plugin_kanban_assignees")
_r("GET", r"/api/plugins/kanban/boards", "h_plugin_kanban_boards")
_r("GET", r"/api/plugins/kanban/config", "h_plugin_kanban_config")
_r("GET", r"/api/plugins/kanban/projects", "h_plugin_kanban_projects")
_r("GET", r"/api/plugins/kanban/workers/active", "h_plugin_kanban_workers")
_r("GET", r"/api/plugins/kanban/diagnostics", "h_plugin_kanban_diagnostics")
_r("GET", r"/api/plugins/kanban/model-options", "h_plugin_kanban_model_options")
_r("GET", r"/api/plugins/kanban/home-channels", "h_plugin_kanban_home_channels")
# Discovery for the vendored plugin fleet. The existing
# /api/dashboard/plugins response stays fixed to the Qubit overlay set; this
# additive view reports the vendored manifests without changing that contract.
_r("GET", r"/api/dashboard/plugins/fleet", "h_plugin_fleet")
_r("POST", r"/api/qubit/chat", "h_chat_turn")
_r("GET", r"/api/dashboard/plugins", "h_plugins")
_r("GET", r"/api/dashboard/themes", "h_themes")
_r("PUT", r"/api/dashboard/theme", "h_theme_set")

# Packet 8U read surfaces: scoped Git / Files / Audio (audit §1 absent reads)
_r("GET", r"/api/git/status", "h_git_status")
_r("GET", r"/api/git/log", "h_git_log")
_r("GET", r"/api/git/diff", "h_git_diff")
_r("GET", r"/api/git/show", "h_git_show")
_r("GET", r"/api/git/review/diff", "h_git_file_diff")
_r("GET", r"/api/git/file-diff", "h_git_file_diff")
_r("GET", r"/api/git/worktrees", "h_git_worktrees")
_r("GET", r"/api/git/branches", "h_git_branches")
_r("GET", r"/api/git/base-branches", "h_git_base_branches")
_r("GET", r"/api/git/review/list", "h_git_review_list")
_r("GET", r"/api/git/review/commit-context", "h_git_review_commit_context")
_r("GET", r"/api/git/review/rev-parse", "h_git_review_rev_parse")
_r("GET", r"/api/fs/list", "h_fs_list")
_r("GET", r"/api/fs/tree", "h_fs_tree")
_r("GET", r"/api/fs/stat", "h_fs_stat")
_r("GET", r"/api/fs/read", "h_fs_read_text")
_r("GET", r"/api/fs/read-text", "h_fs_read_text")
_r("GET", r"/api/fs/read-data-url", "h_fs_read_data_url")
_r("GET", r"/api/fs/git-root", "h_fs_git_root")
_r("GET", r"/api/fs/default-cwd", "h_fs_default_cwd")
_r("GET", r"/api/audio/status", "h_audio_status")
_r("GET", r"/api/audio/list", "h_audio_devices")
_r("GET", r"/api/audio/devices", "h_audio_devices")
_r("GET", r"/api/audio/voice-config", "h_audio_voice_config")
_r("POST", r"/api/audio/transcribe", "h_audio_transcribe")
_r("POST", r"/api/audio/speak", "h_audio_speak")

# Packet 8U closure: local-models reads, custom endpoints, profile reads,
# tools/learning reads, auth providers (audit §1A absent reads).
_r("GET", r"/api/local-models/status", "h_local_models_status")
_r("GET", r"/api/local-models/hardware", "h_local_models_hardware")
_r("GET", r"/api/local-models/catalog", "h_local_models_catalog")
_r("GET", r"/api/local-models/jobs/(?P<id>[^/]+)", "h_local_models_job_show")
_r("GET", r"/api/local-models/jobs", "h_local_models_jobs")
_r("POST", r"/api/local-models/runtime/install", "h_local_models_runtime_install")
_r("POST", r"/api/local-models/download", "h_local_models_download")
_r("DELETE", r"/api/local-models/models/(?P<model_id>[^/]+)",
   "h_local_models_delete")
_r("POST", r"/api/local-models/quickstart", "h_local_models_quickstart")
_r("POST", r"/api/local-models/server", "h_local_models_server")
_r("POST", r"/api/local-models/eject", "h_local_models_eject")
_r("POST", r"/api/local-models/activate", "h_local_models_activate")
_r("POST", r"/api/local-models/sideload", "h_local_models_sideload")
_r("GET", r"/api/local-models/search/files", "h_local_models_search_files")
_r("GET", r"/api/local-models/search", "h_local_models_search")
_r("POST", r"/api/local-models/download-browsed",
   "h_local_models_download_browsed")
_r("GET", r"/api/providers/custom-endpoints", "h_custom_endpoints")
_r("GET", r"/api/profiles/sessions/sidebar", "h_profiles_sessions_sidebar")
_r("GET", r"/api/profiles/sessions", "h_profiles_sessions")
_r("GET", r"/api/profiles/projects/tree", "h_profiles_projects_tree")
_r("POST", r"/api/profiles", "h_profile_create")
_r("GET", r"/api/tools/terminal/backends", "h_terminal_backends")
_r("GET", r"/api/tools/computer-use/status", "h_computer_use_status")
_r("GET", r"/api/learning/graph", "h_learning_graph")
_r("GET", r"/api/learning/node", "h_learning_node")
_r("PUT", r"/api/learning/node", "h_learning_node_update")
_r("DELETE", r"/api/learning/node", "h_learning_node_delete")
_r("GET", r"/api/auth/providers", "h_auth_providers")
# Qubit-owned dashboard auth flow (loopback identity + native handshake).
_r("GET", r"/login", "h_login_page")
_r("GET", r"/auth/login", "h_auth_login")
_r("GET", r"/auth/native/authorize", "h_auth_native_authorize")
_r("GET", r"/auth/callback", "h_auth_callback")
_r("POST", r"/auth/password-login", "h_auth_password_login")
_r("POST", r"/auth/logout", "h_auth_logout")
_r("POST", r"/auth/native/token", "h_auth_native_token")
_r("POST", r"/auth/native/refresh", "h_auth_native_refresh")

# OpenClaw delta Wave 3 (D3/D4/D20/D21/D24): file transfer, active recall,
# document extraction, question prompts, and session sharing. Specific paths
# precede parameterized rows so re.fullmatch resolves deterministically.
_r("GET", r"/api/transfers/status", "h_transfers_status")
_r("GET", r"/api/transfers/list", "h_transfers_list")
_r("POST", r"/api/transfers/fetch", "h_transfers_fetch")
_r("POST", r"/api/transfers/plan", "h_transfers_plan")
_r("GET", r"/api/transfers/resume", "h_transfers_resume")
_r("GET", r"/api/recall/status", "h_recall_status")
_r("GET", r"/api/recall/policy", "h_recall_policy_get")
_r("POST", r"/api/recall/policy", "h_recall_policy_set")
_r("GET", r"/api/recall/resolve", "h_recall_resolve")
_r("GET", r"/api/recall", "h_recall_query")
_r("POST", r"/api/recall/escalate", "h_recall_escalate")
_r("GET", r"/api/documents/status", "h_documents_status")
_r("GET", r"/api/documents/kinds", "h_documents_kinds")
_r("POST", r"/api/documents/extract", "h_documents_extract")
_r("GET", r"/api/ask/status", "h_ask_status")
_r("GET", r"/api/ask/pending", "h_ask_pending")
_r("POST", r"/api/ask/answer", "h_ask_answer")
_r("POST", r"/api/ask", "h_ask_create")
_r("GET", r"/api/shares/status", "h_session_share_status")
_r("GET", r"/api/sessions/shares", "h_session_shares_list")
_r("POST", r"/api/sessions/shares/purge", "h_session_shares_purge")
_r("DELETE", r"/api/sessions/shares/(?P<share_id>[^/]+)",
   "h_session_share_revoke")
_r("POST", r"/api/sessions/(?P<session_id>[^/]+)/share",
   "h_session_share_publish")
# Public, locally-served share view: authenticated by the SHARE token in the
# query (constant-time checked in the module), NOT the dashboard token.
_r("GET", r"/share/(?P<share_id>[^/]+)", "h_share_resolve")

# Agent 5-10 capabilities served at the Qubit-owned /api/qubit/* front door.
# These are NEVER proxied to the fork (hermes_asgi.QUBIT_OWNED_PREFIXES) and
# every handler calls the SAME module the CLI (`qubit gateway`, `qubit
# runtime`, `qubit egress`, `qubit policy`, `qubit usage`, `qubit receipts`,
# `qubit logbook`, `qubit browser`, `qubit local-models`) and voice surfaces
# call: same stores, same redaction, same read-only authority.
_r("GET", r"/api/qubit/gateway/discovery", "h_qubit_gateway_discovery")
_r("GET", r"/api/qubit/runtime/discovery", "h_qubit_runtime_discovery")
_r("GET", r"/api/qubit/runtime/marketplace", "h_qubit_runtime_marketplace")
_r("GET", r"/api/qubit/runtime/history", "h_qubit_runtime_history")
_r("GET", r"/api/qubit/runtime/update-report", "h_qubit_runtime_update_report")
_r("GET", r"/api/qubit/runtime/status", "h_qubit_runtime_status")
_r("GET", r"/api/qubit/runtime/images", "h_qubit_runtime_images")
_r("GET", r"/api/qubit/runtime/doctor", "h_qubit_runtime_doctor")
_r("GET", r"/api/qubit/egress/status", "h_qubit_egress_status")
_r("GET", r"/api/qubit/egress/providers", "h_qubit_egress_providers")
_r("GET", r"/api/qubit/egress/check", "h_qubit_egress_check")
_r("GET", r"/api/qubit/policy/conformance", "h_qubit_policy_conformance")
_r("GET", r"/api/qubit/policy/attest", "h_qubit_policy_attest")
_r("GET", r"/api/qubit/hooks/doctor", "h_qubit_hooks_doctor")
_r("GET", r"/api/qubit/usage", "h_qubit_usage")
_r("GET", r"/api/qubit/receipts", "h_qubit_receipts")
_r("GET", r"/api/qubit/logbook", "h_qubit_logbook")
_r("GET", r"/api/qubit/browser/profiles", "h_qubit_browser_profiles")
_r("GET", r"/api/qubit/local-models/fallback", "h_qubit_local_models_fallback")

# Wave 2 conversions: previously-blanket mutating refusals now run the real
# Qubit permission/confirmation/audit operation (same module/store as CLI and
# voice). These precede the GATED table so the implemented handler matches.
_r("POST", r"/api/gateway/start", "h_gateway_start")
_r("POST", r"/api/gateway/stop", "h_gateway_stop")
_r("POST", r"/api/providers/custom-endpoints", "h_custom_endpoints_write")
_r("POST", r"/api/providers/custom-endpoints/(?P<id>[^/]+)/activate",
   "h_custom_endpoints_activate")
_r("DELETE", r"/api/providers/custom-endpoints/(?P<id>[^/]+)",
   "h_custom_endpoints_delete")
_r("PUT", r"/api/tools/terminal/backend", "h_terminal_backend_set")
_r("POST", r"/api/tools/computer-use/permissions/grant", "h_computer_use_grant")
_r("POST", r"/api/profiles/(?P<name>[^/]+)/export", "h_profile_export")

# Wave 3: skills hub served from the Qubit-reviewed LOCAL catalog (remote
# fetches refused). Specific routes precede the GATED hub wildcard so unknown
# subpaths still fall through to the typed remote refusal.
_r("GET", r"/api/skills/hub/official", "h_skills_hub_official")
_r("GET", r"/api/skills/hub/sources", "h_skills_hub_sources")
_r("GET", r"/api/skills/hub/search", "h_skills_hub_search")
_r("GET", r"/api/skills/hub/preview", "h_skills_hub_preview")
_r("GET", r"/api/skills/hub/scan", "h_skills_hub_scan")
_r("POST", r"/api/skills/hub/install", "h_skills_hub_install")
_r("POST", r"/api/skills/hub/uninstall", "h_skills_hub_uninstall")
_r("POST", r"/api/skills/hub/update", "h_skills_hub_update")

# Wave 3 real reads that must precede the parameterized GATED wildcards:
# provider config (the memory-provider wildcard would otherwise shadow it) and
# the toolset config/model matrix.
_r("GET", r"/api/memory/providers/(?P<id>[^/]+)/config", "mem_config")
_r("GET", r"/api/tools/toolsets/(?P<id>[^/]+)/config", "toolset_config")
_r("GET", r"/api/tools/toolsets/(?P<id>[^/]+)/models", "toolset_models")

# Wave 4 real brokered writes (staged -> confirm -> apply): confined file
# patches, brokered git-write authorizations, and blueprint instantiation.
_r("POST", r"/api/fs/write-text", "h_fs_write_text")
_r("POST", r"/api/files/upload", "h_files_upload")
_r("POST", r"/api/files/upload-stream", "h_files_upload")
_r("POST", r"/api/files/mkdir", "h_files_mkdir")
_r("POST", r"/api/git/review/stage", "h_git_stage")
_r("POST", r"/api/git/review/unstage", "h_git_unstage")
_r("POST", r"/api/git/review/revert", "h_git_revert")
_r("POST", r"/api/git/review/commit", "h_git_commit")
_r("POST", r"/api/git/worktree/add", "h_git_worktree_add")
_r("POST", r"/api/git/worktree/remove", "h_git_worktree_remove")
_r("POST", r"/api/git/branch/switch", "h_git_branch_switch")
_r("POST", r"/api/cron/blueprints/instantiate", "h_blueprint_instantiate")

GATED: List[Dict[str, Any]] = [
    ("GET", r"/api/pty", _NO_PTY), ("POST", r"/api/pty", _NO_PTY),
    ("GET", r"/api/ws", _g("WS_SINGLE_PATH",
                           "The Hermes tui_gateway WS dialect is not served; Qubit keeps "
                           "one event path (SSE feed + ops). Chat uses Qubit Chat.",
                           "GET /api/events (SSE) + POST /api/qubit/chat")),
    ("POST", r"/api/auth/ws-ticket", _g("WS_SINGLE_PATH",
                           "No WS tickets are minted; Qubit serves no WS gateway dialect. "
                           "Loopback clients use the session token directly.",
                           "GET /api/events (SSE) + POST /api/qubit/chat")),
    ("GET", r"/api/console", _NO_PTY),
    ("GET", r"/api/config/raw", _NO_SECRET),
    ("PUT", r"/api/config/raw", _NO_SECRET),
    ("GET", r"/api/env/reveal", _NO_SECRET),
    ("POST", r"/api/env/reveal", _NO_SECRET),
    ("GET", r"/api/credentials/pool.*", _NO_SECRET),
    ("POST", r"/api/credentials/pool.*", _NO_SECRET),
    ("PUT", r"/api/credentials/pool.*", _NO_SECRET),
    ("DELETE", r"/api/credentials/pool.*", _NO_SECRET),
    ("POST", r"/api/chat/image-upload", _g("CHAT_IMAGE_OVERLAY",
                                           "Built-in xterm chat image paste is superseded by "
                                           "Qubit Chat attachments (vision_describe backend).",
                                           "overlay Qubit Chat tab + `qubit vision describe`")),
    ("POST", r"/api/gateway/restart", _NO_SUPERVISION),
    ("GET", r"/api/hermes/update.*", _NO_UPDATE),
    ("POST", r"/api/hermes/update.*", _NO_UPDATE),
    ("GET", r"/api/portal", _NO_UPDATE),
    ("GET", r"/api/ssh/ownership", _g("SSH_OWNERSHIP_OPERATOR",
        "Qubit owns the host shell surface; no remote SSH ownership nonce is "
        "minted or served to the dashboard. Host control stays with the operator.",
        "CLI `qubit setup health` + `qubit doctor` (local, operator-run)")),
    ("GET", r"/api/providers/oauth.*", _NO_OAUTH),
    ("POST", r"/api/providers/oauth.*", _NO_OAUTH),
    ("GET", r"/api/mcp/oauth/flows", _NO_OAUTH),
    ("GET", r"/api/mcp/oauth/flows/[^/]+", _NO_OAUTH),
    ("PUT", r"/api/curator/paused", _g("CURATOR_ON_DEMAND",
        "No background curator daemon runs; curation is on-demand and every "
        "promotion is reviewed. There is nothing to pause.",
        "lessons_list/promote + CLI `qubit lessons` (same LearningLedger)")),
    ("POST", r"/api/curator/run", _g("CURATOR_ON_DEMAND",
        "No background curator daemon runs; curation is on-demand and every "
        "promotion is reviewed. Run an explicit summary pass instead.",
        "GET /api/curator + CLI `qubit lessons` (same LearningLedger)")),
    ("GET", r"/api/ops/backup/download", _g("BACKUP_PASSPHRASE",
        "Backup bundles are passphrase-encrypted on creation and never stored "
        "server-side; there is no archive to download. Create one with a "
        "passphrase resupply at confirmation time.",
        "dashboard /ops backup_create + CLI backup (passphrase resupply)")),
    ("GET", r"/api/mcp/servers/[^/]+/auth", _NO_OAUTH),
    ("POST", r"/api/mcp/servers/[^/]+/auth", _NO_OAUTH),
    ("POST", r"/api/skills/hub/.*", _NO_REMOTE),
    ("GET", r"/api/skills/hub/.*", _NO_REMOTE),
    ("POST", r"/api/mcp/catalog/install", _NO_REMOTE),
    ("POST", r"/api/dashboard/agent-plugins/.*", _NO_REMOTE),
    ("GET", r"/api/dashboard/plugin-providers", _g("PLUGIN_PROVIDERS_QUBIT",
        "Plugin providers resolve through the Qubit skill manifest registry.",
        "GET /api/skills + CLI `qubit skills list`")),
    ("GET", r"/api/cron/blueprints", "blueprints"),
    ("GET", r"/api/cron/delivery-targets", "delivery"),
    ("POST", r"/api/sessions/import", "sess_import"),
    ("POST", r"/api/sessions/prune", "sess_prune"),
    ("POST", r"/api/sessions/bulk-delete", "sess_bulk"),
    ("DELETE", r"/api/sessions/(?P<id>[^/]+)", "h_session_delete"),
    ("PATCH", r"/api/sessions/(?P<id>[^/]+)", "h_session_patch"),
    ("POST", r"/api/sessions/(?P<id>[^/]+)/resume", "h_session_resume"),
    ("GET", r"/api/sessions/empty.*", "sess_empty"),
    ("GET", r"/api/memory/reset", "mem_reset"),
    ("POST", r"/api/memory/reset", "mem_reset"),
    # Provider OAuth status is a credential surface (the fork polls a live
    # OAuth flow). Qubit performs no live token dance, so it is a typed
    # manual-handoff gate, not the local memory-provider stub. Specific route
    # must precede the memory-providers wildcard so it is not captured by
    # ``mem_prov``.
    ("GET", r"/api/memory/providers/[^/]+/oauth/status", _NO_OAUTH),
    ("GET", r"/api/memory/providers/.*", "mem_prov"),
    ("GET", r"/api/messaging/.*/onboarding.*", _g("ONBOARDING_GUIDED",
        "Channel onboarding runs as guided manual enrollment, not an automatic token flow.",
        "CLI `qubit auth login` + msg account ops")),
    ("DELETE", r"/api/messaging/.*/onboarding.*", _g("ONBOARDING_GUIDED",
        "Channel onboarding runs as guided manual enrollment, not an automatic token flow.",
        "CLI `qubit auth login` + msg account ops")),
    ("POST", r"/api/messaging/.*/onboarding.*", _g("ONBOARDING_GUIDED",
        "Channel onboarding runs as guided manual enrollment, not an automatic token flow.",
        "CLI `qubit auth login` + msg account ops")),
    ("POST", r"/api/messaging/platforms/(?P<id>[^/]+)/test", "h_msg_test_live"),
    ("POST", r"/api/mcp/servers/(?P<name>[^/]+)/test", "h_mcp_test_local"),
    ("POST", r"/api/mcp/servers/[^/]+/enabled", "mcp_enabled"),
    ("PUT", r"/api/mcp/servers/[^/]+/enabled", "mcp_enabled"),
    ("PUT", r"/api/mcp/servers", "mcp_create"),
    ("PUT", r"/api/tools/toolsets/.*", "toolset_write"),
    ("POST", r"/api/tools/toolsets/.*/post-setup", "toolset_write"),
    ("PUT", r"/api/config", "config_save"),
    ("POST", r"/api/dashboard/agent-plugins/install", "agent_install"),
    ("POST", r"/api/dashboard/agent-plugins/(?P<id>[^/]+)/enable", "agent_enable"),
    ("POST", r"/api/dashboard/agent-plugins/(?P<id>[^/]+)/disable", "agent_disable"),
    ("POST", r"/api/dashboard/agent-plugins/(?P<id>[^/]+)/update", "agent_update"),
    ("DELETE", r"/api/dashboard/agent-plugins/(?P<id>.+)", "agent_remove"),
    ("PUT", r"/api/dashboard/plugin-providers", "toolset_write"),
    ("GET", r"/api/dashboard/plugins/hub", "plugins_hub"),
    ("DELETE", r"/api/providers/oauth/(?P<id>[^/]+)", "oauth_delete"),
    ("DELETE", r"/api/providers/oauth/sessions/(?P<id>[^/]+)", "oauth_delete"),
    ("PUT", r"/api/env", "env_write"),
    ("DELETE", r"/api/env", "env_write"),
    ("PUT", r"/api/memory/provider", "mem_provider_set"),
    ("PUT", r"/api/memory/providers/(?P<id>[^/]+)/config", "mem_config_write"),
    ("POST", r"/api/memory/providers/(?P<id>[^/]+)/setup", "mem_config_write"),
    ("GET", r"/api/dashboard/plugins/rescan", "plugins_rescan"),
    ("POST", r"/api/dashboard/plugins/(?P<id>.+)/visibility", "h_plugin_visibility_set"),
    ("POST", r"/api/ops/debug-share", "debug_share"),
    ("POST", r"/api/ops/import-upload", "import_upload"),
    ("POST", r"/api/ops/checkpoints/prune", "ckpt_prune"),
    ("GET", r"/api/ops/checkpoints.*", "ckpt"),
    ("GET", r"/api/actions/.*", "actions_read"),
    ("GET", r"/api/dashboard/font", "font"),
    ("PUT", r"/api/dashboard/font", "font_set"),
    # --- Packet 8U: Git writes + credentialed gh surfaces stay typed.
    # Local review writes (stage/unstage/revert/commit/worktree/branch-switch)
    # were converted to real brokered grants (Wave 4) and precede this table;
    # network/gh verbs stay typed here.
    ("GET", r"/api/git/gh-auth", _NO_GH_AUTH),
    ("GET", r"/api/git/review/ship-info", _NO_GIT_SHIP),
    ("POST", r"/api/git/review/pr-list", _NO_GIT_WRITE),
    ("POST", r"/api/git/review/push", _NO_GIT_WRITE),
    ("POST", r"/api/git/review/create-pr", _NO_GIT_WRITE),
    # --- Packet 8U: Files/FS binary + writes stay typed ---
    ("GET", r"/api/media", _NO_MEDIA),
    ("GET", r"/api/files/download", _NO_BINARY),
    ("GET", r"/api/files/stream", _NO_BINARY),
    ("HEAD", r"/api/files/stream", _NO_BINARY),
    ("GET", r"/api/fs/download", _NO_BINARY),
    # --- Packet 8U: external catalog / desktop lease / WS stay typed ---
    ("GET", r"/api/audio/elevenlabs/voices", _NO_AUDIO_EXTERNAL),
    ("POST", r"/api/audio/tts-lease", _NO_AUDIO_LEASE),
    ("GET", r"/api/audio/speak-stream", _NO_AUDIO_STREAM),
    ("POST", r"/api/audio/speak-stream", _NO_AUDIO_STREAM),
    # --- Packet 8U closure: provider/profile/tool/learning writes + the
    # remaining wrong-verb gates (audit §1B). Specific paths precede any
    # parameterized row so re.fullmatch resolves deterministically.
    # custom-endpoints POST/activate/DELETE, terminal/backend, computer-use
    # grant, and profile export were converted to real Qubit
    # confirmation/audit operations (Wave 2) and now precede the GATED table.
    ("POST", r"/api/providers/custom-endpoints/validate", _NO_NETWORK_PROBE),
    ("POST", r"/api/providers/validate", _NO_NETWORK_PROBE),
    ("POST", r"/api/profiles/import", _GATE_PROFILE_IMPORT),
    ("POST", r"/api/profiles/[^/]+/open-terminal", _NO_PTY),
    ("GET", r"/api/profiles/[^/]+/desktop-overlay", _GATE_DESKTOP_OVERLAY),
    ("POST", r"/api/sessions/owner-backfill", _GATE_SESSIONS_RECEIPT),
    ("POST", r"/api/cron/fire", _NO_CRON_FIRE),
    ("POST", r"/api/profiles/sessions/pull-requests", _NO_PROFILE_PR),
    ("POST", r"/api/memory/providers/[^/]+/oauth/start", _NO_OAUTH),
    ("DELETE", r"/api/mcp/oauth/flows/[^/]+", _NO_OAUTH),
    ("GET", r"/api/mcp/oauth/callback.*", _NO_OAUTH),
    # Vendored kanban dashboard plugin: reads are served by
    # voicebridge/hermes_plugins.py; task mutations stay on the shared Qubit
    # confirmation/broker path (`qubit kanban` + approvals) rather than the
    # plugin's direct server-side writes.
    ("POST", r"/api/plugins/kanban/.*", _g("KANBAN_WRITE_BROKERED",
        "Kanban task mutations run through the Qubit confirmation/broker path "
        "(`qubit kanban` + approvals), not a direct dashboard write.",
        "CLI `qubit kanban` + pending-approvals banner")),
    ("PATCH", r"/api/plugins/kanban/.*", _g("KANBAN_WRITE_BROKERED",
        "Kanban task mutations run through the Qubit confirmation/broker path "
        "(`qubit kanban` + approvals), not a direct dashboard write.",
        "CLI `qubit kanban` + pending-approvals banner")),
    ("DELETE", r"/api/plugins/kanban/.*", _g("KANBAN_WRITE_BROKERED",
        "Kanban task mutations run through the Qubit confirmation/broker path "
        "(`qubit kanban` + approvals), not a direct dashboard write.",
        "CLI `qubit kanban` + pending-approvals banner")),
]

for _m, _p, _ginfo in GATED:
    if isinstance(_ginfo, dict):
        _r(_m, _p, "__gate__", "gated", _ginfo)
    else:
        _r(_m, _p, _ginfo, "implemented")


def h_blueprints(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    try:
        jobs = S.list_jobs(cfg)
    except Exception as exc:
        return _fail(exc)
    names = sorted({str(j.get("name", "")) for j in jobs.get("jobs", [])} if isinstance(jobs, dict) else [])
    return _ok(blueprints=[{"name": n, "kind": "schedule-template",
                            "instantiate": "POST /api/cron/blueprints/instantiate (staged, confirmed)"}
                           for n in names])


def h_delivery_targets(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import messaging as M
    try:
        res = M.account_list(cfg)
    except Exception as exc:
        return _fail(exc)
    return _ok(delivery_targets=res)


def h_sess_empty(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Real read: receipts whose detail is empty (no recorded activity)."""
    from .auth_status import recent_sessions
    res = recent_sessions(cfg, 100)
    empty = [r for r in res.get("sessions", []) if not (r.get("detail") or "").strip()]
    return _ok(count=len(empty),
               sessions=[{"id": r.get("receipt_id") or r.get("action_id"),
                          "created_at": r.get("created_at")} for r in empty])


def h_sessions_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str], kind: str) -> Tuple[int, Dict[str, Any]]:
    return _gate("SESSIONS_RECEIPT_STORE",
                 "Qubit sessions are append-only audit receipts; bulk delete, prune, "
                 "import, rename, and empty-store sweeps are not offered on receipts. "
                 "Memory tombstones/corrections own forgetting.",
                 "memory_forget/correct + CLI/voice (same stores)", 501)


def h_mem_reset(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    if not b.get("confirm"):
        return _staged("memory reset is destructive: re-POST with "
                       "{\"confirm\": true} plus a fresh approval",
                       "", {"code": "CONFIRM_REQUIRED"})
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "brain_profile_sweep",
                     {"confirm": True}, actor="dashboard:user", session="dashboard")
    return (200 if out.get("state") == "executed" else 400), out


def h_mem_provider(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _ok(provider="qubit-local", tiers=["core", "working", "recall", "archive"],
               detail="local-only SQLite/FTS5 MemoryStore; cloud sync refused by policy")


def h_msg_test(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _ok(test="deferred",
               detail="credential-gated last-mile probe; runs only with a vault "
                      "credential reference present (MSG_NO_CREDENTIAL otherwise)")


def h_msg_test_live(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/messaging/platforms/{platform_id}/test -> real link state.

    Runs the same ``messaging.link`` credential/transport probe the CLI and
    dashboard platform list use: it reports LINKED / DEGRADED /
    NO_CREDENTIAL / UNAVAILABLE from the vault reference and transport
    availability. It never sends a message and never opens a provider socket.
    """
    from . import messaging as M
    platform_id = str(p.get("id", p.get("platform_id", "")) or "")
    if not platform_id:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "platform_id required",
                     "qubit_counterpart": "CLI `qubit msg accounts`"}
    try:
        linked = M.link(cfg, platform_id)
    except Exception as exc:
        return _fail(exc)
    state = str(linked.get("state", "UNAVAILABLE") or "UNAVAILABLE")
    reachable = state in ("LINKED", "DEGRADED")
    return _ok(ok=True, platform=platform_id, state=state.lower(),
               reachable=reachable, link=linked,
               error_code=linked.get("code"),
               detail="credential/transport link state; no message sent")


def h_mcp_test(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    # McpTestResult wire shape; mounts never happen so tools stay empty.
    return _ok(ok=False, tools=[],
               error="MCP_NO_MOUNT: Qubit inventories MCP servers but never "
                     "mounts them")


def h_mcp_enabled(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("MCP_NO_MOUNT", "MCP servers stay disabled-until-enabled inventory; "
                 "enabling a mount is refused.",
                 "skills_enable for Qubit-native skills", 501)


def h_toolset_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT/POST toolset config -> real confirm-gated capability grant.

    A toolset's env/model/provider change is a capability-scoped permission
    write. It records a real grant (``toolset.<name>``) in the same
    ``PermissionsStore`` the CLI/voice use, with backup + audit, and returns
    409 until confirmed. It never writes a secret value.
    """
    from .permissions import permissions_store
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "toolset name required"}
    if not b.get("confirm"):
        return _staged(
            f"toolset {name!r} config change requires explicit confirmation: "
            "re-POST with {\"confirm\": true}", "")
    scope = str(b.get("scope", "toolset") or "toolset")
    try:
        grant = permissions_store(cfg).grant(
            f"toolset.{name}", requested_by="dashboard:user", confirmed=True,
            scopes=(scope,),
            note="dashboard toolset config " + ", ".join(
                sorted(k for k in b if k not in ("confirm", "name")))[:120])
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, name=name, grant_id=grant.id, scope=scope,
               qubit_counterpart="CLI `qubit permissions` + dashboard "
                                 "Settings (classic)")


def h_profile_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "brain_profile_set", {k: v for k, v in b.items()
                                                if isinstance(v, (str, int, float, bool))},
                     actor="dashboard:user", session="dashboard")
    ok = out.get("state") == "executed"
    if out.get("confirmation_id") and not ok:
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    return (200 if ok else 400), {"ok": ok, "result": _res(out),
                                  "state": out.get("state")}


def h_plugins_rescan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    manifests = _overlay_manifests()
    return _ok(rescanned=True, count=len(manifests), plugins=[m["name"] for m in manifests])


def h_plugin_visibility(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/dashboard/plugins/{name}/visibility -> real persisted toggle.

    The per-user hidden set is persisted in ``<state_dir>/hermes_hidden.json``
    (the same store ``plugins_hub`` reads) with an atomic write + audit, so a
    hidden overlay plugin stays hidden across dashboard reloads.
    """
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "plugin name required",
                     "qubit_counterpart": "CLI `qubit plugins`"}
    hidden = _hidden_set(cfg)
    if bool(b.get("visible", True)):
        hidden.discard(name)
    else:
        hidden.add(name)
    try:
        _hidden_save(cfg, hidden)
    except Exception as exc:
        return _fail(exc)
    _audit("plugin_visibility", "dashboard:user",
           {"name": name, "visible": name not in hidden})
    return _ok(name=name, visible=name not in hidden,
               hidden=sorted(hidden),
               detail="persisted per-user visibility")


def h_debug_share(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "diagnostics", {}, actor="dashboard:user", session="dashboard")
    return _ok(bundle=_res(out),
               detail="redacted diagnostic bundle (never secrets); no external share")


def h_import_upload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("IMPORT_REVIEW_REQUIRED",
                 "Uploads enter quarantine for review; reviewed artifacts install "
                 "via skills install. Nothing executes on upload.",
                 "CLI `qubit import-agent inspect`")


def h_hooks(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as H
    try:
        res = H.list_hooks(cfg)
    except Exception as exc:
        return _fail(exc)
    recs = res.get("hooks", []) if isinstance(res, dict) else []
    entries = []
    for r in recs if isinstance(recs, list) else []:
        if not isinstance(r, dict):
            continue
        entries.append({
            "event": str(r.get("event", "")),
            "matcher": str(r.get("name", "")),
            "command": None,
            "timeout": None,
            "allowed": str(r.get("status", "")) == "active",
            "approved_at": None,
            "executable": False,
        })
    # HooksResponse wire shape; shells never execute (broker-audited
    # delivery only), so command stays null by policy.
    return _ok(hooks=entries, valid_events=["*"])


def h_hooks_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as H
    event = str(b.get("event", "") or "")
    shell = str(b.get("command", b.get("shell", "")) or "")
    name = str(b.get("name", "") or "")
    if not event:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "hook event required"}
    if not b.get("confirm", b.get("approved", False)):
        return _need_confirm("hook declare (shell stored disabled, never runs)")
    if not name:
        name = re.sub(r"[^a-z0-9]+", "-", event.lower()).strip("-")[:40] or "hook"
    try:
        res = H.declare(cfg, name, event, shell,
                        requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    hook = res.get("hook", {}) if isinstance(res, dict) else {}
    return _ok(ok=True, event=str(hook.get("event", event)),
               command="", approved=False)


def h_ckpt(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "checkpoints_list", {}, actor="dashboard:user",
                     session="dashboard")
    res = _res(out) or {}
    recs = res.get("checkpoints", []) if isinstance(res, dict) else []
    sessions = []
    total = 0
    for r in recs if isinstance(recs, list) else []:
        if not isinstance(r, dict):
            continue
        size = int(r.get("bytes", r.get("size", 0)) or 0)
        total += size
        sessions.append({"session": str(r.get("name", r.get("session", ""))),
                         "files": int(r.get("files", 0) or 0), "bytes": size})
    # CheckpointsResponse wire shape.
    return _ok(sessions=sessions, total_bytes=total)


def h_ckpt_prune(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("CHECKPOINT_CONFIRM_REQUIRED",
                 "Checkpoint prune is metadata-destructive: confirm in the Qubit "
                 "operations console with explicit scope.",
                 "dashboard /ops checkpoints + CLI")


def h_actions_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "actions_list", {"limit": 50},
                     actor="dashboard:user", session="dashboard")
    return _ok(actions=_res(out), state=out.get("state"))


def _prefs_path(cfg: Any) -> Path:
    return _store_path(cfg, "hermes_dashboard_prefs.json")


def _prefs(cfg: Any) -> Dict[str, Any]:
    return _load_json_store(_prefs_path(cfg))


def h_font(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str], setter: bool = False) -> Tuple[int, Dict[str, Any]]:
    """GET/PUT /api/dashboard/font -> real persisted dashboard font pref."""
    prefs = _prefs(cfg)
    if setter:
        font = str(b.get("font", "system") or "system").strip() or "system"
        prefs["font"] = font
        try:
            _save_json_store(_prefs_path(cfg), prefs)
        except Exception as exc:
            return _fail(exc)
        _audit("dashboard_font", "dashboard:user", {"font": font})
        return _ok(font=font, persisted=True)
    return _ok(font=str(prefs.get("font", "system") or "system"))


def h_plugins(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Any]:
    # Wire shape is a bare manifest array (api.getPlugins: PluginManifestResponse[]).
    return 200, _overlay_manifests()


def h_themes(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    out = themes_response(cfg)
    active = str(_prefs(cfg).get("theme", "") or "").strip()
    if active and any(t.get("name") == active for t in out.get("themes", [])):
        out["active"] = active
    return _ok(**out)


def h_theme_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/dashboard/theme -> real persisted theme selection."""
    name = str(b.get("theme", "qubit") or "qubit").strip() or "qubit"
    available = {t.get("name") for t in themes_response(cfg).get("themes", [])}
    if name not in available:
        return _ok(ok=True, theme="qubit", persisted=False,
                   detail=f"only the Qubit theme ships in this packet; "
                          f"{name!r} is not installed (session keeps qubit)")
    prefs = _prefs(cfg)
    prefs["theme"] = name
    try:
        _save_json_store(_prefs_path(cfg), prefs)
    except Exception as exc:
        return _fail(exc)
    _audit("dashboard_theme", "dashboard:user", {"theme": name})
    return _ok(ok=True, theme=name, persisted=True)


def _need_confirm(action: str) -> Tuple[int, Dict[str, Any]]:
    return _staged(f"{action} requires explicit confirmation: re-POST "
                   "with {\"confirm\": true}", "")


def _persona_path(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir()) / "hermes_persona.json"
    except Exception:
        return Path("/tmp") / "hermes_persona.json"


def _persona_active(cfg: Any) -> str:
    try:
        raw = json.loads(_persona_path(cfg).read_text())
        return str(raw.get("active", "") or "")
    except (OSError, ValueError):
        return ""


def _persona_save(cfg: Any, name: str) -> None:
    _persona_path(cfg).write_text(json.dumps({"active": name}))


def _hidden_path(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir()) / "hermes_hidden.json"
    except Exception:
        return Path("/tmp") / "hermes_hidden.json"


def _hidden_set(cfg: Any) -> set:
    try:
        raw = json.loads(_hidden_path(cfg).read_text())
        return set(raw.get("hidden", []) or [])
    except (OSError, ValueError):
        return set()


def _hidden_save(cfg: Any, hidden: set) -> None:
    _hidden_path(cfg).write_text(json.dumps({"hidden": sorted(hidden)}))


def h_profiles_active(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_profiles(cfg, q, b, {**p, "kind": "active"})


def profiles_active_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    name = str(b.get("active", b.get("name", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "active profile name required"}
    try:
        _persona_save(cfg, name)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, active=name)


def _profile_field(cfg: Any, b: Dict[str, Any], p: Dict[str, str],
                   key: str) -> Tuple[int, Dict[str, Any]]:
    from . import brain as B
    name = str(p.get("name", "") or "")
    value = b.get("value", b.get("content", b.get("description", "")))
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    if not b.get("confirm"):
        return _need_confirm(f"profile {key} update")
    try:
        res = B.profile_set(cfg, name, key, str(value or ""),
                            requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, field=res.get("field", {}))


def profile_soul_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _profile_field(cfg, b, p, "soul")


def profile_desc(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _profile_field(cfg, b, p, "description")


def profile_model(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    code, out = _profile_field(cfg, b, p, "model")
    if code != 200:
        return code, out
    return _ok(ok=True, provider="ollama-local",
               model=str(b.get("model", b.get("value", ""))))


def profile_describe_auto(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("DESCRIBE_AUTO_MANUAL",
                 "Model-generated profile copy is not offered; write the "
                 "description explicitly so it stays reviewable.",
                 "PUT /api/profiles/{name}/description with {\"confirm\": true}")


def profile_rename(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("PERSONA_IMMUTABLE_ID",
                 "Persona identities are immutable once created (memory "
                 "provenance keys on them); create a new principal instead.",
                 "CLI `qubit brain principal-add` (same Brain store)", 501)


def profile_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import brain as B
    name = str(p.get("name", "") or "")
    if not b.get("confirm"):
        return _need_confirm("profile delete")
    try:
        res = B.profile_forget(cfg, name, requested_by="dashboard:user",
                               confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok}


def cron_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    name = str(b.get("name", "") or "")
    task_text = str(b.get("prompt", b.get("task_text", "")) or "")
    when = str(b.get("when", "") or "")
    if isinstance(b.get("schedule"), dict):
        when = str(b["schedule"].get("expr", "") or when)
    if not when and isinstance(b.get("repeat"), dict):
        when = str(b["repeat"].get("expr", "") or "")
    if not b.get("confirm"):
        return _need_confirm("schedule create (unattended work)")
    try:
        res = S.create(cfg, name, task_text, when,
                       requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    job = res.get("job", {}) if isinstance(res, dict) else {}
    return 200, _cron_shape(job if isinstance(job, dict) else {})


def cron_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/cron/jobs/{job_id} -> real enabled-state transition.

    The only in-place schedule mutation Qubit offers is enable/disable (pause
    or resume); task text/schedule are immutable (delete + recreate retains
    run history). Without ``confirm`` the write stages a typed 409 on the
    shared journal; with it the same ``schedules`` store the CLI/voice use
    performs the transition and records an audit receipt.
    """
    from . import schedules as S
    try:
        job_id = int(p.get("id", "0"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "schedule id must be numeric"}
    enabled = b.get("enabled")
    if enabled is None:
        return _gate("CRON_UPDATE_IMMUTABLE",
                     "Task text and schedule timing are immutable in place; "
                     "disable, delete, and recreate to change them.",
                     "DELETE then POST /api/cron/jobs (same schedules store)")
    enabled = bool(enabled)
    if not b.get("confirm"):
        return _staged(
            f"schedule {'resume' if enabled else 'pause'} requires explicit "
            "confirmation: re-POST with {\"confirm\": true}",
            "")
    try:
        if enabled:
            res = S.resume(cfg, job_id, requested_by="dashboard:user")
        else:
            res = S.pause(cfg, job_id, requested_by="dashboard:user")
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res,
                                  "enabled": enabled,
                                  "qubit_counterpart":
                                  "CLI `qubit schedules pause|resume`"}


def cron_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    if not b.get("confirm"):
        return _need_confirm("schedule delete")
    try:
        S.delete(cfg, int(p.get("id", "0")), requested_by="dashboard:user",
                 confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True)


def skill_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/skills -> install a validated local manifest, staged disabled.

    The manifest is validated by ``permissions.SkillManifest`` and installed
    into the same ``PermissionsStore`` the CLI/voice use; it never runs until a
    separate confirm-gated enable. Without ``confirm`` the install stages a
    typed 409 on the shared journal. A malformed/incomplete manifest is a typed
    400 (no partial install, no fabricated success).
    """
    from .permissions import SkillManifest, SkillValidationError, permissions_store
    from .capabilities import Tier
    if not b.get("confirm"):
        return _staged(
            "skill install stages staged-disabled and requires confirmation: "
            "re-POST with {\"confirm\": true}", "")
    manifest = b.get("manifest")
    if not isinstance(manifest, dict):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill install needs a manifest object"}
    try:
        tier = manifest.get("tier", "reversible")
        spec = SkillManifest(
            skill_id=str(manifest.get("skill_id", b.get("name", "")) or ""),
            version=str(manifest.get("version", "0.0.0") or "0.0.0"),
            name=str(manifest.get("name", manifest.get("skill_id", "")) or ""),
            description=str(manifest.get("description", "") or ""),
            source=str(manifest.get("source", "") or ""),
            revision=str(manifest.get("revision", "1") or "1"),
            license=str(manifest.get("license", "") or ""),
            content_hash=str(manifest.get("content_hash", "") or ""),
            capabilities=tuple(manifest.get("capabilities", []) or []),
            scopes=tuple(manifest.get("scopes", []) or []),
            tier=tier if isinstance(tier, Tier) else Tier(str(tier)),
            sensitivity=str(manifest.get("sensitivity", "internal") or "internal"),
            approval_policy=str(manifest.get("approval_policy", "confirm")
                                or "confirm"),
            timeout_s=float(manifest.get("timeout_s", 30) or 30),
            resource_limits=dict(manifest.get("resource_limits", {}) or {}),
            audit_events=tuple(manifest.get("audit_events", ["skill.install"])
                               or ["skill.install"]),
            compatibility=str(manifest.get("compatibility", "") or ""),
            rollback=str(manifest.get("rollback", "remove the skill") or
                         "remove the skill"))
        rec = permissions_store(cfg).install_skill(
            spec, requested_by="dashboard:user", confirmed=True)
    except SkillValidationError as exc:
        return 400, {"ok": False, "code": "SKILL_INVALID",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    return _ok(ok=True, skill_id=rec.skill_id, status=rec.status,
               version=rec.manifest.version,
               qubit_counterpart="CLI `qubit skills install <manifest>`")


def skill_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/skills/content -> real versioned rollback+reinstall.

    A skill's active payload is replaced by rolling the installed record back
    to its staged predecessor and reinstalling the supplied validated manifest
    (versions are immutable, so rollback is always available). Confirm-gated
    and audited; never an in-place overwrite.
    """
    from .permissions import permissions_store
    name = str(p.get("id", "") or b.get("skill_id", b.get("name", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill name required"}
    if not b.get("confirm"):
        return _staged(
            "skill update stages a rollback+reinstall and requires "
            "confirmation: re-POST with {\"confirm\": true}", "")
    try:
        store = permissions_store(cfg)
        rec = store.skill(name)
        if rec is None:
            return 404, {"ok": False, "code": "NOT_FOUND",
                         "detail": f"no skill {name!r}"}
        rolled = store.rollback_skill(
            name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, skill_id=rolled.skill_id, status=rolled.status,
               detail="rolled back to the prior staged version; reinstall the "
                      "new validated manifest to advance",
               qubit_counterpart="CLI `qubit skills rollback/install`")


def moa_put(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("MOA_SUPERSEDED",
                 "MoA fan-out presets are not executed by Qubit; the ordered "
                 "provider fallback gateway (8Q) is the counterpart.",
                 "Models page provider/fallback display + 8Q gateway", 501)


def mcp_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("MCP_NO_MOUNT",
                 "MCP servers are inventory-only; Qubit never mounts them.",
                 "Qubit-native skills with manifests", 501)


def mcp_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return 404, {"ok": False, "code": "MCP_NOT_MOUNTED",
                 "detail": "no MCP server is mounted; nothing to delete"}


def webhooks_enable(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import webhooks as W
    if not b.get("confirm", True):
        return _need_confirm("webhook delivery toggle")
    try:
        res = W.set_enabled(cfg, bool(b.get("enabled", True)),
                            requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, platform="webhook", enabled=bool(res.get("enabled")),
               needs_restart=False)


def webhooks_enabled(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import webhooks as W
    if not b.get("confirm", True):
        return _need_confirm("webhook subscription toggle")
    try:
        res = W.set_sub_enabled(cfg, str(p.get("id", "")),
                                bool(b.get("enabled", True)),
                                requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, name=str(res.get("id", "")),
               enabled=bool(res.get("enabled")))


def webhooks_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import webhooks as W
    if not b.get("confirm"):
        return _need_confirm("webhook delete")
    try:
        W.remove(cfg, str(p.get("id", "")), requested_by="dashboard:user",
                 confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True)


def msg_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import messaging as M
    if not b.get("confirm", True):
        return _need_confirm("messaging account update")
    try:
        M.account_update(cfg, str(p.get("id", "")),
                         label=str(b.get("label", b.get("name", "")) or ""),
                         enabled=b.get("enabled") if "enabled" in b else None,
                         requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, platform=str(p.get("id", "")))


def h_analytics_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_analytics(cfg, q, b, {**p, "kind": "models"})


def h_files_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_files(cfg, q, b, {**p, "kind": "read"})


def files_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("WRITE_BROKERED",
                 "Direct file deletes are refused; deletes run only as "
                 "brokered, confirmed argv actions with audit receipts.",
                 "CLI `qubit opencode` + dashboard /ops + approvals")


def hooks_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as H
    name = str(b.get("name", "") or "")
    if not name and (b.get("event") or b.get("command")):
        try:
            recs = H.list_hooks(cfg).get("hooks", [])
            for r in recs if isinstance(recs, list) else []:
                if not isinstance(r, dict):
                    continue
                if r.get("event") == b.get("event"):
                    name = str(r.get("name", ""))
                    break
        except Exception:
            name = ""
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "hook name (or matching event) required"}
    if not b.get("confirm"):
        return _need_confirm("hook delete")
    try:
        H.remove(cfg, name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True)


def _agent_row(skill_id: str, manifest: Dict[str, Any], status: str,
               hidden: set) -> Dict[str, Any]:
    return {
        "name": skill_id,
        "version": str(manifest.get("version", "0.0.0") or "0.0.0"),
        "description": str(manifest.get("description", "") or ""),
        "source": str(manifest.get("source", "qubit-local") or "qubit-local"),
        "runtime_status": ("enabled" if status == "enabled" else
                           "disabled" if status in ("staged", "disabled") else
                           "inactive"),
        "has_dashboard_manifest": False,
        "dashboard_manifest": None,
        "path": "",
        "can_remove": True,
        "can_update_git": False,
        "auth_required": False,
        "auth_command": "",
        "user_hidden": skill_id in hidden,
    }


def plugins_hub(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    try:
        recs = permissions_store(cfg).skills()
    except Exception as exc:
        return _fail(exc)
    hidden = _hidden_set(cfg)
    rows = []
    for rec in recs:
        d = rec.to_dict() if hasattr(rec, "to_dict") else {}
        man = d.get("manifest", {}) if isinstance(d, dict) else {}
        if not isinstance(man, dict):
            man = {}
        rows.append(_agent_row(str(d.get("skill_id", "")), man,
                               str(d.get("status", "")), hidden))
    orphans = []
    for m in _overlay_manifests():
        orphans.append({
            "name": m["name"], "label": m["label"],
            "description": m["description"], "icon": m.get("icon", ""),
            "version": m["version"], "tab": m["tab"], "entry": m["entry"],
            "css": m.get("css"), "has_api": m.get("has_api", False),
            "source": m.get("source", "qubit-overlay"),
        })
    return _ok(plugins=rows, orphan_dashboard_plugins=orphans,
               providers={
                   "memory_provider": "qubit-local",
                   "memory_options": [{
                       "name": "qubit-local",
                       "description": "local-only SQLite/FTS5 store",
                       "available": True, "configured": True,
                       "status": "ready"}],
                   "context_engine": "qubit-broker",
                   "context_options": [{
                       "name": "qubit-broker",
                       "description": "single permission broker + audit"}]})


def _agent_status(name: str, status: str, cfg: Any,
                  b: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "plugin name required"}
    if not b.get("confirm", True):
        return _need_confirm(f"plugin {status}")
    try:
        permissions_store(cfg).set_skill_status(
            name, status, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, name=name)


def agent_enable(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _agent_status(str(p.get("id", "")), "active", cfg, b)


def agent_disable(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _agent_status(str(p.get("id", "")), "disabled", cfg, b)


def agent_remove(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    name = str(p.get("id", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "plugin name required"}
    if not b.get("confirm"):
        return _need_confirm("plugin remove")
    try:
        permissions_store(cfg).remove_skill(
            name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, name=name)


def agent_install(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("SKILL_MANIFEST_REQUIRED",
                 "Direct plugin installs are refused; install a validated "
                 "local manifest staged disabled instead.",
                 "CLI `qubit skills install <manifest>` + 7E validation")


def agent_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("SKILL_VERSIONED",
                 "In-place plugin updates are refused; versions are immutable. "
                 "Remove and reinstall the new validated version.",
                 "CLI `qubit skills remove/rollback/install`")


def oauth_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return 404, {"ok": False, "code": "OAUTH_MANUAL_HANDOFF",
                 "detail": "no OAuth sessions are stored server-side; "
                           "enrollment is a manual handoff in your own browser"}


def mem_provider_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/memory/provider -> real persisted local-provider selection."""
    active = str(b.get("active", b.get("provider", "")) or "")
    if active and active != "qubit-local":
        return 400, {"ok": False, "code": "MEMORY_LOCAL_ONLY",
                     "detail": "qubit-local is the only memory provider; "
                               "cloud sync is refused by policy"}
    path = _store_path(cfg, "hermes_memory_provider.json")
    try:
        store = _load_json_store(path)
        store["provider"] = "qubit-local"
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, active="qubit-local", config=store,
               qubit_counterpart="CLI `qubit memory` (same local store)")


def mem_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET provider config -> real persisted local-store settings."""
    name = str(p.get("id", "") or "qubit-local")
    if name != "qubit-local":
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no memory provider {name!r}"}
    store = _load_json_store(_store_path(cfg, "hermes_memory_provider.json"))
    fields = [{"key": k, "value": v}
              for k, v in sorted(store.items()) if k != "provider"]
    return _ok(name="qubit-local", label="Qubit local memory", fields=fields,
               config=store,
               setup={"required": False,
                      "detail": "no setup needed; local-only store"},
               qubit_counterpart="CLI `qubit memory` (same local store)")


def mem_config_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT config / POST setup for a memory provider -> real local setup.

    ``qubit-local`` is the only provider. This persists the provider's
    retrieval options (tier, retention_days) into the same state-dir override
    store the config console uses, confirm-gated and audited. A request for a
    non-local provider is a typed refusal (no cloud sync), never a fake 200.
    """
    name = str(p.get("id", "") or b.get("name", "") or "qubit-local")
    if name != "qubit-local":
        return _gate("MEMORY_LOCAL_ONLY",
                     f"no memory provider {name!r}; qubit-local is the only "
                     "store and cloud sync is refused by policy",
                     "Memory page search + CLI `qubit memory`")
    if not b.get("confirm"):
        return _staged(
            "memory provider setup requires explicit confirmation: re-POST "
            "with {\"confirm\": true}", "")
    values = {k: v for k, v in b.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    path = _store_path(cfg, "hermes_memory_provider.json")
    try:
        store = _load_json_store(path)
        store.update(values)
        store["provider"] = "qubit-local"
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, name="qubit-local", setup={"required": False},
               config=store, path=str(path),
               qubit_counterpart="CLI `qubit memory` (same local store)")


def _store_path(cfg: Any, name: str) -> Path:
    try:
        base = Path(cfg.state_dir())
    except Exception:
        base = Path("/tmp")
    return base / name


def _config_overrides_path(cfg: Any) -> Path:
    return _store_path(cfg, "hermes_config_overrides.json")


def _env_refs_path(cfg: Any) -> Path:
    return _store_path(cfg, "hermes_env_refs.json")


def _load_json_store(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
        return dict(raw) if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json_store(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    try:
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_bytes(path.read_bytes())
    except OSError:
        pass
    tmp.replace(path)
    _audit("store_write", "dashboard:user",
           {"path": path.name, "keys": sorted(data)[:20]})


def config_save(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/config -> real confirm-gated Qubit settings override write.

    Values are persisted to ``<state_dir>/hermes_config_overrides.json`` with
    an atomic backup and an audit line, then read back by ``h_config`` on the
    next request. Secret-shaped keys (``*_key``, ``*_token``, ``*_secret``)
    never enter the store: only a vault reference name is accepted. Without
    ``confirm`` the write stages a typed 409; it never fabricates success.
    """
    if not isinstance(b, dict) or not b:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "config write needs an object body"}
    if not b.get("confirm"):
        return _staged(
            "config write stages through the Qubit settings console: re-POST "
            "with {\"confirm\": true} plus a fresh approval", "")
    values = {k: v for k, v in b.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    secret_shaped = [k for k in values
                     if re.search(r"(_key|_token|_secret|_password)$", k)]
    if secret_shaped:
        return _gate("SECRET_WRITE_REFUSED",
                     "The dashboard never writes secret values; reference "
                     "names only (" + ", ".join(sorted(secret_shaped)[:5]) + ")",
                     "CLI `qubit auth login` (vault references, manual handoff)")
    path = _config_overrides_path(cfg)
    try:
        previous = _load_json_store(path)
        previous.update(values)
        _save_json_store(path, previous)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, config=values, count=len(values),
               path=str(path),
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def env_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT/DELETE /api/env -> real confirm-gated vault-reference registry.

    Only *references* enter ``<state_dir>/hermes_env_refs.json``: a value must
    be a vault reference name (``vault:...``/``ref:...``), never a raw secret.
    DELETE removes a reference. Both are confirm-gated and audited; they never
    write a secret value or fabricate success.
    """
    if not b.get("confirm"):
        return _staged(
            "env reference change stages through the Qubit vault: re-POST "
            "with {\"confirm\": true}", "")
    path = _env_refs_path(cfg)
    deleting = bool(b.get("delete")) or bool(q.get("delete"))
    name = str(b.get("name", b.get("key", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env reference name required"}
    try:
        store = _load_json_store(path)
    except Exception as exc:
        return _fail(exc)
    if deleting:
        existed = name in store
        store.pop(name, None)
        _save_json_store(path, store)
        return _ok(ok=True, name=name, removed=existed, count=len(store),
                   qubit_counterpart="CLI `qubit auth status` (names only)")
    value = str(b.get("value", b.get("ref", "")) or "")
    if not value:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env reference value required"}
    if not re.match(r"^(vault|ref):", value):
        return _gate("SECRET_WRITE_REFUSED",
                     "Env writes accept vault references only, never secret "
                     "values; store the secret in the OS keyring/manager.",
                     "CLI `qubit auth login` (manual-handoff enrollment)")
    store[name] = value
    _save_json_store(path, store)
    return _ok(ok=True, name=name, value=value, count=len(store),
               qubit_counterpart="CLI `qubit auth status` (names only)")


def toolset_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET toolset config -> real Qubit provider/credential-reference matrix."""
    from . import proxy_status as PX
    name = str(p.get("id", "") or "")
    try:
        prov = PX.providers(cfg)
    except Exception as exc:
        return _fail(exc)
    rows: List[Dict[str, Any]] = []
    active: Optional[str] = None
    for row in prov.get("providers", []) or []:
        if not isinstance(row, dict):
            continue
        pname = str(row.get("provider", ""))
        is_active = bool(row.get("allowlisted"))
        if is_active and active is None:
            active = pname
        rows.append({
            "name": pname, "badge": "", "tag": "",
            "env_vars": [{
                "key": f"{pname.upper()}_KEY_REF",
                "prompt": "credential reference (never a value)",
                "url": None, "default": None,
                "is_set": str(row.get("key_ref", "")) != "missing"}],
            "post_setup": None, "requires_nous_auth": False,
            "is_active": is_active,
            "status": "ready" if is_active else "needs_setup"})
    return _ok(name=name, has_category=bool(rows), providers=rows,
               active_provider=active,
               qubit_counterpart="CLI `qubit capability` + dashboard Settings "
                                 "(classic)")


def toolset_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET toolset models -> real local model catalog (loopback-only)."""
    from . import local_models as LM
    name = str(p.get("id", "") or "")
    try:
        cat = LM.catalog(cfg)
    except Exception as exc:
        return _fail(exc)
    rows = [{
        "id": str(m.get("id", m.get("name", ""))),
        "display": str(m.get("name", m.get("id", ""))),
        "speed": "", "strengths": "", "price": ""}
        for m in (cat.get("models", []) if isinstance(cat, dict) else [])
        if isinstance(m, dict)]
    return _ok(name=name, has_models=bool(rows), provider="ollama-local",
               plugin="ollama-http", models=rows, current=None, default=None,
               runtime_observed=bool(cat.get("runtime_observed")) if isinstance(cat, dict) else False,
               qubit_counterpart="CLI `qubit local-models catalog`")


# ---------------------------------------------------------------------------
# OpenClaw delta Wave 3 surfaces (D3/D4/D20/D21/D24): file transfer, active
# recall, document extraction, question prompts, and session sharing. Every
# handler dispatches the SAME module function the CLI/voice call.
# ---------------------------------------------------------------------------
_OK_STATES = ("OK", "EMPTY", "COMPLETE", "DEGRADED", "READY")
_OK_CODES = ("", "OK", "COMPLETE", "EMPTY", "READY")


def _surface(out: Any) -> Tuple[int, Dict[str, Any]]:
    """Adapt a module result to the compat envelope + a typed HTTP status.

    A ``*_CONFIRM_REQUIRED`` result is a 409 staged gate; denied/unavailable
    typed states are 403; success states are 200. The raw module body (with
    its typed ``code``/``owner``/``rollback``) is preserved verbatim.
    """
    if not isinstance(out, dict):
        return _fail(Exception("module returned a non-object result"))
    body = dict(out)
    state = str(body.get("state", "") or "")
    code = str(body.get("code", "") or "")
    if code.upper().endswith("CONFIRM_REQUIRED") or state in (
            "CONFIRM_REQUIRED", "CONFIRMATION_REQUIRED"):
        body["ok"] = False
        return 409, body
    ok = (state in _OK_STATES and code in _OK_CODES) or state == "EMPTY"
    body["ok"] = bool(ok)
    body.setdefault("qubit_counterpart", "")
    return (200 if ok else 403), body


def _ft_default_node(cfg: Any) -> str:
    """First local (loopback) enrolled node, else first node, else ""."""
    from . import file_transfer as FT
    from . import node_host as NH

    try:
        recs = NH._load(cfg).get("nodes", {})
    except Exception:
        return ""
    for node_id, rec in recs.items():
        if isinstance(rec, dict) and FT._is_local(rec):
            return str(node_id)
    return str(next(iter(recs), "") or "")


def _qint(q: Dict[str, str], b: Dict[str, Any], *keys: str, default: int) -> int:
    raw = _query_text(q, b, *keys)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def h_transfers_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    return _ok(**FT.status(cfg))


def h_transfers_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    return _surface(FT.list_directory(
        cfg, node, _query_text(q, b, "path"),
        limit=_qint(q, b, "limit", default=FT.MAX_LIST_ENTRIES)))


def h_transfers_fetch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    if not b.get("confirm"):
        return _need_confirm("file transfer fetch")
    return _surface(FT.fetch(
        cfg, node, _query_text(q, b, "path"),
        offset=_qint(q, b, "offset", default=0),
        max_bytes=_qint(q, b, "max_bytes", "maxBytes",
                        default=FT.DEFAULT_FETCH_BYTES), confirmed=True))


def h_transfers_plan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    if not b.get("confirm"):
        return _need_confirm("file transfer plan")
    return _surface(FT.stream_plan(
        cfg, node, _query_text(q, b, "path"),
        chunk_size=_qint(q, b, "chunk_size", "chunkSize",
                         default=FT.DEFAULT_CHUNK_BYTES)))


def h_transfers_resume(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    return _surface(FT.resume(
        cfg, node, _query_text(q, b, "transfer_id", "id"),
        offset=_qint(q, b, "offset", default=0)))


def h_recall_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _ok(**AM.status(cfg))


def h_recall_policy_get(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _ok(**AM.policy_get(cfg))


def h_recall_policy_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    if not b.get("confirm"):
        return _need_confirm("active-recall policy set")
    enabled = b.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("on", "true", "yes", "enable")
    scoped = b.get("session_scoped")
    if isinstance(scoped, str):
        scoped = scoped.strip().lower() in ("session", "scoped", "true", "on")
    return _surface(AM.policy_set(
        cfg, enabled=enabled, min_score=b.get("min_score"),
        max_items=b.get("max_items"), session_scoped=scoped, confirmed=True))


def h_recall_query(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _surface(AM.recall(
        cfg, _query_text(q, b, "query", "q"),
        session=_query_text(q, b, "session"),
        limit=b.get("limit")))


def h_recall_resolve(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _surface(AM.resolve(
        cfg, _query_text(q, b, "query", "q"),
        session=_query_text(q, b, "session")))


def h_recall_escalate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    if not b.get("confirm"):
        return _need_confirm("memory escalation")
    return _surface(AM.escalate(
        cfg, _qint(q, b, "memory_id", "id", default=0),
        reason=_query_text(q, b, "reason"), confirmed=True))


def h_documents_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import doc_extract as DE
    return _ok(**DE.status(cfg))


def h_documents_kinds(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import doc_extract as DE
    return _ok(**DE.kinds(cfg))


def h_documents_extract(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import doc_extract as DE
    if not b.get("confirm"):
        return _need_confirm("document extraction")
    try:
        out = DE.extract(
            cfg, _query_text(q, b, "path"),
            max_chars=_qint(q, b, "max_chars", "maxChars",
                            default=DE.DEFAULT_MAX_CHARS),
            page=(_qint(q, b, "page", default=0) or None), confirmed=True)
    except DE.DocExtractError as exc:
        return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                     "detail": _redact(exc.detail)[:300],
                     "owner": "qubit-policy",
                     "rollback": "nothing was extracted"}
    return _surface(out)


def h_ask_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    return _ok(**QP.status(cfg))


def h_ask_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    return _ok(**QP.pending(cfg))


def h_ask_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    if not b.get("confirm"):
        return _need_confirm("question prompt create")
    try:
        out = QP.ask(cfg, [{"text": _query_text(q, b, "question", "text"),
                            "kind": "text"}],
                     session=_query_text(q, b, "session"), confirmed=True)
    except QP.QuestionError as exc:
        return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                     "detail": _redact(exc.detail)[:300],
                     "owner": "qubit-policy",
                     "rollback": "prompt store untouched"}
    return _surface(out)


def h_ask_answer(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    answers = b.get("answers", b.get("responses"))
    if answers is None:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "answers object/list is required"}
    try:
        out = QP.answer(cfg, p.get("prompt_id", b.get("prompt_id", "")), answers)
    except QP.QuestionError as exc:
        return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                     "detail": _redact(exc.detail)[:300],
                     "owner": "qubit-policy",
                     "rollback": "prompt store untouched"}
    return _surface(out)


def h_session_share_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    return _surface(SS.status(cfg))


def h_session_shares_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    return _surface(SS.list_shares(cfg))


def h_session_share_publish(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    sid = p.get("session_id", "") or _query_text(q, b, "session_id")
    ttl = b.get("ttl_s", q.get("ttl_s"))
    try:
        ttl = float(ttl) if ttl not in (None, "") else SS.DEFAULT_TTL_S
    except (TypeError, ValueError):
        ttl = SS.DEFAULT_TTL_S
    return _surface(SS.publish(
        cfg, sid, ttl_s=ttl, confirmed=bool(b.get("confirm")),
        requested_by="dashboard:user"))


def h_session_share_revoke(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    sid = p.get("share_id", "") or _query_text(q, b, "share_id")
    return _surface(SS.revoke(cfg, sid, confirmed=bool(b.get("confirm"))))


def h_session_shares_purge(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    if not b.get("confirm"):
        return 409, {
            "ok": False, "state": "CONFIRMATION_REQUIRED",
            "code": SS.CONFIRM_CODE,
            "detail": "session share purge requires explicit confirmation: "
                      "re-POST with {\"confirm\": true}",
            "qubit_counterpart": "CLI `qubit shares purge --confirm` + voice "
                                 "yes",
        }
    return _surface(SS.purge_expired(cfg))


def h_share_resolve(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /share/{share_id}: public token in the query, no dashboard token.

    The share token is the bearer credential (constant-time checked inside the
    module). A missing/expired/revoked/wrong token returns a typed gate and
    NEVER another session's transcript.
    """
    from . import session_share as SS
    sid = p.get("share_id", "") or ""
    token = q.get("token") or b.get("token") or ""
    out = SS.resolve(cfg, sid, token)
    state = str(out.get("state", "") or "")
    body = dict(out)
    body.setdefault("qubit_counterpart", "open /share/<id> with the share token")
    if state == "OK":
        body["ok"] = True
        return 200, body
    body["ok"] = False
    http = {
        "NOT_FOUND": 404, "EXPIRED": 410, "REVOKED": 403, "DENIED": 403,
        "ESTOP": 403, "FAILED": 400, "UNAVAILABLE": 503,
    }.get(state, 403)
    return http, body


# ---------------------------------------------------------------------------
# Agent 5-10 Qubit-owned front door: GET /api/qubit/*. Each handler is a thin
# read over the exact module the CLI/voice use; there is no proxy to the fork.
# ---------------------------------------------------------------------------

def _eval_state_path(cfg: Any, name: str) -> Path:
    return Path(cfg.state_dir()) / "evaluation" / name


def h_qubit_gateway_discovery(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import gateway_peers as GW
    try:
        return _ok(**GW.discovery(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_discovery(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_inventory as RIN
    try:
        return _ok(**RIN._discovery(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_marketplace(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_inventory as RIN
    try:
        return _ok(**RIN._marketplace(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_history(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    name = str(q.get("name", b.get("name", "")) or "")
    try:
        return _ok(**RI.update_history(cfg, name))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_update_report(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.update_report(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.status(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_images(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.images())
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_doctor(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.doctor(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_egress_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import proxy_status as PX
    try:
        return _ok(**PX.egress_status(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_egress_providers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import proxy_status as PX
    try:
        return _ok(**PX.providers(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_egress_check(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import proxy_status as PX
    host = str(q.get("host", b.get("host", "")) or "").strip()
    if not host:
        return 400, {"ok": False, "state": "FAILED",
                     "code": "EGRESS_HOST_REQUIRED",
                     "detail": "query parameter 'host' is required",
                     "qubit_counterpart": "CLI `qubit egress check <host>`"}
    try:
        out = PX.check_egress(cfg, host)
    except Exception as exc:
        return _fail(exc)
    allowed = bool(out.get("allowed"))
    return (200 if allowed else 403), {
        "ok": allowed, "state": "OK" if allowed else "DENIED",
        "qubit_counterpart": "CLI `qubit egress check <host>`", **out}


def h_qubit_policy_conformance(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import release as REL
    root = str(q.get("root", b.get("root", "")) or "")
    if root.strip():
        path = Path(root)
        if not path.is_dir():
            return 400, {"ok": False, "state": "FAILED",
                         "code": "WORKSPACE_UNREADABLE",
                         "detail": f"workspace root {path} is not a directory",
                         "qubit_counterpart":
                         "CLI `qubit policy conformance`"}
        try:
            return _ok(**REL.workspace_conformance(path))
        except Exception as exc:
            return _fail(exc)
    try:
        return _ok(**REL.workspace_conformance())
    except Exception as exc:
        return _fail(exc)


def h_qubit_policy_attest(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import release as REL
    root = str(q.get("root", b.get("root", "")) or "")
    if root.strip():
        path = Path(root)
        if not path.is_dir():
            return 400, {"ok": False, "state": "FAILED",
                         "code": "WORKSPACE_UNREADABLE",
                         "detail": f"workspace root {path} is not a directory",
                         "qubit_counterpart": "CLI `qubit policy attest`"}
        try:
            return _ok(**REL.workspace_attestation(path))
        except Exception as exc:
            return _fail(exc)
    try:
        return _ok(**REL.workspace_attestation())
    except Exception as exc:
        return _fail(exc)


def h_qubit_hooks_doctor(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as HK
    try:
        return _ok(**HK.doctor(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_usage(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .evaluation import usage_analytics as UA
    limit = _qint(q, b, "limit", default=1000)
    ledger = UA.UsageLedger(_eval_state_path(cfg, "usage.jsonl"))
    records = ledger.read(limit=limit)
    out = UA.analyze(records)
    out.update({"records": len(records), "rejections": ledger.rejections,
                "path": str(ledger.path),
                "authority": "append-only local usage ledger; no prompts or "
                             "secrets are stored",
                "qubit_counterpart": "CLI `qubit usage analytics`"})
    return _ok(**out)


def h_qubit_receipts(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .evaluation import receipts as RC
    path = _eval_state_path(cfg, "receipts.jsonl")
    if not path.exists():
        return 404, {"ok": False, "state": "NOT_FOUND",
                     "code": "RECEIPTS_NOT_FOUND",
                     "detail": f"no execution-receipt chain at {path}",
                     "qubit_counterpart": "CLI `qubit receipts`"}
    rows, rejected = [], 0
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rejected += 1
    verdict = RC.verify_receipts(rows)
    return _ok(records=len(rows), rejected=rejected, verify=verdict,
               path=str(path),
               qubit_counterpart="CLI `qubit receipts`")


def h_qubit_logbook(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .evaluation import logbook as LB
    ledger = LB.Logbook(_eval_state_path(cfg, "logbook.jsonl"))
    limit = _qint(q, b, "limit", default=100)
    window = q.get("window_s", b.get("window_s"))
    try:
        window = float(window) if window not in (None, "") else None
    except (TypeError, ValueError):
        window = None
    return _ok(entries=ledger.entries(limit=limit, window_s=window),
               summary=ledger.summary(window_s=window), path=str(ledger.path),
               qubit_counterpart="CLI `qubit logbook entries`")


def h_qubit_browser_profiles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import browser as BR
    try:
        return _ok(**BR.profiles(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_local_models_fallback(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = str(q.get("model", b.get("model", "")) or "")
    try:
        out = (LM.resolve_fallback(cfg, model) if model
               else LM.fallback_chain(cfg))
    except Exception as exc:
        return _fail(exc)
    ok = str(out.get("state", "")) in ("OK", "EMPTY", "READY", "DEGRADED")
    return (200 if ok else 403), {
        "ok": ok, "qubit_counterpart":
        "CLI `qubit local-models fallback [model]`", **out}


# ---------------------------------------------------------------------------
# Skills hub: the Qubit-reviewed LOCAL catalog. Remote hub fetches are refused
# by pinned-source policy; every read/mutation runs the same local manifests +
# PermissionsStore the CLI/voice use (never a network fetch or process spawn).
# ---------------------------------------------------------------------------
_REMOTE_IDENTIFIER = re.compile(r"(://|^github:|^gitlab:|^official/|^hub/)")


def _is_remote_identifier(text: Any) -> bool:
    return bool(_REMOTE_IDENTIFIER.search(str(text or "").strip().lower()))


def _local_skill_catalog(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Local reviewed skill/plugin catalog (installed + Qubit overlay)."""
    from .permissions import permissions_store
    rows: Dict[str, Dict[str, Any]] = {}
    try:
        for rec in permissions_store(cfg).skills():
            d = rec.to_dict() if hasattr(rec, "to_dict") else {}
            man = d.get("manifest", {}) if isinstance(d, dict) else {}
            if not isinstance(man, dict):
                man = {}
            ident = str(d.get("skill_id", "") or "")
            if not ident:
                continue
            rows[ident] = {
                "identifier": ident,
                "name": str(man.get("name", ident) or ident),
                "description": str(man.get("description", "") or ""),
                "source": str(man.get("source", "qubit-local") or "qubit-local"),
                "trust_level": "trusted", "category": "installed",
                "installed": str(d.get("status", "")) == "active",
                "status": str(d.get("status", "")),
                "version": str(man.get("version", "") or ""),
                "tags": [], "repo": None, "_manifest": man,
                "_origin": "permissions",
            }
    except Exception:
        pass
    for m in _overlay_manifests():
        name = str(m.get("name", "") or "")
        if not name or name in rows:
            continue
        rows[name] = {
            "identifier": name,
            "name": str(m.get("label", name) or name),
            "description": str(m.get("description", "") or ""),
            "source": "qubit-overlay", "trust_level": "builtin",
            "category": "overlay", "installed": True, "status": "active",
            "version": str(m.get("version", "") or ""),
            "tags": [], "repo": None, "_manifest": m, "_origin": "overlay",
        }
    return rows


def _hub_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _hub_installed(cfg: Any) -> Dict[str, str]:
    from .permissions import permissions_store
    try:
        return {r.skill_id: r.status for r in permissions_store(cfg).skills()}
    except Exception:
        return {}


def _manifest_spec(manifest: Dict[str, Any]) -> Any:
    from .permissions import SkillManifest
    from .capabilities import Tier
    tier = manifest.get("tier", "reversible")
    return SkillManifest(
        skill_id=str(manifest.get("skill_id", manifest.get("name", "")) or ""),
        version=str(manifest.get("version", "0.0.0") or "0.0.0"),
        name=str(manifest.get("name", manifest.get("skill_id", "")) or ""),
        description=str(manifest.get("description", "") or ""),
        source=str(manifest.get("source", "qubit-local") or "qubit-local"),
        revision=str(manifest.get("revision", "1") or "1"),
        license=str(manifest.get("license", "") or ""),
        content_hash=str(manifest.get("content_hash", "") or ""),
        capabilities=tuple(manifest.get("capabilities", []) or []),
        scopes=tuple(manifest.get("scopes", []) or []),
        tier=tier if isinstance(tier, Tier) else Tier(str(tier)),
        sensitivity=str(manifest.get("sensitivity", "internal") or "internal"),
        approval_policy=str(manifest.get("approval_policy", "confirm")
                            or "confirm"),
        timeout_s=float(manifest.get("timeout_s", 30) or 30),
        resource_limits=dict(manifest.get("resource_limits", {}) or {}),
        audit_events=tuple(manifest.get("audit_events", ["skill.install"])
                           or ["skill.install"]),
        compatibility=str(manifest.get("compatibility", "") or ""),
        rollback=str(manifest.get("rollback", "remove the skill")
                     or "remove the skill"))


def _hub_resolve_manifest(cfg: Any, identifier: Any) -> Tuple[Any, str]:
    """(manifest_dict, origin) for a local identifier; (None, reason) else."""
    ident = str(identifier or "").strip()
    if not ident or _is_remote_identifier(ident):
        return None, "remote"
    path = Path(ident)
    if path.suffix == ".json" and path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return None, "malformed"
        return (raw if isinstance(raw, dict) else None), "path"
    row = _local_skill_catalog(cfg).get(ident)
    if row is None:
        return None, "not_found"
    return row.get("_manifest") or None, str(row.get("_origin", "catalog"))


def _hub_remote_gate(counterpart: str) -> Tuple[int, Dict[str, Any]]:
    return _gate("BLOCKED_REMOTE_SOURCE",
                 "Remote skill-hub fetches are refused by pinned-source "
                 "policy; Qubit serves the reviewed local manifest catalog.",
                 counterpart)


def h_skills_hub_official(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    installed = _hub_installed(cfg)
    out = []
    for row in _local_skill_catalog(cfg).values():
        pub = _hub_public(row)
        pub["installed"] = (str(pub.get("identifier")) in installed
                            or bool(row.get("installed")))
        out.append(pub)
    return _ok(skills=out, source="qubit-local",
               qubit_counterpart="CLI `qubit skills list`")


def h_skills_hub_sources(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    rows = list(_local_skill_catalog(cfg).values())
    return _ok(sources=[{
        "id": "qubit-local", "label": "Qubit reviewed local manifests",
        "available": True, "searchable": True, "rate_limited": False,
        "detail": "remote hubs (GitHub/index) are refused by pinned-source "
                  "policy; local reviewed manifests only"}],
        index_available=False,
        featured=[_hub_public(r) for r in rows[:12]],
        installed=_hub_installed(cfg),
        qubit_counterpart="CLI `qubit skills list`")


def h_skills_hub_search(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    query = str(q.get("query", q.get("q", b.get("q", b.get("query", ""))))
                or "").strip()
    if not query:
        return _ok(results=[], source_counts={}, timed_out=[],
                   installed={})
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 50))
    except (TypeError, ValueError):
        limit = 20
    needle = query.lower()
    hits = [r for r in _local_skill_catalog(cfg).values()
            if needle in str(r.get("name", "")).lower()
            or needle in str(r.get("description", "")).lower()
            or needle in str(r.get("identifier", "")).lower()]
    results = [_hub_public(r) for r in hits[:limit]]
    return _ok(results=results,
               source_counts={"qubit-local": len(results)},
               timed_out=[], installed=_hub_installed(cfg),
               qubit_counterpart="CLI `qubit skills list`")


def h_skills_hub_preview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    ident = str(q.get("identifier", b.get("identifier", "")) or "")
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "identifier is required",
                     "qubit_counterpart": "CLI `qubit skills show <id>`"}
    if _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills show <id>`")
    man, _why = _hub_resolve_manifest(cfg, ident)
    if man is None:
        return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                     "detail": f"no local reviewed skill {ident!r}",
                     "qubit_counterpart": "CLI `qubit skills list`"}
    return _ok(name=str(man.get("name", ident) or ident),
               description=str(man.get("description", "") or ""),
               source=str(man.get("source", "qubit-local") or "qubit-local"),
               identifier=ident, trust_level="trusted", repo=None, tags=[],
               skill_md=json.dumps(man, indent=2, sort_keys=True),
               files=sorted(str(k) for k in man.keys()),
               qubit_counterpart="CLI `qubit skills show <id>`")


def h_skills_hub_scan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    ident = str(q.get("identifier", b.get("identifier", "")) or "")
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "identifier is required",
                     "qubit_counterpart": "CLI `qubit skills validate`"}
    if _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills validate`")
    man, _why = _hub_resolve_manifest(cfg, ident)
    if man is None:
        return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                     "detail": f"no local reviewed skill {ident!r}",
                     "qubit_counterpart": "CLI `qubit skills list`"}
    findings: List[Dict[str, Any]] = []
    verdict, policy = "clean", "allow"
    reason = "local reviewed manifest passes the Qubit skill contract"
    try:
        _manifest_spec(man)
    except Exception as exc:
        verdict, policy = "blocked", "block"
        reason = _redact(str(exc))[:200]
        findings = [{"severity": "high", "category": "validation",
                     "file": "manifest", "line": 0, "description": reason}]
    counts = {sev: 0 for sev in ("critical", "high", "medium", "low")}
    for f in findings:
        if f["severity"] in counts:
            counts[f["severity"]] += 1
    return _ok(name=str(man.get("name", ident) or ident), identifier=ident,
               source=str(man.get("source", "qubit-local") or "qubit-local"),
               trust_level="trusted", verdict=verdict,
               summary=("no blocking findings" if verdict == "clean"
                        else "manifest failed validation"),
               policy=policy, policy_reason=reason, findings=findings,
               severity_counts=counts, tier1=None,
               qubit_counterpart="CLI `qubit skills validate`")


def h_skills_hub_install(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import SkillValidationError, permissions_store
    ident = str(b.get("identifier", q.get("identifier", "")) or "")
    if not ident or _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills install <local-manifest>`")
    if not b.get("confirm"):
        return _staged(
            f"local skill install {ident!r} stages disabled and requires "
            "confirmation: re-POST with {\"confirm\": true}", "")
    man, why = _hub_resolve_manifest(cfg, ident)
    if man is None:
        return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                     "detail": f"no local reviewed manifest {ident!r} ({why})",
                     "qubit_counterpart": "CLI `qubit skills list`"}
    try:
        spec = _manifest_spec(man)
        rec = permissions_store(cfg).install_skill(
            spec, requested_by="dashboard:user", confirmed=True)
    except SkillValidationError as exc:
        return 400, {"ok": False, "code": "SKILL_INVALID",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    return _ok(ok=True, skill_id=rec.skill_id, status=rec.status,
               identifier=ident,
               qubit_counterpart="CLI `qubit skills install <manifest>`")


def h_skills_hub_uninstall(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    name = str(b.get("name", b.get("identifier", q.get("identifier", "")))
               or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "name is required",
                     "qubit_counterpart": "CLI `qubit skills remove <id>`"}
    if not b.get("confirm"):
        return _staged(
            f"skill uninstall {name!r} requires confirmation: re-POST with "
            "{\"confirm\": true}", "")
    try:
        permissions_store(cfg).remove_skill(
            name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills remove <id>`"}
    return _ok(ok=True, name=name,
               qubit_counterpart="CLI `qubit skills remove <id>`")


def h_skills_hub_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    ident = str(b.get("name", b.get("identifier", q.get("identifier", "")))
                or "").strip()
    if not ident or _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills install <local-manifest>`")
    if not b.get("confirm"):
        return _staged(
            f"skill update {ident!r} requires confirmation: re-POST with "
            "{\"confirm\": true}", "")
    try:
        store = permissions_store(cfg)
        if store.skill(ident) is None:
            return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                         "detail": f"no installed skill {ident!r}",
                         "qubit_counterpart": "CLI `qubit skills list`"}
        rolled = store.rollback_skill(ident, requested_by="dashboard:user",
                                      confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        if code == "PermissionError" and "rollback state" in str(exc):
            return _gate("SKILL_VERSIONED",
                         "Skill versions are immutable; this staged record has "
                         "no prior active version to roll back. Reinstall the "
                         "new validated manifest to advance.",
                         "CLI `qubit skills remove/install <manifest>`")
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills rollback`"}
    return _ok(ok=True, skill_id=rolled.skill_id, status=rolled.status,
               detail="rolled back to the prior staged version; reinstall the "
                      "new validated manifest to advance",
               qubit_counterpart="CLI `qubit skills rollback/install`")


# ---------------------------------------------------------------------------
# Wave 4: real brokered file / git / blueprint writes (staged -> confirm ->
# apply through the same Qubit stores and confinement the CLI/voice use).
# ---------------------------------------------------------------------------

def _full_replace_patch(name: str, before: str, after: str) -> str:
    """Unified diff that replaces an entire UTF-8 file's contents."""
    old = before.splitlines()
    new = after.splitlines()
    hunk = (f"@@ -1,{len(old)} +1,{len(new)} @@" if old
            else f"@@ -0,0 +1,{len(new)} @@")
    lines = [f"--- a/{name}", f"+++ b/{name}", hunk]
    lines += ["-" + ln for ln in old]
    lines += ["+" + ln for ln in new]
    return "\n".join(lines) + "\n"


def h_fs_write_text(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/fs/write-text -> real confined file_patch broker (apply).

    The write is a bounded unified diff applied through the same
    ``file_patch`` confinement/backup/rollback/audit path as the CLI. Without
    ``confirm`` it stages a typed 409; with it the file is really written and a
    rollback receipt returned. A path outside the approved project roots is a
    typed 400 (never a host write).
    """
    from . import file_patch as FP
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": "CLI `qubit patch apply`"}
    content = b.get("content", b.get("text", ""))
    if not isinstance(content, str):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "content must be text",
                     "qubit_counterpart": "CLI `qubit patch apply`"}
    if not b.get("confirm"):
        return _staged(
            f"writing {path!r} is a brokered confined file patch and requires "
            "confirmation: re-POST with {\"confirm\": true}", "")
    try:
        target = FP.resolve_target(cfg, path)
        before = (target.read_text(encoding="utf-8")
                  if target.exists() else "")
        patch = _full_replace_patch(target.name, before, content)
        receipt = FP.apply_patch(cfg, path, patch,
                                 requested_by="dashboard:user", confirmed=True)
    except FP.FilePatchError as exc:
        http = {FP.CONFIRM_CODE: 409, FP.ESTOP_CODE: 403,
                FP.OUTSIDE_ROOT_CODE: 400, FP.BAD_INPUT_CODE: 400,
                FP.CONFINEMENT_CODE: 503, FP.CONFLICT_CODE: 409}.get(
                    getattr(exc, "code", ""), 400)
        return http, {"ok": False, "code": exc.code,
                      "detail": _redact(exc.detail)[:300],
                      "owner": "qubit-policy",
                      "rollback": "nothing written; target unchanged",
                      "qubit_counterpart": "CLI `qubit patch apply`"}
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, path=str(target), state=receipt.get("state"),
               receipt=receipt,
               qubit_counterpart="CLI `qubit patch rollback` (undo available)")


def h_files_upload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/files/upload -> real confined UTF-8 text write.

    Accepts plain ``content`` or a base64 ``data_url``/``content_base64``. The
    bytes must decode as UTF-8; genuinely-binary uploads are a typed
    ``FILES_BINARY`` gate (Qubit serves bounded text writes, not binary
    storage). A text upload runs the same confirm-gated ``file_patch`` broker
    as ``/api/fs/write-text``.
    """
    import base64
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": "POST /api/fs/write-text"}
    content = b.get("content")
    if content is None:
        blob = str(b.get("data_url", b.get("content_base64", "")) or "")
        if blob:
            raw = blob.split(",", 1)[1] if blob.startswith("data:") else blob
            try:
                data = base64.b64decode(raw, validate=False)
            except Exception:
                return 400, {"ok": False, "code": "MALFORMED",
                             "detail": "content_base64 is not valid base64",
                             "qubit_counterpart": "POST /api/fs/write-text"}
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                return _gate("FILES_BINARY",
                             "Binary uploads are not stored; Qubit serves "
                             "bounded UTF-8 text writes through the confined "
                             "file broker.",
                             "POST /api/fs/write-text (text only)")
    if not isinstance(content, str):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "content (text) or content_base64 required",
                     "qubit_counterpart": "POST /api/fs/write-text"}
    return h_fs_write_text(cfg, q,
                           {**b, "path": path, "content": content}, p)


def h_files_mkdir(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/files/mkdir -> real confirm-gated confined directory creation.

    The new directory must resolve inside an approved project root through the
    same ``file_patch`` confinement/estop check the brokered write path uses.
    Without ``confirm`` it stages a typed 409; a path outside the roots (or an
    existing target) is a typed 400, never a host write.
    """
    from . import file_patch as FP
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": "CLI `qubit project add-folder`"}
    if not b.get("confirm"):
        return _staged(
            f"creating directory {path!r} is brokered and requires "
            "confirmation: re-POST with {\"confirm\": true}", "")
    try:
        target = FP.resolve_target(cfg, path)
        target.mkdir(parents=True, exist_ok=False)
    except FP.FilePatchError as exc:
        http = {FP.CONFIRM_CODE: 409, FP.ESTOP_CODE: 403,
                FP.OUTSIDE_ROOT_CODE: 400, FP.BAD_INPUT_CODE: 400,
                FP.CONFINEMENT_CODE: 503, FP.CONFLICT_CODE: 409}.get(
                    getattr(exc, "code", ""), 400)
        return http, {"ok": False, "code": exc.code,
                      "detail": _redact(exc.detail)[:300],
                      "owner": "qubit-policy",
                      "rollback": "nothing created; target unchanged",
                      "qubit_counterpart": "CLI `qubit project add-folder`"}
    except FileExistsError:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "target already exists",
                     "qubit_counterpart": "CLI `qubit project add-folder`"}
    except Exception as exc:
        return _fail(exc)
    _audit("files.mkdir", "dashboard:user", {"path": str(target)})
    return _ok(ok=True, path=str(target), created=True,
               qubit_counterpart="CLI `qubit files inspect` / `qubit project`")


def h_git_write(verb: str) -> Handler:
    """POST /api/git/... -> real confirm-gated brokered git-write authorization.

    Qubit git stays a scoped read surface: the handler validates the repo with
    the real ``git_scope`` confinement, then records a capability grant
    (``git.write.<verb>``) scoped to that repo in the shared PermissionsStore
    the CLI/voice use. The actual apply runs through the brokered opencode
    worktree flow, never a raw shell git write from the dashboard.
    """
    counterpart = ("CLI `qubit opencode` (brokered worktree apply) + "
                   "`qubit permissions` grant")

    def _handler(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        from . import git_scope as GS
        from .permissions import permissions_store
        try:
            root = GS.resolve_root(cfg, _git_query_root(q, b))
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            return 400, {"ok": False, "code": code,
                         "detail": _redact(str(exc))[:300],
                         "qubit_counterpart": "CLI `qubit git status`"}
        if not b.get("confirm"):
            return _staged(
                f"git {verb} is a brokered write on {root}; re-POST with "
                "{\"confirm\": true} plus a fresh approval", "")
        try:
            grant = permissions_store(cfg).grant(
                f"git.write.{verb}", requested_by="dashboard:user",
                confirmed=True, scopes=(str(root),),
                note=f"brokered git {verb}; apply runs through the opencode "
                     "worktree flow")
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            return 400, {"ok": False, "code": code,
                         "detail": _redact(str(exc))[:300],
                         "qubit_counterpart": counterpart}
        return _ok(ok=True, verb=verb, root=str(root), grant_id=grant.id,
                   applied=False,
                   detail="authorization recorded; apply through the brokered "
                          "opencode worktree flow",
                   qubit_counterpart=counterpart)

    return _handler


def h_blueprint_instantiate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/cron/blueprints/instantiate -> real confirm-gated schedule.

    Fills a local blueprint's slots and creates the schedule in the same
    ``schedules`` store the CLI/voice use. ``values`` (or top-level keys) carry
    ``name``/``prompt``/``when``. Without ``confirm`` it stages a typed 409; a
    missing schedule expression is a typed 400, never a fabricated job.
    """
    values = b.get("values") if isinstance(b.get("values"), dict) else {}
    merged = {**{k: v for k, v in b.items() if k != "values"}, **values}
    if "blueprint" in merged and "name" not in merged:
        merged["name"] = merged["blueprint"]
    if not merged.get("name"):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "blueprint name required",
                     "qubit_counterpart": "CLI `qubit schedules`"}
    if not merged.get("when") and not merged.get("schedule"):
        return 400, {"ok": False, "code": "BLUEPRINT_BAD_FILL",
                     "detail": "a schedule expression (when) is required",
                     "qubit_counterpart": "CLI `qubit schedules create`"}
    values = {k: v for k, v in merged.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    return cron_create(cfg, q, {**values, "confirm": bool(b.get("confirm"))}, p)


_EXTRA_HANDLERS: Dict[str, Handler] = {
    "cron_pause": lambda cfg, q, b, p: _cron_mutate("pause", cfg, p),
    "cron_resume": lambda cfg, q, b, p: _cron_mutate("resume", cfg, p),
    "cron_trigger": lambda cfg, q, b, p: _cron_mutate("run_due", cfg, p),
    "pairing_create": lambda cfg, q, b, p: h_pairing_mutate("create", cfg, b),
    "pairing_approve": lambda cfg, q, b, p: h_pairing_mutate("approve", cfg, b),
    "pairing_revoke": lambda cfg, q, b, p: h_pairing_mutate("revoke", cfg, b),
    "webhooks_add": lambda cfg, q, b, p: h_webhooks_mutate("add", cfg, b, p),
    "webhooks_show": lambda cfg, q, b, p: h_webhooks_mutate("show", cfg, b, p),
    "webhooks_mutate": lambda cfg, q, b, p: h_webhooks_mutate("remove", cfg, b, p),
    "blueprints": h_blueprints,
    "delivery": h_delivery_targets,
    "sess_import": lambda cfg, q, b, p: h_sessions_write(cfg, q, b, p, "import"),
    "sess_prune": lambda cfg, q, b, p: h_sessions_write(cfg, q, b, p, "prune"),
    "sess_bulk": lambda cfg, q, b, p: h_sessions_write(cfg, q, b, p, "bulk"),
    "sess_delete": lambda cfg, q, b, p: h_sessions_write(cfg, q, b, p, "delete"),
    "sess_patch": lambda cfg, q, b, p: h_sessions_write(cfg, q, b, p, "patch"),
    "h_session_patch": h_session_patch,
    "h_session_delete": h_session_delete,
    "h_session_resume": h_session_resume,
    "sess_empty": lambda cfg, q, b, p: h_sess_empty(cfg, q, b, p),
    "mem_reset": h_mem_reset,
    "mem_prov": h_mem_provider,
    "msg_test": h_msg_test,
    "mcp_test": h_mcp_test,
    "mcp_enabled": h_mcp_enabled,
    "toolset_write": h_toolset_write,
    "profile_write": h_profile_write,
    "plugins_rescan": h_plugins_rescan,
    "plugin_visibility": h_plugin_visibility,
    "debug_share": h_debug_share,
    "import_upload": h_import_upload,
    "ckpt_prune": h_ckpt_prune,
    "ckpt": h_ckpt,
    "h_ckpt": h_ckpt,
    "actions_read": h_actions_read,
    "font": lambda cfg, q, b, p: h_font(cfg, q, b, p, False),
    "font_set": lambda cfg, q, b, p: h_font(cfg, q, b, p, True),
    "h_profiles_active": h_profiles_active,
    "profiles_active_set": profiles_active_set,
    "profile_soul_set": profile_soul_set,
    "profile_desc": profile_desc,
    "profile_model": profile_model,
    "profile_describe_auto": profile_describe_auto,
    "profile_rename": profile_rename,
    "profile_delete": profile_delete,
    "cron_create": cron_create,
    "cron_update": cron_update,
    "cron_delete": cron_delete,
    "skill_create": skill_create,
    "skill_update": skill_update,
    "moa_put": moa_put,
    "mcp_create": mcp_create,
    "mcp_delete": mcp_delete,
    "webhooks_enable": webhooks_enable,
    "webhooks_enabled": webhooks_enabled,
    "webhooks_delete": webhooks_delete,
    "msg_update": msg_update,
    "h_analytics_models": h_analytics_models,
    "h_files_read": h_files_read,
    "files_delete": files_delete,
    "hooks_delete": hooks_delete,
    "plugins_hub": plugins_hub,
    "agent_enable": agent_enable,
    "agent_disable": agent_disable,
    "agent_remove": agent_remove,
    "agent_install": agent_install,
    "agent_update": agent_update,
    "oauth_delete": oauth_delete,
    "env_write": env_write,
    "mem_provider_set": mem_provider_set,
    "mem_config": mem_config,
    "mem_config_write": mem_config_write,
    "h_profile_setup_command": h_profile_setup_command,
    "h_profile_soul": h_profile_soul,
    "h_skill_content": h_skill_content,
    "h_mcp_catalog": h_mcp_catalog,
    "config_save": config_save,
    "toolset_config": toolset_config,
    "toolset_models": toolset_models,
    "h_local_models_status": h_local_models_status,
    "h_local_models_hardware": h_local_models_hardware,
    "h_local_models_catalog": h_local_models_catalog,
    "h_local_models_jobs": h_local_models_jobs,
    "h_local_models_job_show": h_local_models_job_show,
    "h_local_models_runtime_install": h_local_models_runtime_install,
    "h_local_models_download": h_local_models_download,
    "h_local_models_delete": h_local_models_delete,
    "h_local_models_quickstart": h_local_models_quickstart,
    "h_local_models_server": h_local_models_server,
    "h_local_models_eject": h_local_models_eject,
    "h_local_models_activate": h_local_models_activate,
    "h_local_models_sideload": h_local_models_sideload,
    "h_local_models_search": h_local_models_search,
    "h_local_models_search_files": h_local_models_search_files,
    "h_local_models_download_browsed": h_local_models_download_browsed,
    "h_custom_endpoints": h_custom_endpoints,
    "h_profiles_sessions": h_profiles_sessions,
    "h_profiles_sessions_sidebar": h_profiles_sessions_sidebar,
    "h_profiles_projects_tree": h_profiles_projects_tree,
    "h_profile_create": h_profile_create,
    "h_terminal_backends": h_terminal_backends,
    "h_computer_use_status": h_computer_use_status,
    "h_learning_graph": h_learning_graph,
    "h_learning_node": h_learning_node,
    "h_learning_node_update": h_learning_node_update,
    "h_learning_node_delete": h_learning_node_delete,
    "h_auth_providers": h_auth_providers,
    "h_transfers_status": h_transfers_status,
    "h_transfers_list": h_transfers_list,
    "h_transfers_fetch": h_transfers_fetch,
    "h_transfers_plan": h_transfers_plan,
    "h_transfers_resume": h_transfers_resume,
    "h_recall_status": h_recall_status,
    "h_recall_policy_get": h_recall_policy_get,
    "h_recall_policy_set": h_recall_policy_set,
    "h_recall_query": h_recall_query,
    "h_recall_resolve": h_recall_resolve,
    "h_recall_escalate": h_recall_escalate,
    "h_documents_status": h_documents_status,
    "h_documents_kinds": h_documents_kinds,
    "h_documents_extract": h_documents_extract,
    "h_ask_status": h_ask_status,
    "h_ask_pending": h_ask_pending,
    "h_ask_create": h_ask_create,
    "h_ask_answer": h_ask_answer,
    "h_session_share_status": h_session_share_status,
    "h_session_shares_list": h_session_shares_list,
    "h_session_share_publish": h_session_share_publish,
    "h_session_share_revoke": h_session_share_revoke,
    "h_session_shares_purge": h_session_shares_purge,
    "h_share_resolve": h_share_resolve,
    "h_qubit_gateway_discovery": h_qubit_gateway_discovery,
    "h_qubit_runtime_discovery": h_qubit_runtime_discovery,
    "h_qubit_runtime_marketplace": h_qubit_runtime_marketplace,
    "h_qubit_runtime_history": h_qubit_runtime_history,
    "h_qubit_runtime_update_report": h_qubit_runtime_update_report,
    "h_qubit_runtime_status": h_qubit_runtime_status,
    "h_qubit_runtime_images": h_qubit_runtime_images,
    "h_qubit_runtime_doctor": h_qubit_runtime_doctor,
    "h_qubit_egress_status": h_qubit_egress_status,
    "h_qubit_egress_providers": h_qubit_egress_providers,
    "h_qubit_egress_check": h_qubit_egress_check,
    "h_qubit_policy_conformance": h_qubit_policy_conformance,
    "h_qubit_policy_attest": h_qubit_policy_attest,
    "h_qubit_hooks_doctor": h_qubit_hooks_doctor,
    "h_qubit_usage": h_qubit_usage,
    "h_qubit_receipts": h_qubit_receipts,
    "h_qubit_logbook": h_qubit_logbook,
    "h_qubit_browser_profiles": h_qubit_browser_profiles,
    "h_qubit_local_models_fallback": h_qubit_local_models_fallback,
    "h_gateway_start": h_gateway_supervision("start"),
    "h_gateway_stop": h_gateway_supervision("stop"),
    "h_custom_endpoints_write": h_custom_endpoints_write,
    "h_custom_endpoints_activate": h_custom_endpoints_activate,
    "h_custom_endpoints_delete": h_custom_endpoints_delete,
    "h_terminal_backend_set": h_terminal_backend_set,
    "h_computer_use_grant": h_computer_use_grant,
    "h_profile_export": h_profile_export,
    "h_skills_hub_official": h_skills_hub_official,
    "h_skills_hub_sources": h_skills_hub_sources,
    "h_skills_hub_search": h_skills_hub_search,
    "h_skills_hub_preview": h_skills_hub_preview,
    "h_skills_hub_scan": h_skills_hub_scan,
    "h_skills_hub_install": h_skills_hub_install,
    "h_skills_hub_uninstall": h_skills_hub_uninstall,
    "h_skills_hub_update": h_skills_hub_update,
    "h_fs_write_text": h_fs_write_text,
    "h_files_upload": h_files_upload,
    "h_files_mkdir": h_files_mkdir,
    "h_git_stage": h_git_write("stage"),
    "h_git_unstage": h_git_write("unstage"),
    "h_git_revert": h_git_write("revert"),
    "h_git_commit": h_git_write("commit"),
    "h_git_worktree_add": h_git_write("worktree-add"),
    "h_git_worktree_remove": h_git_write("worktree-remove"),
    "h_git_branch_switch": h_git_write("branch-switch"),
    "h_blueprint_instantiate": h_blueprint_instantiate,
    "pairing_clear_pending": h_pairing_clear_pending,
    "h_mcp_servers": h_mcp_servers,
    "h_mcp_catalog_live": h_mcp_catalog_live,
    "h_mcp_test_local": h_mcp_test_local,
    "h_msg_test_live": h_msg_test_live,
    "h_pub": h_pub,
    "h_gateway_drain": h_gateway_drain,
    "h_plugin_visibility_set": h_plugin_visibility,
    "h_login_page": h_login_page,
    "h_auth_login": h_auth_login,
    "h_auth_native_authorize": h_auth_native_authorize,
    "h_auth_callback": h_auth_callback,
    "h_auth_password_login": h_auth_password_login,
    "h_auth_logout": h_auth_logout,
    "h_auth_native_token": h_auth_native_token,
    "h_auth_native_refresh": h_auth_native_refresh,
    "h_plugin_achievements": _hermes_plugins.h_plugin_achievements,
    "h_plugin_achievements_scan_status":
        _hermes_plugins.h_plugin_achievements_scan_status,
    "h_plugin_achievements_recent_unlocks":
        _hermes_plugins.h_plugin_achievements_recent_unlocks,
    "h_plugin_achievements_session_badges":
        _hermes_plugins.h_plugin_achievements_session_badges,
    "h_plugin_achievements_rescan":
        _hermes_plugins.h_plugin_achievements_rescan,
    "h_plugin_achievements_reset_state":
        _hermes_plugins.h_plugin_achievements_reset_state,
    "h_plugin_kanban_board": _hermes_plugins.h_plugin_kanban_board,
    "h_plugin_kanban_task": _hermes_plugins.h_plugin_kanban_task,
    "h_plugin_kanban_stats": _hermes_plugins.h_plugin_kanban_stats,
    "h_plugin_kanban_assignees": _hermes_plugins.h_plugin_kanban_assignees,
    "h_plugin_kanban_boards": _hermes_plugins.h_plugin_kanban_boards,
    "h_plugin_kanban_config": _hermes_plugins.h_plugin_kanban_config,
    "h_plugin_kanban_projects": _hermes_plugins.h_plugin_kanban_projects,
    "h_plugin_kanban_workers": _hermes_plugins.h_plugin_kanban_workers,
    "h_plugin_kanban_diagnostics": _hermes_plugins.h_plugin_kanban_diagnostics,
    "h_plugin_kanban_model_options":
        _hermes_plugins.h_plugin_kanban_model_options,
    "h_plugin_kanban_home_channels":
        _hermes_plugins.h_plugin_kanban_home_channels,
    "h_plugin_fleet": _hermes_plugins.h_plugin_fleet,
}

HANDLERS: Dict[str, Handler] = {
    "h_status": h_status, "h_auth_me": h_auth_me, "h_events": h_events,
    "h_health": h_health, "h_egress_status": h_egress_status,
    "h_sessions_list": h_sessions_list, "h_sessions_stats": h_sessions_stats,
    "h_session_show": h_session_show, "h_session_messages": h_session_messages,
    "h_config": h_config, "h_model_info": h_model_info,
    "h_model_recommended_default": h_model_recommended_default,
    "h_model_options": h_model_options, "h_model_moa": h_model_moa,
    "h_model_auxiliary": h_model_auxiliary, "h_model_set": h_model_set,
    "h_profiles": h_profiles, "h_skills": h_skills,
    "h_skills_toggle": h_skills_toggle, "h_toolsets": h_toolsets,
    "h_cron_jobs": h_cron_jobs, "h_cron_job_show": h_cron_job_show,
    "h_cron_job_runs": h_cron_job_runs,
    "h_logs": h_logs, "h_system_stats": h_system_stats, "h_mcp": h_mcp,
    "h_pairing_list": h_pairing_list, "h_webhooks_list": h_webhooks_list,
    "h_platforms": h_platforms, "h_memory": h_memory,
    "h_analytics": h_analytics, "h_files": h_files, "h_gateway": h_gateway,
    "h_curator": h_curator, "h_env": h_env, "h_ops": h_ops,
    "h_ops_catalog": h_ops_catalog,
    "h_ops_run": h_ops_run,
    "h_kanban_events": h_kanban_events, "h_chat_turn": h_chat_turn,
    "h_plugins": h_plugins, "h_themes": h_themes, "h_theme_set": h_theme_set,
    "h_hooks": h_hooks, "h_hooks_create": h_hooks_create,
    "h_approvals_list": h_approvals_list,
    "h_ops_confirm": h_ops_confirm, "h_ops_cancel": h_ops_cancel,
    "h_git_status": h_git_status, "h_git_log": h_git_log,
    "h_git_diff": h_git_diff, "h_git_show": h_git_show,
    "h_git_file_diff": h_git_file_diff,
    "h_git_worktrees": h_git_worktrees, "h_git_branches": h_git_branches,
    "h_git_base_branches": h_git_base_branches,
    "h_git_review_list": h_git_review_list,
    "h_git_review_commit_context": h_git_review_commit_context,
    "h_git_review_rev_parse": h_git_review_rev_parse,
    "h_fs_list": h_fs_list, "h_fs_tree": h_fs_tree, "h_fs_stat": h_fs_stat,
    "h_fs_read_text": h_fs_read_text,
    "h_fs_read_data_url": h_fs_read_data_url,
    "h_fs_git_root": h_fs_git_root, "h_fs_default_cwd": h_fs_default_cwd,
    "h_audio_status": h_audio_status, "h_audio_devices": h_audio_devices,
    "h_audio_voice_config": h_audio_voice_config,
    "h_audio_transcribe": h_audio_transcribe,
    "h_audio_speak": h_audio_speak,
}
HANDLERS.update(_EXTRA_HANDLERS)


def _overlay_manifests() -> List[Dict[str, Any]]:
    manifests: List[Dict[str, Any]] = []
    plugdir = OVERLAY_DIR / "plugins"
    if plugdir.exists():
        for mf in sorted(plugdir.glob("*/manifest.json")):
            try:
                manifests.append(json.loads(mf.read_text()))
            except (OSError, ValueError):
                continue
    return manifests


def plugins_response() -> Dict[str, Any]:
    return {"plugins": _overlay_manifests()}


def themes_response(cfg: Any) -> Dict[str, Any]:
    theme_path = OVERLAY_DIR / "qubit-theme.json"
    try:
        definition = json.loads(theme_path.read_text())
    except (OSError, ValueError):
        definition = {}
    active = "qubit" if definition else "default"
    themes = [{"name": "qubit", "label": "Qubit Command",
               "description": "Qubit graphite/navy with signal-lime accents",
               "definition": definition}] if definition else []
    return {"active": active, "themes": themes}


def match(method: str, path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
    for route in ROUTES:
        if route["method"] != method:
            continue
        m = re.fullmatch(route["pattern"], path)
        if m:
            return route, {k: v for k, v in m.groupdict().items()}
    return None, {}


def dispatch(cfg: Any, method: str, path: str,
             query: Optional[Dict[str, str]] = None,
             body: Optional[Dict[str, Any]] = None,
             actor: str = "dashboard:user") -> Tuple[int, Any]:
    clean = path.split("?")[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    route, params = match(method, clean)
    if route is None:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no compat route for {method} {path}"}
    if route["disposition"] == "gated":
        gate = route["gate"]
        _audit("gated", actor, {"route": f"{method} {path}",
                                "code": gate.get("code", "")})
        return gate.get("http", 403), {"ok": False, "state": "UNAVAILABLE",
                                       "code": gate.get("code"),
                                       "detail": gate.get("detail"),
                                       "qubit_counterpart": gate.get("counterpart"),
                                       "owner": gate.get("owner"),
                                       "rollback": gate.get("rollback")}
    handler = HANDLERS.get(route["handler"])
    if handler is None:
        return 500, {"ok": False, "code": "NO_HANDLER",
                     "detail": f"handler {route['handler']!r} missing"}
    try:
        code, out = handler(cfg, query or {}, body or {}, params)
    except Exception as exc:
        return _fail(exc)
    _audit("served", actor, {"route": f"{method} {path}", "code": str(code)})
    return code, out
