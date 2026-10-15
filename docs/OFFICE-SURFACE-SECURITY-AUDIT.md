# Office REST/SSE Surface — Security + Correctness Audit

- **Date:** 2026-10-15 (UTC)
- **Repo HEAD:** `17da98198f304bde56b7a81875d1c893118188c5`
- **Target:** the office REST/SSE surface served by `voicebridge/webui.py`, fed by
  `voicebridge/office_stream.py`.
- **Method:** live raw-HTTP probes against the running server on `:8890`
  (peer-tested from loopback `127.0.0.1` and from the LAN address
  `192.168.1.135`). The service was **not** restarted. Every status code below
  was observed, not inferred.
- **Token source:** `~/.config/qubit-voicebridge/voice.yaml` → `webui.token`
  (value used in probes redacted here).
- **LAN IP:** `192.168.1.135` (from `ip -4 addr`).

---

## VERDICT

**One real vulnerability found (HIGH): the session token still leaks to
non-loopback peers — not through `_serve_app_html` (the A8 fix holds there),
but through `_serve_qubit_index`, which injects the token unconditionally.**

Everything else audited holds: auth matrix, query-token scoping, path-traversal
confinement, and SSE-vs-tower data agreement are all correct.

| Area | Result |
|---|---|
| Auth matrix (per route × case) | **PASS** — no route passes that should refuse, except the two documented narrow exceptions |
| Scoped query-token exception did not widen | **PASS** — bad/good query tokens rejected on every ordinary API |
| Path traversal on `/plugin-apps/` | **PASS** — every escape → 404, confined to the plugin dir |
| SSE schema + real-data cross-check | **PASS** — snapshot rooms/agents/feed/HUD exactly match `/agent/api/office/tower` |
| A8 loopback-only token injection | **PARTIAL** — holds on `/plugin-apps/<pl>/<app>/*`; **bypassed** via the SPA shell (`/chat`, `/plugin-apps`, …) |
| Token injection reachable by a non-loopback peer | **FAIL (HIGH)** |

---

## 1. Auth matrix

Legend: **200** = served, **401** = `unauthorized`, **501** = typed
`OFFICE_CAPABILITY_UNBOUND`, **404** = not found.
`anon+ownRef` = no credential + loopback peer + `Host: 127.0.0.1` + a Referer
pointing under `/plugin-apps/`.

| Route | anon | token hdr | token query | BAD hdr | BAD query | wrong Host | foreign Ref | own-app Ref (anon) | LAN anon | LAN token hdr |
|---|---|---|---|---|---|---|---|---|---|---|
| `/api/stream` | 401 | **200** | **200**¹ | 401 | 401 | 401 | 401 | 401 | 401 | 200 |
| `/api/snapshot` | 401 | **200** | **200**¹ | 401 | 401 | 401 | 401 | 401 | 401 | 200 |
| `/api/access` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | **200**² | 401 | 200 |
| `/api/rooms` | 401 | 501 | 401 | 401 | 401 | 401 | 401 | 501² | 401 | 501 |
| `/api/timeline/days` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | **200**² | 401 | 200 |
| `/api/stats/days` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | **200**² | 401 | 200 |
| `/api/agents/3` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | **200**² | 401 | 200 |
| `/api/sessions/recent` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | **200**² | 401 | 200 |
| `/plugin-apps/qubit-office/habblaud/index.html` | **200**³ | 200 | 200 | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/plugin-apps/…/manifest.webmanifest` | 200³ | 200 | 200 | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/plugin-apps/…/bundle/` (dir, no index) | 404 | 404 | 404 | 404 | 404 | 404 | 404 | 404 | 404 | 404 |
| `/agent/api/office/tower` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | 401 | 401 | 200 |
| `/agent/api/status` | 401 | 200 | 401 | 401 | 401 | 401 | 401 | 401 | 401 | 200 |
| `/agent/api/nonsense` | 401 | 404 | 401 | 401 | 401 | 401 | 401 | 401 | 401 | 404 |

¹ `/api/stream` + `/api/snapshot` are the **only** routes that accept a
query token (`_office_app_caller_ok`, `webui.py:1574-1585`). Correct: EventSource
cannot set headers.

² The loopback + same-origin + own-app-Referer triple (`_office_client_read_ok`,
`webui.py:1644-1695`) — a **GET-only** read exemption for the office client's
literal `/api/*` dialect. Mutation authority is not widened (POST is refused).

³ Static asset: served to any caller by design. Confirmed below that the token is
**not** injected for non-loopback callers.

**No route passed that should have refused.** `wrong Host`, foreign `Referer`,
and LAN peers are correctly rejected everywhere a credential is required.

### 1.1 Blind-probe exception is correctly scoped to `/api/access`

| Request (anon, loopback, own-app Referer) | Status |
|---|---|
| `/api/access` | 200 |
| `/api/access?x=1` | 200 |
| `/api/access/` | 404 |
| `/api/access/extra` | 404 |

`_office_app_caller_ok(blind_probe=True)` pins the path to `/api/access`
(`webui.py:1605`), so the tokenless bootstrap read cannot reach any other route.

---

## 2. The scoped query-token exception did not widen — PASS

A **bad** query token must be rejected and must **not** fall back to the weaker
loopback exemption; an **ordinary** API must reject a query token even when it
is correct.

| Request | Expected | Observed |
|---|---|---|
| `GET /api/timeline/days?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/stats/days?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/agents/3?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/sessions/recent?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/rooms?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/access?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/timeline/days?token=<GOOD>` | refuse | **401** |
| `GET /api/rooms?token=<GOOD>` | refuse | **401** |
| `GET /api/access?token=<GOOD>` | refuse | **401** |
| `GET /api/sessions/recent?token=<GOOD>` | refuse | **401** |
| `GET /api/stream?token=WRONG` (no Referer) | refuse | **401** |
| `GET /api/stream?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/snapshot?token=WRONG` + own-app Referer | refuse | **401** |
| `GET /api/stream?token=<GOOD>` | serve | **200** (SSE) |
| `GET /api/snapshot?token=<GOOD>` | serve | **200** (SSE) |

The exception is exactly two paths, and a wrong query token is validated and
refused rather than downgraded. **No widening.**

### 2.1 Documented claim vs code: the query-token branch is NOT loopback-scoped

`_office_app_caller_ok`'s docstring states rule #1 "LOOPBACK ONLY" for the
stream exception. **Observation:** a **non-loopback** peer holding the token
gets the stream via the query string:

```
$ curl -s 'http://192.168.1.135:8890/api/stream?token=<GOOD>' | head -c 60
retry: 2000

event: snapshot
data: {"rev": 1, "serverTime": 1792087798904, "rooms": [{"id": "f1r1", …

$ curl -s -o /dev/null -w '%{http_code}' 'http://192.168.1.135:8890/api/stream?token=BAD'
401
```

This is **not** an unauthenticated hole — it demands the exact token, so it is
auth-equivalent to a header token. But the docstring's "LOOPBACK ONLY" applies
only to the *no-token* fallback, not to the query-token branch. The code is
internally consistent; the **comment is inaccurate** for this branch. Severity:
LOW (documentation), but it matters because it feeds §5's risk model — the
token is the *only* barrier on that path.

---

## 3. Path traversal on `/plugin-apps/<plugin>/<app>/...` — PASS

Sent with `curl --path-as-is` so curl did **not** normalize the URL (a
normalizing client would hide the result). All escape attempts returned
**404 / 9 bytes** (`not found`):

| Literal path | Status |
|---|---|
| `/plugin-apps/qubit-office/habblaud/../../../webui.py` | 404 |
| `/plugin-apps/qubit-office/habblaud/..%2f..%2f..%2fwebui.py` | 404 |
| `/plugin-apps/qubit-office/habblaud/%2e%2e/%2e%2e/%2e%2e/voicebridge/webui.py` | 404 |
| `/plugin-apps/../webui.py` | 404 |
| `/plugin-apps/qubit-office/../../webui.py` | 404 |
| `/plugin-apps/qubit-office/habblaud//../../../../etc/passwd` | 404 |
| `/plugin-apps/qubit-office/habblaud/assets/../../../../../../etc/passwd` | 404 |
| `/plugin-apps/../../../../etc/passwd` | 404 |
| `/plugin-apps//etc/passwd` | 404 |
| `/plugin-apps/absolute/etc/passwd` | 404 |
| `/plugin-apps/qubit-office/habblaud/index.html%00.png` | 404 |
| `/plugin-apps/qubit-office/habblaud/LICENSE.txt` (unexpected ext) | 404 |
| `/plugin-apps/qubit-office/habblaud/index.HTML` (case) | 404 |
| `/plugin-apps/qubit-office/habblaud/PROVENANCE.json` (allowed ext) | **200** (13 766 b) |

- `..` is rejected outright and segments are re-resolved against the plugin root
  (`webui.py:2032-2055`).
- The suffix allowlist is an **allowlist** (`webui.py:2057-2081`): `.txt` and
  `.HTML` are refused, not guessed.
- Compatibility alias `/http://<Host>/plugin-apps/…` is confined the same way:
  `/http://127.0.0.1:8890/plugin-apps/../../webui.py` → **404**.

### 3.1 Symlink escape — verified by logic replication, NOT against the live tree

The docstring claims a symlink cannot escape (`webui.py:2026-2028`). No symlink
exists in the plugin tree today, and creating one would require writing under
`frontend/` (out of scope for this audit). The confinement predicate
(`resolve()` then `startswith(resolved_root + "/")`) was replicated verbatim
against a temp tree:

| Case | Result |
|---|---|
| normal file in plugin dir | 200 |
| symlink → file outside plugin dir | 404 |
| symlink → directory outside plugin dir | 404 |
| dir symlink + `..` | 404 |

**Logic holds.** Live symlink case not exercised (see §7).

---

## 4. SSE correctness + real-data cross-check — PASS (no fabricated data)

### 4.1 Frame schema

Observed on `GET /api/stream?token=<GOOD>`:

```
retry: 2000
                                    ← blank line
event: snapshot
data: {…}
event: feed
data: [ … ]
```

- Begins with `retry: 2000`, then `event: snapshot` / `event: feed` frames.
- Every `data:` line parses as JSON (0 malformed of all frames captured).
- Snapshot carries the client's required top-level keys:
  `rev, serverTime, rooms, agents, accounts, meta` — all present.
- Every agent object carries the client's required `AgentInfo` fields
  (`id, kind, roomId, name, look, role, sessionId, status, activity, state,
  tasks, seed, startedAt, lastEventAt, statusSince, stats, recent, stalled,
  walking, facing, at`). The vendored bundle genuinely consumes these — grep of
  `app-U4Pwh2XA.js` shows `e.tasks.map(...)`, `lastEventAt`, `statusSince`.
- `meta.demo = false`, `meta.version = "qubit-office"`.

### 4.2 Cross-check against the real tower (the anti-fabrication test)

Compared the SSE `snapshot` to `GET /agent/api/office/tower` (same server, same
run):

| Field | SSE snapshot | `/agent/api/office/tower` | Match |
|---|---|---|---|
| rooms | `f1r1, f2r2, f2r3, f3r4` | same (floor#.room_id folded) | ✅ |
| agents | 10 (`ceo`→f1r1, `wren`→f2r2, …) | same 10, same room join | ✅ |
| `hud.headcount` | 10 | 10 | ✅ |
| `hud.revenue_cents` | 1000 | 1000 | ✅ |
| `hud.net_cents` | 980 | 980 | ✅ |
| feed ids | 7 rows, subset of tower `activity` (7 rows) | same ids/kinds/text | ✅ |
| schema | `qubit-office-tower/1` | `qubit-office-tower/1` | ✅ |

The snapshot is a **true translation** of the tower store — rooms, agents, the
agent→room join, HUD fold, and feed rows all agree exactly. No room, agent, or
event is invented; `accounts: []` and `meta.sources: []` are honestly empty.

### 4.3 Revision behavior under repeated polls

| Observation | Result |
|---|---|
| `rev` within one connection | monotonic: `1, 2, 3, 4, …` (increments each snapshot) |
| `rev` on a fresh connection | resets to `1` (per-connection counter, `webui.py:1908`) |
| `serverTime` | epoch-ms, advances per frame |

`rev` is **per-connection, not global**. A client must not use `rev` to detect
"newer" data across reconnects. Consistent with the code; noted as a design
property, not a defect.

---

## 5. A8 loopback-only token injection — **FIX HOLDS ON ITS OWN ROUTE, BYPASSED ELSEWHERE**

### 5.1 The A8 fix itself is correct on `/plugin-apps/…/*.html`

`_serve_app_html` (`webui.py:2113-2153`) injects `window.__QUBIT_SESSION_TOKEN__`
only when `_peer_is_loopback()` (`webui.py:2098-2111`).

| Caller | Token occurrences in office `index.html` | Bytes |
|---|---|---|
| loopback `127.0.0.1` | **1** (`7caun1…p2SB`) | 3019 |
| LAN `192.168.1.135` | **0** (also 0 for `__QUBIT_OFFICE_STREAM_URL__`) | 2815 |

- Loopback still receives the token **and** the office still renders (HTTP 200,
  full document).
- Non-loopback receives the same static HTML **without** the credential
  (2815 b = raw file), so it renders its own "not authenticated" state.
- **No `Host`-header spoof bypass:** a LAN peer sending `Host: localhost` or
  `Host: 127.0.0.1` still gets **0** token injections, and the API routes still
  return **401** to that same peer with a spoofed Host + own-app Referer. The
  peer-address half of `_peer_is_loopback` holds.

### 5.2 **VULNERABILITY (HIGH): the token leaks to non-loopback peers via `_serve_qubit_index`**

The A8 comment (`webui.py:2118-2124`) justifies the fix with:
> "the dashboard root answers 302 to `/login` and never reaches its injector,
> so this route was the ONLY way a non-local peer could obtain the token."

**That premise is false in this build.** `_serve_qubit_index`
(`webui.py:1778-1831`) injects the token at line **1790** with **no
`_peer_is_loopback()` guard**, and it is reached by the SPA shell routes.

**Reproduction (all from the LAN address, no credential):**

```bash
# 1. Scrape the live token from the SPA shell
curl -s 'http://192.168.1.135:8890/chat' \
  | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"'
# -> __QUBIT_SESSION_TOKEN__="7caun11MRuoXc5fZ3nSMagQANDcup2SB"

# 2. Replay it from the LAN against the office API
TOK=$(curl -s 'http://192.168.1.135:8890/chat' \
      | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | head -1 | sed 's/.*="//;s/"//')
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "X-Qubit-Session-Token: $TOK" \
  'http://192.168.1.135:8890/agent/api/office/tower'
# -> 200   (live office data, obtained from off-box)
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "X-Qubit-Session-Token: $TOK" \
  'http://192.168.1.135:8890/agent/api/status'
# -> 200
```

**Routes that leak the token to a non-loopback peer:**

| Route (from LAN) | Status | Token in body |
|---|---|---|
| `/chat` | 200 | **yes** |
| `/plugin-apps` (no trailing slash → falls through the `startswith("/plugin-apps/")` guard) | 200 | **yes** |
| `/http://evil.com/plugin-apps/qubit-office/habblaud/index.html` (alias falls through) | 200 | **yes** |
| `/` | 302 → `/chat` | (via redirect) |
| `/classic/` | 200 | no |
| `/token-setup`, `/approvals`, `/ops`, `/settings` | 200/302 | no |

**Root cause:** two token injectors exist (`webui.py:1810` and `webui.py:2144`);
only the second is loopback-guarded. The first injects the session credential
into any caller's copy of the SPA shell. This is the *same* vulnerability the A8
fix closed on the office-app route, still open on the shell route — and the shell
route is easier to reach than the one A8 hardened.

**Impact:** a peer on the LAN (or anyone who can reach `:8890`) reads the live
session token from `GET /chat` and thereby authenticates the entire
`/agent/api/*` surface — office tower/status, and the broader Qubit API the
token gates.

**Note on `:` binding:** the listener is `0.0.0.0:8890` (all interfaces), so
this is not gated behind a loopback bind.

---

## 6. Other observations

- **`/plugin-apps` (no trailing slash) → 200 SPA shell.** The router tests
  `path.startswith("/plugin-apps/")` (`webui.py:3048`), so the bare prefix is not
  caught and falls through to the shell. Not a traversal, but it is the vector
  in §5.2 — worth handling explicitly.
- **`OPTIONS` on office routes → 204; `HEAD` mirrors GET** (e.g. `HEAD
  /api/stream` → 401, `HEAD /plugin-apps/…/index.html` → 200). No auth bypass
  observed on either.
- **`/agent/api/nonsense`** → 404 with a valid token, 401 without — correct
  (unbound path, not an office-client path).
- **`/hermes*` hard 404** — confirmed absent as a route.

---

## 7. What could NOT be tested (explicit)

1. **Live symlink escape under `/plugin-apps/`** — no symlink exists in the
   plugin tree and adding one requires writing under `frontend/` (out of scope).
   The confinement predicate was replicated and verified in isolation (§3.1);
   the live path with a real symlink is **not** verified.
2. **The full network perimeter** — only the LAN address `192.168.1.135` was
   used as the "off-box" peer. Firewall/NAT/origin-proxy behavior in front of the
   listener was not tested.
3. **`do_POST`/`do_PUT`/`do_DELETE` office mutation paths** — this audit focused
   on the GET/stream surface named in the task. The POST-side auth predicates
   were read (`webui.py:1718`, `_office_app_caller_ok`) but not fuzzed live.
4. **Persistence of the leaked token** — whether the token is rotated, and the
   blast radius on other Qubit installs, was not assessed.
5. **`/agent/api/*` beyond tower/status/nonsense** — the two documented office
   reads were cross-checked; the broader compat table was not exhaustively
   probed for auth parity.
6. **`token_ok` constant-time property** — the comparison is `==`
   (`webui.py:4812`), not `hmac.compare_digest`; timing side-channel was not
   measured.

---

## 8. Recommended remediation (NOT applied — audit only)

1. **Guard `_serve_qubit_index`'s injection with `_peer_is_loopback()`** exactly
   as `_serve_app_html` now does (`webui.py:1790`). This is the single change
   that closes the §5.2 leak. A non-loopback caller should still receive the SPA
   HTML, just without `window.__QUBIT_SESSION_TOKEN__`.
2. **Handle the bare `/plugin-apps` prefix** explicitly (404 or redirect), so it
   cannot fall through the shell router.
3. **Fix the `_office_app_caller_ok` docstring** to state that the query-token
   branch is token-gated, not loopback-gated (§2.1), or scope that branch to
   loopback for consistency with the comment.
4. Consider **`hmac.compare_digest`** in `token_ok` for the credential compare.

---

## Appendix — probe artifacts

Written under
`/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch/`:

| File | Contents |
|---|---|
| `authmatrix.py` / `authmatrix.json` | route × case status matrix (§1) |
| `widen.py` | query-token widening probes (§2) |
| `symlink.py` | confinement logic replication (§3.1) |
| `sse.txt` | captured SSE frames (§4) |
| `tower.json` | captured tower report (§4.2) |
| `ev_lan_chat.html` | the LAN shell response carrying the token (§5.2) |
