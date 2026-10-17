# Office acceptance — VERIFIED checklist against live `:8890`

**Job:** walk every acceptance criterion in `docs/OFFICE-ACCEPTANCE-AND-RISK.md`
(A1–A20) and `docs/OFFICE-ACCEPTANCE-CLOSEOUT.md` against the LIVE server at
`http://127.0.0.1:8890`, record PASS/FAIL with the exact command and the observed
output, and fix only what is genuinely broken.

**Author:** Qubit Agent (acceptance-fix subagent). **Date:** 2026-10-16.
**Worktree:** `/tmp/wt-office-accept`, branch `fix/office-acceptance-2`, base
`5d9448286`.
**Live service:** `0.0.0.0:8890` (pid 2418), token read from the served `/chat`
document (`__QUBIT_SESSION_TOKEN__`).

Every command below was **run this session**; the output quoted is real. A claim
that could not be reproduced from real output is marked **UNVERIFIED**, never
PASS.

Legend: **PASS** (ran it, output quoted) · **FAIL** (ran it, wrong result, fixed
or recorded) · **OUT-OF-SCOPE** (deliberate exclusion by contract) ·
**UNVERIFIED** (not proven this session).

---

## Part 1 — A1–A20, live

| # | Criterion | Command run | Real output | Verdict |
|---|---|---|---|---|
| **A1** | Vendored tree matches its own hash record | `.venv/bin/python scripts/vendor_habblaud.py --check` | `ok: 77 files, 2147631 bytes, commit 4ccce2537472` / `exit=0` | **PASS.** Assert exit code + file count only; the byte total is volatile across re-vendors (2147637 → 2147626 → 2147631 across docs). |
| **A2** | Served plugin-app document returns 200 | `curl -s -o /dev/null -w '%{http_code}' .../plugin-apps/qubit-office/habblaud/` | `200` | **PASS** |
| **A3** | **Served** SPA bundle contains the office route | `IDX=$(curl -s .../chat \| grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' \| head -1)`; `sha256sum` served vs disk; `grep -c '/qubit-office'` | `IDX=/assets/index-CcyfgTav.js`; served hash `c1bf3920…0613cd`; disk hash **identical**; `grep -c` → `1` | **PASS.** NOTE: in a **bare git worktree** `frontend/qubit-dist/` is gitignored/untracked and ABSENT — A3 and the three wiring tests fail there spuriously. Symlink the built dashboard into the worktree first. Verified in the main repo. |
| **A4** | Stream live and bound to Qubit's own tower | `curl -s -o /dev/null -w '%{http_code} %{content_type}' '.../api/stream?token=$T'`; `ss -ltnp \| grep 4747` | `200 text/event-stream; charset=utf-8`; `no listener on 4747` | **PASS** |
| **A5** | Stream refuses absent/blank token | `curl -s -o /dev/null -w '%{http_code}' .../api/stream` | `401` | **PASS** |
| **A6** | `/api/access` reports the view-only capability set | `curl -s -H 'Referer: .../plugin-apps/qubit-office/habblaud/' .../api/access` | `{"remote": false, "local": true, "permissions": false, "messages": false, "terminal": false, "spawn": false}` | **PASS** |
| **A7** | Query-token exception confined to `/api/stream`(+`/api/snapshot`) | `tower?token=x`; `stream?token=wrongtoken123`; `tower` (no token) | `401` / `401` / `401` | **PASS** |
| **A8** | Document route does not hand the token to a non-loopback caller | LAN: `curl -s http://192.168.1.135:8890/plugin-apps/qubit-office/habblaud/ \| grep -c '__QUBIT_SESSION_TOKEN__'`; loopback same | `LAN tokens: 0`; `loopback tokens: 1`; LAN doc `200`; loopback renders (`lang="en"`, feed live). Fix present: `webui.py::_peer_is_loopback` (2130), `_serve_app_html` (2172) | **PASS (fixed for the plugin route).** Residual S1: LAN `GET /chat` → `200` with **1** token — still live, outside the Office route's scope. |
| **A9** | Re-vendor reproducible from the checkout | `cd /home/trick/Projects/habblaud && git rev-parse HEAD; git cat-file -t 4ccce25; ls node_modules/vite/bin/vite.js; ls dist/client/bundle/` | `HEAD=86c0379b…`; `cat-file -t 4ccce25` → `commit`; vite present; on-disk `app-DrspyBzY.js` vs vendored `app-U4Pwh2XA.js` | **FAIL (open, characterized).** The pinned input is reachable and `--src` can run, but the on-disk `dist/client` is a different build than the vendored tree, so a bit-exact re-vendor is **not** reproducible. Not a port defect; an upstream-checkout property. |
| **A10** | `?shell=1` rollback renders and does not throw | `.venv/bin/python scripts/render_office_screenshot.py --url '.../qubit-office?shell=1' --json` | `data_state: ready`; largest canvas 1305×736; `non_background_pixels: 843452` (**87.82%**); `console_errors: []`; HUD text present | **PASS** |
| **A11** | `?shell=1` uses its own data path, independent of SSE | CDP network capture of `?shell=1` | Only `GET /agent/api/office/tower` (repeated); **no** `/api/stream`; source `office-api.ts:46 SNAPSHOT_ROUTE = "/agent/api/office/tower"` | **PASS** (re-measured this session via raw CDP). |
| **A12** | Rollback renderer entry exists | `ls -l .../qubit-office/qubit-office.bundle.js` | `256767` bytes, present | **PASS** |
| **A13** | Three `entry` declaration sites agree | `grep -o '"entry": *"[^"]*"' manifest.json`; disk `fleet.json`; served `/plugins/fleet.json` | `qubit-office.game.js` (manifest); `qubit-office.game.js` (in the served fleet); disk fleet → same. `test_office_plugin_wiring.py` → `35 passed` in the main repo | **PASS.** Caveat: needs `frontend/qubit-dist/`; the served fleet is a JSON **array**, so `python3 -c …d.get()` miscalls — parse as a list. |
| **A14** | Path traversal cannot escape the plugin root | raw socket `GET /plugin-apps/qubit-office/habblaud/../../../../../etc/passwd` | `HTTP/1.0 404 Not Found`; body has no `root:x:` | **PASS** |
| **A15** | Office renderer tracked in git | `git ls-files .../habblaud \| wc -l`; `.../qubit-office.game.js \| wc -l` | `78`; `1` | **PASS** |
| **A16** | Write half gated, not silently missing | `grep -c 'gated: true' office-api.ts`; live POST probes | `2`; `POST /api/spawn → 401`, `/api/permissions → 401`, `/api/messages → 401`; `POST /api/spawn +token → 403` (confirmation-gated) | **PASS** |
| **A17** | Office test suite green | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` | **`188 passed, 24 skipped`** (doc claims `212 passed, 0 skipped`) | **FAIL (doc claim wrong).** Zero failures, but 24 packaging tests skip on `frontend/qubit-app/web/node_modules` (vite) absence — an honest environment skip, not broken tests. The doc's own §4.14g warned about silent skips; the count it publishes is stale. Full `tests/test_office_*.py` at the same commit: `1677 passed, 30 skipped, 15 failed` in a bare worktree — all 15 worktree artifacts (missing `.venv`/`qubit-dist`) **except one real defect** (Part 2, F1). |
| **A18** | Upstream SERVER not required | `ss -ltnp \| grep 4747`; tower fetch | `no listener on 4747`; `{"ok": true, "schema": "qubit-office-tower/1", …}` | **OUT-OF-SCOPE** — Qubit owns the data path. |
| **A19** | Claude Code MOD not required | `find .../qubit-office -type d -name mod` | no match | **OUT-OF-SCOPE** |
| **A20** | Docker not required | `find .../qubit-office \( -iname Dockerfile -o -iname 'docker-compose*' \)` | no match | **OUT-OF-SCOPE** |

### The honest "100%" statement, re-confirmed

**"100% ported" = A1–A17 pass, with A18–A20 recorded as deliberate
exclusions.** On this run: **A1–A8, A10–A16 PASS; A17 FAIL (count claim wrong,
skips unrecorded); A8 PASS with the `/chat` residual; A9 FAIL (open, upstream
property); A18–A20 OUT-OF-SCOPE.** The two open acceptance items are **A9** and
**A17** (the number, not the green-ness).

---

## Part 2 — Real defects found and fixed

### F1 — FIXED · `test_office_autowork_schedule.py` reaches into the real home

`tests/test_office_suite_health.py::test_no_office_test_references_real_home_or_state`
was **RED** at the base and in the live repo:

```
E  AssertionError: office tests must not reach into the real home/state dirs:
   [('test_office_autowork_schedule.py', 'Path.home(')]
```

`tests/test_office_autowork_schedule.py:54` returned
`Path(os.environ.get("QUBIT_HOME") or (Path.home() / ".qubit"))`. The office
suite's own hermeticity guard forbids any office test from depending on the
developer's real home — exactly the pass-here/fail-there coupling it exists to
prevent. `ensure_path()` / `use_qubit_home()` already publish `QUBIT_HOME` into
the environment (`qubit_runtime.pin_qubit_home`), so the `Path.home()` fallback
was **dead code** that only served to couple the test to a machine.

**Fix (`tests/test_office_autowork_schedule.py:54`):** read `QUBIT_HOME` and
raise a typed assertion if it is absent, instead of silently expanding `~`.

**Verified:** `pytest tests/test_office_suite_health.py
tests/test_office_autowork_schedule.py tests/test_office_label_clipping.py -q` →
`25 passed, 4 skipped`.

### F2 — RECORDED (not fixed) · the served `__vite__mapDeps` helper builds a doubled URL

Found by CDP capture of `/qubit-office`: several asset requests go to a
**doubled** URL:

```
http://127.0.0.1:8890/http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/bundle/app-U4Pwh2XA.js  -> 200
```

Mechanism, proven (`prove_doubled_url.py`, `cdp_eval.py`): the vendor transform
(`scripts/vendor_habblaud.py:363-368`) rewrites Vite's preload helper to resolve
deps against `location.origin+/plugin-apps/qubit-office/habblaud/`. Upstream's
`d[i]` values are relative (`bundle/app-XXXX.js`) and resolve correctly — but the
client's own loader (`s(e) = import.meta.resolve ? new URL(import.meta.resolve(e))`)
yields an **absolute** string, and a second resolution against the prefix base
produces the doubled path:

```
new URL('/plugin-apps/qubit-office/habblaud/bundle/app-U4Pwh2XA.js',
        'http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/')
  -> 'http://127.0.0.1:8890/http://127.0.0.1:8890/plugin-apps/.../bundle/app-U4Pwh2XA.js'
```

**This is mitigated, not broken:** the server carries a deliberate compatibility
alias (`voicebridge/webui.py:3095-3103`) that absorbs exactly that same-host
`/http://<host>/plugin-apps/` prefix, so the office renders. Confirmed safe:
`/http://evil.com/api/office/tower` falls through to the SPA shell (not the API),
and traversal under the doubled prefix still `404`s.

**Not fixed on purpose:** removing the alias would break the client unless the
helper rewrite is also corrected, and the correct fix is a coordinated change to
`scripts/vendor_habblaud.py` + re-vendor + provenance regen (a single-writer,
frozen-artifact operation the concurrency hazard in Part 5 of the risk doc warns
against doing from a read-mostly acceptance pass). **OPEN — recommend a
dedicated change.**

---

## Part 3 — Could NOT verify

1. **A9 bit-exact re-vendor.** Requires checking out `4ccce25` in
   `/home/trick/Projects/habblaud` and rebuilding — mutates a checkout another
   agent may use. Characterized only.
2. **A17 at the doc's stated count.** The doc's `212 passed` cannot be
   reproduced; the real number is `188 passed, 24 skipped`. Whether the 24 skips
   *would* pass with `web/node_modules` installed was not tested (installing is a
   network/state operation).
3. **Touch interaction on mobile viewports** and **long-run SSE stability** past
   the probe window — out of scope, as the prior browser docs also note.
4. **The doubled-URL path under a real long session** (does any *stateful* fetch,
   as opposed to a static chunk, ever take the doubled prefix?) — not driven.

---

## Part 4 — Fence / reproduce

```sh
cd /tmp/wt-office-accept
ln -sfn /home/trick/Projects/Repos/qubit-voicebridge/frontend/qubit-dist frontend/qubit-dist
ln -sfn /home/trick/Projects/Repos/qubit-voicebridge/.venv .venv
.venv/bin/python scripts/vendor_habblaud.py --check            # ok: 77 files, exit 0
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/api/stream                            # 401
.venv/bin/python -m pytest tests/test_office_suite_health.py -q                                      # 10 passed
.venv/bin/python -m pytest tests/test_office_game_*.py -q                                            # 188 passed, 24 skipped
TMPDIR=/tmp .venv/bin/python cdp_probe.py 'http://127.0.0.1:8890/qubit-office?shell=1'               # A11
```
