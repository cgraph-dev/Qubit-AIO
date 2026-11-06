# Workbench backend seam — upstream provenance

**Upstream:** `github.com/nesquena/hermes-webui`
**Pin:** `3d9488fca6bb248c77f988bfbffcca9d8cfd9561`
(`Merge pull request #8871 from nesquena/stage/1105-8866`)
**License:** MIT — **Copyright (c) 2025 Hermes Web UI Contributors**
(Licence text below. Preserved as attribution because this package absorbs the
upstream *presentation contract*; no upstream source file is copied.)
**Surveyed read-only at:**
`~/.local/state/qubit-voicebridge/runtime/cache/scratch/repo-survey/hermes-webui/`
**Local tree:** branch `wb/workbench-hydrate`, forked from `main@cbfca908`.
**Scope evidence:** `docs/CHAT_REPLACE_SCOPE.md` (the disqualifier audit this
seam implements against).

---

## The licence, in full

```
MIT License

Copyright (c) 2025 Hermes Web UI Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Per-capability record

`voicebridge/workbench/` contains four shipped modules. Every line that touches
data is **Qubit-authored**; the upstream half is always the *wire shape* and
*navigation rule*, re-implemented against Qubit's own stores. Where an upstream
rule was adopted the upstream `file:line` is cited in the code comment that
implements it.

### `adapters_sessions.py`

| Capability | Upstream origin | Disposition |
|---|---|---|
| Session list (paged, searchable) | `static/index.html:171-188` (session rail), `sessions.js` (client only) | **Qubit-authored.** Reads `QubitSessionStore.list()`. Upstream has no server-side session-list contract separable from its own `state.db`; its client model was not ported. |
| Transcript read | upstream rendered its own transcript from its own store | **Qubit-authored.** `QubitSessionStore.messages()`. No upstream transcript reader is reused. |
| Timeline (recent replies + activity events) | upstream `replay` / rewind read model over a foreign schema | **Qubit-authored.** Turns from the canonical transcript; events from `voicebridge.session_activity`. Upstream replay is **not** ported (see refusals). |
| Send a turn | upstream `api/streaming.py` (a second agent loop) | **Qubit-authored delegation.** Calls `voicebridge.chat_transport.turn` — the function `/qubit-chat`, the voice bridge and the CLI already run. Nothing from `api/streaming.py` was read into this tree. |

### `adapters_workspace.py`

| Capability | Upstream origin | Disposition |
|---|---|---|
| Directory listing shape | `api/workspace.py:1632-1841` (`list_dir`) | **Rule absorbed, code not.** Sort rank (symlink → dir → file; upstream `workspace_sort_rank`), 200-entry cap, `mtime_ns` serialized as a string (upstream `serialize_workspace_entries_for_browser`), broken/looping symlinks filtered (upstream `:1676`). |
| Escaping-symlink disclosure rule | `api/workspace.py:1730-1745` (#4581 hardening) | **Absorbed as a security rule.** An escaping symlink is listed with `target_outside_workspace: true`, no resolved path, no target-derived size/dir. |
| File preview | `api/workspace.py:1866-1889` (`read_file_content`) | **Shape absorbed, bytes from Qubit.** Upstream's body is `{path, content, size, lines}`; this returns the equivalent plus `clipped`, `trust`, and the files layer's own refusal codes. |
| Confinement | upstream `safe_resolve_ws` / `open_anchored_fd` (anchored-fd walk) | **NOT ported.** Qubit confinement is `voicebridge.files` (approved project roots). See refusals below. |

### `workbench_routes.py`

| Capability | Upstream origin | Disposition |
|---|---|---|
| The six REST routes | upstream `api/routes.py::_handle_list_dir` (`:23502`) and the `if parsed.path == …` dispatch idiom | **Qubit-authored.** Route table shape copied from Qubit's own `voicebridge/hud_routes.py` (the same `(method, pattern, handler)` triple `_r()` takes). Lazy adapter resolution so a missing package is a 503, never a boot failure. |

### `__init__.py`

Package surface: docstring, upstream constants (`UPSTREAM_REPO`, `UPSTREAM_PIN`,
`UPSTREAM_LICENSE`, `UPSTREAM_COPYRIGHT`) and `SESSION_AUTHORITY`. No upstream
text; the docstring names what is refused and why.

---

## What is deliberately NOT ported

Each of these is a disqualifier recorded in `docs/CHAT_REPLACE_SCOPE.md` §(d)
with its own `file:line`; the citations are repeated in the package docstring.

### 1. The upstream server and its `api/` package (data corruption risk)

- `api/streaming.py:1413` → `api/agent_runtime.py:439-451` eagerly imports
  `run_agent.AIAgent`; 10 call sites resolve that class.
- `api/routes.py:35146` opens `hermes_home / "state.db"` with a bare
  `sqlite3.connect` — **read-write**.
- Qubit has exactly ONE chat-transcript writer:
  `CHAT_SESSION_AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"`
  (`voicebridge/qubit_compat.py`), with a second-writer refusal implemented
  (`voicebridge/qubit_session_store.py:61-93`, `:219-226`).
- **Consequence:** nothing in this package imports `run_agent` or `sqlite3`,
  and nothing opens a database. Verified by AST gate
  (`voicebridge/workbench/_probe_ast.py`): imports across all four shipped
  modules are `__future__`, `time`, `typing`, `os`, `pathlib`, `stat`,
  `importlib`, `qubit_session_store`.

### 2. The `hermes` binary shell-out

- `api/gateway_restart.py:27-35`, `:118-124` resolves and spawns
  `hermes gateway restart`; `api/startup.py:28-45` and `bootstrap.py:114-115`
  resolve an agent root from the `hermes` console-script shebang.
- **Consequence:** no module here imports `subprocess` or `shutil` and none
  spawns a process. Same AST gate.

### 3. The upstream streaming engine

`api/streaming.py` carries 438 `def`s — a whole second agent loop with its own
turn persistence. Qubit already streams through `voicebridge.chat_transport`
(`/api/qubit/chat/stream`); `send_turn` delegates to the non-streaming
`chat_transport.turn` and a streaming frontend variant should call
`chat_transport.stream_turn`. A second loop would be a second source of truth
for one turn.

### 4. The workspace **write** surface

`api/workspace.py::open_anchored_create_fd`, `make_anchored_dir`,
`open_anchored_write_fd`, `unlink_anchored`, `rmtree_anchored`,
`rename_anchored`, and the `/api/workspace/upload` route
(`api/routes.py:19164`). In Qubit every mutation is a brokered, confirmed,
audited action (`voicebridge/file_patch.py` through `voicebridge/broker.py`),
not a viewer feature. This seam is read-only; `refused_mutation()` exists so a
frontend stub cannot mistake "no handler" for "allowed".

### 5. The workspace path-escape **grant** model

`api/routes.py:23623+` (`_handle_escape_list_dir`, `authorize_escape_target`,
the escape-token store in `api/workspace.py:1902-1949`). This is a security
surface Qubit did not design: it widens the readable roots beyond the folders
the owner registered, with a token the workbench would have to mint. Qubit
confinement stays `voicebridge.files` (registered project folders only) and
there is no grant path at all.

### 6. The upstream per-session client-state model

`static/ui.js` / `sessions.js` / `panels.js` carry 84 + 72
`localStorage`/`sessionStorage` touchpoints — a foreign client-state model that
fights Qubit's `ProfileKeyedRoutes` remount contract (`App.tsx:1294-1310`). The
frontend lane owns its own state; nothing of that model is specified here.

### 7. Upstream replay / rewind over its own schema

The HUD lane already recorded the precedent (`voicebridge/hud/__init__.py`,
`SKIP: replay`): upstream replayed `state.db` rows through a schema it was
verified against. Qubit's schema genuinely differs and the authority exposes no
replay read model, so the workbench's `read_timeline` reports what genuinely
happened (recent replies + activity events) rather than fabricating a replay.

---

## Qubit-authored contract summary

| Rule | Source |
|---|---|
| One session store, no second opener | `voicebridge/qubit_session_store.py` (`QubitSessionStore`, `CHAT_SESSION_AUTHORITY`) |
| One send path | `voicebridge/chat_transport.py::turn` |
| File confinement | `voicebridge/files.py` (`approved_roots`, `_resolve_inside`) |
| Redaction + byte caps on preview | `voicebridge/files.py::read_file` |
| Typed refusals, never empty data | this package's `_unavailable` / `_refusal` helpers |
| Route registration idiom | `voicebridge/hud_routes.py` |
