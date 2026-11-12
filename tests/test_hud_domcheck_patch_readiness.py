"""Readiness gate for the HUD drawer patch (`/tmp/f4_hud_domcheck.patch`).

WHY THIS EXISTS
---------------
`frontend/qubit-app/plugins/qubit-office/game/ui/hud-dock.domcheck.mjs` is the
owner's live edit file and carries two stale checks:

  * `tabs.length === 7` — a hardcoded tab count. Commit 7f35281e1 shipped the
    twelve restored panels (19 tabs), so the check failed on a HUD that was
    actually correct.
  * `rendered = tStats.length > 0 || tRows.length > 0` — the per-tab counter did
    not accept the HUD's own typed empty-state box (`.hud-state--empty`), so the
    Corrections tab was reported as "renders nothing" when it was answering
    honestly ("No correction recorded").

A verified patch fixes both (`/tmp/f4_hud_domcheck.patch`). The owner edits that
file live, so the patch can silently stop applying. This file is the
machine-checkable answer to "does it still apply?" — it never writes anything.

WHAT IT PROVES
--------------
  1. The two stale checks are present **verbatim**, byte for byte, as the exact
     line sequences the patch expects to remove (the `-` lines of each hunk).
     A hand edit that reflows, reindents or rewords them turns this red.
  2. Either the patch still applies cleanly (`git apply --check`), or it is
     already applied (every `-` line gone and every `+` line present) — the two
     states where the owner has nothing to do.

The failure message names which precondition broke, so the owner can tell at a
glance whether to re-cut the patch.

READ-ONLY, HERMETIC
-------------------
Parses the patch, reads the domcheck source, and shells out to
`git apply --check` (which itself writes nothing). No network, no node, no
dashboard build, no office-tree mutation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PATCH = Path("/tmp/f4_hud_domcheck.patch")
TARGET_REL = ("frontend/qubit-app/plugins/qubit-office/game/ui/"
              "hud-dock.domcheck.mjs")
TARGET = REPO / TARGET_REL

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# patch parsing — the patch file is the authority for what "stale" means
# ----------------------------------------------------------------------
def _hunks(text: str) -> list[tuple[str, int, list[str]]]:
    """(file, old_start, body) per hunk, body kept with its ` `, `-`, `+` prefix."""
    out: list[tuple[str, int, list[str]]] = []
    current = ""
    start = 0
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if body:
                out.append((current, start, body))
            body = []
            current = ""
            continue
        if line.startswith("--- "):
            # The file header, not a removed line: `git apply` accepts this
            # text, so a gate that rejected it would be wrong about the
            # pre-image. (Both `--- a/x` and `-x` start with `-`.)
            if body:
                out.append((current, start, body))
            body = []
            continue
        if line.startswith("+++ "):
            current = line[4:].strip()
            if current.startswith("b/"):
                current = current[2:]
            continue
        if line.startswith("@@ "):
            if body:
                out.append((current, start, body))
            # "@@ -220,8 +220,20 @@ ..." -> old start 220
            start = int(line.split(" ")[1].lstrip("-").split(",")[0])
            body = []
            continue
        if body is not None and (line[:1] in (" ", "-", "+") or line == ""):
            body.append(line)
    if body:
        out.append((current, start, body))
    return out


def _minus_plus(body: list[str]) -> tuple[list[str], list[str]]:
    minus = [ln[1:] for ln in body if ln.startswith("-")]
    plus = [ln[1:] for ln in body if ln.startswith("+")]
    return minus, plus


def _target_hunks() -> list[tuple[int, list[str], list[str], list[str]]]:
    """(old_start, full hunk body, removed_lines, added_lines) for our file."""
    if not PATCH.is_file():
        pytest.skip(f"{PATCH} is not present on this host")
    hunks = [h for h in _hunks(PATCH.read_text(encoding="utf-8"))
             if h[0] == TARGET_REL]
    assert hunks, (
        f"{PATCH} carries no hunk for {TARGET_REL} — the patch no longer "
        f"targets the file this gate guards")
    out = []
    for _file, start, body in hunks:
        minus, plus = _minus_plus(body)
        out.append((start, body, minus, plus))
    return out


def _source_lines() -> list[str]:
    assert TARGET.is_file(), (
        f"{TARGET_REL} is missing from the tree — the patch cannot apply and "
        f"the readiness gate has nothing to read")
    return TARGET.read_text(encoding="utf-8").splitlines()


def _hunk_matches(lines: list[str], start: int, body: list[str]) -> bool:
    """Does the hunk's PRE-IMAGE still exist at (or near) its old start?

    This is git's own test, not "are the removed lines contiguous": a `-` line
    and the next `-` line are usually separated by ` ` context lines, so the
    removed lines are NOT one block. Replay the whole hunk body from
    `old_start`, requiring every ` ` and `-` line to match the file — `+` lines
    are the post-image and are skipped.

    The window is generous (200 lines either side of the header's start) so a
    pure line-number shift from an unrelated edit above still reads as
    "pre-image present"; a real content edit does not.
    """
    old = [ln[1:] for ln in body if ln.startswith(" ") or ln.startswith("-")]
    if not old:
        return False
    lo = max(0, start - 1 - 200)
    hi = min(len(lines), start - 1 + 200 + len(old))
    n = len(old)
    for offset in range(lo, max(lo, hi - n) + 1):
        if lines[offset:offset + n] == old:
            return True
    return False


def _patch_applies_cleanly() -> bool:
    """`git apply --check` — writes nothing, only reports applicability."""
    proc = subprocess.run(
        ["git", "apply", "--check", str(PATCH)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)
    return proc.returncode == 0


# ----------------------------------------------------------------------
# 1 — the two stale checks are present verbatim, per patch hunk
# ----------------------------------------------------------------------
def test_the_patch_still_targets_the_hud_domcheck_only():
    """A patch that grew other files is not the patch the owner verified."""
    if not PATCH.is_file():
        pytest.skip(f"{PATCH} is not present on this host")
    files = {h[0] for h in _hunks(PATCH.read_text(encoding="utf-8"))}
    assert files == {TARGET_REL}, (
        f"the patch now touches {sorted(files)} — it is not the verified "
        f"single-file HUD patch")


def _fix_present(lines: list[str]) -> bool:
    """Is the patch's POST-image already in the file?

    The already-applied state is a success for the owner ("nothing to do"), so
    it must be distinguished from a genuine third state. Keyed on the three
    load-bearing marks the patch introduces, plus the hardcoded seven being
    gone — the same signals the replacement test asserts.
    """
    source = "\n".join(lines)
    return ("tabs.length > 0" in source
            and "tEmpty.length > 0" in source
            and "hud-tab-" in source
            and "tabs.length === 7" not in source)


def test_each_removed_block_is_present_verbatim_in_the_domcheck():
    """THE PRE-IMAGE ASSERTION.

    The whole hunk pre-image — every ` ` and `-` line, in order — must still be
    in the file at that position. That is precisely what `git apply` needs to
    find, so a green here means the patch applies; the failure names the hunk
    and its first removed line so the owner knows what drifted.

    If the pre-image is gone AND the fix is already in place, this is the
    "owner already applied it" state, not drift — it passes, so a green file
    always means "nothing to do". Only a genuine third state fails.
    """
    lines = _source_lines()
    hunks = _target_hunks()
    assert hunks, "no hunks to check"

    misses: list[str] = []
    for start, body, minus, _plus in hunks:
        if _hunk_matches(lines, start, body):
            continue
        first = minus[0] if minus else "<empty hunk>"
        misses.append(
            f"hunk @-{start} no longer matches. The patch expects to remove "
            f"{len(minus)} line(s) starting with:\n    {first!r}")
    if not misses:
        return                      # pre-image intact: the patch applies
    assert _fix_present(lines), (
        "the HUD domcheck no longer contains the pre-image the patch replaces, "
        "and the fix is not present either — the file is in a third state, so "
        "/tmp/f4_hud_domcheck.patch is stale and must be re-cut:\n\n"
        + "\n\n".join(misses))


def test_the_two_stale_checks_are_the_ones_named_in_the_task():
    """Guard the gate itself: the patch must still remove the hardcoded tab
    count AND the counter that ignores a typed empty state. If the patch is
    re-cut to fix something else, this gate would silently stop guarding."""
    removed = "\n".join(line for _s, _b, minus, _p in _target_hunks()
                        for line in minus)

    # Stale check A: the hardcoded seven.
    assert "tabs.length === 7" in removed, (
        "the patch no longer removes the hardcoded `tabs.length === 7` — "
        "stale check A changed shape; re-check what this gate guards")
    # Stale check B: the per-tab counter that only counts tiles and rows.
    assert "const rendered = tStats.length > 0 || tRows.length > 0;" in removed, (
        "the patch no longer removes `rendered = tStats.length > 0 || "
        "tRows.length > 0;` — stale check B changed shape")
    # And the replacements must still be the structural form.
    added = "\n".join(line for _s, _b, _minus, plus in _target_hunks()
                      for line in plus)
    assert "tabs.length > 0" in added, (
        "the patch no longer replaces the fixed count with a non-empty check")
    assert "tEmpty.length > 0" in added, (
        "the patch no longer accepts the HUD's typed empty-state box")
    assert 'hud-tab-' in added, (
        "the patch no longer asserts unique per-tab ids")


# ----------------------------------------------------------------------
# 2 — either the patch applies, or it is already applied
# ----------------------------------------------------------------------
def test_the_domcheck_is_in_the_state_the_patch_expects_or_already_patched():
    """The two actionable states. Anything else is a re-cut."""
    if not PATCH.is_file():
        pytest.skip(f"{PATCH} is not present on this host")
    lines = _source_lines()
    pre_image = all(_hunk_matches(lines, start, body)
                    for start, body, _minus, _plus in _target_hunks())

    if pre_image:
        # Pre-image intact: the patch MUST apply, or the owner is blocked.
        assert _patch_applies_cleanly(), (
            "the pre-image lines are present but `git apply --check` still "
            "fails — the patch's context lines (` ` lines, kept in the hunk "
            "header) drifted. Re-cut /tmp/f4_hud_domcheck.patch.")
        return

    # Pre-image gone: acceptable only if the fix already landed.
    assert _fix_present(lines), (
        "the patch's pre-image lines are gone from the domcheck, but the "
        "fix is NOT present either (no `tabs.length > 0`, no `tEmpty.length "
        "> 0`, or the hardcoded seven still lingers). The file was edited to "
        "some third state — re-cut /tmp/f4_hud_domcheck.patch against it.")


def _office_status() -> str:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--",
         "frontend/qubit-app/plugins/qubit-office",
         "frontend/qubit-web/public/plugins/qubit-office"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout


def test_the_gate_does_not_mutate_the_office_tree():
    """This gate is read-only by construction: prove it, don't assert it.

    Snapshot the office plugin's git status, run every check above (including
    `git apply --check`), then require the snapshot to be byte-identical. A
    gate that demanded an ALREADY-clean tree would fail while the owner holds
    legitimate uncommitted work in that very file — which is the normal state
    between edits — so the invariant is "this file changed nothing", not
    "nothing is changed".
    """
    before = _office_status()
    lines = _source_lines()
    for start, body, _minus, _plus in _target_hunks():
        _hunk_matches(lines, start, body)
    if PATCH.is_file():
        _patch_applies_cleanly()
    after = _office_status()
    assert after == before, (
        "the office plugin tree changed while the readiness gate ran. This "
        f"gate only reads; before:\n{before}\nafter:\n{after}")
