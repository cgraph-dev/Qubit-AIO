"""Concurrency harness: does ``vendor_habblaud.vendor()`` publish a CONSISTENT tree?

WHY THIS EXISTS
---------------
``docs/OPEN_THREADS.md`` §4.15 records an office-suite flake left at "no root
cause found": ``tests/test_office_vendor_habblaud.py`` intermittently failed
while hashing the vendored tree against ``PROVENANCE.json``. The root cause was
a NON-ATOMIC PUBLISH in ``scripts/vendor_habblaud.py::vendor()`` — the old
algorithm ``rmtree``'d the destination and wrote the record mid-assembly, so a
concurrent reader could hash a half-built tree. The fix (commit ``ca03150e8``)
makes publish ORDER the barrier: unlink the record, write every file, write the
record LAST via temp-file + ``os.replace``. ``docs/VENDOR-ATOMIC-PUBLISH-FIX.md``
records that fix but left two things undone:

1. **No repeatable harness** proving the fix (the evidence was a one-off probe);
2. a **residual hole** (~1 in 600k reads) stated in prose but never reproduced
   by anything runnable.

This module is that harness. It is hermetic, writes ONLY to a scratch
destination, and never touches the real served tree
(``frontend/qubit-app/plugins/qubit-office/habblaud/``).

WHAT IT MEASURES
----------------
It reconstructs a faithful fake upstream checkout from the CURRENT vendored tree
(the transforms are idempotent, so re-vendoring the vendored tree reproduces it
byte-for-byte — asserted below), points ``vendor()`` at a scratch destination,
and races N reader threads against a writer that re-vendors in a loop. Each read
performs exactly the contract ``--check`` performs:

    every file named in PROVENANCE.json exists with its recorded sha256.

* ``OK``      — record present, the whole tree matched it;
* ``BAD``     — record present, and the tree did NOT match it (with why);
* ``SKIPPED`` — no record on disk at that instant. This is the documented
  contract ("a reader either finds no record and can skip, or finds the complete
  new one"), so it is neither success nor failure.

FALSIFIABILITY
--------------
A harness that reports everything green is worthless. So it also implements the
EXACT OLD algorithm (``vendor_old`` — copied from ``ca03150e8^``) and runs the
SAME readers against it. The old code MUST measure as broken:

* OLD ``rmtree``'s the destination, so for most of every cycle there is no record
  (readers skip) — and in the window where a record DOES exist the tree is being
  torn down/rebuilt, so those reads are ``BAD``. Measured in the fix's own probe:
  0 of 90,818 reads consistent.
* FIXED keeps the tree in place and unlinks/publishes the record as the barrier,
  so essentially every read that finds a record finds a consistent one.

If this harness ever reports OLD as healthy, it is not measuring the thing —
``test_old_algorithm_measures_as_broken`` fails on exactly that.

RESIDUAL HOLE (characterised here, not closed)
----------------------------------------------
Files written by ``copytree(..., dirs_exist_ok=True)`` and the CSS/JS rewrite
loops are written IN PLACE. A reader that loaded generation N's record can have
the writer start generation N+1 (which unlinks the record and overwrites
name-stable files in place) before the reader finishes hashing: it then reads a
file mid-overwrite and sees a truncated/mismatched one. That window is
sub-per-mille; the honest measured number is in
``docs/VENDOR-CONCURRENCY-EVIDENCE.md``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VENDOR_SCRIPT = REPO / "scripts" / "vendor_habblaud.py"
VENDORED_TREE = (
    REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "habblaud"
)

pytestmark = pytest.mark.unit

#: Top-level entries of the vendored tree that come from the Vite build
#: (``<src>/dist/client``) rather than ``<src>/client/public``.
_STAGING_TOP_LEVEL = {"bundle", "index.html"}
#: Entries that are produced by ``vendor()`` itself, not copied from ``--src``.
_GENERATED_TOP_LEVEL = {"PROVENANCE.json", "LICENSE", "PROVENANCE.json.tmp",
                        "LICENSE.tmp"}


def _load_vendor_module():
    """Import ``scripts/vendor_habblaud.py`` as a module without importing pytest.

    Same mechanism the sibling ``tests/test_office_vendor_stream_rewrite.py``
    uses, so the harness exercises the REAL ``vendor()`` and cannot drift from it.
    """
    spec = importlib.util.spec_from_file_location("vendor_habblaud", VENDOR_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VENDOR = _load_vendor_module()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# fake upstream checkout
# ---------------------------------------------------------------------------


def build_fake_upstream(root: Path) -> Path:
    """Materialise a ``--src`` checkout from the CURRENT vendored tree.

    ``vendor(src, dest, skip_build=True)`` reads, under ``--src``:

      * ``<src>/dist/client/{bundle,index.html}``   — the Vite output;
      * ``<src>/client/public/*``                   — fixed-name assets + manifest;
      * ``<src>/LICENSE``                           — the MIT licence.

    The vendored tree is the build output AFTER the pure string transforms, and
    those transforms are idempotent (``rewrite_js``/``rewrite_css`` skip
    already-prefixed paths; ``pin_locale``/``fix_ticker_icon`` accept their
    already-pinned form), so re-vendoring it reproduces it byte-for-byte. That is
    what makes the current tree a faithful stand-in for a fresh build, and it is
    asserted by ``test_fake_upstream_round_trips`` below.

    Returns the ``src`` path.
    """
    src = root / "src"
    staging = src / "dist" / "client"
    public = src / "client" / "public"
    staging.mkdir(parents=True)
    public.mkdir(parents=True)

    shutil.copytree(VENDORED_TREE / "bundle", staging / "bundle")
    shutil.copy2(VENDORED_TREE / "index.html", staging / "index.html")

    for item in VENDORED_TREE.iterdir():
        if item.name in _STAGING_TOP_LEVEL or item.name in _GENERATED_TOP_LEVEL:
            continue
        target = public / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    shutil.copy2(VENDORED_TREE / "LICENSE", src / "LICENSE")
    return src


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------


@dataclass
class ReadStats:
    """Counters for one concurrency run."""

    reads: int = 0
    ok: int = 0
    bad: int = 0
    skipped: int = 0
    #: reason -> count, e.g. "missing", "mismatch", "unreadable", "undecodable"
    bad_reasons: Counter = field(default_factory=Counter)
    #: relative path -> times it was the cause (or first offender) of a BAD read
    bad_files: Counter = field(default_factory=Counter)

    @property
    def decided(self) -> int:
        """Reads that found a record and therefore reached a verdict."""
        return self.ok + self.bad

    def rate(self, n: int) -> float:
        """n as a fraction of ALL reads (including skips)."""
        return (n / self.reads) if self.reads else 0.0

    def decided_rate(self, n: int) -> float:
        """n as a fraction of DECIDED reads.

        THIS IS THE CONTRACT METRIC. ``SKIPPED`` is the documented, correct
        outcome when no record is on disk (a reader "can skip"), so it must not
        dilute the verdict. The question the fix answers is: *of the reads that
        found a record, how many found a consistent tree?*
        """
        return (n / self.decided) if self.decided else 0.0

    def as_dict(self) -> dict:
        return {
            "reads": self.reads,
            "ok": self.ok,
            "bad": self.bad,
            "skipped": self.skipped,
            "ok_rate": self.rate(self.ok),
            "bad_rate": self.rate(self.bad),
            "skip_rate": self.rate(self.skipped),
            "decided": self.decided,
            "decided_ok_rate": self.decided_rate(self.ok),
            "bad_reasons": dict(self.bad_reasons),
            "bad_files": dict(self.bad_files),
        }


def verify_once(dest: Path, stats: ReadStats) -> None:
    """One read of the contract ``--check`` performs, recorded into ``stats``.

    Reads the record ONCE (atomically-ish: one ``read_bytes``), then checks every
    file it names. No record on disk -> ``SKIPPED`` (the documented contract).
    """
    record_path = dest / "PROVENANCE.json"
    stats.reads += 1
    try:
        raw = record_path.read_bytes()
    except FileNotFoundError:
        stats.skipped += 1
        return
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        stats.bad += 1
        stats.bad_reasons["unreadable"] += 1
        stats.bad_files["PROVENANCE.json"] += 1
        return

    for entry in record.get("files", ()):
        target = dest / entry["path"]
        try:
            actual = _sha256(target)
        except FileNotFoundError:
            stats.bad += 1
            stats.bad_reasons["missing"] += 1
            stats.bad_files[entry["path"]] += 1
            return
        if actual != entry["sha256"]:
            stats.bad += 1
            stats.bad_reasons["mismatch"] += 1
            stats.bad_files[entry["path"]] += 1
            return
    stats.ok += 1


def _reader_loop(dest: Path, stop: threading.Event, stats: ReadStats) -> None:
    while not stop.is_set():
        verify_once(dest, stats)


# ---------------------------------------------------------------------------
# the OLD (pre-fix) algorithm — verbatim from commit ca03150e8^
# ---------------------------------------------------------------------------


def vendor_old(src: Path, dest: Path, *, write_delay: float = 0.0) -> dict:
    """The pre-fix ``vendor()`` body: rmtree, rebuild, record written mid-assembly.

    Copied from ``git show ca03150e8^:scripts/vendor_habblaud.py`` (the fix's
    parent), trimmed to the ``--skip-build`` path and to the file operations that
    matter for consistency. The two defects this reproduces:

      * ``shutil.rmtree(dest)`` DELETES the whole tree (and the record with it)
        at the start of every cycle — so the destination is genuinely absent for
        much of each cycle;
      * ``(dest / "PROVENANCE.json").write_text(...)`` truncates then writes, so a
        reader can catch an empty/partial record, and a reader that loaded the
        PREVIOUS record can have the tree torn down beneath it.

    ``write_delay`` optionally widens the tear-down window to make the failure
    reproducible on a fast machine; the default (0.0) is the faithful timing.
    """
    if not (src / "dist" / "client").is_dir():
        raise RuntimeError("--skip-build needs dist/client")

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if write_delay:
        time.sleep(write_delay)

    shutil.copytree(src / "dist" / "client" / "bundle", dest / "bundle")
    public = src / "client" / "public"
    if public.is_dir():
        for item in public.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    html = (src / "dist" / "client" / "index.html").read_text(encoding="utf-8")
    (dest / "index.html").write_text(VENDOR.rewrite_index(html), encoding="utf-8")

    for css in dest.rglob("*.css"):
        original = css.read_text(encoding="utf-8")
        rewritten = VENDOR.rewrite_css(original)
        if rewritten != original:
            css.write_text(rewritten, encoding="utf-8")
    for js in dest.rglob("*.js"):
        original = js.read_text(encoding="utf-8")
        rewritten = VENDOR.rewrite_js(original)
        if js.name.startswith("main-"):
            rewritten = VENDOR.pin_locale(rewritten)
        if js.name.startswith("app-"):
            rewritten = VENDOR.fix_ticker_icon(rewritten)
        if rewritten != original:
            js.write_text(rewritten, encoding="utf-8")
    for mf in dest.rglob("*.webmanifest"):
        original = mf.read_text(encoding="utf-8")
        rewritten = VENDOR.rewrite_manifest(VENDOR.rewrite_js(original))
        if rewritten != original:
            mf.write_text(rewritten, encoding="utf-8")

    licence = src / "LICENSE"
    if licence.is_file():
        shutil.copy2(licence, dest / "LICENSE")

    files = sorted(
        p for p in dest.rglob("*")
        if p.is_file() and p.name != "PROVENANCE.json"
    )
    record = {
        "source": "https://github.com/marmottajr/habblaud",
        "commit": "unknown",
        "licence": "MIT",
        "note": "(old algorithm, harness reproduction)",
        "files": [
            {"path": str(p.relative_to(dest)), "sha256": VENDOR.sha256(p),
             "bytes": p.stat().st_size}
            for p in files
        ],
        "total_bytes": sum(p.stat().st_size for p in files),
        "index_rewritten_from": "/-absolute to " + VENDOR.ASSET_PREFIX,
    }
    # NON-ATOMIC: truncate-then-write. A reader can catch partial JSON, and a
    # reader that already loaded the previous record can have it replaced under
    # it mid-read.
    (dest / "PROVENANCE.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    algorithm: str
    seconds: float
    writes: int
    stats: ReadStats

    @property
    def reads_per_second(self) -> float:
        return self.stats.reads / self.seconds if self.seconds else 0.0

    def report(self) -> str:
        s = self.stats
        return (
            f"[{self.algorithm}] {self.seconds:.2f}s  writes={self.writes}  "
            f"reads={s.reads} ({self.reads_per_second:,.0f}/s)  "
            f"OK={s.ok} ({s.decided_rate(s.ok)*100:.4f}% of decided)  "
            f"BAD={s.bad} ({s.decided_rate(s.bad)*100:.4f}% of decided)  "
            f"SKIPPED={s.skipped} ({s.rate(s.skipped)*100:.2f}% of all)  "
            f"decided={s.decided}  "
            f"reasons={dict(s.bad_reasons)}  "
            f"worst={s.bad_files.most_common(3)}"
        )


def run_concurrency(
    *,
    algorithm: str,
    src: Path,
    dest: Path,
    seconds: float,
    readers: int = 6,
    writer_dwell: float = 0.0,
) -> RunResult:
    """Race ``readers`` reader threads against a loop re-vendoring ``dest``.

    ``algorithm`` is ``"fixed"`` (the real ``vendor()``) or ``"old"``
    (``vendor_old``). Runs for ``seconds`` of wall clock; both threads stop on a
    shared ``Event``. Never writes anywhere but ``dest``.

    ``writer_dwell`` is the pause between re-vend cycles. The default (0.0) is a
    tight loop — maximum tear-down exposure per second, the harshest stress. A
    small dwell models the real cadence (vendoring is occasional, so the tree
    mostly sits published) and raises the number of reads that reach a verdict,
    which is what the contract metric divides by.
    """
    if algorithm == "fixed":
        write_fn = lambda: VENDOR.vendor(src, dest, skip_build=True)  # noqa: E731
    elif algorithm == "old":
        write_fn = lambda: vendor_old(src, dest)  # noqa: E731
    else:
        raise ValueError(f"unknown algorithm {algorithm!r}")

    stats = ReadStats()
    stop = threading.Event()
    writes = 0
    write_errors: list[BaseException] = []

    def writer() -> None:
        nonlocal writes
        while not stop.is_set():
            try:
                write_fn()
                writes += 1
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                write_errors.append(exc)
                stop.set()
                return
            if writer_dwell:
                # Interruptible sleep: wake immediately on stop.
                stop.wait(writer_dwell)

    # Prime the destination so readers have something to read before the first
    # write lands (and so "fixed" starts from a published, consistent tree).
    write_fn()
    writes += 1

    threads = [threading.Thread(target=_reader_loop, args=(dest, stop, stats),
                                daemon=True)
               for _ in range(readers)]
    writer_thread = threading.Thread(target=writer, daemon=True)
    for t in threads:
        t.start()
    writer_thread.start()

    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=5)
    writer_thread.join(timeout=30)

    assert not write_errors, f"writer raised: {write_errors!r}"
    return RunResult(algorithm=algorithm, seconds=seconds, writes=writes,
                     stats=stats)


# ---------------------------------------------------------------------------
# pytest tests
# ---------------------------------------------------------------------------


def test_fake_upstream_round_trips(tmp_path: Path) -> None:
    """The fake ``--src`` re-vendors to the CURRENT tree, byte-for-byte.

    This is the premise of the whole harness: the transforms are idempotent, so
    the vendored tree stands in for a fresh build. If this drifts (a transform
    stops being idempotent), the harness would be measuring a tree that is not
    the shipped one — fail loudly.
    """
    src = build_fake_upstream(tmp_path)
    dest = tmp_path / "dest"
    record = VENDOR.vendor(src, dest, skip_build=True)

    produced = {str(p.relative_to(dest)): _sha256(p)
                for p in dest.rglob("*") if p.is_file()
                and p.name != "PROVENANCE.json"}
    shipped = {str(p.relative_to(VENDORED_TREE)): _sha256(p)
               for p in VENDORED_TREE.rglob("*") if p.is_file()
               and p.name != "PROVENANCE.json"}

    assert set(produced) == set(shipped), (
        "re-vendoring the current tree did not reproduce its file set: "
        f"only-produced={sorted(set(produced) - set(shipped))[:5]} "
        f"only-shipped={sorted(set(shipped) - set(produced))[:5]}"
    )
    differing = sorted(k for k in produced if produced[k] != shipped[k])
    assert not differing, (
        f"re-vendoring changed bytes for {differing[:5]} — a transform is no "
        "longer idempotent and this harness no longer models the shipped tree"
    )
    assert len(record["files"]) == len(shipped)


def test_reader_contract_detects_a_tampered_tree(tmp_path: Path) -> None:
    """Prove the reader can FAIL: corrupt a file, the read must be BAD.

    Without this, a reader that always returned OK would make every other
    assertion here vacuous. This pins the detector, independent of threading.
    """
    src = build_fake_upstream(tmp_path)
    dest = tmp_path / "dest"
    VENDOR.vendor(src, dest, skip_build=True)

    good = ReadStats()
    verify_once(dest, good)
    assert good.ok == 1 and good.bad == 0

    victim = next(dest.rglob("*.woff"))
    victim.write_bytes(victim.read_bytes() + b"corrupt")

    bad = ReadStats()
    verify_once(dest, bad)
    assert bad.bad == 1 and bad.ok == 0
    assert bad.bad_reasons["mismatch"] == 1
    assert str(victim.relative_to(dest)) in bad.bad_files

    # A deleted file is likewise caught.
    victim.unlink()
    missing = ReadStats()
    verify_once(dest, missing)
    assert missing.bad == 1 and missing.bad_reasons["missing"] == 1


#: Dwell between re-vend cycles for the "realistic cadence" tests. A real
#: re-vendor is occasional, so the served tree sits PUBLISHED for long stretches;
#: without a dwell the writer unlinks the record almost immediately after
#: publishing it (measured: one cycle is ~0.4s and the record is present ~0.15%
#: of the time), which starves the reader of decided reads and makes any rate
#: meaningless. 1.0s gives ~800 decided reads in 10s on a 5-reader run.
_CADENCE_DWELL = 1.0


def test_old_algorithm_measures_as_broken_tight_loop(tmp_path: Path) -> None:
    """FALSIFICATION (tight loop): the pre-fix algorithm is never consistent.

    With the writer re-vending as fast as it can, ``rmtree`` deletes the whole
    destination at the start of every cycle, so either no record exists (skip) or
    the tree is being torn down/rebuilt (BAD). The fix's own probe measured
    **0 of 90,818 reads** consistent. If OLD ever reports a healthy OK rate here,
    the harness is broken, not the algorithm — which is the whole point of this
    test.
    """
    src = build_fake_upstream(tmp_path)
    result = run_concurrency(algorithm="old", src=src, dest=tmp_path / "dest",
                             seconds=8.0, writer_dwell=0.0)
    print(result.report())

    assert result.stats.reads > 50, "too few reads to be meaningful"
    assert result.stats.ok == 0, (
        "the OLD algorithm was reported CONSISTENT for "
        f"{result.stats.ok} reads — this harness is not measuring the defect: "
        f"{result.report()}"
    )
    assert result.stats.bad > 0, (
        f"the OLD algorithm produced no inconsistent read at all: {result.report()}"
    )


def test_fixed_algorithm_never_tears_the_tree_down(tmp_path: Path) -> None:
    """The real ``vendor()`` publishes a consistent tree under concurrent reads.

    At realistic cadence the fixed tree is overwhelmingly consistent; the only
    failures it may show are ``mismatch`` on a SINGLE name-stable file — the
    documented residual in-place overwrite (``copytree(dirs_exist_ok=True)`` /
    ``write_text``). It must NEVER show ``missing`` or ``unreadable``: those are
    the whole-tree failures that mean the publish barrier is broken, not the
    residual window.
    """
    src = build_fake_upstream(tmp_path)
    result = run_concurrency(algorithm="fixed", src=src, dest=tmp_path / "dest",
                             seconds=10.0, writer_dwell=_CADENCE_DWELL)
    print(result.report())

    assert result.stats.decided > 100, (
        "too few decided reads to characterise the fix (reader mostly skipped): "
        f"{result.report()}"
    )
    assert result.stats.ok > 0, (
        "not a single consistent read — the fix is not publishing a consistent "
        f"tree: {result.report()}"
    )
    assert result.stats.decided_rate(result.stats.ok) > 0.95, (
        f"fixed algorithm consistent in only "
        f"{result.stats.decided_rate(result.stats.ok)*100:.2f}% of decided "
        f"reads: {result.report()}"
    )
    # The publish barrier holds: no reader may ever find a recorded file GONE, or
    # the record itself unreadable. Only the single-file residual may appear.
    assert set(result.stats.bad_reasons) <= {"mismatch"}, (
        "the fixed algorithm produced a WHOLE-TREE failure (missing/unreadable) "
        f"— the publish barrier is broken, not the known residual: {result.report()}"
    )


def test_old_algorithm_tears_the_tree_down_at_realistic_cadence(
    tmp_path: Path,
) -> None:
    """The OLD algorithm's failure mode is structural: files go MISSING.

    At the same cadence as the fixed run, the old code's ``rmtree`` makes a
    reader — holding the previous generation's record — find recorded files that
    are simply GONE, and time out of the record existence window. This is the
    documented flake: a *correct* detection of an inconsistent tree. The fixed
    algorithm never produces this failure mode (see the sibling test).
    """
    src = build_fake_upstream(tmp_path)
    result = run_concurrency(algorithm="old", src=src, dest=tmp_path / "dest",
                             seconds=10.0, writer_dwell=_CADENCE_DWELL)
    print(result.report())

    assert result.stats.decided > 100, result.report()
    assert result.stats.bad > 0, (
        f"the OLD algorithm produced no inconsistent read at all: {result.report()}"
    )
    whole_tree = set(result.stats.bad_reasons) & {"missing", "unreadable"}
    assert whole_tree, (
        "the OLD algorithm's failures were expected to be whole-tree "
        "(missing/unreadable) from its rmtree; got only "
        f"{dict(result.stats.bad_reasons)}: {result.report()}"
    )


def test_fixed_beats_old_under_identical_readers(tmp_path: Path) -> None:
    """Same readers, same cadence, same budget — fixed must dominate.

    The headline claim, stated as a comparison so a regression in either
    direction is visible in one place: the fixed algorithm has both a higher
    consistent rate AND a lower inconsistent rate than the old one.
    """
    src_old = build_fake_upstream(tmp_path / "old")
    src_fixed = build_fake_upstream(tmp_path / "fixed")
    old = run_concurrency(algorithm="old", src=src_old,
                          dest=tmp_path / "dest-old", seconds=10.0,
                          writer_dwell=_CADENCE_DWELL)
    fixed = run_concurrency(algorithm="fixed", src=src_fixed,
                            dest=tmp_path / "dest-fixed", seconds=10.0,
                            writer_dwell=_CADENCE_DWELL)
    print(old.report())
    print(fixed.report())

    assert fixed.stats.ok > 0, fixed.report()
    assert (fixed.stats.decided_rate(fixed.stats.ok)
            > old.stats.decided_rate(old.stats.ok)), (
        f"the fix did not improve the consistent rate over the old algorithm: "
        f"old={old.report()} fixed={fixed.report()}"
    )
    assert (fixed.stats.decided_rate(fixed.stats.bad)
            < old.stats.decided_rate(old.stats.bad)), (
        f"the fix did not reduce the inconsistent rate vs the old algorithm: "
        f"old={old.report()} fixed={fixed.report()}"
    )


if __name__ == "__main__":  # pragma: no cover - manual soak runner
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--readers", type=int, default=6)
    ap.add_argument("--dwell", type=float, default=0.0,
                    help="pause between re-vend cycles (0 = tight loop)")
    ap.add_argument("--algorithm", choices=["fixed", "old", "both"], default="both")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    algs = ["old", "fixed"] if args.algorithm == "both" else [args.algorithm]
    payload = {"readers": args.readers, "seconds": args.seconds,
               "dwell": args.dwell, "runs": {}}
    with tempfile.TemporaryDirectory(prefix="vendor-concurrency-") as td:
        base = Path(td)
        for alg in algs:
            src = build_fake_upstream(base / alg)
            res = run_concurrency(algorithm=alg, src=src, dest=base / f"dest-{alg}",
                                  seconds=args.seconds, readers=args.readers,
                                  writer_dwell=args.dwell)
            print(res.report())
            payload["runs"][alg] = {
                "reads": res.stats.reads,
                "ok": res.stats.ok,
                "bad": res.stats.bad,
                "skipped": res.stats.skipped,
                "decided": res.stats.decided,
                "decided_ok_rate": res.stats.decided_rate(res.stats.ok),
                "writes": res.writes,
                "seconds": res.seconds,
                "bad_reasons": dict(res.stats.bad_reasons),
                "bad_files": dict(res.stats.bad_files),
            }
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.json_out}")
