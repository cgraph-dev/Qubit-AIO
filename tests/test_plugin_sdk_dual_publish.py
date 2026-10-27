"""Both plugin-SDK hosts publish canonical + legacy globals.

`frontend/qubit-web` is the live dashboard; `qubit_runtime/web` is the vendored
SPA that can also be served. Plugin bundles in the wild read the legacy
`__HERMES_*` names, while first-party code reads `__QUBIT_*`; either single-name
host breaks one side. This guard pins the dual publish without running a build.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_frontend_host_publishes_both_names():
    src = _source("frontend/qubit-web/src/plugins/registry.ts")
    for name in ("__QUBIT_PLUGIN_SDK__", "__QUBIT_PLUGINS__",
                 "__HERMES_PLUGIN_SDK__", "__HERMES_PLUGINS__"):
        assert f"window.{name} =" in src, name


def test_vendored_host_publishes_both_names():
    src = _source("qubit_runtime/web/src/plugins/registry.ts")
    for name in ("__HERMES_PLUGIN_SDK__", "__HERMES_PLUGINS__"):
        assert f"window.{name} =" in src, name
    # The vendored SPA must also expose the canonical names first-party code
    # uses; the legacy pair stays as the external-bundle alias.
    for name in ("__QUBIT_PLUGIN_SDK__", "__QUBIT_PLUGINS__"):
        assert f"window.{name} =" in src, name
