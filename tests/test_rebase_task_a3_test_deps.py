"""Task A3 (v0.21.5 rebase) — vendored-suite test deps must be declared, not guarded.

The v0.21.5 vendored suite imports several dependencies at module import time.
Some are genuine runtime deps and some are only needed by the tests; none may be
"fixed" by wrapping the import in ``pytest.importorskip`` because upstream imports
them unconditionally, so a guard would silently drop coverage instead of proving
the capability.

Classification under test:

* ``truststore`` / ``tomli-w`` -- genuine runtime deps, declared in the product
  ``[project].dependencies`` (``agent/ssl_verify.py`` trust injection; PM/relay
  TOML writes). Already covered by ``test_rebase_task12_deps_entrypoint``.
* ``mcp`` -- optional at runtime (lazy SDK; ``tools/mcp_tool.py`` degrades without
  it) and a *test-only* dep for the product env. Upstream keeps it in the ``mcp``
  extra and the ``dev`` dependency-group; the product env must declare it in the
  ``[test]`` extra.
* ``jsonschema`` -- test-only; upstream declares it nowhere (the tools import it
  lazily with a fallback), but the tests validate against it directly.
* ``fal-client`` -- the optional Fal provider SDK (upstream ``fal`` extra); the
  vendored ``test_fal_sdk.py`` imports it directly.
* ``anthropic`` -- test-only, already pinned by ``test_rebase_task_r4_anthropic_sdk``.

Hermetic: reads TOML + source text only. No network, no model, no venv.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"
PRODUCT_PYPROJECT = REPO / "pyproject.toml"
VENDORED_PYPROJECT = RUNTIME / "pyproject.toml"

# Test-only deps the A3 collection failures demanded in the product test env.
TEST_ONLY_PINS = {
    "mcp": "mcp==2.0.0",
    "jsonschema": "jsonschema==4.26.0",
    "fal-client": "fal-client==0.13.1",
}

# Genuine runtime deps that must stay in the product runtime deps, not the extra.
RUNTIME_PINS = {
    "truststore": "truststore==0.10.4",
    "tomli-w": "tomli-w==1.2.0",
}

# Vendored modules whose direct, unconditional imports caused the collection
# errors. A ``pytest.importorskip`` here would weaken the test, so pin their
# absence.
UNGUARDED_IMPORTS = {
    "qubit_runtime/tests/agent/test_ssl_transport_contracts.py": (
        "from truststore._ssl_constants import _original_SSLContext",
    ),
    "qubit_runtime/tests/tools/test_schema_boolean_required.py": (
        "from jsonschema import Draft202012Validator",
    ),
    "qubit_runtime/tests/tools/test_mcp_tool.py": (
        "from tools.mcp_tool import CreateMessageResultWithTools, SamplingHandler, ToolUseContent",
    ),
    "qubit_runtime/tests/tools/test_fal_sdk.py": ("import fal_client",),
}


def _load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _normalized(dep: str) -> str:
    """Lowercased, underscore-normalized requirement name without version/extras/markers."""
    name = dep.split(";")[0].strip()
    name = re.sub(r"\[.*?\]", "", name)
    name = re.split(r"[<>=!~ ]", name, maxsplit=1)[0]
    return name.lower().replace("_", "-").strip()


def _dep_map(deps: list[str]) -> dict[str, str]:
    return {_normalized(d): d for d in deps}


def test_product_test_extra_declares_the_vendored_test_only_deps():
    extra = _load_toml(PRODUCT_PYPROJECT)["project"]["optional-dependencies"]["test"]
    by_name = _dep_map(extra)
    for name, pin in TEST_ONLY_PINS.items():
        assert by_name.get(name) == pin, f"{name}: expected {pin!r}, got {by_name.get(name)!r} in {extra}"


def test_runtime_deps_stay_declared_for_runtime_not_the_test_extra():
    product = _load_toml(PRODUCT_PYPROJECT)
    runtime = _dep_map(product["project"]["dependencies"])
    test_extra = _dep_map(product["project"]["optional-dependencies"]["test"])
    for name, pin in RUNTIME_PINS.items():
        assert runtime.get(name) == pin, f"{name}: expected {pin!r} in runtime deps, got {runtime.get(name)!r}"
        assert name not in test_extra, f"{name} is a runtime dep and must not be duplicated into [test]"


def test_declared_pins_match_vendored_extras():
    """The declared pins mirror the vendored extra/dev-group classification."""
    doc = _load_toml(VENDORED_PYPROJECT)
    extras = doc["project"]["optional-dependencies"]
    assert extras["mcp"] and any(_normalized(d) == "mcp" for d in extras["mcp"])
    assert extras["fal"] == ["fal-client==0.13.1"], extras["fal"]
    dev = doc["dependency-groups"]["dev"]
    assert any(_normalized(d) == "mcp" for d in dev), dev


def test_imports_are_declared_not_guarded():
    """The failing imports stay unconditional -- no ``importorskip`` weakening."""
    for rel, imports in UNGUARDED_IMPORTS.items():
        source = (REPO / rel).read_text(encoding="utf-8")
        assert "importorskip" not in source, (
            f"{rel} must not importorskip the dependency; declare it in [test] instead"
        )
        for literal in imports:
            assert literal in source, f"{rel} no longer contains {literal!r}"
