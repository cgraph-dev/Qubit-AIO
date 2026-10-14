# OPEN_THREADS.md — durable handoff ledger

**Purpose.** This repo's agent sessions repeatedly died mid-work
(`maximum number of tool-calling iterations`, context compaction, "previous
turn was interrupted mid-run") and each new agent started with no knowledge of
what the last one left unfinished — the owner's words: *"agents stop and you
forget to continue work."* This file is the single durable answer to "what was
in flight?". Read it first; update it at the end of every session.

**Source of record.** OpenViking session `20261010_005652_080bf0`
(744 messages, 2026-10-11 04:53 → 2026-10-12 06:00), archives
`history/archive_001..007/messages.jsonl`. Every item below cites the archive
turn it came from and the on-disk state checked **2026-10-12**.

**State-verification legend:** ✅ DONE (verified on disk) · 🟡 PARTIAL /
needs action · 🔴 OPEN · ❓ UNVERIFIED (claim made, not re-checkable).

---

## 0. THE ONE THING THAT MATTERS MOST

**🔴 The entire session's work is UNCOMMITTED.**

- `git log -1` = `bd6ec9042 Add OmniRoute opt-in integration design spec`
  (2026-10-11 **08:08**). Nothing since — the session's last ~22 hours produced
  **zero commits**.
- `git status` shows **23 changed/untracked paths**, including every fix below:
  `voicebridge/qubit_compat.py`, `webui.py`, `agent.py`, `mcp_servers.py`,
  `voicebridge/kitchen.py` (new), `voicebridge/viking_studio.py` (new),
  `frontend/qubit-web/src/App.tsx`, `OpenVikingStudioPage.tsx` (new),
  `OmniRoutePage.tsx` (new).
- **Next action:** review the diff and **commit** (the owner's own
  `autobackup` branch / `auto-push repos` cron at 23:30 nightly only pushes
  what is committed). This is the highest-value action in the file — until it
  is committed, a crash loses everything and the next agent re-does it all.

---

## 1. Unfinished / at-risk items, prioritized

### P1 — Live verification gaps (things "done" in source but never seen working)

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 1.1 | **Sidebar grouping / collapsible groups not seen rendering** | Source has groups (`App.tsx:398` `"Voice & Speech"`, `:407` `"Tasks & Scheduling"`, children at `:401`/`:410`); bundle rebuilt (`qubit-dist/index.html` 2026-10-12 09:00). Owner said *"sidebar menu still the same wtf"* twice (archive_006:171, archive_007:1) | Recurring complaint across 3 archives; each time answer was "hard-reload, asset hash changed" | Have an agent **drive a real browser** (Playwright was unavailable all session — "socket-path error") to `http://127.0.0.1:8890/chat`, hard-reload, and screenshot the rendered sidebar. Confirm group parents collapse/expand. | 🟡 |
| 1.2 | **OpenViking studio iframe never seen rendering** | `OpenVikingStudioPage.tsx` wrote + typechecks (0 tsc errors, archive_002:56); routes `/api/qubit/viking/studio-health` + `/studio-wiring` now exist in `viking_studio.py` | `viking_studio.py:37-38…138`; route gate `webui.py:1771` | Load `/open-viking` in a browser and confirm `:1933/studio` actually appears in the iframe (CSP is server-side-proxied but the iframe itself was never eyeballed). | 🟡 |
| 1.3 | **Kitchen backend wired but untested end-to-end** | `voicebridge/kitchen.py` exists (built 08:30); route `webui.py:1784` `POST /api/qubit/kitchen/analyze`; uses REA's `_rea_analyze_js` | `kitchen.py:147`, `:192`; `KitchenPage.tsx` modified | Paste a real repo into `/kitchen` and confirm the decompose→capabilities→plan pipeline returns results (not the old "UI SHELL ONLY"). | 🟡 |

### P2 — OpenViking memory stack (largely working; two loose ends)

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 2.1 | **VLM switched to qubit-brain-vision** | `ov.conf` → `ollama/qubit-brain-vision:latest`, `think:false`, `num_ctx:32768`; config `vision.model: qubit-brain-vision` (line 243). Fixed the `empty_response` extraction failure | `~/.openviking/ov.conf`; `runtime/config.yaml:241-243` | None — working. Extraction proven end-to-end (real profile/preference/event files in store). | ✅ |
| 2.2 | **`memory.custom_templates_dir` guardrail** | Set in `ov.conf` → `/home/trick/.openviking/custom_templates`; drops the `avatar`/`introduction` fields that caused the model to **invent a fake avatar URL** | `ov.conf` `memory.custom_templates_dir`; archive_005:50 (proven across 4 clean extractions) | Verify the dir still holds the custom YAMLs and that the loader glob is **non-recursive** (a `memory/` subdir silently loads 0 schemas — pitfall hit in archive_005:50). | ✅ |
| 2.3 | **`每日节奏确认.md` invented Chinese event** | Was flagged as still in the store (archive_005:71,73) | `find` for it on disk = **absent** | None — gone. | ✅ |
| 2.4 | **Andrei Popescu contamination** | Test probe injected fake user persona → overwrote `identity.md`, created false `profile.md` + `preferences/andrei popescu/` (archive_004:108) | `find` = absent; store clean | None — cleaned. **Design risk remains:** OpenViking has no test/prod separation; any input becomes durable truth. Namespace writes before building Qubit Office (multi-bot). | ✅ (risk noted) |
| 2.5 | **Nested-GET 404 for Obsidian notes** | Root cause: `qubit_asgi.py:145` regex rendered `/notes/{name:path}` as `[^/]+`. Fix added: GET route `.+` + `h_plugin_obsidian_note_get` (`qubit_compat.py:9504`, `:16819`) | Route rows present in `qubit_compat.py` | Fix is in the tree but was awaiting a **restart** (archive_005:15). Service last started 08:48; confirm live: `curl /api/plugins/obsidian-memory/notes/<nested/path>`. | 🟡 |
| 2.6 | **`POST /refresh` bridge (Obsidian read staleness)** | Root cause: `_vault_caches` (line 139) populated once, cleared only by `invalidate_cache()`; `/refresh` was not bridged → read API served stale data | Bridge now present: `qubit_compat.py:9504` + `:16803` `h_plugin_obsidian_refresh`, proxy `:16754` | This was the **Qubit Office blocker** (write a note, other bots can't read it). Confirm live with a real write→refresh→read round-trip. | 🟡 |
| 2.7 | **QueueManager drain timeout** | `[QueueManager] Drain timeout for SessionC…` — commit queue not draining cleanly (archive_003:37) | Log line; not re-investigated | Low priority — extractions now land async (~1 min). Watch if extraction latency regresses. | 🟡 |

### P2 — Sidebar / IA (Decisions in force)

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 2.8 | **Do NOT delete the pages** | Verified NOT to be useless across 3 rounds. `/qubit-talk` = voice console; `/webchat` = target of `/webchat` slash command + 3 voice commands (`voice_commands.py:8780`); 5 OpenViking pages = only UI for the local context engine; `/computer` = 975-line impl, 4 endpoints all 200 | archive_002:54,60,69; archive_007:57 | **Leave them.** If revisiting, the live risk is the voice-command + chat-route-contract tests, not the pages. | ✅ (ruled) |
| 2.9 | **Grouping: nothing deleted, only renamed/grouped** | `Talk`→"Voice & Speech", `Qubit Memory Backends`→"Memory Providers", dropped "Qubit" from WebChat; Tasks 5-flat → 1 parent + 4 children | `App.tsx:398,407`; archive_002:101 | Verify in browser (see 1.1). | 🟡 |
| 2.10 | **`/qubit-chat` kept for voice** | Owner ruling: "leave /qubit-chat cos we need that for voice" (archive_006:146) | `App.tsx` | None. | ✅ (ruled) |

### P3 — Tooling & hygiene

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 3.1 | **graphify dropped** | Verdict: redundant with graft, needs an LLM, not in AGENTS.md, no MCP server, was eating ~1.65 GB | `graphify-out*` dirs **absent** from repo root; `.graphifyignore` untracked (new) | Confirm the CLI uninstall decision (`uv tool uninstall graphifyy`) if disk is tight. Don't rebuild. | ✅ (dirs gone) |
| 3.2 | **graft staleness** | `graft/INDEX.md` mtime 2026-10-12 08:10; `graft/` is 944 MB. Prior `graft build` was killed/restarted; `graft check` hung past 5 min twice | `graft/INDEX.md`; archive_006:99,118 | Run `graft build` to completion after the uncommitted changes land; investigate whether `graft` can **scope** to `voicebridge/` + `frontend/` (full scan of 28,904 files is why check/build hang). | 🟡 |
| 3.3 | **REA MCP registered** | `runtime/config.yaml:1067` `rea:` → `command: rea, args: [--mcp]`; 170 tools verified; parses JS/.NET without Ghidra | `runtime/config.yaml:1067-1070` | Registered by hand (sanctioned writer). Needs a **restart** to load into the running process (archive_006:206). | 🟡 |
| 3.4 | **69 "impeccable" skill files in .claude/.cursor/.gemini/.github/.kiro** | An `impeccable` bump 0.1.11→0.1.14 with a 20 MB binary appeared in the tree; flagged "don't let it ride on our commit" | archive_003:127 | Decide: separate commit or exclude. Check `git status` at commit time. | 🔴 |
| 3.5 | **`rea doctor` healthy:false** | Only the native-engine checks fail (hopper/ghidra/ida missing). `node` ✅ `host` ✅ | archive_006:181 | Install Ghidra only if native binary RE is needed; JS/Electron path already works. | ✅ (understood) |
| 3.6 | **Disk reclamation** | Freed ~6.6 GB (uv/npm/pip caches, qubit scratch, graphify bloat). Now `/` 118 G free, 72% used | `df -h` | **Still reclaimable, needs owner call:** ~3.9 GB of `qubit-aio-*`/fix branches (`restore/qubit-aio-full` has 1 uncommitted file); `.config/Code` 6.8 G; `.cache/qubit-pins` 3.6 G; `.wine-flstudio` 3.4 G (owner said delete; **still present**). Check each for unmerged work before deleting. | 🔴 |
| 3.7 | **AION2 = 82 GB of `.steam`** | Explained the "60 GB vanished" — the game install. Not a leak | archive_006:130 | None. | ✅ |
| 3.8 | **Rotate the sudo password** | Agent noted the owner's sudo password is in the session transcript (archive_003:127) | transcript | **Security follow-up — not yet confirmed done.** Rotate it. | 🔴 |

---

## 2. The "14 tests" — actual completion status

Owner sent the tests in three batches (archive_005:10 = 6 core + 3 enhancement;
archive_005:42 = 7 upgrade). The owner counted **"14 tests"** (archive_005:68).
Final tally as reported in **archive_005:71** and **:73**, cross-checked on disk:

**Batch A — 6 core stress tests**
| Test | Verdict | Evidence |
|------|---------|----------|
| 1 Recursive vault linking | PASS | `AI_Brain/Master_Brain_Map.md` (6,713 B) exists |
| 2 Context compression (<30-word L0) | PASS | 27 words, counted from disk |
| 3 Bi-directional schema sync | PASS (test) / **premise false** | inodes differ, no symlink — proven |
| 4 Deep hierarchy blind-query | PASS | `ALPHA-OMECRON-9` returned rank 1, score 0.720 — **but anchor deleted after** (not re-runnable) |
| 5 Ubuntu hardware ingest | PASS | `AI_Brain/Ubuntu_Hardware_Profile.md` (3,187 B) exists |
| 6 Memory sanity cleanup | PASS (0 deletions) | `AI_Brain/Memory_Sanity_Report.md` (2,787 B) exists |

**Batch B — 3 enhancements**
| Item | Verdict |
|------|---------|
| Enh 1 Autonomous watcher | PASS at the time — **but `~/vault_watcher.py` was deleted; not running now** |
| Enh 2 Dataview generator | PASS (block written) — **rendering unverified** (needs Obsidian Dataview plugin) |
| Enh 3 Self-evolution manifesto | PASS — `AI_Brain/Agent_Optimization_Manifesto.md` (5,837 B) exists |

**Batch C — 7 upgrades**
| Test | Verdict |
|------|---------|
| 1 Auto-pruning context optimizer | **NO-OP** — nothing was 72h stale (correct, but not a demonstration) |
| 2 Predictive task caching | PASS — `ov find` returned correct note (0.803); `Cache_*.md` files exist |
| 3 Local code sandbox | PASS — `AI_Brain/Draft_Script_Specs.md` + validated script |
| 4 Git-backed versioning | PASS — vault repo exists (`de700d2 agent(sync):…`), but only 1 file committed |
| 5 Chronos cron injection | PASS — `AI_Brain/System_Cron_Registry.md` (5,528 B) exists |
| 6 Cross-note conflict resolver | PASS — `AI_Brain/System_Conflict_Report.md` (8,786 B) exists |
| 7 Visual metric canvas | PASS — `AI_Brain/Live_System_Canvas.md` (4,946 B) exists |

**Truly abandoned / never ran (the honest gaps):**
- **Test 2 "Semantic Needle in a Haystack" (limbic)** — ❌ **NEVER RAN.** Required
  the `limbic` library, which is broken on this machine (964-byte tokenizer vs
  its 1024 floor). An agent substituted the plugin list endpoint — *not the test
  as written*. Owner accepted OpenViking covers the capability (archive_005:73).
- **Test 6 "Travel Planner"** — ❌ **NEVER RAN** in that batch (owner's message
  was truncated before the instructions). **Later DONE** in archive_006:12 —
  `AI_Brain/Travel/` has 6 files incl. `Rome_Trip_October_2026.md` (5,867 B).
  ⚠️ That run **edited `Point-A.md`** (md5 `710299f5…` → `308b5461…`); revert =
  delete lines 21–23 if unwanted.

**Sidebar / OpenViking items — completed vs abandoned:**
- **Completed:** OpenViking VLM fix, extraction pipeline, `ov` CLI CRUD, Obsidian
  **writes**, `custom_templates_dir` invention guardrail, studio backend routes,
  `/refresh` bridge (code), REA registration, graphify removal, save ~6.6 GB.
- **Abandoned / still open:** browser-render verification of the sidebar + studio
  page; the limbic Test 2; Obsidian read-staleness **live** confirmation; the
  3.9 GB branch cleanup decision; sudo-password rotation; and — above all — the
  **uncommitted working tree**.

---

## 3. Why this recurred, and the mechanism to stop it

**Root cause of the amnesia.** Each archive ends with a "Still open / needs you"
list (archive_002:161, 003:127, 004:108, 005:73, 006:206, 007:57) written into
the *chat transcript* — not into a file. The next agent, starting fresh, never
reads the prior transcript. So the same "needs a restart" / "still pending"
items re-appeared for days, and the owner saw "work stopped" as breakage.

**Smallest durable fix (this file).** `docs/OPEN_THREADS.md` is now the ledger.
The convention: **at the end of every turn that leaves anything incomplete,
append/update the relevant row here** — one line, with `where it got to`,
`evidence`, and `next action`. Cheap, greppable, version-controlled, and it
survives compaction because it is on disk.

**Agent rule (recommend adding to `AGENTS.md` — NOT yet applied; the file is
write-protected and the edit needs owner approval):**
> Before starting work, read `docs/OPEN_THREADS.md`. When you stop — for any
> reason: iteration cap, compaction, interruption — update it with what is still
> open and the exact next action. A turn that leaves work incomplete and does
> not update this file is an incomplete turn.

**Enforcement hook (cheap, optional):** the `auto-push repos` cron (23:30) could
append a diff-stat line + `git status --short` to a `docs/OPEN_THREADS.log` so
even an abrupt kill leaves a breadcrumb of the working tree's dirty state.
Not implemented here — recommend only if the agent rule alone proves
insufficient.

---

## 4. Office game frontend programme — session 2026-10-14

Working from `docs/game-office/frontend-game/README.md` (the audited baseline + rebuild plan).
Owner directive: *"apply and start"*, then *"verify and fix and continue"*.

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 4.1 | **Frontend amendment applied** | Appended to the LIVING log `05-AMENDMENT-LOG.md` (§ Frontend amendment): C4 reaffirmed, C7 clarified, viewpoint-vs-relocation split, blanket source-string bans superseded, execution partition superseded, plus the T1–T6 test-change list. No C-rule or frozen contract edited in place | `docs/game-office/05-AMENDMENT-LOG.md` § Frontend amendment | None — done as the plan's F00 required | ✅ |
| 4.2 | **`docs/STATUS.md` does not exist** | Repo-context and several authority docs reference it; `test -e docs/STATUS.md` = **absent**, not merely unread. No substitute invented; resolving authorities named instead | `frontend-game/README.md` § Authority (updated 2026-10-14) | Owner: name the governing authority, or restore `docs/STATUS.md` | 🔴 |
| 4.3 | **F01 baseline + fixture done** | 9 office files archived with SHA-256 (`evidence/baseline-archive/`); `tower-recording.json` copied byte-identically (sha `fbb9a00c…`) with `PROVENANCE.md`; 606 sprite assets + the 7-module load order recorded | `evidence/F01-BASELINE-INVENTORY.md`, `fixtures/PROVENANCE.md` | None | ✅ |
| 4.4 | **F02 done and VERIFIED LIVE** | Three defects fixed in `qubit-office.js`: (1) listeners never bound — effect read `canvasRef.current` at mount while the loading branch renders no canvas; (2) Fit/Reset no-ops — `fitRef` overwritten by the live camera every redraw, and `fit`/`reset` set `_auto` while `redraw()` read `_autoZoom`; (3) inspector went stale — `setPick` stored the payload object | **Live browser probe after a bridge restart**: canvas listeners `[]` → 6; `wheel_changed_pixels` false → **true**; `drag_changed_pixels` false → **true**; panel age advanced `165547s` → `165548s` across a poll | None | ✅ |
| 4.5 | **F03 done** | `opts.state` threaded into `drawAgentSprite`, so the atlas resolves all twelve poses instead of falling back to walking/idle (`sprites.js:938-944` documents the seam) | `tests/test_office_interaction_lifecycle.py`; sprite + visual suites green | Atlas pose IDs not independently re-derived by the live probe — stated as the one F03 claim not pixel-proven | 🟡 |
| 4.6 | **F04 done — engine CHOSEN: Phaser 4.2.1** | Owner ruling *"best one with most features and newest and most capable to work with ai"* → `phaser@4.2.1` pinned exactly. MIT (Richard Davey / Phaser Studio), source unmodified, no Phaser code copied in. `PhaserSceneHost.ts` implements `SceneHost` beside the retained `CanvasSceneHost` fallback (one-argument swap). **Live browser proof**: Phaser 4.2.1 boots in Chrome with **zero errors**; `textures.createCanvas` / `getContext` / `refresh` all present and a written pixel reads back; canvas count 1 → 0 after `destroy(true)`. **Cost: 7.29 MB raw / 1.25 MB gzip** — ~500× the rest of the plugin; NOT promoted to the served entry, held by a test | `tests/test_office_game_packaging.py` (20 passed); `evidence/F04-ENGINE-DECISION.md`; decision recorded in `frontend-game/10-DECISIONS.md` | **Owner decision:** whether to promote `qubit-office.game.js` as the served entry given 1.25 MB gzip, or custom-build/lazy-load Phaser. Also: add Phaser to `THIRD_PARTY_NOTICES.md` before publication | ✅ (decision + host) / 🔴 (promotion) |
| 4.7 | **Tests added, full game suite green** | `test_office_interaction_lifecycle.py` (18), `test_office_game_packaging.py` (12), `test_office_game_adapter.py` (15), `test_office_game_navigation.py` (16); T2 C4-over-the-whole-plugin-dir in `test_office_tower_plugin.py`; `test_office_inspector.py` harness adapted (not weakened — both MUTATION tests still pass) | `61 passed` (the 4 new suites) and `273 passed` (12 office suites) | None | ✅ |
| 4.11 | **F05 done** | `game/types.ts` typed from the REAL recorded payload + `game/adapter.ts`: serialized abortable loads, revision fence, stale retention, visibility pause, provenance. Tests caught and I fixed 2 real defects (dispose left `loading` true; a failed read claimed a payload while reporting provenance `none`) | `tests/test_office_game_adapter.py`; fence falsifiable (disabling it fails the out-of-order test) | None | ✅ |
| 4.12 | **F06 done — BOTH halves** | `game/navigation.ts` pure reducer (5 place kinds, portal graph DERIVED from the real floorplan, deep-link encode/decode, capped Back, typed refusals) + `game/input.ts` input-context router (containment chain world ⊂ panel ⊂ modal ⊂ text ⊂ editor; innermost-first). Wired into the shell: `keydown` on the container, routed, applied via `travel`/`back`. **Two real bugs found by tests and fixed**: skipping the `lobby → floor 1` portal made the boardroom unreachable; the panel guard `!active.includes("world")` never fired because `world` is always the base context, so the world walked under an open panel | `tests/test_office_game_navigation.py` (16) + `tests/test_office_game_input_context.py` (16); 3 falsification runs (typing guard → 4 fail; portal skip → 1 fail; deleted binding → 1 fail) | None | ✅ |
| 4.13 | **Wiring test caught being a false positive** | `test_the_built_artifact_actually_wires_the_keyboard_and_navigation` originally asserted `"keydown" in src` — which passes even when only `removeEventListener` remains, i.e. on a completely dead keyboard. Strengthened to match `addEventListener("keydown",` by shape; re-falsified and confirmed it now fails on the dead-binding mutation | mutation: deleting the binding + re-running the strengthened assertion | None | ✅ |
| 4.8 | **Bridge restarted for verification** | `systemctl --user restart qubit-voicebridge.service`; MainPID 153843 → **281483**. Clean start, listening on 0.0.0.0:8890 | `journalctl --user -u qubit-voicebridge.service` | None — user's call was granted | ✅ |
| 4.9 | **The office API is `/agent/api/office/tower`, not `/api/office/tower`** | A bare `curl /api/office/tower` returns 404 and looks like a missing route; the SPA rewrites the `/agent` prefix (`webui.py:2484-2488`). With the session token from `window.__QUBIT_SESSION_TOKEN__`, `/agent/api/office/tower` returns **200** with real data | `webui.py:2484`; live 200 | Worth knowing before anyone debugs a "missing" route again | ✅ |
| 4.10 | **All work UNCOMMITTED** | This session: modified `05-AMENDMENT-LOG.md`, `frontend-game/README.md`, `60-IMPLEMENTATION-PLAN.md`, `OPEN_THREADS.md`, `qubit-office.js`, `qubit-office.bundle.js`, `tests/test_office_{inspector,tower_plugin}.py`; new `docs/game-office/frontend-game/` (+evidence, +fixture provenance), `plugins/qubit-office/game/` (7 files), `frontend/qubit-web/vite.office-game.config.ts`, `tests/test_office_game_{packaging,adapter,navigation}.py`, `tests/test_office_interaction_lifecycle.py` | `git status --short` | Owner: the pre-existing 43-file dirty tree plus these. Not committed — the owner's call (see § 0, still the top of this file) | 🔴 |

**Next in the plan:** **F08 (responsive shell, panels, settings) is PARTIAL** — see its STATUS block in
`60-IMPLEMENTATION-PLAN.md` for what landed and the four items still open (the world not growing to fill
the shell was the largest; it is now fixed and verified live). **F07 is done** (playable slice, 17 tests,
committed `186abf7d4`); **F09 (character roster, profile, dialogue shell)** and **F12 (operational
panels)** are unblocked and need no new engine work.

---

*Generated 2026-10-12 by mining session `20261010_005652_080bf0` archives
001–007. Re-verify any 🟡/🔴 row against the worktree before acting — status
claims here are as-of this date and the tree is still changing.*
