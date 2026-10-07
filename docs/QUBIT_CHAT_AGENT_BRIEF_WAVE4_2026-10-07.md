# /qubit-chat wave 4 — voice-first routing + model picker + vision (2026-10-07)

## Authority (read these first — they are the spec, not this file)

- **`docs/DECISIONS.md` ruling #26 (2026-10-02)** — VERBATIM: *"`/qubit-chat`,
  Right-Ctrl, and the wake command must be able to do **everything `/chat` and the
  terminal can do — and more, never less**. The local low model stays the default for
  speed, but the capability set must remain the full one... voice turns dispatch through
  the same full agent turn path /chat uses, on the same broker / policy / audit /
  confirmation / e-stop authority path. Destructive tool calls stay confirm-gated."*
- **`skills/voice-routing/SKILL.md`** — the routing lanes (sticky, templates, classifier).
- **`docs/QUBIT_JEV_BRAIN4_ROUTING_PLAN_2026-09-27.md`** — the routing seams.
- **`docs/PARITY.md`** — the reference-dashboard parity target.

## Owner directive (this wave)

`/chat` MUST be untouched. `/qubit-chat` must be able to:
1. route + serve **voice sessions** (Right-Ctrl / wake word / voice commands)
2. **talk back** (TTS)
3. **route hard work to `/chat`** and **read reports back**
4. execute **voice commands**
5. **create sessions in `/qubit-chat`** from Right-Ctrl / wake / voice commands
   — the owner states this was added today in an OpenCode/VSCode session.
6. change model to **any** model (local or API) from the model provider panel

Env: repo `/home/trick/Projects/Repos/qubit-voicebridge`; service 8890
(`systemctl --user restart qubit-voicebridge.service`, ~18 s).
Token: `TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')`
→ header `X-Qubit-Session-Token: $TOK`. Turn route: `POST /agent/api/qubit/chat/stream`
(body `{session,text}`) — NOT `/turn`.

---

## MEASURED FACTS (verified this session — do not re-derive, but DO re-verify before claiming)

**Voice↔chat session sharing exists.** `voicebridge/orchestrator.py:1108
append_voice_exchange` writes through `chat_transport.store(cfg)` on the SAME session id
as the `/qubit-chat` turn path (`:2180` docstring states it). So a spoken turn is visible
to `/qubit-chat` and vice versa.

**But no NEW sessions are created per voice interaction.** Live session list: 78 visible,
sources `{tui:15, dashboard:39, telegram:1, cron:3, oneshot:1, whatsapp:1}`; the only
voice-shaped row is `voice-chat` (dashboard, 592 msgs). There is **no** per-utterance
voice session and **nothing** with source `voice`/`ptt`/`wake`.

**Local models are missing from the model picker.** `/agent/api/model/options` returns the
`Ollama (local)` row built by `voicebridge/qubit_compat.py:1903 _ollama_options_row`,
which filters to **registered profiles** (`kind==LOCAL and p.enabled and "chat" in roles`).
Registered local profiles are exactly: `qubit-brain4`, `qubit-spark17`, `qubit-spark4`.
Installed-but-unregistered (therefore UNPICKABLE): `qubit-brain` (Llama 3.2 3.6B Q4_K_M,
**157 tok/s** — 19× faster than brain4, and it actually answers), `qubit-brain-vision`,
`qwen3-vl:4b`, `qubit-cand-q4/q5`, `qubit-brain4-pre4b`, HF GGUFs.

**`qubit-brain4` returns EMPTY content via raw Ollama.** Measured 100/300/600 token
budgets: `content=''`, output lands in the `thinking` field, `done_reason=length`, never
terminates. Larger budgets do NOT fix it. It is registered in `voicebridge/profiles.py:158`
and is the DEFAULT voice/chat alias (`runner_contract.py:23 DEFAULT_ALIAS`,
`chat_transport.py:42 DEFAULT_MODEL`). Its registered roles include **`vision`**, which is
wrong for a text model.

**Vision works when VRAM allows.** `POST /agent/api/qubit/chat/vision`
(body `{session, data_url, confirm:true}` — field is `data_url`, NOT `image`) returned
`ok:true` with a real description in 10.9 s using `qubit-brain-vision:latest`. It FAILS
with `VISION_FAILED` when free VRAM < ~3.7 GB; a running game (`AION2.exe`, 3.9 GB) causes
this — not a code bug. `qwen3-vl:2b` (1.89 GB) is a confirmed-available smaller VLM
(registry manifest measured).

---

## TASK 1 — Voice creates /qubit-chat sessions (owner's #5)

Owner: Right-Ctrl / wake / voice commands must **create sessions in `/qubit-chat`**.
Claimed done today in an OpenCode session; **that claim is unverified** — I could not find
such a session in the OpenCode store (newest is 10-05) and the live list shows no
per-utterance voice session.

- (a) Find whether any code creates a `/qubit-chat` (dashboard-source) session on a voice
  turn / wake / PTT. Grep `orchestrator.py`, `voice_commands.py`, `desktop_helper.py`,
  `voice_edit/`, `orb/`. If it exists, verify it live.
- (b) If it does NOT exist: implement it — a voice/wake/PTT interaction opens (or reuses)
  a `/qubit-chat` session with `created_source='dashboard'` so it appears in the picker,
  writing through the SAME canonical store (`chat_transport.store`). Respect
  `ensure()`/`create()` and the wave-2 default (`local_brain_model`) for new dashboard rows.
- **STOP + report** if the session model is ambiguous — do NOT invent a second store.

## TASK 2 — Model picker: every model, local + API (owner's #6)

`_ollama_options_row` (`qubit_compat.py:1903`) lists registered profiles only, so
`qubit-brain` and friends are unpickable.

- (a) Register the installed, chat-capable local models as profiles so they appear —
  at minimum `qubit-brain` (and `qubit-brain-vision` for the vision role). Read
  `voicebridge/profiles.py` for the registration shape; do NOT hardcode in the picker.
- (b) Confirm `POST /agent/api/model/set` can then activate each (the docstring claims the
  row lists "exactly the set `POST /api/model/set` can activate" — make that true).
- (c) Fix `qubit-brain4`'s bogus `vision` role.
- (d) `ChatPage.tsx` uses `ChatSidebar` for model controls; `ModelPickerDialog` exists and
  is UNUSED in ChatPage (`grep -c ModelPickerDialog ChatPage.tsx` → 0). Both call
  `api.getModelOptions`, so this is a wiring gap, not a data gap — evaluate whether to
  wire the full dialog. Keep it surgical (a sibling agent edits ChatPage.tsx).

## TASK 3 — Diagnose why qubit-brain4 returns empty (highest user impact)

It is the registered DEFAULT for voice and chat, and it does not answer via raw Ollama.
The config comment (`voicebridge/config.py:524`) says *"baked EN-only brain, no-think
template"*, and `/api/show` confirms a system prompt + `num_ctx 4096` + Llama-style stops.

- Determine whether the no-think template is absent/broken, or whether a specific
  invocation (options/`think:false`/template) makes it answer. A reasoning model that
  never emits `content` is unusable in voice.
- If it cannot be fixed: report it plainly and recommend the alternative (`qubit-brain`,
  157 tok/s, answers correctly) — do NOT silently switch the default.
- Measure before/after with the same prompt (already benchmarked: `qubit-brain` 157 tok/s,
  `qubit-brain4` 8 tok/s + empty).

## TASK 4 — Vision: API offload with a confirm popup (owner-approved)

Owner confirmed the privacy tradeoff AND requires an in-UI **approve/send-to-API** popup.

- (a) The `images: [b64]` payload (`vision.py:373`) is Ollama-only; OpenAI-style endpoints
  need `{"type":"image_url","image_url":{"url":"data:..."}}`. No translation exists
  anywhere (grepped: zero hits). Build it.
- (b) Let `_analyze` route to a REMOTE vision profile (`_local_chat_raw` is local-only
  today); respect the frontier policy (enabled/allowlist/egress).
- (c) **The popup**: when the resolved vision profile is remote, `/qubit-chat` must ask the
  user to approve sending the image before ANY upload. Never silent egress.
- (d) Also: pull + register `qwen3-vl:2b` (1.89 GB) as a smaller local option.

## TASK 5 — Route hard work to /chat, read reports back (owner's #3)

Verify the existing path end-to-end: a `/qubit-chat` turn that needs heavy work dispatches
to `/chat` (`ORC.launch_task`, route `agent_task`, already proven working) and the report
is read back + spoken. Confirm the report actually lands in the `/qubit-chat` transcript
and that TTS speaks its summary.

---

## RULES FOR EVERY AGENT

- **`/chat` is OFF-LIMITS.** Never modify `frontend/qubit-web/src/pages/TerminalChatPage.tsx`
  or change TUI behaviour. Everything lands in `/qubit-chat` + its backend paths.
- **No silent egress.** Any remote/API image or data send requires an explicit user approval
  in the UI.
- **Do not change an EXISTING session's stored model.** New dashboard sessions default to
  the local brain (wave 2) — keep it.
- **Measure before claiming.** Three prior passes reported success on code that was never
  reachable. A live capture/DOM assertion beats reading source. Re-verify the MEASURED
  FACTS above before acting on them.
- **Never fabricate a data path or a result.** A typed refusal is a valid, valued outcome.
- Note: `execute_code` in a subagent session may fail with a stale-module error — use the
  `terminal` tool with explicit `.venv/bin/python` instead.
- The repo is shared with an OpenCode/VSCode agent doing upstream-sync work in
  `qubit_runtime/` — make surgical edits, and re-check `git status` before restarting.
