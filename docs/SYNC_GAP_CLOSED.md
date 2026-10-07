# Production gap: CLOSED

## Result

The remaining 28 production symbols are done. **19 ported for real, 9 were false
positives.** `sync/final` -> `4de6219e`.

Gate: **PASS** — all four blocking checks green.

## Ported (19 symbols, 7 commits)

| file | symbols | what |
|---|---|---|
| `plugins/platforms/email/adapter.py` | 9 | full pre-auth header subsystem: constants + `_imap_payload`, plus `_message_metadata` / `_parse_fetched_headers` / `_mark_uid_consumed` extracted so every constant is reached by a live path |
| `plugins/platforms/matrix/adapter.py` | 4 | byte-budget cap (`_MAX_CONTENT_BYTES` = 45000, `_content_bytes`, `_utf8_len`) + `_standalone_payloads` chunking, wired into edit/build/standalone-send |
| `cron/jobs.py` | 2 | non-latching croniter retry backoff |
| `agent/skill_commands.py` | 1 | `_last_interactive_skill_commands_by_key` — **used at 3 sites but never defined; a latent `NameError`** |
| `gateway/run_profile_reconcile.py` | 1 | bounded off-loop own-gateway probe |
| `gateway/control_socket.py` | 1 | `_windows_pipe_exchange` + bound, keeping the legacy-pipe fallback |
| `hermes_cli/update_cmd_git.py` | 1 | `_UPSTREAM_ADD_CMD`, built from `OFFICIAL_REPO_URL` (no hardcoded URL) |

The most valuable of these is `_last_interactive_skill_commands_by_key`: a name used
at three call sites with no definition anywhere. That is a guaranteed runtime crash on
the paths that reach it, and it was invisible to every gate because the import is
function-local.

## The 9 false positives — verified, not asserted

I checked each against the tree. All confirmed present under the Qubit-named object:

| upstream | ours | files |
|---|---|---|
| `HERMES_AGENT_LOGO` | `QUBIT_AGENT_LOGO` | 2 |
| `HERMES_CADUCEUS` | `QUBIT_HERO` | 1 |
| `HermesOAuthClientProvider` | `QubitOAuthClientProvider` | 2 |
| `HermesTokenStorage` | `QubitTokenStorage` | 18 |
| `HermesArgumentParser` | `QubitArgumentParser` | 1 |
| `HermesProviderMixin` | `QubitProviderMixin` | 5 |
| `HermesIndexSource` | `QubitIndexSource` | 4 |

The audit's `rebrand()` maps `hermes_`/`HERMES_` prefixes, so it misses bare
`Hermes<PascalCase>` renames. Substituting `HermesX` -> `QubitX` wholesale would have
created duplicate objects under both names -- hazard #1, the class this campaign has
repeatedly had to clean up. Correctly refused.

## Verified independently

- gate PASS (4/4) on the merged tree
- all ported symbols import at runtime (not just grep-present)
- `_UPSTREAM_ADD_CMD` derives from `OFFICIAL_REPO_URL`
- per-file AST diff vs pinned `a036b13793`: 0 structural missing in the 5 real-gap files

## What remains

**Baseline completeness: done.** The tree now matches the revision we synced from
(`a036b13793`) in production code.

**Upstream currency: NOT done, and it is a different job.** Upstream is 958 commits
and 644 `.py` files past that baseline, ~160 files behind head. The audit's own
analysis says ~415 of 1,112 changed files carry real work, dominated by the
`hermes_cli` self-update/custody rebuild (~47%).

Closing the short-ports did not and cannot touch that. Do not conflate the two again:
"28 symbols left" and "958 commits behind" were both true and measured different
things.
