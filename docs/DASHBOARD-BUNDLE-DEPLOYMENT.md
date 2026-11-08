# Dashboard bundle deployment — the package-shadowing trap

The web dashboard SPA is built to `frontend/qubit-dist/` and served by
`voicebridge`. When the bridge runs from an **installed wheel**, the bundle
actually served is the copy inside site-packages, **not** the checkout. Editing
the checkout and rebuilding is therefore invisible to that server. Everything
below is silent: the server keeps returning `200` with a complete, working
dashboard — just an **old** one.

Verified live on 2026-11-08 against `qubit-voicebridge.service` (:8890).

## Resolution order (the whole mechanism)

`voicebridge/resources.py:24-33` — `resource_path()` returns the **first
existing** candidate:

1. `<pkg>/voicebridge/_data/<name>` — `_PACKAGE_DATA`, i.e.
   `<venv>/lib/pythonX.Y/site-packages/voicebridge/_data/`  (`resources.py:18`)
2. only if that does not exist: `<repo>/<name>` — `_REPO_ROOT`
   (`resources.py:21`)

`voicebridge/webui.py:2408-2414` — `_qubit_dist()` is the single caller that
matters for the dashboard:

```python
def _qubit_dist(self) -> "Path":
    from . import resources as _resources
    return _resources.resource_path("frontend/qubit-dist")
```

Consumed by `_serve_qubit_index()` (`webui.py:2416`) and
`_serve_qubit_static()` (`webui.py:2565-2566`).

**Consequence:** package data wins unconditionally. A wheel install never
serves a checkout bundle, and a checkout build never reaches an installed
server. This ordering is *correct and must stay* — it is what makes a clean
isolated install serve a real dashboard with no checkout on disk
(`scripts/verify_installed_dashboard.sh` is the acceptance gate for exactly
that property). The trap is not the order; it is that the order is invisible.

## How to tell which bundle a server is serving

Ask the interpreter, from a **neutral cwd**:

```sh
cd / && /path/to/serving/python -c \
  "from voicebridge import resources; print(resources.resource_path('frontend/qubit-dist'))"
```

The neutral cwd matters: `python -` (and plain `-c`) puts the current directory
on `sys.path`, so probing from the repo root imports the **checkout**
`voicebridge` and reports the checkout — reproducing the illusion that your
rebuild is live. This bit the first version of the sync script.

## The fix: publish the rebuild

```sh
scripts/build_dashboard.sh                              # rebuild checkout bundle
scripts/sync_dashboard_to_install.sh --check            # report drift, change nothing
scripts/sync_dashboard_to_install.sh                    # publish into the install
QUBIT_PYTHON=<serving python> scripts/sync_dashboard_to_install.sh   # pick the venv
```

The script resolves the destination **by importing `voicebridge` from the
serving interpreter** (never by guessing a path shape), refuses to run when the
interpreter already serves the checkout (exit 3 — nothing shadows, nothing to
do), and verifies the copy byte-for-byte afterwards. Files added or removed by
a rebuild are handled (`rsync --delete`, or clear-then-copy without rsync), so
stale hashed assets cannot accumulate.

Typed exit codes: `0` in sync / copied · `2` checkout bundle missing · `3`
interpreter serves the checkout (no-op) · `4` copy failed · `5` drift detected
(`--check` only).

A running bridge picks up the published files on the **next request** (the dist
is read per-request, not cached at boot). Restart only when the change touched
Python routes.

## SPA fallback vs. a real route — do not confuse them

`webui.do_GET` (`webui.py:3964-3981`; the workbench branch is line 3980) tries
`_serve_workbench_ui()` **before** any SPA route. So:

- `/qubit-workbench` is the **ported upstream workbench** (`voicebridge/workbench_ui/`),
  served by `workbench_ui_routes.py` — *not* the React SPA.
- The React SPA (`frontend/qubit-dist`) serves `/chat` and every other
  canonical route, including via the catch-all SPA fallback at `webui.py:4817`.

Both declare a "Qubit Workbench"/workbench surface, which is why a blank-or-wrong
page here is easy to misdiagnose. Check `document.title` and whether
`.qubit-wb-action-grid` exists, not just the URL.

## Known, separate issue (not the trap)

`/qubit-workbench` emits 404s for `manifest.json` and `/api/profile/active`.
The ported page is served with `<base href="/qubit-workbench/">`
(`workbench_ui_routes.py:193`) but links `manifest.json` **relative**
(`workbench_ui/static/index.html`), while the file lives at
`static/manifest.json` — so the browser requests `/qubit-workbench/manifest.json`
(404) instead of `/qubit-workbench/static/manifest.json` (200). Cosmetic: the
workbench renders fully. Fix is either the link in `index.html` or routing
`manifest.json` at the mount root; it does not affect the dashboard bundle.
