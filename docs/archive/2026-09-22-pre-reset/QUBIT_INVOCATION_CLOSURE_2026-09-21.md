# Qubit Invocation Closure — 2026-09-21

**Status:** IMPLEMENTATION REPORT — code-side only. **NOT VERIFIED.**
This document records what the invocation matrix now classifies and what remains
open. It is not a parity, completion, or release claim. It does not override the
active brief (`QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md`) or any ledger.

## 1. Scope and owned files

Workstream: skills/plugins invocation closure. Files changed (all untracked at
HEAD `f984911c81e119fe6fe1220510b8ac324c1224a4`; no commit/push):

| File | Change |
|---|---|
| `voicebridge/invocation_matrix.py` | origin-aware skill classification; per-item management invocation; `invocation_kind`; authoritative `closure_errors` self-check |
| `voicebridge/skill_catalog.py` | `catalog_entries` now emits `origin` (`permissioned` \| `catalog`) |
| `tests/test_invocation_closure9u.py` | NEW — 8 focused tests |
| `docs/QUBIT_INVOCATION_CLOSURE_2026-09-21.md` | this report |

Not edited (owned by other workstreams): `qubit_compat.py`, `cli.py`,
`voice_commands.py`, `plugin_runtime.py`, and all vendored
`qubit_runtime/**`. Gaps requiring those files are reported here as exact
patches, not applied.

## 2. The authoritative classification

`invocation_matrix.build(cfg)` now classifies each item with:

- `origin` — `permissioned` \| `catalog` (skills), `registry` (plugins),
  `config` (MCP).
- `disposition` — `reachable` \| `no-invocation` \| `unsupported`.
- `invocation` — the concrete path (`/slug`, `skills.show:<id>`,
  `plugin_runtime.invoke:<id>`, or `""`).
- `invocation_kind` — `slash` \| `management` \| `handler` \| `none`.
- `closure_errors` — a fail-closed self-check; empty means every in-scope item
  is either reachable through a concrete invocation or carries a typed reason.
  A `reachable` row with no `invocation`, or a gap row with no `reason`, is
  reported as a matrix bug.

Per-kind rules:

- **Skill, slash registered** (`_skill_rows`, `invocation_matrix.py:257`) —
  `reachable`, `invocation=/slug`, `text=true`, `invocation_kind=slash`.
- **Skill, permissioned, no slash** (`invocation_matrix.py:258`) — `reachable`,
  `invocation=skills.show:<id>`, `invocation_kind=management`. This is the gap
  closed in this workstream: an operator-enabled permissioned-store skill is
  not on disk, so the vendored scanner registers no `/slug` for it, but the one
  canonical `skills` operation resolves it by id on voice, CLI, and dashboard.
- **Skill, catalog, no slash** (`invocation_matrix.py:273`) —
  `no-invocation`, reason `SKILL_MANAGEMENT_ONLY`. `qubit skills show <name>`
  reads only the permissioned store, so a bundled skill the scanner did not
  register has no per-item invocation; listing/enabling is not invoking.
- **Skill, no surfaces** — `no-invocation`, reason `NO_SURFACE_BINDING`.
- **Plugin, handler bound** — `reachable`, `invocation_kind=handler`.
- **Plugin, no handler** (`invocation_matrix.py:352`) — `unsupported`, reason
  `PLUGIN_RUNTIME_UNAVAILABLE`.
- **MCP server** (`invocation_matrix.py:400`) — `unsupported`, reason
  `MCP_NOT_CONNECTED_NO_CLIENT_INVOCATION`.

## 3. Permissioned-skill invocation path (the closed gap)

For a permissioned-store skill `X` (installed staged, then enabled `active`),
the matrix now reports `reachable` via a real, id-keyed path on all three
surfaces. Each was exercised read-only against a hermetic temp `QUBIT_HOME`:

- **Voice** — `voicebridge/voice_commands.py:935` (`_b_skills`), action `show`:
  `voicebridge/voice_commands.py:946` → `permissions_store(cfg).skill(id)`.
- **CLI** — `voicebridge/cli.py:5639` (`_cmd_skills`), `show` branch
  `voicebridge/cli.py:5666` → `store.skill(args.skill_id)`.
- **Dashboard** — `voicebridge/qubit_compat.py:1425` (`h_skill_content`,
  route `GET /api/skills/content` at `voicebridge/qubit_compat.py:5445`) →
  `permissions_store(cfg).skill(name)`; and `voicebridge/qubit_compat.py:1414`
  (`h_skills`, route `GET /api/skills` at `voicebridge/qubit_compat.py:5444`)
  lists it via the shared catalog.

The matrix only grants this disposition when at least one of voice/CLI is
present in the live surface index (`invocation_matrix.py:245`); with no surface
the item fails to `no-invocation` (`NO_SURFACE_BINDING`), so reachability is
never fabricated.

## 4. Per-item disposition (representative)

Hermetic run over the repo's bundled skills copied into a temp `QUBIT_HOME`,
plus one enabled permissioned skill and one handler-bound plugin:

| Item | kind | origin | invocation_kind | disposition |
|---|---|---|---|---|
| bundled skills (55), scanner-registered | skill | catalog | slash | reachable |
| `acme_perm` (permissioned, enabled) | skill | permissioned | management | reachable |
| handler-bound plugin | plugin | registry | handler | reachable |
| manifest-only plugin | plugin | registry | none | unsupported |
| configured MCP server | mcp_server | config | none | unsupported |

Summary for that run: in_scope 57, reachable 57, no_invocation 0, unsupported 0,
`closure_errors` 0.

## 5. Remaining gaps

### 5.1 `no-invocation` items

In the representative run there are **0** `no-invocation` items. The disposition
is still reachable-by-design, and is the honest classification for:

- A **catalog/bundled skill the vendored scanner did not register** — e.g. a
  platform-gated skill, a name collision with a core command, or a missing
  frontmatter name (`qubit_runtime/agent/skill_commands.py:326`,
  `:348`, `:355`). Such a skill has no `skills.show` counterpart because
  `_cmd_skills show` reads only the permissioned store
  (`voicebridge/cli.py:5667`). Disposition stays `no-invocation` with
  `SKILL_MANAGEMENT_ONLY`.

### 5.2 Permissioned skills lack a *bundled-payload* slash invocation

A permissioned skill is reachable through the `skills` management operation, but
it cannot be executed as a bundled skill payload in an agent turn: the vendored
scanner (`qubit_runtime/agent/skill_commands.py:363 scan_skill_commands`,
`:389 skills_dir = _skills_dir()`) indexes only on-disk `SKILL.md` files; the
permissioned store's payload is never materialized on disk. This is a genuine
capability gap, not a matrix artifact. It is reported here, not fixed, because
the fix touches shared/vendored files outside this workstream.

**Exact patch required** (owner/workstream decision; not applied):

1. `voicebridge/skill_catalog.py` — add a read-only accessor that returns, for
   each permissioned+active skill, a synthesized skill-command entry
   `{"/<slug>": {name, description, skill_md_path, skill_dir}}` whose payload
   comes from the permissioned manifest instructions (no disk write).
2. `voicebridge/invocation_matrix.py` — merge that accessor's entries into
   `_skill_slash_map` so a permissioned skill with a synthesized payload is
   classified `slash` (`text=true`) instead of `management`.
3. The actual *execution* seam is shared/vendored:
   `qubit_runtime/agent/skill_commands.py:480 build_skill_invocation_message`
   resolves `skill_info["skill_dir"]` and loads the payload from disk
   (`_load_skill_payload`). Executing a permissioned skill therefore requires a
   new loader branch that accepts an in-memory payload, or materializing the
   reviewed payload to a scanner-visible directory at install time — either is
   a shared-file change and needs explicit owner sign-off plus a license/
   provenance check per `AGENTS.md`.

Until then, a permissioned skill's only invocation is the management path
(`management`), which is real but does not load the skill as agent guidance.

### 5.3 Architecture-gated `unsupported` items

Not closeable in code without an external prerequisite:

- **Handler-less plugins** — `voicebridge/plugin_runtime.py` models a plugin
  manifest as metadata; only an in-process bound handler is invocable
  (`plugin_runtime.py:503 handler = ... in _HANDLERS`). A manifest without a
  bound handler is `unsupported` by design (`PLUGIN_RUNTIME_UNAVAILABLE`).
- **Configured MCP servers** — Qubit opens no MCP client connection, so a
  configured server is inventory/config only
  (`MCP_NOT_CONNECTED_NO_CLIENT_INVOCATION`).

## 6. Tests

File: `tests/test_invocation_closure9u.py` (8 tests).

Run (repo-level suite; vendored `qubit_runtime/` is excluded by `pytest.ini`):

```
.venv/bin/pytest tests/test_invocation_closure9u.py tests/test_invocation_matrix9u.py tests/test_catalog_sync9u.py -p no:cacheprovider -q
```

Result: **23 passed** (8 new + 15 existing; 3.36s). New file alone: 8 passed
(1.55s). These are focused checks, not proof of completion and not the final
matrix.

New tests:

- `test_permissioned_skill_is_reachable_by_id_via_management` — permissioned,
  enabled, no slash → `reachable`, `skills.show:<id>`, all three surfaces.
- `test_staged_permissioned_skill_is_out_of_scope` — staged stays out of scope.
- `test_catalog_only_skill_without_slash_is_no_invocation` — bundled, scanner
  miss → `no-invocation` / `SKILL_MANAGEMENT_ONLY`.
- `test_no_surfaces_never_yields_reachability` — empty surfaces → no reachability.
- `test_closure_errors_is_empty_across_kinds` — cross-kind closure self-check.
- `test_closure_self_check_flags_a_fabricated_reachability` — flags a reachable
  row with no invocation and a gap row with no reason.
- `test_catalog_entries_carry_origin` — `origin` is emitted and correct.
- `test_matrix_stays_read_only_with_permissioned_store` — store bytes unchanged.

## 7. Uncertainty and limits

- Counts in §4 come from one hermetic run with a copied subset of bundled
  skills; they are illustrative, not a measured population. Re-running against
  a live home will differ.
- No end-to-end execution of a permissioned skill as agent guidance was
  performed (the seam does not exist; see §5.2). Only the management read path
  (`show`) was exercised on voice/CLI/dashboard.
- `closure_errors == 0` proves the matrix has no *internal* misclassification;
  it does not prove behavioral parity for any capability (see the parity
  contract's end-to-end chain rule).
- The permissioned skill's `management` invocation covers `show`; `enable` and
  `disable` are mutating and gated by confirmation in the voice/CLI handlers and
  were not executed.

## 8. Verdict

Code-side invocation classification is now authoritative and closed for the
items it owns: every in-scope skill/plugin/MCP item is either reachable through
a concrete invocation or carries a typed reason (`closure_errors` 0). The one
remaining real capability gap — a permissioned skill lacks a bundled-payload
slash execution path — is documented with the exact patch and is a shared-file
change requiring owner sign-off. **Not VERIFIED; no commit/push.**
