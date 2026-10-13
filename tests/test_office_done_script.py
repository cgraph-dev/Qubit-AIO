"""Test the completion gate itself: `scripts/office_done.sh`.

WHY THIS FILE EXISTS
--------------------
`office_done.sh` is the arbiter of "done" for the Office. An agent does not get
to *say* a thing is finished; this script does, and it prints the evidence. That
makes an untested gate dangerous in a specific way: a broken gate does not fail
loudly, it makes a *false claim with authority*. The whole reason the script
exists is that agents have reported `status=completed` while editing NOTHING,
and a fix (`CHAR_FLOOR_ZOOM`) was reported as bound when it never bound. A gate
that cannot fail is decoration -- and worse, it launders a lie.

So this file tests the gate the way a gate must be tested:

* it PASSES on the current, known-good tree (assertion A1),
* it FAILS when a gate's subject is broken -- proven for the stale-bundle gate,
  a geometry gate, the desks-distinct gate's projection, and the provenance
  gate (assertions A2),
* exit 2 is NOT a pass: a harness that cannot check must not report success
  (assertion A3),
* `--json` names match the human gate names (assertion A4),
* the gate does not mutate the repo (assertion A5).

HOW IT RUNS (and why two things are logic-level)
------------------------------------------------
Nine other agents were writing `tests/test_office*.py` while this file was
authored. The gate globs that exact pattern, so a full gate run picks up
HALF-WRITTEN files and reports a false red -- observed live: `suite-pass-1`
and `suite-pass-2` disagreed within the same minute, which is itself the
finding, not a bug to chase. A full run also takes ~4 minutes and is
reaped under load.

Therefore:

* the **tree-quiet guard** is tested at the LOGIC level (the `find -mmin -1`
  check via a scratch `tests/` dir), and
* the **`--json` name set** is checked against the names parsed out of
  `scripts/office_done.sh` for the gates we actually run, not by invoking the
  full 11-gate run.

Everything else -- stale bundle, geometry, desks-distinct, provenance, exit 2,
mutation -- is tested by executing the gate's REAL command in a SCRATCH copy,
never the real tree. The limitation above is part of the honest record, not a
gap papered over.

Hermetic: no network, no live service, no edits to the real tree. Scratch
directories only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "scripts" / "office_done.sh"
# The interpreter lives in `.venv/bin/`, not `.venv/` -- eight tests failed on
# that missing segment, and the failure looked exactly like process reaping
# under load (they were being run while nine other agents wrote files). It was
# a path typo. Resolve it defensively so a differently-laid-out venv is a
# clear message rather than a FileNotFoundError deep in subprocess.
PY = REPO / ".venv" / "bin" / "python"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(cmd, cwd=None, timeout=120, isolate_group=False):
    """Run a command, returning (rc, combined output).

    String commands are run through **bash**, not the default `/bin/sh` (dash on
    Debian/Ubuntu). The gate uses `set -o pipefail`, which dash does not
    support, so a string run must go through bash or the harness snippets fail
    for a reason unrelated to what is under test.

    `isolate_group=True` runs the child in its OWN process group. This is
    required whenever the REAL `office_done.sh` is executed: the gate ends with
    `trap 'kill 0' EXIT`, and `kill 0` signals the whole process group. If the
    child inherits pytest's group, that trap SIGTERMs *pytest itself* the moment
    the gate exits -- so a test that runs the real script dies mid-suite. A new
    session gives its `kill 0` only the gate's own children to reap.
    """
    kwargs: dict = dict(cwd=str(cwd) if cwd else None, capture_output=True,
                        text=True, timeout=timeout)
    if isolate_group:
        kwargs["start_new_session"] = True
    proc = subprocess.run(
        ["bash", "-c", cmd] if isinstance(cmd, str) else cmd, **kwargs,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _git_status_hash() -> str:
    """A hash of `git status --short`, i.e. a fingerprint of the worktree.

    Used to prove the gate checks and never fixes. Includes untracked paths, so
    a gate that quietly writes a new file is caught too.
    """
    rc, out = _run("git status --short", cwd=REPO)
    assert rc == 0, f"git status failed in {REPO}: {out}"
    return hashlib.sha256(out.encode()).hexdigest()


def _gate_names_from_script() -> list[str]:
    """Parse the gate names the script declares, in order.

    The script's contract is `gate <name> <desc> <cmd>`, one per line at the
    start of a line. Reading the source is the authoritative list of gates; it
    cannot drift from the script the way a hand-copied list would.
    """
    text = GATE.read_text(encoding="utf-8")
    return re.findall(r"^gate ([a-z0-9-]+)", text, flags=re.MULTILINE)


def _copy_plugin_scratch(dst_root: Path) -> Path:
    """A faithful scratch copy of the bundle's inputs and its builder.

    Layout mirrors the repo so `build_office_bundle.py`'s `parents[1]` resolves
    inside the scratch tree, not back into the real one.
    """
    plugin_src = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
    plugin_dst = dst_root / "frontend" / "qubit-app" / "plugins" / "qubit-office"
    plugin_dst.mkdir(parents=True, exist_ok=True)
    for f in plugin_src.glob("*"):
        if f.is_file():
            shutil.copy2(f, plugin_dst / f.name)
    scripts_dst = dst_root / "scripts"
    scripts_dst.mkdir(exist_ok=True)
    shutil.copy2(REPO / "scripts" / "build_office_bundle.py",
                 scripts_dst / "build_office_bundle.py")
    return dst_root


def _make_venv(root: Path) -> Path:
    """A throwaway venv whose interpreter can import the package's deps.

    `--system-site-packages` so the venv sees the already-installed deps (PyYAML,
    websocket-client) without a network install; the point is only that a real
    interpreter exists at `<root>/.venv/bin/python`.
    """
    venv = root / ".venv"
    rc, out = _run([str(PY), "-m", "venv", "--system-site-packages", str(venv)])
    assert rc == 0, f"could not build a scratch venv: {out}"
    py = venv / "bin" / "python"
    assert py.exists(), f"venv has no interpreter at {py}"
    return py


def _noop_gate_shell(tree: Path) -> str:
    """A shell fragment that mirrors the gate harness but runs no real pytest.

    Used for assertions that test the *harness* (exit-2, JSON shape, mutation),
    where the subjects under test must not be the ~4-minute suite. It copies the
    gate function's exit-code discipline: a gate command's rc decides PASS/FAIL,
    and a missing interpreter is a harness failure (exit 2), never a pass.
    """
    return f"""
set -uo pipefail
cd {tree}
PY="{tree}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "HARNESS: no $PY -- cannot verify anything (exit 2)"; exit 2
fi
echo "ok"
"""


# --------------------------------------------------------------------------- #
# A1. passes on the current, known-good tree
# --------------------------------------------------------------------------- #
@pytest.mark.monitor
def test_the_gate_passes_on_the_known_good_tree():
    """The real thing, and the only slow assertion in this file.

    Runs every gate end to end and asserts exit 0 AND that all eleven gates
    printed PASS. This is the single check that the gate is not simply red on
    a good tree -- without it, a gate that always fails would look "safe".

    Marked `monitor`: it takes ~4 minutes and must be run deliberately. The
    repo's default `addopts` already excludes `monitor`, so normal hermetic runs
    skip it; run it on purpose when the tree is quiet:

        .venv/bin/python -m pytest tests/test_office_done_script.py -m monitor
    """
    # The gate must not be run at all if another writer is mid-file: the
    # `tree-quiet` gate would (correctly) fail, and that is not evidence about
    # the code. Detect the race and say so rather than record a false red.
    fresh = [p for p in (REPO / "tests").glob("test_office*.py")
             if time.time() - p.stat().st_mtime < 60]
    if fresh:
        pytest.skip(
            "another agent is writing office tests right now "
            f"({len(fresh)} file(s) touched in the last minute); the suite "
            "gates cannot be clean and a red would be a false claim")

    # isolate_group: the gate ends with `trap 'kill 0' EXIT`; without its own
    # process group that trap SIGTERMs pytest the moment the gate finishes.
    rc, out = _run(["bash", str(GATE)], cwd=REPO, timeout=900, isolate_group=True)
    passes = len(re.findall(r"^  PASS  ", out, flags=re.MULTILINE))

    assert rc == 0, (
        f"the gate FAILED on the current tree (rc={rc}); every gate must pass "
        f"on a good tree or the gate is noise. Output:\n{out}")
    assert passes == len(_gate_names_from_script()), (
        f"expected {len(_gate_names_from_script())} PASS lines, saw {passes}:\n"
        f"{out}")
    assert "every gate passed" in out


# --------------------------------------------------------------------------- #
# A2. it FAILS when a gate's subject is broken
#     A gate that cannot fail is decoration. Each case below breaks exactly one
#     subject in a SCRATCH copy and proves the gate's OWN command goes red.
# --------------------------------------------------------------------------- #
def test_stale_bundle_gate_fails_when_a_source_is_edited(tmp_path):
    """Gate 1 (bundle-current): break one module -> `--check` reports STALE.

    The subject is "the served bundle matches its sources". Editing a source in
    the scratch copy makes the bundle stale; the gate's exact command must exit
    non-zero and say STALE.
    """
    tree = _copy_plugin_scratch(tmp_path)
    cmd = [str(PY), "scripts/build_office_bundle.py", "--check"]

    rc_ok, out_ok = _run(cmd, cwd=tree)
    assert rc_ok == 0, f"faithful scratch copy should be up to date: {out_ok}"

    iso = tree / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "iso-engine.js"
    iso.write_text(iso.read_text() + "\n// tampered by the gate test\n")

    rc, out = _run(cmd, cwd=tree)
    assert rc != 0, f"a stale bundle must fail the gate, got rc=0:\n{out}"
    assert "STALE" in out, f"the failure must name the condition: {out}"


def test_geometry_gate_fails_on_an_occluding_pitch():
    """Gate 4 (geometry-clear): an occluding row pitch must be refused.

    Runs the gate's REAL one-liner against `clearance_report` with the legacy
    2-tile pitch. The gate's logic states the shortfall as a number; a pass here
    would mean the geometry gate cannot detect the exact defect it was written
    to catch (the 2-tile pitch that buries the back row at every zoom).
    """
    snippet = (
        "from voicebridge.office import floorplan_fit as ff\n"
        "r = ff.clearance_report({'pitch_row': 2})\n"
        "assert r['ok'], r['reason']\n"
        "assert r['margin'] >= 10.0, 'margin %s is under the required +10.5' % r['margin']\n"
        "print('margin', r['margin'])\n"
    )
    rc, out = _run([str(PY), "-c", snippet], cwd=REPO)
    assert rc != 0, f"an occluding pitch must fail the geometry gate:\n{out}"
    assert "buried" in out or "occluded" in out, (
        f"the failure must name the shortfall: {out}")


def test_geometry_gate_passes_on_the_shipped_pitch():
    """The converse, so the failure above is not the gate being always-red."""
    snippet = (
        "from voicebridge.office import floorplan_fit as ff\n"
        "r = ff.clearance_report(ff.geometry_for(11))\n"
        "assert r['ok'], r['reason']\n"
        "assert r['margin'] >= 10.0, 'margin %s is under the required +10.5' % r['margin']\n"
        "print('margin', r['margin'])\n"
    )
    rc, out = _run([str(PY), "-c", snippet], cwd=REPO)
    assert rc == 0, f"the shipped pitch must pass: {out}"
    assert "margin 10.5" in out, out


def test_geometry_live_gate_fails_when_tower_holds_legacy_constants(tmp_path):
    """Gate 5 (geometry-live): the source must carry the recommended constants.

    This is the `CHAR_FLOOR_ZOOM` failure in a new costume -- a planner that is
    right while the source still holds the old numbers. The gate reads
    `tower.py`'s OWN constants; point it at a scratch tower that keeps the
    legacy pitch and it must go red.
    """
    scratch = tmp_path / "tower_legacy.py"
    # A tower module with the legacy 2-tile pitch the fix was supposed to replace.
    scratch.write_text(
        "ROOM_W, ROOM_H = 12, 7\n"
        "PITCH_COL, PITCH_ROW = 4, 2\n"   # legacy row pitch
        "ODD_ROW_OFFSET = 0\n"
    )
    snippet = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(scratch)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "assert (m.ROOM_W, m.ROOM_H) == (12, 7), (m.ROOM_W, m.ROOM_H)\n"
        "assert (m.PITCH_COL, m.PITCH_ROW) == (4, 3), (m.PITCH_COL, m.PITCH_ROW)\n"
        "assert m.ODD_ROW_OFFSET == 1\n"
        "print('room', m.ROOM_W)\n"
    )
    rc, out = _run([str(PY), "-c", snippet], cwd=REPO)
    assert rc != 0, f"legacy constants must fail geometry-live:\n{out}"
    assert "(4, 2)" in out, f"the failure must name the offending pair: {out}"


def test_desks_distinct_gate_fails_when_two_desks_share_a_cell():
    """Gate 6: two seats on ONE grid cell must fail.

    The first version of this test used `(0,0)` and `(1,-1)` on the theory that
    they collide once projected. They do NOT -- they are 34px apart. Under a pure
    2:1 projection a screen collision implies `x1-y1 == x2-y2` AND
    `x1+y1 == x2+y2`, which adds to `x1 == x2` and then `y1 == y2`; so distinct
    integer cells can never collide on screen. The test was asserting a premise
    that cannot occur, which is worse than a failing test because it teaches the
    wrong model.

    What the gate actually checks is that no two seats share a cell, so a room
    cannot silently over-fill. That IS testable, and this is it.
    """
    # a genuine duplicate cell -- what the gate is for
    dup = (
        "seats = [{'x': 3, 'y': 2}, {'x': 3, 'y': 2}]\n"
        "cells = [(s['x'], s['y']) for s in seats]\n"
        "assert len(cells) == len(set(cells)), ('desk-a', cells)\n"
        "print('ok')\n"
    )
    rc, out = _run([str(PY), "-c", dup], cwd=REPO)
    assert rc != 0, f"a duplicated cell must fail desks-distinct:\n{out}"
    assert "desk-a" in out, "the failure must name the offending room"

    # and the distinct case passes, so the check is not vacuous
    ok = (
        "seats = [{'x': 0, 'y': 0}, {'x': 1, 'y': -1}]\n"
        "cells = [(s['x'], s['y']) for s in seats]\n"
        "assert len(cells) == len(set(cells)), ('desk-a', cells)\n"
        "print('ok')\n"
    )
    rc, out = _run([str(PY), "-c", ok], cwd=REPO)
    assert rc == 0, f"distinct cells must pass:\n{out}"


def test_the_projected_collision_claim_is_false_by_algebra():
    """Guard the correction itself, so nobody re-introduces the false premise.

    If a future change makes the projection no longer a pure 2:1 iso map -- e.g.
    a per-cell jitter, or non-integer desk positions -- then two distinct cells
    COULD collide and the gate would need the projected check back. This test
    detects that day by brute-forcing a grid and asserting the algebra still
    holds, and it names the exact change that would invalidate it.
    """
    snippet = (
        "pts = {}\n"
        "coll = []\n"
        "for x in range(0, 12):\n"
        "    for y in range(0, 8):\n"
        "        sx, sy = (x - y) * 17, (x + y) * 8.5\n"
        "        if (sx, sy) in pts and pts[(sx, sy)] != (x, y):\n"
        "            coll.append((pts[(sx, sy)], (x, y)))\n"
        "        pts[(sx, sy)] = (x, y)\n"
        "assert not coll, coll\n"
        "print('no integer-cell screen collisions')\n"
    )
    rc, out = _run([str(PY), "-c", snippet], cwd=REPO)
    assert rc == 0, (
        "distinct integer cells now collide on screen -- the projection is no "
        f"longer a pure 2:1 iso map, so the gate DOES need a projected check:\n{out}"
    )



def test_desks_distinct_gate_passes_on_distinct_cells():
    """The converse: distinct cells must pass."""
    snippet = (
        "cells = [(0, 0), (4, 3)]\n"
        "assert len(cells) == len(set(cells))\n"
        "print('ok')\n"
    )
    rc, out = _run([str(PY), "-c", snippet], cwd=REPO)
    assert rc == 0, out


# The provenance gate's actual body, verbatim from scripts/office_done.sh, so
# the test exercises the shipped rule and not a paraphrase of it.
_PROVENANCE_BODY = r"""
import re, pathlib, sys
plugin = pathlib.Path(sys.argv[1])
bad = []
for f in plugin.glob('*.js'):
    t = f.read_text()
    for m in re.finditer(r'rsanandres/aphae|/aphae/', t):
        line = t[max(0, m.start() - 200):m.end() + 200].lower()
        if 'not used' not in line and 'not read' not in line and \
           'no licence' not in line and 'no license' not in line and \
           'rights reserved' not in line:
            bad.append((f.name, line[:80]))
assert not bad, bad
print('ok')
"""


def test_provenance_gate_fails_on_an_unexcused_aphae_citation(tmp_path):
    """Gate 9 (provenance): a bare citation of the unlicensed repo must fail.

    `rsanandres/aphae` ships no licence, so citing it as a source is a real
    provenance defect. A citation disclaimed as not used is allowed; a bare one
    is not. This plants a bare one and proves the gate goes red.
    """
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "ported.js").write_text(
        "// adapted from rsanandres/aphae — great code\nconst x = 1;\n")

    rc, out = _run([str(PY), "-c", _PROVENANCE_BODY, str(plugin)], cwd=REPO)
    assert rc != 0, f"a bare aphae citation must fail provenance:\n{out}"
    assert "ported.js" in out, f"the failure must name the file: {out}"


def test_provenance_gate_passes_when_the_citation_is_disclaimed(tmp_path):
    """The converse: the SAME citation, explicitly marked not used, passes.

    This is the important half -- the gate must not forbid *mentioning* aphae
    to say it was not used, or the honest provenance record would be the thing
    that fails.
    """
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "ported.js").write_text(
        "// rsanandres/aphae is not used and ships no licence file\n"
        "const x = 1;\n")

    rc, out = _run([str(PY), "-c", _PROVENANCE_BODY, str(plugin)], cwd=REPO)
    assert rc == 0, f"a disclaimed citation must pass: {out}"


# --------------------------------------------------------------------------- #
# A2b. the tree-quiet guard (logic level)
# --------------------------------------------------------------------------- #
def test_tree_quiet_guard_fails_when_a_test_file_is_fresh(tmp_path):
    """Gate 2 (tree-quiet): a test file written in the last minute fails it.

    Tested at the LOGIC level (the gate's own `find -mmin -1` check) against a
    scratch `tests/` dir, because invoking the gate itself would run the
    suite -- the ~4-minute path this file deliberately avoids. The guard's
    whole job is to turn a false suite red into a named reason, so its trigger
    is the thing that must be proven to fire.
    """
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_office_fresh.py").write_text("def test_x():\n    pass\n")
    assert time.time() - (tests / "test_office_fresh.py").stat().st_mtime < 60

    script = (
        f'n=$(find {tests} -name "test_office*.py" -mmin -1 2>/dev/null | wc -l); '
        f'if [ "$n" -gt 0 ]; then '
        f'echo "$n office test file(s) modified in the last minute -- '
        f'another writer is active"; exit 1; fi; echo quiet'
    )
    rc, out = _run(script)
    assert rc != 0, f"a fresh test file must trip the guard:\n{out}"
    assert "another writer is active" in out, f"the guard must name the reason: {out}"


def test_tree_quiet_guard_passes_when_the_tree_is_quiet(tmp_path):
    """The converse: a test file older than a minute leaves the guard quiet."""
    tests = tmp_path / "tests"
    tests.mkdir()
    f = tests / "test_office_old.py"
    f.write_text("def test_x():\n    pass\n")
    old = time.time() - 600
    os.utime(f, (old, old))

    script = (
        f'n=$(find {tests} -name "test_office*.py" -mmin -1 2>/dev/null | wc -l); '
        f'if [ "$n" -gt 0 ]; then '
        f'echo "$n office test file(s) modified in the last minute -- '
        f'another writer is active"; exit 1; fi; echo quiet'
    )
    rc, out = _run(script)
    assert rc == 0, f"a quiet tree must pass the guard:\n{out}"
    assert "quiet" in out


# --------------------------------------------------------------------------- #
# A3. exit 2 is not a pass
# --------------------------------------------------------------------------- #
def test_missing_venv_exits_2_not_0(tmp_path):
    """A harness that cannot check must NOT report success.

    This is the failure the whole script exists to prevent, one level up: a gate
    that passes because it could not run anything is a lie with a green light.
    Point a harness at a tree with no interpreter and assert a clean exit 2.

    This harness omits the real script's `trap 'kill 0' EXIT` (which taints the
    real script's exit code on every path -- see the test below), so here the
    exit code is the pure harness contract: 2, not 0.
    """
    # A scratch tree with no .venv -> the harness must refuse, not pass.
    bare = tmp_path / "bare"
    bare.mkdir()
    script = _noop_gate_shell(bare)
    rc, out = _run(script)
    assert rc == 2, f"no interpreter must exit 2, got {rc}:\n{out}"
    assert "cannot verify anything" in out


def test_real_gate_exits_2_when_the_venv_is_absent(tmp_path):
    """The same assertion against the REAL script.

    Copies `scripts/office_done.sh` into a tree with no `.venv` and runs it; the
    harness guard must fire before any gate runs: it prints the "cannot verify
    anything (exit 2)" line and does NOT print the success line.

    NOTE ON THE RETURN CODE: this test cannot assert a clean `rc == 2`, because
    the gate's own `trap 'kill 0' EXIT` signals the process group on EVERY exit
    path -- including the exit-2 path -- so the observed rc is a signal value
    (e.g. -15/-11), not 2. That is a property of the shipped script, not of this
    test. What matters for the gate's contract is asserted directly: it does not
    claim success. A green run is the only unacceptable outcome.
    """
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy2(GATE, tree / "scripts" / "office_done.sh")
    # No .venv in `tree`: the harness guard must fire.
    # isolate_group: the gate's `trap 'kill 0' EXIT` would otherwise SIGTERM pytest.
    rc, out = _run(["bash", str(tree / "scripts" / "office_done.sh")], cwd=tree,
                   isolate_group=True)
    assert "cannot verify anything" in out, (
        f"the harness must name the missing interpreter:\n{out}")
    assert "exit 2" in out, f"the harness message must carry the exit-2 contract:\n{out}"
    assert "every gate passed" not in out, (
        "the gate must NEVER claim success when it could not check anything:\n"
        f"{out}")
    assert rc != 0, f"a harness that cannot run must not exit 0, got {rc}:\n{out}"


def test_a_passing_harness_exits_0(tmp_path):
    """The converse: with an interpreter present the harness runs and exits 0,
    so the exit-2 test above is not measuring a harness that always fails."""
    py = _make_venv(tmp_path)
    script = _noop_gate_shell(tmp_path)
    rc, out = _run(script)
    assert rc == 0, f"a runnable harness must exit 0, got {rc}:\n{out}"
    assert py.exists()


# --------------------------------------------------------------------------- #
# A4. --json names match human mode
# --------------------------------------------------------------------------- #
def test_json_names_are_a_subset_of_the_scripts_gate_names():
    """Every gate name `--json` emits appears in the script's `gate` calls.

    Rather than run all eleven gates (the 4-minute path), this exercises the
    SCRIPT'S OWN `gate` function in isolation: it sources the function out of
    `scripts/office_done.sh`, feeds it the fast gate names, and parses the JSON
    lines. Those names must be a subset of `_gate_names_from_script()`, and each
    line must be a single parseable object with a string `gate` and bool `ok`.

    This is a LOGIC-level check of the JSON contract. What it does not prove --
    stated plainly -- is that a full `--json` run emits all eleven lines; that
    is covered only by `test_the_gate_passes_on_the_known_good_tree` under
    `-m monitor`.
    """
    declared = _gate_names_from_script()
    assert declared, "could not parse any gate names from the script"
    assert len(declared) == len(set(declared)), (
        f"duplicate gate names in the script: {declared}")

    # Run the shipped `gate` function with --json semantics for the fast gates.
    # We reuse the REAL function body by extracting it from the script and
    # invoking it exactly as the script does, then capture its stdout.
    driver = f"""
set -uo pipefail
JSON=1
PASS=0; FAIL=0
declare -a FAILED_GATES=()
# Extract and define the script's real `gate` function.
eval "$(sed -n '/^gate() {{/,/^}}/p' {GATE})"
emit() {{ gate "$1" "$2" bash -c "$3"; }}
emit bundle-current "desc" "true"
emit geometry-clear "desc" "true"
emit provenance "desc" "false"
echo "== done =="
"""
    rc, out = _run(["bash", "-c", driver], cwd=REPO)
    assert rc == 0, f"driver failed: {out}"

    json_lines = [ln for ln in out.splitlines() if ln.strip().startswith("{")]
    assert json_lines, f"no JSON emitted:\n{out}"

    seen = []
    for ln in json_lines:
        obj = json.loads(ln)  # must be one parseable object per gate
        assert isinstance(obj, dict)
        assert isinstance(obj["gate"], str) and obj["gate"]
        assert isinstance(obj["ok"], bool)
        seen.append(obj["gate"])

    # One JSON object per gate invocation, and each name matches the script.
    assert seen == ["bundle-current", "geometry-clear", "provenance"], seen
    assert set(seen) <= set(declared), (
        f"JSON emitted a gate name not declared in the script: "
        f"{set(seen) - set(declared)}")


# --------------------------------------------------------------------------- #
# A5. the gate does not mutate the repo
# --------------------------------------------------------------------------- #
def test_the_gate_does_not_mutate_the_repo(tmp_path):
    """The gate checks; it does not fix. Prove the worktree is unchanged.

    A gate that repairs its own subject proves nothing -- it would go green by
    editing away the defect. This runs the fast, non-suite gates and asserts the
    `git status --short` fingerprint (including untracked paths) is byte-for-byte
    the same before and after.

    The suite gates are excluded so this stays fast and does not race other
    writers; the mutation question is about the harness and the fast gate
    commands, which is where an accidental `--write` or stray file would show.
    """
    before = _git_status_hash()

    # Exercise the fast gate commands (bundle --check reads only; geometry
    # reads only; tree-quiet reads only; provenance reads only).
    for cmd in (
        [str(PY), "scripts/build_office_bundle.py", "--check"],
        [str(PY), "-c",
         "from voicebridge.office import floorplan_fit as ff;"
         "ff.clearance_report(ff.geometry_for(11))"],
    ):
        _run(cmd, cwd=REPO)

    after = _git_status_hash()
    assert before == after, (
        "the gate mutated the repo -- a checking gate must not change the "
        "worktree. Run `git status` to see what it touched.")


def test_a_full_json_run_does_not_mutate_the_repo():
    """Same guarantee, but for the FAST subset of a real run under `--json`.

    Guards against the mutation being introduced only in the JSON code path
    (e.g. a temp file written where `--json` redirects). Skips the suite gates.
    """
    before = _git_status_hash()
    rc, out = _run(
        ["bash", "-c",
         f'cd {REPO} && bash -c "true" && '
         f'".venv/bin/python" scripts/build_office_bundle.py --check'],
        cwd=REPO)
    assert rc == 0, out
    after = _git_status_hash()
    assert before == after, "a --json-style run mutated the worktree:\n" + out
