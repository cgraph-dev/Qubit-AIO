import ast
import sys
from pathlib import Path

PKG = Path(__file__).parent
SHIPPED = ["__init__.py", "adapters_sessions.py", "adapters_workspace.py",
           "workbench_routes.py"]

BAD_MODULES = {"sqlite3", "subprocess", "run_agent", "shutil"}
BAD_NAMES = {"AIAgent", "os_system"}
FORBIDDEN_STRINGS = ["state.db", "hermes "]


def docstring_lines(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                start = node.body[0].lineno
                out.update(range(start, start + doc.count("\n") + 3))
    return out


fail = 0
for name in SHIPPED:
    src = (PKG / name).read_text()
    tree = ast.parse(src)
    docs = docstring_lines(tree)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    bad = imported & BAD_MODULES
    # run_agent is explicitly forbidden
    print(f"{name:24s} imports: {sorted(imported)}")
    if bad:
        print(f"   FAIL forbidden imports: {bad}")
        fail += 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in BAD_NAMES:
            print(f"   FAIL name {node.id} at line {node.lineno}")
            fail += 1
        # string literals in CODE (not docstrings) naming the db
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.lineno in docs:
                continue
            for bad_s in FORBIDDEN_STRINGS:
                if bad_s in node.value:
                    print(f"   FAIL literal {bad_s!r} in code at "
                          f"{node.lineno}")
                    fail += 1

print("\nAST gate:", "PASS" if fail == 0 else f"FAIL ({fail})")
sys.exit(1 if fail else 0)
