#!/usr/bin/env bash
# RETIREMENT GATE: the executable check for deleting ~/.hermes.
#
# ORDER IS FROM AGENT 5'S INDEPENDENCE_VERDICT (docs/FROZEN_TARGET.md §"Retirement
# checklist"). It CHECKS before it ACTS and REFUSES to proceed if any guard is
# unmet, because the one irreversible step (deleting ~/.hermes) is the very last
# one and every guard protects it.
#
# WHY EACH GUARD EXISTS
#   (a) The port must actually be at the frozen target's PRODUCTION parity. If it
#       is not, retiring hermes deletes the only copy of features Qubit has not
#       yet grown. Tests do not block -- production does (see the instrument).
#   (b) hermes-dashboard.service is currently enabled + active, with
#       Restart=always / RestartSec=5. Moving ~/.hermes aside makes its
#       EnvironmentFile load fail AND its ~/.local/bin/hermes shim exec a dead
#       path (exit 127). Left enabled, that is a restart-storm every 5 seconds,
#       forever. It MUST be disabled before anything moves.
#   (c) The four Qubit units are independent of ~/.hermes and must be the ones
#       left running: qubit-voicebridge, qubit-voice-typing, qubit-voice-orb,
#       qubit-gateway.
#   (d) Only after (a)(b)(c) pass does it PRINT the ordered commands. It does NOT
#       execute them unless given --apply.
#
# SAFETY: this script itself never moves, deletes, restarts, or stops anything
# without --apply, and even with --apply it stops BEFORE the delete so a human
# verifies the surfaces first. It refuses (exit 2) on any unmet guard.
#
# Usage:
#   scripts/retire_hermes_check.sh            # CHECK + print plan (dry, default)
#   scripts/retire_hermes_check.sh --apply    # run the ordered steps up to verify
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_DIR="${HOME}/.hermes"
HERMES_ASIDE="${HOME}/.hermes.retired-$(date +%Y%m%d-%H%M%S)"
HERMES_DASHBOARD="hermes-dashboard.service"
QUBIT_UNITS=(qubit-voicebridge qubit-voice-typing qubit-voice-orb qubit-gateway)
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

fail() { echo "REFUSE: $*" >&2; }
ok()   { echo "  [ok]   $*"; }
GUARD_FAILED=0
bad()  { echo "  [FAIL] $*"; GUARD_FAILED=1; }

echo "======================================================================"
echo "HERMES RETIREMENT GATE            mode: $( ((APPLY)) && echo APPLY || echo 'DRY (print-only)')"
echo "======================================================================"

# ---- Guard (a): production parity at the frozen target -----------------------
echo
echo "(a) production parity at frozen target ad12263a5ed4"
PROG_OUT="$(cd "$REPO" && .venv/bin/python scripts/frozen_target_progress.py 2>&1)"; PROG_RC=$?
PROD_LINE="$(printf '%s\n' "$PROG_OUT" | grep -E '^VERDICT:' | tail -1)"
echo "      ${PROD_LINE:-<no verdict line>}"
if [[ $PROG_RC -eq 0 ]] && printf '%s' "$PROD_LINE" | grep -q '^VERDICT: AT TARGET'; then
  ok "instrument reports AT TARGET for production"
else
  bad "instrument does not report 'AT TARGET' (rc=$PROG_RC): ${PROD_LINE:-no verdict}"
fi

# ---- Guard (b): hermes-dashboard must be DISABLED ----------------------------
echo
echo "(b) hermes-dashboard.service must be DISABLED before anything moves"
DASH_ENABLED="$(systemctl --user is-enabled "$HERMES_DASHBOARD" 2>&1)"
DASH_ACTIVE="$(systemctl --user is-active  "$HERMES_DASHBOARD" 2>&1)"
echo "      is-enabled=$DASH_ENABLED  is-active=$DASH_ACTIVE"
if [[ "$DASH_ENABLED" == "disabled" || "$DASH_ENABLED" == "masked" ]]; then
  ok "$HERMES_DASHBOARD is $DASH_ENABLED (cannot restart-storm)"
else
  bad "$HERMES_DASHBOARD is '$DASH_ENABLED' -- disable it first:"
  echo "           systemctl --user disable --now $HERMES_DASHBOARD"
fi

# ---- Guard (c): the four Qubit units enabled + active ------------------------
echo
echo "(c) four Qubit units must be enabled + active"
for u in "${QUBIT_UNITS[@]}"; do
  en="$(systemctl --user is-enabled "$u" 2>&1)"
  ac="$(systemctl --user is-active  "$u" 2>&1)"
  if [[ "$en" == "enabled" && "$ac" == "active" ]]; then
    ok "$u: enabled + active"
  else
    bad "$u: enabled=$en active=$ac"
  fi
done

echo
if ((GUARD_FAILED)); then
  echo "----------------------------------------------------------------------"
  echo "GATE CLOSED -- one or more guards failed. Nothing was moved or deleted."
  echo "Fix the FAIL lines above, then re-run. hermes stays untouched."
  exit 2
fi

# ---- All guards passed: print (and only with --apply, run) the plan ----------
echo "----------------------------------------------------------------------"
echo "GATE OPEN -- all guards passed. Ordered retirement steps:"
echo
echo "  STEP 1  mv '$HERMES_DIR' aside (NOT delete -- this IS the rollback copy)"
echo "            mv \"$HERMES_DIR\" \"$HERMES_ASIDE\""
echo
echo "  STEP 2  restart the four Qubit units:"
for u in "${QUBIT_UNITS[@]}"; do
  echo "            systemctl --user restart $u"
done
echo
echo "  STEP 3  verify EACH surface (manual, a human confirms each):"
echo "            - dashboard       (the Qubit dashboard loads, no hermes errors)"
echo "            - /qubit-chat     (a real turn returns)"
echo "            - /chat           (a real turn returns)"
echo "            - voice           (Right-Ctrl + wake word respond)"
echo
echo "  STEP 4  ONLY after all four surfaces verified: delete"
echo "            rm -rf \"$HERMES_ASIDE\""
echo
echo "  ROLLBACK (any time before STEP 4, or after):"
echo "            systemctl --user stop hermes-dashboard.service 2>/dev/null"
echo "            systemctl --user enable --now $HERMES_DASHBOARD  # only if you need it back"
echo "            mv \"$HERMES_ASIDE\" \"$HERMES_DIR\""
echo "            systemctl --user restart ${QUBIT_UNITS[*]}"
echo
echo "  LOG-WATCH (run after STEP 2, expect NO hermes 127/restart lines):"
echo "            journalctl --user -u $HERMES_DASHBOARD -n 50 --no-pager"
for u in "${QUBIT_UNITS[@]}"; do
  echo "            journalctl --user -u $u -n 30 --no-pager"
done
echo

if ((!APPLY)); then
  echo "DRY RUN complete. Re-run with --apply to execute STEPS 1-3"
  echo "(the delete in STEP 4 is NEVER automated -- a human runs it after verifying)."
  exit 0
fi

# ---- --apply: execute STEPS 1-3, then STOP before the delete -----------------
echo "----------------------------------------------------------------------"
echo "APPLY: executing STEPS 1-3 (delete is left to a human)."
set -e
mv "$HERMES_DIR" "$HERMES_ASIDE"
echo "  moved $HERMES_DIR -> $HERMES_ASIDE"
for u in "${QUBIT_UNITS[@]}"; do
  systemctl --user restart "$u"
  echo "  restarted $u"
done
echo
echo "STEPS 1-3 done. Now VERIFY the four surfaces manually. When satisfied:"
echo "    rm -rf \"$HERMES_ASIDE\""
echo "Rollback if anything is wrong: mv \"$HERMES_ASIDE\" \"$HERMES_DIR\""
exit 0
