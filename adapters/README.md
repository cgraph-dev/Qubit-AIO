# `adapters/` — the Qubit Adapter Registry

## What an adapter IS here

An **adapter** is a pluggable extension *seam* through which Qubit acquires a
capability it does not implement natively: another agent's skills, a platform's
messaging channel, an MCP server's tools, a plugin pack, or a tool set. The point
of this folder is provenance and identity — when a feature is ported from an
upstream agent (Hermes today; OpenClaw, PicoClaw, … later), the *port* lives
under an adapter named after its source, so the tree reads as "Qubit, with an
adapters/ folder" rather than "a fork of X".

This folder is a **registry + documentation scaffold**. It does **not** own any
runtime code today. It records, for each of the five kinds, the *real* discovery
seam that already exists in the tree — the file and function the runtime calls to
find and load that kind.

## Registry contract

Each entry in `registry.yaml` has these fields:

| field       | meaning |
|-------------|---------|
| `name`      | adapter identifier (`builtin`, `hermes`, `openclaw`, `picoclaw`, …) |
| `kind`      | one of `skills`, `plugins`, `mcps`, `channels`, `tools` |
| `status`    | `shipped` (code exists now) or `planned` (declared seam, no code yet) |
| `source`    | where the kind's definitions come from (`builtin`, `bundled`, `entry-point`, …) |
| `seam`      | `path/to/file.py:LINE` — the discovery entry point the runtime actually calls |
| `symbol`    | the discovery function/method name at that seam |

The schema is intentionally flat: one entry per (name, kind), machine-readable,
validated by `adapters/registry.py`.

## The five discovery seams (real, current)

These are the actual functions the runtime calls today. Nothing below has moved;
these paths are where discovery lives *right now*.

### 1. `skills`

- **Seam:** `qubit_runtime/tools/skills_tool.py:248` — `skills_list()` (public entry)
- **Discovery worker:** `qubit_runtime/tools/skills_tool.py:235` — `_find_all_skills()`
- **Roots:** `qubit_runtime/tools/skills_tool.py:177` — `_skill_search_dirs()` (walks the active profile's `skills/` dir plus bundled `optional-skills/`)
- **Plugin-contributed skills** are merged in from `get_plugin_manager().list_plugin_skill_metadata()` (same file, line 256).

### 2. `plugins`

- **Seam:** `qubit_runtime/qubit_cli/plugins.py:1770` — `discover_plugins()`
- **Loader:** `qubit_runtime/qubit_cli/plugins.py:1279` — `PluginManager.discover_and_load()`
- **Manifest scan:** `qubit_runtime/qubit_cli/plugins_discovery.py:300` — `collect_directory_manifests()`
- **Entry-point scan:** `qubit_runtime/qubit_cli/plugins_discovery.py:182` — `discover_entrypoint_manifests()` (group `hermes_agent.plugins`, line 32)
- **Bundled dir:** `qubit_runtime/plugins/`; a directory plugin needs `plugin.yaml` + `__init__.py` exposing `register(ctx)`.

### 3. `mcps`

- **Seam:** `qubit_runtime/tools/mcp_tool_discovery.py:595` — `discover_mcp_tools()`
- **Config source:** `_config._load_mcp_config()` (called at line 604); servers come from the MCP config, not a directory scan.
- **Registration sink:** `qubit_runtime/tools/mcp_tool_registration.py` — MCP tools land in the shared tool registry.

### 4. `channels`

- **Registry:** `qubit_runtime/gateway/platform_registry.py:320` — `PlatformRegistry.register(PlatformEntry)`
- **Instantiation:** `qubit_runtime/gateway/platform_registry.py:414` — `PlatformRegistry.create_adapter()`
- **Plugin registration API:** `qubit_runtime/qubit_cli/plugins.py:777` — `PluginContext.register_platform()`
- **Bundled platform scan:** `qubit_runtime/gateway/config.py:280` — `Platform._scan_bundled_plugin_platforms()`
- **Per-platform convention:** each `qubit_runtime/plugins/platforms/<name>/adapter.py` exposes `register(ctx)` (e.g. `.../irc/adapter.py:585`, `.../matrix/adapter.py:3294`).

### 5. `tools`

- **Seam:** `qubit_runtime/tools/registry.py:96` — `discover_builtin_tools()` (AST-scans `tools/*.py` + `tools/*/tool.py` for module-level `registry.register(...)` and imports them)
- **Registry singleton:** `qubit_runtime/tools/registry.py:1015` — `registry = ToolRegistry()`
- **Registration sink:** `qubit_runtime/tools/registry.py:666` — `ToolRegistry.register()`

> Paths are relative to the repo root. Line numbers are from commit `9c28ea6b`
> (branch `v5`); `adapters/registry.py`'s test asserts these seam files still
> exist so a move is caught loudly rather than silently.

## How a future adapter plugs in

When a feature is ported from `hermes`, `openclaw`, `picoclaw`, etc.:

1. Add an entry to `registry.yaml` with `name: <source>`, the `kind`, and the
   `seam` it will hook into (the seam is one of the five above — an adapter
   reuses Qubit's existing discovery machinery; it does not invent a parallel one).
2. Put the ported code under `adapters/<source>/` (the folder is the namespace; it
   is *not* on `sys.path` today, so import it as a package or install it as a
   plugin/MCP per the kind's seam).
3. Flip `status: planned → shipped` and record the concrete module path.

**Current shipping decision:** for `skills`/`plugins`/`channels`/`tools`, the
lowest-friction path is for the ported adapter to *be* a plugin
(`plugins/<source>/` with `register(ctx)` registering via `PluginContext`), because
plugins already plug into all four seams. `adapters/<source>/` then documents and
re-exports it. For `mcps`, the adapter is an MCP server config entry. This keeps
the "we built the adapter" identity without a second discovery mechanism.

## Existing code has NOT moved

`qubit_runtime/acp_adapter/` is the one adapter-shaped module that already ships.
It stays exactly where it is. Moving or renaming it would force importer churn
across the tree (`qubit_runtime/tests/acp_adapter`, entry points, docs) for zero
functional gain, and this scaffold is explicitly a registry + docs step. Existing
modules are recorded here as "lives elsewhere" until a planned migration can be
done deliberately, as its own change with its own gate run.

See the **proposals** section of the owning PR for the eventual
`acp_adapter → adapters/acp/` migration; it is deliberately not performed here.
