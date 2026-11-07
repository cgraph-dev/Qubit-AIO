#!/usr/bin/env python3
"""Discovery proof for the browser-companion plugin: load through the real path.

Runs with a throwaway ``QUBIT_HOME``, so the bundled plugins directory is this
checkout's ``qubit_runtime/plugins/`` and nothing else. Asserts the plugin is
present after ``discover_plugins(force=True)``, that all three declared tools are
registered in the real tool registry (not merely importable), that both hooks are
attached, that the bundled skill is reachable, and that a call with no attached
context answers the documented fail-soft shape.

One caveat about running it: the generic module name ``tools`` is shadowed for a
script executed from inside this directory (``sys.path[0]`` is the script's own
directory, and this plugin ships a ``tools.py``). So run it with ``runpy`` from
the repository root, or drive it through a test:

    .venv/bin/python -c "import runpy; runpy.run_path(
        'qubit_runtime/plugins/browser-companion/discovery_check.py', run_name='__main__')"
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # <repo>/qubit_runtime/plugins/browser-companion/x.py
RUNTIME = REPO / "qubit_runtime"

def main() -> int:
    # Keep every side effect inside main(): this file lives in a shipped plugin
    # package, so importing it (the packaging import-graph smoke) must be inert.
    home = tempfile.mkdtemp(prefix="qubit-home-browser-companion-")
    os.environ["QUBIT_HOME"] = home
    os.environ.setdefault("QUBIT_SKIP_TELEMETRY", "1")
    sys.path.insert(0, str(RUNTIME))

    from qubit_cli.plugins import discover_plugins

    discover_plugins(force=True)

    from qubit_cli.plugins import get_plugin_manager
    from tools.registry import registry

    manager = get_plugin_manager()
    loaded = {p.manifest.name: p for p in manager._plugins.values() if getattr(p, "manifest", None)}
    entry = loaded.get("browser-companion")
    assert entry is not None, f"browser-companion not loaded; loaded={sorted(loaded)}"
    assert not entry.error, f"load error: {entry.error}"

    declared = list(entry.manifest.provides_tools)
    assert declared == ["browser_context_status", "browser_get_context", "browser_clear_context"], declared

    registered = []
    for name in declared:
        tool = registry.get_entry(name)
        assert tool is not None, f"{name} declared but NOT registered"
        assert tool.toolset == "browser-companion", (name, tool.toolset)
        registered.append((name, tool.toolset))

    # Declared and registered sets equal in both directions: no phantom, no extra.
    plugin_tools = sorted(
        n for n in manager._plugin_tool_names
        if (found := registry.get_entry(n)) is not None and found.toolset == "browser-companion"
    )
    assert plugin_tools == sorted(declared), (plugin_tools, declared)

    # Both hooks must be attached from this plugin's hook module.
    hook_names = sorted({
        hook for hook in ("pre_llm_call", "pre_tool_call", "post_tool_call")
        if any("browser" in getattr(cb, "__module__", "") and getattr(cb, "__module__", "").endswith(".hooks")
               for cb in manager._hooks.get(hook, []))
    })
    assert hook_names == ["pre_llm_call", "pre_tool_call"], hook_names

    # Fail-soft: no context attached, so every tool answers unavailable and never raises.
    module = entry.module
    assert module is not None, "plugin module not retained by the loader"
    for name in declared:
        out = json.loads(getattr(module.tools, name)({}))
        assert out.get("available") is False, (name, out)
        assert "unavailable" in str(out.get("reason", "")).lower(), (name, out)

    skill = manager._plugin_skills.get("browser-companion:browser-context")
    assert skill is not None, sorted(manager._plugin_skills)

    print("QUBIT_HOME =", home)
    print("plugin:", entry.manifest.name, entry.manifest.version, "source:", entry.manifest.source,
          "kind:", entry.manifest.kind, "enabled:", entry.enabled)
    print("tools registered:", registered)
    print("hooks attached:", hook_names)
    print("skill:", "browser-companion:browser-context", "->", Path(skill["path"]).name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
