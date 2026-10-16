"""`qubit office <verb>` — the FOUR-SITE wiring bug class, caught by INVOKING.

WHY THIS FILE EXISTS
--------------------
Wiring an office op touches FOUR sites. Miss one and the verb is either absent
from the parser or dies at runtime with a `NameError`/`ImportError` traceback:

  1. the local import inside `_cmd_office` (alongside `from .office import ...`);
     a missing one raises `NameError` only when that verb's branch runs, so the
     bug hides until someone invokes it;
  2. the `of_sub.add_parser("<verb>", parents=[common], ...)` subparser (and a
     `--confirm` flag for anything mutating);
  3. the `elif cmd == "<verb>":` branch, which must sit BEFORE the trailing
     `else` that prints `OFFICE_UNSUPPORTED` and returns 2;
  4. the parity surfaces (documented verb lists / challenge surfaces).

A grep proves none of this: a branch can be present and still `NameError` on a
bad import, and a parser line can exist while the dispatch branch is missing.
So this file **invokes every verb through the real parser and the real
`_cmd_office` dispatcher** and asserts, per verb, that:

  * the parser accepts it (site 2 present — a missing subparser is a SystemExit);
  * the dispatcher reaches a real branch (site 3 present — a missing branch
    returns the `OFFICE_UNSUPPORTED` typed refusal, which this file treats as a
    FAILURE, never an acceptable refusal);
  * no Python exception escapes (site 1 present — a `NameError`/`ImportError`
    is exactly the four-site bug, and it is reported as a traceback);
  * a mutating verb invoked WITHOUT `--confirm` still refuses with a typed code
    (the contract: the refusal happens before the store is touched).

Invocation is IN-PROCESS (real parser + real handler) rather than one subprocess
per verb: the venv's import cost is ~40 s per process, and 81 subprocesses would
put this guard firmly in the "never run it" bucket. One process drives all 81
verbs; `test_the_real_entrypoint_agrees` additionally smoke-tests the actual
`python -m voicebridge.cli` entrypoint on a representative sample.

HERMETIC: tmp XDG state only; the office store lives under
$XDG_STATE_HOME/qubit-voicebridge/office/office.db, never the real one. No
network, no provider call, no agent started. Mutating verbs are only invoked
WITHOUT `--confirm` (they must refuse before doing anything); the store is
seeded once via the confirm path (a CEO + a worker seat) so read-only verbs that
need a real entity read real rows.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import traceback

import pytest

from voicebridge import cli as CLI
from voicebridge import config as C

# The expected subcommand count for `qubit office`. Pinned so a verb silently
# dropping off the parser turns this guard RED instead of quietly shrinking.
# 83 = the original 81 surface plus `orphans` (the orphan report) and `reap`
# (the orphan reaper's write half, both added with the D15 reconciliation that
# un-jams an org blocked by a crashed turn).
EXPECTED_VERB_COUNT = 83

# The dialect of typed refusal that means "no dispatch branch reached this
# verb" — i.e. the FOUR-SITE BUG (site 3 missing). It must NEVER appear for a
# verb the parser exposes. This is the single most important assertion here.
UNSUPPORTED_CODE = "OFFICE_UNSUPPORTED"

# The typed refusal every mutating verb returns when invoked without
# `--confirm`: the mutation is refused before the store is touched.
CONFIRM_CODE = "OFFICE_CONFIRM_REQUIRED"

# --------------------------------------------------------------------------- #
# The synthetic-args table: the minimum required arguments to get PAST argparse
# and INTO the dispatch branch for each verb. A verb absent here needs no
# arguments on a bare invocation. Ids are the seeded store's real ids (filled
# at run time via `{CEO}` / `{W}`); nothing here requires `--confirm`.
# --------------------------------------------------------------------------- #
_SYNTHETIC = {
    "record": ["--kind", "revenue", "--cents", "100"],
    "reverse": ["1"],
    "chain": ["--agent", "{W}"],
    "floor-create": ["--number", "2"],
    "department-create": ["--slug", "d2", "--floor", "1"],
    "room-create": ["--department", "1", "--slug", "r2"],
    "hire": ["--slug", "h2", "--supervisor", "{CEO}"],
    "run-begin": ["--agent", "{W}"],
    "run-finish": ["run-x", "--cents", "1"],
    "settle": ["run-x", "--cents", "1"],
    "run-halt": ["run-x"],
    "admit": ["--agent", "{W}"],
    "cap-set": ["--key", "perRun"],
    "run": ["--agent", "{W}", "--task", "t"],
    "trace": ["--agent", "{W}"],
    "attribute": ["--run", "run-x", "--ref", "f"],
    "consent": ["--capability", "read_file"],
    "envelope-allocate": ["--scope", "org", "--cents", "1"],
    "envelope-split": ["--by", "{W}", "--total", "100"],
    "headroom": ["--agent", "{W}"],
    "raise": ["--agent", "{W}", "--cents", "1", "--thesis", "t",
              "--expected-return", "r", "--displaces", "d"],
    "request-decide": ["1", "--by", "{W}", "--approve"],
    "escalate": ["--agent", "{W}", "--kind", "blocked", "--summary", "s"],
    "escalation-resolve": ["1", "--by", "{W}", "--approve"],
    "terminate": ["{W}", "--reason", "r"],
    "report": ["--agent", "{W}", "--period", "2026-10"],
    "business-propose": ["--slug", "b1", "--department", "1", "--thesis", "t",
                         "--offer", "o"],
    "business-terms": ["1"],
    "business-advance": ["1", "--status", "proposed"],
    "business-fund": ["1", "--owner-words", "go"],
    "business-pnl": ["1"],
    "revenue": ["--business", "1", "--cents", "1", "--source", "stripe",
                "--external-ref", "x"],
    "finding-close": ["1"],
    "spot-audit": ["--period", "2026-10"],
    "penalty": ["--agent", "{W}"],
    "programme": ["--headcount", "4"],
    "review": ["--subject", "{W}", "--reviewer", "{CEO}", "--verdict", "meets"],
    "task-create": ["--title", "t"],
    "seat-move": ["{W}"],
    "task-claim": ["1", "--agent", "{W}"],
    "task-trail": ["1"],
    "task-review": ["1", "--accept"],
    "turn-cap": ["--agent", "{W}", "--turns", "5"],
    "propose": ["--by", "{W}", "--title", "t"],
    "approve": ["1"],
    "reject-proposal": ["1", "--reason", "r"],
}

# Verbs whose bare (no --confirm) invocation is READ-ONLY and must therefore
# exit 0 against a seeded store — EXCEPT the two read-only gates that may
# legitimately refuse on business logic (`admit` on the caps, `business-pnl` on
# a missing business). Those two are listed separately so a genuine wiring
# failure cannot hide behind the allowance.
_READ_ONLY = {
    "admit", "at-risk", "attribution-check", "autowork", "business-pnl",
    "businesses", "caps", "chain",
    "consent", "envelopes", "escalation-sync", "escalations", "findings",
    "floorplan", "floors", "hardline", "headroom", "health", "init", "ledger",
    "org-facts", "orphans", "penalty", "pnl", "programme", "proposals",
    "receipts",
    "reconcile", "reports", "requests", "reviews", "roster", "runway",
    "separation", "spot-audit", "task-trail", "tasks", "tick", "tower", "trace",
    "tree", "turn-budgets", "unsettled",
}

# Read-only verbs that may return a TYPED 2 (not CONFIRM_REQUIRED, not
# UNSUPPORTED) because their business logic legitimately refuses on this
# fixture: `admit` applies the caps to an estimate, `business-pnl` names a
# business id that does not exist. Any other refusal code is a failure.
_READ_ONLY_TYPED_REFUSAL_OK = {"admit", "business-pnl"}

# Mutating verbs: a bare (no --confirm) invocation MUST refuse with the confirm
# code, before touching the store. Some are gated by the top-level `mutating`
# set, others inside their handler, but all must refuse typed.
_MUTATING = {
    "adjudicate", "approve", "attribute", "auditor-hire", "business-advance",
    "business-fund", "business-propose", "business-terms", "cap-set",
    "department-create", "dispatch", "envelope-allocate", "envelope-split",
    "escalate", "escalation-resolve", "finding-close", "floor-create", "hire",
    "propose", "raise", "reap", "record", "reject-proposal", "report",
    "request-decide",
    "revenue", "reverse", "review", "room-create", "run", "run-begin",
    "run-finish", "run-halt", "seat-move", "settle", "task-claim",
    "task-create", "task-review", "terminate", "turn-cap",
}


def _enumerate_office_verbs() -> list[str]:
    """Every subcommand the REAL parser exposes under `office`.

    Read from the parser, not a hardcoded list, so the test measures the tree
    instead of asserting a remembered number.
    """
    parser = CLI.build_parser()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "office" in choices:
            office = choices["office"]
            for sub in office._actions:
                sub_choices = getattr(sub, "choices", None)
                if sub_choices and isinstance(sub_choices, dict):
                    return sorted(sub_choices.keys())
    raise AssertionError("no `office` subparser found on the real parser")


VERBS = _enumerate_office_verbs()


@pytest.fixture(scope="session")
def _session_store():
    """Seed ONE office store for the whole session and return (root, ids).

    Built in a session-tmp dir so seeding's ~0.6 s cost is paid once, not once
    per test. `seeded` below re-points XDG_* at this root for each test (the
    autouse `_isolate_qubit_runtime` would otherwise move the store per test).
    Because every bare invocation refuses before writing (proven by
    `test_bare_invocations_do_not_mutate_the_store`), sharing one seeded store
    across tests is safe.
    """
    import os
    import tempfile

    saved = {k: os.environ.get(k) for k in
             ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
              "VOICE_BRIDGE_CONFIG")}
    root = tempfile.mkdtemp(prefix="office-cli-completeness-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(root, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(root, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(root, "d")
    os.environ.pop("VOICE_BRIDGE_CONFIG", None)
    cfgdir = os.path.join(os.environ["XDG_CONFIG_HOME"], "qubit-voicebridge")
    os.makedirs(cfgdir, exist_ok=True)
    with open(os.path.join(cfgdir, "voice.yaml"), "w") as fh:
        fh.write("logging:\n  jsonl: false\n")

    cfg = C.Config.load()

    def run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            args = CLI.build_parser().parse_args(argv)
            rc = CLI._cmd_office(cfg, args)
        text = buf.getvalue()
        try:
            obj = json.loads(text)
        except Exception:
            obj = {}
        return rc, obj

    # Minimal seed: a CEO (no supervisor — allowed for the ceo seat type) and a
    # worker reporting to it — what the read-only verbs that need a real entity
    # (chain/headroom/trace/admit) require. No floor/department is needed on a
    # bare invocation, so none is created.
    assert run(["office", "init"])[0] == 0
    rc, ceo = run(["office", "hire", "--slug", "ceo", "--seat-type", "ceo",
                   "--confirm"])
    assert rc == 0, ceo
    rc, worker = run(["office", "hire", "--slug", "w1", "--seat-type", "worker",
                      "--supervisor", str(ceo["id"]), "--confirm"])
    assert rc == 0, worker

    try:
        yield {"root": root, "cfg": cfg,
               "ids": {"CEO": str(ceo["id"]), "W": str(worker["id"])}}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def seeded(monkeypatch, _session_store):
    """Point this test's XDG_* at the shared session store.

    `tests/conftest.py`'s autouse `_isolate_qubit_runtime` re-points
    `XDG_STATE_HOME` at a NEW temp dir per test, so the office store would move
    per test and the second test onward would see an empty store (read-only
    verbs would refuse with `OFFICE_NOT_FOUND`). This fixture overrides that,
    pinning every test to the one seeded store — cheap, isolated from the real
    store, and correct.
    """
    import os

    root = _session_store["root"]
    monkeypatch.setenv("XDG_CONFIG_HOME", os.path.join(root, "c"))
    monkeypatch.setenv("XDG_STATE_HOME", os.path.join(root, "s"))
    monkeypatch.setenv("XDG_DATA_HOME", os.path.join(root, "d"))
    monkeypatch.delenv("VOICE_BRIDGE_CONFIG", raising=False)
    return _session_store["cfg"], _session_store["ids"]


def _table_row_counts(cfg):
    """Row count per table — the measure of whether a bare verb wrote data.

    Schema-only changes (a lazily-created empty table from a read verb's
    `migrate()`) are not data mutations; only ROW COUNT changes matter.
    """
    from voicebridge.office import store as S

    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in names}
    finally:
        conn.close()


def _invoke(cfg, argv):
    """Real parser + real dispatcher. Returns (rc, parsed-json, stdout, tb)."""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    tb = None
    rc = None
    try:
        with contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            try:
                args = CLI.build_parser().parse_args(argv)
            except SystemExit as se:            # argparse rejected the verb/args
                return None, {"_parse_fail": se.code}, buf_out.getvalue(), \
                    buf_err.getvalue()
            rc = CLI._cmd_office(cfg, args)
    except BaseException:                        # noqa: BLE001 — that's the point
        tb = traceback.format_exc()
    text = buf_out.getvalue()
    try:
        obj = json.loads(text)
    except Exception:
        obj = {}
    return rc, obj, text, (tb or buf_err.getvalue())


def _argv_for(verb, ids):
    extra = [a.format(**ids) for a in _SYNTHETIC.get(verb, [])]
    return ["office", verb] + extra


# --------------------------------------------------------------------------- #
# 1. Enumeration — the real count and no silent drops.
# --------------------------------------------------------------------------- #
def test_office_exposes_every_expected_subcommand():
    assert len(VERBS) == EXPECTED_VERB_COUNT, (
        f"the office parser exposes {len(VERBS)} subcommands, expected "
        f"{EXPECTED_VERB_COUNT}. A missing verb means site 2 (the subparser) "
        f"was dropped:\n{VERBS}")


def test_no_duplicate_or_empty_verb_names():
    assert len(set(VERBS)) == len(VERBS)
    assert all(v and v == v.strip() for v in VERBS)


def test_read_only_and_mutating_sets_cover_every_verb():
    """The classification tables must partition the real verb list.

    If a verb is added to the parser but not classified here, this fails and
    forces a decision: is the new verb read-only or gated? That is the moment
    the four-site checklist is applied.
    """
    unknown = set(VERBS) - _READ_ONLY - _MUTATING
    assert not unknown, (
        f"verbs on the parser but not classified as read-only or mutating: "
        f"{sorted(unknown)}. Classify them (and wire the missing site).")
    overlap = _READ_ONLY & _MUTATING
    assert not overlap, f"verbs in both sets: {sorted(overlap)}"


def test_classification_has_no_stale_verbs():
    """A classified verb the parser no longer exposes is drift."""
    stale = (_READ_ONLY | _MUTATING) - set(VERBS)
    assert not stale, f"classified but absent from the parser: {sorted(stale)}"


# --------------------------------------------------------------------------- #
# 2. Per-verb contract — invoke it for real.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_invokes_without_a_traceback(seeded, verb):
    """Site 1 (imports) + site 2 (parser) + site 3 (dispatch). In one call.

    A `NameError` from a missing local import, an `ImportError`, or any other
    escaping exception is the four-site bug and is reported with its traceback.
    """
    cfg, ids = seeded
    rc, obj, out, err = _invoke(cfg, _argv_for(verb, ids))

    # site 2: the parser accepted the verb (and its required args).
    assert obj.get("_parse_fail") is None and not (
        "Traceback (most recent call last)" in out), (
        f"`office {verb}` did not parse (site 2 — subparser missing/incomplete "
        f"or required args not wired): {out!r} {err!r}")

    # site 1: no Python exception escaped the dispatcher.
    assert "Traceback" not in err, (
        f"`office {verb}` raised a Python exception — the FOUR-SITE BUG "
        f"(a missing import, site 1):\n{err}")
    assert "Traceback" not in out, (
        f"`office {verb}` printed a traceback:\n{out}")

    # site 3: the branch was reached — never the catch-all.
    assert obj.get("code") != UNSUPPORTED_CODE, (
        f"`office {verb}` returned {UNSUPPORTED_CODE} — the dispatch branch is "
        f"MISSING (site 3). The verb is on the parser but falls through to the "
        f"trailing `else`.")

    # a working verb exits 0 (read-only/ok) or returns a typed refusal (2).
    assert rc in (0, 2, 3), f"`office {verb}` returned rc={rc}"


@pytest.mark.parametrize("verb", sorted(_MUTATING))
def test_mutating_verb_refuses_without_confirm(seeded, verb):
    """A mutating verb invoked bare must refuse TYPED, before touching anything.

    The refusal code is the contract; `OFFICE_UNSUPPORTED` is not a refusal, it
    is the missing-branch bug wearing a refusal's clothes.
    """
    cfg, ids = seeded
    rc, obj, out, err = _invoke(cfg, _argv_for(verb, ids))
    assert obj.get("code") != UNSUPPORTED_CODE, (
        f"`office {verb}` hit {UNSUPPORTED_CODE} (site 3 missing)")
    assert rc == 2, (
        f"`office {verb}` without --confirm returned rc={rc}, expected 2 "
        f"(a typed refusal). Output:\n{out}\n{err}")
    assert obj.get("code") == CONFIRM_CODE, (
        f"`office {verb}` without --confirm returned code "
        f"{obj.get('code')!r}, expected {CONFIRM_CODE!r}. A bare mutation must "
        f"never reach the store:\n{out}")


@pytest.mark.parametrize("verb", sorted(_READ_ONLY))
def test_read_only_verb_succeeds(seeded, verb):
    """A read-only verb exits 0 against a seeded store.

    `admit` and `business-pnl` are read-only but may legitimately refuse typed
    on this fixture (caps / a missing business id) — allowed, but only with a
    TYPED code and never `OFFICE_UNSUPPORTED`.
    """
    cfg, ids = seeded
    rc, obj, out, err = _invoke(cfg, _argv_for(verb, ids))
    assert obj.get("code") != UNSUPPORTED_CODE, (
        f"`office {verb}` hit {UNSUPPORTED_CODE} (site 3 missing)")
    if verb in _READ_ONLY_TYPED_REFUSAL_OK:
        assert rc in (0, 2), f"`office {verb}` returned rc={rc}:\n{out}"
        if rc == 2:
            assert obj.get("code"), f"typed refusal without a code: {out}"
        return
    assert rc == 0, (
        f"read-only `office {verb}` returned rc={rc} (expected 0):\n{out}\n{err}")
    assert out.strip(), f"read-only `office {verb}` printed nothing"


# --------------------------------------------------------------------------- #
# 3. The whole matrix in one pass — and the summary table the doc quotes.
# --------------------------------------------------------------------------- #
def test_full_verb_matrix_has_no_traceback_and_no_unsupported(seeded):
    """Drive ALL verbs in one test and assert the bug-class invariants.

    This is the guard that stays valid as verbs are added: it reads the verb
    list from the parser, so a new verb is covered the moment it exists.
    """
    cfg, ids = seeded
    rows = []
    problems = []
    for verb in VERBS:
        rc, obj, out, err = _invoke(cfg, _argv_for(verb, ids))
        traceback_seen = ("Traceback" in err) or ("Traceback" in out)
        parse_fail = obj.get("_parse_fail")
        code = obj.get("code")
        unsupported = code == UNSUPPORTED_CODE
        rows.append((verb, rc, code, traceback_seen, parse_fail))
        if unsupported:
            problems.append(f"{verb}: {UNSUPPORTED_CODE} (site 3 missing)")
        if traceback_seen:
            problems.append(f"{verb}: TRACEBACK\n{err or out}")
        if parse_fail is not None:
            problems.append(f"{verb}: PARSE FAIL ({parse_fail})")

    assert not problems, (
        "the four-site bug class found incompletely-wired verbs:\n  "
        + "\n  ".join(problems))

    # Every verb produced either success (0) or a typed refusal (2).
    bad_codes = [(v, rc) for (v, rc, *_r) in rows if rc not in (0, 2, 3)]
    assert not bad_codes, f"verbs with an unexpected exit code: {bad_codes}"


def test_bare_invocations_do_not_mutate_the_store(seeded):
    """Running ALL verbs bare (no --confirm) writes no DATA.

    The confirm gate's promise, and the reason a bare invocation is safe to
    ship: a mutating verb refuses before it touches anything, and a read-only
    verb reads. If a future change lets a bare verb write, this fails — and it
    also protects the real store, since the same code path is what a human hits.

    Only a change to a row COUNT of a table that already held rows is a
    mutation; a read verb lazily creating an empty table via `migrate()` is a
    schema step, not a write, and is excluded.
    """
    cfg, ids = seeded
    before = _table_row_counts(cfg)
    for verb in VERBS:
        _invoke(cfg, _argv_for(verb, ids))
    after = _table_row_counts(cfg)

    changed = {}
    for t in set(before) | set(after):
        b, a = before.get(t), after.get(t)
        if b == a:
            continue
        # A table that did not exist before and is still EMPTY afterwards is a
        # lazy schema migration, not a data mutation.
        if b is None and a == 0:
            continue
        changed[t] = (b, a)
    assert not changed, (
        f"a bare (no --confirm) office invocation mutated the store — row "
        f"counts changed: {changed}")


def test_the_real_entrypoint_agrees(seeded):
    """The subprocess entrypoint gives the same verdict as the in-process call.

    Representative sample: a read-only success, a bare mutation, and a verb
    whose branch is easy to forget. Proves the wiring holds through the actual
    `python -m voicebridge.cli` path (argv → parser → dispatch → exit code), the
    way a human invokes it.
    """
    import os

    env = dict(os.environ)
    py = sys.executable
    cases = [
        (["office", "health"], 0),
        (["office", "tower"], 0),
        (["office", "record", "--kind", "revenue", "--cents", "100"], 2),
        (["office", "escalate", "--agent", "2", "--kind", "blocked",
          "--summary", "s"], 2),
        (["office", "turn-budgets"], 0),
    ]
    for argv, want in cases:
        proc = subprocess.run(
            [py, "-m", "voicebridge.cli", *argv],
            capture_output=True, text=True, env=env, timeout=180)
        blob = proc.stdout + proc.stderr
        assert "Traceback" not in blob, (
            f"`{' '.join(argv)}` printed a traceback:\n{blob}")
        assert proc.returncode == want, (
            f"`{' '.join(argv)}` exited {proc.returncode}, expected {want}:\n"
            f"{blob}")


def test_refusal_constants_match_the_source():
    """The two refusal codes this guard keys on must be the ones the office emits.

    The confirm gate lifts its code from `office.errors.CONFIRM_CODE`; the
    catch-all prints the literal `OFFICE_UNSUPPORTED` in cli.py. Assert both,
    so a rename fails here rather than making the guard check a dead string.
    """
    from voicebridge.office import errors as OE

    assert CONFIRM_CODE == OE.CONFIRM_CODE, (
        f"the confirm code changed: guard keys on {CONFIRM_CODE!r}, "
        f"office.errors says {OE.CONFIRM_CODE!r}")
    from pathlib import Path

    src = Path(CLI.__file__).read_text(encoding="utf-8")
    assert UNSUPPORTED_CODE in src, (
        f"{UNSUPPORTED_CODE!r} is not in {CLI.__file__}; the catch-all was "
        f"renamed and this guard is now checking the wrong string")


# --------------------------------------------------------------------------- #
# 4. Parity surface (site 4): the doc's verb count must match the parser.
# --------------------------------------------------------------------------- #
def test_doc_matches_the_parser():
    """`docs/OFFICE-CLI-COMPLETENESS.md` must quote the real count.

    Site 4 is the parity surface. When the doc and the parser disagree, the doc
    is stale or a verb was added without updating its listing — this fails so
    the discrepancy is fixed rather than remembered.
    """
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1]
           / "docs" / "OFFICE-CLI-COMPLETENESS.md")
    if not doc.exists():
        pytest.skip("completeness doc not present")
    text = doc.read_text(encoding="utf-8")
    assert f"{len(VERBS)} subcommands" in text or f"{len(VERBS)} verbs" in text, (
        f"the doc does not state the real verb count ({len(VERBS)}); update "
        f"docs/OFFICE-CLI-COMPLETENESS.md")
