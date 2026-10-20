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

## Wave 7 — 2026-10-20 (10 lanes; defects closed, 128 dead symbols)

Full evidence in `docs/STATUS.md` (top entry). Durable state:

- ✅ Closed: the 9 parity defects (memory `patch`, Alibaba refusal classes, TUI history identity, ACP read-only catalog, session-search read path, bot receipt lock, `mark_job_run(execution_id=)`, session-key namespace) and F2 (duplicate `_start_gateway_*` helpers). Theme callbacks were already correct on HEAD; the upstream test was ported.
- ✅ Measured on merged main: dead symbols **167 → 128** (0 new); absent test files **783 → 764**; `test_source_channel_integration.py` **23 failed → 33 passed**; 158 focused tests green.
- 🟡 Not live: boot/worker changes remain merged-but-not-running (no service restart).
- 🔴 New defects exposed by the parity lane, unmodified: (1) `gateway/session.py:1297::list_sessions` raises `TypeError` comparing naive vs aware `updated_at` when recovered UTC routes meet legacy local-time routes; (2) ACP credential binding is replaced after the editor path (`tests/acp_adapter/test_policy_identity_compat.py`).
- 🔴 Owner decisions still open: F4 control-socket bind failure is fatal in `run_bootstrap.py:855` (old body and `control_socket.py:191` say non-fatal); F5 exclusions added to `docs/FROZEN_TARGET.md` without sign-off.
- 🔴 Remaining: 128 dead symbols (deferred blockers in `TRIAGE.md` §Wave 7), 764 absent + 894 short-port test symbols, retirement.

---

## Wave 6 — 2026-10-20 (residuals + dead symbols + test parity + adversarial verify)

Full evidence in `docs/STATUS.md` (top entry). Durable state:

- ✅ Landed: `same_create_retry` + contract test (`5c578fcdd`); source-release test-stub sync (`b9e1a3b3b`); dead symbols **216 → 167** on merged main (0 new) incl. worker output-log port (`023b9e479`); 20 test files ported, absent tests **804 → 784** (`8f0e0fafe`); connectors-only reader + brand copy fixed (`74ecc8410`).
- 🟡 **Not applied live:** the boot/worker changes are still merged-but-not-running (no service restart by design).
- 🔴 **Owner decisions needed:** F4 — a control-socket bind failure is now fatal in `run_bootstrap.py:855`, while the old `run.py` body and `control_socket.py:191` call it non-fatal. F5 — the dead-symbol wave added exclusions to `docs/FROZEN_TARGET.md:51-55` (owner-declared table) without sign-off.
- 🔴 **Next structural lane:** F2 — seven `_start_gateway_*` helpers exist twice (`run.py` vs `run_bootstrap.py`) with divergent bodies; two tests import the stale copies (`test_multiplex_hot_serve.py:167`, `test_startup_restart_race.py:382`). De-dup carefully; `_start_gateway_replace_existing_instance` is live in both contexts.
- 🔴 **9 production defects found by the test-parity lane, unmodified:** memory single-op `patch` unreachable (`tools/memory_tool.py:104,136`; `qubit_cli/write_approval_commands.py:105`); Alibaba content-policy refusal classes (`agent/error_classifier.py:359`); repeated-answer history reconcile by `(role,text)` instead of row identity (`tui_gateway/server.py:3153`); theme callbacks absent (`tui_gateway/agent_callbacks.py:146`); ACP `list_sessions` creates storage / ignores read-only catalog (`acp_adapter/session.py:210`); session-search read path creates the DB (`tools/session_search_tool.py:629`); bot receipt reader lacks the writer lock (`tools/bot_live_delivery.py:459`); `cron.jobs.mark_job_run` lacks `execution_id` (`cron/jobs.py:2424`); duplicate session-key namespace inverse (`gateway/session.py:666` vs `qubit_state_keys.py:77`). Each has the upstream test named in `docs/STATUS.md`.
- 🔴 **Still open:** 167 dead symbols; 784 absent + 894 short-port test symbols; retirement.

---

## Wave 5 — 2026-10-20 (5 lanes merged; boot fix not live)

Full evidence is in `docs/STATUS.md` (top entry). Durable state:

- ✅ Merged on `main`: `49f749514` (scoped turn half + fanout overflow), `c7f5c7409` (3 security/correctness defects), `b0a3d8613` (titled session / welcome trial / owner prune), `bd4e2d913` (dead-symbol tranche 3 + 17), `f3a1e6898` (boot reservation + session authority; `worker.adopt` rung 4 on a scratch home).
- 🟡 **Not applied live:** the boot/worker changes are merged but the running `qubit-gateway.service` still executes the pre-merge code (no restart by design). A restart is needed to exercise it live; prove on a scratch home first if in doubt.
- 🔴 **Open residuals (pre-existing, named by the lanes, not fixed):**
  - ✅ Fixed on branch `fix/residual-gw-wave6` (`f33527445`): `same_create_retry` is defined in `qubit_state_local.py`, with both call sites' retry/conflict paths tested.
  - ✅ Already fixed in `main` by `6a8dbf5c1` (canonical contract module imported), pinned by `d4bd5e917`; the boot path now validates against `CanonicalSnapshot`, not `SessionCreateResult`.
  - `test_source_release_channels.py` / `test_source_release_probe.py` still have pre-existing failures unrelated to `_probe`/forward-only (later `_prepare_git_command` lambda signature, zip fallback, config-probe subprocess).
- 🔴 **Remaining milestone work:** 216 dead symbols (13 deferred architectural ports, evidence in `TRIAGE.md`); 1,691 test symbols / 804 files (non-blocking); retirement steps. Re-run `qubit_runtime/scripts/check_dead_symbols.py` and `scripts/audit_sync_completeness.py` rather than quoting these counts.

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
panels)** are DONE — see § 4.14 below.

### 4.14 — Current state of Qubit Office (re-verified 2026-10-14, evidence-backed)

VERIFIED by running, this session. "VERIFIED" below means the exact command was run and its output is
the evidence; "NOT VERIFIED" means it is claimed or plausible but was not exercised live.

| # | Claim | State | Exact evidence |
|---|-------|-------|----------------|
| 4.14a | **The REAL third-party office app is vendored** — `github.com/marmottajr/habblaud`, MIT, commit `4ccce2537472`, client build output only (server / Claude Code mod / Docker NOT vendored) | **VERIFIED** | `.venv/bin/python scripts/vendor_habblaud.py --check` → `ok: 77 files, **2147631** bytes, commit 4ccce2537472` (exit 0). `PROVENANCE.json` records source URL, commit, MIT licence and per-file sha256. Lives at `frontend/qubit-app/plugins/qubit-office/habblaud/`. **CORRECTED 2026-10-15:** this row previously read `--src /tmp/habblaud-full --check` and `2147125 bytes`; `--check` needs no `--src`, no checkout exists at that path, and the byte total had drifted (the tree is also now English-pinned, hence 2147631 not 2147637). |
| 4.14b | **The vendored app is served and renders** | **VERIFIED (served AND renders)** | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/` → **200**. The body is the real Habblaud `index.html` (pt-BR, `<canvas id="world">`, module `bundle/main-DND_b4_E.js`). Bundle + an asset also return 200. **Pixel-verified** by headless-chrome probe: default `/qubit-office` frame reports `Habblaud Connected 4 rooms 10 agents` with **0 page errors**; an earlier probe of the same page counted **22,776 non-background canvas pixels across 162 tones** (a black/empty canvas would be one tone). |
| 4.14c | **Which implementation is the default on `/qubit-office`** | **VERIFIED — the VENDORED APP** | `game/entry.ts` mounts the vendored app by default; the hand-built shell is opt-in via `?shell=1`. Live probe (headless chrome): plain `/qubit-office` → **1 iframe**, src `/plugin-apps/qubit-office/habblaud/`, frame text `Habblaud Connected 4 rooms 10 agents`; `?shell=1` → **0 iframes, 2 canvases** (the old shell). NOTE: `manifest.json` line 11 still declares `"entry": "qubit-office.game.js"` — that is the plugin entry, and the *entry itself* chooses the app. Both statements are true at different layers; do not read the manifest as "which renderer you get". An earlier revision of this row claimed the hand-built renderer was the default — **that was measured before the inversion landed and is corrected here.** |
| 4.14d | **The default/fallback switch** | **VERIFIED — `?shell=1` opts back into the hand-built shell; `?app=1` is a compat alias that also wins** | `game/entry.ts:81`: `const wantShell = params.has("shell") && !params.has("app")`. **CORRECTED 2026-10-15:** this row previously claimed "the earlier `?app=1` form is gone". It is NOT gone — it is a deliberate backward-compatibility alias for bookmarks from the first Habblaud preview, and it *overrides* `shell` (`?shell=1&app=1` mounts the app). The vendored app is still the unconditional default. `tests/test_office_game_shell.py::test_the_default_renderer_is_the_app_and_the_shell_is_opt_in` now evaluates the real expression under node for all four param cases. |
| 4.14e | **F09 (character roster, profile, dialogue shell) is DONE** | **VERIFIED (tests pass)** | `tests/test_office_game_character.py` — real count in § 4.14g (run together with F12). |
| 4.14f | **F12 (operational panels, boardroom) is DONE** | **VERIFIED (tests pass)** | `tests/test_office_game_operational.py` — real count in § 4.14g. |
| 4.14g | **The office game suite passes** | **VERIFIED 2026-10-15** | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` → **`212 passed in 110.04s`**, exit 0, **0 skipped**. **CORRECTED 2026-10-15:** this row previously read `211 passed`, and at an earlier point `3 failed, 209 passed` — the three failures were stale tests pinning the pre-rewrite contract of `entry.ts`/`office-api.ts` and were reconciled in `d50a56945`. F09+F12 alone: `pytest tests/test_office_game_character.py tests/test_office_game_operational.py -q` → `66 passed`. |
| 4.14h | **The vendored app is the DEFAULT renderer of the Office tab; the F00–F17 packets describe the fallback** | **VERIFIED 2026-10-15** | **CORRECTED:** this row previously called the vendored route a "PARALLEL track" with "no reconciliation document yet". Both parts were stale. The vendored app is the default at `/qubit-office` (see § 4.14c) and the hand-built packet renderer is the opt-in fallback (`?shell=1`). Reconciliation now exists: `docs/OFFICE-DOC-RECONCILIATION.md`, plus `docs/OFFICE-LIVE-VERIFICATION.md`, `docs/BROWSER-PARITY-FINDINGS.md`, `docs/FIREFOX-PARITY-FINDINGS.md`, `docs/OFFICE-ACCEPTANCE-AND-RISK.md`, `docs/VENDOR-REPRODUCIBILITY.md`, `docs/VENDOR-ATOMIC-PUBLISH-FIX.md`. The packets remain the plan of record for the hand-built renderer they describe. |
| 4.14i | **`docs/STATUS.md` exists** | **VERIFIED present 2026-10-15** | `ls -la docs/STATUS.md` → `6172` bytes. **CORRECTED:** this row previously stated the file did not exist and recorded its absence as a labelled unknown. It exists now (as of 2026-10-15 17:18). |
| 4.14j | **Work is COMMITTED; the tree is clean** | **VERIFIED 2026-10-15** | `git status --short \| wc -l` → **0**. **CORRECTED:** this row previously read **49** dirty entries with HEAD at `3d7989e36`. HEAD is now `fba27ca06` (through `c3b005f18` checkpoint → `ead2053b5` clarify/vendor/office rescue → `80f232080` A8 token fix → `baf6093f5` clarify tests → `fba27ca06` vendor preload assertions). |

### 4.15 — Office test toolchain: the `NODE_ENV=production` trap (2026-10-14)

`pytest tests/test_office_game_*.py` errors when `NODE_ENV=production` is set in the shell: `npm ci` /
`npm install` then defaults to `--omit=dev`, installs no devDependencies, and
`frontend/qubit-web/node_modules` loses `typescript` and `vite`. `scripts/build_dashboard.sh` also
`rm -rf`s that dir unless `KEEP_NODE_MODULES=1`. Two symptoms: the office suites used to echo
`N skipped` as a **false green** (fixed — they now error, see below), and the packaging suite failed
with `Cannot find module .../typescript/bin/tsc`. Recovery — set `NODE_ENV=development` first, then:

```
npm install --include=dev --prefix frontend/qubit-web typescript vite   # restore the tree copy
KEEP_NODE_MODULES=1 scripts/build_dashboard.sh                          # rebuild keeping node_modules
export QUBIT_TSC=/path/to/tsc                                          # or point straight at one
```

`tests/_office_toolchain.py` self-heals: when no `tsc` resolves it installs `typescript@5.9.2` into
`~/.local/state/qubit-voicebridge/runtime/cache/scratch/ts-tool` (outside the tree, so a dashboard build
cannot delete it) and the suites **RUN**. With `QUBIT_TSC_SELF_HEAL=0` and no compiler, every office
suite **ERRORS at collection** naming the exact install command — never a skip. Falsified both ways
2026-10-14 (hide `tsc` + `SELF_HEAL=0` → collection error; hide `tsc` + self-heal → `18 passed`).

**Intermittent test failure — ROOT-CAUSED AND FIXED 2026-10-15.** Running
`pytest tests/test_office_vendor_habblaud.py tests/test_office_assets_vendored.py tests/test_office_plugin_app.py -q`
once produced **`5 failed, 81 passed`**; four re-runs produced `86 passed` every time; no root cause was found
at the time. The root cause is now identified and repaired:

`scripts/vendor_habblaud.py::vendor()` **deleted the destination tree and wrote `PROVENANCE.json`
mid-assembly**, so any concurrent reader — including these tests, which hash the tree against that
record — could observe a half-generated directory. **Measured on the old algorithm: 0 of 90,818
concurrent reads were consistent; 19 were inconsistent.** The intermittent first-run failure was a
**correct detection of an inconsistent tree**, not a broken test.

The fix (commit `ca03150e8`) makes publish order the consistency barrier: the record is unlinked
first, every file is written, and the record is written **last** via temp-file + `os.replace`; `LICENSE`
is copied the same way. Full write-up: `docs/VENDOR-ATOMIC-PUBLISH-FIX.md`, including one residual
hole that is **deliberately not closed** (~1 in 600k reads can still catch an in-place file overwrite,
because `copytree` writes non-atomically). Treat the vendored-app hash assertions as **reliable but
still fenced against concurrent re-vendors** — and assert **exit code + file count, never the byte
total**, which drifts legitimately across rebuilds.

---

*Generated 2026-10-12 by mining session `20261010_005652_080bf0` archives
001–007. Re-verify any 🟡/🔴 row against the worktree before acting — status
claims here are as-of this date and the tree is still changing.*

---

## SESSION 2026-10-16 — 5-lane catch-up + rebrand ruling

**State verified on disk 2026-10-16 ~08:40.** Branch `pre-sync-worktree-snapshot`,
HEAD `2808e2a58`, tree clean, **85 ahead / 0 behind `canonical/main`** (a clean superset, not
a divergence). Tagged `session/2026-10-16-catchup`.

### 🔴 THE LOSS RISK: nothing is pushed

`git branch -r --contains HEAD` is empty — every commit below is **local-only**. Remote is
`canonical` → `github.com/cgraph-dev/Qubit-AIO.git`. Push before doing anything else.

### ✅ Landed this session (12 commits)

| Commit | What |
|---|---|
| `080514a2d` | **SUPERVISED_CHILD dual-read**: writers emit `QUBIT_*`, 10 readers accept both. Fixes the `venv_sync.py:308` split-brain. 22 tests; 7 proven red without the change. |
| `3301809e6` | lane-6 report (`REPORT-supervised-marker.md`) |
| `70260712c` | **`docs/REBRAND-SWEEP-PLAN.md`** — the 630-symbol sweep design |
| `63b458a41` | `scripts/upstream_drift_report.py` + pin reconciliation (reads `FROZEN_REV` from one home, fails closed; **did not touch** the `69948c0057` anchor — still 16 occurrences) |
| `73a78da84` | drift report doc |
| `2808e2a58` | `docs/DRIFT-REPORT-2026-10-16.txt` — verified, deterministic |
| `6db59dfeb` | **HEAD defect fix**: branch rows lacked `message_identity()`; ported from `sync-w24` `8efc0dd62`. Verified independently. |
| `6ee2adf0c` | Office memory-receipt fix (concurrent session's work, frozen + attributed) |
| `516a5390a`…`62e7a4518` | `scripts/check_commit_hygiene.py` + 27 tests + report |
| `d2c467e9a`,`1b21b26a1`,`b8cd9c233` | `sync-w24` safe subset (2 of 86) + manifests |

### Verified findings worth not re-deriving

- **Upstream drift = 3,895 commits, but only 298 genuine production-file additions.** 1,114
  we already carry (upstream merely newer); 127 deleted. The commit count overstates work ~5×.
  Re-run: `scripts/upstream_drift_report.py` (~9 min, deterministic).
- **`sync-w24`'s 86 "TAKE" paths: 84 are unsafe.** Proven: all 84 delete 1–67 non-blank HEAD
  lines, and 43 carry Hermes-branded branch-only content. `deferred.json` has per-path detail.
- **`sync-w24` is not a stale dead branch** — it carried a real fix HEAD never took
  (`8efc0dd62`), now landed. Re-read it for content, not only for conflicts.
- **Agent lane `catchup-1` went the INVERSE direction** (~140 `QUBIT_*`→`HERMES_*` in tests,
  treating drift as spec). Parked at `parked/catchup-1-hermes-direction` with a git note.
  Not salvageable under the owner ruling except one `lambda force, **kw` signature fix
  (recorded in the sweep plan).
- **`hermes_cli` test lane hit its iteration cap** at 338 failing (from 535). Unfinished —
  needs re-dispatch. Its orphaned edits are at `/tmp/preserved-ag1-leftovers.patch`.

### 🟡 Open

- **Push the branch.** Highest priority; nothing is backed up.
- **`hermes_cli` tests**: 338 failing remain (`qubit_runtime/tests/hermes_cli/`). Re-dispatch.
- **Rebrand sweep**: Phase 0 (manifest tool) not started. See `docs/REBRAND-SWEEP-PLAN.md`.
- **`check_commit_hygiene.py` reports 13 live violations** — the peer worktree dirt is
  expected; the `stranded_branch_commits` one is the push issue above.
- **Stale stash recovery**: another session's 590-line office WIP was found in `stash@{0}` and
  preserved to `/tmp/preserved-stash0-office-590lines.patch` (sha256 `4a16d9bc…`). **The stash
  stack is shared across worktrees — never bare `git stash pop` in a worktree you did not
  create.** Its owning session should reclaim it.

---

## COVERAGE GAP found 2026-10-16 (during the 8-lane dispatch)

**🔴 Root `tests/` — 904 files — is not covered by any current lane** (all 8 lanes target
`qubit_runtime/tests/*`). It is a real separate tree, not a symlink to `qubit_runtime/tests/`.

It has live failures. Sampled at `9facd1ca9`:

- `tests/agent/test_auxiliary_client.py::TestGetTextAuxiliaryClient::test_returns_none_when_nothing_available`
  fails: the test stubs `_read_nous_auth`, `_resolve_codex_credential_and_base` and
  `_resolve_api_key_provider` all to `None`, yet `get_text_auxiliary_client()` still returns an
  `openai.OpenAI` instance — so a **fourth resolution path** exists that the test does not stub.
  Likely a stale test (a route landed upstream) rather than a code bug, but that must be proven,
  not assumed: check whether the new path is legitimate and the test is behind, or whether the
  path should have been gated on credentials.

**Action for the next session:** dispatch a lane for `tests/` (root) with the same protocol as
lanes 8–11 (measure baseline first, per-class verdicts, isolation check, PROGRESS.md trail).
Also still uncovered: `frontend/` (TS/Vitest) and the `ui-tui` vitest suite.

**Why it was not dispatched immediately:** 8 lanes were already running and loadavg was 19.06
with 22 GB free. Adding a 9th heavy pytest lane risks an OOM-kill that would corrupt a
running lane's run, which costs more than the delay.

---

## 2026-10-16 (later) — restart recovery, a caught regression, and stale lesson

### The regression that validated the sweep plan

A lane renamed one env var to its canonical `QUBIT_*` name and added a dual-read alias
(`anon_auth.GUEST_ONBOARDING_ENV`). It **looked** correct and followed the documented
single-write/dual-read pattern — and **broke 20 tests** (baseline 1 failed → 21 failed).

Cause: it migrated the *reader* while the **writers and the tests still set the old spelling**.
Dual-read is only half a migration. Reverted as `ad2e66acc`.

**Rule for any future env-var rename (all three together, never one):**
readers, every writer (setenv / `.env` line / launcher / systemd unit / launchd plist), AND the
tests. Then report a **before/after failure count** — the harness makes this one run, and it is
what would have caught this in seconds instead of by hand.

### 🔴 HARD RULE: never bare `git stash`/`pop` in this repo

The stash stack is **shared across worktrees** and holds **13 entries from prior sessions**
(`stash@{0}` is a 590-line office WIP, preserved to `/tmp/preserved-stash0-office-590lines.patch`,
sha256 `4a16d9bc…`). A bare `git stash pop` collided with it **twice in one session** and produced
4 merge conflicts in files that were never mine.

To get a baseline without touching the stash: `git checkout HEAD~1 -- <file>`, run, then
`git checkout HEAD -- <file>`. Never `git stash`.

### Verified Phase 0 tooling (branch `agent/catchup-12`)

`scripts/rebrand_sweep.py` — runs, gates CI, honours the pin deny-list. Measured at `ad2e66acc`:

```
symbols=926 (hermes=670 qubit=256)  dual_spelled=91 (undeclared=88)
boundary=432   deny_list=18 files
```

So the plan's original figures (630 / 231 / 152) have **drifted** — recompute, do not quote them.
`--check` exits 1, listing 88 symbols spelling both ways in code with no declared dual-read.
Safety verified: the frozen pin `69948c0057` is still 16 occurrences in `scripts/`, and the branch
modifies none of the 18 deny-listed files.

### Landed during recovery (all pushed to canonical/main)

| Commit | What |
|---|---|
| `2c9bf297f` | **Fourth branch-copy path** carries `message_identity()` — sibling to CLI/workdir/Desktop. Falsified 3-red-without / 3-green-with. |
| `ad2e66acc` | Revert of the `GUEST_ONBOARDING` regression |
| `0b21c3a5f` | Phase 0 sweep tooling (recovered) |
| `7f2a2098c` | sync-triage hunk matrix harness |

Rescued-but-unlanded: a `QUBIT_GUEST_ONBOARDING` change that needs writers+tests moved with it.
---

## 2026-10-17 — Desktop vitest: node-builtin tests are dead in the jsdom project

**🔴 OPEN (documented, not fixed).** `apps/desktop/src/**` tests that import a `node:`
builtin cannot run under the `ui` vitest project, which pins `environment: 'jsdom'`.
Vite externalizes `node:` for browser compatibility and jsdom has no loader for it, so the
suite fails at import with `Error: No such built-in module: node:` — reported as `(0 test)`,
i.e. the file never even collects.

**Scale (measured).** 12 of the 14 `src/**` tests importing a `node:` builtin have no
`@vitest-environment` directive and are in this state:

```
src/app/chat/right-rail/preview-act.test.ts
src/app/chat/right-rail/preview-file-save-scope.test.tsx
src/app/right-sidebar/files/ipc.test.ts
src/components/assistant-ui/thread/system-message.test.tsx
src/components/assistant-ui/tool/inline-edit-persistence.test.tsx
src/components/pane-shell/pane-visibility.test.ts
src/components/ui/__tests__/no-native-title.test.ts
src/lib/speech-text.identifier-corpus.test.ts
src/plugins/hermes-bots/cron-prompt.test.ts
src/plugins/hermes-bots/group-chat-view.inline-code.test.tsx
src/plugins/hermes-bots/group-chat-view.wrap.test.tsx
src/plugins/hermes-bots/relay-deliver-budget.test.ts
src/store/nanostores-batch-guard.test.ts
```

Only `src/hover-variant.test.ts` is fine, because it imports **no app code** — a pure node
test that carries `// @vitest-environment node` and so runs correctly.

**What does NOT work (probed, not assumed).** A minimal reproduction (one file using
`node:child_process` + jsdom) was used to test each candidate; all still failed with
`No such built-in module: node:`:

- `test.server.deps.inline: [/node:/]`  ·  `inline: true`
- `ssr.noExternal: true`  ·  `resolve.conditions: ['node']`
- `test.pool: 'forks'`
- `test.server.deps.external: [/node:/]`
- `resolve.alias` mapping each `node:<m>` to `<m>`
- static **and** dynamic `await import('node:child_process')` inside a test body

So it cannot be fixed by a config knob. The two requirements are mutually exclusive in one
file: a `node` environment gives real builtins but no `window`; `jsdom` gives `window` but no
builtins. `// @vitest-environment node` alone swaps one failure for the other
(`ReferenceError: window is not defined` from the app import graph).

**The real fix (not done).** `cron-prompt.test.ts` needs both: it shells out (`spawnSync`) and
imports `./cron`, whose module graph reaches `src/store/onboarding-gate.ts`, which calls
`loadGate()` at import time and dereferences `window.hermesDesktop`. Hand-stubbing `window` in
the test fails twice over — vitest hoists static imports above any statement in the file, and
the graph then wants `window.addEventListener` and more (it pulls `src/sdk/index.ts`). The
prescribed fix is the one the root AGENTS.md gives for logic trapped in a god-file:
**extract the pure functions** (`routinePrompt`, `normalizedProfileName`, `routineInputError`,
`isLegacyDelegatedRoutine`, plus `shellQuote` and the three literals they use) into a light
`cron-prompt.ts` sibling with no React/SDK import, and point the test at that. `botsText()`
(from `./i18n`, which imports `@qubit/plugin-sdk`) is the coupling to break or pass in.

**Interim state.** `cron.tsx`'s delegation wrapper was rebranded to `qubit -p … chat` and the
test's assertion moved with it (commit `3338de83a`) — the pairing is correct, but the test
cannot execute until the above is done, so it currently proves nothing.

