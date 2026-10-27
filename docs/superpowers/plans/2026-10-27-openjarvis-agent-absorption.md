# OpenJarvis 8-agent absorption — Qubit-native roles

Date: 2026-10-27. Owner direction: **absorb**, do not become. OpenJarvis stays an
optional benchmark/engine; its eight agent behaviours become Qubit's own,
reachable through Qubit's one loop, one `QUBIT_HOME`, one authority chain, and
voice.

This closes the gap between `docs/IDENTITY.md` (“does not merely route to it or
wrap it … a wrapper that only forwards is not absorption”) and the current
out-of-process adapter.

## Non-goals

- No OpenJarvis import or subprocess in the role path; no copied OpenJarvis file.
- No second agent loop, session store, approval path, server or web HUD.
- No change to the default engine; no `HERMES_*`/`.hermes` rename.
- No prompt copied from upstream without a `THIRD_PARTY_NOTICES.md` record.
  Prompts here are authored Qubit-native from the documented mapping.
- No `VERIFIED` claim from this program.

## Mapping (8 roles)

| OpenJarvis agent | Qubit role | Kind |
|---|---|---|
| `orchestrator` | `orchestrator` | native role: delegation/MoA posture |
| `native_react` | `react` | native role: reason-act posture |
| `operative` | `operative` | native role: gateway/kanban/work state |
| `native_openhands` | `openhands` | native role: coding/sandbox work |
| `simple` | `simple` | native role: fast chat |
| `deep_research` | `researcher` | native role + existing research tools |
| `morning_digest` | `digest` | alias to existing `recipes/morning_digest` |
| `monitor_operative` | `monitor` | alias to existing `recipes/monitor_operative` |

Every role must be reachable by text **and** voice, and must run real work
through the existing authority path — not a forwarder.

## Contract — `qubit_runtime/roles/base.py`

```python
@dataclass(frozen=True)
class AgentRole:
    id: str
    title: str
    description: str
    system_prompt: str = ""
    toolsets: tuple[str, ...] = ()
    model_tier: str = "local"        # "local" | "detail" | "api" | ""
    confirmation: str = "preview"    # "preview" | "confirm" | "auto"
    voice_aliases: tuple[str, ...] = ()
    recipe_id: str = ""              # when set, dispatch the existing recipe
    source: str = ""                 # provenance, e.g. "openjarvis:orchestrator (mapped)"
    def to_wire(self) -> dict[str, Any]: ...
```

`registry.py` auto-discovers a module-level `ROLE` in every sibling module
(`roles/*.py`, excluding `base`, `registry`, `runner`, `__init__`).
`list_roles()` is sorted by id; `get_role(id)` raises typed `UnknownRoleError`.
Adding a role must never require editing a central list.

`runner.py` exposes `run_role(role_id, prompt="", *, engine="native", cfg=None,
json_mode=False)`:

- unknown role → `UnknownRoleError` (exit 2)
- `recipe_id` set → `recipes.runner.run_recipe(...)` (no duplicate prompt)
- otherwise → one turn through `RuntimePort`, native by default, with the
  role's `system_prompt` and `toolsets`; non-native engines either honour the
  bounded turn or refuse typed (`role_engine_unsupported`) — never silently drop
  toolsets or fall back.
- returns `{ok, role, engine, text, model, provider, elapsed_ms, ...}`.

## CLI and voice

- `qubit agent roles [--json]` — list id/title/toolsets/voice aliases.
- `qubit agent run <role> --prompt TEXT [--engine NAME] [--json]` — one turn;
  exit 0 ok, 2 unknown role/engine/refusal, 1 failed turn, 130 Ctrl-C.
- Voice: each role's `voice_aliases` registers an additive `CLI_BRIDGE_REGISTRY`
  entry that calls the real runner. No confirmation bypass; no typed-refusal
  masquerading as a capability.

## File ownership (one worktree per lane)

| Lane | Owns |
|---|---|
| core | `qubit_runtime/roles/{__init__,base,registry,runner}.py`, CLI wiring, generic voice bridge, `qubit_runtime/tests/roles/test_roles_core.py` |
| orchestrator | `qubit_runtime/roles/orchestrator.py`, `tests/roles/test_role_orchestrator.py` |
| react | `qubit_runtime/roles/react.py`, `tests/roles/test_role_react.py` |
| operative | `qubit_runtime/roles/operative.py`, `tests/roles/test_role_operative.py` |
| openhands | `qubit_runtime/roles/openhands.py`, `tests/roles/test_role_openhands.py` |
| simple+research | `simple.py`, `researcher.py`, `digest.py`, `monitor.py`, `tests/roles/test_role_pack.py` |
| audit | `scripts/audit_openjarvis_absorption.py`, `tests/test_openjarvis_absorption.py`, `docs/OPENJARVIS-8-AGENT-ABSORPTION.md` |

Role lanes must not edit the core, shared CLI, voice registry list, or each
other's files. If the core contract is missing when a lane starts, code against
the contract above and keep the module import-safe.

## Acceptance

- Every role appears in `qubit agent roles`; ids unique; every role has
  non-empty description, at least one voice alias, provenance, and either a
  `system_prompt` or a `recipe_id`.
- `qubit agent run <role> --prompt "..."` returns real text from the native
  engine (or the recipe brief for aliases); no OpenJarvis process/import.
- Voice aliases resolve to the real runner (focused test with a fake backend).
- `scripts/audit_openjarvis_absorption.py` reports 8/8 covered, 0 wrappers.
- Focused tests green per lane; merged gate: roles + recipes + voice bridge +
  audit. No full matrix unless declared; no `VERIFIED`.
