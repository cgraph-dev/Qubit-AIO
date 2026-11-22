"""Fast, hermetic tests for the dashboard's authentication contract.

These AST-level tests intentionally do not claim live HTTP acceptance. They
protect the security boundary while a packaged-server acceptance lane is built.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "qubit_runtime/qubit_cli/web_server.py"
STATUS = ROOT / "qubit_runtime/qubit_cli/web_routers/status.py"


def _function(tree, name):
    matches = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(matches) == 1, f"expected one {name}, got {len(matches)}"
    return matches[0]


def test_sensitive_route_guard_rejects_unauthorized():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    fn = _function(tree, "_require_token")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert any(isinstance(c.func, ast.Name) and c.func.id == "_has_valid_session_token" for c in calls)
    assert any(isinstance(c.func, ast.Name) and c.func.id == "HTTPException"
               and any(k.arg == "status_code" and isinstance(k.value, ast.Constant)
                       and k.value.value == 401 for k in c.keywords) for c in calls)


def test_session_token_is_compared_in_constant_time():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    fn = _function(tree, "_has_valid_session_token")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "hmac"
               and n.func.attr == "compare_digest" for n in ast.walk(fn))


def test_public_health_reports_auth_requirement():
    tree = ast.parse(STATUS.read_text(encoding="utf-8"))
    fn = _function(tree, "get_health")
    assert any(isinstance(n, ast.Constant) and n.value == "auth_required" for n in ast.walk(fn))
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "_require_token" for n in ast.walk(fn))


def test_host_identity_requires_token():
    tree = ast.parse(STATUS.read_text(encoding="utf-8"))
    fn = _function(tree, "get_host_identity")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_require_token" for n in ast.walk(fn))
