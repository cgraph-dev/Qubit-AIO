"""Subscriber WebSocket for the dashboard chat sidebar: ``/api/events``.

Upstream parity (hermes-agent ``hermes_cli/web_routers/chat_ws.py``): the
dashboard serves ``/api/events`` as a WebSocket and fans out frames published
on ``/api/pub`` to every subscriber on the same ``channel``. The React
sidebar's ``eventsFeedClient.ts`` dials exactly that URL
(``buildWsUrl("/api/events", {channel})``) and is unmodified upstream code, so
the endpoint must exist for the feed to connect at all.

Qubit keeps ONE durable event order (``voicebridge.events.EventHub`` + journal)
instead of upstream's in-memory per-channel sets, so a subscriber here is
seeded from the journal past its cursor and then pumped live. Wire shape is
one JSON object per text frame — the AG-UI envelope the SSE path already
emits — so both transports carry the same records.

Auth mirrors every other Qubit WS (``pty_ws``/``stt_ws``): peer + origin are
checked first, then the single-use ticket (or the SPA session token in the
query string on loopback). A refusal is a typed JSON envelope, never a silent
close.

Failure mode: a slow reader drops its oldest frames (Subscriber.offer) and the
socket is torn down on write failure. The agent loop is never blocked — this
module only ever reads from the hub's per-subscriber deque.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

from .logging_setup import log_event
from . import pty_ws as P

#: Channel ids are client-minted and opaque; upstream accepts any short token
#: (``_VALID_CHANNEL_RE``), we match that contract so an upstream build's
#: channel string is never rejected here.
_VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

#: Bound the per-subscriber backlog. The hub caps its own deque too; this is
#: the local hand-off buffer between the pump thread and the socket.
_DRAIN_TIMEOUT_S = 15.0
#: Wall-clock heartbeat so an idle feed is not reaped by an intermediary.
_HEARTBEAT_S = 15.0


class EventsWsError(RuntimeError):
    """Raised when the events socket cannot be served."""


class EventsWsServer:
    """Serve ``/api/events`` as a WebSocket bound to one EventHub."""

    def __init__(self, hub: Any, tickets: Any = None, *,
                 estop: Any = None) -> None:
        self.hub = hub
        self.tickets = tickets
        self._estop = estop
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    def _estop_engaged(self) -> bool:
        if self._estop is None:
            return False
        try:
            return bool(self._estop())
        except Exception:  # noqa: BLE001 - a broken probe must not wedge the socket
            return True

    def serve(self, handler: Any, *, ticket_value: str = "",
              origin_ok: bool, peer_ok: bool, token_ok: bool = False) -> None:
        """Accept and pump one subscriber, or write a typed refusal.

        Auth precedence is the same as ``/api/pub``: the single-use ticket when
        one is supplied, else the loopback session token already validated by
        the caller (``token_ok``).
        """
        if not peer_ok:
            P._reject(handler, 403, "PEER_REFUSED",
                      "The events feed is bound to the approved local interface.")
            return
        if not origin_ok:
            P._reject(handler, 403, "ORIGIN_REFUSED",
                      "The events feed origin does not match the bound host.")
            return
        if self.hub is None:
            P._reject(handler, 503, "EVENT_HUB_UNAVAILABLE",
                      "No event hub is attached to this listener.")
            return
        if ticket_value:
            if self.tickets is None:
                P._reject(handler, 503, "TICKET_STORE_UNAVAILABLE",
                          "No ticket authority is attached to the events socket.")
                return
            if self.tickets.consume(ticket_value, scope=SCOPE_EVENTS) is None:
                P._reject(handler, 401, "TICKET_REQUIRED",
                          "A short-lived scoped events ticket is required.")
                return
        elif not token_ok:
            P._reject(handler, 401, "TOKEN_REQUIRED",
                      "The events feed requires the dashboard session token.")
            return
        if self._estop_engaged():
            P._reject(handler, 423, "ESTOP_ENGAGED",
                      "Qubit emergency stop is engaged; events socket refused.")
            return
        try:
            P.handshake(handler)
        except P.WsProtocolError as exc:
            P._reject(handler, 400, "WS_HANDSHAKE", str(exc))
            return
        with self._lock:
            self._active += 1
        try:
            self._pump(handler)
        finally:
            with self._lock:
                self._active -= 1

    def _pump(self, handler: Any) -> None:
        sock = handler.connection
        # Parse the query inline: ``webui.query_params`` lives in the module
        # that imports us, so importing it here would be circular.
        import urllib.parse as _up
        raw = getattr(handler, "path", "") or ""
        q = dict(_up.parse_qsl(raw.split("?", 1)[1], keep_blank_values=True)) \
            if "?" in raw else {}
        channel = str(q.get("channel", "") or "")
        last_seq = q.get("last_seq", "")
        cursor = int(last_seq) if str(last_seq).isdigit() else None
        sub = self.hub.subscribe(last_event_id=cursor)
        log_event("events.ws.subscribe", channel=channel, cursor=cursor,
                  active=self.active_count)
        try:
            import time
            heartbeat_due = time.monotonic() + _HEARTBEAT_S
            while not sub.closed:
                items = sub.drain(timeout=_DRAIN_TIMEOUT_S)
                frames = []
                for rec in items:
                    seq = rec.get("seq")
                    if isinstance(seq, int):
                        cursor = max(cursor or 0, seq)
                    frames.append(json.dumps(rec, separators=(",", ":"),
                                             ensure_ascii=False))
                # A genuine cursor gap: tell the client to replay from scratch
                # rather than silently skipping records.
                if sub.take_gap(cursor or 0):
                    frames.append(json.dumps({"type": "resync", "event": "resync"}))
                if time.monotonic() >= heartbeat_due:
                    frames.append(json.dumps({"type": "PING"}))
                    heartbeat_due = time.monotonic() + _HEARTBEAT_S
                for frame in frames:
                    if not P.write_frame(sock, _OP_TEXT, frame.encode("utf-8")):
                        return
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected — normal lifecycle
        finally:
            self.hub.unsubscribe(sub)
            P.close_frame(sock)


#: Ticket scope for the events socket (mirrors SCOPE_PTY / SCOPE_STT).
SCOPE_EVENTS = "events"
_OP_TEXT = 0x1

__all__ = ["EventsWsServer", "SCOPE_EVENTS", "EventsWsError"]
