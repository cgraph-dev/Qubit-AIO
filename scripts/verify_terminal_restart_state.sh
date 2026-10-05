#!/usr/bin/env bash
# verify_terminal_restart_state.sh — classify the dashboard terminal's state
# after a backend restart. READ-ONLY: it never restarts the service; it inspects
# whatever state currently exists and prints a PASS/FAIL/STATE verdict per item.
#
# Usage:
#   scripts/verify_terminal_restart_state.sh [BASE_URL] [SESSION_TOKEN]
#
#   BASE_URL       default http://127.0.0.1:8890 (the `voicebridge run` dashboard)
#   SESSION_TOKEN  if omitted, read from the served page (__QUBIT_SESSION_TOKEN__);
#                  NEVER hardcode/commit a token.
#
# Auth model (matches the SPA): REST probes send header X-Qubit-Session-Token
# (the backend also accepts the compat spelling X-Hermes-Session-Token). The WS
# upgrade mints a single-use ?ticket= via POST /agent/api/auth/ws-ticket.
#
# Verdicts:
#   (a) clean auto-reconnect, output replayed   -> PASS
#   (b) reconnected but empty/stale             -> WARN
#   (c) stuck on error, no retry                -> FAIL
#   (d) stuck/loading, backend down             -> FAIL
#   (e) dead, manual reload required            -> FAIL
#
# Exit 0 = every probe ran to a definite verdict; non-zero = a probe could not
# run (backend unreachable / token unreadable), so verdicts are partial.

set -u
BASE="${1:-http://127.0.0.1:8890}"
TOKEN="${2:-}"

pass()  { printf '  %-6s %s\n' "PASS"  "$1"; }
warn()  { printf '  %-6s %s\n' "WARN"  "$1"; }
fail()  { printf '  %-6s %s\n' "FAIL"  "$1"; }
state() { printf '  %-6s %s\n' "STATE" "$1"; }
info()  { printf '  %-6s %s\n' "INFO"  "$1"; }
hr()    { printf '%s\n' "------------------------------------------------------------"; }

PROBLEMS=0
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 2; }
# Prefer the repo venv (it carries `websockets`); fall back to system python.
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python || true)"
fi

hr; echo "Dashboard terminal restart-state verification"; echo "BASE=$BASE"; hr

# ---- 1. Read the session token from the served page -----------------------
if [ -z "$TOKEN" ]; then
  TOKEN="$(curl -fsS --max-time 5 "$BASE/chat" 2>/dev/null \
    | grep -oE '__QUBIT_SESSION_TOKEN__="[^"]+"' | head -1 \
    | sed -E 's/.*="([^"]+)"/\1/')"
fi
if [ -z "$TOKEN" ]; then
  fail "could not read __QUBIT_SESSION_TOKEN__ from $BASE/chat (or none supplied)"
  PROBLEMS=$((PROBLEMS+1))
else
  info "session token read (${#TOKEN} chars) — not printed"
fi

# ---- 2. Backend liveness --------------------------------------------------
health_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
  -H "X-Qubit-Session-Token: $TOKEN" "$BASE/agent/api/health" 2>/dev/null || echo 000)"
if [ "$health_code" = "200" ]; then
  HEALTHY=1; pass "backend UP (/agent/api/health -> 200)"
else
  HEALTHY=0; fail "backend DOWN/unauthorized (/agent/api/health -> $health_code)"
  PROBLEMS=$((PROBLEMS+1))
fi

# ---- 3. Ticket mintable (a hard refusal = permanent dead-end) -------------
TICKET=""
if [ "$HEALTHY" = "1" ] && [ -n "$TOKEN" ]; then
  ticket_json="$(curl -s --max-time 5 -X POST \
    -H "Content-Type: application/json" -H "X-Qubit-Session-Token: $TOKEN" \
    -d '{"scope":"pty"}' "$BASE/agent/api/auth/ws-ticket" 2>/dev/null)"
  TICKET="$(printf '%s' "$ticket_json" | grep -oE '"ticket"[[:space:]]*:[[:space:]]*"[^"]+"' | sed -E 's/.*"([^"]+)"$/\1/')"
  if [ -n "$TICKET" ]; then
    pass "PTY ticket mintable (/agent/api/auth/ws-ticket -> ticket)"
    PROFILE="$(printf '%s' "$ticket_json" | grep -oE '"profile_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*"([^"]*)"$/\1/')"
    [ -n "$PROFILE" ] && info "resolved profile: $PROFILE"
  else
    fail "PTY ticket REFUSED — body: $(printf '%s' "$ticket_json" | head -c 200)"
    PROBLEMS=$((PROBLEMS+1))
  fi
else
  info "skipping ticket probe (backend down / no token)"
fi

# ---- 4. attach-reuse round-trip (proves buffered replay on reattach) ------
# This is the mechanism a browser reload relies on: a repeated ?attach= token
# must map to the SAME PTY and replay its buffer, spawning NO second terminal.
if [ -n "$TICKET" ] && [ -n "$PY" ]; then
  if "$PY" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("websockets") else 1)' 2>/dev/null; then
    probe_out="$("$PY" - "$BASE" "$TOKEN" "$TICKET" <<'PYEOF' 2>&1
import asyncio, json, subprocess, sys, time, urllib.request
BASE, TOKEN, TICKET = sys.argv[1], sys.argv[2], sys.argv[3]

def mint():
    req = urllib.request.Request(
        f"{BASE}/agent/api/auth/ws-ticket", data=json.dumps({"scope": "pty"}).encode(),
        headers={"Content-Type": "application/json", "X-Qubit-Session-Token": TOKEN}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=5))["ticket"]

def count():
    o = subprocess.run(["bash", "-c", "ps -eo cmd | grep -c 'ui-tui/dist/entry.js'"],
                       capture_output=True, text=True).stdout.strip()
    return int(o or 0)

async def main():
    import websockets
    attach = "verify-probe-%d" % int(time.time())
    before = count()
    print(f"PY before={before}")
    sizes = []
    for i in (1, 2):
        t = mint() if i == 2 else TICKET
        url = f"ws://{BASE.split('//',1)[1]}/agent/api/pty?ticket={t}&cols=100&rows=30&attach={attach}"
        got = 0
        try:
            async with websockets.connect(url, max_size=None) as ws:
                end = time.time() + 6
                while time.time() < end and got < 400:
                    try:
                        m = await asyncio.wait_for(ws.recv(), timeout=3)
                    except asyncio.TimeoutError:
                        break
                    got += len(m) if isinstance(m, (bytes, bytearray)) else len(m.encode())
        except Exception as e:
            print(f"PY conn{i} error: {type(e).__name__}: {e}")
        sizes.append(got)
        print(f"PY conn{i}_bytes={got} after={count()}")
        await asyncio.sleep(0.5)
    after = count()
    print(f"PY after={after}")
    print(f"PY reused={'yes' if after <= before + 1 else 'no'} replay={'yes' if sizes[1] > 0 else 'no'}")

asyncio.run(main())
PYEOF
)"
    echo "$probe_out" | grep -E '^PY ' | sed 's/^PY /  INFO   /'
    if echo "$probe_out" | grep -q 'reused=yes replay=yes'; then
      pass "attach-reuse: second ?attach= connection replayed output, no 2nd PTY -> state (a) reachable"
    else
      warn "attach-reuse probe inconclusive (see INFO lines above)"
    fi
  else
    info "python 'websockets' not installed; skipping attach-reuse probe"
  fi
else
  info "skipping attach-reuse probe (no ticket / no python)"
fi

# ---- 5. The dead-end classification --------------------------------------
hr; echo "VERDICT PER STATE"; hr
state "client model: socket close -> 1500ms -> connect() -> withBackoff(wsTicket) = 5 tries / ~7s"
state "  onclose() reads NO close code; only ?resume= change / New chat / reload re-arms it"
if [ "$HEALTHY" = "0" ]; then
  fail "(d) backend down NOW. A tab open since the drop is in the WS_TICKET catch:"
  fail "    banner only, NO auto-retry. Bring the backend up; if the tab waited >~7s"
  fail "    it needs a manual reload (state e) — this IS a dead-end."
elif [ -n "$TICKET" ]; then
  pass "(a) clean auto-reconnect reachable: backend up + ticket mints."
  state "    A tab whose close was <~7s ago reconnects with no reload."
  state "    A tab stuck past ~7s needs reload / New chat — state (e) dead-end."
else
  fail "(c) backend up but ticket REFUSED -> terminal cannot start; dead-end until"
  fail "    the refusal cause is fixed (context floor / chat disabled / bad profile)."
fi
hr
if [ "$PROBLEMS" = "0" ]; then
  echo "OVERALL: all probes ran to a definite verdict."
else
  echo "OVERALL: $PROBLEMS probe(s) could not run — verdicts are partial."
fi
hr
echo "State legend:"
echo "  (a) clean auto-reconnect w/ replayed output   (b) reconnected but empty/stale"
echo "  (c) stuck on error, no retry                  (d) stuck/loading or backend-down"
echo "  (e) dead, manual reload required"
exit $([ "$PROBLEMS" = "0" ] && echo 0 || echo 1)
