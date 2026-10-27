# OpenJarvis 8-agent absorption — roles, mapping, and audit

Date: 2026-10-27. Scope: Qubit absorbs the eight OpenJarvis agent behaviours as
native Qubit roles. A role is a preset for Qubit's one agent loop; it is not a
second runtime and it does not wrap or shell out to OpenJarvis.

References (design/provenance only — no code or prompt is copied):

- `docs/superpowers/plans/2026-10-27-openjarvis-agent-absorption.md` — the
  8-role mapping, the `AgentRole` contract, CLI/voice exposure and acceptance.
- `docs/superpowers/specs/2026-10-26-runtime-boundary-and-openjarvis-design.md`
  — the RuntimePort boundary, the "adapters, never vendored" rule and the
  broader phase plan.
- Upstream pin for reference only:
  `open-jarvis/OpenJarvis@05839014127772bd077ba9ba2a2393a007f63ac6`
  (Apache-2.0). Qubit role prompts are authored Qubit-native; no upstream
  prompt is copied without a `THIRD_PARTY_NOTICES.md` record.

## Mapping

| OpenJarvis agent | Qubit role | Kind | How it runs through Qubit's authority path |
|---|---|---|---|
| `orchestrator` | `orchestrator` | native role — delegation/MoA posture | One turn through `RuntimePort` → native `AIAgent`, with the role's prompt/toolsets; delegation and MoA remain the existing Qubit machinery. |
| `native_react` | `react` | native role — reason-act posture | Same turn path; the role only changes the system posture/toolsets, not the loop or authority chain. |
| `operative` | `operative` | native role — gateway/kanban/work state | Same turn path; gateway, kanban and work-state tools are the existing Qubit surfaces, subject to the same confirmations. |
| `native_openhands` | `openhands` | native role — coding/sandbox work | Same turn path; code execution and sandboxing stay Qubit's tools and approval policy. |
| `simple` | `simple` | native role — fast chat | Same turn path with a minimal posture; still one session store and one authority chain. |
| `deep_research` | `researcher` | native role + existing research tools | Same turn path; research tools are Qubit's, not an OpenJarvis process. |
| `morning_digest` | `digest` | recipe alias | `recipe_id` dispatches the existing `recipes/morning_digest` through the same recipe runner/authority path. |
| `monitor_operative` | `monitor` | recipe alias | `recipe_id` dispatches the existing `recipes/monitor_operative`; it does not duplicate the prompt. |

Required id set: `{orchestrator, react, operative, openhands, simple,
researcher, digest, monitor}`.

## Contract each role must satisfy

Every `roles/*.py` module (excluding `base`, `registry`, `runner`, `__init__`)
must define exactly one module-level `ROLE = AgentRole(...)` with:

- non-empty `id`, `title`, `description`;
- at least one `voice_aliases` entry (text **and** voice reachability);
- non-empty `source` provenance naming the mapped upstream agent;
- either a non-empty `system_prompt` or a `recipe_id`;
- a valid `model_tier` (`local` / `detail` / `api` / empty) and
  `confirmation` (`preview` / `confirm` / `auto`);
- no wrapper: no `openjarvis` import/reference, no subprocess call/import, no
  HTTP call/import, and no module that only forwards another module's `ROLE`.

The role modules are discovered by `roles/registry.py`; adding a role does not
require editing a central list. A role with a `recipe_id` delegates to
`recipes.runner.run_recipe`; every other role runs one bounded turn through
`RuntimePort` with the native engine by default.

## Audit command

```bash
.venv/bin/python scripts/audit_openjarvis_absorption.py
.venv/bin/python scripts/audit_openjarvis_absorption.py --json
.venv/bin/python scripts/audit_openjarvis_absorption.py --root /tmp/some-tree
```

The audit is pure AST: it does not import the runtime, install or import
OpenJarvis, call the network, or start a subprocess. It reads
`<root>/roles/*.py` (accepting either the runtime root or a repo root), reports
the role table, then exits:

- `0` — all eight required ids are present, unique and clean, with zero wrapper
  modules;
- `1` — any gap: missing/duplicate id, missing required field, absent
  prompt/recipe, unreadable literal, syntax error, a declared `recipe_id` that
  a present recipe tree does not register, or any wrapper indicator.

Extra role ids beyond the eight are reported as `unexpected` but do not fail the
audit, so later roles can be added without editing the tool. `--json` emits the
same report with `required`, `found`, `missing`, `duplicates`, `unexpected`,
per-role fields/flags and the ordered `gaps` list.

## Status — merged 2026-10-27

All seven lanes are merged on `main` and the merged tree was exercised:

- `scripts/audit_openjarvis_absorption.py` → **CLEAN (8/8, 0 wrappers)**, exit 0.
  Rows: `digest`, `monitor`, `openhands`, `operative`, `orchestrator`, `react`,
  `researcher`, `simple`; no missing/duplicate ids; no wrapper indicators.
- `qubit_runtime/tests/roles` → **60 passed** (contract, discovery, runner,
  native toolset wiring, recipe dispatch, typed refusals, voice alias binding).
- root absorption/voice set (`tests/test_openjarvis_absorption.py`,
  `tests/test_recipe_voice_wave4x.py`, `tests/test_voice_inventory_coverage.py`)
  → **26 passed**.
- Real CLI: `qubit agent roles --json` lists all eight;
  `qubit agent run simple --prompt 'Reply with exactly: ROLES_OK' --json` →
  `{"ok": true, "text": "ROLES_OK", "model": "deepseek-v4.1-flash",
  "provider": "opencode-go"}`; `qubit agent run monitor --json` returned the
  real read-only cron/kanban/memory/compression sections.
- Voice: `role_bridges.install()` registers 7 additive `CLI_BRIDGE_REGISTRY`
  entries (`role_digest`, `role_openhands`, `role_operative`, `role_orchestrator`,
  `role_react`, `role_researcher`, `role_simple`); `resolve("fix this code")`
  returns `role_openhands`. The `monitor` aliases stay with the existing
  `monitor_operative` recipe bridge, which calls the same recipe.

Honest limits: `AgentRole.model_tier` is a declared hint; the runner uses the
active model and reports the real `model`/`provider` plus `model_tier` in the
result (no separate tier map was configured). Role turns call the real model,
so latency depends on the provider. No full matrix and **no `VERIFIED`** claim.
