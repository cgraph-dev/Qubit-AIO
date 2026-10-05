# Qubit-chat slash parity — `/qubit-chat` vs `/chat` — 2026-10-04

Verifier-lane audit. Product code untouched (tests + this doc only).
Test: `tests/test_qubitchat_parity_audit9u.py`
(7 passed / 2 failed on 2026-10-04 — the 2 failures are the gap list, by design).

Run: `TMPDIR=/tmp/pytest-q .venv/bin/python -m pytest tests/test_qubitchat_parity_audit9u.py -v`

## Terminal inventory: 60 verbs

Enumerated from `qubit_runtime/gateway/slash_commands*.py`
(`async def _handle_*_command`) + the authoritative registry
(`run_busy.py::_PLAIN_COMMANDS` 22 + `_IDLE_COMMANDS` 36) plus handler-only
extras (`reset`, `undo`): agents approve approvals bg blueprint branch btw
bundles busy codex-runtime commands compress context debug deny diff fast
footer goal heartbeat help insights kanban login loop memory model pause
personality platform profile reasoning refine reload-mcp reload-skills reset
restart resume retry review rollback save sessions sethome skills status stop
subgoal suggestions title topic topup undo update usage verbose version voice
whoami yolo.

(`bg`/`sethome` are the registered names for the `background`/`set_home`
handlers per the alias table. The strictest /chat surface is wider — the
110-name `qubit_cli/commands.py` registry + CLI `_SLASH_DISPATCH` on top of
the gateway set — tracked as follow-up, not asserted.)

## /qubit-chat coverage

| Layer | Covered | Detail |
|---|---|---|
| Catalog as data (`GET /api/chat/slash-commands` → `h_chat_slash_commands`) | **60/60** | Live probe: 200, 377 entries (gateway registry 136 incl. aliases, skills 196, plugins 14, toolsets 31). Backend-catalog lane LANDED. |
| Turn execution (`chat_transport.SLASH_COMMANDS`) | **3/60** (+2 native) | Executes help, skills, model only. `mcp`, `plugins` are dashboard-native verbs with no terminal counterpart. Everything else answers honest help — correct fallback, not parity. |
| Endpoints (skills/plugins/mcp/toolsets) | **4/4 reachable** | `match()` resolves + `HANDLERS` callable + disposition `implemented` for `/api/skills`, `/api/dashboard/plugins`, `/api/mcp/servers`, `/api/tools/toolsets`. |
| Composer slash popup (ChatPage.tsx) | **0 markers** | No filter/popup testids; send path (`streamChatTurn` → `POST /api/qubit/chat/stream`) intact. |

## Gap list (the 2 test failures, with owners)

1. **Turn execution — 55 verbs** (`test_qubitchat_turn_executes_terminal_verbs`):
   agents approvals approve bg blueprint branch btw bundles busy codex-runtime
   commands compress context debug deny diff fast footer goal heartbeat
   insights kanban loop memory pause personality profile reasoning refine
   reload-mcp reload-skills reset restart resume retry review rollback save
   sessions sethome status stop subgoal suggestions title topic topup undo
   update usage verbose version voice whoami yolo. Owner: turn-routing lane.
   Out-of-scope (documented, not gaps): `login` (paired-DM auth ceremony),
   `platform` (no messaging transport on this surface).
2. **Slash popup wiring** (`test_chatpage_slash_popup_wiring`): no
   `slash-popup`/`slashFilter`/`SlashCommands`/etc. in ChatPage.tsx. Owner:
   frontend-popup lane (data source: the catalog endpoint above, already live).

## Notes

- Catalog `mcp` source reports `miss: AttributeError` under an empty cfg
  (fail-soft by contract); with a real service cfg the `/api/mcp/servers`
  source serves (reachability test proves route + handler). Needs a
  live-cfg verification, not counted as a gap here.
- ChatPage.tsx send path verified present (`streamChatTurn`); chat session /
  message endpoints unchanged.
