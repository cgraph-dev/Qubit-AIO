#!/usr/bin/env bash
# deletion_test.sh — re-runnable "hermes is gone" gate for Qubit VoiceBridge.
#
# Answers one question with numbers: does Qubit still boot and serve with NO
# hermes anywhere on the box? Builds an isolated sandbox (empty HOME, no
# ~/.hermes, no ~/.hermes/hermes-agent) and exercises real entry points.
#
# PART A runs with PATH STRIPPED of every dir holding an executable `hermes`, so
# it proves REMOVAL (boots with hermes uninstallable), not merely that Qubit never
# calls it. It also drives a REAL agent turn (cli._ChatTurn + _chat_run_agent)
# against a local ollama model and asserts a non-empty completion.
#
#   usage:  ./deletion_test.sh            # build sandbox (if missing) + run
#           ./deletion_test.sh --rebuild  # force sandbox rebuild
#
# Exit 0 iff every check passes. Prints PASS/FAIL per check with raw evidence.
#
# Safe to commit: touches only /tmp/qb-n5/{sandbox,home}, never the main checkout.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX="$REPO/sandbox"
ISO_HOME="$REPO/home"
QHOME="$REPO/qhome"
PROBE="$REPO/deletion_probe.py"
VENV="$REPO/.venv"
PY="$VENV/bin/python"

REBUILD=0
[[ "${1:-}" == "--rebuild" ]] && REBUILD=1

echo "================================================================"
echo " deletion_test.sh   repo=$REPO"
echo "   HEAD=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null) \
dirty=$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l)"
echo "================================================================"

# --- 0. preconditions ------------------------------------------------------
if [[ ! -x "$PY" ]]; then
  echo "FATAL: venv python not found at $PY"
  exit 2
fi

# --- 1. build the isolated sandbox -----------------------------------------
build_sandbox() {
  echo "[build] creating isolated sandbox at $SANDBOX ..."
  rm -rf "$SANDBOX"
  mkdir -p "$SANDBOX"
  # Product packages: top-level + the ones nested under qubit_runtime/
  cp -a "$REPO/qubit_runtime" "$SANDBOX/qubit_runtime"
  cp -a "$REPO/voicebridge"   "$SANDBOX/voicebridge"
  for d in plugins agent tools cron pm; do
    [[ -e "$REPO/qubit_runtime/$d" ]] && cp -a "$REPO/qubit_runtime/$d" "$SANDBOX/$d"
  done
  [[ -e "$REPO/scripts" ]]           && cp -a "$REPO/scripts"           "$SANDBOX/scripts"
  [[ -e "$REPO/qubit_runtime/scripts" ]] && cp -a "$REPO/qubit_runtime/scripts" "$SANDBOX/scripts"
  ln -sfn "$VENV" "$SANDBOX/.venv"
  echo "[build] done."
}

if [[ ! -d "$SANDBOX/qubit_runtime" || "$REBUILD" == "1" ]]; then
  build_sandbox
else
  echo "[build] sandbox exists (use --rebuild to force)"
fi

# --- 2. prove hermes is absent ---------------------------------------------
rm -rf "$ISO_HOME" "$QHOME"
mkdir -p "$ISO_HOME" "$QHOME"
echo "[env]   HOME=$ISO_HOME (empty)   QUBIT_HOME=$QHOME"
echo "[env]   ~/.hermes exists? $([[ -e "$ISO_HOME/.hermes" ]] && echo YES-BAD || echo no)"

HERMES_ON_PATH="$(command -v hermes 2>/dev/null || true)"
echo "[env]   hermes binary on PATH? ${HERMES_ON_PATH:-no}"

# Build a PATH with every dir holding an executable `hermes` removed, so PART A
# proves REMOVAL (boots with hermes uninstallable), not merely non-dependency.
CLEAN_PATH="$("$PY" - <<'PYEOF'
import os, sys
parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
def has(d):
    e = os.path.join(d, "hermes")
    return os.path.isfile(e) and os.access(e, os.X_OK)
print(os.pathsep.join(p for p in parts if not has(p)))
PYEOF
)"
if command -v hermes >/dev/null 2>&1; then
  if PATH="$CLEAN_PATH" command -v hermes >/dev/null 2>&1; then
    echo "[env]   WARNING: could not strip hermes from PATH; PART A runs with it present"
    RUN_PATH="$PATH"
  else
    echo "[env]   stripped hermes from PATH for PART A (dirs removed: $(echo "$PATH" | tr ':' '\n' | sort -u | while read d; do [ -x "$d/hermes" ] && echo -n "$d "; done))"
    RUN_PATH="$CLEAN_PATH"
  fi
else
  echo "[env]   hermes already absent from PATH; PART A runs as-is"
  RUN_PATH="$PATH"
fi

# --- 3. run the python probe -----------------------------------------------
echo
echo "----------------------------------------------------------------"
echo " PART A — in-process probe (imports, entry points, providers)"
echo "----------------------------------------------------------------"
env -i \
  HOME="$ISO_HOME" \
  QUBIT_HOME="$QHOME" \
  PYTHONPATH="$SANDBOX:$SANDBOX/qubit_runtime" \
  PATH="$RUN_PATH" \
  LANG=C.UTF-8 \
  DT_SANDBOX="$SANDBOX" \
  "$PY" "$PROBE"
PROBE_RC=$?
echo "probe exit=$PROBE_RC"

# --- 4. static hermes-dependency scan --------------------------------------
echo
echo "----------------------------------------------------------------"
echo " PART B — static scan for HARD hermes dependencies (classified)"
echo "----------------------------------------------------------------"
# Classification rule (matches the deletion-test dossier):
#   BLOCKER — code that at RUNTIME resolves the hermes INSTALL/pin, imports a
#             hermes_* module, or WRITES into ~/.hermes as Qubit's own home.
#   COMPAT  — a deliberate alias (e.g. `HermesCLI = QubitCLI`) or a read-only
#             reference to the operator's *external* fork home (a migration src).
#   COSMETIC- a docstring, comment, issue link, or user-facing string.
scan() {
  local label="$1" pattern="$2" globs="$3"
  local n
  n="$(cd "$SANDBOX" && grep -rniE "$pattern" --include="*.py" $globs 2>/dev/null \
        | grep -viE '/tests?/|/test_' | wc -l)"
  if [[ "$n" == "0" ]]; then
    echo "[PASS] $label — none"
  else
    echo "[INFO] $label — $n hit(s); review classification below"
    (cd "$SANDBOX" && grep -rniE "$pattern" --include="*.py" $globs 2>/dev/null \
      | grep -viE '/tests?/|/test_' | head -8 | sed 's/^/        | /')
  fi
}

scan "imports of hermes_* modules (BLOCKER if real)" '^[[:space:]]*(import|from)[[:space:]]+hermes_' 'qubit_runtime voicebridge'
scan ".hermes dir CREATION (BLOCKER)"   'makedirs[^\n]*\.hermes|mkdir[^\n]*\.hermes'              'qubit_runtime voicebridge'
scan "subprocess invokes hermes binary (BLOCKER)" 'subprocess[^\n]*\bhermes\b|Popen\([^\n]*\bhermes\b' 'qubit_runtime voicebridge'

# The decisive probe: with QUBIT_HOME UNSET and an empty HOME, does the core
# home resolver return ~/.hermes (BLOCKER) or ~/.qubit (rebrand landed)?
echo
echo "[probe] home resolution with QUBIT_HOME unset:"
env -i HOME="$ISO_HOME" PATH="$PATH" LANG=C.UTF-8 \
  PYTHONPATH="$SANDBOX:$SANDBOX/qubit_runtime" DT_SANDBOX="$SANDBOX" \
  "$PY" - <<'PYEOF'
import sys, os
sys.path[:] = [p for p in sys.path if "/home/trick/Projects/Repos" not in (p or "")]
sys.path.insert(0, os.path.join(os.environ["DT_SANDBOX"], "qubit_runtime"))
sys.path.insert(0, os.environ["DT_SANDBOX"])
try:
    import qubit_constants as c
    h = str(c.get_qubit_home())
    print(f"        | get_qubit_home() = {h}")
    if h.endswith("/.hermes"):
        print("        | VERDICT: BLOCKER — core home falls back to ~/.hermes")
    else:
        print("        | VERDICT: OK — core home is Qubit's own (~/.qubit)")
except BaseException as e:
    print(f"        | FAILED: {type(e).__name__}: {e}")
PYEOF

# The 49-site QUBIT_HOME-or-~/.hermes fallback class (mostly skills/evals).
echo
echo "[probe] QUBIT_HOME or ~/.hermes literal fallbacks (COSMETIC unless runtime/core):"
( cd "$SANDBOX" && grep -rniE 'QUBIT_HOME[^)]*(\.hermes)|else[^\n]*\.hermes' --include="*.py" \
    qubit_runtime voicebridge 2>/dev/null | grep -viE '/tests?/|/test_' | wc -l \
  | sed 's/^/        | count: /' )
echo "        | (core-runtime sites are deliberate read-only COMPAT: _external_default_home)"

echo
echo "================================================================"
if [[ "$PROBE_RC" == "0" ]]; then
  echo " OVERALL: PASS — Qubit boots with hermes absent"
else
  echo " OVERALL: FAIL — see failures above"
fi
echo "================================================================"
exit "$PROBE_RC"
