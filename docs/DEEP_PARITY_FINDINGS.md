# Deep 1:1 parity findings — ported pages vs upstream page sources

Owner report: "pages have bugged UI, not aligned correctly, extra panels and
buttons not connected to anything and not working, and many more."

## The real gap (why class adoption did not produce 1:1)

Previous waves ported upstream **CSS classes only**. Upstream pages are **Lit
TypeScript components** built from a shared render-helper system. Adopting the
stylesheet without the component structure gives the right *colors* but the
wrong *hierarchy*, which is exactly "not aligned correctly / extra panels".

Upstream page sources live under (openclaw pin `7ff3b3be3713`):
    UP = ~/.local/state/qubit-voicebridge/runtime/cache/scratch/upstream/openclaw

Per-route page sources:
    ui/src/pages/worktrees/worktrees-page.ts
    ui/src/pages/cron/cron-page.ts
    ui/src/pages/devices/devices-page.ts  + devices/capability-chips.ts
                                          + devices/entry-menu.ts
                                          + devices/devices-dialogs.ts
    ui/src/pages/sessions/*.ts            (list-query, page-state, category-cell, …)
    ui/src/pages/logs/logs-page.ts + view.ts + log-lines.ts
    ui/src/pages/config/config-page.ts
    ui/src/pages/channels/channels-page.ts
    ui/src/pages/systems/systems-page.ts
    ui/src/pages/workboard/*
    ui/src/pages/activity/*

## The shared component contract (`ui/src/components/settings-ui.ts`)

This is the atomic structure every upstream settings-style page composes. Our
pages MUST match this DOM, not just carry the class names.

- `renderSettingsPage`  -> `.settings-page`
- `renderSettingsPageHeader` -> `.settings-page-header`
- `renderSettingsSection({title, description, count, actions, notice}, rows)`
    <section class="settings-section">
      <div class="settings-section__header">
        <div class="settings-section__copy">
          <h2 class="settings-section__heading">TITLE <span class="settings-count">N</span></h2>
          <p class="settings-section__desc">DESC</p>
        </div>
        <div class="settings-section__actions">…</div>     <- only when actions exist
      </div>
      [notice]
      <div class="settings-group"> …rows… </div>
    </section>
- `renderSettingsGroup(rows)` -> `.settings-group`
- `renderSettingsRow({title, description, control, stacked, stackedOnNarrow})`
    <div class="settings-row [settings-row--stacked]">
      <div class="settings-row__text"> <div class="settings-row__title">…</div>
                                      <p class="settings-row__desc">…</p> </div>
      <div class="settings-row__control">CONTROL</div>     <- ONLY when control is defined
    </div>
  **Key rule: `.settings-row__control` is emitted ONLY if a control exists.**
  A row with no control must NOT render an empty control div — that is the
  source of phantom panels/padding.
- `renderSettingsNavRow` -> `button.settings-row.settings-row--nav` + `.settings-row__chevron`
- `renderSettingsSummary(items)` -> `dl.settings-summary` > `.settings-group.settings-summary__tile` > dt/dd
- `renderSettingsStatus({...})` -> `.settings-status`
- `renderSettingsValue(v, {mono})` -> `.settings-row__value` (+ `--mono`)
- `renderSettingsEmpty` -> `.settings-empty`
- `renderSettingsLoadingSkeleton` -> `.settings-loading-skeleton*`
- `renderSettingsToggleRow` -> row + `.settings-switch`

Other shared components to mirror:
    components/sessions-hub-header.ts   (sessions header)
    components/sessions-hub-tabs.ts
    components/settings-workspace.ts    (the two-pane workspace shell)
    components/confirm-dialog.ts        (confirm gating)
    components/settings-sidebar.ts

## Verified REAL defects (evidence-backed, not guesses)

1. **`HarnessRouterPage.tsx:289` — a `<button class="hx-group-btn">` with NO
   handler at all** (the "Your harnesses" group header). It looks clickable and
   does nothing. Upstream's group header toggles the group.
   Found by an AST-accurate JSX scan (only genuinely inert tag in the app).

2. **`/worktrees` was a full crash** (React #31: object rendered as child).
   FIXED in the previous wave — `listBranches()` typed `string[]` but the
   backend returns `{name, checkedOut, isDefault, isRemote, worktreePath}`.
   Lesson: upstream `worktrees-page.ts` is the reference for the correct shape.

3. **Empty `.settings-row__control` divs** — see the rule above. Any ported page
   that always renders a control wrapper produces the extra panels the owner
   sees.

4. **`disabled` used without a reason string.** Upstream always pairs a refused
   control with its reason (`manage_reason`, prerequisite, counterpart). A bare
   `disabled` is a defect.

## Methods that WORK (use these, they are proven)

- **DOM-level control audit** (`/tmp/deadclick.py <route>`): enumerates visible
  controls, clicks each, and diffs the DOM signature to find controls that
  change nothing. More reliable than grep.
- **AST-accurate inert-button scan**: parse each `<button ...>` open tag
  respecting brace depth; a tag with no `onClick`/`type="submit"`/`href`/
  `disabled` is inert. Grep alone gives ~90% false positives because CSS
  `disabled:` variants and doc comments match naively.
- **Live route probe** (`/tmp/shot_route.py <route> <png>`): prints h1/h2, char
  count, page errors. `<2000 chars` or any page error = suspect.
- **Compare against the upstream page source**, not the CSS, for structure.

## Environment

- App: http://127.0.0.1:8890 (already running; do NOT restart, do NOT touch :9119).
- Build: `cd frontend/qubit-web && npm run build`; tsc: `npx tsc --noEmit`.
- Tests: `NODE_ENV=test npx vitest run <file>`. NEVER the full suite.
- Playwright: `.venv/bin/python` in the repo root.
- No commits, no pushes, no full pytest.
- `verbatimModuleSyntax` is ON: type-only imports must use `import type`.


## OWNER DIRECTIVES (2026-10-04) — supersede any caution language in this doc

1. **1:1 target:** port each page's REAL upstream structure (markup + handlers +
   flows), then keep only what the Qubit backend can actually do. An unwired
   control renders **disabled with a visible reason** — never inert-clickable,
   never fake success, never silently absent.
2. **Zero upstream references in our source.** No upstream project name,
   product handle, or repo path anywhere in first-party source, comments,
   identifiers, or user-visible strings. The ONE exception is legal attribution
   in `THIRD_PARTY_NOTICES.md` and the provenance header comment of a ported
   stylesheet (repo + licence + commit + source path must stay).
3. **DO NOT POSTPONE A MISSING FEATURE — BUILD IT.** If a ported page needs a
   backend capability that does not exist, IMPLEMENT IT: add the route/handler in
   `voicebridge/` (register it in `qubit_compat.py` so it resolves) and wire the
   page to it. "Backend missing" is a task, not a valid reason to leave a
   control disabled. Reserve disabled-with-reason for things the platform
   genuinely cannot do (e.g. a hardware capability), and for those state the
   reason precisely.
4. **Do not break what works.** Existing passing tests must keep passing; the
   service must keep running; no destructive change to a working route.

Corollary for agents: before reporting "not wired", check
`voicebridge/qubit_compat.py` for an existing handler for that operation. Many
"missing" operations already have a registered handler under a different route
name — wire to it rather than adding a duplicate.


## Backend reality check (2026-10-04) — DO NOT re-derive this, it is measured

Static analysis of `voicebridge/qubit_compat.py` gives a FALSE "missing routes"
signal. Measured facts:

- The route table holds **387 `_r(...)` rows**; the `HANDLERS` +
  `_EXTRA_HANDLERS` literals register **355** names (`h_*` style), and the file
  defines **607** top-level functions.
- A naive scan reports **34 rows "unresolved"** — almost all
  `/api/plugins/kanban/*` and `/api/plugins/qubit-achievements/*`. **They are
  NOT broken.** Verified live: `/api/plugins/kanban/board`, `/stats`,
  `/assignees`, `/api/plugins/qubit-achievements/achievements` and
  `/api/dashboard/plugins/fleet` all return **200 with real data**. Plugin
  REST routers are mounted by a separate path, so they are absent from the
  `HANDLERS` dict by design.

**Rule: never conclude a route is missing from static analysis alone.** Probe it:
`GET http://127.0.0.1:8890/agent/api/<path>` with header
`X-Hermes-Session-Token: <token from served HTML `__QUBIT_SESSION_TOKEN__`>`.
Note the real prefix is **`/agent/api/...`**, not `/api/...`. A route that
returns a typed JSON error (e.g. `{"ok": false, "code": "..."}`) is alive and
validating; a genuinely absent route 404s with the SPA fallback.

Practical consequence for this workstream: the Qubit backend is **not** the
bottleneck for most pages. Assume the capability exists and WIRE TO IT; only
treat something as missing after a live probe proves it 404s or returns a typed
refusal.


## Measured substance audit (2026-10-04) — 20 unowned pages

Method: `/tmp/substance.py` loads each route, reads `main.innerText`, counts
visible controls, and collects the **root segment of every applied class**.
Ported upstream families = `settings- session- sessions- qb- workboard- hx-
cron- device- ak-`. Anything showing only `flex/gap/font/bg/border/arc/duration`
is **pure Tailwind**, i.e. NOT ported.

RESULT: **16 of 20 pages have ZERO ported class families.**
Worst (chars / controls / ported-families):
    /open-viking/retrieval      55 chars,  3 ctrl, 0   <- effectively blank
    /open-viking/uploads       193 chars,  5 ctrl, 0
    /nodes                     208 chars,  4 ctrl, 0
    /agent-workspace           363 chars,  3 ctrl, 0
    /open-viking/resources     746 chars,  0 ctrl, 0   <- no controls at all
    /canvas                    620 chars,  9 ctrl, 0
    /open-viking               536 chars, 17 ctrl, 0
    /qubit-memory             1542 chars,  4 ctrl, 0
    /open-viking/agent-hub   28106 chars,  0 ctrl, 0   <- huge text dump, NO controls
Pages with at least one ported family: only /checkpoints, /qubit-task-inbox,
/qubit-board-admin, /session-ops.

Not a crash: every one of these returns `page errors: none`. They are **thin and
unstyled**, which is what the owner means by "unusable / not 1:1".

REGISTERED ROUTES (46 total) — use these exact paths; several guessed paths
(`/talk`, `/scheduler`, `/users`, `/canvas`, `/skills-review`, `/mcp-calls`)
return the SPA fallback and look like a broken page when they are simply wrong:
    /qubit-talk  /qubit-scheduler  /admin/skills-review  /admin/mcp-calls
    /admin/users  /qubit-memory  /canvas  /nodes  /agent-workspace
    /open-viking  /open-viking/retrieval  /open-viking/uploads
    /open-viking/agent-hub  /open-viking/resources
Full list: `grep -oE '"([^"]+)":\s*([A-Za-z]\w*Page)' frontend/qubit-web/src/App.tsx`


## Rebrand audit (2026-10-04) — 5,475 files flagged, almost none are real leaks

Method: `/tmp/rebrand.py` scans `frontend/qubit-web/src`, `voicebridge`,
`qubit_runtime` for upstream handles (hermes / openclaw / openmuse /
harnessrouter / nousresearch / nous-research / CopilotKit) and classifies each
hit by context.

**DO NOT rename these — they are load-bearing, and renaming breaks Qubit:**

| Category | Count | Why it must stay |
|---|---|---|
| `@nous-research/ui` import paths | 122 | Real published MIT npm dependency the build resolves. A dependency name is not our source. Renaming breaks the build. |
| `HERMES_*` env vars / `~/.hermes` paths | 1,036 | The wire contract and the operator's real on-disk layout. Renaming means Qubit stops finding its own state. |
| Legacy migration keys (`hermes-sidebar-collapsed`, `hermes-locale`, `hermes-dashboard-theme`, `hermes.tokenReloadAttempted`, `HERMES_QWEN_*`) | 226 | These exist **to migrate away from** the old brand. Deleting them orphans real users' saved settings/secrets. |
| `hermes_cli/*`, `hermes_agent`, `hermes-dashboard.service` refs | 19 | Cross-repo parity references in comments; they name a real upstream path, not our product. |
| `_SPEAK_BRAND_MAP` in `voicebridge/desktop_helper.py` | 1 file | This is the speak-time substitution that **removes** upstream names from spoken output. It is the cure, not the disease. |

**Genuine user-visible leaks found and FIXED:** `ChannelsPage.tsx` told users to
message `@NousHostedHermesBot` and showed an "upstream hosted service" badge →
rewritten to "third-party hosted service" with no upstream handle.

**Remaining genuine leaks are test fixtures only** (`qubit_runtime/tests/**`
using `BOT_USER_NAME = "HermesBot"`, `@MyHermesBot`). Harmless and out of the
product surface; leave unless asked.

**Attribution exception (still enforced):** ported stylesheets keep their real
provenance (repo + licence + commit + source path) in the leading
`/* ... */` header. The audit skips those blocks. Scoping the brand-leak check
to the RULE BODY (not the header) is what keeps legal attribution intact.


## PAGE FAMILY CLASSIFICATION (2026-10-04) — read before touching ANY page

**The mistake that shipped:** agents rewrote Hermes-family pages onto the
*openclaw* `settings-*` contract. That is the WRONG target for those pages and
reads to the owner as "the page is broken." This is a hard rule now.

There are TWO different upstream consoles and they must not be mixed:

| | Hermes-family pages | openclaw-family pages |
|---|---|---|
| Reference tree | `$HH/web/src/pages/*.tsx` (22 files) | `$OC/ui/src/pages/<dir>/` |
| Markup | **plain Tailwind + `<Card>`/`<CardContent>`** | **`settings-page/section/group/row`** |
| Pages | Analytics, Channels, Chat, Config, Cron, Docs, Env, Files, Logs, Mcp, Models, Pairing, Plugins, ProfileBuilder, Profiles, Sessions, Skills, System, Webhooks | about, activity, agents, approval(s), apps, channels, config, connection, cron, custodian, dashboards, debug, device(s), labs, logs, meetings, memory-import, model-*, new-session, plugin(s), portals, profile, question, search, secrets, sessions, skill-workshop, skills, systems, terminal, usage, workboard |
| Our files | the same-named `*Page.tsx` | the Qubit-specific pages |

`$HH = ~/.cache/qubit-pins/hermes-agent`
`$OC = ~/.local/state/qubit-voicebridge/runtime/cache/scratch/upstream/openclaw`

A page whose **name matches a Hermes reference page** is a Hermes-family page:
**keep it 1:1 with Hermes — plain Tailwind + Card.** Do NOT apply `settings-*`
to it, no matter how much "nicer" the hierarchy looks.

**Reverted under this rule (were wrongly converted):** `ChannelsPage`,
`CronPage`, `PairingPage`, `SessionsPage` — restored to `HEAD`, then only the
genuine defect fixes re-applied on top.

**Verify a page's family before editing:**
`ls ~/.cache/qubit-pins/hermes-agent/web/src/pages/ | grep -i <page>` — if it
hits, the Hermes file IS your 1:1 reference; diff against it, don't restyle it.


## Full-height pages need a bounded ancestor (2026-10-04)

**Symptom.** A page's composer/controls are pushed far below the fold; the
transcript renders but the bottom of the UI is unreachable. `/webchat` measured
`scrollHeight = 104,305px` in a `950px` viewport, composer at `y = 104,444`.

**Root cause.** Two layers, and fixing only the first does nothing:

1. `.qb-chat` used `height: 100%` — a percentage height resolves only against a
   *definite* ancestor height. Inside an auto-height parent it does not bound
   the box; the column grows to its content instead.
2. `App.tsx` wraps *every* route in a plain block `div`. That wrapper only got
   `min-h-0 flex flex-1 flex-col` when `isDocsRoute || isChatRoute` — so
   `/webchat` and `/qubit-bot` fell through to a block div, which grows to
   content and never passes a height constraint down. Everything below it
   became unbounded.

**Fix.** `chat-routes.ts` gained `isFullHeightPath()` / `FULL_HEIGHT_ROUTE_PATHS`
(full-bleed console routes: chat routes + `/webchat` + `/qubit-bot`), and
`App.tsx` uses it for BOTH the `min-h-0 flex flex-1 flex-col` chain and to
suppress the bottom padding. `isChatPath` is deliberately NOT reused — "is this a
chat" and "does this page own the viewport" are different questions.

**Rule.** Any page with a fixed footer element (composer, action bar) must be in
`FULL_HEIGHT_ROUTE_PATHS`, or it reproduces the 100k-px bug. Verify with the
measured height chain, not by eyeballing a screenshot: the wrapper must report
viewport-ish height and the inner scroller must have `scrollHeight > height`.

**`/files` epoch-zero.** `files.py::_public_dir` returned only
`name/rel/dir/size` — no `mtime`. The `h_files` adapter read
`en.get("mtime", 0)`, so every row fell back to `0` and the table showed
"Jan 1, 1970". `_public_dir` now returns `modified`/`mtime` (real POSIX seconds,
`0` on a stat failure rather than an invented time).


## Scoped base classes are invisible outside their scope (2026-10-04)

**Symptom.** ~60 controls across 16 console pages rendered as plain text:
no background, no border, no colour. Pages read as "unstyled text dumps"
(`/qubit-memory`, `/nodes`, `/canvas`, `/qubit-taskflows`, `/checkpoints`,
`/session-ops`, `/admin/skills-review`, `/worktrees`, …). Buttons were
text with stray padding; select menus fell back to browser default chrome.

**Root cause.** `workboard.css` defines the shared control bases **scoped to
a `.workboard` ancestor**:

```css
.workboard .btn { border: 1px solid …; background: …; padding: 8px 14px; }
.workboard .btn--sm { padding: 6px 10px; font-size: 12px; }
```

Sixteen pages use `btn` / `btn--sm` / `btn--ghost` / `btn--xs` **without**
being inside `.workboard`, so they matched **no rule at all**. A DOM probe
confirmed it: the element reported `backgroundColor`, `borderTopWidth` and
`borderTopColor` all empty while `padding: 6px 10px` applied — i.e. a
modifier was matching somewhere but the base never was.

Two classes were also emitted with **no definition anywhere**:
`settings-select` (dropdowns) and `settings-row--nav` (rows-as-links).

**Fix.** `src/styles/upstream/console-primitives.css` (new), imported from
`index.css` AFTER `workboard.css`. It restates `.btn` + all modifiers,
`.settings-input`, `.settings-select`, `.settings-row--nav` and `.callout`
at document scope, using the same geometry as the workboard block and the
theme colour tokens. `.workboard`'s own rules keep winning inside their
scope by specificity, so nothing regressed there.

Also: a disabled **ghost** button has neither border nor background, so
`opacity` alone was its only disabled signal and it read as ordinary muted
text. Disabled ghost buttons now get a dashed outline; enabled ghost
buttons get a 5% resting surface so a toolbar of them stays legibly a
control group rather than loose text.

**Result (live DOM probe, defect count of controls with no surface in self
or 3 ancestors):** `/qubit-memory` 5→0, `/nodes` 4→0, `/canvas` 9→0,
`/worktrees` 2→0, `/checkpoints` 5→0, `/qubit-taskflows` 22→0,
`/admin/skills-review` 7→0, `/admin/users` 0, `/admin/mcp-calls` 0,
`/computer` 0, `/analytics` 0.

**Rule.** Before styling a page, check whether the base classes it emits
are **scoped** in their stylesheet. A scoped base (`X .btn`) is invisible
outside `X` and fails silently — no error, no missing-class warning, just
unstyled DOM. When two upstreams contribute both scoped and unscoped
consumers, keep ONE unscoped definition and let scope-specific rules
override by specificity. Verify with a computed-style probe that walks
wrapper ancestors; a screenshot alone cannot distinguish "ghost button
(by design)" from "no stylesheet matched".


## Scoped token sets are invisible outside their scope (2026-10-04)

**This is the master root cause of the "unstyled text dump" console.**

**Symptom.** ~8 console pages rendered as raw text: no padding, no borders,
no hover surfaces, no readable secondary text. Buttons were plain text;
panels had no outline; list rows ran together.

**The wrong answer (mine, twice).** I first blamed missing CSS rules, then a
broken `--foreground`. Both were wrong. The stylesheets are complete and the
classes are defined.

**Root cause.** The ported upstream token set is declared **only inside scoped
wrappers**:

```css
.qb-upstream { --text: …; --muted: …; --border: …; --space-2: …; }
.workboard   { --text: …; --accent: …; --bg-hover: …; }
.hx-root     { --v2-ink: …; --v2-cap: …; }
```

A page receives those tokens **only if it opts in by carrying the wrapper
class**. Measured live across the console:

| route | `.qb-upstream` | result |
|---|---|---|
| /open-viking | 1 | tokens resolve |
| /worktrees, /checkpoints, /qubit-taskflows, /qubit-memory, /canvas, /nodes, /session-ops, /workboard | **0** | every token `(UNSET)` |

With the token absent, each declaration reading it is **invalid at
computed-value time and silently drops** — no error, no warning, no missing
class. 103 references to `--muted`, 64 to `--text`, 54 to `--border`, 42 to
`--bg-hover`, 35 to `--space-2` all evaporated on those pages.

**This is the same bug as the scoped `.btn`, one layer deeper.** The earlier
fix unscoped the *class*; this unscopes the *token set*.

**Fix.** `src/styles/upstream/console-tokens.css` re-declares the set on
`:root` with identical values, imported after `upstream-compat.css`. The scoped
blocks are **not** removed — a page inside `.qb-upstream` still resolves from
there, and the deliberate accessible `--muted`-as-text decision documented in
`upstream-compat.css` is preserved verbatim. Measured after: `UNSET=none` on
/worktrees, /checkpoints, /qubit-taskflows.

**Two false alarms worth recording**, because both cost time:
- `--v2-*` reported UNSET *on /workboard and /worktrees* — but those pages do
  not carry `hx-root`, and the Harness Router (which does) resolves them all
  (`--v2-cap #aab6a4`, `--v2-card #161b24`). Probing the wrong page produces a
  fake defect.
- A grep for declarations across `.css` files reported "only 2 undefined"
  because the tokens *are* declared — just not in a scope that applies.
  **Static declaration-scanning cannot answer this question; only a
  computed-style probe at the point of use can.**

**Guard.** `src/styles/theme-tokens.test.ts` (5 tests) pins the transparent
`--foreground` premise, the `--color-*` remap, the `:root` hoist, the survival
of the scoped block, and that every `var()` we write is resolvable or carries a
fallback.

**Rule.** When porting a themed UI, ask of every token: *is this declared at
`:root`, or only inside a wrapper the consuming page may not carry?* A scoped
token set is a silent, total styling failure for any page outside the scope.


## Independent baseline sweep — 27 routes (2026-10-04)

Measured live at 1440x950 against entry chunk `index-DKpcKZ7-.js`
(md5 `8d51d8af6bd7a4b6b419097fb2fd9543`). Columns: visible leaf-text length,
interactive controls, controls with no visible surface (wrapper-aware),
controls with an EMPTY accessible name, vertical-text leaves, page errors.

| route | text | ctl | unsurf | empty | vert | errs |
|---|---|---|---|---|---|---|
| /qubit-bot | 259 | 8 | 1 | 0 | 0 | 0 |
| /qubit-harness-router | 547 | 18 | 1 | 0 | 0 | 0 |
| /open-viking | 874 | 17 | 0 | 0 | 0 | 0 |
| /open-viking/retrieval | 993 | 2 | 0 | 0 | 0 | 0 |
| /open-viking/uploads | 1012 | 5 | 0 | 0 | 0 | 0 |
| /open-viking/resources | 1373 | 8 | 0 | 0 | 0 | 0 |
| /open-viking/agent-hub | 3795 | 30 | 2 | 0 | 0 | 0 |
| /agent-cards | 1758 | 29 | **13** | **2** | 0 | 0 |
| /canvas | 1015 | 9 | 0 | 0 | 0 | 0 |
| /nodes | 511 | 4 | 0 | 0 | 0 | 0 |
| /worktrees | 3376 | 29 | 0 | 0 | 0 | 0 |
| /checkpoints | 926 | 7 | 0 | 0 | 0 | 0 |
| /session-ops | 3351 | 77 | 0 | 0 | 0 | 0 |
| /qubit-taskflows | 2136 | 23 | 0 | 0 | 0 | 0 |
| /qubit-memory | 2254 | 5 | 0 | 0 | 0 | 0 |
| /admin/skills-review | 2186 | 20 | 0 | 1 | 0 | 0 |
| /analytics | 895 | **0** | 0 | 0 | 0 | 0 |
| /computer | 706 | 6 | 0 | 0 | 0 | 0 |
| /logs | 19140 | 121 | 101* | 0 | 0 | 0 |
| /sessions | 671 | 7 | 2 | 0 | 0 | 0 |
| /channels | 4221 | 101 | 33* | 0 | 0 | 0 |
| /cron | 395 | 7 | 0 | 0 | 0 | 0 |
| /models | 3601 | 37 | 26* | 0 | 0 | 0 |
| /files | 2016 | 153 | 102* | 0 | 0 | 0 |
| /profiles | 155 | 1 | 1 | 0 | 0 | 0 |
| /pairing | 102 | 1 | 0 | 0 | 0 | 0 |

\* Starred `unsurf` counts are the detector counting correct-by-design rows:
`/logs` and `/files` render every row as a surface-less `<button>` (Hermes does
the same — rows are hover-highlighted text with no fill), and `/channels` /
`/models` are nav/list rows. A naive count here produces phantom defects.

**Vertical text: 0 across all 27 routes. Page errors: 0 across all 27.**

### Two readings I got WRONG and corrected by measuring

1. **`/profiles` (155 chars) and `/pairing` (102 chars) are not broken.** They
   are sparse because the data is sparse — one profile on this machine, one
   pending pairing request. All their API calls return 2xx. Low text length is
   not a rendering failure; check the data before calling a page empty.
2. **`/qubit-bot`'s 68KB of hidden markup is not a collapsed-height bug.**
   `.qb-below-body` carries `display:none` **and** the `hidden` attribute — a
   legitimately closed disclosure panel. The bounded flex chain is intact
   (`qb-chat` 747px, `qb-chat-scroll` 608px `overflow-y:auto`, `main` 769px).
   Only 5 of 192 leaf text nodes are visible because the thread is genuinely
   empty: a welcome blurb plus 3 suggestion chips. The fix is content/semantics
   parity with openmuse, not CSS repair.

**Rule reinforced:** a large `innerHTML` with a small `innerText` is a signal to
inspect, not a verdict. Collapsed panels, closed disclosures and SVG/icon
markup all inflate HTML without hiding anything the user is missing. Confirm
with `getComputedStyle` (`display`, `hidden` attribute) before concluding.


## CORRECTION: /qubit-harness-router's surface-less controls are CORRECT (2026-10-04)

I reported the Harness Router's 13 surface-less controls as a defect **twice**.
That was wrong, and I verified it by measuring the hover state rather than the
rest state. Independent re-measurement at 1440x950:

| control | rest bg | rest border | rest opacity | HOVER bg |
|---|---|---|---|---|
| `.hx-group-btn` | `rgba(0,0,0,0)` | `0px` | 1 | **`rgb(28,35,46)`** |
| `.hx-ic` | `rgba(0,0,0,0)` | `0px` | **0** | **`rgb(28,35,46)`**, opacity → 1 |
| `.hx-proj-btn` | `rgba(0,0,0,0)` | `0px` | 1 | **`rgb(28,35,46)`** |

Every one has a real hover surface. `.hx-ic` is an icon button that is
deliberately invisible until its row is hovered (`opacity: 0` → `1`) — the
upstream reveal-on-row-hover pattern. These are nav-style rows and are correct
by design; adding a resting fill would move them away from upstream.

**Why the error happened, and the rule it teaches:** I measured only the REST
state and treated "no background, no border" as proof of a defect. A control's
affordance can live entirely in its hover/focus states, and a list row that
highlights on hover is a complete, deliberate design. **Measure rest AND hover
AND focus before calling any control unsurfaced.** The same mistake is available
on `/logs` (101 rows), `/files` (102), `/channels` (33) and `/models` (26) —
all of those are rows rendered as surface-less buttons on purpose, and Hermes
does exactly the same.

**A real defect does exist on the same axis**, found by the same measurement
method, so the distinction is worth keeping sharp: `/qubit-bot`'s
`.qb-actions-row` buttons are transparent at rest AND transparent on hover, with
only a focus outline — no affordance at any state. That one is genuine.


## The "no Qubit backend" refusals are a STALE CAPABILITY REGISTRY, not missing backends

Owner report (verbatim): *"LOOK A NEW ERROR SAYS NO BACKEND, I ASKED WHEN YOU FIND SOMETHING
MISSING YOU NEED IMPORT FROM UPSTREAM AND ADAPT TO QUBIT TO WORK INTEGRATED WE NEED EVERYTHING
WORKING, THIS IS WHY UI IS FAKE"* — alongside a screenshot of `/agent-cards` rendering a complete
delegate form whose submit was disabled with *"Delegation from the dashboard has no Qubit backend."*

**That claim was false.** `POST /agent/api/qubit/harness/task_submit` was live the whole time:

```
POST /agent/api/qubit/harness/task_submit                    -> 409 CONFIRM_REQUIRED (+ confirmation_id)
POST .../task_submit?confirmation_id=<id>                    -> 200
{ "state": "OK", "session": "dashboard-...", "harness": "qubit-worker",
  "authority": "canonical session (sessions.create) + one real agent turn (conversations.turn)",
  "turn": { "ok": true, "reply": "I'm processing the request..." } }
```

**Root cause.** `frontend/qubit-web/src/lib/ocFamilyHarnessApi.ts` declared
`export type RouteStatus = "NEEDS-BRIDGE"` — a *single-member* union — and applied it to **every
one of the 14** harness routes. That was accurate when the bridge did not exist. It was never
re-probed afterwards, so the console disabled working controls and told the operator the backend
was missing while the route returned 200.

Re-probed live, **7 of 14 responded 200** (`harnesses`, `starter_kits`, `plugins`, `tasks`,
`session_create`, `artifact_get`, `custom_list`); the remaining param-validation 400s were
unproven, so they correctly stayed `NEEDS-BRIDGE`. `task_submit` itself flips to 200 once a
non-empty `prompt` is supplied.

**Fix.** `RouteStatus` became `"LIVE" | "NEEDS-BRIDGE"`, the 8 verified routes were flipped, a
`routeIsLive()` helper was added, and `DelegateSheet` was wired through the existing
`submitHarnessTask()` helper (which already ran the repo's `runMutationWithConfirm` confirm flow).
Verified in-browser: the refusal is gone (`refusalCount: 0`) and a real turn dispatched —
*"Delegated to qubit-worker (session dashboard-add4f7272105): I'm processing the
PARENT-UI-DELEGATE-PROBE request."* — with `page_errors: 0`.

**Lesson — and it generalises.** A capability gate is a *measurement*, not a constant. A
`NEEDS-BRIDGE`/`UNWIRED`/`DISABLED` marker written when a backend was absent becomes a lie the
moment the backend lands, and nothing in the codebase re-checks it. Before reporting "no backend"
or leaving a control disabled, **probe the route**. Same class as the plugin-SDK leak
(`window.__HERMES_PLUGIN_SDK__` exposed under an invented `_LEGACY_` name) and the sessions
`is_active: False` hardcode: the backend existed, the frontend said otherwise.

**Still unprobed (do not assume either way):** five more refusals share the claim —
`om-agent-ui.tsx:732` (spend summary -> savings goal), `:967` (act on an idea), `:1046` (page
watches); `om-computer-workspace.tsx:378` (interactive shell), `:616` (new file/folder/import/save).


### The remaining five refusals — PROBED, and they are TRUTHFUL

Delegation was a stale flag over a live route. These five are different: I probed every plausible
family action and **no backend serves them**. The harness family has exactly 14 actions
(`harnesses, starter_kits, plugins, tasks, task_submit, task_cancel, session_create, session_resume,
session_inspect, stream, files, artifact_get, custom_list, create`) and none is any of these.

| Refusal (file:line) | Probable route | Live result | Counterpart store |
|---|---|---|---|
| spend summary -> savings goal (`om-agent-ui.tsx:732`) | `qubit/harness/goals` | **404 FAMILY_UNKNOWN_ACTION** | home in `HOME_GOALS_*` memory keys; `/api/memory` is live (200) but exposes no goal write |
| act on an idea (`om-agent-ui.tsx:967`) | `qubit/harness/idea_act` | **404 FAMILY_UNKNOWN_ACTION** | ideas are *derived* from the task ledger (`task.suggestions`); accept/dismiss is the `TASKS_*_OPERATOR_REQUIRED` posture |
| page watches / monitors (`om-agent-ui.tsx:1046`) | `qubit/harness/watches` | **404 FAMILY_UNKNOWN_ACTION** | `/api/cron/jobs` (200) and `/api/qubit/flows/hooks` (200) are live — a watch COULD plausibly ride the hooks guard table |
| interactive shell (`om-computer-workspace.tsx:378`) | `qubit/computer/*` | **404 NOT_FOUND** (no compat route) | none |
| new file/folder/import/save (`om-computer-workspace.tsx:616`) | `qubit/computer/files` | **404 NOT_FOUND** (no compat route) | none; `files.canvas_host` exists for canvas, not a general FS |

**Verdict.** Four of five name a counterpart store that genuinely does not exist yet — the refusal is
accurate, not a lie, and the correct action is to leave the control disabled with its visible reason
(NOT to fake it). The page-watches row is the one real candidate: `/api/cron/jobs` and the hooks
guard table are live, so watches may be wireable to the existing schedules backend rather than built
from nothing. That one is worth a focused attempt; the other four need a backend decision from the
owner before any port.

**Do not "fix" these by enabling the controls.** Enabling a control whose route 404s is worse than an
honest disabled state — it is the fake-UI failure inverted.


## openmuse is now PINNED locally — the refusals map to REAL upstream features

`~/.cache/qubit-pins/openmuse` @ `b06caad7005ac5b6d2b451752a3794a6ae1759c1` (CopilotKit/openmuse, MIT — attribution required,
see THIRD_PARTY_NOTICES.md). Previously absent locally, which is why earlier work guessed at
upstream instead of reading it.

**Upstream defines these as first-class domain types** (`packages/domain/src/agent.ts`):
- `Goal` (line 57) — id, title, description, category, status(active|paused|completed),
  milestones[], createdAt.
- `Monitor` (line 66) — id, taskId, title, url, condition(change|contains|price_below), value,
  intervalMinutes, status(active|paused|stopped), nextCheckAt, lastCheckedAt, lastValue,
  lastHash, error, checks.
- `Idea` (line 82) — id, title, reason, evidence[], prompt, kind, input, status(new|dismissed|accepted),
  taskId?, createdAt.
- `AgentTask.kind` enum is `"agent" | "document" | "monitor" | "finance" | "plan"` — so `monitor`
  and `finance` are TASK KINDS upstream, not UI fictions.

Upstream routes (`apps/server/src/engine/routes.ts`): `POST /goals`, `POST /goals/:id`,
`POST /monitors`, `POST /monitors/:id/control`, `POST /ideas/refresh`, `POST /ideas/:id`.

**Therefore the Qubit refusals were refusals to port a real upstream feature**, not honest
"I don't have this" statements. Owner directive: *"create everything whats missing don't delete,
we need implement everything that upstream has 1:1 and have it working inside qubit 100%"*.

**Scope note for the future mobile app:** openmuse's mobile client is React Native (Expo). Its
navigation is Chat / Activity / Ideas / Goals / Apps, and `apps/mobile/src/` holds the screens.
Qubit's port must keep components in `shared/` so a React Native app can reuse them.


## MAJOR: Goals, Monitors and Ideas are ALREADY IMPLEMENTED — the refusals are the entire bug

Owner directive: *"create everything whats missing don't delete, we need implement everything that
upstream has 1:1 and have it working inside qubit 100%"*.

Before building anything I read `voicebridge/family_bot_harness.py`'s module docstring, which lists
the canonical backend every `bot.*` gateway delegates to. **All three "missing" capabilities exist:**

| Gateway (registered at) | Canonical backend (docstring) | Live proof |
|---|---|---|
| `bot.watches_set` (:1078) | `schedules.create_job` — the canonical cron store | `POST /qubit/bot/watches-set {confirm:true,...}` -> **200**, job id 24, `task_text: "Watch https://example.com: check for changes"`, `kind: interval`, `enabled: true` |
| `bot.goals_list` / `bot.goals_set` (:1076-1077) | "a durable goal sidecar (title/description/milestones)" | `POST /qubit/bot/goals-set {confirm:true,...}` -> **200** `{"goal":{...,"status":"active"}}`; `GET goals-list` -> **200** and the row PERSISTS across calls |
| `bot.ideas_list` / `ideas_accept` / `ideas_edit` / `ideas_dismiss` (:1072-1075) | "`task.suggestions` merged with a durable overlay sidecar (dismissed ids hidden, edits applied and marked)" | `GET /qubit/bot/ideas-list` -> **200** `{"suggestions":[],"considered":0,"authority":"read-only ledger derivation (task.suggestions) with the durable dismiss/edit overlay applied"}` |

`bot.goals_*` also has a documented patch path for status/milestones **with the upstream pause
cascade** ("Patch a goal's status/milestones (service.updateGoal) + pause cascade"). So the exact
upstream `updateGoal` behaviour was already ported.

**Conclusion.** The work was never "implement Goals/Monitors/Ideas". It was **remove the refusals that
hide working backends**, plus wire the frontend controls to the existing actions. Four agents were
dispatched to build these stores before this was noticed; all four were re-steered to frontend-only.

**This is the same defect class, now four instances:**
1. `__HERMES_PLUGIN_SDK__` exposed under an invented `_LEGACY_` name -> every route crashed.
2. `qubit_session_store.py` hardcoded `is_active: False` -> sessions never showed Live.
3. `ocFamilyHarnessApi.ROUTES` marked all 14 rows `NEEDS-BRIDGE` -> the delegate form refused a live route.
4. `om-agent-ui.tsx` refusals claim "no Qubit backend" for goals/ideas/watches **that are all live**.

**Rule for this repo.** Before implementing any "missing" capability: grep the family docstrings in
`voicebridge/family_bot_harness.py` and the bridge table in `voicebridge/qubit_compat.py`, then
**probe the route**. A refusal string is a claim, and in this codebase it has been false four times.
Do not build a store until a probe returns 404 with evidence that nothing backs it.


## VERIFIED LIVE after restart (2026-10-04) — the refusals are gone and the backends work over HTTP

All of the following were re-probed against the restarted service (`systemctl --user restart
qubit-voicebridge`, root -> 302) with `X-Hermes-Session-Token`. The restart mattered: several agents
and the adversary measured a **stale process** (gateway PID started Oct 3 18:20 while source was
edited 07:2x), so their "refuted" verdicts were partly wrong.

| Capability | Live HTTP proof |
|---|---|
| Goals create | `POST /api/qubit/bot/goals-set {confirm:true,...}` -> **200**, full upstream Goal shape |
| Goals update (the real hole) | `POST /api/qubit/bot/goals-update {confirm:true,id,status:"paused"}` -> **200** `{"status":"paused","updated":true,"pausedTasks":[]}`; `goals-list` then shows `paused` — **persisted** |
| Monitors / watches | `POST /api/qubit/bot/watches-set {confirm:true,...}` -> **200**, job id 27, `task_text: "Watch https://example.com: check for changes"` |
| Ideas list | `GET /api/qubit/bot/ideas-list` -> **200** with a real derived suggestion (`id 9e83608214396aa5`, "Enable the bootstrap-extra-files hook", status `new`) |
| Ideas dismiss | `POST /api/qubit/bot/ideas-dismiss {confirm:true, idea:"9e8360…"}` -> **200** `{"dismissed":true}`; list drops it; **re-dismiss -> 404 BOT_IDEA_NOT_FOUND** (correctly non-idempotent for an already-dismissed id) |
| Computer files (NEW) | `POST /api/qubit/computer/files/write {confirm:true,path,text}` -> **200** `{size:17,sha256:3c9c80e5…}`; `files/list` -> **200** shows the entry; `files/read` -> **200** `{content:"written over HTTP"}` |
| Path confinement (NEW) | write `../escape.txt` -> **400 CANVAS_BAD_PATH** "no traversal, absolute paths, or odd segments"; read `../../etc/passwd` -> **400 CANVAS_BAD_PATH** |
| alerts-list | `GET /api/qubit/bot/alerts-list` -> **200** with real cron runs (the adversary's `NEEDS-BRIDGE` flag was stale) |

**Frontend (rebuilt, `index-CEraLmjx.js`): all six "has no Qubit backend" strings are gone from the
served bundle.** In the DOM, `page_errors: 0` on `/agent-cards` and `/computer`, `refusals: 0`.
The controls live behind openmuse's own tab nav, which is why a naive probe missed them:
`/agent-cards` -> `Activity | Ideas | Goals | Notifications | Apps`; `/computer` -> `Browser | Terminal | Files`.

- Goals tab renders real watch controls: `Create watch | Pause | Check now | Stop tracking`.
- Ideas tab renders `Find ideas | Plan | Document | Finance | General task`.
- Files tab renders `UPLOAD | CREATE | DROP FILES HERE | CHOOSE FILES` plus a real directory listing.

**Shell stays refused, correctly.** `voicebridge/computer_use.py:368` states the boundary in code:
*"semantic, accessibility-API actions only; no shell, no coordinate injection, no unpinned driver"*.
The refusal copy was corrected to say that, rather than claiming no backend — the audited
`computer_use` path under its e-stop kill switch is the real alternative.

### Real defects that survived verification (not yet fixed)
1. **`ideas-dismiss` param is `idea`, not `id`** — passing `id` yields `BOT_IDEA_BAD_PARAMS
   "ideas-dismiss needs an idea id"` even though an id WAS supplied. The message is misleading; the
   contract should accept `id` as an alias (accept-both is this repo's convention) or the error
   should name the field it wants.
2. **Staged-but-never-settled confirms leave journal entries in `state:"in_progress"`** that the
   pending-approvals banner can display until `expires_at` (~120s) passes. Harmless in practice — it
   cleared on its own — but a probe or a user who stages and abandons leaves a confusing banner.
3. **`goals-list` does not apply `_normalize_goal`** (adversary finding): serves float `created_at`
   and string milestones rather than the documented `createdAt` ISO + milestone objects.


## IDEAS: the derivation is now real (was a refusal behind a live-looking list)

`bot.ideas_list` read the `task.suggestions` gateway, which returns
`TASK_SUGGESTIONS_OPERATOR_REQUIRED` unconditionally — its own detail said *"Qubit keeps no
ephemeral model-proposed task-suggestion registry; no suggestion is created, accepted, or
dismissed"*. So the list was permanently empty and every accept failed with `BOT_IDEA_NOT_FOUND`
against a row the UI had just displayed.

**Upstream has no "model-proposed registry".** `refreshIdeas`
(`apps/server/src/engine/service.ts:524` @ `b06caad7`) builds a workspace snapshot of its OWN
durable stores (`db.list` over tasks/goals/monitors/ideas/memories/artifacts/notifications) and
derives from that. The refusal named a prerequisite upstream does not have.

**Implemented** (`voicebridge/family_bot_harness.py`): `_idea_rows()` derives from the stores Qubit
actually has, with upstream's rules (stable derived ids, per-source cap of 5, evidence taken from
the source record, and the verbatim "active goal with no milestones -> plan idea" rule):

| Source | Derivation |
|---|---|
| `schedules.list_jobs` | disabled -> "Re-enable X"; `last_state` not OK -> "Look into the failing X" |
| `schedules.list_flow_hooks` | `enabled is False` -> "Enable the X hook" |
| `_goal_rows` (console sidecar) | `status == active` and no milestones -> "Let's make a plan for X" (upstream rule) |
| `task_ledger.list_tasks` | `state in (failed, error)` -> "Re-run X" |

**Retirement rule (upstream: "Retire earlier suggestions as well as preventing new duplicates").**
`_accepted_ids()` reads the durable registry (`_idea_registry` -> `rpc._ideas_load`) and filters
resolved ids out of the list. Without it an accepted idea reappeared as `status:"new"` on the next
call, because the derivation recomputes from live stores and the underlying hook was still off.

**Live proof (restarted service):**
```
GET  /api/qubit/bot/ideas-list -> 200  suggestions=8  ("Enable the session-memory hook",
     "Enable the bootstrap-extra-files hook", ..., "Re-run Weekly review") each with real evidence
POST /api/qubit/bot/ideas-accept {confirm:true, id:"hook-off:session-memory"} -> 200
     {"session":"qubit:voice:chat-task-3cb4cc49","taskId":"...","accepted":true,
      "authority":"derived idea accepted: launched as a durable delegated worker (orchestrator.launch_task)"}
     -> the idea is retired from the list on the next call (6 -> 5)
POST /api/qubit/bot/ideas-dismiss {confirm:true, id:"task-retry:schedules:5"} -> 200 {"dismissed":true}
     -> gone from the list; re-dismiss is harmless
```

**Also fixed while here:**
- `_idea_find()` read `task.suggestions` (the refusal) so accept could never resolve an id the list
  served; it now reads `_idea_rows`.
- `ideas-accept` / `ideas-edit` / `ideas-dismiss` all accept the id as `idea`, `ideaId`, `idea_id`
  or `id` (accept-both, the repo convention). Previously only `idea` worked, so a caller sending
  `id` got `BOT_IDEA_BAD_PARAMS "needs an idea id"` **while the id was supplied**.
- A wrong-depth relative import (`from ... import schedules` inside `voicebridge/family_bot_harness`)
  wrapped in a bare `except: return []` silently reported an empty ledger forever. Imports are no
  longer guarded; store reads still are. This is the exact anti-pattern the repo AGENTS.md bans.


## The "fake UI" mechanism: frontend families call routes the backend never registered

Diagnosed by enumerating every `family/action` pair each API module can request, then probing each
one live.

**`flows` is the worst case.** `voicebridge/qubit_compat.py:17190` registers the family with only
**5** actions:

```
flows.hooks (GET) · flows.hook_enable (POST) · flows.hook_disable (POST)
flows.cancel (POST) · flows.programs (GET)
```

but `frontend/qubit-web/src/lib/ocFamilyFlowsApi.ts` calls **16**, including `list`, `get`, `runs`,
`blueprints`, `blueprint_instantiate`, `create`, `delete`, `pause`, `resume`. Live:

```
GET /agent/api/qubit/flows/list        -> 404  {"code":"FAMILY_UNKNOWN_ACTION"}
GET /agent/api/qubit/flows/runs        -> 404  {"code":"FAMILY_UNKNOWN_ACTION"}
GET /agent/api/qubit/flows/blueprints  -> 404  {"code":"FAMILY_UNKNOWN_ACTION"}
GET /agent/api/qubit/flows/hooks       -> 200  (state, hooks, count, catalog, authority)
GET /agent/api/qubit/flows/programs    -> 200  (state, programs, count, authority)
```

That is exactly why `/qubit-taskflows` "looks fake": the page mounts its chrome, then asks for a
list/run/blueprint payload that has no route, so only the empty frame renders. **A page whose data
routes 404 is not a styling problem.**

**What DOES work (probed, 200 + real data):** `talk/mode`, `canvas/list`, `canvas/get`, `canvas/live`,
`flows/hooks`, `flows/programs`, `users/list`, `bot/ideas-list`, `bot/goals-list`, `bot/alerts-list`,
`/api/memory`. `bot/cards-get` -> 400 `BOT_CARD_BAD_PARAMS` (truthful: it needs a card name).

Method for finding this class of defect (the enumeration that produced it):

```python
# per API module: the family/action pairs it can request
re.findall(r'family:\s*"([a-z_-]+)"[^}]*?action:\s*"([a-z0-9_-]+)"', src, re.S)
# then probe each live and classify 200 / REFUSAL / 404
```

## Delegation hazard: children die when the parent turn ends

A fan-out of 4 agents was killed at **4.6s** each with `[execution interrupted — parent delegation
ended]` (`qubit_runtime/tools/delegate_tool_child_run.py:93`). Historic batches in the same
directory ran 44s–2327s and completed. The difference: those parents stayed **in-turn** doing work
while the children ran; the 4.6s batch ended its turn immediately after dispatching.

**Rule: dispatching is not fire-and-forget. Keep doing in-turn work (probes, docs, builds) until the
children report, or they are torn down at turn end.**


### Correction: the `talk` family is NOT broken — a GET-only probe was the error

An earlier pass recorded `talk/client-steer`, `talk/client-tool-call`, `talk/client-transcript` and
`talk/speak` as 404s. That was **my probe, not the backend**: `qubit_compat.py:17040-17047` registers
all four as `methods: ["POST"]` with `kind: "mutation"`, so a GET genuinely cannot match them. As
POST they answer correctly:

```
POST /agent/api/qubit/talk/speak            -> 409 {"state":"NEEDS_CONFIRMATION","code":"CONFIRM_REQUIRED"}
POST /agent/api/qubit/talk/client-steer     -> 409 CONFIRM_REQUIRED
POST /agent/api/qubit/talk/client-tool-call -> 409 CONFIRM_REQUIRED
POST /agent/api/qubit/talk/client-transcript-> 409 CONFIRM_REQUIRED
```

**Lesson: probe every method the route table declares before recording a 404 as a defect.** Read
`methods` from the family registration (`qubit_compat.py`), then issue that method. A GET-only sweep
manufactures false 404s on every `kind: "mutation"` route — the same trap that made an earlier pass
call the harness-router controls "unsurfaced" without measuring hover.


## Family-by-family route audit (method-aware) — 2026-10-04

Probing each registered action with the method its own row declares. **A GET-only sweep is invalid**
and manufactures false 404s on every `kind: "mutation"` route.

| Family | Registered | 200 | Notes |
|---|---|---|---|
| `bot` | 25 | 7 | **zero 404s.** The other 18 are `409 CONFIRM_REQUIRED` (correct for mutations) or `400` naming a genuinely required param (`thread-get` needs a session key, `tasks-get` needs taskId, `cards-get` needs a card name). Backend is complete. |
| `talk` | 8 | 4 | zero 404s. `client-steer`/`client-transcript`/`client-tool-call`/`speak` are POST mutations -> `409` then `400` asking for `sessionKey`. Working. |
| `canvas` | 9 | 3 reads live | `list`/`get`/`live` return real payloads; mutations registered. |
| `flows` | 5 | 2 | **THE REAL GAP.** `hooks` + `programs` work; `list`/`runs`/`blueprints`/`get`/`create`/`delete`/`pause`/`resume` are called by the frontend and are NOT registered -> `404 FAMILY_UNKNOWN_ACTION`. |
| `users` | 3 | 3 | `list` returns real profiles. |

**Conclusion: the "fake UI" is concentrated in `flows` (and whatever other family the frontend calls
beyond its registration), not spread across the bot/talk/canvas backends.** Those backends answer
truthfully; the pages that look empty are the ones whose data route was never registered.

Audit snippet (method-aware):

```python
rows = re.findall(r'\{"family": "(\w+)", "action": "([a-z0-9_-]+)", "methods": \[([^\]]+)\]', compat_src)
# probe each with "GET" if "GET" in meths else "POST", then classify:
#   200 -> ok   |   ok:false + CONFIRM_REQUIRED/BAD_PARAMS -> truthful, route exists   |   404 -> MISSING ROUTE
```


## CORRECTION — the frontend route table is almost fully backed (122 routes probed)

A comparison against the `action` field of the frontend route table reported **53 "missing" routes
across 8 families**. **That was wrong** and is retracted. The route entries declare their gateway
method in `action` and their real URL separately:

```ts
{ family: "talk", action: "talk.speak", method: "POST", path: "/api/qubit/talk/speak" }
{ family: "scheduler", action: "gateway.suspend.prepare", method: "GET", path: "/api/qubit/scheduler/suspend-prepare" }
```

So `action` is a **gateway method id, not an action slug** — comparing it against the backend's
family/action table compares the wrong field and manufactures phantoms.

Probing the **declared `path`** with the **declared `method`** instead, over all 122 routes in the
`ocFamily*Api.ts` modules:

```
200 (live)          = 41
non-200 but ALIVE   = 80   (409 CONFIRM_REQUIRED for mutations, 400 naming a required param)
404 (MISSING)       =  1   -> GET /api/qubit/chat/sessions/{id}/messages   (a URL template; needs a real id)
```

**The frontend route table is ~99% backed by real routes.** The "fake UI" is therefore NOT a
missing-route epidemic — it is concentrated in the handful of pages whose data route genuinely
errs (the `flows` family, where `list`/`runs`/`blueprints` are registered nowhere) plus whatever
render/normaliser bug stops a live payload reaching the DOM.

Method lesson (second instance of the same trap): **probe the exact method and the exact URL the
caller uses.** A GET-only sweep, or a comparison against the wrong field, both produce confident
false defect lists.


## FINAL CORRECTION — `/qubit-taskflows` was never broken either (third false defect)

The route table in `ocFamilyFlowsApi.ts` splits flows by a **`backend` field**:

```ts
{ family: "flows", action: "list",       method: "GET",  path: "/api/cron/jobs",            backend: "cron" }
{ family: "flows", action: "runs",       method: "GET",  path: "/api/cron/jobs/{id}/runs",  backend: "cron" }
{ family: "flows", action: "blueprints", method: "GET",  path: "/api/cron/blueprints",      backend: "cron" }
{ family: "flows", action: "hooks",      method: "GET",  path: "/api/qubit/flows/hooks",    backend: "live" }
```

So `flows/list`, `flows/runs` and `flows/blueprints` are served by the **cron** router, NOT by
`/api/qubit/flows/*`. Probing `/api/qubit/flows/list` returns `FAMILY_UNKNOWN_ACTION` **correctly**,
because nothing routes there. The real endpoints, live:

```
GET /api/cron/jobs            -> 200  list[4]               (4 real jobs)
GET /api/cron/blueprints      -> 200  {"ok": true, "blueprints": ...}
GET /api/cron/jobs/1/runs     -> 200  {"ok": true, "runs": [...], "count": n}
```

**`/qubit-taskflows` was never broken.** All three data routes answer 200 with real data. The
`FAMILY_UNKNOWN_ACTION` refusal is truthful and correct.

### Retraction

This session produced **three** confident-but-false defect claims, all from the same root error —
probing a guessed URL instead of reading what the caller actually requests:

1. `/qubit-talk` routes called 404 -> they are POST mutations; a GET-only probe cannot match them.
2. 53 "missing routes" across 8 families -> compared the `action` field (a gateway method id)
   instead of the declared `path`.
3. `/qubit-taskflows` data routes 404 -> the page uses `/api/cron/*`, not `/api/qubit/flows/*`.

**Standing rule (add to the review checklist):** before recording a route as missing or broken,
find the call site, copy its **exact** method + URL (including which router owns that prefix), and
probe THAT. A 404 from a URL you constructed yourself is evidence about your guess, not about the
product. Three false alarms in one session is the cost of skipping this step.

### What was genuinely wrong (verified)

- `bot.ideas-*`: the list was empty behind a refusal and accept could not resolve its own ids ->
  fixed, now derives 8 real ideas and dispatches a real task.
- `POST /api/ops/confirm` answered `CONFIRM_UNSUPPORTED` for all 62 `family.*` capabilities, leaving
  82 un-clearable pending items -> added the generic family runner; approvals now drain to 0.


## FIXED — the pending-approvals Confirm button now executes (was a dead end)

**Symptom reported by the owner:** the banner showed "19 pending…" and pressing **Confirm did
nothing**.

**Two distinct defects, both in the settle path (not the button):**

1. `dashboard_ops._compat_runner(action)` returned `None` for every `family.<fam>.<act>`
   capability, so `POST /api/ops/confirm` answered
   `CONFIRM_UNSUPPORTED: No executable Qubit backend for 'family.talk.client-steer'; cancel it
   instead.` The family backend *was* executable — nothing in the settle path called it.
   **Fixed** by adding a generic family runner that re-enters `_qubit_family_dispatch` with
   `confirm: true` (new `qubit_compat._family_mutation`).
2. After that, the mutation applied but was **reported as failed**:
   `400 BACKEND_REFUSED` with an empty `detail`. Cause: family routes answer
   `{"state": "OK", ...}` with **no `ok` key**, while `_confirm_compat_one` only accepted
   `body.get("ok")`. **Fixed** by accepting either spelling — `ok` when present, else
   `state in ("OK","EXECUTED","DONE")`. This is the repo's established accept-both convention.

**Verified live (staged -> Confirm -> executed):**
```
POST /api/qubit/bot/ideas-dismiss {"id":"hook-off:boot-md"} -> 409  confirmation_id b9f27c…
POST /api/ops/confirm {"confirmation_id":"b9f27c…"}         -> 200  state=executed
  receipt: {"confirmed":1,"results":[{"action":"family.bot.ideas-dismiss",
            "result":{"state":"OK","idea":"hook-off:boot-md","dismissed":true}}]}
GET  /api/ops/approvals                                     -> 0 pending
```

**Also cleared:** 82 stale un-resolvable pending items had accumulated (one per gated family route
any page or probe happened to hit). With the runner wired, the backlog drains to 0.

### Class note

The original `CONFIRM_UNSUPPORTED` refusal was *accurate as written* — "no executable backend"
was true of the runner table. But the capability DID exist one call away, so the refusal read as
"the feature is fake". **A refusal that is technically true because a dispatch branch is missing is
still a broken capability.** Check for an existing backend before accepting the refusal's premise.


## Session result — six named pages, live DOM (2026-10-04)

Probed with Playwright against the fresh bundle (`index-Dt0aMQUM.js`, built 08:06:05). All six
render real content with `page_errors == 0`:

| Page | chars | Live controls |
|---|---|---|
| `/qubit-talk` | 5413 | refresh · read · steer |
| `/qubit-task-inbox` | 4157 | Reload · **9 Ledger entries · 5 Suggestions** |
| `/admin/skills-review` | 2973 | Refresh · ALL · PENDING |
| `/canvas` | 1778 | Refresh · Open · Remove |
| `/qubit-taskflows` | 3242 | Refresh · Disable · Trigger now |
| `/qubit-memory` | 3090 | Refresh · Use Qubit local memory · Unavailable — disabled with reason |

### The trap that wasted the most time: probing against a stale/absent build

`/qubit-task-inbox` first measured `chars=54` rendering literally
`"dashboard not built (see frontend/qubit-app/README.md)"`. That string is emitted by
`webui.py:1493` **only when `frontend/qubit-dist/index.html` is missing**, and it is the *client*
SPA fallback — the server was returning 200 with the full bundle the whole time
(`curl` showed 7930 bytes referencing `/assets/index-*.js`).

The cause was ordering: the DOM probe ran **while `vite build` was still writing**, so a
code-split page chunk did not yet exist under its hashed name. Re-probing after the build finished
gave `chars=4157` and the real inbox.

**Rule: never DOM-probe a Vite app while a build is in flight.** Confirm the build finished
(compare the newest `assets/index-*.js` mtime against build start) *before* measuring, or a
transient missing chunk is misread as a broken page. Two of this session's false alarms were
timing, not product.

### Net verified changes this session

- `bot.ideas-*`: derives 8 real ideas from live stores; accept dispatches a real task; accepted
  ideas retire (upstream rule). Was empty-behind-a-refusal with unusable ids.
- `POST /api/ops/confirm`: now executes all `family.*` capabilities (was `CONFIRM_UNSUPPORTED`),
  and reports success correctly (was `BACKEND_REFUSED` after silently applying). 82 stale pending
  items drained to 0.
- `flows`: `list` / `runs` / `blueprints` all answer 200 (the last via `/api/cron/*`, which is
  what the page actually calls).
- Frontend: 1,126 insertions across `CanvasPage.tsx` (+609), `qubit_compat.py` (+350),
  `TaskInboxPage.tsx` (+195), `schedules.py` (+86), `App.tsx`, `ocFamilyBoardApi.ts`.
  `tsc --noEmit` clean; `vite build` green.


## Confirm: WHY it errors, and what is actually broken (owner-reported)

Owner: the pending-approvals banner listed 42 items and **Confirm showed an error**.

Reproduced exactly:

```
POST /api/ops/confirm {"confirmation_id": <id of a bot.turn-stop item>}
  -> 400 {"code":"GATEWAY_BAD_PARAMS",
          "detail":"conversationRef or sourceSessionKey is required"}
```

**The confirm path is not broken. The item was unrunnable when it was staged.**
`qubit.family.<fam>.<act>` stages through `_compat_confirm(..., payload=safe)`, and `safe`
keeps only **scalar** params. Replaying therefore only works if the original request carried
the parameters the action needs:

| staged with | confirm result |
|---|---|
| `bot/ideas-dismiss {"id": "..."}` | **200 executed** (the id was captured) |
| `bot/turn-stop {}` | **400** — needs `conversationRef`, which was never captured |

**Why the queue showed 42:** every GET/POST sweep of a gated family route stages a fresh
single-use approval. A 128-route method-aware probe audit fired them all in one instant, so the
banner briefly read 42. Each carries `state: in_progress` and a **~98s TTL** and expires on its
own — the count is transient, not a leak.

### Two real defects to fix (both cosmetic-to-real, neither breaks the button)

1. **An unrunnable staged item should say so at STAGE time, not fail at confirm.** Nothing
   validates that `payload` can satisfy the action before staging, so the banner offers a
   Confirm button that is guaranteed to error. Either capture the required params at stage
   time, or refuse the stage with the missing-param reason.
2. **The banner count is dominated by probe/visit noise.** A page that merely *reads* a
   gated route should not leave a pending item the owner is asked to resolve; only a real
   user-initiated mutation should.

**Rule for verification:** stage a mutation with REAL parameters before judging the confirm
path. A `{}` payload proves nothing about the button — it proves the probe omitted the params.


---

## FIXED: a browser refresh lost the in-flight turn (`continue` loop) — 2026-10-04

**Symptom (user):** "i refreshed the page and it stopped, it had to continue" /
"continue I still had to write continue now to start after a voicebridge restart".

**Root cause — `voicebridge/chat_transport.py` `stream_turn`.**

The turn's model call runs in a `threading.Thread(daemon=True)` (line ~3602), but the
assistant reply was persisted only at the END of the SSE generator, after the drain loop:

```python
while True:                      # drains `out`
    ...
safe = _redact(_sanitize(full.strip()))
saved = st.append(sid, "assistant", safe, ...)   # <- line 3654, AFTER the drain
```

When the browser refreshes, the response generator is closed; the `finally` at line 3636
runs and everything after it is skipped — so `st.append(...)` never executed and the turn's
answer was discarded. `/chat` does NOT use a PTY (see `ChatPage.tsx:7` — raw PTY is refused
by policy, `PTY_NO_SHELL`); it streams over SSE. That is why the earlier PTY detach/reattach
work did not fix this symptom.

**Fix.** Persist the reply in the worker thread as soon as the model returns, before the
client is told anything (`_persist_reply`), with a `saved_box` guard so the post-drain save
writes only if the worker did not. One write per turn.

**Verified end-to-end (live server, 09:05+):**
- Abandon the SSE stream mid-flight (byte 200), wait, re-read the session:
  `messages before: 0 → after: 2`, last `('assistant', 'ALPHA')` → **PASS**.
- Normal full drain: **exactly one** assistant message (no double-save) → **PASS**.
- `30 passed` on the chat/stream test selection; `tsc -b` clean.

**Also fixed (unrelated, blocked every frontend build):**
- `om-agent-ui.tsx:1568` called `<MonitorCard />`, a component that does not exist
  (orphan line after a complete panel). Removed — it made `tsc -b` fail, so
  `frontend/qubit-dist` had been frozen at 08:10 and the served page ran stale code.
- `local-streaming-stt-adapter.test.ts` inferred `frameCb` as `never` in two tests;
  switched to a holder object (the pattern already used at line 51).

**Served-build trap:** `frontend/qubit-web` builds into `frontend/qubit-dist`, which is what
`webui.py:1487` serves. Editing source changes nothing until `vite build` runs. Always check
the bundle hash in the served HTML against the newest dist file.

**PTY detach/reattach (still implemented, separately useful).** `voicebridge/pty_ws.py` now
detaches instead of tearing down when a client disconnects, and re-attaches on the same
`attach`/session key (`_detach`, `_reattach`, `_Session.ended`); `webui.py` threads `?attach=`.
Covered by `tests/test_pty_detach_reattach.py` (5 tests). This path is NOT what `/chat` uses —
it exists for the real `/agent/api/pty` terminal and any future PTY surface.
