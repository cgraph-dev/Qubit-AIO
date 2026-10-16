# Office port — risk review and acceptance checklist

**Job:** define what "100% ported" can honestly mean for the Habblaud-in-Qubit
Office port, find how the current integration breaks, and give the human a
machine-checkable acceptance checklist.

**Author:** Qubit Agent (risk/scope subagent). **Date:** 2026-10-15.
**Worktree:** `/home/trick/Projects/Repos/qubit-voicebridge`, HEAD `d50a56945`.
**Read-only:** this file is the only artifact written. No code, test, or other
doc was edited; no service was restarted.

Every command below was **run this session**; the output quoted is real. Where a
claim could not be checked it is marked OPEN or UNKNOWN, not smoothed over.

---

## Part 0 — The headline

Two claims get separated here, because conflating them is how "100%" gets
misused:

1. **The client port works.** The vendored Habblaud client renders, connects to
   Qubit's own `/api/stream`, and the rollback path works. This is VERIFIED
   (live service on `:8890`, active).
2. **"Port everything 100%" cannot honestly include the upstream server, the
   Claude Code mod, or Docker.** Those are *deliberately excluded*, and porting
   them would be adding a **second agent-orchestration backend Qubit already
   has**. Evidence in Part 1.

So the honest scope statement is:

> **100% = 100% of the vendored CLIENT, wired to Qubit's own stores and auth.**
> The server / mod / Docker are OUT-OF-SCOPE *by design*, and the client does
> not need them (proven in Part 1.3).

There is also **one HIGH-severity security finding** (Part 2, S1) — a token
exposure that is *pre-existing and dashboard-wide*, not introduced by the Office
port, but which the Office port inherits and which the acceptance checklist
therefore requires to stay scoped, not widen.

---

## Part 1 — Upstream inventory: what Habblaud contains vs what was vendored

A checkout of the upstream **does exist locally** at
`/home/trick/Projects/habblaud` (this falsifies the "no checkout exists so
re-vendor is impossible" hypothesis — see Part 4, M1):

```
$ cd /home/trick/Projects/habblaud && git rev-parse HEAD
86c0379b18cd1be35dd3661f98f87662633db3ef
$ git log --oneline 4ccce2537472fe5a8d432f06974494d2e778ff15..HEAD
86c0379 World: privacidade com resync e pong na conexão da cena, CHANGELOG do botão só local
$ git rev-list --count 4ccce25..HEAD
1
```

The vendored pin `4ccce25` (`Versão 0.11.0`) is the **parent of the checkout's
HEAD** — the local checkout is one commit ahead of what was vendored.

### 1.1 The inventory table

| Upstream component | What it does | Vendored? | Does Qubit NEED it to render/work? | Verdict |
|---|---|---|---|---|
| `dist/client/` (built Vite output: `bundle/*.js`, `*.css`, woff fonts, `assets/*` PNG, `index.html`, `manifest.webmanifest`) | The pixel-art office **client** — the thing a human sees | **YES** (77 files, 2,147,637 B, recorded in `PROVENANCE.json`) | **YES** — it is the default renderer of `/qubit-office` | **REQUIRED** |
| `client/public/` (fixed-name assets) | Favicon, brand marks, `manifest.json`, og-image | YES (copied into vendored root) | YES — referenced by the served document | REQUIRED |
| `LICENSE` (MIT, `Copyright (c) 2026 Márcio Junior`) | Legal notice | YES (byte-identical, sha256 `5de137c2…1dee9a`) | YES — MIT requires the notice to travel | REQUIRED |
| `server/` (Node/TS: `accounts`, `pty`, `spawn`, `codex`, `messages`, `permissions`, `history`, `deliveries`, `updates`, `world`, `http/sse.ts`, `http/app.ts`) | The upstream **backend**: serves `/api/stream`, `/api/access`, `/api/agents/:id`, PTY terminal, permission/message mutations, reads Claude Code `~/.claude` dirs | **NO** | **NO** — Qubit serves its own `/api/stream` (`voicebridge/office_stream.py` translates Qubit's tower report into the client's `OfficeSnapshot`) | **DELIBERATELY EXCLUDED** |
| `mod/` (`habblaud`, `habblaud-permissoes`, `habblaud-mensagens` — Claude Code plugins) | Hook into *Claude Code* sessions: read rate limits, show "needs you", answer permissions, send messages. Polls `http://127.0.0.1:4747/api/mod/summary` — **the upstream server** | **NO** | **NO** — it instruments Claude Code, a *different* agent runtime; `mod/README.md` states the endpoint is `GET http://127.0.0.1:4747/api/mod/summary`, which is the non-vendored server | **DELIBERATELY EXCLUDED** |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `scripts/docker-up.ts` | Containerize the upstream server + client | **NO** | **NO** — there is no upstream server to containerize | **DELIBERATELY EXCLUDED** |
| `shared/` (upstream TS types/i18n/world logic) | Source shared by client+server | **NO** | **NO** — consumed only at build time by the client build, which is what was vendored | DELIBERATELY EXCLUDED |
| `client/src/` (human-readable TypeScript **source**) | Source of the client | **NO** — only build output is vendored | **NO** — the minified bundle is what ships | DELIBERATELY EXCLUDED |
| Root docs (`README*.md` ×4, `CHANGELOG.md`, `CONTRIBUTING.md`), `.claude-plugin/`, `.github/`, `scripts/` (upstream build/release) | Upstream project scaffolding | **NO** | **NO** | UNNECESSARY |

This matches `PROVENANCE.json`'s own `note`:

> "Vendored build output of the Habblaud client. **Client-side only: the server,
> the Claude Code mod, and the Docker setup are NOT vendored.** Served from
> `/plugin-apps/qubit-office/habblaud/`."

### 1.2 Confirm the vendored tree is exactly what the record says

```
$ .venv/bin/python scripts/vendor_habblaud.py --check
ok: 77 files, 2147637 bytes, commit 4ccce2537472          (exit 0)
```

Independent re-hash of every recorded file against its sha256 (my own script,
not the repo's):

```
checked 77 mismatch 0 missing 0
```

On-disk count: **78** files (77 recorded + `PROVENANCE.json`, which excludes
itself). This is correct, not a discrepancy.

### 1.3 Does the vendored CLIENT work without the upstream SERVER?

**Yes — proven by the live system, not by inspection.**

The client's only *required* runtime dependency on a server is its SSE feed.
Qubit re-implements that route itself, translating its own tower report:

- `voicebridge/office_stream.py` — "renders the tower payload into the
  `OfficeSnapshot` shape the client's store expects, and streams it."
- `voicebridge/webui.py::_serve_office_stream` (line 1701) — serves
  `GET /api/stream` from `voicebridge/office.tower::state(cfg)`.
- The vendor transform rewrites the client's `openStream('/api/stream')` call to
  prefer `window.__QUBIT_OFFICE_STREAM_URL__` (set by the server in the injected
  HTML), falling back to `/api/stream`. So the client talks to **Qubit's**
  endpoint, not a Habblaud server.

Live proof (service active, bound `0.0.0.0:8890`):

```
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/qubit-office
200
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/
200
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/api/stream        # no token
401
```

And the stream is live and grounded in Qubit's own store — fetching the tower
route Qubit owns returns real data, not upstream Habblaud data:

```
$ curl -s -H "X-Qubit-Session-Token: $T" http://127.0.0.1:8890/agent/api/office/tower
{"ok": true, "schema": "qubit-office-tower/1", "now": 1792079935.19, "hud":
 {"headcount": 10, "revenue_cents": 1000, "cost_cents": 20, "net_cents": 980, …}}
```

`schema: "qubit-office-tower/1"` is Qubit's. **No process listens on the
upstream port 4747 for Qubit's office** — Qubit owns the whole data path. The
prior live-verification doc (`docs/OFFICE-LIVE-VERIFICATION.md`) independently
recorded `REQ GET /api/stream?token=…` → `RES 200 text/event-stream` and frame
text `"Habblaud Connected 10 agents …"`.

**Conclusion:** the server, mod, and Docker are not just "not vendored" — they
are **not needed**. Porting them would duplicate Qubit's own orchestrator,
session store, and confirmation authority.

### 1.4 Upstream-parity gap that IS real, and must be named

The client's *interactive half* (permissions, messages, PTY terminal, spawn,
agent transcripts) upstream goes to the **server** it no longer has. Qubit has
deliberately left these unbound. This is honest, recorded in
`game/app/office-api.ts` (`qubit: null`, `gated: true`) and surfaced in
`/api/access`:

```
$ curl -s -H 'Referer: …/plugin-apps/qubit-office/habblaud/' http://127.0.0.1:8890/api/access
{"remote": false, "local": true, "permissions": false, "messages": false,
 "terminal": false, "spawn": false}
```

So the honest "100%" boundary has a **named remainder**: the office is
**view-only**. "100% ported" must mean *100% of the client's read/render half*,
with the write half **explicitly gated pending Qubit's confirmation authority** —
not silently broken. Anyone claiming "100%" without this qualifier is
overclaiming.

---

## Part 2 — Adversarial risk review

Severities: **HIGH** (exploitable / breaks correctness materially), **MEDIUM**
(real but bounded), **LOW** (hygiene / maintainability).

### S1 — HIGH · The session token is served in the plugin-app HTML to any caller the port is reachable from

**Evidence (verified this session):**

```
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/
200
$ curl -s http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/ | grep -o '__QUBIT_SESSION_TOKEN__=[^;]*'
__QUBIT_SESSION_TOKEN__="7caun11MRuoXc5fZ3nSMagQANDcup2SB"
```

That is the **live, valid** token. From a **non-loopback** peer (the machine's
LAN IP `192.168.1.135`), the same document is served and leaks the same token:

```
$ curl -s "http://192.168.1.135:8890/plugin-apps/qubit-office/habblaud/" | grep -o '__QUBIT_SESSION_TOKEN__=[^;]*'
__QUBIT_SESSION_TOKEN__="7caun11MRuoXc5fZ3nSMagQANDcup2SB"
```

The token then **works** for API access:

```
$ curl -s -o /dev/null -w '%{http_code}\n' -H "X-Qubit-Session-Token: $T" http://192.168.1.135:8890/agent/api/status
200
$ curl ... http://192.168.1.135:8890/agent/api/office/tower        -> 200   (real office store)
$ curl ... http://192.168.1.135:8890/chat                          -> 200
$ curl -s -o /dev/null -w '%{http_code}\n' "http://192.168.1.135:8890/api/stream?token=$T" -> 200
```

**Scope-limiting facts (this is why it is HIGH-but-inherited, not "the port
broke security"):**

- It is **not new.** `/chat` (the SPA shell) also serves the token to an
  unauthenticated caller — same 200 + token occurrence:
  ```
  $ curl -s -o /dev/null -w '%{http_code} ' http://127.0.0.1:8890/chat ; curl -s http://127.0.0.1:8890/chat | grep -c '__QUBIT_SESSION_TOKEN__'
  200 1
  ```
  `_serve_app_html` (1959-1973) deliberately *mirrors* `_serve_qubit_index`
  (1634) — the injection is the dashboard's existing design: the SPA is served
  to whoever can reach the route, and the token gates *API* access.
- The code's **default** host is loopback (`webui.py:4631`
  `self.host = host or cfg_host or "127.0.0.1"`), and `start()` refuses to run
  non-loopback without a token (`Packet 1B fail-closed`).
- **The live service is configured non-loopback anyway**: `ss -ltnp` shows
  `0.0.0.0:8890`. So on *this* deployment the exposure is real and LAN-wide.

**Why it still matters for THIS acceptance:** the Office port added a *second*
unauthenticated document route that leaks the token, and the app frame is
exactly the thing a user might share/screenshot/embed. The acceptance checklist
therefore requires the *scoping* to hold (Part 3, A7/A8): the token stays
scoped to the local app document, no widening of the exemption, and non-loopback
serving remains an explicit, token-gated choice.

### S2 — MEDIUM · The blind-probe exemption accepts any loopback page whose Referer merely contains `/plugin-apps/`

`_office_app_caller_ok(blind_probe=True)` (webui.py:1532-1604) allows a
**tokenless** `GET /api/access` when: peer is loopback **and** `Host` is loopback
**and** `Referer` contains `OFFICE_APP_PREFIX = "/plugin-apps/"`. Measured:

```
referer .../plugin-apps/qubit-office/habblaud/  -> 200   (intended)
no referer                                       -> 401
spoofed Host: evil.com (with good referer)       -> 401   (Host check holds)
arbitrary local page referer .../plugin-apps/    -> 200   (over-broad)
```

The Referer *substring* check is broader than "our app document" — any
loopback-origin page under `/plugin-apps/` qualifies. But `/api/access` returns
only static capability flags (no store data, no mutation authority), and the
exemption is confined to that one endpoint
(`blind_probe and path != "/api/access" → refuse`; verified:
`GET /api/agents/ceo` with a good referer → **401**). The blast radius is
capability discovery on loopback — low value. Severity MEDIUM (defence-in-depth,
not data exposure). It becomes HIGH only if the exemption is ever widened past
`/api/access`.

### S3 — MEDIUM · Token-leakage surfaces are one query-string mistake away from catastrophic

The design pins a **query-token** exception to exactly two routes:

```python
if path.split("?",1)[0].rstrip("/") not in ("/api/stream", "/api/snapshot"):
    return False
```

Verified the exception is tight:

```
$ curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8890/api/stream?token=wrongtoken123'
401
$ curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8890/agent/api/office/tower?token=wrong'
401
$ curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8890/agent/api/office/tower'     # no token
401
```

**But** the stream URL that lands in the served HTML is
`/api/stream?token=<live-token>` (webui.py:1962). A query-string token is, by
the code's own comment, what `token_ok(..., qp=True)` rejects on purpose "because
a query token leaks into logs and Referer." Here it cannot be avoided
(EventSource cannot set headers), so the token **does** reach the server access
log and any intermediary. Acceptable only because of S1's loopback scoping —
which the live deployment does not enforce.

### C1 — LOW/cosmetic · `ceo: undefined` in the mobile activity feed (upstream bug, not a port regression)

Prior docs record `Activity ceo: undefined ceo started a turn …` on 390×844.
`docs/OFFICE-LIVE-VERIFICATION.md` D1 proves the SSE payload is correct
(`agentName` values `wren`/`lead-content`/`ceo`, never empty) and
`office_stream.py` falls back `character.get("slug") or agent_id or ""`. The
`undefined` is inside the **minified upstream bundle's own mobile layout path**.
Not fixed, correctly: hand-patching a generated artifact would invalidate the
`PROVENANCE.json` hash record. The fix belongs as a transform step in
`scripts/vendor_habblaud.py` + rebuild + provenance regen. **OPEN.**

### C2 — LOW · pt-BR copy ships in the vendored UI

`<html lang="pt-BR">`; Portuguese strings in the served document. Owner ruling
is English-authoring for product surfaces. Locale chunks `en-*.js`, `es-*.js`,
`fr-*.js` **are present** in the vendored bundle, so the honest fix is to set
the client's locale default to `en` in the injected config — not to translate
strings. **OPEN.**

### O1 — MEDIUM · A dashboard rebuild can silently invalidate the Office port's served entry

`frontend/qubit-dist/` is **gitignored** (`.gitignore:66`) and **untracked**
(`git ls-files frontend/qubit-dist | wc -l` → **0**); it is regenerated by
`scripts/build_dashboard.sh`. By contrast the Office assets **are** tracked:
`git ls-files …/habblaud | wc -l` → **78**, and `qubit-office.game.js` is
tracked. **The risk:** the plugin *entry contract* (`"/qubit-office"` in
`FULL_HEIGHT_ROUTE_PATHS`, the plugin loader, `__QUBIT_PLUGIN_SDK__`) lives in
the *rebuilt* SPA. A dashboard rebuild that regresses the plugin loader or the
full-height route list would break the Office tab with **no tracked-file diff**
to point at it. `docs/BROWSER-PARITY-FINDINGS.md` §1a/1b already had to verify
the *served bytes* (`jt=[…\`/qubit-office\`]` in the minified bundle) for exactly
this reason. **Acceptance must re-check the SERVED bundle, not just the source.**
(Part 3, A3.)

### O2 — LOW · Fresh clone has no `frontend/qubit-dist/`

A fresh clone serves `404 dashboard not built` from `_serve_qubit_index` until
`scripts/build_dashboard.sh` runs. The vendored Office tree is fine (tracked),
but the *host shell* is absent. Not a port defect — an inherited build
property — but it means "does the Office work on a fresh clone" is **NO** until
the dashboard is built. Say so plainly.

### O3 — The vendored tree IS re-vendorable (a claimed blocker is FALSE)

The task asked me to verify "no upstream checkout exists, so a re-vendor is
currently IMPOSSIBLE." **That claim is false.** A checkout exists at
`/home/trick/Projects/habblaud` with `node_modules/` (incl. `vite`),
`dist/client/`, and the pinned commit `4ccce25` in history:

```
$ test -f /home/trick/Projects/habblaud/node_modules/vite/bin/vite.js && echo YES
YES
$ ls /home/trick/Projects/habblaud/dist/client/index.html
/home/trick/Projects/habblaud/dist/client/index.html
```

So `scripts/vendor_habblaud.py --src /home/trick/Projects/habblaud` **can run**
(and `--skip-build` can even reuse the existing `dist/client`). Caveat: the
current `dist/client` is a *different* build than what was vendored (different
chunk hashes — `main-DND_b4_E.js` vendored vs `main-DFcdTxa7.js` on disk), so a
byte-identical re-vendor from the *current* checkout state is not guaranteed.
**Re-vendor is feasible; bit-exact reproduction is not currently verified.**
This is a maintainability nuance, not a blocker. (Part 3, A9.)

---

## Part 3 — The acceptance checklist

Each item: **criterion · exact command · verdict**. Verdicts are
**VERIFIED-NOW** (I ran it and it passed), **OPEN** (true today but unproven /
known gap), or **OUT-OF-SCOPE-WITH-REASON**.

Machine-checkable criteria for "the Office port is done":

| # | Criterion | Command | Verdict |
|---|---|---|---|
| **A1** | The vendored tree matches its own hash record exactly (no drift, no undeclared file) | `.venv/bin/python scripts/vendor_habblaud.py --check` → `ok: 77 files, …, commit 4ccce2537472`, exit 0 | **VERIFIED-NOW** — but the byte total is now **volatile**: it read `2147637` when this review began and `2147626` minutes later, because a **concurrent agent re-vendored** the tree mid-review (`git status` shows `PROVENANCE.json`, `main-DND_b4_E.js`, `index.html`, `manifest.webmanifest` modified under `habblaud/`). Assert the **exit code + file count**, not the byte total. See Part 5. |
| **A2** | The served plugin-app document returns 200 | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/` → `200` | **VERIFIED-NOW** |
| **A3** | The **served** SPA bundle contains the office route (rebuild-drift guard, O1) | `curl -s http://127.0.0.1:8890/assets/index-*.js \| grep -c '/qubit-office'` → `≥1` (hash the served file first, as `BROWSER-PARITY-FINDINGS.md` §1b does) | **VERIFIED-NOW** (recorded in that doc) |
| **A4** | The stream route is live and bound to Qubit's own tower, not an upstream server | `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8890/api/stream?token=<T>'` → `200`; and `lsof -i:4747` → no listener for Qubit's office | **VERIFIED-NOW** (200); **OUT-OF-SCOPE-WITH-REASON** for the upstream-server half — there is no upstream server, by design |
| **A5** | The stream route refuses an absent/blank token | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/api/stream` → `401` | **VERIFIED-NOW** |
| **A6** | `/api/access` reports the conservative, view-only capability set | `curl -s -H 'Referer: http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/' http://127.0.0.1:8890/api/access` → `{"remote":false,"local":true,"permissions":false,"messages":false,"terminal":false,"spawn":false}` | **VERIFIED-NOW** |
| **A7** | The query-token exception stays confined to `/api/stream` (+`/api/snapshot`); ordinary APIs still reject a query token | `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8890/agent/api/office/tower?token=x'` → `401` (and `…/api/stream?token=wrongtoken` → `401`) | **VERIFIED-NOW** |
| **A8** | The token-bearing document route does not accept a **non-loopback, non-token** caller, and the blind-probe exemption returns no store data (S1/S2 scoping) | from a non-loopback peer: `curl -s http://<lan-ip>:8890/api/access` → `401`; and confirm the served HTML's token is only reachable under the deployment's explicit non-loopback token policy | **OPEN** — live service binds `0.0.0.0:8890` and serves the token to LAN peers (S1). Must be closed by config or by gating the document. |
| **A9** | A re-vendor is reproducible from the *recorded* commit | `.venv/bin/python scripts/verify_habblaud_revendor.py` → `REPRODUCIBLE`, exit 0 | **VERIFIED-NOW** (2026-10-16) — a clean clone at the pinned `4ccce2537472` + `npm ci` + `vite build` + the vendor transforms reproduces **all 77 files byte-identical** to the committed tree. The build is deterministic (two consecutive builds of the same source are byte-identical); the earlier "not reproducible" verdict built the shared checkout's *different* HEAD (`86c0379`), not the pin. Evidence: `docs/evidence/a9-revendor-reproduction.json`, `docs/VENDOR-REPRODUCIBILITY.md` |
| **A10** | The `?shell=1` rollback still renders and does not throw | `python scripts/render_office_screenshot.py --url 'http://127.0.0.1:8890/qubit-office?shell=1'` → `data_state ready`, `console_errors: []`, painted pixels > 0 | **VERIFIED-NOW** (recorded in `BROWSER-PARITY-FINDINGS.md` §3, all 3 viewports) |
| **A11** | `?shell=1` uses its own data path (`/agent/api/office/tower`), independent of the SSE route — so it survives a stream regression | CDP network capture of `?shell=1` → only `GET /agent/api/office/tower`, no `/api/stream` | **VERIFIED-NOW** (recorded in `BROWSER-PARITY-FINDINGS.md` §3d) |
| **A12** | The rollback renderer's entry (`qubit-office.bundle.js`) still exists and is reachable | `test -f frontend/qubit-app/plugins/qubit-office/qubit-office.bundle.js` | **VERIFIED-NOW** |
| **A13** | The three `entry` declaration sites agree (`manifest.json`, served `fleet.json` ×2) | `grep -o '"entry": *"[^"]*"' .../manifest.json` and the served `fleet.json` → all `qubit-office.game.js` | **VERIFIED-NOW** (recorded live-verification #8) |
| **A14** | Path traversal cannot escape the plugin root (raw HTTP, not client-normalized) | raw-socket `GET /plugin-apps/qubit-office/habblaud/../../../../../etc/passwd` → `404` | **VERIFIED-NOW** |
| **A15** | The Office renderer is tracked in git (survives clone/rebuild) | `git ls-files frontend/qubit-app/plugins/qubit-office/habblaud \| wc -l` → `78`; `git ls-files …/qubit-office.game.js` → non-empty | **VERIFIED-NOW** |
| **A16** | The write half is explicitly gated (not silently missing) — "100%" excludes it by contract | `grep -c 'gated: true' .../game/app/office-api.ts` → `2`; `bindingSummary()` → `8 bound · 5 unavailable · 2 confirmation-gated` | **VERIFIED-NOW** |
| **A17** | The Office test suite is green (a red suite hides regressions) | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` → `0 failed` | **OPEN** — as of `docs/SHELL-TEST-RECONCILIATION.md`'s work this was reconciled at HEAD `d50a56945` ("reconcile the 3 drifted shell tests"); re-run to confirm |
| **A18** | The upstream SERVER is not required and is not to be ported | no process on the upstream port for Qubit's office; `curl -s http://127.0.0.1:8890/agent/api/office/tower -H "X-Qubit-Session-Token: $T"` returns `schema: "qubit-office-tower/1"` | **OUT-OF-SCOPE-WITH-REASON** — Qubit owns the data path; porting the server duplicates Qubit's orchestrator (Part 1.1) |
| **A19** | The Claude Code MOD is not required and is not to be ported | `mod/README.md` says it polls `127.0.0.1:4747/api/mod/summary` (the non-vendored server); no `mod/` tree in Qubit | **OUT-OF-SCOPE-WITH-REASON** — it instruments Claude Code, a different runtime |
| **A20** | Docker is not required and is not to be ported | no `Dockerfile`/`docker-compose.yml` in the Qubit Office plugin tree | **OUT-OF-SCOPE-WITH-REASON** — nothing upstream to containerize |

### What "100%" is allowed to mean, on this checklist

**"100% ported" = A1–A17 all pass, with A18–A20 recorded as deliberate
exclusions.** A claim of 100% that includes the server/mod/Docker (A18–A20) is
**wrong by contract** — it would port a second orchestrator Qubit already has.

One item (A8) and one (A17) are currently **OPEN** and must not be counted
as done:

- **A8** — the token-to-LAN exposure (S1). Inherited, not new, but real on this
  deployment because the service binds `0.0.0.0`.
- **A17** — confirm the reconciled suite is actually green at HEAD.

**A9 is now VERIFIED-NOW** (2026-10-16). It was recorded OPEN on a stale-checkout
misdiagnosis: the reproducible rebuild runs against the *recorded* commit
(`4ccce2537472`), which is not the shared checkout's HEAD (`86c0379`). Building
the pin reproduces all 77 vendored files byte-for-byte. See
`docs/VENDOR-REPRODUCIBILITY.md`.

---

## Part 4 — Summary of findings

| ID | Severity | Finding |
|---|---|---|
| S1 | **HIGH** | The live session token is injected into the plugin-app HTML and served to unauthenticated callers, including LAN peers (`0.0.0.0:8890`); the leaked token opens `/agent/api/status`, `/agent/api/office/tower`, `/chat`, and the SSE stream. **Pre-existing, dashboard-wide** (`/chat` leaks it too), inherited by the Office port. |
| S2 | MEDIUM | `blind_probe` Referer check accepts any loopback `/plugin-apps/` page; confined to `/api/access` (static capability flags only). |
| S3 | MEDIUM | The query-token exception is correctly pinned to `/api/stream`(+`/api/snapshot`), but the token necessarily reaches access logs/Referer there; safe only because of the (unenforced) loopback scope. |
| C1 | LOW | `ceo: undefined` in the mobile feed — upstream minified-bundle bug, not a port regression; needs a vendor-transform fix. |
| C2 | LOW | pt-BR copy ships in the vendored UI; locale chunks for `en` exist, so the fix is a config default, not translation. |
| O1 | MEDIUM | `frontend/qubit-dist/` is untracked; a dashboard rebuild can break the Office tab with no tracked diff. Verify the SERVED bundle. |
| O2 | LOW | Fresh clone has no dashboard bundle → 404 until `scripts/build_dashboard.sh` runs. |
| O3 | LOW | Re-vendor IS possible (checkout exists at `/home/trick/Projects/habblaud` with `vite` + `dist/client`); bit-exact reproduction is unverified because the current `dist/client` differs from the vendored build. |

**What "100%" cannot mean (blunt):** it cannot mean *all of the upstream
repository*. The server, the Claude Code mod, and the Docker setup are
**deliberately excluded and not needed** — Qubit already owns the orchestrator,
the session store, the tower, and the confirmation authority (Part 1.1–1.3). A
"100%" that ports them is a scope error, and it would ship a second backend.
The client's *write* half is likewise **gated by contract**, not missing: the
office is honestly **view-only** until the confirmation authority is wired
(Part 1.4).

---

## Part 5 — Concurrency hazard observed during this review

While writing this artifact, **another agent re-vendored the Habblaud tree**.
`git status` at the start of the review was clean; by the end:

```
 M frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json
 M frontend/qubit-app/plugins/qubit-office/habblaud/bundle/main-DND_b4_E.js
 M frontend/qubit-app/plugins/qubit-office/habblaud/index.html
 M frontend/qubit-app/plugins/qubit-office/habblaud/manifest.webmanifest
 M qubit_runtime/tui_gateway/agent_callbacks.py
 M scripts/vendor_habblaud.py
```

and the recorded byte total moved from **2147637 → 2147626**. `--check` is
self-consistent at both points (exit 0), so this is a legitimate publish, not
corruption — but it means:

1. **Any acceptance run must be fenced.** Hash the tree, run the checks, hash
   again; a mid-run re-vendor invalidates the verdict. The non-atomic publish
   window (`docs/VENDOR-ATOMIC-PUBLISH-FIX.md` residual: ~1 in 600k reads) is
   exactly a concurrent-reader hazard, and here the writer is another agent.
2. **Assert exit code + file count, never the byte total** (A1) — the byte total
   is a moving quantity while a re-vendor is in flight.
3. **Single-writer discipline is required for the vendored tree.** Two agents
   writing `frontend/qubit-app/plugins/qubit-office/habblaud/` concurrently is
   the failure mode this document's checklist has to survive. Whoever owns the
   final acceptance run must own the tree for its duration.

This is recorded, not smoothed over: the review's own artifact was written into
a live worktree that a peer was mutating.
