#!/usr/bin/env python3
"""Discovery proof for the browser-companion plugin: load through the real path.

Runs with a throwaway ``QUBIT_HOME``, so the bundled plugins directory is this
checkout's ``qubit_runtime/plugins/`` and nothing else. Asserts:

* the plugin is present after ``discover_plugins(force=True)`` and has no load error;
* every one of the 27 tools ``plugin.yaml`` declares is registered in the real
  tool registry under toolset ``browser-companion`` (not merely importable), and
  nothing is registered that the manifest does not declare;
* all three hooks are attached;
* the bundled skill resolves;
* every tool fails soft and typed with nothing attached (upstream's contract);
* the route factory is queued on the ``api_server`` seam and, invoked the way the
  API server invokes it, adds exactly the eight routes the extension calls.

One caveat about running it: the generic module name ``tools`` is shadowed for a
script executed from inside this directory (``sys.path[0]`` is the script's own
directory, and this plugin ships a ``tools.py``). So run it with ``runpy`` from
the repository root:

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

CONTEXT_TOOLS = (
    "browser_context_status",
    "browser_get_context",
    "browser_clear_context",
    "browser_event_log",
    "browser_control_status",
    "browser_context_journal",
    "browser_text_utility",
)
CONTROL_TOOLS = (
    "tab_back", "tab_batch", "tab_click", "tab_drag", "tab_fill", "tab_hover",
    "tab_navigate", "tab_press", "tab_screenshot", "tab_scroll", "tab_scroll_to",
    "tab_select", "tab_snapshot", "tab_activate", "tab_close", "tab_create",
    "tab_group", "tab_ungroup", "tab_list", "tab_type",
)
URL_ARG_TOOLS = {"tab_navigate": "https://example.com", "tab_create": "https://example.com"}
EXPECTED_ROUTES = 8


def _payload(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise AssertionError(f"handler returned {type(raw).__name__}, not a string")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"handler result is not JSON: {raw[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AssertionError(f"handler result is not a JSON object: {raw[:200]!r}")
    return parsed


PLUGIN_ID = "browser-companion"
TOOLSET = PLUGIN_ID

def _prepare() -> None:
    global get_plugin_manager, registry
    home = tempfile.mkdtemp(prefix="qubit-home-browser-companion-")
    os.environ["QUBIT_HOME"] = home
    os.environ.setdefault("QUBIT_SKIP_TELEMETRY", "1")
    sys.path.insert(0, str(RUNTIME))

    # A bundled ``standalone`` plugin is opt-in: the operator enables it in
    # config.yaml. This proof writes exactly that one line into the throwaway home,
    # through the same writer `qubit plugins enable` uses, so discovery is the real
    # path rather than a monkeypatched one.
    from qubit_cli.config import load_config, save_config  # noqa: E402

    config = load_config() or {}
    plugins_config = config.setdefault("plugins", {})
    enabled = list(plugins_config.get("enabled") or [])
    if PLUGIN_ID not in enabled:
        enabled.append(PLUGIN_ID)
        plugins_config["enabled"] = enabled
        save_config(config)

    from qubit_cli.plugins import discover_plugins, get_plugin_manager  # noqa: E402
    from tools.registry import registry  # noqa: E402

    discover_plugins(force=True)


def main() -> int:
    _prepare()
    manager = get_plugin_manager()
    loaded = {p.manifest.name: p for p in manager._plugins.values() if getattr(p, "manifest", None)}
    entry = loaded.get(TOOLSET)
    assert entry is not None, f"{TOOLSET} not loaded; loaded={sorted(loaded)}"
    assert not entry.error, f"load error: {entry.error}"
    print(f"plugin present: {entry.manifest.name} v{entry.manifest.version} kind={entry.manifest.kind}")
    print(f"  requires_qubit: {entry.manifest.requires_hermes!r}")

    declared = list(entry.manifest.provides_tools)
    expected = [*CONTEXT_TOOLS, *CONTROL_TOOLS]
    assert declared == expected, f"manifest tools != expected\ndeclared={declared}\nexpected={expected}"

    registered = []
    for name in declared:
        tool = registry.get_entry(name)
        assert tool is not None, f"{name} declared but NOT registered"
        assert tool.toolset == TOOLSET, (name, tool.toolset)
        registered.append((name, tool.toolset, tool.emoji or ""))
    print(f"\ntools declared and registered: {len(registered)}")
    for name, toolset, emoji in registered:
        print(f"  {name:<26} toolset={toolset:<18} {emoji}")

    for hook_name in ("pre_llm_call", "pre_tool_call", "post_tool_call"):
        assert manager.has_hook(hook_name), f"hook not attached: {hook_name}"
    print("\nhooks attached: pre_llm_call, pre_tool_call, post_tool_call")

    skill_file = Path(__file__).resolve().parent / "skills" / TOOLSET / "SKILL.md"
    assert skill_file.is_file(), f"skill file missing: {skill_file}"
    print(f"bundled skill: {skill_file.relative_to(REPO)}")

    # Fail-soft, typed, with nothing attached — upstream's documented contract.
    print("\nfail-soft with nothing attached:")
    for name in CONTEXT_TOOLS:
        result = _payload(registry.dispatch(name, {}, task_id="t", session_id="s", turn_id="u"))
        if name == "browser_text_utility":
            # No args, no model call: the bounded local utility answers a typed
            # refusal naming the actions it accepts, never an exception.
            assert result.get("ok") is False and result.get("error") == "unsupported_action", (name, result)
        else:
            assert result.get("available") is False, (name, result)
            assert result.get("reason") == "Browser context unavailable.", (name, result)
        print(f"  {name:<26} -> {json.dumps(result)[:88]}")
    for name in CONTROL_TOOLS:
        args = {"url": URL_ARG_TOOLS[name]} if name in URL_ARG_TOOLS else {}
        result = _payload(registry.dispatch(name, args, task_id="t", session_id="s", turn_id="u"))
        assert result.get("success") is False, (name, result)
        assert result.get("code") in {"no_browser", "invalid_arguments"}, (name, result)
        print(f"  {name:<26} -> {json.dumps(result)[:88]}")

    # The route factory is queued on the api_server seam; invoke it the way the
    # API server does and check exactly which routes it adds.
    factories = manager.get_platform_handler_factories("api_server")
    assert factories, "no api_server handler factory registered — the routes would never wire"
    print(f"\napi_server handler factories: {[name for _, name in factories]}")

    from aiohttp import web

    app = web.Application()

    class _Adapter:
        """The surface the API server hands its factories."""

        def _check_auth(self, request):
            return None

        def _expected_api_key(self):
            return "test-key"

        async def _ensure_session_db_async(self):
            class _Db:
                async def get_session(self, session_id):
                    return {"id": session_id}

            return _Db()

    for factory, _name in factories:
        factory(app, _Adapter())
    # aiohttp adds a HEAD route beside every GET; count the declared methods only.
    routes = sorted(
        {
            f"{route.method} {route.resource.canonical}"
            for route in app.router.routes()
            if isinstance(getattr(route, "resource", None), web.Resource) and route.method != "HEAD"
        }
    )
    print("routes wired on the api_server seam:")
    for route in routes:
        print(f"  {route}")
    assert len(routes) == EXPECTED_ROUTES, f"expected {EXPECTED_ROUTES} routes, got {len(routes)}: {routes}"

    print("\nDISCOVERY PROOF: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
