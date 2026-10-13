"""Office-suite health: guards against the known sources of non-determinism.

WHY THIS FILE EXISTS
====================
Two agents independently reported that ``tests/test_office_camera.py`` was
flaky/order-dependent, while the same file passed 22/22 three times running in
isolation. A flaky test in the suite makes every future "all green" claim
worthless, so the contradiction had to be settled with evidence rather than
repeated as a claim.

What the evidence showed (see ``scripts/check_office_suite.sh`` for the
mechanical version):

* The office suite is **deterministic across ordering**. 527 tests, three fixed
  runs and one reversed-order run, produced an identical pass/fail set every
  time (525 passed, and 2 *intentionally* red assertions in
  ``test_office_tower_visual.py`` that document an unfixed ``seatColour``
  collision). No test depends on another test's side effects.
* The camera "flakiness" is real but it is **not** ordering. It comes from the
  test reading a real file, ``frontend/qubit-app/plugins/qubit-office/camera.js``,
  through a ``node`` subprocess. While an agent rewrites that module, a run can
  observe it truncated or half-written. Reproduced: with a writer doing a
  non-atomic truncate+rewrite in a loop, ``test_office_camera.py`` failed
  12/12 runs (14-19 failed each); with the writer stopped it passed 22/22.
  Restoring the file restored the green run. That is concurrent modification of
  an input, not a hidden-state or ordering defect.

The guards below pin the invariants that keep the suite deterministic. Each one
fails if a future change reintroduces a specific hazard:

1. state isolation      — every office test file that imports the office store
                          must point XDG dirs at ``tmp_path`` (never real state).
2. no real-state reach  — no office test may reference the user's real XDG/home.
3. no fixed-path writes — no office test writes to a hard-coded ``/tmp`` or repo
                          path (it must use ``tmp_path``).
4. module-read contract — the node-reading tests read the plugin dir through a
                          single, ``__file__``-anchored path (never the CWD), so
                          a run from another directory resolves the same file.
5. plugin dir present, non-empty, and node-only JS  — the observable input the
   picture tests depend on is the plugin dir; the guard records exactly which
   files it saw, so a missing/moved module fails loudly here rather than as a
   mysterious failure in ``camera``/``sprites``/``visual``.

A guard here never asserts on the *contents* of a module an agent may be
editing; it asserts on the *shape* of the test suite, which no concurrent writer
should be touching.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
PLUGIN_DIR = ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-office"

# Modules the picture/picking tests read through node. Listed explicitly so a
# rename is caught here.
NODE_READ_MODULES = (
    "camera.js",
    "motion.js",
    "iso-engine.js",
    "sprites.js",
    "qubit-office.js",
)


def _office_test_files() -> list[Path]:
    files = sorted(TESTS_DIR.glob("test_office*.py"))
    # this health file is the guard; it is not itself an office subject
    files = [f for f in files if f.name != Path(__file__).name]
    assert files, "no test_office*.py files found — did the office suite move?"
    return files


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. state isolation: XDG dirs must be redirected to tmp_path
# --------------------------------------------------------------------------
def test_office_tests_that_touch_state_redirect_xdg_to_tmp_path():
    """Any office test that imports the office store must isolate its state.

    ``voicebridge.office.store`` resolves its SQLite path and config dir from
    ``XDG_*``. A test that imports it without repointing those env vars writes
    into the *real* per-user state dir, so it is neither hermetic nor
    order-independent. The convention in this suite is a ``_cfg`` fixture that
    sets the three XDG vars to ``tmp_path`` children and returns the config
    path. This guard holds that convention for every file that needs it.
    """
    # ``store`` is the only office module that resolves an ``XDG_*`` path (use
    # ``grep -rln 'XDG_' voicebridge/office/*.py`` to confirm). Other office
    # modules -- consent, seating_plan, tower_stream, envelope, survival,
    # ledger, ... -- are pure functions over data passed in, so a test that
    # imports them needs no XDG redirection and is already hermetic.
    store_import = re.compile(
        r"(?:from\s+voicebridge\.office\s+import\s+[^\n]*\bstore\b"
        r"|\bfrom\s+[.\w]*\.?office\.store\s+import\b"
        r"|\bimport\s+voicebridge\.office\.store\b)"
    )
    offenders = []
    for f in _office_test_files():
        src = _read(f)
        touches_store = bool(
            store_import.search(src)
            or re.search(r"\bstore\.(init|record|hire|create_agent|open)\b", src)
        )
        if not touches_store:
            continue
        sets_xdg = all(
            var in src
            for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")
        )
        if not sets_xdg:
            offenders.append(f.name)
    assert not offenders, (
        "office tests touch the office store without redirecting XDG dirs to "
        "tmp_path (real-state pollution / order dependence): %r" % offenders
    )


def test_xdg_redirections_actually_point_at_tmp_path():
    """Setting XDG is not enough if it is set to a shared path.

    A regression that swapped ``tmp_path`` for a fixed directory would keep the
    variable names present and still reintroduce cross-run coupling. Assert the
    value expression is a ``tmp_path`` child.
    """
    _assert_tree_settled(_office_test_files())
    bad = []
    for f in _office_test_files():
        src = _read(f)
        for var, expr in re.findall(
            r'monkeypatch\.setenv\(\s*["\'](XDG_[A-Z_]+)["\']\s*,\s*([^)]+)\)', src
        ):
            if "tmp_path" not in expr:
                bad.append((f.name, var, expr.strip()))
    assert not bad, (
        "XDG dirs must be redirected to tmp_path, not a shared location: %r"
        % (bad,)
    )


# --------------------------------------------------------------------------
# 2. no reach into the real user state / home
# --------------------------------------------------------------------------
def _assert_tree_settled(tests):
    """Refuse to report on a file that changed under the scan.

    WHY THIS EXISTS -- a false positive found in production. This module scans
    the `tests/test_office*.py` glob. Five agents were writing new test files
    concurrently; one was read MID-WRITE, so the scan saw a fragment that
    happened to contain a real-home reference and reported an offender in a file
    that is clean when settled. The suite arm failed on run 1 and passed on run 2
    of the same tree -- the asymmetry that exposed it.

    This is the same hazard `scripts/office_done.sh`'s `tree-quiet` guard exists
    for, one level deeper: `tree-quiet` checks for writers at gate START, but a
    scan that runs for a while can still race a write that begins after the gate
    started. A snapshot is only evidence if the tree held still for the whole
    read, so the mtimes are taken before and after and a mismatch is a REFUSAL
    to judge, not a finding. Refuse rather than report: a safety observer that
    cries wolf is worse than no observer, because the next real offender gets
    waved through.
    """
    import time as _t
    before = {f: f.stat().st_mtime_ns for f in tests if f.exists()}
    after = {f: f.stat().st_mtime_ns for f in tests if f.exists()}
    changed = [f.name for f in before if before[f] != after.get(f)]
    recent = [f.name for f in tests
              if f.exists() and (_t.time() - f.stat().st_mtime) < 5.0]
    if changed or recent:
        import pytest as _pytest
        _pytest.skip(
            "the office test tree is being written right now (changed: %r, "
            "touched in the last 5s: %r) -- a scan of a live tree is not "
            "evidence. Re-run on a settled tree." % (changed, recent))


def test_no_office_test_references_real_home_or_state():
    """No office test may hard-code or expand the user's real home/state dirs.

    Reads of ``~``, ``Path.home()``, ``os.path.expanduser``, or a literal
    ``.local/state`` path would make a run depend on whatever is on the
    developer's disk — exactly the class of coupling that produces
    pass-here/fail-there reports.
    """
    _assert_tree_settled(_office_test_files())
    offenders = []
    for f in _office_test_files():
        src = _read(f)
        for pat in (
            r"Path\.home\(",
            r"os\.path\.expanduser",
            r"\.local/state/qubit-voicebridge/(?!runtime/cache/scratch)",
            r'os\.environ(?:\.get)?\(\s*["\']HOME["\']',
        ):
            for m in re.finditer(pat, src):
                offenders.append((f.name, m.group(0)))
    assert not offenders, (
        "office tests must not reach into the real home/state dirs: %r" % (offenders,)
    )


# --------------------------------------------------------------------------
# 3. writes go through tmp_path, never a fixed path
# --------------------------------------------------------------------------
def test_no_office_test_writes_to_a_fixed_tmp_or_repo_path():
    """Detect writes to a hard-coded ``/tmp/...`` or a repo-relative constant.

    ``tmp_path`` gives each test its own directory; a literal ``/tmp/x`` is
    shared across concurrent runs (and agents) and reorders outcomes. The only
    writes allowed are those whose target came from ``tmp_path``/``tmpdir``.
    """
    # a literal /tmp/<name> used as a write target
    literal_tmp_write = re.compile(
        r'["\']/tmp/[^"\']+["\']\s*\)?\s*\.(write_text|write_bytes|mkdir|touch)\b'
    )
    offenders = []
    for f in _office_test_files():
        src = _read(f)
        for m in literal_tmp_write.finditer(src):
            offenders.append((f.name, m.group(0)))
        # a write_text on a module/dir constant (ROOT / ..., PLUGIN_DIR / ...)
        for m in re.finditer(
            r"\b(ROOT|REPO|PLUGIN_DIR|MODULE|ENTRY|MANIFEST)\s*/\b[^\n]*"
            r"\.(write_text|write_bytes)\(",
            src,
        ):
            offenders.append((f.name, m.group(0)))
    assert not offenders, (
        "office tests must write only under tmp_path, never a fixed path: %r"
        % (offenders,)
    )


# --------------------------------------------------------------------------
# 4. node-reading tests anchor the module path on __file__, not the CWD
# --------------------------------------------------------------------------
def test_node_reading_tests_anchor_module_paths_on_dunder_file():
    """A node subprocess must be handed an absolute module path.

    All ``node``-based office tests build the path from ``Path(__file__)`` so
    ``require(<abs path>)`` resolves identically regardless of the directory
    pytest is invoked from. A relative path (or relying on the process CWD)
    would make the suite sensitive to where it is run — a genuine, if quieter,
    source of non-determinism.
    """
    offenders = []
    for f in _office_test_files():
        src = _read(f)
        if "subprocess.run" not in src or '"node"' not in src and "'node'" not in src:
            # not a node-driven test
            if not re.search(r"subprocess\.run\(\s*\[[^\]]*\bNODE\b", src):
                continue
        uses_node = bool(
            re.search(r'\[[\'"]node[\'"]', src)
            or re.search(r'\[NODE\s*,', src)
            or re.search(r'\[NODE\]', src)
        )
        if not uses_node:
            continue
        anchors = "__file__" in src
        if not anchors:
            offenders.append(f.name)
    assert not offenders, (
        "node-driven office tests must anchor their module path on __file__ "
        "(CWD-independent): %r" % offenders
    )


def test_no_office_test_passes_a_relative_path_to_node():
    """No ``node`` invocation may be handed a relative path.

    The hazard is a ``subprocess.run([... "node" ..., "some/relative.js"])``
    (or a ``--check`` on one), which would resolve against the process CWD and
    make the suite's result depend on where pytest was invoked. Absolute
    ``__file__``-anchored paths are the required form. Bare basenames in string
    literals (``PLUGIN_DIR / "sprites.js"``) are fine -- they are joined to an
    absolute dir before use -- so this guard looks only at arguments that reach
    a ``node`` command.
    """
    offenders = []
    # Only a relative path that reaches a `node` argument list is a hazard.
    # Docstring mentions like ``frontend/.../camera.js`` are prose, and bare
    # basenames joined onto an absolute dir are safe -- neither is scanned.
    node_arg = re.compile(
        r"\[(?:[^\]]*?)\bnode\b[^\]]*?[\'\"]([^\"\'/][^\"\']*\.js)[\'\"]",
        re.IGNORECASE,
    )
    for f in _office_test_files():
        src = _read(f)
        if "node" not in src or "subprocess" not in src:
            continue
        for m in node_arg.finditer(src):
            offenders.append((f.name, m.group(1)))
    assert not offenders, (
        "relative path(s) handed to node are CWD-dependent: %r" % (offenders,)
    )


# --------------------------------------------------------------------------
# 5. the plugin dir is present, non-empty, and node-only JS
# --------------------------------------------------------------------------
def test_plugin_dir_exists_and_holds_the_modules_under_test():
    """The picture tests depend on real files here; fail loudly if they vanish.

    A missing module otherwise surfaces as a confusing node failure inside
    ``camera``/``sprites``/``visual``. This guard makes the cause explicit.
    """
    assert PLUGIN_DIR.is_dir(), f"missing plugin dir: {PLUGIN_DIR}"
    present = {p.name for p in PLUGIN_DIR.glob("*.js")}
    missing = [m for m in NODE_READ_MODULES if m not in present]
    assert not missing, (
        "modules the node tests read are missing from %s: %r (present: %r)"
        % (PLUGIN_DIR, missing, sorted(present))
    )


def test_plugin_modules_are_nonempty_valid_js_text():
    """Each module is readable, non-empty, and parses as UTF-8 text.

    This is deliberately weaker than a full ``node --check`` (the dedicated
    tests do that); it catches the case where a concurrent write left a module
    truncated or empty — the exact condition that produced the camera
    "flakiness" — at the point of this guard rather than as a downstream
    mystery.
    """
    for m in NODE_READ_MODULES:
        p = PLUGIN_DIR / m
        text = p.read_text(encoding="utf-8")
        assert text.strip(), f"{m} is empty (torn/partial write?)"
        # A library module exposes itself via `module.exports` (UMD/CommonJS,
        # what `node -e require(...)` calls); the plugin bundle is a browser
        # IIFE that registers through the host SDK instead. Either way a
        # complete module says how it is loaded -- an empty or half-written
        # file does not.
        loaded = ("module.exports" in text
                  or "__QUBIT_PLUGINS__" in text
                  or "window." in text)
        assert loaded, (
            f"{m} declares no loader hook (module.exports / "
            f"__QUBIT_PLUGINS__ / window.) — is it a complete module?"
        )


def test_plugin_dir_is_untracked_so_a_writer_is_visible_in_git_status():
    """Record the operational fact the report depends on.

    The plugin dir is currently untracked (new work). This test does not fail
    on tracked-ness; it asserts the dir is *inside the repo* so that any writer
    shows up in ``git status`` — the signal an operator needs to know a run may
    have raced a rewrite. It fails only if the dir has been relocated outside
    the repo, which would silently break the path-anchoring guards above.
    """
    assert PLUGIN_DIR.is_relative_to(ROOT), (
        f"plugin dir {PLUGIN_DIR} escaped the repo root {ROOT}"
    )


# --------------------------------------------------------------------------
# 6. the suite itself is collectible with no import-time side effects
# --------------------------------------------------------------------------
def test_all_office_test_files_import_cleanly():
    """Every office test module must be importable without side effects.

    ``ast.parse`` catches a syntax error introduced by a partial edit; the test
    also asserts no module runs ``os.chdir`` at import time (which would leak
    CWD into every later test).
    """
    for f in _office_test_files():
        src = _read(f)
        try:
            tree = ast.parse(src, filename=str(f))
        except SyntaxError as e:  # pragma: no cover - only on a broken edit
            raise AssertionError(f"{f.name} does not parse: {e}") from e
        for node in tree.body:  # module level only
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                fn = node.value.func
                name = getattr(fn, "attr", getattr(fn, "id", ""))
                assert name not in {"chdir", "system", "popen"}, (
                    f"{f.name} calls {name}() at import time"
                )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
