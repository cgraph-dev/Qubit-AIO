"""Regression: file tools must survive a ``tools`` namespace shadow.

The live checkout is installed editable and its repository root is also on
``sys.path`` (the voicebridge ``.pth``).  The root contains its own ``tools/``
directory without an ``__init__.py``, so ``import tools`` resolves to a
namespace package and never runs ``qubit_runtime/tools/__init__.py``.  A
``from tools import check_file_requirements`` then raises ``ImportError``,
every file tool's ``check_fn`` fails, and read_file/write_file/patch/
search_files disappear from the model's tool list.

This test reproduces that exact layout in a subprocess started from the
repository root and asserts the file tools still resolve and register.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / "qubit_runtime"
FILE_TOOLS = {"read_file", "write_file", "patch", "search_files"}

_CHILD = r"""
import importlib.machinery
import os
import sys
from pathlib import Path

root = Path(os.getcwd()).resolve()
runtime = root / "qubit_runtime"
sys.path.insert(0, str(root))


class WorktreeFinder:
    # Stand-in for the editable install, pinned to THIS checkout. Added after
    # the path finder and before the ambient editable finder so a repository-
    # root tools/ directory (no __init__.py) still shadows the package while
    # its submodules load from this worktree.

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        top = fullname.split(".", 1)[0]
        base = runtime / top
        if not (base.is_dir() or base.with_suffix(".py").is_file()):
            return None
        if fullname == top:
            return importlib.machinery.PathFinder.find_spec(fullname, [str(runtime)])
        parent = runtime.joinpath(*fullname.split(".")[:-1])
        return importlib.machinery.PathFinder.find_spec(fullname, [str(parent)])


index = len(sys.meta_path)
for i, finder in enumerate(sys.meta_path):
    if getattr(finder, "__module__", "").startswith("__editable__"):
        index = i
        break
sys.meta_path.insert(index, WorktreeFinder)

import tools

assert tools.__file__ is None, (
    f"expected the repository-root tools/ namespace shadow, got {tools.__file__!r}"
)
assert not hasattr(tools, "check_file_requirements"), (
    "shadowed tools package unexpectedly exports check_file_requirements"
)

import tools.file_tools as file_tools

assert Path(file_tools.__file__).resolve().is_relative_to(runtime), (
    f"file_tools loaded from {file_tools.__file__!r}, not this worktree"
)
assert file_tools._check_file_reqs() is True

from tools.registry import registry

registered = set(registry.get_all_tool_names())
expected = {"read_file", "write_file", "patch", "search_files"}
missing = expected - registered
assert not missing, f"file tools not registered under shadow: {sorted(missing)}"

from model_tools import get_tool_definitions

definitions = {
    tool["function"]["name"]
    for tool in get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
}
missing_defs = expected - definitions
assert not missing_defs, (
    f"file tools not resolved under shadow: {sorted(missing_defs)}; "
    f"got {sorted(definitions)}"
)
print("FILE_TOOLS_SHADOW_OK")
"""


def test_file_tools_register_under_tools_namespace_shadow():
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    assert proc.returncode == 0, (
        "file tools failed under the live-style tools namespace shadow\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "FILE_TOOLS_SHADOW_OK" in proc.stdout, proc.stdout
