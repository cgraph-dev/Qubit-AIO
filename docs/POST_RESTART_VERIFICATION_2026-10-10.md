# Post-Restart Verification + Health Baseline — 2026-10-10

Scope: verify the live system AFTER the repin restart (450 files, upstream 26610b09)
into `qubit-voicebridge.service` at **2026-10-10 01:16:05** (pid 1630938), end to end,
and record a baseline a future check can compare against.
Read-only except one throwaway test session (created then deleted). No service was
restarted or stopped; no process was killed.

Deployed revision: `1d3059c4d03154893c487eab1bb9c6ba510809c6`
("merge: repin wave 1 -- 450 files from upstream 26610b09").
Note: the branch tip later moved to `871c8ed5`; the *running* service is the `1d3059c4`
tree, which is what this verification exercised.

---

## 1. Post-restart PASS/FAIL table

| Check | Result | Raw evidence (method) |
|---|---|---|
| `qubit-voicebridge.service` active | PASS | `Active: active (running) since 01:16:06`; MainPID **1630938** (`systemctl --user status`) |
| `qubit-gateway.service` active | PASS | `active (running) since 2026-10-09 21:34:47`; MainPID **1044993** (not restarted by the repin) |
| `qubit-voice-typing.service` active | PASS | `active (running) since 2026-10-09 09:59:39`; MainPID **6153** (`voicebridge.desktop_helper`) |
| `qubit-voice-orb.service` | EXPECTED-INACTIVE | `inactive (dead)`, MainPID 0, `ConditionResult=yes`, last exit `status=0/SUCCESS`. The orb is the X11 Tk overlay; its log shows `The X11 connection broke (error 1). Did the X11 server die?` then a clean stop. With no live X session it starts, fails to attach, and exits 0. **Not a defect.** |
| `:8890` listening | PASS | `ss -ltn` → `LISTEN 0.0.0.0:8890` owned by `pid=1630938,fd=4` |
| Correct pid bound | PASS | `ps -p 1630938` → `python -m voicebridge run`, started `Sat Oct 10 01:16:05` |
| `/chat` HTTP 200 | PASS | `curl -o /dev/null -w %{http_code}` → `200` (1.7 ms); `/` 302→`/chat` 200 |
| SPA bundle loads | PASS | `/dashboard` serves shell; `index-CsxeFZeM.js` 200 97 228 B; `index-CLsKqGnO.css` 200 355 622 B; `react-vendor-BAKIVMI9.js` 200 227 026 B; `/favicon.ico` 200 |

**Auth note (method):** the SPA API requires header `X-Qubit-Session-Token` (from
`webui.py::_qubit_token_ok`). A bare `Authorization: Bearer` returns 401; the token is
`voice.yaml:token`. All API probes below used the correct header.

## 2. Real turn through /chat (fresh session)

- Endpoint: `POST /agent/api/qubit/chat` with `{"text","session"}` (SSE frame contract;
  dispatch at `webui.py:1872`, backend `voicebridge.agent.run_session`).
- Session id used: `verify_y6x_1791584657` (never touched the owner's TUI sessions).
- SSE frame returned (single JSON frame, no chunking):
  `{"ok": true, "reply": "PONG-VERIFY", "text": "PONG-VERIFY", "session": "...",
  "route": "agent", "elapsed_ms": 4478, "receipt": {"runtime": "qubit-vendored-agent",
  "path": "voicebridge.agent.run_session", "provider": "opencode-go",
  "model": "deepseek-v4.1-flash"}}`
- Reply exact match: **PASS** (`PONG-VERIFY`).
- Persistence: `GET /agent/api/sessions` listed the new row
  (`source: dashboard`, `message_count: 2`) — **PASS**.
- Cleanup: `DELETE /agent/api/sessions/<id>` `{"confirm":true}` → `{"ok":true,...,
  "archived":true}`; re-list shows the id **gone** — **PASS**.

## 3. Full page sweep (post-restart, real data)

Page shells all serve the 1312 B SPA index (client-routed); the page *data* comes from
these API endpoints (all with the session-token header):

| Page | API endpoint | Result |
|---|---|---|
| /chat | `/agent/api/status` (status payload) | 200 68 634 B |
| /cron | `/agent/api/cron/jobs` | 200 3 141 B (job `auto-push repos`, `30 23 * * *`, completed 19) |
| /sessions | `/agent/api/sessions` | 200 39 217 B (50 sessions) |
| /logs | `/agent/api/logs` | 200 17 452 B |
| /models | `/agent/api/model/options` | 200 14 235 B |
| /skills | `/agent/api/skills` | 200 59 578 B |
| /plugins | `/agent/api/plugins/kanban/events` | 200 2 007 B |
| /kanban | `/agent/api/plugins/kanban/events` | 200 2 007 B |
| /devices | `/agent/api/device/status` | 200 253 B (enrolled=false; browser_cdp MISSING) |

All nine pages mount and fetch real data — **PASS**. Note: bare `/agent/api/<page>` (e.g.
`/plugins`, `/models`) 404s; the real endpoints are the dotted paths above.

## 4. Journal classification (`--since '30 min ago'`, both units)

236 lines total. Classification of every warning/error:

- **NOISE / expected (bulk):** 74 `Found left-over process … Ignoring` + 74 matching
  `remains running after unit stopped` lines — the restart's own control-group backlog.
  See §5.
- **ENVIRONMENTAL — SQLite WAL advisory (REAL, known):** fired once at 01:16:13 for
  **`runtime/state.db`** (the 366 MB live DB the process holds open via fds 17/61/62):
  *"linked SQLite 3.45.1 … is vulnerable to the WAL-reset corruption bug
  (https://sqlite.org/wal.html#walresetbug) — is already in WAL mode — leaving WAL in place
  (no live downgrade under concurrent openers). Upgrade to SQLite 3.51.3+ (or backports
  3.50.7 / 3.44.6); Qubit-managed installs can repair … `qubit update`. See `qubit doctor`.
  Fires once per process per database."*
  **Recommended fix:** upgrade the SQLite linked into the venv to 3.51.3+ (or 3.50.7 /
  3.44.6 backport). Qubit-managed installs: `qubit update` / `qubit doctor`. Do **not**
  downgrade WAL live. **No upgrade performed** (out of scope). This is the known advisory,
  not new corruption.
- **ENVIRONMENTAL — hermes-herald config missing:** `hermes-herald: cannot load
  /home/trick/.hermes/config.yaml: [Errno 2] No such file or directory`. A stale
  upstream-path lookup for a config that does not exist in a Qubit install. Non-fatal
  (plugin skipped). Real-but-benign.
- **No tracebacks, no exceptions, no retry/timeout/refused errors** in the window.

**Real defects found: none** in the restart window. Two known-environmental advisories
(WAL, hermes-herald path) and left-over-process noise only.

## 5. Left-over process inventory (recommendation only — none killed)

- The task cited 4 pids (1630587, 1630652, 1630653, 1630669). Verified: these exact pids
  appear in the journal (2 lines each — one `remains running`, one `Found left-over …
  Ignoring`), as children of the *previous* service incarnation.
- **All four are DEAD now** (`kill -0` → not alive; `ps` → no row). They are a subset of a
  much larger backlog systemd reported: **74 distinct leftover pids** (484370 → 1630669),
  a mix of `python`, `node`, `node-MainThread`, `sh`, and `npm exec @playwright/mcp`
  processes accumulated across earlier restart/test cycles.
- **None of the 74 are alive now** — all reaped during/after the restart.
- Distinct from these: the playwright MCP procs currently alive (1631125, 1631503, …) are
  children of the *current* service (1630938, same cgroup) — legitimate, not leftovers.
- **Recommendation: nothing to kill.** The 4 cited pids (and the full 74) are already
  gone; the "left-over process" log lines are historical. Optional hygiene for the future:
  enable `KillMode=mixed` with a post-stop sweep so a hung cgroup child cannot linger into
  the next start — but no action is needed now.

## 6. Health baseline (compare future checks against these)

| Metric | Value |
|---|---|
| voicebridge MainPID / start | **1630938** / `2026-10-10 01:16:06 EEST` |
| gateway MainPID / start | **1044993** / `2026-10-09 21:34:47 EEST` |
| typing MainPID / start | **6153** / `2026-10-09 09:59:39 EEST` |
| orb | inactive (dead), exit 0 — expected (no X11 session) |
| `:8890` | LISTEN, pid 1630938 |
| bundle id | **`index-CsxeFZeM.js`** (css `index-CLsKqGnO.css`) |
| gate verdict (`scripts/gate_all.py` @1d3059c4) | **PASS** — import 29/0, entry-point HARD 0, lazy 0 unguarded, shadowed 19 known/0 new; capability 1728 missing / 385 files (informational) |
| live state.db (mutable) | `runtime/state.db` ≈ **366 MB**, `-wal` ≈ 14 MB; sha256 changes continuously under live WAL writes — use size+WAL, not hash |
| dormant state.db | `state.db` 274 432 B, sha256 `808e7831…` (last written 2026-10-07) |
| cron store | `runtime/cron/jobs.json` sha256 `dd80ec98…`; `executions.db` 57 344 B |
| live sessions | 50 total, **1 active** (owner TUI `20261006_061407_690af0`), 0 archived |
| SQLite linked in venv | 3.45.1 (→ WAL advisory above) |

Method notes: every number above was read live (`systemctl --user show`, `ss -ltn`,
`sha256sum`, `stat`, `curl` with the session-token header) at ~01:25–01:31 EEST
2026-10-10. The running service tree is `1d3059c4`; the branch tip has since advanced,
so a future check must confirm the *live pid's* tree, not the branch tip.
