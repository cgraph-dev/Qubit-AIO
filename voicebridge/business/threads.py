"""Owner/agent and room/agent-to-agent threads over the shared A2A event log.

P4-02. This module owns **thread semantics**, not persistence: callers inject
the same :class:`voicebridge.business.a2a.EventLog` the message fabric uses, so
there is exactly one append-only interaction record and no second message
store. Thread metadata is itself an append-only log record (``record_type ==
"thread"``); messages are ordinary A2A envelopes carried on the same log.

Enforced here:

* every participant can read its own threads and messages in per-thread
  sequence order (read-your-writes); a non-participant cannot read at all;
* ``open_thread`` is idempotent for identical metadata and a typed conflict
  otherwise, so a retried open never forks a thread;
* ``post``/``reply`` dedupe a reused idempotency key and refuse a conflicting
  one (the A2A fabric owns that check);
* a message is only visible to participants, even on a ``broadcast`` thread —
  there is no tenant-wide or global chat room;
* a mention (``@principal`` or ``@room``) is accepted only when the mentioned
  principal is a participant **and** an injected ``can_mention`` grants it; a
  thread never widens authority, and no message shape transfers ownership;
* payloads and participant lists are bounded, and ``sweep`` delegates
  retention/dead-lettering to the log rather than deleting records.

Threads never call the runtime, a task, or a lease. A message cannot transfer
ownership by natural language because nothing in this module mutates durable
ownership: task/workspace authority only moves through :mod:`runs` and
:mod:`sessions` under an explicit recipient acceptance.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from . import a2a
from .a2a import A2AMessage, EventLog
from .errors import BAD_INPUT_CODE, BusinessError
from .ids import new_id
from .util import now_iso, require_text

THREAD_RECORD = "thread"
THREAD_KINDS = ("owner_agent", "room", "direct", "broadcast")
VISIBILITY_BY_KIND = {
    "owner_agent": "direct",
    "direct": "direct",
    "room": "thread",
    "broadcast": "broadcast",
}
MAX_PARTICIPANTS = 64
MAX_BODY_CHARS = 8000
MAX_MENTIONS = 16
DEFAULT_MAX_BODY_BYTES = 16384
ROOM_MENTION = "@room"

THREAD_NOT_FOUND_CODE = "BUSINESS_THREAD_NOT_FOUND"
THREAD_EXISTS_CODE = "BUSINESS_THREAD_EXISTS"
THREAD_NOT_PARTICIPANT_CODE = "BUSINESS_THREAD_NOT_PARTICIPANT"
THREAD_MENTION_REFUSED_CODE = "BUSINESS_THREAD_MENTION_REFUSED"
THREAD_AUTHORITY_REFUSED_CODE = "BUSINESS_THREAD_AUTHORITY_REFUSED"
THREAD_BODY_TOO_LARGE_CODE = "BUSINESS_THREAD_BODY_TOO_LARGE"

# A payload may not carry these names: authority is never moved by a chat body.
_RESERVED_PAYLOAD_KEYS = frozenset(
    {
        "authority",
        "authority_transfer",
        "capabilities",
        "capability_subset",
        "delegate_to",
        "grant",
        "grants",
        "owner_principal",
        "transfer_ownership",
    }
)
_FORBIDDEN_KINDS = frozenset({"authority", "grant", "ownership", "delegation"})


@dataclass(frozen=True, slots=True)
class Thread:
    thread_id: str
    tenant_id: str
    kind: str
    participants: tuple[str, ...]
    visibility: str
    owner: str | None
    subject: str | None
    created_at: str
    created_epoch: float
    last_sequence: int

    def includes(self, principal: str) -> bool:
        return principal in self.participants


@dataclass(frozen=True, slots=True)
class ThreadMessage:
    """A typed view over one A2A message posted to a thread."""

    message: A2AMessage
    body: str
    reply_to: str | None
    mentions: tuple[str, ...]

    @property
    def message_id(self) -> str:
        return self.message.message_id

    @property
    def thread_id(self) -> str:
        return self.message.thread_id

    @property
    def sender(self) -> str:
        return self.message.sender_principal

    @property
    def recipient(self) -> str:
        return self.message.recipient_principal

    @property
    def sequence(self) -> int:
        return self.message.sequence

    @property
    def delivery_state(self) -> str:
        return self.message.delivery_state

    @property
    def sent_at(self) -> str:
        return self.message.sent_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "sequence": self.sequence,
            "body": self.body,
            "reply_to": self.reply_to,
            "mentions": list(self.mentions),
            "delivery_state": self.delivery_state,
            "sent_at": self.sent_at,
        }


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _participants(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)):
        raise BusinessError(
            BAD_INPUT_CODE, "participants must be an iterable of principals"
        )
    seen: list[str] = []
    for item in values:
        clean = _text(item, "participant", max_len=200)
        if clean not in seen:
            seen.append(clean)
    if len(seen) < 2:
        raise BusinessError(
            BAD_INPUT_CODE, "a thread needs at least two distinct participants"
        )
    if len(seen) > MAX_PARTICIPANTS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"a thread may hold at most {MAX_PARTICIPANTS} participants",
        )
    return tuple(seen)


def _kind(value: Any) -> str:
    clean = _text(value, "thread kind", max_len=40)
    if clean not in THREAD_KINDS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"thread kind must be one of {', '.join(THREAD_KINDS)}",
        )
    return clean


def _message_kind(value: Any) -> str:
    clean = _text(value, "message kind", max_len=64)
    if clean in _FORBIDDEN_KINDS:
        raise BusinessError(
            THREAD_AUTHORITY_REFUSED_CODE,
            "a thread message cannot carry an authority-transfer kind",
        )
    return clean


def _thread_record(record: Mapping[str, Any]) -> Thread | None:
    if record.get("record_type") != THREAD_RECORD:
        return None
    return Thread(
        thread_id=str(record["thread_id"]),
        tenant_id=str(record["tenant_id"]),
        kind=str(record["kind"]),
        participants=tuple(record.get("participants") or ()),
        visibility=str(record.get("visibility") or "direct"),
        owner=record.get("owner"),
        subject=record.get("subject"),
        created_at=str(record.get("created_at") or ""),
        created_epoch=float(record.get("created_epoch") or 0.0),
        last_sequence=0,
    )


def _records(log: EventLog, tenant_id: str) -> list[Mapping[str, Any]]:
    return list(log.read(tenant_id))


def _last_sequence(records: Iterable[Mapping[str, Any]], thread_id: str) -> int:
    highest = 0
    for record in records:
        if record.get("thread_id") != thread_id:
            continue
        highest = max(highest, int(record.get("sequence", 0)))
    return highest


def _resolve_thread(
    log: EventLog, tenant_id: str, thread_id: str
) -> tuple[Thread, list[Mapping[str, Any]]]:
    records = _records(log, tenant_id)
    found: Thread | None = None
    for record in records:
        if record.get("thread_id") != thread_id:
            continue
        candidate = _thread_record(record)
        if candidate is not None:
            found = candidate
            break
    if found is None:
        raise BusinessError(
            THREAD_NOT_FOUND_CODE,
            f"no thread {thread_id!r} in tenant {tenant_id!r}",
        )
    return (
        replace(found, last_sequence=_last_sequence(records, thread_id)),
        records,
    )


def _require_participant(thread: Thread, principal: str) -> None:
    if not thread.includes(principal):
        raise BusinessError(
            THREAD_NOT_PARTICIPANT_CODE,
            f"{principal!r} is not a participant of thread {thread.thread_id!r}",
        )


def _default_recipient(thread: Thread, sender: str) -> str:
    for principal in thread.participants:
        if principal != sender:
            return principal
    raise BusinessError(
        BAD_INPUT_CODE, "a thread needs a recipient other than the sender"
    )


def _mentions(
    thread: Thread,
    sender: str,
    values: Iterable[str] | None,
    can_mention: Callable[[str, str], bool] | None,
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes, Mapping)):
        raise BusinessError(BAD_INPUT_CODE, "mentions must be a list")
    cleaned: list[str] = []
    for raw in values:
        token = _text(raw, "mention", max_len=200)
        if token == ROOM_MENTION:
            if thread.kind != "room":
                raise BusinessError(
                    THREAD_MENTION_REFUSED_CODE,
                    "@room is only valid in a room thread",
                )
            mention = ROOM_MENTION
        else:
            principal = token[1:] if token.startswith("@") else token
            principal = _text(principal, "mention", max_len=200)
            if not thread.includes(principal):
                raise BusinessError(
                    THREAD_MENTION_REFUSED_CODE,
                    f"{principal!r} is not a participant of this thread",
                )
            mention = principal
        if mention not in cleaned:
            cleaned.append(mention)
    if len(cleaned) > MAX_MENTIONS:
        raise BusinessError(
            BAD_INPUT_CODE, f"a message may mention at most {MAX_MENTIONS} people"
        )
    if cleaned and can_mention is None:
        raise BusinessError(
            THREAD_MENTION_REFUSED_CODE,
            "a mention requires an injected mention authority checker",
        )
    for mention in cleaned:
        if mention == ROOM_MENTION:
            if not can_mention(sender, ROOM_MENTION):  # type: ignore[misc]
                raise BusinessError(
                    THREAD_MENTION_REFUSED_CODE,
                    f"{sender!r} may not mention the room",
                )
            continue
        if mention == sender:
            raise BusinessError(
                THREAD_MENTION_REFUSED_CODE, "a sender cannot mention itself"
            )
        if not can_mention(sender, mention):  # type: ignore[misc]
            raise BusinessError(
                THREAD_MENTION_REFUSED_CODE,
                f"{sender!r} may not mention {mention!r}",
            )
    return tuple(cleaned)


def _assert_no_authority_payload(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    if extra is None:
        return {}
    if not isinstance(extra, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "payload must be a mapping")
    clean = dict(extra)
    reserved = sorted(set(clean) & _RESERVED_PAYLOAD_KEYS)
    if reserved:
        raise BusinessError(
            THREAD_AUTHORITY_REFUSED_CODE,
            "a thread message cannot carry authority fields: "
            + ", ".join(reserved),
        )
    return clean


def _body(value: Any) -> str:
    body = _text(value, "message body", max_len=MAX_BODY_CHARS)
    return body


def open_thread(
    log: EventLog,
    *,
    tenant_id: str,
    kind: str,
    participants: Iterable[str],
    owner: str | None = None,
    subject: str | None = None,
    visibility: str | None = None,
    thread_id: str | None = None,
    now: float | None = None,
) -> tuple[Thread, bool]:
    """Open a thread. Identical retries return the existing thread."""
    tenant = _text(tenant_id, "tenant_id")
    thread_kind = _kind(kind)
    members = _participants(participants)
    who = _optional_text(owner, "owner", max_len=200)
    if who is not None and who not in members:
        raise BusinessError(
            BAD_INPUT_CODE, "the thread owner must be a participant"
        )
    title = _optional_text(subject, "subject", max_len=300)
    moment = time.time() if now is None else float(now)
    tid = _text(thread_id or new_id("thr"), "thread_id", max_len=120)
    derived = VISIBILITY_BY_KIND[thread_kind]
    vis = derived if visibility is None else _text(visibility, "visibility", max_len=40)
    if vis not in a2a.VISIBILITIES or vis != derived:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"visibility must be {derived!r} for a {thread_kind!r} thread",
        )
    existing_records = _records(log, tenant)
    for record in existing_records:
        if record.get("thread_id") != tid:
            continue
        candidate = _thread_record(record)
        if candidate is None:
            continue
        if (
            candidate.kind == thread_kind
            and candidate.participants == members
            and candidate.visibility == vis
            and candidate.owner == who
            and candidate.subject == title
        ):
            return (
                replace(
                    candidate,
                    last_sequence=_last_sequence(existing_records, tid),
                ),
                False,
            )
        raise BusinessError(
            THREAD_EXISTS_CODE,
            f"thread {tid!r} already exists with different metadata",
        )
    log.append(
        {
            "record_type": THREAD_RECORD,
            "tenant_id": tenant,
            "thread_id": tid,
            "kind": thread_kind,
            "participants": list(members),
            "visibility": vis,
            "owner": who,
            "subject": title,
            "created_at": now_iso(moment),
            "created_epoch": moment,
        }
    )
    thread, _ = _resolve_thread(log, tenant, tid)
    return thread, True


def get_thread(log: EventLog, *, tenant_id: str, thread_id: str) -> Thread:
    tenant = _text(tenant_id, "tenant_id")
    tid = _text(thread_id, "thread_id", max_len=120)
    thread, _ = _resolve_thread(log, tenant, tid)
    return thread


def list_threads(
    log: EventLog,
    *,
    tenant_id: str,
    principal: str,
    kind: str | None = None,
) -> list[Thread]:
    """Threads ``principal`` participates in, newest metadata first."""
    tenant = _text(tenant_id, "tenant_id")
    who = _text(principal, "principal")
    wanted = _kind(kind) if kind is not None else None
    records = _records(log, tenant)
    threads: list[Thread] = []
    for record in records:
        candidate = _thread_record(record)
        if candidate is None or not candidate.includes(who):
            continue
        if wanted is not None and candidate.kind != wanted:
            continue
        if any(item.thread_id == candidate.thread_id for item in threads):
            continue
        threads.append(
            replace(
                candidate,
                last_sequence=_last_sequence(records, candidate.thread_id),
            )
        )
    threads.sort(key=lambda item: (item.created_epoch, item.thread_id))
    return threads


def post(
    log: EventLog,
    *,
    tenant_id: str,
    thread_id: str,
    sender: str,
    body: Any,
    idempotency_key: str,
    recipient: str | None = None,
    kind: str = "message",
    mentions: Iterable[str] | None = None,
    reply_to: str | None = None,
    can_mention: Callable[[str, str], bool] | None = None,
    payload: Mapping[str, Any] | None = None,
    expires_at: float | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    now: float | None = None,
) -> tuple[ThreadMessage, bool]:
    """Append one thread message. Returns ``(message, created)``."""
    tenant = _text(tenant_id, "tenant_id")
    who = _text(sender, "sender")
    thread = get_thread(log, tenant_id=tenant, thread_id=thread_id)
    _require_participant(thread, who)
    target = (
        _default_recipient(thread, who)
        if recipient is None
        else _text(recipient, "recipient")
    )
    if not thread.includes(target):
        raise BusinessError(
            THREAD_NOT_PARTICIPANT_CODE,
            f"recipient {target!r} is not a participant of this thread",
        )
    if target == who:
        raise BusinessError(
            BAD_INPUT_CODE, "a message needs a recipient other than the sender"
        )
    message_kind = _message_kind(kind)
    text = _body(body)
    mention_list = _mentions(thread, who, mentions, can_mention)
    extra = _assert_no_authority_payload(payload)
    encoded = len(text.encode("utf-8"))
    if encoded > int(max_body_bytes):
        raise BusinessError(
            THREAD_BODY_TOO_LARGE_CODE,
            f"message body exceeds the {int(max_body_bytes)} byte limit",
        )
    envelope = {
        "body": text,
        "reply_to": _optional_text(reply_to, "reply_to", max_len=120),
        "mentions": list(mention_list),
        "thread_kind": thread.kind,
    }
    if extra:
        envelope["meta"] = extra
    message, created = a2a.send(
        log,
        tenant_id=tenant,
        sender_principal=who,
        recipient_principal=target,
        payload=envelope,
        idempotency_key=idempotency_key,
        kind=message_kind,
        visibility=thread.visibility,
        thread_id=thread.thread_id,
        correlation_id=thread.thread_id,
        expires_at=expires_at,
        max_payload_bytes=max_body_bytes + 4096,
        now=now,
    )
    return _view(message), created


def reply(
    log: EventLog,
    *,
    tenant_id: str,
    thread_id: str,
    sender: str,
    body: Any,
    idempotency_key: str,
    mentions: Iterable[str] | None = None,
    can_mention: Callable[[str, str], bool] | None = None,
    payload: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> tuple[ThreadMessage, bool]:
    """Reply to the newest visible message; the recipient is its sender."""
    thread = get_thread(log, tenant_id=tenant_id, thread_id=thread_id)
    who = _text(sender, "sender")
    _require_participant(thread, who)
    history = read(
        log, tenant_id=tenant_id, thread_id=thread_id, principal=who, now=now
    )
    if history:
        last = history[-1]
        recipient = last.sender if last.sender != who else _default_recipient(thread, who)
        reply_to = last.message_id
    else:
        recipient = _default_recipient(thread, who)
        reply_to = None
    return post(
        log,
        tenant_id=tenant_id,
        thread_id=thread_id,
        sender=who,
        body=body,
        idempotency_key=idempotency_key,
        recipient=recipient,
        kind="reply",
        mentions=mentions,
        reply_to=reply_to,
        can_mention=can_mention,
        payload=payload,
        now=now,
    )


def _view(message: A2AMessage) -> ThreadMessage:
    payload = message.payload if isinstance(message.payload, Mapping) else {}
    return ThreadMessage(
        message=message,
        body=str(payload.get("body") or ""),
        reply_to=payload.get("reply_to"),
        mentions=tuple(payload.get("mentions") or ()),
    )


def read(
    log: EventLog,
    *,
    tenant_id: str,
    thread_id: str,
    principal: str,
    since_sequence: int = 0,
    include_expired: bool = False,
    unacked_only: bool = False,
    limit: int | None = None,
    now: float | None = None,
) -> list[ThreadMessage]:
    """Read a thread in sequence order; only participants may read."""
    thread = get_thread(log, tenant_id=tenant_id, thread_id=thread_id)
    who = _text(principal, "principal")
    _require_participant(thread, who)
    messages = a2a.replay(
        log,
        tenant_id=thread.tenant_id,
        principal=who,
        thread_id=thread.thread_id,
        since_sequence=int(since_sequence),
        include_expired=include_expired,
        now=now,
    )
    views = [_view(message) for message in messages]
    if unacked_only:
        views = [item for item in views if item.delivery_state in ("pending", "delivered")]
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise BusinessError(BAD_INPUT_CODE, "limit must be an integer >= 0")
        views = views[:limit]
    return views


def ack(
    log: EventLog,
    *,
    tenant_id: str,
    thread_id: str,
    principal: str,
    message_id: str,
    now: float | None = None,
) -> ThreadMessage:
    """Acknowledge one message; only its recipient and a participant may ack."""
    thread = get_thread(log, tenant_id=tenant_id, thread_id=thread_id)
    who = _text(principal, "principal")
    _require_participant(thread, who)
    acked = a2a.ack(
        log,
        tenant_id=thread.tenant_id,
        principal=who,
        message_id=_text(message_id, "message_id"),
        now=now,
    )
    return _view(acked)


def replay(
    log: EventLog,
    *,
    tenant_id: str,
    principal: str,
    thread_id: str | None = None,
    since_sequence: int = 0,
    include_expired: bool = True,
    now: float | None = None,
) -> list[ThreadMessage]:
    """Offline backlog replay across every thread ``principal`` may read."""
    tenant = _text(tenant_id, "tenant_id")
    who = _text(principal, "principal")
    if thread_id is not None:
        return read(
            log,
            tenant_id=tenant,
            thread_id=thread_id,
            principal=who,
            since_sequence=since_sequence,
            include_expired=include_expired,
            now=now,
        )
    messages = a2a.replay(
        log,
        tenant_id=tenant,
        principal=who,
        since_sequence=since_sequence,
        include_expired=include_expired,
        now=now,
    )
    visible_threads = {thread.thread_id for thread in list_threads(
        log, tenant_id=tenant, principal=who)}
    return [
        _view(message)
        for message in messages
        if message.thread_id in visible_threads
    ]


def sweep(
    log: EventLog,
    *,
    tenant_id: str,
    retention_seconds: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Dead-letter expired/over-retention messages through the shared fabric."""
    return a2a.sweep(
        log, tenant_id=tenant_id, retention_seconds=retention_seconds, now=now
    )


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "MAX_BODY_CHARS",
    "MAX_MENTIONS",
    "MAX_PARTICIPANTS",
    "ROOM_MENTION",
    "THREAD_AUTHORITY_REFUSED_CODE",
    "THREAD_BODY_TOO_LARGE_CODE",
    "THREAD_EXISTS_CODE",
    "THREAD_KINDS",
    "THREAD_MENTION_REFUSED_CODE",
    "THREAD_NOT_FOUND_CODE",
    "THREAD_NOT_PARTICIPANT_CODE",
    "THREAD_RECORD",
    "Thread",
    "ThreadMessage",
    "VISIBILITY_BY_KIND",
    "ack",
    "get_thread",
    "list_threads",
    "open_thread",
    "post",
    "read",
    "reply",
    "replay",
    "sweep",
]
