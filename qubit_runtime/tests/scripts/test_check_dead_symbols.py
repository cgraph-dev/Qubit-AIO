"""The dead-symbol detector must see framework/string wiring, not just Name loads.

Each false-positive class is a real construct -- a pytest fixture decorator or
parameter, a collected ``Test*`` class, a route decorator, a lazy lookup call,
or a module-level dispatch table -- so the detector must key off that construct
and still report a plain uncalled function.
"""
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dead_symbols.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_dead_symbols", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _facts(src: str, filename: str = "tests/test_sample.py"):
    mod = _load()
    tree = mod.parse_tree(src, filename)
    v = mod.FileFacts()
    v.visit(tree)
    return mod, v


FIXTURE_AND_CLASS_SRC = '''
import pytest


@pytest.fixture
def explicit_static_config(tmp_path):
    return tmp_path


@pytest_asyncio.fixture
async def cron_owner(monkeypatch):
    return monkeypatch


def loopback_app():
    return "undecorated fixture"


class TestPasswordLoginRoute:
    def test_valid(self, loopback_app):
        assert loopback_app


class TestPatchReplace:
    def test_replace(self):
        assert True


class TestRateLimit:
    def __init__(self):
        self.limit = 1


class NotCollected:
    def test_nope(self):
        assert True
'''


def test_fixtures_and_pytest_collected_classes_are_detected_by_construct():
    _, v = _facts(FIXTURE_AND_CLASS_SRC)
    assert {"explicit_static_config", "cron_owner"} <= v.fixture_defs
    assert "loopback_app" in v.param_names
    assert {"TestPasswordLoginRoute", "TestPatchReplace"} <= v.pytest_classes
    # A Test* class with __init__ is not collected, and neither is a plain class.
    assert "TestRateLimit" not in v.pytest_classes
    assert "NotCollected" not in v.pytest_classes


ROUTE_SRC = '''
@router.post("/api/auth/ws-ticket")
def ws_ticket():
    return {}


@app.get(path="/healthz")
def healthz():
    return {}


@cache.delete(key)
def not_a_route():
    return {}


def unregistered():
    return {}
'''


def test_route_decorator_needs_a_route_verb_and_a_path():
    _, v = _facts(ROUTE_SRC, "qubit_cli/web_routers/sample.py")
    assert {"ws_ticket", "healthz"} <= v.route_defs
    assert "not_a_route" not in v.route_defs
    assert "unregistered" not in v.route_defs


DISPATCH_SRC = '''
_DURABLE_CLAIM_OPS = {
    "drop": ("drop_completion_delivery", "Could not drop durable completion claim"),
}


def pick():
    merge = _lazy("qubit_cli.profile_model_seed", "merge_endpoint_seed")
    return merge


def drop_completion_delivery():
    return True


def merge_endpoint_seed():
    return {}


def truly_dead():
    return None
'''


def test_string_dispatch_and_lazy_lookup_count_as_uses():
    _, v = _facts(DISPATCH_SRC, "gateway/sample.py")
    assert "drop_completion_delivery" in v.dispatch_refs
    assert "merge_endpoint_seed" in v.dispatch_refs
    assert "drop" not in v.dispatch_refs  # a dict KEY is a command, not a callee
    assert "truly_dead" not in v.dispatch_refs
    assert not v.uses.get("truly_dead")


def test_classification_excludes_framework_constructs_but_not_a_dead_function():
    mod, v = _facts(FIXTURE_AND_CLASS_SRC)
    module = "tests/test_sample.py"
    fixtures = {(module, "explicit_static_config")}
    classes = {(module, "TestPasswordLoginRoute")}
    cases = {
        "explicit_static_config": (mod.Definition(module, "explicit_static_config", "function", 1), "pytest-fixture"),
        "loopback_app": (mod.Definition(module, "loopback_app", "function", 1), "pytest-fixture"),
        "TestPasswordLoginRoute": (mod.Definition(module, "TestPasswordLoginRoute", "class", 1), "pytest-class"),
    }
    for name, (defn, prefix) in cases.items():
        reason = mod._framework_exclusion(defn, fixtures, classes, set(), v.param_names)
        assert reason and reason.startswith(prefix), (name, reason)

    route = mod.Definition("qubit_cli/web_routers/sample.py", "ws_ticket", "function", 1)
    reason = mod._framework_exclusion(route, set(), set(),
                                      {("qubit_cli/web_routers/sample.py", "ws_ticket")}, set())
    assert reason and reason.startswith("route")

    dead = mod.Definition(module, "truly_dead", "function", 1)
    assert mod._framework_exclusion(dead, fixtures, classes, set(), v.param_names) is None
    assert mod._seam(dead, set()) is None


E2E_SRC = FIXTURE_AND_CLASS_SRC + ROUTE_SRC + DISPATCH_SRC


def test_report_keeps_schema_and_reports_only_the_true_positive(tmp_path, monkeypatch, capsys):
    mod = _load()
    root = tmp_path / "checkout"
    tests = root / "qubit_runtime" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_sample.py").write_text(E2E_SRC)

    upstream = """
def probe():
    explicit_static_config
    cron_owner
    loopback_app
    TestPasswordLoginRoute
    TestPatchReplace
    ws_ticket
    healthz
    drop_completion_delivery
    merge_endpoint_seed
    truly_dead
"""
    monkeypatch.setattr(mod, "find_upstream", lambda *_: None)
    monkeypatch.setattr(mod, "read_pin", lambda *_: {"upstream.py": upstream})
    monkeypatch.setattr(sys, "argv", ["check_dead_symbols.py",
                                      "--root", str(root), "--rev", "deadbeef",
                                      "--json", "-", "--no-cache"])

    rc = mod.main()
    assert rc == 1, "a true dead symbol must fail the gate"
    report = json.loads(capsys.readouterr().out)
    for key in ("dead_count", "dead_symbols", "excluded_benign", "excluded_count",
                "definitions_examined", "dead_of_which_test_seams"):
        assert key in report
    assert report["dead_count"] == 1
    assert [h["name"] for h in report["dead_symbols"]] == ["truly_dead"]
    reasons = {h["name"]: h["exclusion"] for h in report["excluded_benign"]}
    assert reasons["explicit_static_config"].startswith("pytest-fixture")
    assert reasons["TestPasswordLoginRoute"].startswith("pytest-class")
    assert reasons["ws_ticket"].startswith("route")
    # String-bound helpers have a real use site, so they are neither dead nor excluded.
    assert "drop_completion_delivery" not in reasons
    assert "merge_endpoint_seed" not in reasons
