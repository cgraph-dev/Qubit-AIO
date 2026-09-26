"""Task N2 (v0.21.5 rebase) — the PM cluster is environment, pinned in place.

The triaged `pm` failures are not rebase regressions: `qubit_runtime/pm/*` and
`qubit_runtime/tests/pm/*` are byte-identical to the pinned upstream
(`69948c0057`) after the deterministic rename, so no product behaviour drifted.

Two independent environment constraints remain, and this test pins how they are
declared rather than papered over:

* The checked-in PM runtime is Python 3.14 only (`pm/pyproject.toml`
  `requires-python = ">=3.14,<3.15"`; `pm/uv.lock` `requires-python = "==3.14.*"`).
  On a 3.12 host every runtime-staging test errors with
  ``pm.package.InstallError: uv sync exited 2``. The suite must *skip* those
  tests on an unsupported host -- the same guard upstream already applies in
  ``tests/pm/test_cold_runtime_e2e.py`` -- instead of reporting a false failure.
* The isolated PM worker (`pm/worker.py`) imports ``truststore`` at process
  start and is spawned with ``python -I`` while
  ``pm.runtime.runtime_environment()`` strips every ``PYTHON*`` variable, so the
  worker imports it from the interpreter running the tests. The product ``[test]``
  extra therefore carries ``truststore``/``tomli-w`` at the exact runtime pins.

Hermetic: reads TOML + source text only. No network, no model, no venv.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"
PRODUCT_PYPROJECT = REPO / "pyproject.toml"
PM_PYPROJECT = RUNTIME / "pm" / "pyproject.toml"
PM_WORKER = RUNTIME / "pm" / "worker.py"
PM_FIXTURES = RUNTIME / "tests" / "pm" / "_fixtures.py"

# The PM runtime deps the isolated worker imports from the test interpreter.
WORKER_TEST_PINS = {"truststore": "truststore==0.10.4", "tomli-w": "tomli-w==1.2.0"}


def _load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _normalized(dep: str) -> str:
    name = dep.split(";")[0].strip()
    name = re.sub(r"\[.*?\]", "", name)
    name = re.split(r"[<>=!~ ]", name, maxsplit=1)[0]
    return name.lower().replace("_", "-").strip()


def test_pm_runtime_is_python_314_only():
    """Documents the environment constraint behind `uv sync exited 2`."""
    requires = _load_toml(PM_PYPROJECT)["project"]["requires-python"]
    assert "3.14" in requires and "3.15" in requires, requires
    assert not re.search(r"3\.(?:1[0-3])", requires), requires


def test_isolated_worker_python_fixture_skips_below_314():
    """The runtime-staging fixture must skip, not error, on an older host."""
    source = PM_FIXTURES.read_text(encoding="utf-8")
    assert "the checked-in PM runtime currently requires Python 3.14" in source
    assert re.search(r"sys\.version_info\[:2\]\s*!=\s*\(3,\s*14\)", source), source


def test_pm_worker_imports_truststore():
    """The worker's real import is why the test interpreter must carry it."""
    source = PM_WORKER.read_text(encoding="utf-8")
    assert re.search(r"^\s*import truststore\b", source, re.MULTILINE), source


def test_product_test_extra_declares_the_isolated_worker_deps():
    product = _load_toml(PRODUCT_PYPROJECT)
    runtime = {_normalized(d): d for d in product["project"]["dependencies"]}
    extra = {_normalized(d): d for d in product["project"]["optional-dependencies"]["test"]}
    for name, pin in WORKER_TEST_PINS.items():
        assert runtime.get(name) == pin, f"{name}: runtime pin is {runtime.get(name)!r}"
        assert extra.get(name) == pin, (
            f"{name}: the [test] extra must carry the isolated worker dep at {pin!r}, got {extra.get(name)!r}"
        )
