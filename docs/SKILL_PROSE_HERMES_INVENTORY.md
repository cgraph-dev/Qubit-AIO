# Bundled-skill prose: stale `~/.hermes` / upstream-name inventory

Scope: `qubit_runtime/skills/**` + `qubit_runtime/optional-skills/**` (SKILL.md and
skill scripts). Branch `pre-sync-worktree-snapshot` @ `6fbf9aef`. Worktree-only; not committed.

Conventions matched (taken from already-correct skills, not invented):
- scratch/illustrative paths: `~/.qubit/cache/scratch/...`
- skill script paths: `${QUBIT_HOME:-$HOME/.qubit}/skills/...` or `~/.qubit/skills/...`
- env file: `${QUBIT_HOME:-~/.qubit}/.env`

## Measured counts (before)

`grep -rn '\.hermes' qubit_runtime/skills --include='*.md'` = 3 (the two skill trees differ:
the 249 the audit cites is the **optional-skills** tree). Full tree measure:

| Pattern | Count |
|---|---|
| literal `~/.hermes` lines (both trees) | 243 |
| `.hermes` in `.md` after full-tree walk | 195 |
| `.hermes` in code (`.py/.sh/.tmpl`) | 48 |
| product-name `hermes` (case-insensitive, both trees) | 646 |
| `hermes-agent` (skill name, deliberate) | 27 |

The audit's "249" is a raw heuristic over optional-skills; this pass re-measured every
occurrence and classified it individually.

## Classification table

| Class | Lines | Action | Reason |
|---|---|---|---|
| A. MD prose / copyable commands | ~210 | **FIXED** | Model reads SKILL.md; wrong home in instruction text |
| B. `${QUBIT_HOME:-~/.hermes}` prose idiom | 15 | **FIXED** → `~/.qubit` | Fallback pointed at retired home |
| C. Script docstrings / usage examples | 8 | **FIXED** | Display text users read (`--help`, docs) |
| D. `$HOME/.hermes`, `<user>/.hermes`, `.hermes/plans`, `.hermes/skills` variants | ~14 | **FIXED** | Same stale home, non-tilde spelling |
| E. `${QUBIT_HOME:-$HOME/.hermes}` in shell scripts | 2 | **FIXED** | Same idiom as B |
| F. Runtime fallback logic (`os.environ.get("QUBIT_HOME", "~/.hermes")`, `Path.home() / ".hermes"`) | 9 | **LEFT** | Behaviour, not prose; owner-decision class (HANDOFF #5) |
| G. Code comments (`_watermark.py:35`, `diagnose-oauth-mcp.py:34`) | 2 | **LEFT** | Rule (b): comment, not user-visible |
| H. Provenance docstrings (`_qubit_home.py` ×2) | 2 | **LEFT** | Explicit "pre-rebrand `~/.hermes`" explanation |
| I. Migration tool `openclaw_to_hermes.py` | 12 | **LEFT** | By-design: migrates into hermes-named dest; filename test-pinned |
| J. Compat markers/aliases (`.hermes-package.json`, `metadata.hermes.*`, `read-old .hermes.md`, `HERMES_HOME`) | 4 | **LEFT** | Deliberate wire/compat contract |

## Result

- **Fixed: 234 line-replacements across 67 skill files** (pure 1:1 `~/.hermes`→`~/.qubit`).
- `.hermes` remaining in skill `.md`: **3 lines**, all deliberate compat (desktop-plugins
  marker, read-old alias, `metadata.hermes.*`).
- `.hermes` remaining in any skill file: 37, all in classes F–J.

## Left alone (with reasons)

- `migration/{openclaw-migration,qubit-migration}/scripts/openclaw_to_hermes.py` — migration
  tool; destination is intentionally the OpenClaw→Qubit import home; `test_migration_canonical9u`
  pins the `openclaw_to_hermes.py` filename.
- `qubit_runtime/skills/**/scripts/_qubit_home.py` — docstring explicitly explains why the
  default is `~/.qubit` and NOT the pre-rebrand `~/.hermes`.
- 9 runtime `QUBIT_HOME`-fallback sites (`hyperliquid_client.py`, `telephony.py`, `canvas_api.py`,
  `diagnose-oauth-mcp.py`, `setup.sh.tmpl`, godmode scripts, `memento_cards.py`, `unbroker/paths.py`,
  `_watermark.py`) — behaviour, flagged for owner (same class as HANDOFF open item 5).
- `AGENTS.md:23` (`metadata.hermes.*`), `desktop-plugins.md:19` (`.hermes-package.json` live marker),
  `project-context-files.md:7` (read-old `.hermes.md`).

## Verification

Tests run one at a time (`.venv/bin/python`):
- PASS: `test_skill_debrand_migration9u`, `test_outbound_brand_headers_wave24`,
  `test_migration_canonical9u`, `test_change_report_secret_false_positive8u`,
  `test_skill_provenance_symbol9u`, `test_qubit_verified_agent8_skills`, `test_plugins_skills8u`,
  `test_outbound_identity_tail_wave24::test_optional_skills_user_agents_are_qubit`,
  `test_invocation_matrix9u`, `test_skill_catalog_authority9u`.
- Pre-existing failures unrelated to this change (unmodified files / missing artifacts / env
  guards): `test_outbound_identity_tail_wave24::test_agent_user_agents_are_qubit`
  (`tools/tts_tool_providers.py`), `test_agent_compat::...live_tree` (reads empty `~/.hermes/skills`),
  `test_qubit_vendor8tf` (missing `docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json` + runtime-home guard),
  `test_catalog_sync9u::test_plugin_state_import_is_typed_unsupported`, `tests/agent/*` (import errors).
- Syntax: `py_compile` on 5 edited `.py`, `bash -n` on 2 edited `.sh` — all OK.
