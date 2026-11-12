"""Pin the office-CLI matrix's ``QUBIT_HOME`` isolation.

WHY THIS FILE EXISTS
--------------------
``tests/test_office_cli_completeness.py``'s session fixture seeds ONE office
store and drives all 84 verbs against it. That fixture sets the three XDG_*
vars into a temp root, but those XDG vars are NOT what decides where the store
lives: ``voicebridge.paths.voice_layout()`` prefers the "new" tree under
``QUBIT_HOME``, and only falls back to the XDG paths when ``QUBIT_HOME`` is
unset (``mode == "read-old"``). An agent/operator shell exports ``QUBIT_HOME``
pointing at the real runtime, so a fixture that sets XDG_* and stops there
seeds the operator's LIVE store: the seed's ``office hire --slug ceo`` meets an
existing CEO and refuses ``OFFICE_EXISTS``, the fixture dies, and the whole
matrix reports 8 passed / 171 errors (measured before the fix, commit 6e1c024ab).

That is a silent, environment-dependent failure mode: it is invisible in a shell
without ``QUBIT_HOME`` and catastrophic in every shell that exports it. This file
pins the fix so dropping ``os.environ.pop("QUBIT_HOME", None)`` from the fixture
turns the suite RED here instead of quietly re-pointing it at the real store.

HOW IT PINS IT
--------------
`_session_store` is a *session-scoped* fixture built with plain ``os.environ``
writes (not ``monkeypatch``), so its correctness cannot be observed from a
test that runs after it: the fixture restores the environment on teardown. This
file therefore re-executes the fixture's own body in a fresh interpreter, with a
POISON ``QUBIT_HOME`` exported, and asks the same questions the seed asks:

  * does the store the fixture would seed resolve inside the fixture's tmp root?
  * is it the poison ``QUBIT_HOME`` tree?  (it must never be)

The child imports the fixture function itself (``_session_store.__wrapped__``)
so the test follows the fixture rather than a copy of it — reverting the fix in
the fixture reverts it here too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The body of the child process. It wraps the completeness fixture's own
# function (``_session_store``, a ``pytest.fixture`` whose undecorated body is
# ``__wrapped__``) in the minimum pytest scaffolding needed to run it, then
# reports where the office store resolved. ``pytest.fail`` inside the fixture is
# what turns a leak into a dead session, so the child records that failure
# instead of propagating it.
_CHILD = textwrap.dedent('''
    import json, os, sys

    repo = sys.argv[1]
    sys.path.insert(0, repo)
    # pytest.ini's ``pythonpath = tests qubit_runtime``: the tests tree holds
    # importable helpers (``tmp_policy``) and the vendored runtime root must be
    # importable exactly as it is under pytest.
    sys.path.insert(0, os.path.join(repo, "tests"))
    sys.path.insert(1, os.path.join(repo, "qubit_runtime"))

    # Load the completeness module BY PATH: ``qubit_runtime/tests/`` is also a
    # package named ``tests``, so ``import tests.<x>`` resolves to the vendored
    # tree depending on sys.path order. The file path cannot be ambiguous.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "office_cli_completeness_under_test",
        os.path.join(repo, "tests", "test_office_cli_completeness.py"))
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)

    out = {"seed_ok": False, "seed_error": None, "root": None,
           "store": None, "qubit_home_after": None}

    body = T._session_store.__wrapped__

    # Drive the fixture body exactly as pytest would: it is a generator
    # fixture, so the seeded state arrives on the first ``next()``.
    gen = body()
    try:
        try:
            state = next(gen)
        except BaseException as exc:  # a poisoned store kills the seed here
            out["seed_error"] = f"{type(exc).__name__}: {exc}"
            state = None
        out["qubit_home_after"] = os.environ.get("QUBIT_HOME")
        if state is not None:
            out["seed_ok"] = True
            out["root"] = state["root"]
            # The real answer: where would the seed's Config put office.db?
            from voicebridge.office import store as S
            out["store"] = str(S.db_path(state["cfg"]))
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    print(json.dumps(out))
''')


def _run_fixture_body_with_poison_qubit_home(tmp_path):
    """Run the completeness seed in a child with ``QUBIT_HOME`` set to a poison.

    Returns the child's JSON dict. The poison home is a real directory inside
    ``tmp_path`` (an existing ``voice/`` tree, so ``voice_layout`` resolves it as
    the "new" layout) — i.e. exactly the shape of the operator's runtime home,
    without ever touching it.
    """
    poison = tmp_path / "poison-qubit-home"
    (poison / "voice" / "state").mkdir(parents=True)
    (poison / "voice" / "config").mkdir(parents=True)

    env = {k: v for k, v in os.environ.items()
           if k not in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                        "XDG_CACHE_HOME", "VOICE_BRIDGE_CONFIG", "HERMES_HOME",
                        "OPENCLAW_HOME", "OPENCLAW_STATE_DIR",
                        "HERMES_DELEGATED_CHILD_CONTEXT")}
    env["QUBIT_HOME"] = str(poison)
    # A real pytest process sets this; ``assert_safe_test_home`` keys off it and
    # the fixture's own cleanup path is only meaningful under pytest.
    env["PYTEST_CURRENT_TEST"] = "tests/test_office_cli_isolation.py::_child"
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (REPO_ROOT, env.get("PYTHONPATH", "")) if p)

    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, REPO_ROOT],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        "the fixture body could not be driven in a child process:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"child produced no JSON line:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(lines[-1]), poison


def test_fixture_store_is_not_the_ambient_qubit_home(tmp_path):
    """``QUBIT_HOME`` must be CLEARED by the fixture, so the store is in tmp.

    If the fixture stops clearing ``QUBIT_HOME``, the seed resolves the office
    store inside the ambient home — here the poison dir standing in for the
    operator's real runtime — and this test fails. It is the regression pin for
    commit 6e1c024ab.
    """
    result, poison = _run_fixture_body_with_poison_qubit_home(tmp_path)

    assert result["seed_ok"], (
        "the completeness fixture's seed FAILED while an ambient QUBIT_HOME was "
        "set — that is the leak commit 6e1c024ab fixed (the seed met a live "
        f"store and refused):\n{result['seed_error']}")

    store = result["store"]
    root = result["root"]
    assert store, "the child did not report the resolved office store path"

    # 1. The store resolves inside the fixture's own tmp root.
    assert os.path.commonpath([os.path.realpath(store),
                               os.path.realpath(root)]) == \
        os.path.realpath(root), (
        f"the office store resolved OUTSIDE the fixture's tmp root:\n"
        f"  store: {store}\n  root:  {root}\n"
        "The fixture is seeding a store it does not own.")

    # 2. ...and never inside the ambient QUBIT_HOME (the operator's real home).
    poison_real = os.path.realpath(poison)
    store_real = os.path.realpath(store)
    assert os.path.commonpath([store_real, poison_real]) != poison_real, (
        "the office store resolved inside the ambient QUBIT_HOME — the "
        "operator's REAL office store. The session fixture must clear "
        f"QUBIT_HOME, not only the XDG_* vars:\n  store:       {store}\n"
        f"  QUBIT_HOME:  {poison}")

    # 3. The env var really is gone after the fixture body ran (belt and braces:
    #    the store assertion above is the behavioural one, this one is direct).
    assert result["qubit_home_after"] in (None, ""), (
        "the fixture left QUBIT_HOME set after resolving its store: "
        f"{result['qubit_home_after']!r} (it must be popped)")
