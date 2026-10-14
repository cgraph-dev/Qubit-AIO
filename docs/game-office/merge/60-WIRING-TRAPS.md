# 60 — Wiring traps

Facts discovered by **reading the running code**, not assumed. Each would bite an implementer
who followed the docs instead of the source.

---

## Trap 1 — the API is not at `/api/office/tower`

A bare `GET http://127.0.0.1:8890/api/office/tower` returns **404**:

```
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8890/api/office/tower
404
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8890/agent/api/office/tower
401        # exists, needs the session token
```

The SPA's API lives under the **`/agent` prefix** (`webui.py:2484`: `/agent/api/<x>` is
rewritten to `/api/<x>`). `webui.py:1069` registers `/api/office/tower` in
`QUBIT_READ_AUTHORITY`, which is the *internal* name, not the wire path.

**Consequence:** any new probe, test, or doc that says `GET /api/office/tower` without the
`/agent` prefix will 404 and be misread as "the endpoint is broken." The browser is fine
because the SPA bundle knows the prefix (`__QUBIT_API_PREFIX__="/agent"`).

### Known false positive — do not re-report this

**Two independent review passes have now reported "the running dashboard can't fetch office
data: `GET /api/office/tower` returns 404" as their headline finding.** Both were testing the
wrong URL. The route is served, and the page renders.

Verified 2026-10-14 from the live daemon:

```
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8890/agent/api/office/tower
401                                   # route EXISTS (auth required, as designed)
$ .venv/bin/python scripts/render_office_screenshot.py --json
data-state=ready, canvas_count=2, non_background_pct=37.4, console_errors=[]
```

The page itself fetches `fetchJSON("/api/office/tower")` (`qubit-office.js:1177`) and the
**plugin SDK prepends the served prefix** — `window.__QUBIT_API_PREFIX__ = "/agent"` is
declared in the page the server sends. So the browser requests
`/agent/api/office/tower` and gets 200 with the session token.

**Rule for reviewers:** if you find a 404 at `/api/...`, curl the `/agent/api/...` twin before
writing it up. A 401 on the prefixed path means the route is fine and you simply had no token.
A reviewer with no browser cannot distinguish "the route is missing" from "I asked the wrong
host path" — so state which URL you tested and its exact status.

The stale-daemon suspicion ("the Python process started 12 October, so its route table is
older than the source") is also wrong for this route: the office route has been registered
since before that process started, and the page demonstrably reaches `ready`.

---

## Trap 2 — `.wav` is not servable (M9)

`_PLUGIN_ASSET_CONTENT_TYPES` (`qubit_runtime/qubit_cli/web_routers/dashboard_ui.py:379`):

```python
{".js", ".mjs", ".css", ".json", ".html", ".svg",
 ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
 ".woff2", ".woff", ".ttf", ".otf", ".map"}
```

**Audio is absent.** Sprite/furniture/tile/font assets serve today; sound does not. Options:
1. add `".wav": "audio/wav"` (one line) in that vendored file — but it is a vendored runtime
   file, so the change must be made in `qubit_runtime/` and the pin refreshed, not patched in
   `.venv` only;
2. inline the 12 sounds as base64 data-URIs in the bundle (no server change, larger bundle);
3. drop the sounds (they are optional).

The plan stages the files and defers the decision. **Do not ship audio before resolving this**
or the page silently 404s 12 requests.

---

## Trap 3 — plugin assets are path-confined

`resolve_plugin_asset` (`qubit_compat.py:10965`):

```python
if not target.is_relative_to(base.resolve()):
    return 403, ..., {"code": "PLUGIN_PATH_BLOCKED"}
```

`base` is the plugin's own directory. So the vendored tree **must** live under
`frontend/qubit-app/plugins/qubit-office/` (→ `assets/`), never at the repo root and never a
symlink out of the plugin dir (`.resolve()` follows symlinks).

---

## Trap 4 — the served plugin dir is the source dir, and there is also a `qubit-dist` copy

Two resolution paths exist and they are **not** the same:

| route | served from | code |
|---|---|---|
| `/dashboard-plugins/qubit-office/<file>` | `frontend/qubit-app/plugins/qubit-office/` | `webui.py:1610` `_serve_qubit_plugin` |
| `/plugins/qubit-office/<file>` | `frontend/qubit-dist/plugins/` | `_serve_qubit_static` |

Verified live: `/dashboard-plugins/qubit-office/qubit-office.bundle.js` → **200, 188073 b**;
`/plugins/qubit-office/qubit-office.bundle.js` → **404** (the built dist has no office folder).
The SPA manifest has **no `assetBase`**, so `pluginAssetRoot()` (`fleet.ts:47`) defaults to
`/dashboard-plugins/<name>` — the working path. **Do not add an `assetBase` for office**; it
would move it to the 404 path.

---

## Trap 5 — the manifest must stay in sync in two places

`55-DASHBOARD-PAGE.md` §deviation 3: the sidebar is built from the static fleet **plus**
`GET /api/dashboard/plugins`. The office entry must be identical in:
- `frontend/qubit-web/public/plugins/fleet.json`
- `frontend/qubit-dist/plugins/fleet.json`

and the plugin dir from `frontend/qubit-app/plugins/qubit-office/manifest.json`. A test
(`test_office_tower_plugin.py`) asserts the two fleet copies match. Adding an `assets/` folder
does **not** need a manifest change (the folder is fetched, not declared) — but adding a
*module* does need the bundle `ORDER` update (Trap 6).

---

## Trap 6 — a new JS module is invisible until added to the bundle ORDER

The loader injects one `<script>` per entry. `scripts/build_office_bundle.py` concatenates the
modules in its `ORDER` list and **asserts** each module's expected global appears. A module
added to disk but not to `ORDER` is never served; a module in `ORDER` that fails to export its
global fails the build loudly. Both are good — the trap is only for someone who adds a file and
forgets the list.

---

## Trap 7 — stale daemon serves stale code

The dashboard on :8890 is `python -m voicebridge run`, started **2026-10-12 19:38** (verified by
`/proc/2601736`). Python changes to handlers need a restart to take effect; the JS bundle is
read from disk per request, so JS/asset changes appear on reload without a restart. `scripts/
check_stale_daemons.py` reports 0 stale processes today, so the run is currently consistent —
but after editing a `voicebridge/*.py` file, restart before trusting the page.

---

## Trap 8 — the whole Office tree is untracked

`git status` shows `docs/game-office/`, `frontend/qubit-app/plugins/qubit-office/`, the office
tests, and the office scripts as **`??`**. There is **no commit history** for any of it
(`git log -- <paths>` is empty). Consequence: the merge has no rollback point until someone
commits. The first implementation action should be a checkpoint commit of the *existing*
working state, before any borrowed asset lands — so a bad merge is revertible.

---

## Trap 9 — the doc-consistency test does not cover the merge

`tests/test_office_doc_consistency.py` passes (16 tests) but checks only the agent-monitor slug
and the refuted collision claim. It does **not** catch the stale `README.md` counts (16 modules
vs 26), the `ROOM_W/ROOM_H` drift, or the visual-defect ledger's fixed-but-OPEN rows. The merge
adds a **new** consistency test (`tests/test_office_merge_doc_consistency.py`) covering: the
every asset the renderer loads is
listed in the vendoring manifest.
