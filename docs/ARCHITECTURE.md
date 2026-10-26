# Qubit ARCHITECTURE — the layers that exist, and where each lives

**Status:** structural inventory, re-authored 2026-10-19. **Not a verdict.** Nothing here is
`VERIFIED`, `M1`, `M2`, `CODE_READY`, or `COMPLETE`, and nothing here claims capability parity
— see `docs/STATUS.md` for current truth and `docs/FROZEN_TARGET.md` for the finish line.

This file describes **only what is in the tree**. Every path, count, and symbol named below is
followed by the command that produced it. If a number here disagrees with a fresh run of that
command, the command wins and this file is stale.

> **Why this file was re-written, not restored.** The previous `docs/ARCHITECTURE.md` was
> deleted in `eafb54e26` (owner English-only ruling, "Qubit will author its own doc set"). The
> surviving current docs do not carry its content, so this is a new document sourced from the
> worktree, not a resurrection of the deleted one.

## Identity

Qubit is a personal AI agent — not "a fork". Identity is canonical in `docs/IDENTITY.md` and is
not restated here; upstream lineage is provenance only.

## Top-level layout

```
qubit_runtime/   the vendored agent runtime (upstream lineage, Qubit namespace)
voicebridge/     the voice control surface
frontend/        dashboard pages + the SPA packages (qubit-web, qubit-shared, apps)
adapters/        the adapter registry — discovery seams, not runtime code (adapters/README.md)
skills/          built-in skills
config/  scripts/  systemd/  templates/  tests/  tools/  benchmarks/
```

Verify the directory list:

```bash
ls -d */
```

## The runtime (`qubit_runtime/`)

The runtime is the largest layer and holds the agent core, gateway, tools, and CLI.

| Path | Holds |
|---|---|
| `qubit_runtime/run_agent.py` | agent facade; the turn loop is in `qubit_runtime/agent/` |
| `qubit_runtime/model_tools.py` | tool orchestration + discovery |
| `qubit_runtime/toolsets.py` | toolset table + core tool list |
| `qubit_runtime/cli.py` | CLI REPL and slash dispatch |
| `qubit_runtime/qubit_cli/` | CLI subcommands, setup, config, web server, `web_routers/` |
| `qubit_runtime/agent/` | turn phases, providers, memory, compression, prompt building |
| `qubit_runtime/gateway/` | messaging gateway; one adapter per platform in `platforms/` |
| `qubit_runtime/tools/` | tool implementations, auto-discovered |
| `qubit_runtime/tui_gateway/` | JSON-RPC backend for TUI + desktop |
| `qubit_runtime/plugins/` | plugin packages (memory, context engine, model providers, ...) |

```bash
for d in run_agent.py model_tools.py toolsets.py cli.py qubit_cli agent gateway tools tui_gateway plugins; do
  printf '%-18s %s\n' "$d" "$(test -e "qubit_runtime/$d" && echo present || echo ABSENT)"
done
```

### Engine boundary

`agent/runtime_port.py` is the engine-neutral turn contract: a `TurnRequest`
in, ordered `TurnEvent`s out (exactly one terminal `done` or `error`), with
capabilities and a cancel token. `agent/runtime_native.py` wraps the vendored
`AIAgent` (default, unchanged); `agent/runtime_registry.py` selects a backend
by name. `qubit runtime engines` and `qubit runtime run` are diagnostics only;
they do not replace the product chat/oneshot/gateway path. Later phases add
out-of-process engines behind the same port.


### Dashboard routers

`qubit_runtime/qubit_cli/web_server.py` mounts the dashboard routers; one module per surface
lives under `qubit_runtime/qubit_cli/web_routers/`.

```bash
ls qubit_runtime/qubit_cli/web_routers/*.py | wc -l   # router modules
grep -c 'include_router' qubit_runtime/qubit_cli/web_server.py  # mount sites
```

### Platform adapters

One adapter module per messaging platform:

```bash
ls qubit_runtime/gateway/platforms/*.py | wc -l
```

Adapter authoring rules live in `qubit_runtime/gateway/platforms/ADDING_A_PLATFORM.md`.

## The voice surface (`voicebridge/`)

`voicebridge/` is the voice control surface: a top-level package, separate from the runtime, and
**the dependency runs both ways**. `voicebridge/` imports the runtime in many modules, and the
runtime imports `voicebridge` from specific call sites (deferred, inside functions). Do not
assume a clean one-way boundary when moving code between them.

The pipeline (`config/voice.*.yaml`, `voicebridge/` helpers) and its roadmaps were documented in
`docs/archive/` material retired in `eafb54e26`; the surviving authority for audio/voice work is
in-tree config plus the `qubit-voice-mode` skill.

```bash
find voicebridge -name '*.py' -not -path '*/__pycache__/*' | wc -l
ls config/voice.example.yaml
```

## Home and profile resolution

User state (`config.yaml`, `.env`, logs, sessions) is profile-aware. Both resolvers live in one
module and are the only correct source for these paths — never hardcode a home directory.

```bash
grep -n 'def get_qubit_home\|def display_qubit_home' qubit_runtime/qubit_constants.py
```

## What this document deliberately does not contain

- **Milestones, dates, or completion percentages.** Those live in `docs/STATUS.md` (evidence-
  backed current truth) and `docs/FROZEN_TARGET.md` (the pinned finish line). Inventing them
  here would create a second, unsourced authority.
- **A parity claim.** The parity method and target are owned by `qubit_runtime`'s audit tooling
  and `docs/FROZEN_TARGET.md`; the reference dashboard and voice-actionability requirement are
  restated from the owner fleet-wide rule in `AGENTS.md` only.
- **Numbers that drift.** Where a count is given it is accompanied by its command; treat the
  command as canonical.
