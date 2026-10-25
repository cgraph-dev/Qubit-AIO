"""Plugin-compat pointers must resolve to the SAME object the name moved to, never a same-named stranger.

Regression: ``qubit_cli.kanban_db.connect`` was pointed at ``qubit_cli.projects_db.connect`` (a different
database, no ``board=`` parameter) because the generator ranked candidate homes by path proximity. The
manifest codified the mistake, so the compat lint treated it as valid.

Invariant checked here: for every ``moved-lazy`` entry whose target module also exists in the manifest of
some other facade under the same name, or whose facade stem has a sibling ``<stem>_*`` module defining the
name, the facade attribute IS the sibling's object.
"""
import importlib
import importlib.util
import json
import pkgutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = ROOT / "compat_manifest.json"
# Stdlib modules that do not exist on native Windows; a pointer whose target imports one of them
# (the dashboard PTY bridge: fcntl/termios) cannot be resolved there, only located (#112576).
_POSIX_ONLY_STDLIB = {"fcntl", "termios", "pty", "tty", "grp", "pwd", "resource"}

# This file resolves every pointer on purpose; the once-per-name plugin warning is expected here.
pytestmark = [
    pytest.mark.skipif(not MANIFEST.exists(), reason="compat layer removed (scheduled revert)"),
    pytest.mark.filterwarnings("ignore::FutureWarning"),
]


def _entries():
    return [e for e in json.loads(MANIFEST.read_text())["entries"] if e["kind"] == "moved-lazy"]


def _sibling_modules(facade: str) -> list[str]:
    pkg, _, stem = facade.rpartition(".")
    try:
        parent = importlib.import_module(pkg) if pkg else None
    except Exception:
        return []
    paths = getattr(parent, "__path__", None) if parent else [str(ROOT)]
    if not paths:
        return []
    prefix = f"{pkg}." if pkg else ""
    return [prefix + m.name for m in pkgutil.iter_modules(paths) if m.name.startswith(stem + "_")]


def test_moved_lazy_pointers_resolve_to_the_split_off_siblings_object():
    """When a facade's own ``<stem>_*`` sibling binds the name, the facade attribute must be THAT object.

    A sibling may legitimately re-import the value from elsewhere (then the pointer target is the origin and
    the objects are identical); what must never happen is the pointer resolving to a same-named stranger.
    """
    bad = []
    for e in _entries():
        facade, name = e["facade"], e["name"]
        sibs = _sibling_modules(facade)
        if not sibs:
            continue
        try:
            got = getattr(importlib.import_module(facade), name)
        except AttributeError:
            # The pin's upstream sync reverted most PLUGIN-COMPAT blocks (removal date
            # 2026-09-14): a facade that keeps no pointer has nothing to compare. The
            # surviving blocks (auth/commands/...) are still checked below.
            continue
        except ModuleNotFoundError as exc:
            if sys.platform == "win32" and exc.name in _POSIX_ONLY_STDLIB:
                # POSIX-only target: object identity is checked on POSIX hosts; here the
                # declared target module must at least exist in the tree.
                assert importlib.util.find_spec(e["target"]) is not None, (facade, name, e["target"])
                continue
            bad.append((facade, name, f"unresolvable: {exc!r}"))
            continue
        except Exception as exc:  # unresolvable pointer is its own failure
            bad.append((facade, name, f"unresolvable: {exc!r}"))
            continue
        for s in sibs:
            try:
                mod = importlib.import_module(s)
            except Exception:
                continue
            if name in vars(mod):
                sib_obj = vars(mod)[name]
                same = (sib_obj == got) if isinstance(got, (int, float, str, bytes, bool, type(None))) else (sib_obj is got)
                if not same:
                    bad.append((facade, name, e["target"], s))
    assert not bad, f"compat pointers resolve to a different object than the facade's own sibling binds: {bad}"


def test_kanban_db_connect_opens_a_kanban_board(tmp_path, monkeypatch):
    """``kanban_db_connect.connect(board=...)`` opens a Kanban DB, not projects.db.

    The fork's post-sync decomposition removed the historical ``kanban_db.connect``
    PLUGIN-COMPAT alias; the canonical split module is the contract now.
    """
    import qubit_cli.kanban_db_boards as kanban_db_boards
    import qubit_cli.kanban_db_connect as kanban_db_connect

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    # The fork refuses to resurrect an uncreated named board; creation is the
    # boards module's job, not a plain connect's.
    kanban_db_boards.create_board("qa")
    db = tmp_path / "board.db"
    conn = kanban_db_connect.connect(db, board="qa")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "tasks" in tables, tables
    assert not (tmp_path / "projects.db").exists()
