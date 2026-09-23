# Qubit PTY policy — scoped `/chat` terminal exception (2026-09-17)

Status: **owner-approved, documented, tested.**

## Decision

The owner selected the real Hermes terminal for `/chat` (2026-09-17). This is
the **only** exception to the `PTY_NO_SHELL` policy:

* `GET /api/pty` (non-upgrade) still returns the typed `PTY_NO_SHELL` gate.
* The **WebSocket upgrade** `/agent/api/pty` is served as the `/chat` terminal,
  authenticated by a short-lived, single-use, scoped ticket.
* Every other raw shell/PTY/console path (`/api/console`, `/api/pty` GET,
  `POST /api/profiles/*/open-terminal`) remains a typed refusal.
* `/qubit-chat` is unchanged (Qubit-native ChatPage).

## Security contract (implemented in `voicebridge/pty_ws.py`)

- **Auth:** `POST /agent/api/auth/ws-ticket` mints a 30 s, single-use ticket
  scoped to `pty`, bound to session/owner/target. The long-lived
  API/session token is never accepted on the WebSocket query string or
  subprotocol. A ticketless or reused ticket is a typed HTTP 401.
- **Boundaries:** loopback peer only; `Origin` host must equal the `Host`
  header (or a configured `webui.allowed_origins`); the listener already binds
  loopback by default and refuses a non-loopback bind without a token.
- **Policy:** the broker emergency stop is consulted before spawn and polled
  during the session; an engaged e-stop refuses the upgrade (423) or closes the
  live socket.
- **Limits:** max 4 concurrent PTY sessions, 30 min idle timeout, 1 MiB inbound
  frame cap, bounded PTY read, poll backoff.
- **Cleanup:** the PTY child is killed (and the session loop closed) on client
  close, EOF, stall, e-stop, error, and listener shutdown (`WebUI.stop` →
  `PtyWsServer.cancel_all`).
- **Redaction:** the ticket is returned exactly once and never logged, echoed in
  errors, or placed in receipts; connection logs carry only session/owner/target
  and pid.

## One listener

The upgrade is served from the existing `ThreadingHTTPServer` handler thread
(socket hijack after the RFC 6455 handshake). No second listener, port, or
service is created.

## Evidence

- `tests/test_pty_ws8u.py` — 12 integration tests: handshake, ticket
  single-use/scope, auth rejection, origin rejection, input/output echo, resize
  (escape consumed, not written to the PTY), reconnect with a fresh ticket,
  client cancel → bridge closed, e-stop refusal, session limit, listener-stop
  cleanup, vendored-bridge availability, and a **browser WebSocket round-trip**.
- `tests/test_qbpv2_agent2_chat_runtime.py::test_ws_upgrade_to_pty_requires_ticket_not_a_loop`.
- `tests/test_qubit_playwright8tf.py::test_chat_is_terminal_and_qubit_chat_is_native`.
