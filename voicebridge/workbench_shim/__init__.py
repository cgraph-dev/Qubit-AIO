"""Workbench API shim — hermes-webui's ``/api/*`` contracts on Qubit's data.

WHAT THIS PACKAGE IS
---------------------------------------------------------------------------
The ported static UI (``v3/wbport``) was written against hermes-webui's HTTP
surface. Qubit serves many of the same *concepts* under different names and
shapes. This package is the translation seam: one function per upstream route
family that (a) calls the Qubit source that already owns the data, and (b)
re-shapes the answer to upstream's wire contract, because their JS parses it.

The full route-by-route contract table — upstream's real request/response shape,
read from upstream's ``api/`` handlers and ``static/`` consumers, next to the
Qubit source it delegates to — lives in ``docs/WORKBENCH_SHIM_MAP.md``. Read that
first; this docstring is the invariants, that file is the map.

DELEGATE, NEVER DUPLICATE (the five hard rules, enforced here)
---------------------------------------------------------------------------
1. **Sessions** come only from :class:`~voicebridge.qubit_session_store
   .QubitSessionStore`, the declared ``CHAT_SESSION_AUTHORITY``. Turns go only
   through :func:`voicebridge.chat_transport.turn` — the same function
   ``/qubit-chat``, the voice bridge and the CLI run. There is no second sender
   and no ``append`` call anywhere in this package.
2. **Files** come only from
   :mod:`voicebridge.workbench.adapters_workspace`, which already enforces
   Qubit's approved-project-root confinement (``voicebridge.files``). No path
   check is re-implemented and no write lane is added.
3. **Crons** come only from :mod:`voicebridge.cron_bridge` — the vendored
   ``cron.jobs`` JSON store the live scheduler fires from.
4. **Approvals** come only from :class:`voicebridge.approvals.ApprovalJournal`,
   the one durable settle-once journal the CLI, voice yes/no and the dashboard
   banner share.
5. **Auth** is Qubit's single scheme, ``X-Qubit-Session-Token``, enforced by the
   listener before dispatch. This package reads no header and adds no scheme.

NO DATABASE OF ITS OWN
---------------------------------------------------------------------------
Nothing here opens a database. The 826 MB chat transcript (``state.db``) has one
writable opener — ``QubitSessionStore`` — and this package reaches it only
through that class. A grep for a sqlite import in this package must return zero
hits: if code here ever needs one, the design has already gone wrong.

NO INVENTED DATA
---------------------------------------------------------------------------
Where Qubit genuinely has no counterpart, the route answers a typed
``501 {"ok": false, "code": "NOT_PORTED", "detail": ...}`` naming the missing
Qubit concept — never a plausible-looking empty payload. :func:`gap` builds it;
:data:`GAPS` is the registry the route table and the tests both read, so the set
of refusals is enumerable rather than scattered.

Public contract
---------------------------------------------------------------------------
Every handler has the compat table's signature
``handler(cfg, q, b, p) -> (status, payload)`` and returns JSON-ready dicts. The
route rows live in :mod:`voicebridge.workbench_shim_routes` (registration line
is delivered, not applied, in this branch).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "UPSTREAM_PIN",
    "NOT_PORTED",
    "GAPS",
    "gap",
    "sessions",
    "workspace",
    "crons",
    "approvals",
    "system",
]

#: The upstream commit whose contracts this shim matches. Every shape below was
#: read at this pin (``api/routes.py``, ``api/workspace.py``, ``api/commands.py``,
#: ``static/*.js``).
UPSTREAM_PIN = "github.com/nesquena/hermes-webui@3d9488fca6bb248c77f988bfbffcca9d8cfd9561"

#: The one code every unported route answers.
NOT_PORTED = "NOT_PORTED"

#: code -> the missing Qubit concept, in the user-facing words the UI shows.
#: Kept here (not inline at each route) so the refusal set is greppable and a
#: test can assert the route table and this registry agree.
GAPS: Dict[str, str] = {
    "MID_TURN_STEER": (
        "mid-turn steering: Qubit's chat_transport.turn is a synchronous turn "
        "with no in-flight agent object to stash guidance into, so there is no "
        "run to steer"
    ),
    "BROKERED_WRITE": (
        "a direct workspace write: Qubit file mutations are brokered, confirmed "
        "actions with an audit receipt (voicebridge.file_patch via "
        "voicebridge.broker), never a viewer write"
    ),
    "CRON_OUTPUT_FILES": (
        "cron per-run output files as markdown artifacts: Qubit's cron ledger "
        "records structured executions (cron.executions), not a .md per run"
    ),
    "SESSION_YOLO": (
        "a per-session yolo/auto-approve switch: Qubit has no session-scoped "
        "approval bypass; the equivalent is a policy change made by the operator"
    ),
    "CLARIFY_QUEUE": (
        "a parked clarification queue an agent blocks on: Qubit's clarify is a "
        "routing decision inside chat_transport, not a pending-question store"
    ),
    "STATUS_RESPONSE_DIVERGES": (
        "hermes-webui's StatusResponse wire type: Qubit already serves its own "
        "StatusResponse at GET /api/status, and re-binding that path would break "
        "the dashboard that reads it"
    ),
    "BACKGROUND_AGENT": (
        "a per-session parallel background-agent lane: Qubit runs one synchronous "
        "turn path with no detached background agent per session"
    ),
}


def gap(concept: str, *, detail: str = "", **extra: Any) -> Tuple[int, Dict[str, Any]]:
    """A typed refusal for a route Qubit genuinely cannot answer.

    ``concept`` names the missing Qubit concept (``GAPS`` key). The status is
    always 501 — the request was well-formed, the capability does not exist —
    and ``ok`` is always ``False`` so a client that only checks ``ok`` still
    sees a refusal instead of an empty success.
    """
    why = GAPS.get(concept) or concept.replace("_", " ").lower()
    text = f"not ported: {why}."
    if detail:
        text = f"{text} {detail}"
    out: Dict[str, Any] = {
        "ok": False,
        "code": NOT_PORTED,
        "concept": concept,
        "detail": text,
        "upstream": UPSTREAM_PIN,
    }
    out.update(extra)
    return 501, out


def str_param(q: Any, *names: str, default: str = "") -> str:
    """First present query value under any of ``names`` (compat table gives a dict)."""
    if not isinstance(q, dict):
        return default
    for name in names:
        if name in q and q[name] is not None:
            return str(q[name])
    return default


def flag(q: Any, *names: str) -> bool:
    """A query flag the way upstream reads them (``1``/``true``/``yes``)."""
    return str_param(q, *names).strip().lower() in ("1", "true", "yes", "on")


def body_of(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def int_param(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def truthy(value: Any) -> bool:
    """``confirm``-style acceptance: a bool, or the strings/ints clients send."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def e_stopped(cfg: Any) -> bool:
    """The one mutation gate, read from the single e-stop authority.

    Fail closed, exactly like ``qubit_compat._estop``: an unreadable authority
    means "stopped", never "allowed".
    """
    try:
        from ..broker import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        try:
            from .. import power as PW

            return bool(PW.estop_engaged(cfg))
        except Exception:
            return True


def redact(text: Any) -> str:
    """Qubit's shared redaction, fail-soft to the raw string."""
    try:
        from ..redaction import redact_text

        return redact_text(str(text))
    except Exception:
        return str(text)


def confirm_gate(cfg: Any, action: str, detail: str,
                 q: Optional[Dict[str, str]] = None,
                 b: Optional[Dict[str, Any]] = None,
                 payload: Optional[Dict[str, Any]] = None
                 ) -> Optional[Tuple[int, Dict[str, Any]]]:
    """The SHARED confirm contract for writes lacking a canonical module.

    Delegates to ``qubit_compat._compat_confirm`` so a cron write issued by the
    ported UI stages and settles on exactly the journal the Qubit dashboard,
    the CLI and voice confirmations use — one path, one receipt. Returns
    ``None`` when the request may proceed.
    """
    try:
        from .. import qubit_compat as HC

        fn = getattr(HC, "_compat_confirm", None)
        if callable(fn):
            staged = fn(cfg, action, detail, q, b, payload)
            return staged  # type: ignore[no-any-return]
    except Exception:
        pass
    # Fail closed if the shared journal cannot be reached: refuse rather than
    # let an unconfirmed mutation through.
    return 503, {
        "ok": False, "state": "UNAVAILABLE", "code": "CONFIRM_UNAVAILABLE",
        "detail": (f"the shared confirmation journal is not reachable, so the "
                   f"{action} write is refused rather than performed "
                   f"unconfirmed"),
    }


def join_rows(rows: Any) -> List[Dict[str, Any]]:
    """The dict rows of a list-or-None (a store field may be absent)."""
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


# The route families. Imported LAST so their modules can use the helpers above
# without a circular import; each is a module with ``routes()``/``HANDLERS``.
from . import sessions, workspace, crons, approvals, system  # noqa: E402
