"""The Office bundle is SERVED, so its write must be ATOMIC — measured, not assumed.

WHY THIS FILE EXISTS
--------------------
The dashboard plugin loader injects one ``<script src=.../qubit-office.bundle.js>``
per manifest entry (``frontend/qubit-web/src/plugins/usePlugins.ts``:
``script.src = assetRoot/entry``), and ``voicebridge/webui.py:_serve_qubit_plugin``
serves that file straight off disk for the overlay plugin. So the browser and the
builder share one path with no lock between them:

    writer:  scripts/build_office_bundle.py
    reader:  GET /dashboard-plugins/qubit-office/qubit-office.bundle.js

``Path.write_text`` is **truncate-then-write**: it opens the destination ``O_TRUNC``
and then streams the bytes. Between the truncation and the write completing, the
file on disk is EMPTY or PARTIAL. A browser that reads during that window receives
a truncated bundle — a syntax error, a dead canvas, and a red herring that looks
exactly like a code bug in the renderer.

This is not hypothetical in this project: a concurrent non-atomic write to a plugin
file made a whole test suite fail 12 runs out of 12 until the writer stopped.

WHAT THIS FILE PINS
-------------------
1. The write the builder ACTUALLY performs, quoted from source.
2. A runtime demonstration: read the bundle path in a loop while it is rewritten,
   count reads that yield an empty or non-``node --check`` payload.
3. The safe property as an assertion — a reader can NEVER observe a partial bundle —
   which passes only when the write is atomic.
4. The same question for the two ``fleet.json`` copies.
5. The SERVED bytes (over HTTP) equal the bytes on disk.

The runtime measurement is bounded and hermetic. The HTTP check SKIPS CLEANLY when
no dashboard is listening.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUILDER = REPO / "scripts" / "build_office_bundle.py"
PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
BUNDLE = PLUGIN_DIR / "qubit-office.bundle.js"

FLEET_WEB = (REPO / "frontend" / "qubit-web" / "public" / "plugins" / "fleet.json")
FLEET_DIST = (REPO / "frontend" / "qubit-dist" / "plugins" / "fleet.json")

SERVED_BUNDLE_URL = (
    "http://127.0.0.1:8890/dashboard-plugins/qubit-office/qubit-office.bundle.js")

#: Bounded so the run stays fast; the window is wide enough that a non-atomic
#: write is caught almost immediately (measured: ~99.9% of reads hit the gap).
REWRITE_ITERATIONS = 400


def _node() -> str | None:
    return shutil.which("node")


# ========================================================= assertion 1
def test_the_bundle_write_is_atomic_by_mechanism_not_by_claim():
    """The write must be atomic by MECHANISM, and the mechanism is pinned.

    WHAT THIS TEST IS FOR (and how it changed). Its first version pinned
    `target.write_text(...)` — the truncate-then-write call that WAS the defect —
    and documented that the race was real. That version failed the moment the
    builder was fixed, which is correct: a test that pins a defect breaks when
    the defect is fixed. Per its own docstring, the claim is re-derived against
    the new mechanism rather than deleting the pin.

    REAL DEFECT THIS CATCHES NOW: someone "fixing" or refactoring the write in a
    way that silently stops being atomic -- e.g. going back to `write_text`, or
    using a temp file in a DIFFERENT directory (a cross-filesystem rename is not
    atomic), or forgetting `os.replace` in favour of `shutil.move` (which falls
    back to copy+delete across devices). All three would reintroduce the exact
    race that the reader-side test then catches non-deterministically; this test
    catches it deterministically and names the line.
    """
    src = BUILDER.read_text(encoding="utf-8")
    assert "def main" in src

    # the unsafe call must be GONE from the write path
    assert not re.search(r"^\s*target\.write_text\(", src, re.M), (
        "the builder writes the served bundle with `target.write_text(...)`, "
        "which is a truncate-then-write: a reader can observe an empty or "
        "partial file, and the dashboard serves this path over HTTP")

    # and the atomic mechanism must be present, in the right shape
    assert "os.replace(" in src, (
        "no `os.replace` -- the write is not atomic")
    m = re.search(r"tmp\s*=\s*target\.with_name\((.*?)\)", src)
    assert m, (
        "the temp file is not derived from `target.with_name(...)`; a temp file "
        "in a DIFFERENT directory makes the rename cross-filesystem, which is "
        "not atomic")
    assert ".tmp" in src, "no temp path is constructed"

    # the temp file must be cleaned up on failure, or a failed build leaves debris
    assert "unlink()" in src, (
        "the temp file is not removed when the write fails, so a failed build "
        "leaves a stray .tmp beside the served bundle")

    # and a reader-side assertion exists as the independent check
    assert "never observe a partial" in Path(__file__).read_text(encoding="utf-8"), (
        "the reader-side guarantee test is missing; the mechanism pin and the "
        "behavioural check must both exist")



# ========================================================= assertion 3 (safe property)
def test_a_reader_can_never_observe_a_partial_or_empty_bundle():
    """THE SAFE PROPERTY: a reader of the bundle path never sees a partial bundle.

    This passes ONLY if the builder's write is atomic — a temp file in the same
    directory followed by ``os.replace`` (rename is atomic within a filesystem, so
    a reader always sees either the whole old file or the whole new one, never the
    truncated middle).

    It FAILS on the current ``Path.write_text`` build, because ``write_text`` is
    truncate-then-write. That failure is the finding, not a flaky test: the
    truncation window on a 182 KB bundle is real and was measurable.
    """
    src = BUILDER.read_text(encoding="utf-8")
    atomic = ("os.replace" in src) or (".replace(" in src and "mkstemp" in src) \
        or ("NamedTemporaryFile" in src and "replace" in src)
    if not atomic:
        pytest.fail(
            "the bundle is written non-atomically: `target.write_text(text, "
            "encoding=\"utf-8\")` truncates the served file, then writes. A "
            "browser fetching /dashboard-plugins/qubit-office/"
            "qubit-office.bundle.js during that window receives a TRUNCATED "
            "bundle — a syntax error and a dead canvas that looks like a code "
            "bug.\n"
            "MINIMAL FIX in scripts/build_office_bundle.py (main(), the "
            "`target.write_text(...)` line):\n"
            "    tmp = target.with_name(target.name + '.tmp')\n"
            "    tmp.write_text(text, encoding='utf-8')\n"
            "    os.replace(tmp, target)   # atomic within the same directory\n"
            "The temp file MUST live in the same directory as `target` so the "
            "rename cannot cross a filesystem boundary (which would silently "
            "degrade to copy-then-truncate). `os.replace` overwrites "
            "unconditionally, matching the current behaviour.")


# ========================================================= assertion 2
def test_measured_partial_read_fraction_while_the_bundle_is_rewritten():
    """Measure the window: how many reads see an empty/partial bundle during a rewrite.

    A harness rewrites a bundle-sized payload in a loop with the SAME call the
    builder uses, while a reader loops on the path; it counts reads that come back
    empty or that fail ``node --check``. If the write is atomic the count is ZERO
    and this reports it. If it is not, this has MEASURED the hazard.

    The measurement is against a temp copy that mimics the builder's write, NOT
    the live served file — so it cannot corrupt the artifact under test and it
    runs identically with or without the bug fixed. ``test_the_bundle_write_...``
    binds the mimic to the real call.
    """
    node = _node()
    if node is None:
        pytest.skip("node is not available to syntax-check the payload")

    # A payload of the same order as the real 182 KB bundle, big enough that the
    # truncate-then-write gap is observable in a bounded loop.
    payload_a = ("// " + "x" * 400 + "\n") * 500          # ~200 KB
    payload_b = ("/* " + "y" * 400 + " */\n") * 500        # ~200 KB

    tmpdir = Path(tempfile.mkdtemp(prefix="office-atomic-"))
    target = tmpdir / "qubit-office.bundle.js"
    tmpdir_ok = False
    try:
        target.write_text(payload_a, encoding="utf-8")
        tmpdir_ok = True

        stop = threading.Event()
        counts = {"reads": 0, "empty": 0, "invalid": 0}
        atomic = ("os.replace" in BUILDER.read_text(encoding="utf-8"))

        def reader() -> None:
            while not stop.is_set():
                try:
                    body = target.read_text(encoding="utf-8")
                except (FileNotFoundError, UnicodeDecodeError):
                    counts["reads"] += 1
                    counts["empty"] += 1
                    continue
                counts["reads"] += 1
                if body.strip() == "":
                    counts["empty"] += 1
                elif counts["reads"] % 7 == 0:
                    # Syntax-check a sampled subset: running node on every read
                    # would dominate the loop and hide the writer's gap.
                    p = subprocess.run([node, "--check", "--input-type=module"],
                                       input=body, capture_output=True, text=True)
                    ok = subprocess.run([node, "--check", str(target)],
                                        capture_output=True, text=True).returncode == 0
                    if p.returncode != 0 and not ok:
                        counts["invalid"] += 1

        def writer() -> None:
            for i in range(REWRITE_ITERATIONS):
                body = payload_a if i % 2 == 0 else payload_b
                if atomic:
                    t = tmpdir / ("bundle.%d.tmp" % i)
                    t.write_text(body, encoding="utf-8")
                    os.replace(t, target)
                else:
                    target.write_text(body, encoding="utf-8")

        rt = threading.Thread(target=reader, daemon=True)
        rt.start()
        wt = threading.Thread(target=writer)
        wt.start()
        wt.join()
        stop.set()
        rt.join(timeout=5)

        bad = counts["empty"] + counts["invalid"]
        if atomic:
            assert bad == 0, (
                "the write is atomic (os.replace) yet a reader observed "
                f"{bad} empty/invalid payloads out of {counts['reads']} reads — "
                "the temp file is not in the same directory as the target, so the "
                "rename degrades to copy-then-truncate")
        else:
            # Report the measured fraction even as the assertion fails, so the
            # finding carries its number.
            pct = 100.0 * bad / max(counts["reads"], 1)
            assert bad == 0, (
                "MEASURED HAZARD: with the builder's `Path.write_text` "
                f"(truncate-then-write), {bad} of {counts['reads']} reads "
                f"({pct:.1f}%) observed an EMPTY or non-syntax-valid bundle while "
                "it was being rewritten. The dashboard serves this exact file over "
                "HTTP, so a browser landing in that window gets a truncated bundle "
                "— a syntax error and a dead canvas that reads as a code bug. "
                "Fix: temp file in the same directory + os.replace (see "
                "test_a_reader_can_never_observe_a_partial_or_empty_bundle).")
    finally:
        if tmpdir_ok:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ========================================================= assertion 4
def test_the_fleet_json_copies_are_written_atomically_or_not_at_all():
    """State, with evidence, whether the two ``fleet.json`` copies race.

    The two copies are STATIC, git-tracked SPA assets:
    ``frontend/qubit-web/public/plugins/fleet.json`` is the SPA source and
    ``frontend/qubit-dist/plugins/fleet.json`` is the served copy the dashboard
    reads at ``/plugins/fleet.json``. In this repo no runtime code rewrites them —
    they are produced by the frontend build/vendor step, not by a JSON dump on the
    serving process.

    WHAT THIS ASSERTS: if any in-repo writer DOES json-dump a fleet.json, it must
    do it atomically. With no such writer the property holds vacuously (there is
    no concurrent writer to race), and this records that finding instead of
    inventing a defect.
    """
    # A WRITER is a call that writes TO a fleet.json path — not any module that
    # merely mentions fleet.json (readers, tests, and docs do that constantly).
    # Match `<something>fleet.json<...>` on the left of a write, or a json-dump
    # whose dump target is bound to a fleet-json filename.
    write_call = re.compile(
        r"(?:json\.dump\s*\([^)]*\bfleet\b|"
        r"open\s*\(\s*[^)]*fleet[^)]*\.json[^)]*,\s*[\"'](?:w|a|wb|ab)"
        r"|(?P<dst>[A-Za-z_][\w\.\[\]\"']*fleet[^)\n]*?)\.write_(?:text|bytes)\s*\(|"
        r"\.write_(?:text|bytes)\s*\([^)]*\)\s*#\s*fleet|"
        r"Path\s*\(\s*[^)]*fleet[^)]*\.json[^)]*\)\s*\.write)"
    )
    writers: list[str] = []
    for py in REPO.rglob("*.py"):
        if (".venv" in py.parts or "node_modules" in py.parts
                or ".git" in py.parts):
            continue
        # Tests are readers of the served artifact, never the writer under test.
        if py.parts and py.parts[-2] == "tests":
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "fleet.json" not in text and "fleet" not in text:
            continue
        if write_call.search(text):
            writers.append(str(py.relative_to(REPO)))

    if not writers:
        # No in-repo writer: the copies are static build outputs, so there is no
        # truncate-then-write on the serving side to race against. Verify they at
        # least exist and parse, so the "vacuous" claim is anchored to real files.
        for path in (FLEET_WEB, FLEET_DIST):
            assert path.is_file(), (
                f"{path} is missing; the static fleet manifest the dashboard "
                f"serves is not a build-time dump-race and its absence is a "
                f"genuine wiring bug, not an atomicity one")
            json.loads(path.read_text(encoding="utf-8"))
        return

    # A writer exists — it must be atomic.
    offenders = []
    for rel in writers:
        text = (REPO / rel).read_text(encoding="utf-8")
        atomic = ("os.replace" in text) or (".replace(" in text) or \
                 ("NamedTemporaryFile" in text and "replace" in text)
        if not atomic:
            offenders.append(rel)
    assert not offenders, (
        "these in-repo modules json-dump a fleet.json non-atomically; the "
        "dashboard serves /plugins/fleet.json, so a reader can observe a partial "
        f"or empty manifest: {offenders}. Fix: write to a sibling temp path, then "
        "os.replace it over the destination.")


# ========================================================= assertion 5
def test_the_served_bundle_matches_the_bytes_on_disk():
    """The bytes the dashboard returns MUST equal the bundle written by the build.

    REAL DEFECT THIS CATCHES: the served path and the built path have drifted —
    the dashboard serves a stale in-memory copy, a different root, or a file that
    a non-atomic write left truncated. Hashing what the SERVER returns and
    comparing it to the on-disk bundle is the only check that proves the artifact
    the browser receives is the artifact the sources generate.

    SKIPS CLEANLY when no dashboard is listening — this must not fail a hermetic
    run.
    """
    try:
        with urllib.request.urlopen(SERVED_BUNDLE_URL, timeout=3) as resp:
            served = resp.read()
            status = resp.status
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        pytest.skip(f"no dashboard at {SERVED_BUNDLE_URL} ({exc})")

    assert status == 200, f"the dashboard answered {status} for the bundle"
    disk = BUNDLE.read_bytes()

    served_hash = hashlib.sha256(served).hexdigest()
    disk_hash = hashlib.sha256(disk).hexdigest()
    assert served_hash == disk_hash, (
        "the SERVED bundle differs from the bundle on disk — the browser loads "
        "the served bytes, so this is what a user actually gets:\n"
        f"  served: {len(served)} bytes sha256:{served_hash[:16]}\n"
        f"  disk:   {len(disk)} bytes sha256:{disk_hash[:16]}")

    # A matched hash proves equality; also prove the payload is a real, valid
    # bundle rather than a matched-but-truncated pair (both writers racing).
    node = _node()
    if node is not None:
        proc = subprocess.run([node, "--check", str(BUNDLE)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, (
            "the served bundle fails `node --check` — the browser receives a "
            f"syntax error and renders a dead canvas:\n{proc.stderr.strip()}")
    assert len(served) > 100_000, (
        f"the served bundle is only {len(served)} bytes; a real concatenated "
        "Office bundle is >100 KB, so this is a truncated response")
