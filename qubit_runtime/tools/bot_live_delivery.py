"""Durable, at-most-once handoff to an existing Bot Chat owner.

Adapted from FalconOrtiz's live-owner mailbox (#101564). A single private
record advances queued -> claimed -> terminal under a process-shared lock.
Claims never expire: a crashed consumer leaves an inspectable unknown outcome,
not permission to execute the same input again. Receipts are permanent.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextlib import ExitStack, contextmanager
from qubit_dir_acl import make_user_dir, unusable_dir_remedy

from utils import atomic_json_write, atomic_write_text, fsync_directory
from pathlib import Path
from typing import Any, Callable

from qubit_cli.active_sessions import _FileLock

log = logging.getLogger(__name__)

DELIVERY_DIR_NAME = "bot_live_delivery"
_SEQUENCE_FILE = ".sequence"
_OWNER_KEYS = ("profile_home", "session_id", "lease_id", "live_session_id")
_TERMINAL = frozenset({"settled", "failed", "cancelled", "ambiguous"})


def _bot_chat_tip(home: Path) -> str | None:
    """Current Bot Chat tip id, or None when the profile has no store or no Bot Chat yet."""
    from qubit_state import SessionDB

    if not (home / 'state.db').is_file():
        return None
    db = SessionDB(db_path=home / 'state.db', read_only=True)
    try:
        row = db.get_session_by_title('Bot Chat')
        return db.get_compression_tip(row['id']) if row else None
    finally:
        db.close()


def find_canonical_owner(profile_home: Path | str) -> dict[str, Any] | None:
    """Return the exact Bot Chat tip's lease, including unsupported CLI owners."""
    from qubit_cli.active_sessions import active_session_registry_snapshot
    from qubit_state import SessionDB

    home = Path(profile_home).resolve()
    if not (home / "state.db").is_file():
        return None
    db = SessionDB(db_path=home / "state.db", read_only=True)
    try:
        row = db.get_session_by_title("Bot Chat")
        session_id = db.get_compression_tip(row["id"]) if row else None
    finally:
        db.close()
    if not session_id:
        return None
    for entry in active_session_registry_snapshot(registry_home=home):
        if entry["session_id"] == session_id:
            return {**entry, "profile_home": str(home)}
    return None


def find_canonical_live_owner(profile_home: Path | str) -> dict[str, Any] | None:
    """Only advertised consumers may receive owner-pinned mailbox deliveries."""
    entry = find_canonical_owner(profile_home)
    meta = (entry or {}).get("metadata") or {}
    if entry and meta.get("bot_live_delivery_consumer") is True and meta.get("live_session_id"):
        return {key: entry[key] for key in ("profile_home", "session_id", "lease_id")} | {
            "live_session_id": meta["live_session_id"]}
    return None


def _owner(home: Path | str, owner: dict[str, Any]) -> dict[str, str]:
    pinned = {key: owner.get(key) for key in _OWNER_KEYS}
    if not all(isinstance(value, str) and value for value in pinned.values()):
        raise ValueError("owner requires profile_home, session_id, lease_id and live_session_id")
    if pinned["profile_home"] != str(Path(home).resolve()):
        raise ValueError("owner belongs to a different profile home")
    return pinned


def _delivery_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32,64}", value) is None:
        raise ValueError("delivery id must be 32 to 64 lowercase hex characters")
    return value


def _root(home: Path | str) -> Path:
    return Path(home).resolve() / "runtime" / DELIVERY_DIR_NAME


def authority_delivery(home, params):
    """Call only this home's already-running authority; never start a fallback."""
    return _authority_rpc(home, "bot_relay.deliver", params)


def authority_settle(home, params):
    """The same authority's ``bot_relay.settle``: the delivery id settled as never sent, or its
    existing receipt (``gateway/session_bot.py:_settle``)."""
    return _authority_rpc(home, "bot_relay.settle", params)


def _authority_rpc(home, method, params):
    import asyncio
    from qubit_cli.gateway_runtime import discover_gateway_endpoint
    from qubit_cli.gateway_client import GATEWAY_WS_PROTOCOL, GatewayClient, _session_ticket, gateway_ws_target
    from websockets.asyncio.client import connect

    home = Path(home).resolve()

    async def request():
        discovery = await asyncio.to_thread(discover_gateway_endpoint, home, timeout=5)
        if discovery.state != 'ready' or discovery.endpoint is None:
            raise ValueError('profile authority is not ready')
        endpoint = discovery.endpoint
        ticket = await asyncio.to_thread(_session_ticket, home, endpoint)
        url, protocols = gateway_ws_target(endpoint, ticket)
        # Loopback authority dial: never through HTTP(S)_PROXY (websockets>=14 honours it by default).
        async with connect(url, subprotocols=protocols, open_timeout=10, proxy=None) as ws:
            if ws.subprotocol != GATEWAY_WS_PROTOCOL:
                raise ValueError('authority protocol mismatch')
            async with GatewayClient(ws) as client:
                return await client.rpc(method, **params)

    return asyncio.run(request())


def has_mailbox(profile_home: Path | str) -> bool:
    """Whether any delivery was ever admitted for this profile (the mailbox directory is created on
    first admission only). A cheap pre-check for pollers: no mailbox means nothing to claim, so the
    owner lookup — a state.db open plus the exclusive active-session registry lock — can be skipped."""
    return _root(profile_home).is_dir()


_warned_unhardened: set[Path] = set()
# Mailbox dirs already reported unusable by this process: WARNING once per dir, DEBUG after,
# because the receipt readers re-enter ``_locked`` while Bot Chat is waiting.
_warned_unusable: set[Path] = set()


class MailboxUnusable(PermissionError):
    """This token cannot list, create or lock inside the mailbox dir (#139485, #138872).

    On Windows an elevated install/update creates ``runtime/bot_live_delivery`` owned by
    Administrators with no entry for the user; the same user's non-elevated gateway then
    cannot even open ``.lock``. Bot Chat deliveries pause; nothing else depends on the dir.
    A ``PermissionError`` subclass so every existing caller keeps its handling.
    """


def _dir_owner(path: Path) -> str:
    """Best-effort owner name for the log line; never raises (an ACL that hides it is the reported case)."""
    if os.name == "nt":
        try:
            import pywintypes
            import win32security
        except ImportError as exc:
            return f"unknown ({exc})"
        try:
            descriptor = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
            name, domain, _ = win32security.LookupAccountSid(None, descriptor.GetSecurityDescriptorOwner())
        except pywintypes.error as exc:
            return f"unknown ({exc})"
        return f"{domain}\\{name}" if domain else name
    import pwd  # windows-footgun: ok — POSIX branch only

    try:
        uid = path.stat().st_uid
    except OSError as exc:
        return f"unknown ({exc})"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def _unusable(root: Path, exc: PermissionError) -> MailboxUnusable:
    remedy = unusable_dir_remedy(root)
    level = logging.DEBUG if root in _warned_unusable else logging.WARNING
    _warned_unusable.add(root)
    log.log(level, "bot_live_delivery: mailbox directory %s (owner %s) is not usable by this process (%s); "
            "Bot Chat deliveries are paused until it is repaired, other clients are unaffected. To repair, %s",
            root, _dir_owner(root), exc, remedy)
    return MailboxUnusable(exc.errno, f"bot mailbox directory is not usable: {exc}", str(root))


def _prepare(root: Path) -> Path:
    """Create/harden the mailbox dir and its ``.lock`` file; returns the lock path."""
    created = not root.is_dir()
    make_user_dir(root.parent, parents=True, exist_ok=True)
    make_user_dir(root, mode=0o700, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError as exc:
        # On POSIX chmod fails only when another uid owns the dir: refuse a mailbox someone else controls.
        if os.name != "nt" and root.stat().st_uid != os.getuid():  # windows-footgun: ok — POSIX only
            raise
        # A foreign Windows ACL can deny FILE_WRITE_ATTRIBUTES even when this process can
        # still use the existing mailbox. Hardening must not abort recovery. The poller
        # re-enters twice a second, so WARNING once per dir and DEBUG after.
        level = logging.DEBUG if root in _warned_unhardened else logging.WARNING
        _warned_unhardened.add(root)
        log.log(level, "bot_live_delivery: could not harden mailbox directory %s (%s)", root, exc)
    if created:
        # Only a fresh mailbox dir needs its parents durably linked; the live
        # poller re-enters this lock twice a second per profile, and two
        # directory fsyncs per idle poll was measurable disk churn for nothing.
        fsync_directory(root.parent)
        fsync_directory(root.parent.parent)
    lock = root / ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    return lock


@contextmanager
def _locked(home: Path | str):
    """Hold the mailbox dir lock. A dir this token cannot use raises ``MailboxUnusable``
    (logged once per dir with its owner and the remedy); errors inside the body pass through."""
    root = _root(home)
    with ExitStack() as stack:
        try:
            stack.enter_context(_FileLock(_prepare(root)))
        except PermissionError as exc:
            raise _unusable(root, exc) from exc
        _warned_unusable.discard(root)
        yield root


def _read(path: Path) -> dict[str, Any] | None:
    """Exact-id read: absent → None; unreadable or not a JSON object → raises (callers fail closed)."""
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    if not isinstance(record, dict):
        raise ValueError(f"ticket {path.name} is not a JSON object ({type(record).__name__})")
    return record


# Tickets already reported unreadable by this process. The live poller rescans the
# dir twice a second, so a persistent bad ticket is WARNING once and DEBUG after.
_warned_unreadable: set[Path] = set()


def _ticket_shape_error(path: Path, record: dict[str, Any]) -> str | None:
    """Why a parsed ticket is unusable by the scans, or None when it is well-formed.

    A ticket that parses as JSON but lost a field (truncated rewrite, foreign
    writer, hand edit) used to raise KeyError/TypeError out of the sequence
    scan and the claim sweep — wedging admission and delivery for the whole
    profile exactly like corrupt JSON did before ``_scan_read`` existed.
    """
    owner = record.get("owner")
    created_at, sequence = record.get("created_at"), record.get("sequence", record.get("created_at"))
    if record.get("delivery_id") != path.stem or record.get("id") != path.stem:
        return "id does not match filename"
    status = record.get("status")
    if not isinstance(status, str) or status not in ({"queued", "claimed"} | _TERMINAL):
        return f"unknown status {status!r}"
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (created_at, sequence)):
        return "created_at/sequence are not integers"
    if not isinstance(owner, dict) or not all(isinstance(owner.get(k), str) and owner[k] for k in _OWNER_KEYS):
        return "owner pin is incomplete"
    return None


def _scan_read(path: Path) -> dict[str, Any] | None:
    """Bulk-scan variant: one unreadable or malformed ticket must not wedge the whole dir.

    Directory scans (sequence high-water mark, queued-claim sweep) may only
    treat a file as absent when it is provably absent; an unreadable ticket
    degrades to "that one delivery is uninspectable" with a warning.
    Exact-id reads (admission idempotency, completion, result lookup) keep
    using _read so a permission error still fails closed instead of
    licensing an overwrite of a possibly-live receipt.
    """
    try:
        record = _read(path)
        problem = None if record is None else _ticket_shape_error(path, record)
    except (OSError, ValueError) as exc:  # ValueError: corrupt JSON and invalid UTF-8 alike
        record, problem = None, str(exc)
    if problem is not None:
        level = logging.DEBUG if path in _warned_unreadable else logging.WARNING
        _warned_unreadable.add(path)
        log.log(level, "bot_live_delivery: skipping unreadable ticket %s (%s)", path.name, problem)
        return None
    _warned_unreadable.discard(path)
    return record


def _next_sequence(root: Path) -> int:
    """Allocate the next admission sequence under the dir lock.

    The high-water mark lives in a counter file beside the tickets, so a ticket
    the scan cannot read does not drop its sequence and hand a later admission
    a duplicate or lower one. Readable tickets still bootstrap dirs written
    before the counter existed. Wall time can roll back; sequences never do.
    """
    counter = root / _SEQUENCE_FILE
    try:
        persisted = int(counter.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        persisted = 0
    scanned = max((record.get("sequence", record["created_at"])
                   for candidate in root.glob("*.json")
                   if (record := _scan_read(candidate)) is not None), default=0)
    sequence = max(persisted, scanned) + 1
    atomic_write_text(counter, str(sequence), mode=0o600, fsync_dir=True)
    return sequence


def _write(path: Path, record: dict[str, Any]) -> None:
    atomic_json_write(path, record, indent=None, sort_keys=True, fsync_dir=True, mode=0o600)


def deliver_to_live_owner(
    profile_home: Path | str, owner: dict[str, Any], message: str,
    *, delivery_id: str | None = None, author: dict[str, Any] | None = None,
    notification_category: str = "result",
) -> dict[str, Any]:
    """Return durable admission immediately, without waiting for the owner.

    Retry with the same id AND pinned owner/message to inspect the existing
    state. Reusing an id with a different payload is an error, never an overwrite.
    """
    return _door(authority_delivery, profile_home, owner, message, delivery_id=delivery_id, author=author,
                 notification_category=notification_category)


def settle_held_delivery(profile_home: Path | str, owner: dict[str, Any], message: str, *, delivery_id: str,
                         author: dict[str, Any] | None = None) -> dict[str, Any]:
    """Finalize a refusal marked ``admitted: false`` under the same identity: the owner's terminal
    not-sent receipt (``refused``), or the receipt of an attempt that admitted the id meanwhile."""
    return _door(authority_settle, profile_home, owner, message, delivery_id=delivery_id, author=author)


def _door(call: Callable[[Path, dict], dict], profile_home, owner, message, *, delivery_id=None, author=None,
          notification_category="result"):
    pinned = _owner(profile_home, owner)
    if not isinstance(message, str):
        raise ValueError("message must be a string")
    key = _delivery_id(delivery_id if delivery_id is not None else uuid.uuid4().hex)
    with _locked(profile_home) as root:
        path = root / f"{key}.json"
        existing = _read(path)
        if existing is not None:
            if (existing["owner"] != pinned or existing["message"] != message or existing.get("author") != author
                    or existing.get("notification_category", "result") != notification_category):
                raise ValueError("delivery id already belongs to a different payload")
            return existing
        record = dict(delivery_id=key, id=key, owner=pinned, **pinned,
                      message=message, status="queued", created_at=time.time_ns(),
                      sequence=_next_sequence(root), **({"author": dict(author)} if author else {}))
        if notification_category == "diagnostic":
            record["notification_category"] = notification_category
        _write(path, record)
        return record


def _matches(home: Path | str, record: dict, owner: dict) -> bool:
    pinned = record["owner"]
    if any(pinned[key] != owner[key] for key in ("profile_home", "lease_id", "live_session_id")):
        return False
    if pinned["session_id"] == owner["session_id"]:
        return True
    from qubit_state import SessionDB

    db = SessionDB(db_path=Path(home) / "state.db", read_only=True)
    try:
        return db.get_compression_tip(pinned["session_id"]) == owner["session_id"]
    finally:
        db.close()


def owner_holds_delivery(profile_home: Path | str, record: dict) -> bool:
    """Whether the canonical live owner is still one that could claim ``record``."""
    owner = find_canonical_live_owner(profile_home)
    return owner is not None and _matches(profile_home, record, owner)


def claim_pending_delivery(
    profile_home: Path | str, owner: dict[str, Any],
) -> dict[str, Any] | None:
    """Claim oldest matching input exactly once; caller supplies its current lease.

    A lease transfer across compression is accepted only along the original
    stored session's compression chain. A new lease/live session cannot steal it.
    Caller must hold its normal turn-admission guard before invoking this.
    """
    current = _owner(profile_home, owner)
    if not _root(profile_home).is_dir():
        return None
    with _locked(profile_home) as root:
        pending = []
        for path in root.glob("*.json"):
            record = _scan_read(path)
            if record is not None and record["status"] == "queued" and _matches(profile_home, record, current):
                pending.append(record)
        if not pending:
            return None
        record = min(pending, key=lambda item: (
            item.get("sequence", item["created_at"]), item["delivery_id"]))
        record.update(status="claimed", claimed_at=time.time_ns())
        _write(root / f"{record['delivery_id']}.json", record)
        return record


def complete_delivery(
    profile_home: Path | str, delivery_id: str, *, status: str,
    reply: str = "", error: str = "", reason: str = "",
) -> dict[str, Any]:
    """Persist an immutable terminal receipt; duplicate identical completion is safe."""
    key = _delivery_id(delivery_id)
    if status not in _TERMINAL:
        raise ValueError("invalid terminal delivery status")
    outcome = dict(status=status, reply=reply, error=error, reason=reason)
    with _locked(profile_home) as root:
        path = root / f"{key}.json"
        record = _read(path)
        if record is None:
            raise FileNotFoundError(f"delivery not found: {key}")
        if record["status"] in _TERMINAL:
            if any(record.get(k) != v for k, v in outcome.items()):
                raise ValueError("delivery already has a different terminal receipt")
            return record
        if record["status"] != "claimed":
            raise ValueError("delivery must be claimed before completion")
        record.update(outcome, completed_at=time.time_ns())
        _write(path, record)
        return record


def cancel_queued_delivery(
    profile_home: Path | str, delivery_id: str, *, error: str, reason: str,
) -> dict[str, Any] | None:
    """Cancel an ownerless queued receipt without racing a consumer's claim.

    A claim or terminal result that won the mailbox lock is returned unchanged.
    """
    key = _delivery_id(delivery_id)
    with _locked(profile_home) as root:
        path = root / f"{key}.json"
        record = _read(path)
        if record is None or record["status"] != "queued":
            return record
        record.update(status="cancelled", reply="", error=error, reason=reason,
                      completed_at=time.time_ns())
        _write(path, record)
        return record


def read_delivery_result(profile_home: Path | str, delivery_id: str) -> dict[str, Any] | None:
    """Read admission/claim/terminal state without waiting or deleting its receipt."""
    return _read(_root(profile_home) / f"{_delivery_id(delivery_id)}.json")


_PENDING = ("queued", "claimed")
_POLL_SECONDS = 0.5


def _poll_receipt(profile_home: Path | str, delivery_id: str) -> dict[str, Any] | None:
    """A waiter's read: the receipt file the owner republishes when the admission settles (and on
    restart recovery). Never an authority RPC: a wait outlives owner restarts, and replaying the
    envelope (``read_delivery_result``) is for producers re-confirming their admission."""
    record = _read(_root(profile_home) / f"{_delivery_id(delivery_id)}.json")
    if record is not None and record.get("status") == "canonical":
        # Admitted with its outcome not yet published (an older owner's last write): pending.
        return {**record, "status": "queued"}
    return record


def await_delivery(
    profile_home: Path | str, delivery_id: str, timeout: float | None,
    *, should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Poll a receipt until the owner settles it, ``timeout`` lapses, or ``should_stop`` says so.

    Every transport that hands a turn to a live Bot Chat owner (local ``message_agent``, the
    Desktop relay, ``qubit peer dm`` and ``qubit peer run``) waits on the same receipt; keeping
    the loop here is what stops the lanes drifting (one lane returned a receipt sentence instead
    of the reply, two never waited at all). Returns the last record read — still pending when the
    budget lapsed, None when the receipt was never readable.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        record = read_delivery_result(profile_home, delivery_id)
        if record is None or record["status"] not in _PENDING:
            return record
        if should_stop is not None and should_stop():
            return record
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return record
        time.sleep(_POLL_SECONDS if remaining is None else min(_POLL_SECONDS, remaining))


async def await_delivery_async(
    profile_home: Path | str, delivery_id: str, timeout: float | None,
    *, should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """``await_delivery`` for an event loop: never blocks a worker thread for the whole budget."""
    import asyncio

    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        record = await asyncio.to_thread(read_delivery_result, profile_home, delivery_id)
        if record is None or record["status"] not in _PENDING:
            return record
        if should_stop is not None and should_stop():
            return record
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return record
        await asyncio.sleep(_POLL_SECONDS if remaining is None else min(_POLL_SECONDS, remaining))
