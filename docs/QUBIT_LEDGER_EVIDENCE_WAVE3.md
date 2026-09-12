# Qubit ledger evidence — Wave 3 (inventory ingestion + evidence gates)

Status: **append-only evidence report.** The frozen acceptance contract and the
execution directive still govern; this file records method, counts, and open
work. It does not close or reclassify any requirement.

## Scope

Wave 3 makes the gap ledger *provably complete against the direct source
inventory* and keeps it deterministic:

- `scripts/rebuild_ledger_qubit.py` ingests
  `docs/QUBIT_SOURCE_INVENTORY.json` (agent A / `scripts/source_inventory.py`)
  when present and records the outcome in the section provenance.
- Every non-evidence inventory item is either matched to a real row in the
  merged ledger or emitted as an explicit `MISSING` open row. No upstream
  surface is silently dropped.
- Op evidence is *linked* to a production dispatcher, so a textual mention
  (docstring, comment, unrelated assertion) can never mark a row
  `IMPLEMENTED`.
- `scripts/gap_ledger.py` keeps strict schema validation and adds
  `--require-inventory-complete` plus a machine-readable omissions summary.

## Method

### 1. Evidence gates (unchanged requirements, stricter linking)

`build_catalog_rows` marks a catalog op `IMPLEMENTED` only when **all** hold:

1. the op resolves to a real callable production backend
   (`voice_commands.REGISTRY` / `_BACKENDS` or `dashboard_ops.EXTRA_OPS`);
2. an on-disk `tests/test_*.py` function exercises that caller and the file
   exists at build time;
3. every declared surface (`cli`/`dashboard`/`voice` from the catalog
   `interfaces`) is present.

Wave 3 tightens condition 2. The AST test index now records an op id only when
the id is *linked* to a production dispatch call — as a literal argument to
`run_op` / `run_extra_op` / `run_registry_op` / `voice_say` / `confirm_op` /
`cancel_op` / ... (or a closure-reachable dispatcher), or through an evidence
constant table (`REFUSAL_OPS`, `READ_OPS`, `PLUGIN_ROW_IDS`, ...) or a
`pytest.mark.parametrize` value that reaches a dispatcher. Strings that appear
only in a module/function docstring, a comment, an assertion, or an unrelated
call are never evidence.

Page/plugin rows keep their documented dashboard-only rule: a test that reaches
a real production dashboard route (`run_op`, the compat dispatcher, the plugin
shell resolver) and references the row id (directly or via a table constant) is
the proof; there is no CLI/voice surface for a rendered page by design.

### 2. Direct source-inventory ingestion

`load_source_inventory()` is a tolerant reader:

- **absent** file → `present=false`, `error="inventory file absent"`, zero
  ingested rows;
- **malformed** JSON / wrong root → `present=true`, `error=<reason>`, zero
  ingested rows;
- **present** → items are read from `items` (or `surfaces`/`rows`/`inventory`).

Cross-cutting evidence categories (`hermes_i18n_key`, `hermes_locale_key`,
`hermes_upstream_test`) are counted as `evidence_only` and are not standalone
capability rows, matching `scripts/source_inventory.py`.

For every other item:

- if `ledger_match` names a row id that exists in **any** of the three ledger
  sections (hermes/openclaw/qubit, read read-only), it is `matched`;
- otherwise (no match, or a `ledger_match` that does not exist) an explicit
  `MISSING` open row `inv:<item-id>` is appended with `needs` inferred from the
  source category (`hardware` / `creds` / `owner`), an empty backend and
  `proving_test`, and a note explaining why it is open.

Because the Qubit generator is the owned ingestion point, omissions sourced
from Hermes or OpenClaw are emitted as explicit `inv:` rows **in the Qubit
section** (the only section this workstream owns) with `upstream_ref` recording
the true source path and `area` recording the inventory category. The
orchestrator may re-home them into the Hermes/OpenClaw sections in dependency
order; until then they are visible and cannot be dropped.

The section embeds the machine-readable block:

```json
"inventory": {
  "present": true,
  "schema": "qubit-source-inventory/1",
  "items_total": 0, "surface_items": 0, "evidence_only": 0,
  "matched": 0, "unmatched": 0, "unexplained_omissions": 0,
  "unidentified": 0, "unreproducible_evidence": 0,
  "by_category": {}, "omitted_ids": [], "error": ""
}
```

### 3. Merged-report gate

`scripts/gap_ledger.py` still validates the fixed schema and fails on a
violation. It adds:

- `--require-inventory-complete` — exit 1 unless a source-inventory report is
  present, readable, and reports zero unexplained omissions (absence is **not**
  completeness);
- `--omissions-out PATH` — writes the machine-readable omissions summary
  (`reports`, `unexplained_omissions`, `omitted`, `errors`);
- the same summary is included under `"inventory"` in the `--json` output and
  rendered in the merged Markdown.

## Counts (snapshot)

Snapshot generated from `docs/QUBIT_GAP_LEDGER.*.json` after
`scripts/rebuild_ledger_qubit.py` was run against the then-current
`docs/QUBIT_SOURCE_INVENTORY.json`. The inventory is a moving artifact built by
agent A; regenerate the section and this snapshot after it changes.

### Qubit section

| metric | value |
| --- | --- |
| rows | 1788 |
| IMPLEMENTED | 534 |
| GATED | 42 |
| PARTIAL | 5 |
| MISSING (explicit inventory rows) | 1207 |

### Inventory ingestion

| metric | value |
| --- | --- |
| inventory present | yes |
| schema | `qubit-source-inventory/1` |
| items total | 6776 |
| surface items | 1819 |
| evidence-only items (i18n/locale/upstream tests) | 4957 |
| matched to an existing row | 612 |
| unmatched → explicit open rows | 1207 |
| unexplained omissions | 1207 |
| unidentified items | 0 |
| IMPLEMENTED rows with unreproducible evidence | 0 |
| reader error | none |

Top omission categories (explicit `inv:` rows):

| category | open rows |
| --- | --- |
| `hermes_cli_option` | 351 |
| `hermes_model` | 211 |
| `hermes_cli_subcommand` | 179 |
| `openclaw_extension` | 132 |
| `hermes_workflow` | 104 |
| `hermes_tool` | 83 |
| `hermes_provider` | 38 |
| `hermes_cli_positional` | 34 |
| `hermes_skill` | 29 |
| `hermes_dashboard_component` | 14 |

### Merged ledger

| metric | value |
| --- | --- |
| total rows | 2313 |
| IMPLEMENTED | 939 |
| open rows | 1374 |

Open-row breakdown by `needs` (all sources):

| needs | open rows |
| --- | --- |
| owner | 855 |
| creds | 405 |
| hardware | 114 |

Open-row breakdown by `needs` (the 1207 explicit inventory rows):

| needs | open rows |
| --- | --- |
| owner | 766 |
| creds | 354 |
| hardware | 87 |

The `needs` split is inferred from the source category. `creds` covers
providers/OAuth/channels/messaging/secrets, `hardware` covers
devices/audio/screen/desktop/browser/local models, and `owner` is the default
triage bucket for unassigned code-side work. It is a starting point for
triage, not an owner decision.

## How a row is closed

A row moves to `IMPLEMENTED` only when the generator itself derives it from
evidence — no status is hand-edited in the JSON:

1. **Real backend** — the op id resolves to a callable production backend
   (registry/extra-op), or a page/plugin is served by a real dashboard route.
2. **Real behavioral test on disk** — a `tests/test_*.py` function exercises
   that caller (linked dispatcher argument/table/parametrize for ops; a real
   dashboard route for pages/plugins). The cited file must exist at build time.
3. **Surfaces present** — every declared CLI/dashboard/voice surface is wired
   (page/plugin rows declare dashboard only, by design).
4. **Inventory reconciled** — the capability appears in the direct source
   inventory, or its inventory item is explicitly recorded as an open `inv:`
   row.

Any one of these missing leaves the row open (`PARTIAL`, `GATED`, `MISSING`,
`UNVERIFIED`, or a surface-only status). `GATED` is reserved for real typed
Qubit policy gates; hardware/credential/owner-only execution remains open with
the matching `needs`.

## Reproduce

```bash
python scripts/rebuild_ledger_qubit.py                 # regenerate the section
python scripts/rebuild_ledger_qubit.py --check         # non-zero if stale
python scripts/gap_ledger.py --out /tmp/ledger.md \
    --omissions-out /tmp/omissions.json                # write summary
python scripts/gap_ledger.py --require-inventory-complete   # release gate
python -m pytest tests/test_ledger_ingestion_wave3.py \
    tests/test_ledger_qubit_build.py \
    tests/test_ledger_hermes_build.py \
    tests/test_ledger_openclaw_build.py -q
```

Expected while omissions remain: `--require-inventory-complete` exits `1` with
`INVENTORY INCOMPLETE: <n> unexplained source inventory omission(s)` and prints
the omissions JSON. It passes only when the inventory is present, readable, and
every surface item has a row.

## Owned files

- `scripts/rebuild_ledger_qubit.py` — linked evidence gates + inventory
  ingestion.
- `scripts/gap_ledger.py` — `--require-inventory-complete`, omissions summary.
- `tests/test_ledger_ingestion_wave3.py` — determinism, schema, evidence, and
  ingestion tests.
- `docs/QUBIT_LEDGER_EVIDENCE_WAVE3.md` — this report.

`docs/QUBIT_GAP_LEDGER.qubit.json` is regenerated by the owned generator only.
The hermes/openclaw sections and the merged `docs/QUBIT_GAP_LEDGER.md` are not
written by this workstream.

---

# Wave 3b — parent-surface reconciliation (agent A, 2026-09-13)

Status: **append-only evidence report.** Requirements are unchanged; this
section records an honest reclassification of the direct source inventory so
that a CLI flag, model entry, plugin asset, extension, workflow source file, or
dashboard component is no longer counted as an independent capability.

## Problem

The ledger had **1,207** `inv:` `MISSING` rows, including 211 models, 132
OpenClaw extensions, 104 workflows, 83 tools, 38 providers, and hundreds of
`hermes_<cmd>_<sub>_*` rows that are CLI options/positionals of commands the
ledger already implements. Treating every upstream surface as a standalone
capability inflated the gap.

## Classification rules (mechanical, no hand-flips)

Every non-evidence inventory item is classified in `scripts/source_inventory.py`:

| category | parent (`parent_id`) | coverage |
| --- | --- | --- |
| `hermes_cli_option`, `hermes_cli_positional` | its command family (`cli:<command>`; nearest existing ancestor, e.g. `cli:cron create` → `cli:cron`) | `cli-option` |
| `hermes_cli_subcommand` alias (`is_alias_of`) | the alias target's `cli:` row | `cli-alias` |
| `hermes_model` | `model` | `model-entry` |
| `hermes_provider` | `proxy_providers` | `provider-entry` |
| `hermes_plugin_api` / `_asset` / `_test`, `hermes_dashboard_plugin` | its plugin (`plugin:<name>`) | `plugin-surface` |
| `openclaw_extension` | the audited `OC-*` capability matched by name, else the manifest's first mapped category | `openclaw-capability` |
| `hermes_workflow` | the area capability (`browser_status`, `opencode_status`, `computer_status`, `memory_search`, `channels_status`, `reports_list`, `runtime_status`, `schedules_list`) | `workflow-surface` |
| `hermes_dashboard_component` / `_dialog` / `_form` / `_nav` | `webui_status` | `dashboard-surface` |
| `hermes_i18n_key`, `hermes_locale_key`, `hermes_upstream_test` | evidence only (never a row) | `evidence-*` |

A category absent from this table (a non-alias CLI subcommand, a tool, a skill,
a plugin capability, a backend route) is a **distinct capability**.

## Coverage gate

A parented surface is **covered** when its resolved parent row is `IMPLEMENTED`
in the live merged ledger: the parent's real production caller plus on-disk
behavioral test already covers the surface, and the surface is preserved /
forwarded / declared under it. A covered parented surface produces **no**
standalone `MISSING` row. An uncovered parented surface still produces a
`MISSING` row, tagged as `parented_uncovered`, and a distinct capability with
no row is tagged `distinct_uncovered`. Strict `IMPLEMENTED` gates are unchanged
(real production backend + on-disk proving test).

The inventory artifact is generated against the pinned `f5fc5776` baseline
(deterministic); the ledger section recomputes parent status against the **live**
merged ledger, so the two can differ while other agents land work.

## Counts

### Before (committed at 539649d4)

| metric | value |
| --- | --- |
| Qubit section rows | 1788 |
| IMPLEMENTED | 534 |
| GATED | 42 |
| PARTIAL | 5 |
| MISSING (`inv:`) | **1207** |

### After (live working tree)

| metric | value |
| --- | --- |
| Qubit section rows | 907 |
| IMPLEMENTED | 534 |
| GATED | 30 |
| PARTIAL | 5 |
| MISSING (`inv:`) | **338** |
| MISSING distinct-uncovered | **271** |
| MISSING parented-uncovered | **67** |
| parented surfaces (total) | 1080 |
| parented covered | 1013 |
| distinct surfaces (total) | 739 |
| distinct covered | 468 |

`GATED` is derived from the live `voicebridge.hermes_compat.GATED` set and moves
with concurrent module work; it is not changed by this reclassification.

MISSING distinct-uncovered by category: `hermes_cli_subcommand` 147,
`hermes_tool` 73, `hermes_skill` 29, `openclaw_extension` 11, `hermes_plugin`
10, `hermes_backend_route` 1.

MISSING parented-uncovered by parent: `OC-19` 19, `cli:sessions` 11,
`cli:skills` 9, `cli:mcp add` 7, `cli:mcp` 6, `cli:plugins install` 6,
`cli:skills install` 3, `cli:skills publish` 3, `cli:mcp remove` 1,
`cli:mcp configure` 1, `cli:config set` 1.

### Inventory baseline snapshot (`f5fc5776`)

| metric | value |
| --- | --- |
| candidate omissions | 346 |
| distinct-uncovered | 272 |
| parented-uncovered | 74 |
| parented covered | 1006 |
| distinct covered | 467 |

## Reproduce

```bash
python scripts/source_inventory.py          # inventory + omissions (baseline-pinned)
python scripts/rebuild_ledger_qubit.py      # live parent coverage -> ledger section
python -m pytest tests/test_source_inventory.py \
    tests/test_ledger_ingestion_wave3.py -q
```

## Rollback

Restore the pre-change copies from the agent's external backup (the committed
`539649d4` state) and re-run both generators:

- `scripts/source_inventory.py`, `scripts/rebuild_ledger_qubit.py`,
  `docs/QUBIT_SOURCE_INVENTORY.json`, `docs/QUBIT_SOURCE_OMISSIONS.md`,
  `docs/QUBIT_LEDGER_EVIDENCE_WAVE3.md`, `tests/test_source_inventory.py`,
  `tests/test_ledger_ingestion_wave3.py`, `docs/QUBIT_GAP_LEDGER.qubit.json`.
