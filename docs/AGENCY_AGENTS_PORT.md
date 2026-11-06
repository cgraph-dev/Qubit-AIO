# Agency Agents Port — 18 divisions, 283 personas

**Status:** skills ported and loading; office org-chart data delivered, not yet
consumed by a renderer.

Upstream: **agency-agents** (MIT). This document is the port record required by
the root `AGENTS.md` licensing rules: exact repository, commit, source paths,
licence, and local changes.

| Field | Value |
|---|---|
| Repository | `github.com/msitarzewski/agency-agents` |
| Commit pin | `b859c515d813efcfc4755d3471dd9e7686c7706d` |
| Licence | MIT — *Copyright (c) 2025 AgentLand Contributors* |
| Source tree | `/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch/repo-survey/agency-agents/` (read-only copy) |
| Ported into | `qubit_runtime/skills/agency/<division>/<persona>/SKILL.md` |
| Org-chart data | `voicebridge/office/agency_org.py` |

## 1. Counts: upstream vs ported

The upstream persona count was derived **from disk**, not from a claim: every
`*.md` under a directory named in upstream `divisions.json`, at any depth,
excluding `README*`.

> Note on the figure "330": that is `find . -name '*.md' | wc -l` over the whole
> checkout. It is 283 personas plus 47 other markdown files: 4 upstream root docs
> (`README`, `CONTRIBUTING`, `CONTRIBUTING_zh-CN`, `SECURITY`), 19 under
> `integrations/` (generated per-tool *output*, explicitly not source), 16 under
> `strategy/` (playbooks and runbooks with no agent frontmatter), 6 under
> `examples/`, 1 under `scripts/` and 1 `.github/` template. **The persona count
> is 283**, and every one of the 283 is ported. Nothing was dropped.

| Division | Upstream | Ported | | Division | Upstream | Ported |
|---|---:|---:|---|---|---:|---:|
| academic | 6 | 6 | | paid-media | 7 | 7 |
| design | 10 | 10 | | product | 6 | 6 |
| engineering | 66 | 66 | | project-management | 7 | 7 |
| finance | 5 | 5 | | research | 1 | 1 |
| game-development | 21 | 21 | | sales | 9 | 9 |
| gis | 13 | 13 | | security | 12 | 12 |
| healthcare | 3 | 3 | | spatial-computing | 6 | 6 |
| marketing | 37 | 37 | | specialized | 59 | 59 |
| | | | | support | 6 | 6 |
| | | | | testing | 9 | 9 |
| **Total** | **283** | **283** | | | | |

`game-development` ships 5 sub-teams (`blender/`, `godot/`, `roblox-studio/`,
`unity/`, `unreal-engine/`) whose personas sit one level deeper than every other
division; the port walks recursively, so all 21 are included.

### Loader proof

Discovery was run through Qubit's real loader
(`qubit_runtime/agent/skill_utils.py`, `iter_skill_index_files` +
`parse_frontmatter`) under the project venv (`.venv`, which has the
`ruamel.yaml==0.18.16` the loader needs):

```
loader saw 342 SKILL.md; agency skills resolved: 283
  agency/academic 6, agency/design 10, agency/engineering 66, agency/finance 5,
  agency/game-development 21, agency/gis 13, agency/healthcare 3,
  agency/marketing 37, agency/paid-media 7, agency/product 6,
  agency/project-management 7, agency/research 1, agency/sales 9,
  agency/security 12, agency/spatial-computing 6, agency/specialized 59,
  agency/support 6, agency/testing 9
distinct categories: 18
```

342 − 283 = 59 pre-existing Qubit skills, which still resolve unchanged.

## 2. Why the skills live under `agency/`

Skill categories went to `qubit_runtime/skills/agency/<division>/` rather than
directly under `skills/<division>/`, for one concrete reason: **`research` already
exists as a first-party Qubit category** (`skills/research/arxiv`,
`grounded-citations`, `llm-wiki`, …). A division of the same name would have
mixed third-party personas into a first-party category and put
`research-synthesist` alongside `arxiv` under one heading.

Rather than special-case one division, all 18 use the same `agency/` root: one
tree shape, no division can shadow a category, and the whole port is
removable with a single `rm -rf skills/agency` if it is ever retired.

*(During this work one command deleted `skills/research/` by mistake. It was
restored immediately from git — `git checkout -- qubit_runtime/skills/research` —
and verified intact: `arxiv/SKILL.md` (282 lines), its
`scripts/search_arxiv.py` (114 lines), `DESCRIPTION.md`, and the four sibling
skills. No first-party skill was lost.)*

## 3. Frontmatter mapping

Upstream frontmatter is 4–6 keys and is **not** a Qubit contract; Qubit's loader
requires `name`, a ≤60-char `description`, and reads category from
`metadata.qubit.category`. Every persona body is kept byte-for-byte — only the
frontmatter was rewritten.

| Upstream | Qubit | Treatment |
|---|---|---|
| `name: Backend Architect` | `name: engineering-backend-architect` | **Renamed.** Qubit resolves skills by lowercase slug and the directory name; the display name is preserved in `metadata.qubit.persona_name`. The slug is the upstream *file stem*, which is unique across all 283. |
| `description:` (400–600 chars) | `description:` (≤60 chars) | **Composed.** The contract caps this at 60; upstream's blurb does not survive that budget without reading as a truncated stub, so the listing line is built from the persona's own name + division ("Backend Architect — Engineering persona.") and upstream's text is carried verbatim in `metadata.qubit.upstream_description`. |
| `color:` (`blue`, `#9333EA`, …) | `metadata.qubit.persona_color` | Carried verbatim (103 distinct values; 99 personas use a hex colour, the rest CSS colour words). |
| `emoji:` | `metadata.qubit.persona_emoji` | Carried verbatim. |
| `vibe:` | `metadata.qubit.persona_vibe` | Carried verbatim — it is the persona's one-line voice. |
| `tools:` (17 files) | `metadata.qubit.upstream_tools` | Carried as a comma list. These are *upstream's* tool names (WebFetch, Bash, …), recorded as provenance, **not** mapped to Qubit toolsets — the persona bodies describe the role, and inventing a Qubit toolset mapping would be a guess. |
| `services:` (4 files) | provenance comment | Carried item by item. This is a nested YAML list of mappings (name/url/tier); the contract has no slot, so it lives in the skill's provenance comment rather than being flattened or dropped. |
| `author:` (7 files) | prepended to `author` | `author` = upstream author + `AgentLand Contributors + Qubit Agent`, per the HARDLINE rule to credit the human first. 7 personas name John Williams (`@itallstartedwithaidea`) or another individual. |
| — | `license: MIT` | Required by the contract; matches upstream. |
| — | `platforms: [linux, macos, windows]` | Persona bodies are prose; no platform-bound primitive, so all three. |
| — | `version: 1.0.0` | Contract field. |
| — | `metadata.qubit.category: agency/<division>` | Groups the picker by division. |
| — | `metadata.qubit.tags` | `[agency, persona, <division>, <persona words>]`. |
| — | `metadata.qubit.division_label/_icon/_color` | From upstream `divisions.json` per division (`icon` is upstream's Lucide name, e.g. `Code`, `#3B82F6`). |
| — | `metadata.qubit.upstream`, `upstream_path`, `upstream_pin`, `upstream_license`, `upstream_copyright` | Provenance, per skill. |

Every skill also carries, immediately under the frontmatter, one comment line:

```
<!-- Ported from agency-agents <path> @ b859c515d813 (MIT, Copyright (c) 2025
     AgentLand Contributors). Persona body verbatim; frontmatter rewritten to
     the Qubit skill contract. -->
```

So the MIT notice travels with the file, not only with this document.

## 4. Body fidelity

Verified programmatically, not by eye: for all 283 skills the body parsed by the
Qubit loader is **byte-identical** to the body parsed from the upstream file,
after removing only the provenance comment above.

```
bodies byte-identical: 283   mismatched: 0
desc>60: 0 | no period: 0 | banned words: 0
name!=dir: 0 | missing MIT notice: 0 | missing emoji: 0 | missing division colour: 0
duplicate names: none
```

## 5. The Office org chart — `voicebridge/office/agency_org.py`

Data plus a **pure projection** of it onto the office's existing org model.
Reads the skills' frontmatter as its single source of truth (no second copy of
the roster), writes nothing to the store, and does not touch a renderer.

### Data shape

```python
load()            -> {"schema": "qubit-office-agency-org/1", "source": <dir>,
                      "divisions": {slug: Division}, "personas": [Persona]}
divisions()       -> [Division]                 # 18, sorted by slug
personas(div=None)-> [Persona]                  # catalogue rows
division_colors() -> {slug: "#RRGGBB"}

Division = {slug, label, icon, color, kind, floor_role, personas: [slug]}
Persona  = {slug, name, emoji, color, vibe, description, division,
            division_label, division_icon, division_color, category, path}

plan_building()   -> {schema, headcount, floors_needed, rooms_needed,
                      floors:    [{number, slug, name, role, division}],
                      programme: [room_layout.programme-shaped rooms],
                      seats:     [{slug, name, seat_type, supervisor,
                                   division, room, persona_emoji, persona_color}],
                      boardroom, check, ok}
summary()         -> counts only, for a HUD badge or a test
```

`plan_building` returns **offices the office layer already accepts**: `floors`
is `org.floor_create`-shaped, `programme` is `room_layout.programme`-shaped, and
each seat names its supervisor so `org.hire` can be called in one pass.

### The building it derives

| | |
|---|---:|
| personas = seats | 283 |
| divisions = floors | 18 (+1 executive floor) |
| rooms | 41 (1 boardroom + 39 work + 1 review) |
| ceo | 1 |
| director_functional | 3 |
| director_portfolio | 20 |
| manager | 39 |
| worker | 220 |

Every number is **derived**, never chosen:

* one persona = one seat (it never invents a person and never drops one);
* a manager carries at most `store.MAX_SPAN` (7) leaves (D34) → a 66-persona
  division is a directorate of nine work rooms, not one enormous room;
* a portfolio director carries at most 7 managers → 18 divisions need 20
  directorates (`ceil(managers / 7)`), and a division past that splits into a
  second directorate rather than over-working one seat;
* the CEO cannot carry 20 directors → the `director_functional` tier carries
  them, three seats round-robin, which is what makes the chain legal.

The chief-of-staff persona holds the single `ceo` seat (the office allows exactly
one, reporting to the owner, not to an agent — D2/C30); the orchestrator persona
holds the functional tier. Executive seats are filled first from the roster, so
no persona ever holds two seats.

A plan is only returned `ok` when **all** of these pass: `room_layout.check`
(seat count equals headcount, no lone-manager room, no over-span unit), a
reimplementation of the C30 chain invariant (`_chain_ok`), and the span limit
(`_spans_ok`). A caller cannot be handed a building the office would refuse.

### End-to-end proof

`tests/probe/agency_org_hire.py` builds a throwaway store and executes the
documented integration point — floor → department → room → seat — through the
real `org` API, then reads the tree back:

```
seats hired: 283   tree seats: 283
seat types: {'ceo': 1, 'director_functional': 3, 'director_portfolio': 20,
             'manager': 39, 'worker': 220}
roots: ['specialized-chief-of-staff']
over_span: []
floors: 19 (departments per floor: [1])
VERDICT: OK
```

### Integration point (deliberately not wired)

`tower.py` was **not** modified. To render division floors, a consumer calls
`agency_org.plan_building()`, creates floors/departments/rooms/seats in the order
the probe demonstrates, and then lets the existing `tower.floorplan(cfg)` render
the store exactly as it does today. `tower_stream.py` and the Office plugin need
no change at all: they already draw whatever rooms come back from the store.

**Recommendation (not applied, per the task boundary):** `tower.py` needs no
change to draw this building, but the boardroom and review room sit on their own
executive floor (19) rather than floor 0 — the office's `floor_create` requires
1..60, so there is no floor 0. If the Office HUD assumes the executive floor is
lowest, that assumption belongs in the consumer, not here.

## 6. Omissions

**No persona was skipped.** All 283 are ported. The files deliberately *not*
ported as skills, and why:

| Upstream path | Files | Why not a skill |
|---|---:|---|
| `integrations/**` | 19 | Generated *output* for other tools (Cursor, Codex, Aider, …), produced by upstream `scripts/convert.sh`. Explicitly not source; porting it would ship the same 283 personas a second time. |
| `strategy/**` | 16 | Playbooks (`phase-0`…`phase-6`), runbooks and coordination templates. Upstream's own `divisions.json` note says they carry no agent frontmatter — they are process documents, not personas. |
| `examples/**` | 6 | Worked discovery exercises showing the personas in use, not personas. |
| `README.md`, `CONTRIBUTING.md`, `CONTRIBUTING_zh-CN.md`, `SECURITY.md` | 4 | Repository housekeeping. |
| `.github/PULL_REQUEST_TEMPLATE.md` | 1 | Repository housekeeping. |
| `scripts/**` | 1 | Upstream CI tooling (`.md` only by coincidence). |
| `divisions.json` | 1 | Not a persona — its 18 label/icon/colour entries are consumed by `agency_org.py` and stamped into every skill's frontmatter. |

Total not ported: 47 markdown files (283 + 47 = 330), none of them a persona.
`divisions.json` is not counted among them — it is a `.json` file, not a `.md`,
and its 18 entries are consumed by `agency_org.py` rather than ported as a skill.

## 7. Reproducing

The generator used for this port is a scratch script (kept out of the tree so it
cannot drift from the committed output): it lists files from disk, parses
upstream frontmatter with ruamel, rewrites only the frontmatter, asserts the body
is untouched, and refuses to write on a division mismatch. Regenerating is
idempotent — running it twice produces identical bytes.
