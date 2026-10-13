"""Audit for CHECK-SHAPED CODE THAT CANNOT FAIL.

WHY THIS FILE EXISTS
--------------------
This codebase has shipped, at least five times, a check that is structurally
incapable of failing — which is worse than no check, because it *certifies*
drift as verified and trains every later reader to ignore the checker. The
canonical instance is `floorplan_fit.assert_matches_tower()`, which once filled
`current` from its OWN `LEGACY_PITCH_*` constants and compared the module to
itself (see that function's comment block, `voicebridge/office/floorplan_fit.py`
~262-270).

This file pins the *class*, in three families:

  1. SELF-COMPARISON — a comparison of two values derived from the SAME source,
     so it cannot report a disagreement.
  2. EVIDENCE-ERASING FILTERS — a filter that removes text BEFORE a scan, so the
     scan can pass by deleting its own evidence.
  3. ASSERTIONS WEAKER THAN THEIR MESSAGE — a message that claims a thing is
     proven while the condition cannot fail (`assert X or True`; a bare
     `assert <module> is not None`; `xfail(strict=False)` that passes whether the
     subject is broken or fixed).

HOW IT AVOIDS BECOMING THE THING IT AUDITS
------------------------------------------
Every known instance is enumerated in an ALLOWLIST with its file:line and the
reason it is a real finding. The test FAILS if:
  * a NEW instance appears that is not on the list, or
  * a listed instance disappears/is re-shaped without this file being updated
    (so a fix must be recorded here deliberately, not silently).

Each detector is itself proven able to fail by `test_the_detectors_can_actually
_fail`, which feeds it a synthetic defect.

Hermetic: reads source text and calls pure functions; the one mutation proof
writes a scratch copy in a temp dir and never edits the real tree.
"""
from __future__ import annotations

import ast
import re
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OFFICE = REPO / "voicebridge" / "office"
SCRIPTS = REPO / "scripts"
TESTS = REPO / "tests"


# ===================================================================== #
#  KNOWN FINDINGS  (file:line -> why it is a real 'cannot fail' check)   #
# ===================================================================== #
# A fix to any of these must delete its entry here — the test then goes red
# until this file is updated, which is the point: the fix is recorded, not
# silent.
#
# ALL FOUR ORIGINAL ENTRIES ARE NOW DELETED, because all four defects were fixed
# (see `FIXED_DEFECTS` below). The dictionary is kept — rather than removed — so
# the detectors and the can-fail proof below stay live and a NEW instance of any
# family is caught immediately, while a fix to a listed one still has to be
# recorded deliberately instead of silently satisfying the allowlist.
KNOWN = {}

#: Every defect this audit originally found, and how each was closed. Recorded
#: here because the allowlist is now empty: without this, the reason the detector
#: families exist would be invisible to the next reader, and an empty `KNOWN`
#: reads like "nothing was ever wrong" rather than "all four were fixed".
FIXED_DEFECTS = (
    ("tests/test_office_camera.py", "or_true",
     "`assert \"aphae\" not in src.lower().split(\"not used\")[0] or True` — the "
     "`or True` made it true for EVERY input. Replaced with the same check minus "
     "the tautology, which now fails with the offending text quoted."),
    ("tests/test_office_cross_module.py", "nonstrict_xfail",
     "`xfail(strict=False)` on test_assert_matches_tower_reports_the_geometry_"
     "tower_carries — passed whether the checker was broken (xfail) or fixed "
     "(xpass), so it could never go red. Marker removed; it now runs as an "
     "ordinary test and is a real guard."),
    ("tests/test_office_proposal_directive.py", "bare_module_none",
     "`assert store is not None` where `store` is an imported module — a module "
     "is never None. Replaced with re-reads of the task table AND the proposal "
     "row, so the claimed 'nothing to roll back' is actually asserted."),
    ("scripts/check_stale_daemons.py", "self_compare_unsat",
     "`if (now - 3600) > now: ok = False` in `_self_test()` — unsatisfiable, so "
     "the branch that exists to 'prove the stale comparison FIRES' could never "
     "fire. Replaced with the shipped predicate driven in both directions, and "
     "proved able to fail: an inverted copy exits 1 with two SELF-TEST FAILED "
     "lines."),
)


# ===================================================================== #
#  detectors (pure; each proven able to fail below)                     #
# ===================================================================== #
def _find_bare_module_none_asserts(src: str):
    """Yield (lineno, subject) for `assert NAME is not None` where NAME is an
    imported MODULE (always truthy). A called/subscripted subject is not
    matched — only a bare Name that resolves to an import."""
    tree = ast.parse(textwrap.dedent(src))
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                module_names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                module_names.add(a.asname or a.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and len(t.ops) == 1
                and isinstance(t.ops[0], ast.IsNot)
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value is None
                and isinstance(t.left, ast.Name)
                and t.left.id in module_names):
            yield node.lineno, t.left.id


def _find_or_true_asserts(src: str):
    """Yield lineno for `assert X or True` — a tautology."""
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assert) and isinstance(node.test, ast.BoolOp)
                and isinstance(node.test.op, ast.Or)):
            for v in node.test.values:
                if isinstance(v, ast.Constant) and v.value is True:
                    yield node.lineno


def _find_nonstrict_xfails(src: str):
    """Yield lineno of `xfail(...)` markers whose strict is not True."""
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if name != "xfail":
                continue
            strict = None
            for kw in node.keywords:
                if kw.arg == "strict":
                    strict = getattr(kw.value, "value", None)
            if strict is not True:
                yield node.lineno


def _find_unsat_self_compare(src: str):
    """Yield lineno for `(x - N) > x` / `(x + N) < x` — arithmetically false."""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Compare):
            seg = ast.get_source_segment(src, node) or ""
            if re.search(r"\((\w+)\s*-\s*\d+\)\s*>\s*\1\b", seg):
                bad.append(node.lineno)
    return bad


def _strip_hash_line_comments(src: str) -> str:
    """The naive `#`-line-comment stripper (the shape used by tower_stream.py)."""
    return re.sub(r"#[^\n]*", "", src)


# ===================================================================== #
#  shared enumeration over the suite                                    #
# ===================================================================== #
def _office_test_files():
    return sorted(TESTS.glob("test_office*.py"))


def _scan_suite(finders):
    """Return {('<relpath>', kind): [linenos]} over the office suite."""
    found: dict = {}
    for p in _office_test_files():
        src = p.read_text()
        rel = str(p.relative_to(REPO))
        for kind, fn in finders:
            hits = list(fn(src))
            if hits:
                found.setdefault((rel, kind), []).extend(
                    h if isinstance(h, int) else h[0] for h in hits)
    return found


# ===================================================================== #
#  FAMILY 3 — assertions weaker than their message                       #
# ===================================================================== #
def test_no_unlisted_or_true_tautology():
    got = {k: v for k, v in
           _scan_suite([("or_true", _find_or_true_asserts)]).items()}
    known = {k for k in KNOWN if k[1] == "or_true"}
    new = set(got) - known
    assert not new, (
        "a NEW `assert <expr> or True` tautology appeared (it can never fail); "
        "remove the `or True` or add a recorded entry to KNOWN:\n  "
        + "\n  ".join(f"{f}:{ls}" for (f, _), ls in got.items() if (f, "or_true") in new))
    # and a listed one must still be present, else the fix went unrecorded
    missing = known - set(got)
    assert not missing, (
        f"a known or-True finding vanished ({missing}) — if it was fixed, delete "
        "its KNOWN entry here")


def test_no_unlisted_nonstrict_xfail():
    got = _scan_suite([("nonstrict_xfail", _find_nonstrict_xfails)])
    known = {k for k in KNOWN if k[1] == "nonstrict_xfail"}
    new = set(got) - known
    assert not new, (
        "a NEW non-strict xfail appeared (passes whether the subject is broken "
        "or fixed — it cannot fail). Use strict=True, or record it in KNOWN:\n  "
        + "\n  ".join(f"{f}:{ls}" for (f, _), ls in got.items()))
    missing = known - set(got)
    assert not missing, (
        f"a known non-strict xfail vanished ({missing}) — if fixed, delete its "
        "KNOWN entry here")


def test_no_unlisted_bare_module_is_not_none():
    got = _scan_suite([("bare_module_none", _find_bare_module_none_asserts)])
    known = {k for k in KNOWN if k[1] == "bare_module_none"}
    new = set(got) - known
    assert not new, (
        "a NEW `assert <imported-module> is not None` appeared; a module is "
        "never None so it cannot fail. Test the real thing, or record it:\n  "
        + "\n  ".join(f"{f}:{ls}" for (f, _), ls in got.items()))
    missing = known - set(got)
    assert not missing, (
        f"a known bare-module-None finding vanished ({missing}); if fixed, "
        "delete its KNOWN entry")


# ===================================================================== #
#  FAMILY 1 — self-comparison                                            #
# ===================================================================== #
def test_assert_matches_tower_reads_both_sides_from_tower_source():
    """The canonical historical self-comparison, pinned so it cannot return.

    `assert_matches_tower` must read every field it reports as `current` from
    tower.py's TEXT. Point it at a scratch tower.py with a scrambled stride and
    require the reported `current` to move — a self-comparison would not.
    """
    import voicebridge.office.floorplan_fit as ff

    base = Path(ff._TOWER_PATH).read_text(encoding="utf-8")
    scratch = base.replace("PITCH_COL, PITCH_ROW = 4, 3",
                           "PITCH_COL, PITCH_ROW = 7, 5")
    assert scratch != base, "could not perturb the scratch tower.py"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "tower.py"
        tmp.write_text(scratch, encoding="utf-8")
        orig = ff._TOWER_PATH
        try:
            ff._TOWER_PATH = str(tmp)
            d = ff.assert_matches_tower()
        finally:
            ff._TOWER_PATH = orig

    assert d["current"]["pitch_col"] == 7 and d["current"]["pitch_row"] == 5, (
        "assert_matches_tower reported pitch %r/%r from a scratch tower.py "
        "defining 7,5 — it is comparing the module to itself, not to the file"
        % (d["current"]["pitch_col"], d["current"]["pitch_row"]))


def test_no_unlisted_arithmetically_unsatisfiable_self_compare():
    """A `(x - N) > x` comparison can never be true — a self-comparison."""
    got = {}
    for p in sorted(OFFICE.glob("*.py")) + sorted(SCRIPTS.glob("*.py")):
        for ln in _find_unsat_self_compare(p.read_text()):
            got.setdefault((str(p.relative_to(REPO)), "self_compare_unsat"),
                           []).append(ln)
    known = {k for k in KNOWN if k[1] == "self_compare_unsat"}
    new = set(got) - known
    assert not new, (
        "a NEW `(x - N) > x` self-comparison appeared — it can never be true:\n  "
        + "\n  ".join(f"{f}:{ls}" for (f, _), ls in got.items() if (f, "self_compare_unsat") in new))
    missing = known - set(got)
    assert not missing, (
        f"a known unsatisfiable self-comparison vanished ({missing}); if fixed, "
        "delete its KNOWN entry")


# ===================================================================== #
#  FAMILY 2 — evidence-erasing filters                                   #
# ===================================================================== #
def test_the_string_aware_stripper_keeps_strings_and_removes_comments():
    """The repo's GOOD stripper (test_office_determinism.py) must keep its
    controls: a string that MUST survive and a comment that MUST be removed."""
    from tests.test_office_determinism import _strip_comments_keep_strings as s
    sample = (
        'var url = "http://example/Date.now"; // Date.now here\n'
        "/* Math.random\n   in a block */\n"
        "var sq = 'Math.random';\n"
        "var t = `Date.now ${x}`;\n"
    )
    out = s(sample)
    assert "http://example/Date.now" in out, "a string literal was deleted"
    assert "'Math.random'" in out, "a single-quoted string was deleted"
    assert "`Date.now ${x}`" in out, "a template literal was deleted"
    assert "// Date.now here" not in out, "a line comment survived"
    assert "Math.random\n   in a block" not in out, "a block comment survived"


def test_the_tower_stream_scan_stripper_erases_string_evidence():
    """Pin the REAL defect: the `#`-strip in tests/test_office_tower_stream.py
    runs over the whole text after the docstring strip, so a `#` inside a
    single-line STRING constant truncates the rest of that line — a scanner
    behind it can pass by deleting its own evidence."""
    scan = (REPO / "tests" / "test_office_tower_stream.py").read_text()
    assert re.search(r're\.sub\(r"#\[\^\\n\]\*"', scan) is not None, (
        "the `#`-comment stripper in test_office_tower_stream.py moved; if it "
        "was replaced with a string-aware stripper, delete this assertion")
    # demonstrate the hazard: a `#` inside a string eats real code after it
    line = 'LBL = "tot#als"; open("x")\n'
    out = _strip_hash_line_comments(line)
    assert "open(" not in out, (
        "EXPECTED HAZARD (not observed): a `#` inside a string literal must "
        "truncate the line so `open(` is erased by the naive stripper; got %r"
        % out)
    # and the string's own closing quote is eaten too — shows the damage
    assert out.count('"') % 2 == 1, (
        "the naive stripper left an unbalanced quote, confirming it does not "
        "understand string boundaries: %r" % out)


# ===================================================================== #
#  PROVE THE DETECTORS THEMSELVES CAN FAIL                               #
# ===================================================================== #
def test_the_detectors_can_actually_fail():
    """Feed each detector a synthetic defect and require it to be caught."""
    assert list(_find_or_true_asserts("def t():\n    assert 1 == 2 or True\n")), \
        "the or-True detector missed a tautology"
    got = list(_find_bare_module_none_asserts(
        "from m import store\ndef t():\n    assert store is not None\n"))
    assert got and got[0][1] == "store", "the module-None detector missed it"
    n = list(_find_nonstrict_xfails(
        "@pytest.mark.xfail(reason='x', strict=False)\ndef t():\n    pass\n"))
    assert len(n) == 1, "the non-strict xfail detector is broken"
    assert list(_find_nonstrict_xfails(
        "@pytest.mark.xfail(reason='x', strict=True)\ndef t():\n    pass\n")) == [], \
        "the xfail detector wrongly flags a strict xfail"
    assert _find_unsat_self_compare("def f(now):\n    if (now - 1) > now:\n        pass\n") == [2], \
        "the unsatisfiable self-compare detector is broken"
    assert _find_unsat_self_compare("def f(now):\n    if t > now:\n        pass\n") == [], \
        "the self-compare detector flags a legitimate comparison"
    # family 2: the naive `#` stripper truncates on a `#` inside a string
    assert "open(" not in _strip_hash_line_comments('s = "a#b"; open("x")\n'), \
        "the naive-stripper hazard control is broken"
