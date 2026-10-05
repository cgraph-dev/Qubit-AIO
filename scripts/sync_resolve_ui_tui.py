#!/usr/bin/env python3
"""Resolve the ui-tui conflicts left by the sync by taking upstream's incoming
change while preserving Qubit-local content.

The sync (scripts/sync_upstream.py) leaves a file at PRE-sync state whenever its
3-way merge conflicts. These 19 TypeScript files then break the synced TUI and
its tests (missing i18n call sites + Qubit-local de-branding carried only in the
pre-sync literals).

Resolution rule per conflict hunk:
  * take the NEW side (upstream's incoming change, mostly i18n refactor);
  * restore the Qubit-local spelling for any identifier the sync FALSELY
    translated. Ground truth: Qubit's own (unsynced) ui-tui tree uses
    HERMES_TUI_* everywhere and exports launchHermesCommand from externalCli.ts;
    the sync's context-free HERMES_ -> QUBIT_ / hermes -> qubit rules invented
    QUBIT_TUI_* and launchQubitCommand.
  * preserve genuine Qubit-local de-branding: where upstream moved a user-facing
    string into the i18n catalog, the catalog entry still carries upstream's
    "Nous"/"Hermes" wording; de-brand it to the value Qubit's pre-sync literal
    used (manifest docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json
    content_debrand.phrase_map).

Read-only against upstream; writes only the listed Qubit files + catalog leaves.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_upstream as su  # noqa: E402

# --- false renames the sync's identifier rules invented ---------------------
# These rewrite QUBIT_* (falsely translated) back to the canonical HERMES_*.
FALSE_RENAME_FIX = {
    "QUBIT_TUI_": "HERMES_TUI_",
    "launchQubitCommand": "launchHermesCommand",
    "~/.qubit/": "~/.hermes/",
}

# --- per-file literal preservation applied AFTER taking the NEW side ---------
# NEW kept these as plain literals where Qubit's local tree deliberately
# re-branded them.
PER_FILE = {
    "ui-tui/src/components/branding.tsx": [
        # NEW comment says the tiny tier is the bare brand name, untranslated.
        ("const TAG_TINY = 'Nous Research'", "const TAG_TINY = 'Qubit Labs'"),
    ],
    "ui-tui/src/__tests__/createSlashHandler.test.ts": [
        # Local test asserts the Qubit-branded page title literal.
        ("t('slashCmd.core.status.pageTitle')",
         "t('slashCmd.core.status.pageTitle')"),
    ],
}

# --- catalog de-branding ----------------------------------------------------
# catalog-relative key -> (upstream text, Qubit-local text). The catalog leaves
# are the new home of user-facing prose; keeping upstream's wording would
# regress Qubit branding, so restore Qubit's pre-sync (CUR) literal values.
CATALOG_DEBRAND = {
    "content.ts": [
        ("Hermes needs a model provider before the TUI can start a session.",
         "Qubit needs a model provider before the TUI can start a session."),
    ],
    "userMessages.ts": [
        ("Hermes stopped unexpectedly — restarting and reopening your chat (the reply in progress was lost).",
         "Qubit stopped unexpectedly — restarting and reopening your chat (the reply in progress was lost)."),
        ("Hermes stopped unexpectedly · restarting…",
         "Qubit stopped unexpectedly · restarting…"),
        ("Connection to Hermes lost — reconnecting and reopening your chat…",
         "Connection to Qubit lost — reconnecting and reopening your chat…"),
        ("Hermes stopped and could not be restarted. Your chat is saved.",
         "Qubit stopped and could not be restarted. Your chat is saved."),
        ("Hermes keeps trying to reconnect in the background",
         "Qubit keeps trying to reconnect in the background"),
        ("Hermes stopped · /logs for details", "Qubit stopped · /logs for details"),
        ("Hermes is taking longer than usual to start.",
         "Qubit is taking longer than usual to start."),
        ("Something went wrong inside Hermes · /logs for details",
         "Something went wrong inside Qubit · /logs for details"),
        ("Something went wrong inside Hermes (${what}) · /logs for details",
         "Something went wrong inside Qubit (${what}) · /logs for details"),
        ("The terminal UI and the Hermes backend are out of sync",
         "The terminal UI and the Qubit backend are out of sync"),
        ("Hermes is not connected right now, so that was not sent.",
         "Qubit is not connected right now, so that was not sent."),
        ("Hermes did not answer within ${secs}s. Try again",
         "Qubit did not answer within ${secs}s. Try again"),
        ("The disk is full, so Hermes could not save the turn",
         "The disk is full, so Qubit could not save the turn"),
        ("Hermes hit an internal error while running this turn",
         "Qubit hit an internal error while running this turn"),
    ],
    "billing.ts": [
        ("a billing admin can turn it on from the portal's Hermes Agent page.",
         "a billing admin can turn it on from the portal's Qubit Agent page."),
        ("By confirming, you allow Nous Research to charge your card.",
         "By confirming, you allow Qubit Labs to charge your card."),
        ("By confirming, you authorize Nous Research to charge",
         "By confirming, you authorize Qubit Labs to charge"),
    ],
    "connection.ts": [
        ("That answer did not reach Hermes. Try again.",
         "That answer did not reach Qubit. Try again."),
        ("Hermes could not start that again. Try again.",
         "Qubit could not start that again. Try again."),
        ("your learned skills and memories will start mapping out here as you use Hermes.",
         "your learned skills and memories will start mapping out here as you use Qubit."),
    ],
    "subscription.ts": [
        ("manage your subscription on the Nous portal.",
         "manage your subscription on the Qubit portal."),
    ],
    "slashCmd/session.ts": [
        ("nousBalanceTitle: 'Nous balance'", "nousBalanceTitle: 'Qubit balance'"),
        ("Not logged into Nous Portal — run /portal to log in, then /subscription.",
         "Not logged into Qubit Labs — run /portal to log in, then /subscription."),
        ("a billing admin can turn it on from the portal's Hermes Agent page.",
         "a billing admin can turn it on from the portal's Qubit Agent page."),
        ("💳 Not logged into Nous Portal — run /portal to log in, then /topup.",
         "💳 Not logged into Qubit Labs — run /portal to log in, then /topup."),
    ],
    "slashCmd/core.ts": [
        ("hermesTag: (index: string) => `Hermes #${index}`",
         "hermesTag: (index: string) => `Qubit #${index}`"),
    ],
    "libText.ts": [
        ("but image clipboard and local screenshot paths still depend on the machine running Hermes",
         "but image clipboard and local screenshot paths still depend on the machine running Qubit"),
        ("nousDetail: 'Your Nous credit balance is exhausted — top up to keep going.'",
         "nousDetail: 'Your Qubit credit balance is exhausted — top up to keep going.'"),
        ("nousTitle: 'Out of Nous credits'", "nousTitle: 'Out of Qubit credits'"),
    ],
    "chatBits.ts": [
        ("tagFull: 'Nous Research · Messenger of the Digital Gods'",
         "tagFull: 'Qubit Labs · Messenger of the Digital Gods'"),
    ],
}

CATALOG_DIR = su.QR / "ui-tui" / "src" / "i18n" / "en"

UI_TUI_FILES = [
    "ui-tui/src/app/slash/commands/core.ts",
    "ui-tui/src/app/slash/commands/debug.ts",
    "ui-tui/src/app/slash/commands/session.ts",
    "ui-tui/src/app/slash/commands/setup.ts",
    "ui-tui/src/app/slash/commands/subscription.ts",
    "ui-tui/src/app/slash/commands/topup.ts",
    "ui-tui/src/app/userMessages.ts",
    "ui-tui/src/components/billingOverlay.tsx",
    "ui-tui/src/components/branding.tsx",
    "ui-tui/src/components/connectionSetupOverlay.tsx",
    "ui-tui/src/components/journey.tsx",
    "ui-tui/src/components/modelPicker.tsx",
    "ui-tui/src/components/subscriptionOverlay.tsx",
    "ui-tui/src/config/env.ts",
    "ui-tui/src/content/setup.ts",
    "ui-tui/src/entry.tsx",
    "ui-tui/src/lib/billingDialog.ts",
    "ui-tui/src/lib/terminalParity.ts",
    "ui-tui/src/__tests__/createSlashHandler.test.ts",
]


def merged_text(up_path: str) -> tuple[str, int]:
    """Re-run the sync merge; take the NEW side of every conflict hunk."""
    qpath = su.route(up_path)
    target = su.QR / qpath
    raw_base = su.git_show(su.PIN_SHA, up_path)
    raw_new = su.git_show(su.BASE_SHA, up_path)
    base = raw_base if raw_base is not None else ""
    t_base, _ = su.translate(base)
    t_new, _ = su.translate(raw_new)
    cur = target.read_text(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "cur").write_text(cur)
        (d / "base").write_text(t_base)
        (d / "new").write_text(t_new)
        r = subprocess.run(
            ["git", "merge-file", "-p", "--diff3",
             str(d / "cur"), str(d / "base"), str(d / "new")],
            capture_output=True, text=True, check=False)
    merged = r.stdout
    if r.returncode != 0:
        # take the NEW side (3rd group) of each diff3 conflict
        merged = re.sub(
            r"<<<<<<<[^\n]*\n(.*?)\|\|\|\|\|\|\|[^\n]*\n(.*?)=======\n(.*?)>>>>>>>[^\n]*\n",
            lambda m: m.group(3), merged, flags=re.S)
    return merged, merged.count("<<<<<<<")


def fix_false_renames(text: str) -> str:
    for bad, good in FALSE_RENAME_FIX.items():
        text = text.replace(bad, good)
    return text


def main() -> int:
    apply = "--apply" in sys.argv
    rc = 0
    print("== ui-tui source files ==")
    for f in UI_TUI_FILES:
        merged, hunks = merged_text(f)
        assert hunks == 0, f"{f}: {hunks} conflict hunks unresolved"
        merged = fix_false_renames(merged)
        for bad, good in PER_FILE.get(f, ()):
            merged = merged.replace(bad, good)
        # sanity: no leftover conflict markers, no false-rename residue
        assert "<<<<<<<" not in merged, f"{f}: leftover markers"
        if "QUBIT_TUI_" in merged or "launchQubitCommand" in merged:
            print(f"  WARN {f}: residual false rename")
            rc = 1
        if apply:
            (su.QR / su.route(f)).write_text(merged, encoding="utf-8")
            print(f"  wrote {su.route(f)}")
        else:
            print(f"  ok    {su.route(f)}")

    print("== i18n catalog de-branding ==")
    for rel, edits in CATALOG_DEBRAND.items():
        p = CATALOG_DIR / rel
        if not p.exists():
            print(f"  MISSING {p}")
            rc = 1
            continue
        text = p.read_text(encoding="utf-8")
        for bad, good in edits:
            if bad not in text:
                print(f"  WARN {rel}: pattern absent -> {bad[:60]!r}")
                rc = 1
            text = text.replace(bad, good)
        if apply:
            p.write_text(text, encoding="utf-8")
            print(f"  wrote catalog/{rel}")
        else:
            print(f"  ok    catalog/{rel}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
