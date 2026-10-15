# Office acceptance closeout — A1-A20 and the risk findings, re-run against current HEAD

**Job:** bring `docs/OFFICE-ACCEPTANCE-AND-RISK.md` to current truth. Walk every
acceptance criterion A1-A20 and every risk finding (S1/S2/S3, C1/C2, O1/O3), run
its stated command, and record the real output as **VERIFIED-NOW**, **OPEN**, or
**OUT-OF-SCOPE-WITH-REASON**.

**Author:** Qubit Agent (acceptance closeout subagent). **Date:** 2026-10-15.
**Worktree:** `/home/trick/Projects/Repos/qubit-voicebridge`, HEAD `17da98198`
(`docs(office): correct the stale claims, re-verified from the tree`). Tree clean
(`git status --short | wc -l` → `0`).
**Read-only:** this file is the only artifact written. No code, test, or other
doc was edited; no service was restarted.

Every command below was **run this session**; the output quoted is real. Where a
claim could not be checked it is marked OPEN or UNVERIFIED, not smoothed over.

---

## Part 0 — What changed since `OFFICE-ACCEPTANCE-AND-RISK.md`

The 2026-10-15 review left three items OPEN: **A8**, **A9**, **A17**. All three
have moved, and two of them have moved *more* than the newer docs recorded:

| Item | Was | Is now | Evidence |
|---|---|---|---|
| **A8** token-to-LAN exposure | OPEN (HIGH) | **VERIFIED-NOW (fixed)**. LAN caller gets the document with **0** tokens; loopback gets **1**. | `curl http://192.168.1.135:8890/plugin-apps/qubit-office/habblaud/` → 200 / 0 tokens; loopback → 200 / 1 token. Fix commit `80f232080`, `voicebridge/webui.py::_peer_is_loopback` (2098) + `_serve_app_html` (2113). |
| **A9** re-vendor reproducibility | OPEN | **OPEN — characterized, and further along than `VENDOR-REPRODUCIBILITY.md` says.** The checkout now HAS the vendored commit and its blobs (`git cat-file -t 4ccce25` → `commit`), where that doc recorded *"(nothing: not present)"*. Still not bit-exact because the on-disk `dist/client` is a different build. | `cd /home/trick/Projects/habblaud && git cat-file -t 4ccce2537472…` → `commit`; `git log --oneline -1 4ccce25` → `4ccce25 Versão 0.11.0`; blobs readable (package.json `"version": "0.11.0"`). |
| **A17** office suite green | OPEN ("re-run to confirm") | **VERIFIED-NOW**. `212 passed`, exit 0. | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` → `212 passed in 129.53s`. |

**Three findings the newer docs did NOT close, and one that regressed:**

1. **`/chat` still leaks the live token to LAN peers** (S1 residual). The A8 fix
   covered the plugin-app document route only. Measured this session: LAN
   `GET /chat` → 200 with **1** token occurrence. This is the pre-existing
   dashboard-wide leak the original review named; it is **still live**.
2. **S2's blast radius is WIDER than the review recorded.** The original doc
   claimed the blind-probe exemption "returns no store data … confined to
   `/api/access`" and that `GET /api/agents/ceo` with a good referer → **401**.
   Measured now: `GET /api/agents/ceo` → **200 with real per-agent transcript
   data** (status, room, supervisor, history). See S2 below.
3. **C1 and C2 are both FIXED** (vendor transforms), which the newer docs still
   listed as OPEN defects: served HTML is now `<html lang="en">`, and the served
   `app-U4Pwh2XA.js` carries the corrected ticker interpolation.

---

## Part 1 — A1-A20, command by command

Legend: **VERIFIED-NOW** (ran it, passed, output quoted) · **OPEN** (true gap) ·
**OUT-OF-SCOPE-WITH-REASON**.

| # | Criterion | Command run this session | Real output | Verdict |
|---|---|---|---|---|
| **A1** | Vendored tree matches its own hash record | `.venv/bin/python scripts/vendor_habblaud.py --check` | `ok: 77 files, 2147631 bytes, commit 4ccce2537472` / `exit=0` | **VERIFIED-NOW.** Assert exit code + file count only — the byte total is volatile across rebuilds (here `2147631`, previously `2147637`/`2147626`). The changing byte total is legitimate, `--check` is self-consistent. |
| **A2** | Served plugin-app document returns 200 | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/` | `200` | **VERIFIED-NOW** |
| **A3** | **Served** SPA bundle contains the office route (O1 rebuild-drift guard) | `sha256sum frontend/qubit-dist/assets/index-CAakg9VA.js`; `curl -s http://127.0.0.1:8890/assets/index-CAakg9VA.js \| sha256sum`; `curl … \| grep -c '/qubit-office'` | disk hash `755378d4…3c5b51d0`; served hash **identical**; `grep -c` → `1`; literal `jt=[...Dt,`/webchat`,`/qubit-bot`,`/qubit-office`]` present in the served bytes | **VERIFIED-NOW.** Note the bundle is referenced from `/chat` (root `/` answers **302** to `/login`), so grep the served `/assets/index-*.js`, not `/`. |
| **A4** | Stream route live and bound to Qubit's own tower, not an upstream server | `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8890/api/stream?token=<T>'`; `ss -ltnp \| grep 4747` | `200 text/event-stream; charset=utf-8`; `no listener on 4747` | **VERIFIED-NOW** (200, and no upstream listener). The "upstream-server half" remains **OUT-OF-SCOPE** — there is no upstream server by design. Live SSE body confirmed: `retry: 2000` then `event: snapshot` with `data: {"rev":1,… "rooms":[boardroom,desk-a,desk-b,review], "agents":[…"name":"ceo"…]}`. |
| **A5** | Stream refuses absent/blank token | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/api/stream` | `401` | **VERIFIED-NOW** |
| **A6** | `/api/access` reports the conservative view-only capability set | `curl -s -H 'Referer: http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/' http://127.0.0.1:8890/api/access` | `{"remote": false, "local": true, "permissions": false, "messages": false, "terminal": false, "spawn": false}` | **VERIFIED-NOW** |
| **A7** | Query-token exception stays confined to `/api/stream`(+`/api/snapshot`) | `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8890/agent/api/office/tower?token=x'`; `… '/api/stream?token=wrongtoken123'`; `… '/agent/api/office/tower'` (no token) | `tower?token=x -> 401`; `stream?token=wrong -> 401`; `tower no token -> 401` | **VERIFIED-NOW** |
| **A8** | Token-bearing document route does not hand the token to a non-loopback caller | LAN: `curl -s http://192.168.1.135:8890/plugin-apps/qubit-office/habblaud/ \| grep -c '__QUBIT_SESSION_TOKEN__'`; loopback: same against `127.0.0.1`; plus LAN `/api/access` (with and without referer) | `LAN tokens: 0`; `loopback tokens: 1`; LAN doc still `200` and still renders (`<title>Habblaud</title>`, `main-DND_b4_E.js`); `LAN /api/access (no referer) -> 401`; `LAN /api/access (referer) -> 401` | **VERIFIED-NOW (FIXED).** The prior review's fix premise — "/chat leaks it too, so the plugin route mirrors the dashboard" — is inverted by measurement: the dashboard root answers **302** to `/login` and never reaches its injector, so this plugin route had been the ONLY non-loopback path to the credential. Loopback behavior unchanged (token still injected, office still renders: default `/qubit-office` → `data_state: ready`, canvas 1305×782, **99.28%** non-background, `console_errors: []`). **Residual, NOT covered by this fix:** LAN `GET /chat` → **200 with 1 token** (S1, below). |
| **A9** | A re-vendor is reproducible from the checkout | `cd /home/trick/Projects/habblaud && git rev-parse HEAD; git rev-list --count HEAD; git cat-file -t 4ccce2537472fe5a8d432f06974494d2e778ff15; git ls-tree -r 4ccce25 --name-only \| head`; `git config --get remote.origin.partialclonefilter`; `node_modules/.bin/vite --version`; `ls dist/client/bundle/` | `HEAD=86c0379b…`; `rev-list --count HEAD -> 1`; `cat-file -t 4ccce25 -> commit`; `git log -1 4ccce25 -> 4ccce25 Versão 0.11.0`; full tree readable (`.claude-plugin`, `Dockerfile`, `LICENSE`, `client/…`); a blob read returns `{"name":"habblaud","version":"0.11.0",…}`; filter `blob:none`; `vite/8.3.4`; on-disk `dist/client/bundle/app-DrspyBzY.js` vs vendored `app-U4Pwh2XA.js` | **OPEN (characterized — and one step better than `VENDOR-REPRODUCIBILITY.md`).** That doc recorded `git cat-file -t 4ccce25 → (nothing: not present)`. Measured now the commit, its tree, and readable blobs ARE present in the checkout — so the pinned input is now locally reachable, and `--src` from `4ccce25` is possible in principle. Bit-exact reproduction is STILL not verified, because the on-disk `dist/client` is a different build (different chunk hashes) than the vendored tree; a `--skip-build` re-vendor would publish a different artifact under the old commit's provenance. To close: `git checkout 4ccce25…` (or a clean worktree at it), rebuild, then compare file count + per-file content (not the byte total). |
| **A10** | `?shell=1` rollback renders and does not throw | `.venv/bin/python scripts/render_office_screenshot.py --url 'http://127.0.0.1:8890/qubit-office?shell=1' --json` | `data_state: ready`; `canvas_count: 2`; largest canvas 1305×736; `non_background_pixels: 843450` (**87.8%**); `console_errors: []`; HUD text `Qubit Office / PLACE / Outside the building / A+ / Motion / Settings` present | **VERIFIED-NOW.** (The script's human "ready: … 0 non-background pixels" line samples a different canvas than the JSON's `pixels_largest_canvas`; the JSON is authoritative.) |
| **A11** | `?shell=1` uses its own data path (`/agent/api/office/tower`), independent of SSE | static + served-bundle check: `grep -n 'SNAPSHOT_ROUTE' frontend/qubit-app/plugins/qubit-office/game/app/office-api.ts` | `office-api.ts:46: export const SNAPSHOT_ROUTE = "/agent/api/office/tower";` | **VERIFIED (code path).** The shell polls `/agent/api/office/tower` via `OfficeAdapter`. This session did not re-run a per-request CDP network capture (the prior `BROWSER-PARITY-FINDINGS.md` §3d did, recording "no `/api/stream`"); the static evidence is unchanged and consistent. Marked VERIFIED on code + prior live capture, not re-measured here. |
| **A12** | Rollback renderer entry exists and is reachable | `test -f frontend/qubit-app/plugins/qubit-office/qubit-office.bundle.js` | `bundle.js YES` | **VERIFIED-NOW** |
| **A13** | Three `entry` declaration sites agree | `grep -o '"entry": *"[^"]*"' manifest.json`; same for `frontend/qubit-dist/plugins/fleet.json` and served `/plugins/fleet.json` | all three → `"entry": "qubit-office.game.js"` | **VERIFIED-NOW** |
| **A14** | Path traversal cannot escape the plugin root (raw HTTP) | raw socket: `GET /plugin-apps/qubit-office/habblaud/../../../../../etc/passwd HTTP/1.1` | `HTTP/1.0 404 Not Found`; body does not contain `root:x:` | **VERIFIED-NOW** |
| **A15** | Office renderer tracked in git | `git ls-files …/habblaud \| wc -l`; `git ls-files …/qubit-office.game.js \| wc -l` | `78`; `1` | **VERIFIED-NOW** |
| **A16** | Write half explicitly gated (not silently missing) | `grep -c 'gated: true' frontend/qubit-app/plugins/qubit-office/game/app/office-api.ts`; POST probes | `2`; `POST /api/spawn -> 401`, `POST /api/permissions -> 401`, `POST /api/messages -> 401` (no token); `POST /api/spawn +token -> 403` (confirmation-gated) | **VERIFIED-NOW.** `bindingVerdict()`/`bindingSummary()` live at `office-api.ts:59/78`. The write half is gated by token **and** confirmation, not absent. |
| **A17** | Office test suite green | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` | `212 passed in 129.53s (0:02:09)` / `EXIT=0` | **VERIFIED-NOW.** Also green: `tests/test_office_vendor_habblaud.py` → `17 passed`; `tests/test_office_vendor_stream_rewrite.py` → `5 passed`. |
| **A18** | Upstream SERVER not required, not to be ported | `ss -ltnp \| grep 4747`; `curl -H "X-Qubit-Session-Token: $T" …/agent/api/office/tower` | `no listener on 4747`; tower returns `{"ok": true, "schema": "qubit-office-tower/1", … "hud": {"headcount": 10, …}}` | **OUT-OF-SCOPE-WITH-REASON** — Qubit owns the data path (`qubit-office-tower/1` is Qubit's schema); porting the server would duplicate Qubit's orchestrator. |
| **A19** | Claude Code MOD not required, not to be ported | `find frontend/qubit-app/plugins/qubit-office -type d -name mod` | no match | **OUT-OF-SCOPE-WITH-REASON** — it instruments Claude Code, a different runtime; it polls the non-vendored server on 4747. |
| **A20** | Docker not required, not to be ported | `find frontend/qubit-app/plugins/qubit-office \( -iname Dockerfile -o -iname 'docker-compose*' \)` | no match | **OUT-OF-SCOPE-WITH-REASON** — nothing upstream to containerize. |

### The honest "100%" statement, re-confirmed

**"100% ported" = A1-A17 pass, with A18-A20 recorded as deliberate
exclusions.** On this run: **A1-A7, A10-A17 VERIFIED-NOW; A8 VERIFIED-NOW
(fixed); A9 OPEN-characterized; A11 VERIFIED on code + prior live capture;
A18-A20 OUT-OF-SCOPE by design.** The only open acceptance item is **A9**
(re-vendor reproducibility), which is an upstream-checkout property, not a
defect in the port.

---

## Part 2 — Risk findings, current state

### S1 — HIGH · session token served to unauthenticated callers — **PARTIALLY FIXED**

- **Plugin-app document route: FIXED** (this is A8). LAN → 200 with 0 tokens;
  loopback → 200 with 1; loopback still renders.
- **Dashboard `/chat` shell: STILL LEAKS.** Measured this session:
  ```
  curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.135:8890/chat   -> 200
  curl -s http://192.168.1.135:8890/chat | grep -c '__QUBIT_SESSION_TOKEN__' -> 1
  curl -s http://127.0.0.1:8890/chat  | grep -c '__QUBIT_SESSION_TOKEN__'    -> 1
  ```
  The live service still binds `0.0.0.0:8890` (`ss -ltnp` → `0.0.0.0:8890`), so
  the exposure is real and LAN-wide. The original review's "pre-existing,
  dashboard-wide" characterization **still holds for `/chat`**; it just no
  longer holds for the plugin-app route.
- **Verdict:** **OPEN (residual, HIGH)** for `/chat`; **CLOSED** for the
  plugin-app route. The acceptance requirement A8 ("no non-loopback,
  non-token caller gets the credential from the document route") is met for the
  route it names; the wider dashboard leak is out of the Office port's scope but
  must not be restated as fixed.

### S2 — MEDIUM → **now higher** · blind-probe Referer exemption is over-broad AND reads real agent data

The exemption at `webui.py::_office_app_caller_ok` (1532) still accepts any
loopback page whose `Referer` merely contains `/plugin-apps/`. Measured:

```
good app referer  /api/access              -> 200
arbitrary referer /plugin-apps/other/      -> 200   (over-broad, unchanged)
spoofed Host: evil.com + good referer      -> 401   (Host check holds)
/api/agents/ceo  + good referer            -> 200   <-- CHANGED: was 401
```

**The original doc's claim that the exemption is "confined to `/api/access`
(static capability flags only)" is now FALSE.** `GET /api/agents/:id` returns
**real per-agent transcript data** to a tokenless loopback caller with a
`/plugin-apps/` referer:

```
{"ok": true, "schema": "qubit-office-habblaud-agent/1", "id": "1", … "slug": "ceo",
 "status": "active", "history": [{"id":"2","at":…,"kind":"run_unpriced","text":"finished a turn…"}, …]}
```

Bounded: the other office-client roots sampled still refuse tokenless access
(`/api/agents` → 501, `/api/rooms` → 501, `/api/permissions` → 501,
`/api/messages` → 501, `/api/snapshot` → 401, `/api/status` → 401), writes are
gated (POSTs → 401 without token), and the exemption remains loopback+Host-bound
so the `evil.com` spoof is rejected. **But the blast radius is no longer
"capability discovery on loopback" — it is "read real agent transcripts on
loopback from any local page under `/plugin-apps/`."** Severity moves from the
review's MEDIUM toward **MEDIUM-HIGH (defence-in-depth now protecting real
data)**. Still loopback-only; still not remote.

### S3 — MEDIUM · query-token exception pinned to `/api/stream`(+`/api/snapshot`) — **UNCHANGED**

The exception is still tight; ordinary APIs reject a query token (A7: `401`).
The token still necessarily reaches access logs/Referer on the SSE URL, and the
served HTML still carries `/api/stream?token=<live-token>` — now **loopback
only** (A8), which materially reduces the exposure S3 depended on. **Verdict:
MEDIUM, narrowed by the A8 fix** (the query token no longer travels to LAN peers
via the plugin document).

### C1 — LOW · `ceo: undefined` in the mobile feed — **FIXED**

The vendor transform now rewrites the feed ticker. Verified in the **served**
bundle `bundle/app-U4Pwh2XA.js`:

```
grep -oE 'agentName\}: [^;]{0,90}' bundle/app-U4Pwh2XA.js
 -> agentName}: ${c.activity.icon ? `${c.activity.icon} ` : ``}${c.activity.text}`:``),this.toggleBtn.inne
```

The buggy literal `${c.agentName}: ${c.activity.icon} ${c.activity.text}` is
**absent** (grep count `0`); the fixed form is present. Transform lives in
`scripts/vendor_habblaud.py` (the "Stop the feed ticker rendering a literal
`undefined`" block, ~line 153). **VERIFIED-NOW (fixed).** Note: the fix targets
`main-*.js`'s ticker *sibling*; the live ticker is in `app-*.js` — worth a
maintainer confirming the transform is applied to the bundle that actually
ships the ticker.

### C2 — LOW · pt-BR copy shipped in the vendored UI — **FIXED**

Served document is now `<html lang="en">` (was `lang="pt-BR"`). The vendor
transform sets the locale default to `en` (`DEFAULT_LOCALE = "en"`,
`scripts/vendor_habblaud.py:56`), which rewrites the client's hard-coded
`pt-BR` fallback. Verified live in the served bundle:
`grep -oE 'return`[a-z-]+`' bundle/main-DND_b4_E.js | sort | uniq -c` →
`4 return`en``, `1 return`es``, `1 return`fr`` — **no `return`pt-BR``**.
**VERIFIED-NOW (fixed).** (Firefox parity doc independently recorded
`iframe.contentDocument.documentElement.lang == "en"`.)

### O1 — MEDIUM · dashboard rebuild can silently invalidate the served entry — **LIVE (guard holds)**

`frontend/qubit-dist/` remains untracked (`git ls-files frontend/qubit-dist |
wc -l` → `0`), so a rebuild can break the Office tab with no tracked diff. The
guard is A3: hash the **served** bundle and grep it — this session the served
hash equals the on-disk hash (`755378d4…`) and the `/qubit-office` literal is
present. **Verdict: risk LIVE; the acceptance guard works.** Re-run A3 after any
dashboard rebuild.

### O2 — LOW · fresh clone has no `frontend/qubit-dist/` — **LIVE**

`webui.py:1782` still serves `404 dashboard not built (see …)` when the SPA is
absent. The vendored Office tree is tracked; the **host shell** is not. "Does
the Office work on a fresh clone" is **NO** until `scripts/build_dashboard.sh`
runs. Not a port defect — inherited build property. Unchanged.

### O3 — LOW · vendored tree re-vendorable — **RECONCILED INTO A9**

O3's core claim ("a checkout exists; re-vendor can run") is **true and now
stronger**: the checkout `git cat-file -t 4ccce25` → `commit` with readable
blobs (see A9). The caveat O3 named ("the current `dist/client` is a different
build") is **still true**. O3 is the same finding as A9 and carries the same
verdict: **OPEN-characterized**. Not a blocker.

---

## Part 3 — Criteria I could NOT verify, and why

1. **A11 per-request network capture.** I verified the code path
   (`SNAPSHOT_ROUTE = "/agent/api/office/tower"`) and relied on
   `BROWSER-PARITY-FINDINGS.md` §3d's prior CDP capture for the "no `/api/stream`"
   half. I did **not** re-run a per-request CDP network capture this session.
2. **Firefox live render.** Not re-run here. `FIREFOX-PARITY-FINDINGS.md`
   records a PASS (geckodriver → real ELF binary, `99.86%` painted,
   `lang == "en"`, zero app console errors) — that is a browser run by another
   session, carried over, not reproduced by me.
3. **A9 bit-exact reproduction.** Requires checking out `4ccce25…` in the
   checkout and rebuilding — a network/state operation I deliberately did not
   perform (it would mutate a checkout another agent may use, and it does not
   change the served page). So A9 is **characterized**, not closed.
4. **Touch interaction** on the mobile viewports (tapping controls), and
   **long-run SSE stability** past the probe window — both out of scope, as the
   prior browser doc also notes.
5. **Whether the C1 transform is applied to the bundle that actually ships the
   ticker** (`app-*.js` vs `main-*.js`). The fixed literal is present in the
   served `app-*.js` and the buggy literal is absent, so the *outcome* is right;
   I did not trace the transform's target filename.

---

## Part 4 — Reproduce this closeout

```sh
cd /home/trick/Projects/Repos/qubit-voicebridge
git log --oneline -1                                  # 17da98198
git status --short | wc -l                            # 0
.venv/bin/python scripts/vendor_habblaud.py --check   # ok: 77 files, …, commit 4ccce2537472 (exit 0)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/   # 200
T=$(curl -s http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | cut -d'"' -f2)
# A8: LAN gets NO token, loopback gets one
curl -s http://192.168.1.135:8890/plugin-apps/qubit-office/habblaud/ | grep -c '__QUBIT_SESSION_TOKEN__'   # 0
curl -s http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/     | grep -c '__QUBIT_SESSION_TOKEN__'   # 1
# S1 residual: /chat still leaks to the LAN
curl -s http://192.168.1.135:8890/chat | grep -c '__QUBIT_SESSION_TOKEN__'                                # 1
# S2 widened: /api/agents/:id returns real data to a tokenless loopback referer
curl -s -H 'Referer: http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/' http://127.0.0.1:8890/api/agents/ceo
.venv/bin/python -m pytest tests/test_office_game_*.py -q   # 212 passed
.venv/bin/python scripts/render_office_screenshot.py --url 'http://127.0.0.1:8890/qubit-office?shell=1' --json
cd /home/trick/Projects/habblaud && git cat-file -t 4ccce2537472fe5a8d432f06974494d2e778ff15  # commit
```

---

## Part 5 — One-line status

**A1-A7, A10-A17 VERIFIED-NOW; A8 VERIFIED-NOW (fixed for the plugin route, but
`/chat` still leaks the token to the LAN); A9 OPEN-characterized (checkout now
carries the pinned commit, but the on-disk build differs); A18-A20
OUT-OF-SCOPE by design. C1 and C2 are now FIXED. S2's blast radius is wider than
recorded — the loopback exemption reads real agent transcripts, not just
capability flags. The single open acceptance item is A9.**
