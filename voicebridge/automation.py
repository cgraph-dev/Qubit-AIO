"""Qubit automation: standing orders, flows, heartbeat, QA runner, yields.

Closes the remaining Qubit automation rows that have no external
dependency, on top of the ONE existing scheduling authority
(:mod:`voicebridge.schedules` / the monitor tick) and the EXISTING
messaging/permission/audit/e-stop path — never a second scheduler, broker,
listener, or transport:

* **OC-27 autonomous scheduler** — the bounded :func:`tick` is driven by
  ``schedules.autofire`` (the monitor's single in-process tick) and includes
  coalesced catch-up reporting for windows missed while the process was down.
* **OC-31 auto-reply / standing orders / heartbeat** — standing orders match
  an inbound event and auto-reply through the messaging outbox (the same
  confirmed draft->send transport the rest of Qubit uses); heartbeats are
  periodic liveness records on the same tick.
* **OC-D05-adjacent QA Lab** — :func:`qa_run` executes a bounded declarative
  scenario against the automation primitives and returns a structured report.
* **OC-D25 subagent yield handoff** — a bounded, redacted continuation
  context store (`yield_handoff`/`yield_resume`) with an enforced delegation
  depth so recursive handoff can never run away.

Safety invariants, all enforced in code and covered by hermetic tests:

* Disabled by default (no orders/flows/heartbeats until the user creates
  them). Creating or toggling any persistent object requires ``confirmed``.
* Every mutating/observable operation consults the shared durable
  emergency-stop latch first and fails closed with a typed code.
* The shared permission store is consulted before standing-order creation;
  a hard-deny capability or a ``deny`` mode refuses the write.
* Bodies, replies, contexts, and receipts pass through the centralized
  redaction boundary and are hard length-bounded before leaving the module.
* Auto-reply delivery goes through ``messaging.draft``/``messaging.send``
  (broker-governed, credential-gated, idempotent), never a private
  transport; a missing credential is a typed ``UNAVAILABLE``, never a
  fabricated ``SENT``.
* ``tick`` never raises into the monitor loop: every failure is a typed
  envelope.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = "qubit-automation/1"
SCHEMA_VERSION = 1
STORE_NAME = "automation.json"

MAX_ORDERS = 128
MAX_FLOWS = 64
MAX_HEARTBEATS = 64
MAX_EVENTS = 200
MAX_YIELDS = 128
MAX_RUNS = 200
MAX_NAME_LEN = 64
MAX_MATCH_LEN = 200
MAX_REPLY_LEN = 1000
MAX_CONTEXT_LEN = 4000
MAX_REASON_LEN = 200
MAX_STEPS = 20
MAX_DEPTH = 4
MAX_EVENT_TEXT = 2000
MAX_DETAIL_LEN = 300
MAX_RECIPIENT_LEN = 200
MAX_SCENARIOS = 64
MAX_DIRECTIVE_LEN = 2000

# The capability a standing order is authorized under in the shared store.
AUTO_REPLY_CAPABILITY = "automation.standing_order"

# User-configurable directive carried by a scheduled job ``task_text``. The
# existing ``schedules`` store/CLI/dashboard/voice are the ONE configuration
# surface; a directive lets a user configure/run a standing order, flow,
# heartbeat, QA scenario, or yield handoff on the single scheduler tick
# (``schedules.autofire`` -> ``run_due`` -> :func:`run_directive`).
DIRECTIVE_PREFIX = "automation:"

CONFIRM_CODE = "AUTOMATION_CONFIRM_REQUIRED"
ESTOP_CODE = "AUTOMATION_ESTOP"
BAD_INPUT_CODE = "AUTOMATION_BAD_INPUT"
EXISTS_CODE = "AUTOMATION_EXISTS"
NOT_FOUND_CODE = "AUTOMATION_NOT_FOUND"
CORRUPT_CODE = "AUTOMATION_CORRUPT"
PERMISSION_CODE = "AUTOMATION_PERMISSION_DENIED"
IO_CODE = "AUTOMATION_IO"
RUNTIME_CODE = "AUTOMATION_RUNTIME_DISABLED"
DEPTH_CODE = "AUTOMATION_YIELD_DEPTH"
CONSUMED_CODE = "AUTOMATION_YIELD_CONSUMED"
DIRECTIVE_CODE = "AUTOMATION_DIRECTIVE"

DELIVERY_SENT_CODE = "AUTOMATION_DELIVERY_SENT"
DELIVERY_UNAVAILABLE_CODE = "AUTOMATION_DELIVERY_UNAVAILABLE"
DELIVERY_FAILED_CODE = "AUTOMATION_DELIVERY_FAILED"
DELIVERY_ESTOP_CODE = "AUTOMATION_DELIVERY_ESTOP"

EVENT_KINDS = ("channel", "webhook", "internal")
STEP_ACTIONS = ("reply", "notify", "heartbeat")
QA_ACTIONS = ("check", "order", "flow", "heartbeat")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class AutomationError(Exception):
    """Typed automation failure (never a traceback at the boundary)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------- boundaries
def _now() -> float:
    return time.time()


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text or ""))
    except Exception:
        return "?"


def _bounded(text: Any, limit: int) -> str:
    return _redact(text)[:max(1, int(limit))]


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"automation.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as schedules/plugins (fail closed)."""
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "denied", "code": ESTOP_CODE,
            "detail": f"emergency stop latched; {operation} refused",
            "owner": "qubit-policy",
            "rollback": "automation store untouched; nothing to roll back"}


def _gate(cfg: Any, operation: str) -> None:
    if estop_engaged(cfg):
        raise AutomationError(ESTOP_CODE,
                              f"emergency stop latched; {operation} refused")


def _permission_gate(cfg: Any, capability: str) -> None:
    """Consult the shared permission store; hard-deny / deny mode refuses."""
    try:
        from .permissions import is_hard_deny_auto, permissions_store
    except Exception:
        return
    if is_hard_deny_auto(capability):
        raise AutomationError(PERMISSION_CODE,
                              f"capability {capability!r} is hard-denied")
    try:
        mode, scopes, _until = permissions_store(cfg).mode()
    except Exception:
        return
    if mode == "deny" and capability not in set(scopes or ()):
        raise AutomationError(
            PERMISSION_CODE,
            f"permission mode deny refuses {capability!r}")


# ------------------------------------------------------------------- store
def _store_path(cfg: Any) -> str:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    configured = get("automation", "store_path", default="") or ""
    if configured:
        return os.fspath(configured)
    return os.fspath(Path(cfg.state_dir()) / STORE_NAME)


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "orders": {}, "flows": {}, "heartbeats": {}, "events": [],
            "yields": {}, "runs": [], "scenarios": {}}


def _lock(path: str):
    import fcntl

    try:
        fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except OSError as exc:
        raise AutomationError(IO_CODE, f"cannot lock automation store: "
                                        f"{_bounded(exc, 120)}")


def _unlock(fd) -> None:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _load(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return _blank()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AutomationError(CORRUPT_CODE,
                              f"automation store unreadable ({_bounded(exc, 120)})")
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise AutomationError(CORRUPT_CODE, "automation store is malformed")
    base = _blank()
    for key in ("orders", "flows", "heartbeats", "yields", "scenarios"):
        if not isinstance(raw.get(key), dict):
            raw[key] = base[key]
    for key in ("events", "runs"):
        if not isinstance(raw.get(key), list):
            raw[key] = base[key]
    return raw


def _save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise AutomationError(IO_CODE, f"cannot create automation dir: "
                                        f"{_bounded(exc, 120)}")
    payload = json.dumps(data, sort_keys=True, indent=1).encode("utf-8")
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".automation.",
                                   suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise AutomationError(IO_CODE,
                              f"cannot persist automation store: "
                              f"{_bounded(exc, 120)}")


def _trim_runs(data: Dict[str, Any]) -> None:
    if len(data["runs"]) > MAX_RUNS:
        del data["runs"][:len(data["runs"]) - MAX_RUNS]


def _record_run(data: Dict[str, Any], record: Dict[str, Any]) -> None:
    data["runs"].append(record)
    _trim_runs(data)


# ------------------------------------------------------------- validation
def _check_name(name: Any) -> str:
    text = str(name or "").strip().lower()
    if not _NAME_RE.match(text):
        raise AutomationError(
            BAD_INPUT_CODE,
            "name must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    return text


def _check_text(value: Any, field: str, limit: int, *, required: bool) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise AutomationError(BAD_INPUT_CODE, f"{field} is required")
    if len(text) > limit:
        raise AutomationError(BAD_INPUT_CODE,
                              f"{field} exceeds {limit} chars")
    return text


def _match_order(pattern: str, text: str) -> bool:
    raw = str(pattern or "")
    hay = str(text or "")
    if raw.startswith("re:"):
        try:
            return re.search(raw[3:], hay, re.IGNORECASE) is not None
        except re.error:
            return False
    return raw.lower() in hay.lower()


# ---------------------------------------------------------- standing orders
def order_add(cfg: Any, name: str, *, match: str, reply: str, account: str,
              to: str, requested_by: str = "cli:user",
              confirmed: bool = False) -> Dict[str, Any]:
    """Create an auto-reply standing order (standing authorization).

    Creation is the confirming user's standing authorization for the target;
    only configuration is stored here (nothing is sent). Each reply still
    passes through the messaging broker at delivery time.
    """
    _gate(cfg, "standing order create")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "standing order requires explicit --confirm")
    clean_name = _check_name(name)
    clean_match = _check_text(match, "match", MAX_MATCH_LEN, required=True)
    clean_reply = _check_text(reply, "reply", MAX_REPLY_LEN, required=True)
    clean_account = _check_text(account, "account", MAX_NAME_LEN,
                                required=True)
    clean_to = _check_text(to, "recipient", MAX_RECIPIENT_LEN, required=True)
    _permission_gate(cfg, AUTO_REPLY_CAPABILITY)
    try:
        from . import messaging as M

        M.account_show(cfg, clean_account)
    except AutomationError:
        raise
    except Exception as exc:
        raise AutomationError(
            BAD_INPUT_CODE,
            f"delivery account {clean_account!r} unavailable "
            f"({_bounded(exc, 80)})") from None
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if clean_name in data["orders"]:
            raise AutomationError(EXISTS_CODE, f"order {clean_name!r} exists")
        if len(data["orders"]) >= MAX_ORDERS:
            raise AutomationError(EXISTS_CODE,
                                  f"order limit reached ({MAX_ORDERS})")
        rec = {"id": clean_name, "name": clean_name, "match": clean_match,
               "reply": clean_reply, "account": clean_account, "to": clean_to,
               "enabled": True, "created_at": _now(), "created_by": requested_by,
               "last_event_id": "", "last_run_at": 0.0, "count": 0}
        data["orders"][clean_name] = rec
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("order_add", requested_by,
           {"order": clean_name, "account": clean_account,
            "to": _bounded(clean_to, 80)})
    return {"state": "OK", "order": _public_order(rec)}


def _public_order(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "name": rec["name"],
            "match": _bounded(rec.get("match", ""), MAX_MATCH_LEN),
            "reply": _bounded(rec.get("reply", ""), MAX_REPLY_LEN),
            "account": rec.get("account", ""),
            "to": _bounded(rec.get("to", ""), MAX_RECIPIENT_LEN),
            "enabled": bool(rec.get("enabled")),
            "count": int(rec.get("count", 0)),
            "last_run_at": float(rec.get("last_run_at", 0.0))}


def order_list(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    with _open_locked(path) as data:
        orders = [_public_order(r)
                  for r in sorted(data["orders"].values(),
                                  key=lambda r: r.get("id", ""))]
    return {"state": "OK", "orders": orders}


def order_show(cfg: Any, name: str) -> Dict[str, Any]:
    clean = _check_name(name)
    path = _store_path(cfg)
    with _open_locked(path) as data:
        rec = data["orders"].get(clean)
        if rec is None:
            raise AutomationError(NOT_FOUND_CODE, f"no order {clean!r}")
        return {"state": "OK", "order": _public_order(rec)}


def order_set_enabled(cfg: Any, name: str, enabled: bool, *,
                      requested_by: str = "cli:user",
                      confirmed: bool = False) -> Dict[str, Any]:
    _gate(cfg, "standing order toggle")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "order toggle requires explicit --confirm")
    clean = _check_name(name)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        rec = data["orders"].get(clean)
        if rec is None:
            raise AutomationError(NOT_FOUND_CODE, f"no order {clean!r}")
        rec["enabled"] = bool(enabled)
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("order_toggle", requested_by,
           {"order": clean, "enabled": bool(enabled)})
    return {"state": "OK", "order": _public_order(rec)}


def order_remove(cfg: Any, name: str, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    _gate(cfg, "standing order remove")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "order remove requires explicit --confirm")
    clean = _check_name(name)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if clean not in data["orders"]:
            raise AutomationError(NOT_FOUND_CODE, f"no order {clean!r}")
        data["orders"].pop(clean, None)
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("order_remove", requested_by, {"order": clean})
    return {"state": "OK", "removed": clean}


# ------------------------------------------------------------- event queue
def queue_event(cfg: Any, *, kind: str, source: str, text: str,
                requested_by: str = "core:ingress",
                confirmed: bool = False) -> Dict[str, Any]:
    """Persist a bounded inbound event for the next automation tick."""
    _gate(cfg, "queue event")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "queueing an event requires explicit --confirm")
    clean_kind = str(kind or "").strip().lower()
    if clean_kind not in EVENT_KINDS:
        raise AutomationError(BAD_INPUT_CODE,
                              f"kind must be one of {EVENT_KINDS}")
    clean_source = _check_text(source, "source", MAX_NAME_LEN, required=True)
    clean_text = _check_text(text, "text", MAX_EVENT_TEXT, required=True)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if len(data["events"]) >= MAX_EVENTS:
            raise AutomationError(BAD_INPUT_CODE,
                                  f"event queue full ({MAX_EVENTS})")
        rec = {"id": "ev_" + uuid.uuid4().hex[:12], "kind": clean_kind,
               "source": clean_source, "text": clean_text,
               "created_at": _now(), "created_by": requested_by}
        data["events"].append(rec)
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("event_queue", requested_by,
           {"event": rec["id"], "kind": clean_kind})
    return {"state": "OK", "event": dict(rec)}


def _find_outbox(cfg: Any, key: str) -> str:
    try:
        from . import messaging as M

        for rec in M.list_msgs(cfg).get("messages", []):
            if rec.get("idempotency_key") == key:
                return str(rec.get("id", ""))
    except Exception:
        return ""
    return ""


def _deliver_reply(cfg: Any, order: Dict[str, Any], event_id: str,
                   deliver: Optional[Callable[..., Dict[str, Any]]]) -> Dict[str, Any]:
    """Hand one auto-reply to the messaging outbox (or an injected transport)."""
    body = f"[auto-reply {order['id']} {event_id}] {order['reply']}"
    body = _bounded(body, MAX_REPLY_LEN)
    if deliver is not None:
        try:
            result = deliver(cfg, order, body, event_id) or {}
        except Exception as exc:  # noqa: BLE001
            return {"state": "FAILED", "code": DELIVERY_FAILED_CODE,
                    "detail": _bounded(exc, MAX_DETAIL_LEN), "message_id": ""}
        state = str(result.get("state", "")).upper()
        if state in ("OK", "SENT"):
            return {"state": "SENT", "code": DELIVERY_SENT_CODE,
                    "detail": "delivered via injected transport",
                    "message_id": str(result.get("message_id", ""))}
        return {"state": "UNAVAILABLE", "code": str(
            result.get("code", DELIVERY_UNAVAILABLE_CODE)),
            "detail": _bounded(result.get("detail", "transport unavailable"),
                               MAX_DETAIL_LEN), "message_id": ""}
    try:
        from . import messaging as M
    except Exception as exc:  # noqa: BLE001
        return {"state": "UNAVAILABLE", "code": DELIVERY_UNAVAILABLE_CODE,
                "detail": _bounded(exc, MAX_DETAIL_LEN), "message_id": ""}
    idem = f"automation-order-{order['id']}-event-{event_id}"
    try:
        msg_id = M.draft(cfg, order["account"], order["to"], body,
                         idempotency_key=idem,
                         requested_by="automation:standing-order",
                         confirmed=True)["message"]["id"]
    except M.MessagingError as exc:
        if exc.code == M.EXISTS_CODE:
            msg_id = _find_outbox(cfg, idem)
            if not msg_id:
                return {"state": "UNAVAILABLE",
                        "code": DELIVERY_UNAVAILABLE_CODE,
                        "detail": "idempotency key held by unknown message",
                        "message_id": ""}
        else:
            unavailable = exc.code in (M.NO_CREDENTIAL_CODE, M.UNAVAILABLE_CODE,
                                       M.NO_TRANSPORT_CODE, M.NOT_PAIRED_CODE,
                                       M.ESTOP_CODE)
            return {"state": "UNAVAILABLE" if unavailable else "FAILED",
                    "code": exc.code if unavailable else DELIVERY_FAILED_CODE,
                    "detail": _bounded(exc.detail, MAX_DETAIL_LEN),
                    "message_id": ""}
    except Exception as exc:  # noqa: BLE001
        return {"state": "FAILED", "code": DELIVERY_FAILED_CODE,
                "detail": _bounded(exc, MAX_DETAIL_LEN), "message_id": ""}
    try:
        sent = M.send(cfg, msg_id, requested_by="automation:standing-order",
                      confirmed=True)
    except M.MessagingError as exc:
        unavailable = exc.code in (M.NO_CREDENTIAL_CODE, M.UNAVAILABLE_CODE,
                                   M.NO_TRANSPORT_CODE, M.NOT_PAIRED_CODE,
                                   M.ESTOP_CODE)
        return {"state": "UNAVAILABLE" if unavailable else "FAILED",
                "code": exc.code if unavailable else DELIVERY_FAILED_CODE,
                "detail": _bounded(exc.detail, MAX_DETAIL_LEN),
                "message_id": msg_id}
    except Exception as exc:  # noqa: BLE001
        return {"state": "FAILED", "code": DELIVERY_FAILED_CODE,
                "detail": _bounded(exc, MAX_DETAIL_LEN), "message_id": msg_id}
    confirmed = (isinstance(sent, dict)
                 and sent.get("message", {}).get("state") == M.SENT)
    if not confirmed:
        return {"state": "FAILED", "code": DELIVERY_FAILED_CODE,
                "detail": "transport did not confirm the send",
                "message_id": msg_id}
    return {"state": "SENT", "code": DELIVERY_SENT_CODE,
            "detail": f"delivered via account {order['account']}",
            "message_id": msg_id}


def process_events(cfg: Any, *, limit: int = 20,
                   deliver: Optional[Callable[..., Dict[str, Any]]] = None,
                   estop: Optional[Callable[[], bool]] = None,
                   now: Optional[float] = None) -> Dict[str, Any]:
    """Drain queued events through matching enabled standing orders.

    Bounded and fail-closed: a latched e-stop returns a typed denial without
    consuming the queue. Each event is matched once, removed on completion,
    and every reply is idempotent per (order, event). A delivery that cannot
    happen is a typed ``UNAVAILABLE`` receipt, never a fabricated ``SENT``.
    """
    stop = estop or (lambda: estop_engaged(cfg))
    if stop():
        denied = refuse_estop("process events")
        denied["processed"] = 0
        denied["results"] = []
        return denied
    moment = now if now is not None else _now()
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        bound = max(1, min(int(limit or 20), MAX_EVENTS))
        pending = list(data["events"])[:bound]
        remaining = list(data["events"])[bound:]
        results: List[Dict[str, Any]] = []
        for event in pending:
            matched = [r for r in data["orders"].values()
                       if r.get("enabled")
                       and _match_order(r.get("match", ""), event["text"])]
            if not matched:
                results.append({"event_id": event["id"], "order_id": "",
                                "state": "NONE", "code": "",
                                "detail": "no standing order matched",
                                "message_id": ""})
                continue
            for order in matched:
                out = _deliver_reply(cfg, order, event["id"], deliver)
                order["count"] = int(order.get("count", 0)) + 1
                order["last_run_at"] = moment
                order["last_event_id"] = event["id"]
                _record_run(data, {
                    "kind": "auto_reply", "order_id": order["id"],
                    "event_id": event["id"], "state": out["state"],
                    "code": out["code"], "detail": out["detail"],
                    "ts": moment})
                _audit("auto_reply", "automation:tick",
                       {"order": order["id"], "event": event["id"],
                        "state": out["state"], "code": out["code"]})
                results.append({"event_id": event["id"],
                                "order_id": order["id"],
                                "state": out["state"], "code": out["code"],
                                "detail": out["detail"],
                                "message_id": out["message_id"]})
        data["events"] = remaining
        _save(path, data)
    finally:
        _unlock(fd)
    return {"state": "OK", "processed": len(results), "results": results,
            "actor": "automation:tick"}


# --------------------------------------------------------- heartbeat / tick
def heartbeat_add(cfg: Any, name: str, interval_min: int, *, note: str = "",
                  requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Create a periodic heartbeat record (runs on the shared schedule tick)."""
    _gate(cfg, "heartbeat create")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "heartbeat requires explicit --confirm")
    clean = _check_name(name)
    try:
        interval = int(interval_min)
    except (TypeError, ValueError):
        raise AutomationError(BAD_INPUT_CODE, "interval_min must be an int")
    if not 1 <= interval <= 10080:
        raise AutomationError(BAD_INPUT_CODE,
                              "interval_min must be 1..10080")
    clean_note = _check_text(note, "note", MAX_DETAIL_LEN, required=False)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if clean in data["heartbeats"]:
            raise AutomationError(EXISTS_CODE, f"heartbeat {clean!r} exists")
        if len(data["heartbeats"]) >= MAX_HEARTBEATS:
            raise AutomationError(EXISTS_CODE,
                                  f"heartbeat limit reached ({MAX_HEARTBEATS})")
        rec = {"id": clean, "name": clean, "interval_min": interval,
               "note": clean_note, "enabled": True, "count": 0,
               "last_at": 0.0, "created_at": _now(), "created_by": requested_by}
        data["heartbeats"][clean] = rec
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("heartbeat_add", requested_by, {"heartbeat": clean,
                                           "interval_min": str(interval)})
    return {"state": "OK", "heartbeat": _public_heartbeat(rec)}


def _public_heartbeat(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "name": rec["name"],
            "interval_min": int(rec.get("interval_min", 0)),
            "note": _bounded(rec.get("note", ""), MAX_DETAIL_LEN),
            "enabled": bool(rec.get("enabled")),
            "count": int(rec.get("count", 0)),
            "last_at": float(rec.get("last_at", 0.0))}


def heartbeat_list(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    with _open_locked(path) as data:
        items = [_public_heartbeat(r)
                 for r in sorted(data["heartbeats"].values(),
                                 key=lambda r: r.get("id", ""))]
    return {"state": "OK", "heartbeats": items}


def heartbeat_due(cfg: Any, *, now: Optional[float] = None) -> List[str]:
    """Read-only: ids of heartbeats whose interval has elapsed."""
    moment = now if now is not None else _now()
    path = _store_path(cfg)
    with _open_locked(path) as data:
        due = []
        for rec in data["heartbeats"].values():
            if not rec.get("enabled"):
                continue
            base = float(rec.get("last_at") or rec.get("created_at") or 0.0)
            if moment >= base + int(rec.get("interval_min", 0)) * 60.0:
                due.append(str(rec["id"]))
        return sorted(due)


def _heartbeat_tick(cfg: Any, data: Dict[str, Any], moment: float) -> int:
    fired = 0
    for rec in data["heartbeats"].values():
        if not rec.get("enabled"):
            continue
        base = float(rec.get("last_at") or rec.get("created_at") or 0.0)
        if moment < base + int(rec.get("interval_min", 0)) * 60.0:
            continue
        rec["last_at"] = moment
        rec["count"] = int(rec.get("count", 0)) + 1
        fired += 1
        _record_run(data, {"kind": "heartbeat", "order_id": rec["id"],
                           "event_id": "", "state": "OK",
                           "code": "AUTOMATION_HEARTBEAT",
                           "detail": _bounded(rec.get("note", ""), 120),
                           "ts": moment})
        _audit("heartbeat", "automation:tick",
               {"heartbeat": rec["id"], "count": str(rec["count"])})
    return fired


def tick(cfg: Any, *, now: Optional[float] = None,
         deliver: Optional[Callable[..., Dict[str, Any]]] = None,
         estop: Optional[Callable[[], bool]] = None,
         limit: int = 20) -> Dict[str, Any]:
    """The ONE bounded automation tick, called by ``schedules.autofire``.

    Runs due heartbeats and drains queued auto-reply events on the shared
    monitor cadence. Adds no timer/thread/authority of its own, refuses
    everything while the durable e-stop is latched, and NEVER raises into the
    monitor loop (every failure is a typed envelope).
    """
    stop = estop or (lambda: estop_engaged(cfg))
    if stop():
        denied = refuse_estop("automation tick")
        denied.update({"heartbeats": 0, "processed": 0, "results": []})
        return denied
    moment = now if now is not None else _now()
    path = _store_path(cfg)
    heartbeats = 0
    try:
        fd = _lock(path)
        try:
            data = _load(path)
            heartbeats = _heartbeat_tick(cfg, data, moment)
            _save(path, data)
        finally:
            _unlock(fd)
    except AutomationError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": _bounded(exc.detail, MAX_DETAIL_LEN),
                "heartbeats": 0, "processed": 0, "results": []}
    except Exception as exc:  # noqa: BLE001 — never raise into the monitor
        return {"state": "FAILED", "code": RUNTIME_CODE,
                "detail": _bounded(exc, MAX_DETAIL_LEN),
                "heartbeats": 0, "processed": 0, "results": []}
    events = process_events(cfg, limit=limit, deliver=deliver, estop=stop,
                            now=moment)
    pending = yield_pending(cfg)
    return {"state": "OK", "heartbeats": heartbeats,
            "processed": events.get("processed", 0),
            "results": events.get("results", []),
            "pending_yields": len(pending.get("pending", [])),
            "actor": "automation:tick"}


# ------------------------------------------------------------------- flows
def flow_add(cfg: Any, name: str, steps: Any, *,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Create a bounded declarative flow (ordered automation steps)."""
    _gate(cfg, "flow create")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "flow create requires explicit --confirm")
    clean = _check_name(name)
    if not isinstance(steps, (list, tuple)) or not steps:
        raise AutomationError(BAD_INPUT_CODE, "steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise AutomationError(BAD_INPUT_CODE,
                              f"flow exceeds {MAX_STEPS} steps")
    cleaned: List[Dict[str, Any]] = []
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise AutomationError(BAD_INPUT_CODE,
                                  f"step {index} must be an object")
        action = str(raw.get("action", "")).strip().lower()
        if action not in STEP_ACTIONS:
            raise AutomationError(BAD_INPUT_CODE,
                                  f"step {index} action must be one of "
                                  f"{STEP_ACTIONS}")
        step = {"action": action}
        if action == "reply":
            step["account"] = _check_text(raw.get("account"), "account",
                                          MAX_NAME_LEN, required=True)
            step["to"] = _check_text(raw.get("to"), "recipient",
                                     MAX_RECIPIENT_LEN, required=True)
            step["body"] = _check_text(raw.get("body"), "body",
                                       MAX_REPLY_LEN, required=True)
        elif action == "notify":
            step["text"] = _check_text(raw.get("text"), "text",
                                       MAX_DETAIL_LEN, required=True)
        cleaned.append(step)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if clean in data["flows"]:
            raise AutomationError(EXISTS_CODE, f"flow {clean!r} exists")
        if len(data["flows"]) >= MAX_FLOWS:
            raise AutomationError(EXISTS_CODE,
                                  f"flow limit reached ({MAX_FLOWS})")
        data["flows"][clean] = {"id": clean, "name": clean, "steps": cleaned,
                                "created_at": _now(), "created_by": requested_by,
                                "count": 0, "last_run_at": 0.0}
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("flow_add", requested_by, {"flow": clean,
                                      "steps": str(len(cleaned))})
    return {"state": "OK", "flow": _public_flow(data["flows"][clean])}


def _public_flow(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "name": rec["name"],
            "steps": len(rec.get("steps", [])),
            "count": int(rec.get("count", 0)),
            "last_run_at": float(rec.get("last_run_at", 0.0))}


def flow_list(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    with _open_locked(path) as data:
        return {"state": "OK", "flows": [
            _public_flow(r) for r in sorted(data["flows"].values(),
                                            key=lambda r: r.get("id", ""))]}


def _step_execute(cfg: Any, step: Dict[str, Any],
                  deliver: Optional[Callable[..., Dict[str, Any]]]) -> Dict[str, Any]:
    action = step["action"]
    if action == "reply":
        order = {"id": "flow", "account": step["account"], "to": step["to"],
                 "reply": step["body"]}
        return _deliver_reply(cfg, order, "flow", deliver)
    if action == "notify":
        return {"state": "OK", "code": "AUTOMATION_NOTIFY",
                "detail": _bounded(step.get("text", ""), MAX_DETAIL_LEN),
                "message_id": ""}
    return {"state": "OK", "code": "AUTOMATION_HEARTBEAT", "detail": "",
            "message_id": ""}


def flow_run(cfg: Any, name: str, *, deliver: Optional[Callable[..., Dict[str, Any]]] = None,
             estop: Optional[Callable[[], bool]] = None,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Run a flow step-by-step (observable -> explicit confirmation)."""
    stop = estop or (lambda: estop_engaged(cfg))
    if stop():
        return refuse_estop("flow run")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "flow run requires explicit --confirm")
    clean = _check_name(name)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        flow = data["flows"].get(clean)
        if flow is None:
            raise AutomationError(NOT_FOUND_CODE, f"no flow {clean!r}")
        steps = list(flow.get("steps", []))
        flow["count"] = int(flow.get("count", 0)) + 1
        flow["last_run_at"] = _now()
        _save(path, data)
    finally:
        _unlock(fd)
    results = []
    for step in steps:
        if stop():
            results.append({"action": step["action"], "state": "UNAVAILABLE",
                            "code": DELIVERY_ESTOP_CODE,
                            "detail": "emergency stop latched mid-flow"})
            break
        out = _step_execute(cfg, step, deliver)
        _audit("flow_step", requested_by,
               {"flow": clean, "action": step["action"],
                "state": out["state"]})
        results.append({"action": step["action"], "state": out["state"],
                        "code": out["code"],
                        "detail": _bounded(out.get("detail", ""),
                                           MAX_DETAIL_LEN)})
    failed = any(r["state"] in ("FAILED", "UNAVAILABLE") for r in results)
    return {"state": "PARTIAL" if failed else "OK",
            "flow": clean, "steps": len(steps), "results": results}


# ----------------------------------------------------------------- QA lab
def qa_run(cfg: Any, scenario: Any, *,
           executor: Optional[Callable[[Dict[str, Any]], Any]] = None,
           estop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Run a bounded declarative QA scenario over the automation primitives.

    Each step is ``{"name", "action", "expect"}`` with an action in
    :data:`QA_ACTIONS`. A step "passes" when its resolved state/code matches
    the optional ``expect`` (string) or, without ``expect``, when it is not
    ``FAILED``/``UNAVAILABLE``. Returns a structured report; never raises for
    a failing step and refuses the whole run while the e-stop is latched.
    """
    stop = estop or (lambda: estop_engaged(cfg))
    if stop():
        denied = refuse_estop("qa run")
        denied.update({"passed": 0, "failed": 0, "total": 0, "steps": []})
        return denied
    if not isinstance(scenario, (list, tuple)) or not scenario:
        raise AutomationError(BAD_INPUT_CODE,
                              "scenario must be a non-empty list")
    if len(scenario) > MAX_STEPS:
        raise AutomationError(BAD_INPUT_CODE,
                              f"scenario exceeds {MAX_STEPS} steps")
    report: List[Dict[str, Any]] = []
    passed = failed = 0
    for index, raw in enumerate(scenario):
        if not isinstance(raw, dict):
            raise AutomationError(BAD_INPUT_CODE,
                                  f"scenario step {index} must be an object")
        step = dict(raw)
        action = str(step.get("action", "")).strip().lower()
        name = _bounded(step.get("name") or f"step-{index + 1}",
                        MAX_NAME_LEN)
        if action not in QA_ACTIONS and executor is None:
            raise AutomationError(BAD_INPUT_CODE,
                                  f"scenario step {index} action must be one "
                                  f"of {QA_ACTIONS}")
        if executor is not None:
            try:
                result = executor(step)
            except Exception as exc:  # noqa: BLE001
                result = {"state": "FAILED", "code": "QA_EXECUTOR_ERROR",
                          "detail": _bounded(exc, MAX_DETAIL_LEN)}
        else:
            result = _qa_default(cfg, step, action, stop)
        state = str(result.get("state", "FAILED"))
        expect = step.get("expect")
        ok = (state == expect) if expect is not None else \
            state not in ("FAILED", "UNAVAILABLE")
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        report.append({"name": name, "action": action,
                       "state": state, "ok": ok,
                       "code": str(result.get("code", ""))[:80],
                       "detail": _bounded(result.get("detail", ""),
                                          MAX_DETAIL_LEN)})
        if stop():
            report.append({"name": "e-stop", "action": "stop",
                           "state": "UNAVAILABLE", "ok": False,
                           "code": DELIVERY_ESTOP_CODE,
                           "detail": "emergency stop latched mid-scenario"})
            failed += 1
            break
    _audit("qa_run", "automation:qa",
           {"total": str(len(report)), "passed": str(passed),
            "failed": str(failed)})
    return {"state": "OK" if failed == 0 else "FAILED", "passed": passed,
            "failed": failed, "total": len(report), "steps": report}


def _qa_default(cfg: Any, step: Dict[str, Any], action: str,
                stop: Callable[[], bool]) -> Dict[str, Any]:
    if action == "check":
        return {"state": "OK" if bool(step.get("expect", True)) else "FAILED",
                "code": "QA_CHECK", "detail": "deterministic check"}
    if action == "order":
        queued = queue_event(cfg, kind="internal",
                             source="automation:qa",
                             text=_check_text(step.get("text", "qa"),
                                              "text", MAX_EVENT_TEXT,
                                              required=True),
                             requested_by="automation:qa", confirmed=True)
        out = process_events(cfg, estop=stop)
        matched = [r for r in out.get("results", [])
                   if r.get("event_id") == queued["event"]["id"]
                   and r.get("state") != "NONE"]
        return {"state": "OK" if matched else "NONE",
                "code": "QA_ORDER",
                "detail": f"{len(matched)} standing order(s) matched"}
    if action == "flow":
        return flow_run(cfg, str(step.get("flow", "")), estop=stop,
                        requested_by="automation:qa", confirmed=True)
    return {"state": "OK", "code": "QA_HEARTBEAT", "detail": ""}


def _check_scenario(scenario: Any) -> List[Dict[str, Any]]:
    """Validate a declarative scenario without executing it (bounded)."""
    if not isinstance(scenario, (list, tuple)) or not scenario:
        raise AutomationError(BAD_INPUT_CODE,
                              "scenario must be a non-empty list")
    if len(scenario) > MAX_STEPS:
        raise AutomationError(BAD_INPUT_CODE,
                              f"scenario exceeds {MAX_STEPS} steps")
    cleaned: List[Dict[str, Any]] = []
    for index, raw in enumerate(scenario):
        if not isinstance(raw, dict):
            raise AutomationError(BAD_INPUT_CODE,
                                  f"scenario step {index} must be an object")
        action = str(raw.get("action", "")).strip().lower()
        if action not in QA_ACTIONS:
            raise AutomationError(BAD_INPUT_CODE,
                                  f"scenario step {index} action must be one "
                                  f"of {QA_ACTIONS}")
        step = {"action": action,
                "name": _check_text(raw.get("name") or f"step-{index + 1}",
                                    "name", MAX_NAME_LEN, required=False)}
        if action == "check":
            if "expect" in raw:
                step["expect"] = raw["expect"]
        elif action == "order":
            step["text"] = _check_text(raw.get("text", "qa"), "text",
                                       MAX_EVENT_TEXT, required=True)
        elif action == "flow":
            step["flow"] = _check_text(raw.get("flow"), "flow", MAX_NAME_LEN,
                                       required=True)
        cleaned.append(step)
    return cleaned


def qa_scenario_add(cfg: Any, name: str, scenario: Any, *,
                    requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Persist a named, bounded QA scenario for the one scheduler to run."""
    _gate(cfg, "qa scenario create")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "qa scenario create requires explicit --confirm")
    clean = _check_name(name)
    cleaned = _check_scenario(scenario)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if clean in data["scenarios"]:
            raise AutomationError(EXISTS_CODE, f"scenario {clean!r} exists")
        if len(data["scenarios"]) >= MAX_SCENARIOS:
            raise AutomationError(EXISTS_CODE,
                                  f"scenario limit reached ({MAX_SCENARIOS})")
        rec = {"id": clean, "name": clean, "steps": cleaned,
               "created_at": _now(), "created_by": requested_by,
               "count": 0, "last_run_at": 0.0, "last_state": ""}
        data["scenarios"][clean] = rec
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("qa_scenario_add", requested_by,
           {"scenario": clean, "steps": str(len(cleaned))})
    return {"state": "OK", "scenario": _public_scenario(rec)}


def _public_scenario(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "name": rec["name"],
            "steps": len(rec.get("steps", [])),
            "count": int(rec.get("count", 0)),
            "last_state": str(rec.get("last_state", "")),
            "last_run_at": float(rec.get("last_run_at", 0.0))}


def qa_scenario_list(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    with _open_locked(path) as data:
        return {"state": "OK", "scenarios": [
            _public_scenario(r) for r in sorted(
                data["scenarios"].values(), key=lambda r: r.get("id", ""))]}


def qa_scenario_run(cfg: Any, name: str, *,
                    executor: Optional[Callable[[Dict[str, Any]], Any]] = None,
                    estop: Optional[Callable[[], bool]] = None,
                    requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Run a persisted QA scenario through :func:`qa_run` (confirmed)."""
    stop = estop or (lambda: estop_engaged(cfg))
    if stop():
        denied = refuse_estop("qa scenario run")
        denied.update({"passed": 0, "failed": 0, "total": 0, "steps": []})
        return denied
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "qa scenario run requires explicit --confirm")
    clean = _check_name(name)
    path = _store_path(cfg)
    with _open_locked(path) as data:
        rec = data["scenarios"].get(clean)
        if rec is None:
            raise AutomationError(NOT_FOUND_CODE, f"no scenario {clean!r}")
        scenario = list(rec.get("steps", []))
    report = qa_run(cfg, scenario, executor=executor, estop=stop)
    fd = _lock(path)
    try:
        data = _load(path)
        live = data["scenarios"].get(clean)
        if live is not None:
            live["count"] = int(live.get("count", 0)) + 1
            live["last_run_at"] = _now()
            live["last_state"] = str(report.get("state", ""))
            _save(path, data)
    finally:
        _unlock(fd)
    report["scenario"] = clean
    return report


# -------------------------------------------------------- subagent yields
def yield_handoff(cfg: Any, session: str, *, reason: str, context: str,
                  depth: int = 0, requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Persist a bounded, redacted continuation context for a delegated turn.

    This is the Qubit-safe equivalent of an Qubit ``sessions_yield``
    handoff: recursive delegation is bounded by :data:`MAX_DEPTH`, the
    context is size-limited and redacted, and only a confirmed caller may
    create or consume one. The emergency stop refuses both directions.
    """
    _gate(cfg, "yield handoff")
    if not confirmed:
        raise AutomationError(CONFIRM_CODE,
                              "yield handoff requires explicit --confirm")
    clean_session = _check_text(session, "session", MAX_NAME_LEN,
                                required=True)
    try:
        level = int(depth)
    except (TypeError, ValueError):
        raise AutomationError(BAD_INPUT_CODE, "depth must be an int")
    if level < 0:
        raise AutomationError(BAD_INPUT_CODE, "depth must be >= 0")
    if level > MAX_DEPTH:
        raise AutomationError(
            DEPTH_CODE,
            f"delegation depth {level} exceeds MAX_DEPTH={MAX_DEPTH}")
    clean_reason = _check_text(reason, "reason", MAX_REASON_LEN,
                               required=True)
    clean_context = _check_text(context, "context", MAX_CONTEXT_LEN,
                                required=True)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if clean_session in data["yields"] and \
                not data["yields"][clean_session].get("consumed"):
            raise AutomationError(
                EXISTS_CODE,
                f"session {clean_session!r} already has a pending handoff")
        if len(data["yields"]) >= MAX_YIELDS:
            raise AutomationError(EXISTS_CODE,
                                  f"yield limit reached ({MAX_YIELDS})")
        rec = {"handoff_id": "yh_" + uuid.uuid4().hex[:12],
               "session": clean_session, "reason": clean_reason,
               "context": _redact(clean_context)[:MAX_CONTEXT_LEN],
               "depth": level, "consumed": False, "created_at": _now(),
               "created_by": requested_by}
        data["yields"][clean_session] = rec
        _record_run(data, {"kind": "yield", "order_id": clean_session,
                           "event_id": rec["handoff_id"], "state": "OK",
                           "code": "AUTOMATION_YIELD",
                           "detail": clean_reason, "ts": rec["created_at"]})
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("yield", requested_by,
           {"session": clean_session, "handoff": rec["handoff_id"],
            "depth": str(level)})
    return {"state": "OK", "handoff_id": rec["handoff_id"],
            "session": clean_session, "depth": level, "consumed": False}


def yield_resume(cfg: Any, session: str, *, consume: bool = False,
                 requested_by: str = "cli:user") -> Dict[str, Any]:
    """Read (and optionally consume) a pending handoff for a session."""
    _gate(cfg, "yield resume")
    clean_session = _check_text(session, "session", MAX_NAME_LEN,
                                required=True)
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        rec = data["yields"].get(clean_session)
        if rec is None:
            raise AutomationError(NOT_FOUND_CODE,
                                  f"no handoff for session {clean_session!r}")
        if rec.get("consumed"):
            raise AutomationError(CONSUMED_CODE,
                                  f"handoff for {clean_session!r} was consumed")
        out = {"state": "OK", "handoff_id": rec["handoff_id"],
               "session": clean_session,
               "reason": _bounded(rec.get("reason", ""), MAX_REASON_LEN),
               "context": _bounded(rec.get("context", ""), MAX_CONTEXT_LEN),
               "depth": int(rec.get("depth", 0)),
               "created_at": rec.get("created_at")}
        if consume:
            rec["consumed"] = True
            rec["consumed_at"] = _now()
            _save(path, data)
            out["consumed"] = True
            _audit("yield_consume", requested_by,
                   {"session": clean_session, "handoff": rec["handoff_id"]})
        else:
            out["consumed"] = False
    finally:
        _unlock(fd)
    return out


def yield_pending(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    with _open_locked(path) as data:
        items = [{"handoff_id": r["handoff_id"], "session": r["session"],
                  "reason": _bounded(r.get("reason", ""), MAX_REASON_LEN),
                  "depth": int(r.get("depth", 0)),
                  "created_at": r.get("created_at")}
                 for r in data["yields"].values()
                 if not r.get("consumed")]
    return {"state": "OK", "pending": items}


# ------------------------------------------------- scheduler directives
def is_directive(task_text: Any) -> bool:
    """True when a scheduled job's task text is an automation directive."""
    return str(task_text or "").strip().lower().startswith(DIRECTIVE_PREFIX)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _parse_directive(task_text: Any) -> tuple[str, Dict[str, str]]:
    body = str(task_text or "").strip()
    body = body[len(DIRECTIVE_PREFIX):].strip()
    if not body:
        raise AutomationError(DIRECTIVE_CODE, "empty automation directive")
    if len(body) > MAX_DIRECTIVE_LEN:
        raise AutomationError(DIRECTIVE_CODE, "automation directive too long")
    try:
        tokens = shlex.split(body)
    except ValueError as exc:
        raise AutomationError(DIRECTIVE_CODE,
                              f"unparseable directive ({_bounded(exc, 80)})")
    if not tokens:
        raise AutomationError(DIRECTIVE_CODE, "empty automation directive")
    verb = tokens[0].strip().lower()
    kwargs: Dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise AutomationError(
                DIRECTIVE_CODE, f"directive argument {token!r} must be key=value")
        key, value = token.split("=", 1)
        kwargs[key.strip().lower()] = value
    return verb, kwargs


def _dispatch_directive(cfg: Any, verb: str, kw: Dict[str, str], *,
                        deliver: Optional[Callable[..., Dict[str, Any]]],
                        stop: Callable[[], bool],
                        requested_by: str) -> Dict[str, Any]:
    alias = verb.replace("-", "_")
    if alias in ("status", "automation_status"):
        return status(cfg)
    if alias in ("order.add", "order_add"):
        return order_add(cfg, kw.get("name", ""), match=kw.get("match", ""),
                         reply=kw.get("reply", ""),
                         account=kw.get("account", ""), to=kw.get("to", ""),
                         requested_by=requested_by, confirmed=True)
    if alias in ("order.enable", "order_enable", "order.disable",
                 "order_disable"):
        enabled = "disable" not in alias
        return order_set_enabled(cfg, kw.get("name", ""), enabled,
                                 requested_by=requested_by, confirmed=True)
    if alias in ("order.remove", "order_remove"):
        return order_remove(cfg, kw.get("name", ""),
                            requested_by=requested_by, confirmed=True)
    if alias in ("order.list", "order_list"):
        return order_list(cfg)
    if alias in ("heartbeat.add", "heartbeat_add"):
        try:
            every = int(kw.get("every", kw.get("interval_min", "0")))
        except (TypeError, ValueError):
            raise AutomationError(BAD_INPUT_CODE, "every must be an int")
        return heartbeat_add(cfg, kw.get("name", ""), every,
                             note=kw.get("note", ""),
                             requested_by=requested_by, confirmed=True)
    if alias in ("heartbeat.list", "heartbeat_list"):
        return heartbeat_list(cfg)
    if alias in ("flow.add", "flow_add"):
        raw = kw.get("steps", "")
        try:
            steps = json.loads(raw)
        except (TypeError, ValueError):
            raise AutomationError(BAD_INPUT_CODE,
                                  "steps must be a JSON array")
        return flow_add(cfg, kw.get("name", ""), steps,
                        requested_by=requested_by, confirmed=True)
    if alias in ("flow.run", "flow_run"):
        return flow_run(cfg, kw.get("name", ""), deliver=deliver, estop=stop,
                        requested_by=requested_by, confirmed=True)
    if alias in ("flow.list", "flow_list"):
        return flow_list(cfg)
    if alias in ("qa.add", "qa_add", "qa.scenario_add", "qa_scenario_add"):
        raw = kw.get("scenario", "")
        try:
            scenario = json.loads(raw)
        except (TypeError, ValueError):
            raise AutomationError(BAD_INPUT_CODE,
                                  "scenario must be a JSON array")
        return qa_scenario_add(cfg, kw.get("name", ""), scenario,
                               requested_by=requested_by, confirmed=True)
    if alias in ("qa.run", "qa_run", "qa.scenario_run", "qa_scenario_run"):
        named = kw.get("name", "")
        if named:
            return qa_scenario_run(cfg, named, estop=stop,
                                   requested_by=requested_by, confirmed=True)
        raw = kw.get("scenario", "")
        try:
            scenario = json.loads(raw)
        except (TypeError, ValueError):
            raise AutomationError(BAD_INPUT_CODE,
                                  "scenario must be a JSON array")
        return qa_run(cfg, scenario, estop=stop)
    if alias in ("qa.list", "qa_list", "qa.scenario_list",
                 "qa_scenario_list"):
        return qa_scenario_list(cfg)
    if alias in ("yield.handoff", "yield_handoff"):
        try:
            depth = int(kw.get("depth", "0") or 0)
        except (TypeError, ValueError):
            raise AutomationError(BAD_INPUT_CODE, "depth must be an int")
        return yield_handoff(cfg, kw.get("session", ""),
                             reason=kw.get("reason", ""),
                             context=kw.get("context", ""), depth=depth,
                             requested_by=requested_by, confirmed=True)
    if alias in ("yield.resume", "yield_resume"):
        return yield_resume(cfg, kw.get("session", ""),
                            consume=_truthy(kw.get("consume", "")),
                            requested_by=requested_by)
    if alias in ("yield.pending", "yield_pending"):
        return yield_pending(cfg)
    raise AutomationError(DIRECTIVE_CODE, f"unknown directive {verb!r}")


def run_directive(cfg: Any, task_text: Any, *,
                  estop: Optional[Callable[[], bool]] = None,
                  deliver: Optional[Callable[..., Dict[str, Any]]] = None,
                  now: Optional[float] = None,
                  requested_by: str = "automation:directive",
                  ) -> Dict[str, Any]:
    """Execute one user directive on the shared scheduler tick (never raises).

    The directive is a bounded ``automation: <verb> key=value ... `` string
    carried by a scheduled job's ``task_text``. The user authored and
    confirmed the job through the existing ``schedules`` store, so a directive
    is a pre-authorized configuration/run request; it still consults the
    emergency stop and every underlying operation keeps its own gates.
    """
    stop = estop or (lambda: estop_engaged(cfg))
    if stop():
        denied = refuse_estop("automation directive")
        denied["directive"] = ""
        return denied
    try:
        verb, kw = _parse_directive(task_text)
        out = _dispatch_directive(cfg, verb, kw, deliver=deliver, stop=stop,
                                  requested_by=requested_by)
    except AutomationError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": _bounded(exc.detail, MAX_DETAIL_LEN),
                "directive": _bounded(task_text, MAX_DIRECTIVE_LEN)}
    except Exception as exc:  # noqa: BLE001 — never raise into the scheduler
        return {"state": "FAILED", "code": RUNTIME_CODE,
                "detail": _bounded(exc, MAX_DETAIL_LEN),
                "directive": _bounded(task_text, MAX_DIRECTIVE_LEN)}
    if not isinstance(out, dict):
        out = {"state": "FAILED", "code": RUNTIME_CODE,
               "detail": "directive produced no result"}
    out.setdefault("state", "OK")
    out["directive"] = verb
    return out


# ------------------------------------------------------------------ status
class _LockedStore:
    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def __enter__(self) -> Dict[str, Any]:
        self.fd = _lock(self.path)
        return _load(self.path)

    def __exit__(self, *exc) -> bool:
        if self.fd is not None:
            _unlock(self.fd)
        return False


def _open_locked(path: str) -> _LockedStore:
    return _LockedStore(path)


def status(cfg: Any) -> Dict[str, Any]:
    """Read-only automation report (counts, estop, store path)."""
    path = _store_path(cfg)
    try:
        with _open_locked(path) as data:
            counts = {"orders": len(data["orders"]),
                      "flows": len(data["flows"]),
                      "heartbeats": len(data["heartbeats"]),
                      "events": len(data["events"]),
                      "scenarios": len(data["scenarios"]),
                      "yields": sum(1 for r in data["yields"].values()
                                    if not r.get("consumed")),
                      "runs": len(data["runs"])}
    except AutomationError as exc:
        return {"state": "FAILED", "schema": SCHEMA, "code": exc.code,
                "detail": _bounded(exc.detail, MAX_DETAIL_LEN),
                "store": path, "estop": estop_engaged(cfg)}
    return {"state": "OK", "schema": SCHEMA, "counts": counts,
            "step_actions": list(STEP_ACTIONS), "qa_actions": list(QA_ACTIONS),
            "max_depth": MAX_DEPTH, "store": path, "estop": estop_engaged(cfg)}
