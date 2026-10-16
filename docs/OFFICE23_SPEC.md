# office2 / office3 integration spec — verified facts

Parent-verified on 2026-10-16. Every line number below was read from the real
file in the main worktree. Trust these; do not re-derive them by searching
(an earlier agent did that, searched the wrong tree, got empty results for
strings that demonstrably exist, and then fabricated its report).

## 1. The five registration surfaces (adding ONE tab like /qubit-office)

| # | File | Line | What it is |
|---|------|------|------------|
| 1 | `voicebridge/webui.py` | 890 | `SPA_ROUTE_PATHS` tuple — the shell-route allowlist. `/qubit-office` is the last entry. New paths MUST be added here or the shell never renders. |
| 2 | `frontend/qubit-web/src/App.tsx` | 339 | route map entry: `"/qubit-office": () => <PluginPage name="qubit-office" />,` |
| 3 | `frontend/qubit-web/src/App.tsx` | 477 | nav tab: `{ path: "/qubit-office", label: "Qubit Office", icon: Users, exactEnd: true },` |
| 4 | `frontend/qubit-web/src/lib/chat-routes.ts` | 57 | `FULL_HEIGHT_ROUTE_PATHS` — needed only for pages with a fixed footer/composer |
| 5 | `frontend/qubit-web/src/lib/resolve-page-title.ts` | 67 | `"/qubit-office": "Qubit Office",` — document title |

Plugin manifest lives at
`frontend/qubit-app/plugins/<name>/manifest.json`.

## 2. Serving — ALREADY GENERIC, needs no code change

`voicebridge/webui.py`:
- line ~60: `OFFICE_APP_PREFIX = "/plugin-apps/"`
- line ~3064: `if path.startswith("/plugin-apps/"): self._serve_plugin_app(...)`
- `_serve_plugin_app` (line ~2029) takes **`parts[0]` as the plugin directory
  name** under `resources.resource_path("frontend/qubit-app/plugins")`, applies
  an extension allowlist, confines the resolved path inside the plugin dir, and
  for `.html` calls `_serve_app_html` (line ~2129).

=> A new dir `frontend/qubit-app/plugins/qubit-office2/` is served at
`/plugin-apps/qubit-office2/` with **zero webui.py changes**. Verify by reading
those lines; do not "make it generic" — it already is.

Token injection (`_serve_app_html` / `_peer_is_loopback`, line ~2114) is
**loopback-only**. A page served over the LAN gets HTML but no token. This is
deliberate and documented (a measured token leak). Do not weaken it.

Referer-based auth (~1597, ~1692) keys on `OFFICE_APP_PREFIX` being present in
the referer — generic `"/plugin-apps/"`, so new pages inherit it automatically.

## 3. `fleet.json` is a DEAD REGISTRY — do not edit it

`frontend/qubit-web/public/plugins/fleet.json` and
`frontend/qubit-dist/plugins/fleet.json` both list `qubit-office`, but a
repo-wide grep finds **no reader** for them in `voicebridge/` or
`frontend/qubit-web/src/`. Copying its entry into a new page achieves nothing.
Leave it alone.

## 4. Upstream licences (verified from primary sources)

- **Agent-Quest** (`https://github.com/FulAppiOS/Agent-Quest`): MIT.
  LICENSE = "MIT License", "Copyright (c) 2026 Fulvio Schilone".
  Stack: Bun + TypeScript. Root: `bin/ client/ server/ scripts/ docs/`
  `package.json bun.lock install.sh`. **It is a server+client app, not a static
  Vite build.** Only `client/` is vendorable as a static bundle.
- **starnet** (`https://github.com/androoAGI/starnet`): MIT
  (GitHub API `spdx_id: MIT`, LICENSE file present, HTTP 200). An earlier agent
  claimed "no LICENSE file" — that was WRONG.
  `docs/VOICE_CHAT_PLAN.md` on branch `feat/harness-backend` is a DESIGN DOC.
  office3 is an **authored reimplementation**, not a vendor of upstream code.

## 5. Environment

- Repo: `/home/trick/Projects/Repos/qubit-voicebridge`
- Python: `.venv/bin/python -m pytest` (system python3 has NO pytest)
- Scratch/TMPDIR: `/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch`
- Live server: `http://127.0.0.1:8890`
- Git: COMMIT AS YOU GO. Never commit in the main worktree; use your own
  `git worktree add /tmp/<name> -b <branch>`.

## 6. What already failed (do not repeat)

Five agents ran in parallel; all five were killed at ~239s by the same
state-DB contention error. Four of them then reported `changed_files` that
**do not exist anywhere** — no disk, no git, no dangling commit. If you cannot
actually produce an artifact, say so; a truthful blocker beats an invented
success.
