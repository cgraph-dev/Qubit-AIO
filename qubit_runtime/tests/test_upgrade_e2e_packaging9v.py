"""Focused pins for the upgrade E2E shared-root-cause fixes.

Two shipped-module regressions surfaced by ``test_fresh_process_entrypoints``:

* the whole ``evals/`` tree was in ``packages.find``, including developer probe
  scripts that run ``argparse``/``sys.argv`` at import time, so the "every shipped
  module imports" smoke could never pass;
* ``plugins/browser-companion/discovery_check.py`` ran plugin discovery at module
  import instead of under ``main()``.
"""

from __future__ import annotations

import importlib.util
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _include() -> set[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(cfg["tool"]["setuptools"]["packages"]["find"]["include"])


def test_only_importable_eval_libraries_ship():
    include = _include()
    # The product CLI imports exactly these two libraries.
    assert {"evals.bench", "evals.optimize"} <= include
    # Shipping the `evals` package marker would also ship every top-level probe script
    # (argparse/argv at import). Ship only the importable libraries.
    assert "evals" not in include and "evals.*" not in include
    # Script-only trees must not ship either.
    for script_pkg in (
        "evals.compaction",
        "evals.browser_use",
        "evals.postmortem",
        "evals.desktop_bug_campaign",
        "evals.readtool",
    ):
        assert script_pkg not in include, f"{script_pkg} is a probe-script tree, not an importable library"

    import evals.bench  # noqa: F401  product import surface
    import evals.optimize  # noqa: F401


def test_browser_companion_discovery_check_is_import_safe():
    path = ROOT / "plugins" / "browser-companion" / "discovery_check.py"
    before = dict(os.environ)
    spec = importlib.util.spec_from_file_location("_browser_companion_discovery_check", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must be inert: no discovery, no QUBIT_HOME rewrite

    assert os.environ.get("QUBIT_HOME") == before.get("QUBIT_HOME")
    assert callable(mod.main)
