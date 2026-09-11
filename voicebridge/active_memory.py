"""Active-recall memory layer (OpenClaw v2026.9.4 delta D4).

Adds the missing active-recall resolution/selection surface over the existing
Packet 5B :class:`voicebridge.memory.store.MemoryStore` (and, when configured,
the Packet 8M :class:`SemanticMemory` adapter): a bounded session policy, an
active recall with scoring/threshold/dedupe/cap, a resolve step that produces
the actual context block(s) for prompt injection, and a confirmation-gated
escalation.

Design guarantees (all enforced by code and the hermetic test suite):

* **Disabled by default.** Active recall only runs when the persisted policy is
  explicitly enabled; a disabled policy gates every read/write honestly with a
  typed code, never silently falling through to raw store output.
* **Bounded.** ``min_score`` (0..1), ``max_items`` (1..50), and the resolve
  context character budget are hard-bounded; a caller-supplied ``limit`` can
  only narrow the configured cap, never widen it.
* **Confirmation-gated writes.** ``policy_set`` and ``escalate`` refuse without
  an explicit ``confirmed=True`` and return typed ``*_CONFIRM_REQUIRED``.
* **Redacted.** Snippets, query echoes, provenance refs, and policy values pass
  through the centralized redaction boundary before leaving the module, so a
  legacy secret-shaped record can never be injected into a prompt raw.
* **Fail-closed under emergency stop.** The durable emergency-stop/global-
  disable latch (the same one the dashboard, voice, broker, and runner observe)
  refuses every operation; an unreadable latch is treated as engaged.

State lives under ``<state_dir>/memory/``: the policy in ``active-memory.json``
(atomic write, 0600) and the memories in the existing ``memory.db``.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .memory.store import (MemoryStore, MemoryStoreError, TIERS,
                           normalize_text, redact_secrets)

SCHEMA = "qubit-active-memory/1"
SCHEMA_VERSION = 1
POLICY_NAME = "active-memory.json"

MAX_ITEMS_LIMIT = 50
DEFAULT_MAX_ITEMS = 5
MAX_CONTEXT_CHARS = 1500
MAX_REASON_CHARS = 200
MAX_QUERY_CHARS = 2000
MAX_POOL = 200
CONF_STEP = 0.1

ESTOP_CODE = "ACTIVE_MEMORY_ESTOP"
UNAVAILABLE_CODE = "ACTIVE_MEMORY_UNAVAILABLE"
BAD_INPUT_CODE = "ACTIVE_MEMORY_BAD_INPUT"
CONFIRM_CODE = "ACTIVE_MEMORY_CONFIRM_REQUIRED"
EMPTY_CODE = "ACTIVE_MEMORY_EMPTY"
NOT_FOUND_CODE = "ACTIVE_MEMORY_NOT_FOUND"
CORRUPT_CODE = "ACTIVE_MEMORY_CORRUPT"

_POLICY_FIELDS = frozenset(("enabled", "min_score", "max_items",
                            "session_scoped", "schema_version"))
_TIER_WEIGHT = {"core": 0.3, "working": 0.2, "recall": 0.1, "archive": 0.0}
_ESCALATION = {"archive": "recall", "recall": "working", "working": "core",
               "core": "core"}


class ActiveMemoryError(Exception):
    """Typed active-memory failure (never a traceback at the boundary)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------- boundaries
def _redact(text: Any) -> str:
    from .redaction import redact_text

    return redact_text(redact_secrets("" if text is None else str(text)))


def estop_engaged(cfg: Any) -> bool:
    """True while the durable emergency-stop / global-disable latch is set.

    Same probe the rest of the runtime uses; fails closed (an import error or
    unreadable latch reports engaged).
    """
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def _refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "results": [], "blocks": [], "refs": [],
            "explanation": "active recall is fail-closed while the emergency "
                           "stop is latched; reset it before using memory"}


def _refuse(code: str, state: str, detail: str) -> Dict[str, Any]:
    return {"state": state, "code": code, "detail": detail,
            "results": [], "blocks": [], "refs": []}


# ------------------------------------------------------------------- paths
def _memory_dir(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "memory"


def memory_db_path(cfg: Any) -> Path:
    return _memory_dir(cfg) / "memory.db"


def policy_path(cfg: Any) -> Path:
    return _memory_dir(cfg) / POLICY_NAME


def _open_store(cfg: Any) -> MemoryStore:
    return MemoryStore(memory_db_path(cfg))


# ------------------------------------------------------------------ policy
def _default_policy() -> Dict[str, Any]:
    return {"enabled": False, "min_score": 0.0,
            "max_items": DEFAULT_MAX_ITEMS, "session_scoped": False,
            "schema_version": SCHEMA_VERSION}


def _validate_policy_values(pol: Dict[str, Any]) -> Optional[str]:
    unknown = [k for k in pol if k not in _POLICY_FIELDS]
    if unknown:
        return f"unsupported policy field(s): {sorted(unknown)}"
    if not isinstance(pol.get("enabled"), bool):
        return "enabled must be a boolean"
    ms = pol.get("min_score")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return "min_score must be a number in [0, 1]"
    if not math.isfinite(float(ms)) or not (0.0 <= float(ms) <= 1.0):
        return "min_score must be a number in [0, 1]"
    mi = pol.get("max_items")
    if isinstance(mi, bool) or not isinstance(mi, int):
        return f"max_items must be an integer in [1, {MAX_ITEMS_LIMIT}]"
    if not (1 <= mi <= MAX_ITEMS_LIMIT):
        return f"max_items must be an integer in [1, {MAX_ITEMS_LIMIT}]"
    if not isinstance(pol.get("session_scoped"), bool):
        return "session_scoped must be a boolean"
    ver = pol.get("schema_version")
    if isinstance(ver, bool) or not isinstance(ver, int):
        return "schema_version must be an integer"
    if ver > SCHEMA_VERSION:
        return f"unsupported schema_version {ver}"
    return None


def _load_policy(cfg: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read and validate the persisted policy. A missing file is the disabled
    default; a corrupt/unknown/invalid file is a typed ``corrupt`` health and
    the disabled default is returned so callers fail closed."""
    defaults = _default_policy()
    path = policy_path(cfg)
    if not path.exists():
        return dict(defaults), {"state": "ok", "detail": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return dict(defaults), {"state": "corrupt",
                                "detail": f"policy unreadable: {exc}"}
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return dict(defaults), {"state": "corrupt",
                                "detail": "policy has an unknown schema"}
    pol = raw.get("policy")
    if not isinstance(pol, dict):
        return dict(defaults), {"state": "corrupt",
                                "detail": "policy payload is not an object"}
    merged = dict(defaults)
    merged.update({k: pol[k] for k in _POLICY_FIELDS if k in pol})
    err = _validate_policy_values(merged)
    if err is not None:
        return dict(defaults), {"state": "corrupt", "detail": err}
    return merged, {"state": "ok", "detail": ""}


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def policy_get(cfg: Any) -> Dict[str, Any]:
    """Return the persisted policy (redacted, typed; never raises)."""
    policy, health = _load_policy(cfg)
    ok = health["state"] == "ok"
    return {"state": "OK" if ok else "CORRUPT",
            "code": "" if ok else CORRUPT_CODE,
            "schema": SCHEMA, "detail": health["detail"],
            "policy": dict(policy), "estop": estop_engaged(cfg)}


def policy_set(cfg: Any, *, enabled: Optional[bool] = None,
               min_score: Optional[float] = None,
               max_items: Optional[int] = None,
               session_scoped: Optional[bool] = None,
               confirmed: bool = False) -> Dict[str, Any]:
    """Confirmation-gated, bounded update of the active-recall policy.

    Estop is checked first, then confirmation, then bounds. A corrupt policy is
    never silently overwritten; the caller must recover it explicitly. On
    success the policy is persisted atomically with 0600 permissions.
    """
    if estop_engaged(cfg):
        return _refuse_estop("policy_set")
    policy, health = _load_policy(cfg)
    if health["state"] != "ok":
        return _refuse(CORRUPT_CODE, "CORRUPT", health["detail"])
    if not confirmed:
        return _refuse(CONFIRM_CODE, "CONFIRM_REQUIRED",
                       "changing the active-recall policy requires confirmed=True")
    updates = {"enabled": enabled, "min_score": min_score,
               "max_items": max_items, "session_scoped": session_scoped}
    if all(v is None for v in updates.values()):
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT",
                       "no policy field was supplied")
    merged = dict(policy)
    for key, value in updates.items():
        if value is not None:
            merged[key] = value
    merged["schema_version"] = SCHEMA_VERSION
    err = _validate_policy_values(merged)
    if err is not None:
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT", err)
    try:
        _atomic_write(policy_path(cfg), {"schema": SCHEMA, "policy": merged})
    except OSError as exc:
        return _refuse(UNAVAILABLE_CODE, "FAILED",
                       f"policy could not be persisted: {exc}")
    return {"state": "OK", "code": "", "schema": SCHEMA, "policy": dict(merged)}


# ------------------------------------------------------------------- recall
def _scope_for(policy: Dict[str, Any], session: str) -> Optional[str]:
    if policy["session_scoped"]:
        return (session or "default").strip() or "default"
    return None


def _refs(rec: Dict[str, Any]) -> List[str]:
    refs = [_redact(p) for p in (rec.get("provenance") or [])]
    refs.append(f"memory:{rec.get('id')}@{rec.get('scope')}")
    return refs


def _result(rec: Dict[str, Any], score: float) -> Dict[str, Any]:
    return {"id": rec.get("id"), "snippet": _redact(rec.get("text", "")),
            "tier": rec.get("tier"), "scope": rec.get("scope"),
            "source": rec.get("source"),
            "confidence": rec.get("confidence"), "score": round(score, 4),
            "refs": _refs(rec)}


def _rank(pool: List[Dict[str, Any]], min_score: float, cap: int
          ) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for rec in pool:
        try:
            conf = float(rec.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        score = conf + _TIER_WEIGHT.get(rec.get("tier"), 0.0)
        if score < min_score:
            continue
        scored.append((score, rec))
    scored.sort(key=lambda item: (-item[0], -int(item[1].get("id") or 0)))
    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    seen_text: set = set()
    for score, rec in scored:
        rid = rec.get("id")
        text_key = normalize_text(rec.get("text", ""))
        if rid in seen_ids or text_key in seen_text:
            continue
        seen_ids.add(rid)
        seen_text.add(text_key)
        out.append(_result(rec, score))
        if len(out) >= cap:
            break
    return out


def recall(cfg: Any, query: str, *, session: str = "",
           limit: Optional[int] = None) -> Dict[str, Any]:
    """Active recall over the existing MemoryStore.

    Applies the session policy scope, a bounded score threshold, dedupe, and a
    hard item cap, and returns only redacted snippets. Returns a typed EMPTY
    result when nothing qualifies, and a typed gate when disabled / estoped /
    corrupt / unavailable.
    """
    if estop_engaged(cfg):
        return _refuse_estop("recall")
    policy, health = _load_policy(cfg)
    if health["state"] != "ok":
        return _refuse(CORRUPT_CODE, "CORRUPT", health["detail"])
    if not policy["enabled"]:
        return _refuse(UNAVAILABLE_CODE, "DISABLED",
                       "active recall is disabled by policy")
    if not isinstance(query, str) or not query.strip():
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT", "query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT",
                       f"query exceeds {MAX_QUERY_CHARS} characters")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return _refuse(BAD_INPUT_CODE, "BAD_INPUT",
                           "limit must be a positive integer")
    cap = policy["max_items"]
    if limit is not None:
        cap = min(cap, limit)
    scope = _scope_for(policy, session)
    pool_limit = min(MAX_POOL, max(cap * 8, cap + 16))
    try:
        store = _open_store(cfg)
        try:
            pool = store.search(query, scope=scope, limit=pool_limit)
        finally:
            store.close()
    except sqlite3.DatabaseError as exc:
        return _refuse(CORRUPT_CODE, "CORRUPT", f"memory store corrupt: {exc}")
    except Exception as exc:
        return _refuse(UNAVAILABLE_CODE, "UNAVAILABLE",
                       f"memory store unavailable: {exc}")
    results = _rank(pool, float(policy["min_score"]), cap)
    if not results:
        return {"state": "EMPTY", "code": EMPTY_CODE,
                "query": _redact(query), "scope": scope,
                "results": [], "count": 0}
    return {"state": "OK", "code": "", "query": _redact(query),
            "scope": scope, "min_score": policy["min_score"],
            "max_items": cap, "results": results, "count": len(results)}


def resolve(cfg: Any, query: str, *, session: str = "") -> Dict[str, Any]:
    """Resolve recall results into the bounded prompt context block(s).

    Never injects raw secrets (every snippet is redacted) and never exceeds the
    configured ``max_items`` or the context character budget. Gates propagate
    unchanged from :func:`recall`.
    """
    base = recall(cfg, query, session=session)
    if base["state"] != "OK":
        return {"state": base["state"], "code": base.get("code", ""),
                "detail": base.get("detail", ""), "context": "",
                "blocks": [], "refs": [], "count": 0}
    blocks: List[Dict[str, Any]] = []
    refs: List[str] = []
    total = 0
    for item in base["results"]:
        sep = 1 if blocks else 0
        remaining = MAX_CONTEXT_CHARS - total - sep
        if remaining <= 0:
            break
        text = item["snippet"][:remaining]
        if not text:
            break
        blocks.append({"id": item["id"], "text": text,
                       "source": item["source"], "score": item["score"],
                       "refs": item["refs"]})
        refs.extend(item["refs"])
        total += sep + len(text)
        if total >= MAX_CONTEXT_CHARS:
            break
    return {"state": "OK", "code": "", "context":
            "\n".join(b["text"] for b in blocks), "blocks": blocks,
            "refs": refs, "count": len(blocks),
            "budget": MAX_CONTEXT_CHARS}


def escalate(cfg: Any, memory_id: int, *, reason: str,
             confirmed: bool = False) -> Dict[str, Any]:
    """Confirmation-gated escalation of one memory toward ``core``.

    Raises the record's tier one step (``archive -> recall -> working -> core``)
    and bumps confidence by a bounded step, recording an append-only history
    entry. Typed gates when disabled/estoped/not confirmed, and typed
    NOT_FOUND / BAD_INPUT otherwise.
    """
    if estop_engaged(cfg):
        return _refuse_estop("escalate")
    policy, health = _load_policy(cfg)
    if health["state"] != "ok":
        return _refuse(CORRUPT_CODE, "CORRUPT", health["detail"])
    if not policy["enabled"]:
        return _refuse(UNAVAILABLE_CODE, "DISABLED",
                       "active recall is disabled by policy")
    if not confirmed:
        return _refuse(CONFIRM_CODE, "CONFIRM_REQUIRED",
                       "escalating a memory requires confirmed=True")
    if isinstance(memory_id, bool) or not isinstance(memory_id, int) \
            or memory_id <= 0:
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT",
                       "memory_id must be a positive integer")
    if not isinstance(reason, str) or not reason.strip():
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT", "reason must not be empty")
    if len(reason) > MAX_REASON_CHARS:
        return _refuse(BAD_INPUT_CODE, "BAD_INPUT",
                       f"reason exceeds {MAX_REASON_CHARS} characters")
    try:
        store = _open_store(cfg)
    except sqlite3.DatabaseError as exc:
        return _refuse(CORRUPT_CODE, "CORRUPT", f"memory store corrupt: {exc}")
    except Exception as exc:
        return _refuse(UNAVAILABLE_CODE, "UNAVAILABLE",
                       f"memory store unavailable: {exc}")
    try:
        rec = store.get(memory_id)
    except MemoryStoreError:
        store.close()
        return _refuse(NOT_FOUND_CODE, "NOT_FOUND",
                       f"no memory with id {memory_id}")
    current_tier = rec.get("tier") if rec.get("tier") in TIERS else "recall"
    new_tier = _ESCALATION[current_tier]
    try:
        confidence = float(rec.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    new_confidence = min(1.0, confidence + CONF_STEP)
    import time as _time
    with store._lock:
        store._conn.execute(
            "UPDATE memories SET tier=?, confidence=?, updated_at=? WHERE id=?",
            (new_tier, new_confidence, _time.time(), memory_id))
        store._history(memory_id, "update",
                       {"escalated": True, "from_tier": current_tier,
                        "to_tier": new_tier, "reason": _redact(reason)})
        store._conn.commit()
    store.close()
    return {"state": "OK", "code": "", "id": memory_id,
            "previous_tier": current_tier, "tier": new_tier,
            "confidence": round(new_confidence, 4),
            "refs": [f"memory:{memory_id}@{rec.get('scope')}"]}


# ------------------------------------------------------------------ status
def status(cfg: Any) -> Dict[str, Any]:
    """Typed active-memory status: policy, store availability, estop.

    Read-only for the store: the memory count is reported only when the
    database already exists. A missing database is a healthy empty store, not
    an error.
    """
    latched = estop_engaged(cfg)
    policy, health = _load_policy(cfg)
    db_path = memory_db_path(cfg)
    memories: Optional[int] = None
    store_state = "absent"
    store_code = ""
    if db_path.exists():
        try:
            store = _open_store(cfg)
            try:
                memories = len(store._active_select(scope=None, staged=False))
                store_state = "ok"
            finally:
                store.close()
        except sqlite3.DatabaseError:
            store_state, store_code = "corrupt", CORRUPT_CODE
        except Exception:
            store_state, store_code = "unavailable", UNAVAILABLE_CODE
    if latched:
        state, code = "ESTOP", ESTOP_CODE
    elif health["state"] != "ok":
        state, code = "CORRUPT", CORRUPT_CODE
    elif store_state in ("corrupt", "unavailable"):
        state, code = "DEGRADED", store_code
    else:
        state, code = "OK", ""
    return {"state": state, "code": code, "schema": SCHEMA,
            "enabled": policy["enabled"], "min_score": policy["min_score"],
            "max_items": policy["max_items"],
            "session_scoped": policy["session_scoped"],
            "policy_state": health["state"], "policy_detail": health["detail"],
            "store_state": store_state, "store_path": str(db_path),
            "memories": memories, "estop": latched}
