# OFFICE DOC RECONCILIATION — the Office documentation vs the current code tree

**Audit date:** 2026-10-15
**Worktree:** `/home/trick/Projects/Repos/qubit-voicebridge`
**HEAD at audit:** `c3b005f18 checkpoint: working Habblaud port into Qubit Office (live-verified)` — tree **clean** (`git status --short | wc -l` → `0`)
**Method:** every checkable claim in the Office docs was re-run against this worktree. Every number below is a real command output, pasted verbatim. Nothing was softened. Where a claim could not be checked, it is listed in §(c) as unverified, not assumed true.

The docs audited: `docs/OPEN_THREADS.md` (§4.14, §4.15), `docs/game-office/README.md`, `docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md`, `docs/game-office/30-VISUAL-DEFECT-LEDGER.md`, `docs/game-office/HABBLAUD-PROVENANCE.md`.

---

## (a) Stale / false claims

| # | Doc | Quoted claim | Reality (current tree) | Command that proves reality |
|---|-----|--------------|------------------------|-----------------------------|
| A1 | `OPEN_THREADS.md` §4.14h (line 210) | "The vendored-app route is a **PARALLEL track** to the F00–F17 packet plan … The packets are still the plan of record for the Office tab; the vendored route does not advance or replace them. **There is no reconciliation document yet** — that is an open item." | **FALSE.** The vendored app is the **default** renderer of the Office tab; the F00–F17 shell is the opt-in fallback. The vendored app *did* replace the shell as the default (that is what "default renderer" means) — both are still reachable through the one plugin entry. "No reconciliation document yet" is also now false: this file is that document. | `grep -n 'wantShell' frontend/qubit-app/plugins/qubit-office/game/entry.ts` → `81: const wantShell = params.has("shell") && !params.has("app");` and `84: const Office = wantShell ? createOfficeComponent(...) : createOfficeApp(...)` |
| A2 | `OPEN_THREADS.md` §4.14d (line 206) | "**VERIFIED — `?shell=1` opts back into the hand-built shell** … The earlier `?app=1` form is **gone**: with the app now the default, an opt-*in* to the app is meaningless." | **FALSE at HEAD.** `?app=1` is **not gone** — it is retained as an explicit compatibility alias, and it is *load-bearing*: `wantShell` requires `!params.has("app")`, so `?shell=1&app=1` mounts the **app**, not the shell. The switch is `params.has("shell") && !params.has("app")`, not `params.has("shell")`. | `grep -n 'has("app")' frontend/qubit-app/plugins/qubit-office/game/entry.ts` → `81: … && !params.has("app");` (also in the served bundle: `grep -o 'wantShell[^;]*' frontend/qubit-app/plugins/qubit-office/qubit-office.game.js` → `wantShell = params.has("shell") && !params.has("app")`) |
| A3 | `game-office/README.md` line 122 | "**16 modules / 5,288 lines**, `qubit office` with **65 subcommands**, **221 tests passing**" | **FALSE on all three counts.** Real: **28** modules, **11,743** lines, **81** subcommands. | `ls voicebridge/office/*.py \| wc -l` → `28`; `wc -l voicebridge/office/*.py \| tail -1` → `11743 total`; `.venv/bin/python -m voicebridge.cli office --help` → subcommand list contains **81** commands |
| A4 | `OPEN_THREADS.md` §4.14g (line 209) and `60-IMPLEMENTATION-PLAN.md` (lines 217, 230, 269) | "`.venv/bin/python -m pytest tests/test_office_game_*.py -q` → **`211 passed`**, exit 0, **0 skipped**" (and the packet's repeated `204 passed`) | **FALSE.** The suite is **`3 failed, 209 passed`** — and the failures are deterministic (reproduced 3×, each in ~1.5 s when run alone). No skips this run, but the pass count is 209, not 211, and the suite is **not green**. | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` → `3 failed, 209 passed in 114.19s` (second run: `3 failed, 209 passed in 159.49s`) |
| A5 | `OPEN_THREADS.md` §4.14c (line 205) | "**VERIFIED — the VENDORED APP** … `game/entry.ts` mounts the vendored app by default; the hand-built shell is opt-in via `?shell=1`." | The **default-renderer claim itself is TRUE** (the code mounts the app unless `?shell`/`?app`), but it is labelled **VERIFIED** while the repo's own green test that guards it — `test_the_default_renderer_is_the_app_and_the_shell_is_opt_in` — **now FAILS** (A2). A "VERIFIED" row whose falsifier test is red is not verified; at best the default holds and the *switch semantics recorded in the docs* are wrong. | `.venv/bin/python -m pytest 'tests/test_office_game_shell.py::test_the_default_renderer_is_the_app_and_the_shell_is_opt_in' -q` → `1 failed in 0.22s`, `AssertionError: entry.ts still keys off '?app'` |
| A6 | `OPEN_THREADS.md` §4.14a (line 203) | "`.venv/bin/python scripts/vendor_habblaud.py --src /tmp/habblaud-full --check` → `ok: 77 files, **2147125 bytes**, commit 4ccce2537472`" | **Byte total is stale.** Current real output is `2147637 bytes` (512 bytes more). File count and commit are correct. (`/tmp/habblaud-full` no longer exists; `--check` does not need `--src`.) | `.venv/bin/python scripts/vendor_habblaud.py --check` → `ok: 77 files, 2147637 bytes, commit 4ccce2537472` |
| A7 | `HABBLAUD-PROVENANCE.md` §1 (lines 11, 15) | "78 files on disk (77 tracked in `PROVENANCE.json` …)" ; "Total: **2,147,057 bytes** over the 77 recorded files" ; served "by `voicebridge/webui.py::**_serve_qubit_plugin**`" | Two errors: the recorded byte total is **2,147,637** (not 2,147,057 — a 580-byte drift), and the **route is served by `_serve_plugin_app`**, not `_serve_qubit_plugin`. (`_serve_qubit_plugin` is the *asset* route for `/dashboard-plugins/`.) The 78-on-disk / 77-recorded file counts are correct. | `scripts/vendor_habblaud.py --check` → `2147637 bytes`; `grep -n 'def _serve_plugin_app\|def _serve_qubit_plugin' voicebridge/webui.py` → `1857: def _serve_plugin_app` (the plugin-app route), `1975: def _serve_qubit_plugin` (the `/dashboard-plugins/` asset route); `grep -n 'path.startswith("/plugin-apps/")' voicebridge/webui.py` → routes to `_serve_plugin_app` |
| A8 | `OPEN_THREADS.md` §4.14j (line 212) | "**Work is UNCOMMITTED** — `git status --short \| wc -l` → **49** dirty entries. `git log --oneline -3` top = `3d7989e36 …`" | **FALSE.** The tree is **clean (0 entries)** and HEAD is `c3b005f18`. The Office/Habblaud work has since been committed (9 commits touch the plugin dir). | `git status --short \| wc -l` → `0`; `git log --oneline -1` → `c3b005f18 checkpoint: working Habblaud port into Qubit Office (live-verified)` |
| A9 | `OPEN_THREADS.md` §4.14i (line 211) | "**`docs/STATUS.md` still does not exist** — `ls docs/STATUS.md` → `No such file or directory`." | **FALSE.** `docs/STATUS.md` exists (6,172 bytes, mtime 2026-10-15 17:18). | `ls -la docs/STATUS.md` → `-rw-rw-r-- 1 trick trick 6172 Oct 15 17:18 docs/STATUS.md` |
| A10 | `30-VISUAL-DEFECT-LEDGER.md` lines 18–47 | "the live renderer `qubit-office.js` **calls none of them**" ; "The live renderer is `qubit-office.js` (956 lines)" ; "The served bundle is `qubit-office.bundle.js`, rebuilt by `scripts/build_office_bundle.py`." | **FALSE for the served surface.** Neither `qubit-office.js` nor `qubit-office.bundle.js` is the served renderer any more — `manifest.json` `"entry"` is `qubit-office.game.js`, and by default the page mounts the **Habblaud iframe**, not any `qubit-office.js`. `qubit-office.js` is also **1,706 lines**, not 956. (The ledger's 24 per-defect line citations into `qubit-office.js` are therefore anchored to a file that is no longer the live renderer.) | `grep -n 'entry' frontend/qubit-app/plugins/qubit-office/manifest.json` → `12: "entry": "qubit-office.game.js"`; `wc -l frontend/qubit-app/plugins/qubit-office/qubit-office.js` → `1706`; `grep -n 'wantShell' …/game/entry.ts` (app-is-default) |
| A11 | `60-IMPLEMENTATION-PLAN.md` lines 9–24 ("PARALLEL TRACK" block) | "It is the **default** renderer for `/qubit-office`; the hand-built shell is the fallback, opt-in via **`?shell=1`**." then "the vendored route **does not advance or replace these packets**. No document yet reconciles the two tracks — that is an open item (§4.14h)." | **Half true, half stale.** Default/fallback is correct (matches A2's real switch, modulo the `?app` alias). "Does not advance or replace" contradicts itself — a renderer that is the **default** *is* the current plan of record for the tab, and `?shell=1` demotes the packets' renderer to fallback. "No document yet reconciles" is false as of this file. | Same entries as A1/A2; `grep -n 'wantShell' …/game/entry.ts` |
| A12 | `60-IMPLEMENTATION-PLAN.md` F04 block (lines 90–94) vs `OPEN_THREADS.md` §4.6 (line 181) | Packet: "**Phaser is NOT installed and NOT chosen.**" vs Open-threads: "**F04 done — engine CHOSEN: Phaser 4.2.1** … `phaser@4.2.1` pinned exactly." | **The two docs contradict each other, and the code resolves it in favour of §4.6: Phaser IS installed and pinned.** `frontend/qubit-web/package.json` declares `"phaser": "4.2.1"`. The packet's "NOT installed" line is stale. | `grep -n 'phaser' frontend/qubit-web/package.json` → `23: "phaser": "4.2.1",` |

**Claims checked and found TRUE (keep them):**

| Doc | Claim | Verified by |
|---|---|---|
| `OPEN_THREADS.md` §4.14a | vendored commit `4ccce2537472`, MIT, 77 files | `scripts/vendor_habblaud.py --check` → `ok: 77 files, … commit 4ccce2537472` |
| `OPEN_THREADS.md` §4.14b / `60-IMPLEMENTATION-PLAN.md` | the app is served at `/plugin-apps/qubit-office/habblaud/` and returns 200 | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/` → `200` (service active) |
| `OPEN_THREADS.md` §4.14c | the vendored app is the default on `/qubit-office` | `entry.ts:81,84` (the app branch is the `: createOfficeApp(...)` default) |
| `OPEN_THREADS.md` §4.14f / F09,F12 STATUS | `tests/test_office_game_character.py` and `tests/test_office_game_operational.py` **exist** | `ls tests/test_office_game_*.py` lists both |
| `60-IMPLEMENTATION-PLAN.md` F00–F12 | packet modules exist (`game/types.ts`, `adapter.ts`, `navigation.ts`, `input.ts`, `art/*`, `ui/*`, `systems/*`) | `find frontend/qubit-app/plugins/qubit-office/game` shows them |

---

## (b) Corrected reconciliation

### What the default renderer is

As of HEAD `c3b005f18`, `/qubit-office` with **no query parameters mounts the vendored third-party app** — the Habblaud client (`github.com/marmottajr/habblaud`, MIT, commit `4ccce2537472`) served from `/plugin-apps/qubit-office/habblaud/`, embedded as an iframe fed by the live `/api/stream` SSE route. This is decided inside the plugin entry, `frontend/qubit-app/plugins/qubit-office/game/entry.ts`:

```ts
const params = new URLSearchParams(window.location.search);
const wantShell = params.has("shell") && !params.has("app");
const Office = wantShell ? createOfficeComponent(R, H, liveData) : createOfficeApp(R, H);
```

`manifest.json` declares `"entry": "qubit-office.game.js"` — that is the *plugin* entry (the built wrapper), and it is the entry that **chooses** the renderer. The manifest does not name the renderer; do not read it as "which you get".

### What the fallback is

The hand-built F00–F17 shell is the **opt-in fallback**, reached by `?shell=1`. It is not dead code: it is the rollback for the promotion, kept one query parameter away so a regression in the vendored app (whose write half depends on bindings Qubit has not finished — see `app/office-api.ts`) can be escaped with one reload.

The real switch is **`?shell=1` AND NOT `?app`**. `?app=1` is retained as a compatibility alias for bookmarks/deployments from the first Habblaud preview, and it **overrides** `shell`: `?shell=1&app=1` mounts the **app**. So the accurate statement is: *`?shell=1` opts into the shell; `?app=1` forces the app and wins any conflict with `shell`.* The code comment in `entry.ts` (lines 60–62) documents this; the OPEN_THREADS §4.14d row that says the `?app=1` form "is gone" does **not**.

### Relationship between the vendored Habblaud app and the F00–F17 packet plan

They are **two implementations of the same goal** ("an office you watch agents work in"), and the vendored app is **not** a parallel, disconnected track — it is the **default renderer of the same tab the packets build for**. That means:

1. The packets are **no longer unconditionally the plan of record for the Office tab's renderer.** The packets still describe the hand-built renderer (F00–F17, `game/` TypeScript → `qubit-office.game.js`), and their design work (navigation reducer, input-context chain, adapter/revision fence, panels, settings tokens) is **real and shipped inside that renderer** — but that renderer is now the fallback, not the default.
2. The vendored app **did replace the shell as the default**, which is a material change to the tab the packets target; existing packet proofs that asserted "the shell IS the served entry's output" now describe the `?shell=1` surface only.
3. **This document is the reconciliation** that §4.14h and the packet's PARALLEL-TRACK note said was missing. The honest framing is now: *the packets remain the plan for the fallback renderer; the vendored app is the default renderer; both are reached through one plugin entry; the switch is `?shell` with `?app` as a compatibility alias.*

Two facts the docs must stop hiding:

- The suite that guards this arrangement is **red**: `.venv/bin/python -m pytest tests/test_office_game_*.py -q` → **`3 failed, 209 passed`**, reproducibly. The failing tests are `test_the_default_renderer_is_the_app_and_the_shell_is_opt_in` (asserts `?app` is gone — the alias exists), `test_the_stream_is_bound_and_the_writes_are_gated_not_missing`, and `test_the_binding_summary_counts_the_new_truth` (the app reports `8 bound · 5 unavailable · 2 confirmation-gated`, while the test expects `3 of 5 routes bound … 0 missing … 2 gated`). The code changed (commit `c3b005f18` re-added the `?app` alias and widened the binding table); the tests and the docs did not. This is a real drift, not flakiness — each test fails alone in ~0.2–1.5 s.
- Any §4.14 row labelled **VERIFIED** whose own falsifier test is now failing must be downgraded. §4.14c and §4.14d both qualify.

---

## (c) Claims I could NOT verify, and why

1. **"Pixel-verified: `Habblaud Connected 4 rooms 10 agents`, 22,776 non-background pixels / 162 tones"** (§4.14b). I did not run a headless-Chrome pixel probe in this audit; I only confirmed the route returns `200`. The pixel claims are plausible but **not re-verified here** — I am not restating them as true.
2. **Live probe: plain `/qubit-office` → 1 iframe; `?shell=1` → 0 iframes, 2 canvases** (§4.14c). Same reason — no browser probe was run. The *code* evidence (`entry.ts`) supports the default/fallback split; the iframe/canvas counts are carried over unverified.
3. **"exit 0, 0 skipped"** for the game suite (§4.14g). The run I did shows `3 failed, 209 passed` with no `skipped` token, but `pytest` exited via the pipeline (`\| tail`) — I read the summary line, not a raw exit code, and the run had **3 failures**, so the "exit 0 / all green" framing is unsupported regardless.
4. **`app/api-bridge.ts` binding table internals** (why it now reports `8 bound · 5 unavailable · 2 confirmation-gated`). I confirmed the *observable* mismatch (test vs runtime string) but did not trace whether the code is the corrected truth or the test is. Both are possible; the docs assert the test's version, the code emits a different one, and that contradiction is unresolved by this audit.
5. **The F08 "world grows to fill the shell" live measurement** (500→879 desktop etc.). No browser probe run; carried over unverified.
6. **Flakiness note** (the `5 failed, 81 passed` vendored-app run in §4.15). I did not reproduce or refute it; it is recorded as an open item in the docs and remains one.

---

### Reproduce this audit

```sh
cd /home/trick/Projects/Repos/qubit-voicebridge
git log --oneline -1                       # c3b005f18
git status --short | wc -l                 # 0
ls voicebridge/office/*.py | wc -l         # 28
wc -l voicebridge/office/*.py | tail -1    # 11743 total
.venv/bin/python -m voicebridge.cli office --help   # 81 subcommands
grep -n 'wantShell' frontend/qubit-app/plugins/qubit-office/game/entry.ts
.venv/bin/python scripts/vendor_habblaud.py --check # ok: 77 files, 2147637 bytes, commit 4ccce2537472
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/
.venv/bin/python -m pytest tests/test_office_game_*.py -q   # 3 failed, 209 passed
```
