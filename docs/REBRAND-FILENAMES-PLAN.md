# Filename rebrand — scope and ordering (measured 2026-10-17)

698 tracked files have `hermes` in the **name**. That is a different job from the 1,059
user-visible **strings** (see `docs/REBRAND-SWEEP-STATUS.md`): renaming a file breaks
every importer, and several of these names are **wire contracts an external reader
depends on**.

> **AUTHORITY: `docs/REBRAND_MODULE_MIGRATION_PLAN.md` §4 is the single source of the
> do-not-rename list.** This file does not replace it. If a rename candidate appears in
> §4, §4 wins. Read §4 before renaming anything here.

## The one rule that decides every case

From `HANDOFF.md` §3, quoted in that plan: **a name stays if something OUTSIDE the repo
reads it.** Not "is it user-visible" — *does something not in this tree read it*.

## The `adapters/` + `agent_compat/` layer is the whole point — do not disturb it

Qubit ships a deliberate **compat surface** so it can absorb content (skills, plugins,
tools, channels, MCPs) written for Hermes and for other agents:

- `voicebridge/agent_compat/framework.py` — the engine; `ForeignAdapter` instances
- `voicebridge/agent_compat/adapters/hermes.py` — the Hermes adapter's naming tables
  (pure data), consumed by the framework. **2,559 importers.** Renaming this file or
  its `_RESERVED` entries orphans pip-installed plugins, which publish under the
  `hermes_agent.plugins` entry-point group.
- `voicebridge/agent_compat/adapters/openclaw.py` — a second `ForeignAdapter`
- `adapters/` — a registry + doc scaffold. It owns **no runtime code**; it records five
  discovery seams, and `adapters/test_registry.py` asserts each still exists. Moving a
  seam file without updating `registry.yaml` in the same commit fails loudly by design.

The architecture is: **the second adapter is a data file, not a rewrite.** Any rename
wave must leave that property intact.

## The set, classified

| Count | Class | Risk |
|---|---|---|
| 352 | `tests/` | **Low** — churn only. Last, or never. |
| 182 | TypeScript (`.ts`/`.tsx`) | **Medium** — sweep imports in the same commit |
| 58 | `plugin-catalog/` | **High** — user-facing plugin **IDs**; renaming orphans installs |
| 49 | other | needs triage against §4 |
| 28 | `contributors/emails` | **DO NOT TOUCH** — provenance record |
| 10 | shipped images (`.png`) | **Medium** — user-visible, referenced from code |
| 10 | `website/docs` | **Careful** — a doc ABOUT the rename keeps the old name; also the vendored docs mirror is excluded by §4 |
| 8 | Python | see below |
| 1 | `apps/desktop/src/plugins/` | shipped plugin |

## The Python subset is only EIGHT files

| Importers | Path | Verdict |
|---|---|---|
| **2,559** | `voicebridge/agent_compat/adapters/hermes.py` | **NEVER rename** — it IS the compat layer; its name and `_RESERVED` entries are how foreign plugins are recognised |
| 7 | `optional-skills/migration/*/scripts/openclaw_to_hermes.py` (×2) | Keep — names the tool it migrates FROM |
| 0 | `scripts/hermes_byte_manifest.py` | Rename candidate, but check §4 first |
| 0 | `scripts/hermes_frontend_map.py` | Rename candidate |
| 0 | `scripts/hermes_surface.py` | Rename candidate |
| 0 | `scripts/hermes_vendor_manifest.py` | **Keep** — records upstream provenance; the name is accurate |
| 0 | `scripts/rebuild_ledger_hermes.py` | **Half-renamed already** — see below |

## The worked example of the hazard, already in the tree

`scripts/rebuild_ledger_qubit.py` **exists** and imports `rebuild_ledger_hermes` at lines
845 and 861. The rename looks done; renaming the old file without fixing those two
imports is an ImportError. This is why the rule is:

> **Rename the file and fix every referrer in ONE commit.**

## Ordering, if and when this is done

1. **The 10 shipped images** — visible, small referrer set.
2. **`plugin-catalog/` (58)** — only with a migration path; these are IDs users have.
3. **TypeScript (182)** — one directory at a time, referrers in the same commit.
4. **Python (8)** — trivial, except the compat adapter, which stays.
5. **`tests/` (352)** — last, and arguably never.

## Gates that must pass, taken from §5 of the migration plan

`scripts/gate_all.py` (PASS 5/5), `scripts/safe_import_walk.py`, `scripts/check_lazy_imports.py`,
`scripts/check_duplicate_defs.py`, `tests/test_agent_compat.py` (proves `hermes_cli is
qubit_cli` under both import orders), and `adapters/test_registry.py` (every recorded seam
still exists). A diff that touches a §4 token is **rejected regardless of gate result** —
the gate cannot see a wire contract an external reader depends on.
