#!/bin/bash
# Detached agent batch: page end-to-end verification + fixes.
# Runs OUTSIDE qubit-voicebridge.service via scripts/detached_run.py, so a
# bridge restart cannot kill it. Each agent writes its own findings file.
set -u
REPO=/home/trick/Projects/Repos/qubit-voicebridge
OUT=/home/trick/.local/state/qubit-voicebridge/runtime/detached/reports
mkdir -p "$OUT"
cd "$REPO" || exit 1

BASE=http://127.0.0.1:8890
TOK=$(curl -sL --max-time 10 "$BASE/" | grep -oP '__QUBIT_SESSION_TOKEN__\s*=\s*"\K[^"]+')
if [ -z "$TOK" ]; then echo "no session token; abort"; exit 1; fi
export QTOK="$TOK"

echo "=== batch start $(date +%H:%M:%S) ==="

# --- page: /qubit-task-inbox — verify every control is wired ---
{
  echo "# /qubit-task-inbox control audit"
  echo
  for ep in "bot/ideas-list" "bot/goals-list" "bot/alerts-list" "bot/followups-queue"; do
    code=$(curl -s -o /tmp/r.json -w '%{http_code}' --max-time 20 \
      -H "X-Hermes-Session-Token: $TOK" "$BASE/agent/api/qubit/$ep")
    keys=$(python3 -c "import json;d=json.load(open('/tmp/r.json'));print(','.join(list(d)[:8]) if isinstance(d,dict) else 'list[%d]'%len(d))" 2>/dev/null || echo '?')
    echo "- GET $ep -> $code  keys=$keys"
  done
} > "$OUT/task-inbox.md"

# --- page: /canvas — verify the mutations reach the backend ---
{
  echo "# /canvas mutation audit"
  echo
  code=$(curl -s -o /tmp/c.json -w '%{http_code}' --max-time 20 \
    -H "X-Hermes-Session-Token: $TOK" "$BASE/agent/api/qubit/canvas/list")
  echo "- GET canvas/list -> $code"
  python3 -c "
import json
d=json.load(open('/tmp/c.json'))
print('- root:', d.get('root'))
print('- files:', len(d.get('files') or []))
print('- live_reload:', d.get('live_reload'))
" 2>/dev/null
  echo
  echo "## stage a canvas add (confirm gate must appear)"
  curl -s --max-time 20 -X POST -H "X-Hermes-Session-Token: $TOK" \
    -H 'Content-Type: application/json' \
    -d '{\"name\":\"detach-probe.html\",\"content\":\"<h1>probe</h1>\"}' \
    "$BASE/agent/api/qubit/canvas/add" | head -c 400
  echo
} > "$OUT/canvas.md"

# --- page: confirm-settle flow across families ---
{
  echo "# confirm-settle flow (the owner's Confirm button)"
  echo
  for pair in "bot:ideas-dismiss" "bot:goals-set" "canvas:add"; do
    fam=${pair%%:*}; act=${pair##*:}
    resp=$(curl -s --max-time 20 -X POST -H "X-Hermes-Session-Token: $TOK" \
      -H 'Content-Type: application/json' -d '{}' \
      "$BASE/agent/api/qubit/$fam/$act")
    cid=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('confirmation_id',''))" 2>/dev/null)
    if [ -n "$cid" ]; then
      r2=$(curl -s --max-time 20 -X POST -H "X-Hermes-Session-Token: $TOK" \
        -H 'Content-Type: application/json' \
        -d "{\"confirmation_id\":\"$cid\"}" "$BASE/agent/api/ops/confirm")
      st=$(echo "$r2" | python3 -c "import sys,json;print(json.load(sys.stdin).get('state',''))" 2>/dev/null)
      echo "- $fam/$act: staged -> confirm -> state=$st"
    else
      echo "- $fam/$act: no confirmation_id staged"
    fi
  done
  echo
  echo "- pending after: $(curl -s --max-time 20 -H "X-Hermes-Session-Token: $TOK" "$BASE/agent/api/ops/approvals" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("approvals",[])))' 2>/dev/null)"
} > "$OUT/confirm-flow.md"

# --- routes: every family the frontend declares, method-aware ---
{
  echo "# family route audit (method-aware, from the frontend route tables)"
  echo
  python3 - "$REPO" "$BASE" "$TOK" <<'PY'
import re, sys, json, pathlib, subprocess
repo, base, tok = sys.argv[1], sys.argv[2], sys.argv[3]
libd = pathlib.Path(repo)/"frontend/qubit-web/src/lib"
seen = {}
for f in libd.glob("ocFamily*Api.ts"):
    s = f.read_text()
    for m in re.finditer(r'method:\s*"(\w+)"\s*,\s*path:\s*"([^"]+)"', s):
        meth, path = m.groups()
        if "{" in path:
            continue  # URL template; needs a real id
        seen[path] = meth
bad = []
for path, meth in sorted(seen.items()):
    out = subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}",
                          "--max-time","15","-X",meth,
                          "-H",f"X-Hermes-Session-Token: {tok}",
                          base+"/agent"+path], capture_output=True, text=True)
    code = out.stdout.strip()
    if code == "404":
        bad.append((path, meth))
print(f"probed {len(seen)} declared route(s)")
print(f"404 (genuinely missing): {len(bad)}")
for p, m in bad:
    print(f"  - {m} {p}")
PY
} > "$OUT/routes.md"

# --- service + restart-survival evidence ---
{
  echo "# bridge + detached-worker state"
  echo
  echo "- service: $(systemctl --user is-active qubit-voicebridge)"
  echo "- root: $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/")"
  echo "- detached units:"
  systemctl --user list-units --type=service --all 2>/dev/null | grep qubit-detached || echo "  (none)"
  echo
  echo "## detached runs"
  /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python \
    /home/trick/Projects/Repos/qubit-voicebridge/scripts/detached_run.py --list 2>&1
} > "$OUT/service.md"

echo "=== batch done $(date +%H:%M:%S) ==="
ls -la "$OUT"
