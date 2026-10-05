"""Worktree slash-command surface: alias routing + canonical delegation.

Pins two things that were gotten wrong once already:
1. `remove` must NOT spawn its own raw `git worktree remove` — it delegates to
   the canonical confined lifecycle (`voicebridge.cli._cmd_worktree`), so the
   slash command cannot drift weaker than the CLI's project-root confinement.
2. `restore` must route to the REGISTERED gateway handler
   (`qubit_handlers/worktrees.py::_wt_restore`), because `_cmd_worktree` has no
   `restore` verb — routing it there would silently fall through to `switch`.
"""
import ast
import pathlib
import re
import textwrap

SRC = pathlib.Path(__file__).resolve().parents[1] / "qubit_runtime" / "qubit_cli" / "cli_commands_mixin.py"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def _map() -> dict:
    m = re.search(r"_WORKTREE_SUBCOMMANDS\s*=\s*\{(.*?)\}", _src(), re.S)
    assert m, "_WORKTREE_SUBCOMMANDS not found"
    ns: dict = {}
    exec("_WORKTREE_SUBCOMMANDS = {" + m.group(1) + "}", ns)
    return ns["_WORKTREE_SUBCOMMANDS"]


def _body(name: str, nxt: str) -> str:
    s = _src()
    return s[s.index(name): s.index(nxt)]


def test_module_compiles():
    ast.parse(_src())


def test_aliases_route_to_the_right_handlers():
    d = _map()
    assert d["remove"] == "_worktree_remove"
    assert d["rm"] == "_worktree_remove"
    assert d["delete"] == "_worktree_remove"
    assert d["restore"] == "_worktree_restore"
    assert d["readd"] == "_worktree_restore"
    # `prune` is BULK reclaim and must stay distinct from targeted `remove`.
    assert d["prune"] == "_worktree_prune"
    assert d["remove"] != d["prune"]


def test_every_mapped_handler_is_defined():
    defined = set(re.findall(r"def (_worktree_\w+)\(self", _src()))
    assert set(_map().values()) <= defined


def test_remove_does_not_spawn_its_own_raw_git():
    body = _body("def _worktree_remove", "def _worktree_lifecycle")
    assert "subprocess.run" not in body, (
        "remove must not run raw git itself — delegate to the confined lifecycle")
    assert "_worktree_lifecycle(" in body


def test_remove_keeps_its_two_refusal_guards():
    body = _body("def _worktree_remove", "def _worktree_lifecycle")
    assert "this session is running in" in body
    assert 'match.verdict != "reclaim"' in body
    assert "--force" in body


def test_lifecycle_reuses_the_canonical_cli_entrypoint():
    body = _body("def _worktree_lifecycle", "def _worktree_restore")
    assert "_cmd_worktree" in body, "must reuse voicebridge.cli._cmd_worktree"
    assert "confirm" in body, "the canonical gate needs confirm=True"


def _body_code_only(name: str, nxt: str) -> str:
    """The function's CODE with its docstring removed (prose may mention names)."""
    body = _body(name, nxt)
    try:
        tree = ast.parse(textwrap.dedent(body))
    except SyntaxError:
        return body
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        del fn.body[0]
    return ast.unparse(fn)


def test_restore_uses_the_registered_gateway_handler():
    body = _body_code_only("def _worktree_restore", "def _worktree_prune")
    assert "_wt_restore" in body, "restore must call the registered handler"
    assert "qubit_handlers" in body
    # It must not CALL into the CLI lifecycle: `_cmd_worktree` has no `restore`
    # verb, so it would silently fall through to the `switch` branch.
    assert "_cmd_worktree" not in body, (
        "restore must not call _cmd_worktree (no restore verb -> falls to switch)")


def test_help_lists_the_new_verbs():
    s = _src()
    assert "/worktree remove" in s
    assert "/worktree restore" in s
