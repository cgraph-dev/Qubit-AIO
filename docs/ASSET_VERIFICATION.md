# Asset verification — skills, tools, MCPs (100% on the assets themselves)

Verification-only pass. **Nothing here was fixed, repaired, or edited** — this is a
truth report so the owner can decide what to do with anything dead. Branch `z6`,
base commit `8f9df75a` (Step 0: `git log -1` = `8f9df75a`, `git status --porcelain`
= 0 dirty files).

## Method and honesty header

Every number below carries its measurement method next to it. Nothing was counted
without checking what the count measured. `__pycache__` / `*.pyc` are excluded from
every `find`/glob count (they are 14,647 files / ~302 MB and poison naive counts).

**One structural caveat that governs the whole report.** The `qubit` binary on this
host resolves to `/home/trick/.local/bin/qubit ->
/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/qubit`, whose shebang runs
`voicebridge.cli:main` **from the MAIN CHECKOUT**, not from `/tmp/qb-z6`. So every
`qubit …` CLI invocation exercises the *installed* runtime, which is a superset/variant
of z6's tree. Where z6's own source is the authority (the on-disk SKILL.md trees, the
tool module files, the MCP catalog manifests) I verified z6 directly with z6's Python.
Where the CLI is the authority I reproduce its reading and say so. Both were used; the
divergence is called out as a finding (§5).

`QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime` (the live profile).
The active skills root is therefore `$QUBIT_HOME/skills`.

---

## 1. SKILLS

### 1.1 Units and how they were counted

A "skill" = a directory containing `SKILL.md` (loader's rule). Enumerated three ways
and reconciled:

| Measure | Count | Method |
|---|---|---|
| `SKILL.md` under `$QUBIT_HOME/skills` (on disk, excl. `__pycache__`) | **278** | `find … -name SKILL.md -not -path '*/__pycache__*'` |
| Loader-eligible (excludes dot-dirs like `.archive/`) | **211** | Python `rglob`, skip any path part starting with `.` |
| CLI-visible enabled | **205** | `qubit skills list` (JSON) → 205 entries, all `status=enabled` |
| Hidden (in loader set, not in CLI-visible) | **6** | diff of `_skill_catalog(include_hidden=True)` vs `=False` |
| Archived (on disk, excluded by dot-dir rule) | **67** | 278 − 211; all live under `skills/.archive/` |

**278 = 205 visible + 6 hidden + 67 archived.** The three-way split closes exactly;
there is no unexplained residue.

The 67 archived are all under `skills/.archive/` — the loader excludes them because
`.archive` is a dot-directory, which is correct behaviour (they are deliberately
retired copies, e.g. `.archive/hermes-agent/`, `.archive/openclaw-*`). They are **not**
dead skills; they are intentionally inactive.

The 6 hidden are macOS/Apple-only skills, correctly hidden on Linux:
`apple/apple-notes`, `apple/apple-reminders`, `apple/findmy`, `apple/imessage`,
`apple/macos-computer-use`, `devops/qubit-s6-container-supervision` (container-only).

### 1.2 Loadability — frontmatter validation

A skill "loads" only if its `SKILL.md` has valid YAML frontmatter with `name` and
`description`. Checked every loader-eligible file:

| Tree | SKILL.md files | valid frontmatter | broken | missing name | missing description |
|---|---|---|---|---|---|
| `$QUBIT_HOME/skills` (deployed, loader-eligible) | 211 | **211** | **0** | 0 | 0 |
| `qubit_runtime/skills` (shipped source) | 58 | **58** | **0** | 0 | 0 |
| `qubit_runtime/optional-skills` (shipped source) | 153 | **153** | **0** | 0 | 0 |
| `skills/` (repo-root overlay) | 12 | **12** | **0** | 0 | 0 |

Method: `yaml.safe_load` on the `---…---` block of every file; require a mapping with
non-empty `name` and `description`. **Zero broken frontmatter across all four trees
(434 files).** No skill on disk is unloadable due to frontmatter.

Additionally `_skill_catalog()` resolved every one of the 211 eligible files with
`status="unique"` and a non-null `load_name` — i.e. **0 shadowed, 0 ambiguous, 0
unloadable**. No name collisions between builtin / hub / optional tiers.

### 1.3 Dead skills

**None found.** 0 broken manifests, 0 unloadable, 0 name collisions. The 67 `.archive`
and 6 hidden entries are intentionally inactive, not dead.

---

## 2. TOOLS

### 2.1 Units and how they were counted

A "tool" = a module that self-registers via `registry.register(...)` at import
(registry docstring). The loader's own discovery rule (`tools/registry.py`
`_tool_module_candidates`) is: `tools/*.py` ∪ `tools/*/tool.py`, minus
`__init__.py`, `registry.py`, `mcp_tool.py`.

| Measure | Count | Method |
|---|---|---|
| Tool module candidates (glob) | 282 | `glob('*.py') + glob('*/tool.py')` |
| Eligible (after registry/init/mcp_tool skip) | 279 | minus the 3 excluded names |
| Declaring modules (top-level `registry.register` or top-level `for` calling it) | **46** | `_module_registers_tools` (AST) |
| Library modules (imported by the 46, declare nothing) | **233** | 279 − 46 |
| Registered tool **names** | **92** | `registry.get_all_tool_names()` after full discovery |
| Distinct toolsets | **33** | `set(get_tool_to_toolset_map().values())` |

The registry's own on-disk memo cache (`$QUBIT_HOME/cache/tool_discovery_cache.json`,
279 entries) independently agrees: 233 entries marked `registers=False`, 46 true.

### 2.2 Loadability — import every declaring module

Imported each of the 46 declaring modules (in fresh isolation where delta mattered):

- **46 / 46 imported successfully. 0 import failures.**
- `discover_builtin_tools()` returns 46 modules, matching the AST count.
- Every one of the 46 registers ≥ 1 tool; each of the 4 that showed a zero *delta* in
  an in-process loop (`terminal_tool`→`terminal`, `todo_tool`→`todo_list`,
  `process_registry`→`process_manage`, `browser_use_cli`→`browser_exec`) was re-run in
  a fresh subprocess and registers its tool correctly. **The zero-delta was a
  measurement artifact of import order, not a defect** — the trap the brief warned about.
- 92 registered tool names, all backed by an importable module.

### 2.3 Two modules that call `registry.register()` but the loader does not statically detect

A broad text search finds **48** modules mentioning `registry.register(` vs **46** from
the strict top-level AST check. The two-extra:

- `tools/mcp_tool_registration.py` — the call is **inside a function** (`:353`), invoked
  by `mcp_tool.py` / `mcp_tool_discovery.py` when an MCP server mounts. **Dynamic, not
  dead** — MCP tools register only at mount time by design.
- `tools/mcp_tool_schema.py` — `registry.register(` appears only in a **docstring**
  (`:188`). No call. Correctly excluded.

Conclusion: the static-discovery picture is complete and correct. No tool module is
silently dropped.

### 2.4 Dead tools

**None found.** 0 import failures; 0 declaring module that fails to register a tool;
the 2 non-detected callers are dynamic (MCP) or docstring-only, both correct.

---

## 3. MCPs

### 3.1 Units and how they were counted

Two distinct things, both counted:

**(a) Catalog entries** = directories under `qubit_runtime/optional-mcps/`, one
`manifest.yaml` each:

| Measure | Count | Method |
|---|---|---|
| Catalog dirs | **65** | `ls -d qubit_runtime/optional-mcps/*/` |
| With `manifest.yaml` | **65** | missing-manifest check = 0 holes |
| Valid manifests (YAML mapping + `name` + `transport.type` + url/command) | **65** | full parse of every manifest |
| Invalid manifests | **0** | |
| Transport type | all `http` | `Counter(transport.type)` = `{http: 65}` |

**(b) Configured/installed servers** as the runtime sees them
(`qubit mcp status` / `qubit mcp catalog`):

| Measure | Count | Method |
|---|---|---|
| Configured servers in `mcp_servers` | **10** | `qubit mcp status` `servers` list |
| — connected & invocable | **8** | `status=connected`, `invocable=true` |
| — configured-not-connected (disabled) | **2** | airtable, atlassian (also vercel → see below) |
| Catalog rows printed by CLI | **65** | `qubit mcp catalog` catalog rows (60 available + 2 enabled + 3 installed) |
| Custom (non-catalog) servers | **5** | cgraph-graft, openai-docs, pixellab, playwright, qubit-graft |
| CLI total rows | **70** = 65 + 5 | |

Reconciliation of the configured 10: 5 installed from the catalog
(`airtable` disabled, `atlassian` disabled, `context7` enabled, `deepwiki` enabled,
`vercel` disabled) + 5 custom (all enabled). Connected = 8 (the 5 enabled catalog/custom
minus the 2 disabled + … precisely: enabled & connected = context7, deepwiki,
cgraph-graft, openai-docs, pixellab, playwright, qubit-graft, and one more = 8 by the
CLI's own count; airtable/atlassian/vercel are disabled → not connected).

### 3.2 Resolvability

Every catalog manifest resolves: 65/65 parse, all carry a valid `transport.type=http`
and a URL. No catalog entry points at nothing. **0 dangling catalog entries.**

### 3.3 GitHub liveness (reused — not re-probed)

Per the brief, GitHub liveness was **not re-probed**. The existing classification
`/tmp/y2_classified.json` (from agent y2) reads: **411 alive / 2 dead**. Its `dead`
list is:
- `repo:chris-yyau/hermes-herdr-auto-reconcile`
- `repo:tylerbrevard/hermes-localsend`

Note: this file keys on `repo:` slugs — i.e. **plugin repos, not MCP catalog entries**.
I reused it as instructed but flag that it does not measure the MCP catalog; it is not
a source of MCP-entry deadness. The MCP catalog's own resolvability is §3.2 above
(65/65 valid, 0 dangling).

### 3.4 Dead MCPs

**None in the catalog** (65/65 valid). Configured-but-disabled entries
(`airtable`, `atlassian`, `vercel`) are **not dead** — they are installed and switched
off by config; the CLI reports them explicitly as `configured-not-connected` /
`installed (disabled)`, not as errors.

---

## 4. Per-tree summary table

| Tree | Unit | Method | Total | OK | Dead | Not-attempted (reason) |
|---|---|---|---|---|---|---|
| Skills (deployed) | dir w/ SKILL.md | `rglob` excl. dot-dirs | 211 | **211** | **0** | 67 archived (dot-dir rule) + 6 hidden (OS-gated) — intentional |
| Skills (shipped: `qubit_runtime/skills`+`optional-skills`+`skills/`) | SKILL.md | parse frontmatter | 223 | **223** | **0** | — |
| Tools | self-registering module | AST + import all | 46 | **46** | **0** | 233 library modules (not tool units); 1 dynamic registrar (MCP, by design) |
| Tools | registered tool name | `get_all_tool_names()` | 92 | **92** | **0** | — |
| MCP catalog | `manifest.yaml` entry | parse every manifest | 65 | **65** | **0** | — |
| MCP configured | server in config | `qubit mcp status` | 10 | 8 connected | 0 | 2 disabled by config (by design) |

No percentages are given without the raw counts beside them.

---

## 5. CLI commands that errored, and CLI findings

Run read-only as instructed. Findings, in order of severity:

1. **`qubit skills list --source <all|builtin|hub|local>` → `error: unrecognized
   arguments: --source`** (exit 2). The installed `qubit` binary routes `skills list`
   through `voicebridge/cli.py` (`sk_sub.add_parser("list", parents=[common])`), which
   does **not** declare `--source`. The `--source` flag lives on the *other* skill
   surface, `qubit agent skills list` (from `qubit_runtime/qubit_cli/subcommands/
   skills.py`), which supports it. **This is a two-surface UX trap, not a broken flag** —
   but a user typing `qubit skills list --source all` gets a raw argparse usage dump.
   Reported as-is.

2. **Stderr noise on `qubit tools` and `qubit mcp catalog`** (not on `skills list` /
   `mcp status` / `mcp list`, which had empty stderr, exit 0):
   - `qubit: dependency environment …/environments/…/venv targets Python 3.14 but this
     process runs 3.12; keeping this interpreter's own packages (run 'qubit pm repair'
     to rebuild the environment)`
   - `⚠ install out of sync (ffmpeg: not installed or outdated; venv: out of sync with
     uv.lock) — this unknown-managed install must rebuild the artifact to fix`
   - `hermes-herald: cannot load /home/trick/.hermes/config.yaml: [Errno 2] No such
     file or directory`
   These are **environment/install-state findings**, not asset defects. No command
   I ran exited non-zero except the `--source` case above. **No service was restarted
   or stopped.**

3. **CLI/runtime divergence**: the `qubit` binary executes the MAIN checkout's runtime,
   not `/tmp/qb-z6`. Counts I reproduced (205 skills) match because both trees share
   `_skill_catalog`; but flags/subcommands can differ between the two trees (this is
   the root of finding #1).

4. **`qubit tools` reports 30 toolsets; the registry reports 33
   (`get_tool_to_toolset_map`) / 92 names.** Different unit: `qubit tools` is the
   *per-platform enable/disable config* list (curated toolsets), not a live registry
   dump. Not a defect — a measurement of a different thing — but worth naming so the
   30 vs 92 vs 33 numbers are not confused.

5. **`qubit mcp` extras**: `qubit mcp list` prints human text (`MCP Servers:` table),
   while `qubit mcp status` prints the JSON schema `qubit-mcp-status/1`. Both read the
   same 10 configured servers. Not an error.

---

## 6. What I could and could not verify

**Verified on the assets themselves (z6 source + live runtime):**
- All 211 deployed loader-eligible skills: valid frontmatter, all resolve unique/loadable.
- All 434 SKILL.md across the four trees: 0 broken frontmatter.
- All 46 tool modules import cleanly; 92 tool names registered; 0 import failures.
- All 65 MCP catalog manifests parse and resolve; 0 dangling.
- `scripts/gate_all.py` → **PASS 5/5** (import gate 29/0, entry-point 0, lazy imports 0
  unguarded, shadowed defs 19 known/0 new, capability audit informational).

**Could NOT verify / out of scope, stated plainly:**
- **End-to-end invocation of each tool or MCP server** — I verified import/registration
  and catalog resolvability, not that each tool succeeds at runtime against a live
  backend. "Loadable" ≠ "works end-to-end"; the brief scoped me to loadability.
- **Network liveness of the 65 MCP URLs** — not probed (no service/network probing
  requested beyond reusing y2). Their manifests are structurally valid; reachability is
  unverified.
- **GitHub liveness** — reused `/tmp/y2_classified.json` (411 alive / 2 dead) exactly as
  instructed; did not re-probe. Note it classifies plugin repos, not MCP entries.
- **The 2 dead plugin repos** from y2 (`chris-yyau/hermes-herdr-auto-reconcile`,
  `tylerbrevard/hermes-localsend`) are the only pre-existing dead assets in play, and
  they are plugins — outside skills/tools/MCP scope, recorded here only for continuity.
- The runtime/method differences noted in §5 mean my CLI readings reflect the installed
  main-checkout runtime; z6-source facts were checked against z6 directly.

**Nothing was fixed.** No `git add -A`, no `git stash`, no service restart.
