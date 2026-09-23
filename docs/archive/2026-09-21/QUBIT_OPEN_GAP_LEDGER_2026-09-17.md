# Qubit open-gap ledger — 2026-09-17 (implementation-first mode)

Status: **INCOMPLETE — do not report code-ready or verified.**

This ledger is the honest current view. It deliberately disagrees with
`docs/QUBIT_GAP_LEDGER.md`'s "0 code-side open rows": that merged ledger is a
surface-catalog (route + proving-test exists), not executed differential
evidence, and the guardrails (`docs/QUBIT_REALITY_AND_EXECUTION_GUARDRAILS.md`)
require the differential oracle to decide completion.

## Authoritative oracle

`/tmp/qubit-differential/runner/REPORT_FINAL2.json` (differential runner):

- verdict **INCOMPLETE**
- rows **14666** · PASS **19** · SAFE_EQUIVALENT **0** · OPERATOR_ONLY **108** · **OPEN 14539**
- failing gates:
  - `openclaw_undisposed_addition` = FAIL (101 rows)
  - `unexecuted_open_row` = FAIL (14539 rows)

All other gates pass (no unexecuted row, missing caller, skipped fixture, mock
substitution, refusal-as-success, unsafe normalizer, visual-only control,
voice-backend mismatch, persistence/recovery diff, secret leak).

## Open rows by class (Hermes triage, `hermes_open_triage.json`)

| class | count | notes |
|---|---:|---|
| A_vendored | 9732 | source-parity rows; 8677 SAFE_EQUIVALENT-designated, 1055 still OPEN (config/env keys) |
| C_compat | 600 | 422 SAFE_EQUIVALENT, 178 OPERATOR_ONLY |
| D_frontend | 281 | dashboard page/control rows still OPEN |
| E_operator | 100 | OPERATOR_ONLY (live prerequisites) |
| B_missing | 30 | 29 refused-dialect `rpc.method.*` + 1 evals route |

## Concrete code-side blockers (current tree)

1. **Route ownership — RESOLVED (owner decision 2026-09-17).**
   The vendored Hermes runtime is the canonical implementation for
   Hermes-compatible routes; Qubit owns the front door, policy boundaries, and
   the Qubit/OpenClaw additions. Implemented as an explicit, generated,
   guarded matrix:
   - `voicebridge/route_ownership.py` — the classification authority
     (`hermes_runtime` / `qubit_policy` / `qubit_addition`).
   - `scripts/route_ownership_matrix.py` — static generator (446 rows, **0
     violations**) writing `docs/QUBIT_ROUTE_OWNERSHIP.json`.
   - `tests/test_route_ownership_matrix.py` — guard (current, no violations).
   The matrix records **90 Qubit compat handlers the fork owns**
   (`non_authoritative_handlers`).
   **Reachability (item 1, done):** `scripts/compat_handler_reachability.py`
   proves, via AST (comments/strings excluded), that every one of those 81
   unique handlers is reachable: 1 production caller (`h_power` ← `webui.py`),
   1 direct test reference, 71 route-test-reachable, 8 registered parameterized
   routes (`h_session_show`, `h_session_messages`, `h_share_resolve`,
   `webhooks_show`, `sess_empty`, `h_profile_soul`, `h_profile_setup_command`,
   `h_local_models_job_show`). **`dead_module_functions` = []** and
   **`delete_candidates` = []**: there is no genuinely unreachable compat code
   to delete, so none was deleted. Guard:
   `tests/test_compat_handler_reachability.py`.

2. **Vendored config/env rows unbound (1055 A_vendored OPEN; backlog D3).**
   Every vendored `config_key`/`env_var` row must be bound to the Qubit config
   schema or explicitly disposed. Not done.

3. **OpenClaw undisposed additions (101; backlog B10).**
   The differential runner's `openclaw_undisposed_addition` gate fails closed
   because 101 observed OpenClaw delta rows have no terminal disposition with a
   real proof or a recorded `operator_prerequisite`. Not done.

4. **B_missing refused dialect (30 rows).**
   29 `rpc.method.*` in the vendored tree + 1 evals route. Disposition is an
   owner policy call (backlog C6); Qubit refuses the OpenClaw network RPC
   dialect as a second authority (backlog D2, 422 rows).

5. **Dashboard controls (281 D_frontend OPEN).**
   The UI inventory records 692 rows / 684 OPEN, open because no executed Hermes
   reference + canonical state assertion exists for each control (not because
   the screens are blank). Turning reachability/screenshots into executed
   differential evidence is the work.

## Owner / frozen-contract blockers (not implementable by an agent)

- D1: terminal `source_parity` class for the 8677 byte-identical vendored rows
  requires an owner amendment of the frozen INTERFACE/gate.
- D2: a listening OpenClaw-compatible RPC/WS server (422 rows) is owner policy;
  Qubit deliberately refuses a second authority.
- D3/C1: the vendored config/env binding was gated on `voicebridge/**`
  ownership; the ownership freeze is the recorded blocker.
- C2/C3/C4: live-instance hygiene (external listener on 9119, two instances
  sharing one state dir, non-loopback dashboard host) are operator actions.

## Item 6 — chat surfaces (owner decision 2026-09-17, latest scope)

- **`/qubit-chat` — FROZEN as-is.** Owner: "we leave /qubit-chat how it looks
  now." It keeps the current Qubit-native ChatPage UI; no change is allowed
  unless the owner says so.
- **`/chat` — must render the real Hermes xterm terminal**, Qubit-branded,
  sharing sessions/providers/streaming/policy/audit/e-stop/VoiceBridge.
  Owner selected the literal xterm option, which requires a documented, tested
  exception to the `PTY_NO_SHELL` policy.

  **Infrastructure gap (blocking, precise):** Qubit's single listener
  (`voicebridge/webui.py`, `http.server`) has **no WebSocket server** — it
  refuses every `Upgrade: websocket` with a typed 426 (`_refuse_hermes_ws`).
  A real xterm `/chat` needs a PTY WebSocket endpoint
  (handshake + framing + auth + PTY spawn/resize/reconnect). The pieces exist:
  `websockets==15.0.1`, `ptyprocess`, and the vendored `PtyBridge`
  (`qubit_hermes/hermes_cli/pty_bridge.py`), and the vendored terminal
  frontend libs are still present in `frontend/qubit-web/src/lib/pty-*.ts`.
  Two ways to serve it, both needing an owner call because of the single-
  listener rule:
  1. in-process ASGI WS mount for the vendored runtime (same runtime, one
     listener), or
  2. a manual socket-upgrade handler inside `webui`.
  Until one is chosen and implemented + tested, `/chat` cannot be the real
  xterm terminal. Not started; not claimed.

## Item 6 — chat surfaces (owner decision 2026-09-17)

- **`/qubit-chat` — FROZEN as-is.** Keeps the current Qubit-native ChatPage UI.
- **`/chat` — IMPLEMENTED (real Hermes terminal).**
  - `voicebridge/pty_ws.py`: one WebSocket upgrade path on the existing Qubit
    listener (`/agent/api/pty`) — RFC 6455 handshake/framing, short-lived
    single-use scoped tickets, origin/peer/owner/target validation, broker
    e-stop, session/idle/frame limits, cleanup, and the vendored `PtyBridge`
    (`hermes --tui`). No second listener.
  - `POST /agent/api/auth/ws-ticket` mints the 30 s scoped ticket; the
    long-lived token is never on the WS URL.
  - `frontend/qubit-web/src/pages/TerminalChatPage.tsx` mounts the vendored
    xterm terminal for `/chat`; `/qubit-chat` still renders the native ChatPage.
  - Policy exception documented and tested:
    `docs/QUBIT_PTY_POLICY_EXCEPTION_2026-09-17.md`.
  - Evidence: `tests/test_pty_ws8u.py` (12 tests incl. browser WS round-trip),
    `test_chat_is_terminal_and_qubit_chat_is_native`, the updated 401-not-426
    upgrade test, plus the provenance/branding/route guards.
  - **Still open within item 6:** shared sessions between the terminal (vendor
    session store) and the native ChatPage (Qubit chat store) — they share the
    broker/policy/audit/e-stop but not yet one session store; and the real
    `hermes --tui` spawn is wired as the default but exercised hermetically via
    an injected bridge (vendored-bridge availability is asserted).

## Regenerated this pass (read-only artifacts)

- `scripts/source_inventory.py` → 6775 items, **0** unexplained omissions,
  byte diff 12173 identical.
- `scripts/rebuild_ledger_qubit.py` → 725 rows (686 IMPLEMENTED / 36
  SAFE_EQUIVALENT / 3 OPERATOR_ONLY), 0 non-terminal.
- `scripts/rebuild_ledger_hermes.py` → 573 rows (457 / 116).
- `scripts/rebuild_ledger_openclaw.py` → 104 rows (94 / 8 / 2).

These surface-catalog counts are **not** completion evidence.

## Verdict

`INCOMPLETE`. The full verification phase was not run: the user's rule is to run
it only when the code-side inventory has zero genuine open rows, which it does
not. No commit, push, reset, or clean was performed.
