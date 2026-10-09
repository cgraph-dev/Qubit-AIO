# E2E VERIFICATION — live :8890 surfaces (Qubit VoiceBridge + Gateway)

    worktree   /tmp/qb-v3   branch v3-e2e
    base       4f4bef6e    (clean at task start, 0 dirty)
    date       2026-10-09 19:0x EEST
    scope      VERIFICATION. No service restarted/stopped. READ-ONLY on every store.
               One unambiguous low-risk bug fixed (frontend nav dedupe). No cron/session/row
               created, modified, or deleted (a throwaway chat session was created for the
               round-trip then archived+deleted — see §1).

**VERDICT: THE LIVE SYSTEM WORKS END TO END.** The owner's #1 surface (/chat) mounts, sends a
real turn, gets a real reply, and persists it. Every nav page loads and fetches real data. The
backend is healthy. The one known daemon defect (`update_token_counts(task=)`) is NOT present in
the running daemon. Two real defects found (one fixed in source, one needs the owner's call).

---

## 1. THE CHAT ROUND TRIP — PASS

Playwright opened `http://127.0.0.1:8890/chat`: SPA mounted (`#root` child = 1), page title
`Qubit Agent - Command Center`, 44 buttons, composer `<textarea>` present, model picker shows
`deepseek-v4.1-flash` (marked LIVE), session list populated with real sessions (640 msgs, cron
rows, etc.). Screenshot: `/tmp/qb-v3/_e2e/page_chat.png`.

Real turn (sent into a FRESH chat-store session `dashboard-e3bb66a0c243`, NEVER the owner's):

    POST /agent/api/qubit/chat/stream  {"text":"reply with the single word pong","session":"<fresh>"}
    event: start  {"route":"agent","session_id":"dashboard-e3bb66a0c243"}
    event: delta  {"text":"pong"}
    event: done   {"ok":true,"reply":"pong","elapsed_ms":3208,
                   "receipt":{"runtime":"qubit-vendored-agent",
                              "path":"voicebridge.agent.run_session",
                              "provider":"opencode-go","model":"deepseek-v4.1-flash",
                              "transport":"voicebridge.chat_transport.agent"}}

Persistence confirmed: `GET .../sessions/<id>/messages` returns the user msg + assistant `pong`
(total 2). The probe session was then DELETEd (`confirm=true` → `SESSION_NOT_FOUND` on re-GET):
state restored. The owner's live session was never written to.

## 2. THE PAGES — ALL PASS

All 10 requested pages returned HTTP 200, mounted the SPA, and rendered real API-backed rows.
Screenshots under `/tmp/qb-v3/_e2e/`.

| page        | HTTP | real data rendered                              | screenshot                |
|-------------|------|-------------------------------------------------|---------------------------|
| /           | 302→chat | dashboard + embedded chat host (real)       | page_.png                 |
| /chat       | 200  | composer, model picker, session list            | page_chat.png             |
| /cron       | 200  | 2 jobs w/ schedule + last/next run              | page_cron.png             |
| /sessions   | 200  | 443 total, 9686 msgs, 3 platforms Connected      | page_sessions.png         |
| /logs       | 200  | live AGENT.LOG rows (real logger lines)          | page_logs.png             |
| /models     | 200  | main model + model profiles                     | page_models.png           |
| /skills     | 200  | "205/205 enabled", 24 categories w/ counts       | page_skills.png           |
| /plugins    | 200  | plugin list (205 KB body)                       | page_plugins.png          |
| /tools      | 200  | *unknown-route fallback* — see DEFECT D2         | page_tools.png            |
| /kanban     | 200  | 4 lanes, 0 tasks (board genuinely empty)         | page_kanban.png           |

`/models` and `/skills` names looked "fictional" to a vision pass; they are the owner's real
local/proxy aliases (`opencode-go`, `ollama`) — confirmed directly via `GET /agent/api/model/options`.

## 3. BACKEND HEALTH — PASS

    qubit-voicebridge.service   active (running) since 17:15:38; Main PID 484217; 28.5G RSS
    qubit-gateway.service       active (running) since 16:59:11; Main PID 460128; 497M RSS
    :8890 listener              python pid 484217 (the voicebridge `run` process)

Key `/agent/api/*` routes (X-Api-Token auth; the SPA API is mounted under `/agent`):

    /agent/api/status            200  68631b   /agent/api/sessions       200  38669b
    /agent/api/skills            200  58711b   /agent/api/cron/jobs      200   3217b
    /agent/api/logs              200  15593b   /agent/api/model/options  200  14235b
    /agent/api/tools/toolsets    200   9540b   /agent/api/runtime        200  66393b
    /agent/api/plugins/kanban/events 200 2007b
    /agent/api/branding          404  (real route is /api/branding — harmless probe miss)

## 4. JOURNAL WARNINGS — CLASSIFIED

`-p warning` over the last 2h: **ZERO** lines on both units. Over 4h (startup window) the
distinct warnings are:

| warning                                             | class   | note                                                        |
|-----------------------------------------------------|---------|-------------------------------------------------------------|
| `update_token_counts() got ... 'task'`              | ABSENT  | known defect fixed at a3d2d5ce (ancestor of HEAD); running daemon does NOT emit it |
| SQLite 3.45.1 WAL-reset corruption advisory (×6)    | REAL/INFO | env-level: linked SQLite older than 3.51.3; warns once/db; not a service fault; `qubit update`/`qubit doctor` fixes |
| `Failed to connect to MCP server 'pixellab'` (×12)  | REAL/LOW | `${env:PIXELLAB_API_TOKEN}` unset in profile .env; pixellab MCP tools unavailable; user config |
| `Skill 'plan'/'canvas' slash command collides`      | NOISE   | expected guidance warning, `/skill <name>` fallback exists  |
| Telegram / Whatsapp connect + `Poll error` (1×)     | NOISE   | transient startup ordering (wa bridge not yet up at t=0)    |
| `Shutdown context: signal=SIGTERM`                  | NOISE   | the 16:59→17:15 restart, expected                           |
| systemd `left-over process ...` + `Failed with result 'exit-code'` | NOISE | unclean prior stop; KillMode drop-in present; resolved on start |
| `httpx ... receive_response_body.failed GeneratorExit` | NOISE | DEBUG-level; abandoned SSE/stream body, benign              |

## 5. INDEPENDENCE SMOKE — PASS

    from qubit_cli.main import main            -> OK   (sys.path=qubit_runtime)
    import agent / tui_gateway.entry / voicebridge.gateway_daemon -> all OK
    runtime hermes_* imports: NONE (only voicebridge.agent_compat.adapters.hermes,
                              Qubit's own compat adapter module)
    ./deletion_test.sh  ->  RESULT: 17 passed, 0 failed
                            drove a REAL agent turn with hermes off PATH:
                            model=qwen3-vl:2b completed=True final_response='pong'
                            OVERALL: PASS — Qubit boots with hermes absent
    scripts/gate_all.py  ->  VERDICT: PASS — all blocking checks green (5/5)

No runtime `hermes_*` import is needed for a real turn.

## 6. DEFECTS FOUND

**D2 — `/tools` has no SPA route (LOW, by design, NOT fixed).**
`frontend/qubit-web/src/App.tsx` has no `/tools` route; the sidebar has no "Tools" link (the
tool surface is `/skills`). `GET /tools` returns 200 (the index HTML) and React renders
`UnknownRouteFallback` (`path="*"`). Probing `/tools` was a bad query — the route does not exist
by design. No change made.

**D1 — Duplicate sidebar entry "Devices" + React duplicate-key warning (LOW-MED, FIXED in source).**
`qubit-devices` plugin declares `tab.path=/devices`, which is ALSO a builtin nav item
(`App.tsx:361`). `buildNavItems` pushed both, so the sidebar rendered TWO links to `/devices`
("Devices" and "Qubit Devices") and React logged a duplicate-key error
(`key={item.path}` at `App.tsx:855`) on EVERY page. Playwright confirmed 2 anchor tags:
`{"count":2,"texts":["Devices","Qubit Devices"]}`.
Fix (`1b61754e`): skip a plugin tab whose path already exists in the nav list.
Verified: `tsc -b` reports ZERO errors in App.tsx; `vitest run resolve-page-title.test.ts` 9/9.
**Not live yet** — the running server serves the prebuilt `/assets/index-*.js`; applying needs a
frontend rebuild + gateway restart, which is the OWNER's call.

### Needs the owner's call
- **SQLite WAL advisory** (env): migrate the embedded SQLite to ≥3.51.3 via `qubit update`.
- **pixellab MCP token** unset → 12 warnings + pixellab tools unavailable. Set
  `PIXELLAB_API_TOKEN` in the profile `.env` if these tools are wanted.
- **D1 rebuild**: to make the sidebar fix live, rebuild `frontend/qubit-web` and restart the
  gateway.

## 7. COULD NOT VERIFY (honest list)

- The live dashboard serves the PREBUILT bundle, so the D1 fix is verified at source+typecheck+unit
  level only; it is not exercised in the running page (a rebuild would be required, and rebuilding
  the live server is out of scope).
- Owner's private conversation content (previews are `[REDACTED]` server-side) — not read, by design.
- WhatsApp/Telegram/Discord inbound message delivery — only adapter "Connected" status and the
  gateway's registration logs were checked, not a live inbound round trip.
- `/kanban` shows 0 tasks; that is an empty board, not verified against an expected non-empty state.
- Cron job execution: schedules and last/next run times render, but no job was triggered
  (triggering would mutate cron state — out of scope).
