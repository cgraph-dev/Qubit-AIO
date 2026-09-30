# GitHub sync & auto-push — audit & verification recipe

Context: three private repos under `cgraph-dev` back up the whole voice stack +
both agent homes. This file is the repeatable check that "everything is really
on GitHub and nothing sensitive-by-mistake leaked" — run it after ANY change to
the sync setup, on a new PC, or when the user asks "is the backup working?".

## Repos
- `qubit-voice-bridge` — code + `config/voice.local.yaml` (installed config;
  install.sh restores it). Models NOT in repo (auto-download).
- `openclaw-home` — git repo INSIDE `~/.openclaw` (agents, sessions,
  `state/openclaw.sqlite`, memory, workspace). Include: `openclaw.json*`,
  `agents/**/catalog.json`, `agents/**/*.sqlite`, `.env` (USER WANTS API keys
  in the private repos — their explicit choice). Exclude: `credentials/`,
  `identity/`, `*.pem`, `*.key`, runtime dirs (browser/node/npm/tools/media/
  logs/extensions), `*.sqlite-wal/-shm`.
- `hermes-home` — git repo INSIDE `$QUBIT_HOME`. Whitelist .gitignore:
  `/*` + `!/memories/ !/skills/ !/plugins/ !/cron/ !/hooks/ !/sessions/
  !/pastes/ !/checkpoints/ !/scripts/ !/config.yaml !/.env !/state.db`.
  `state.db` at $QUBIT_HOME ROOT is the live 43 MB session store (sessions/
  state.db is a dead 0-byte leftover — Qubit relocated it).

## Credential model (multi-account machine)
- gh CLI has TWO accounts: `rankvalut` (active) + `cgraph-dev`. gh's own
  credential helper answers git with the ACTIVE account → `Repository not
  found` on the other account's private repos. Fix in place:
  - `git config --global credential.useHttpPath true`
  - `~/.git-credentials` (chmod 600): cgraph-dev token on per-repo paths
    (`https://cgraph-dev:TOKEN@github.com/cgraph-dev/<repo>.git`), rankvalut
    token as bare `https://rankvalut:TOKEN@github.com` fallback.
  - unset gh's helper: `git config --global --unset-all credential.https://github.com.helper`
- Verify resolution: `printf 'protocol=https\nhost=github.com\npath=cgraph-dev/<repo>.git\n\n' | git credential fill` → must show `username=cgraph-dev`.
- Nightly auto-push: `$QUBIT_HOME/scripts/auto-push.sh` via Qubit cron
  `3e396bb6e6a5` (23:30, no_agent, deliver=origin). Checkpoints live SQLite
  DBs (sqlite3 wal_checkpoint PASSIVE) BEFORE commit: openclaw.sqlite,
  openclaw-agent.sqlite, qubit root state.db. Drops empty embedded `.git`
  dirs (Qubit leaves a zero-commit `workspace/.git` that breaks git add).
  Silent on no-change; ✅ summary on push; full error on failure.

## Audit steps (in order)
1. Fresh clone all three: `git clone https://github.com/cgraph-dev/<repo>.git`.
2. KEY-FILE PRESENCE **AND SIZE** — `stat -c %s` not just `ls`:
   `openclaw-home/state/openclaw.sqlite`, `hermes-home/state.db` (expect 43 MB),
   `hermes-home/config.yaml`, `openclaw-home/openclaw.json`. A 0-byte sqlite =
   wrong file tracked (state.db relocation trap).
3. Secret scan on the CLONES (the definitive test):
   `grep -rlE 'gho_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}' <repo>`
   — expect hits ONLY in the files the user wants to carry keys in
   (openclaw.json*, catalog.json, .env, config.yaml). Unexpected hits = leak.
4. `git credential fill` per repo (step above).
5. Run `bash $QUBIT_HOME/scripts/auto-push.sh` — should exit 0, silent or ✅.
6. `cronjob action=list` → job exists, enabled, deliver=origin.

## gitignore semantics that bite (git 2.43)
- **Trailing comments break patterns**: `pattern # comment` keeps the
  whitespace before `#` in the pattern → never matches. Comments on own lines.
- **Index beats ignore**: files already staged are never ignored, and
  `git check-ignore` reports them as NOT ignored (it matches a nonexistent
  twin path). After changing ignore rules: `git reset && git add -A`, then
  `git ls-files | grep -c <secret>` must be 0.
- `git rm -r --cached .` does NOT reliably clear the index for this purpose;
  use `git reset`.

## History scrub (secret got pushed)
- Cannot delete the repo with a gho_ token lacking `delete_repo` scope.
- Recipe: `git checkout --orphan sanitized && git add -A && git commit` →
  `git branch -M sanitized main` → `git push --force origin main`. The branch
  ref then points at a single clean root commit; the old objects remain in
  GitHub storage until GC (acceptable for a private 2-commit-old repo).
- NOTE: after orphan checkout the remote config survives, but re-add origin if
  missing. THE SAFETY LAYER BLOCKS `git push --force` without user consent —
  get explicit approval first (user denied once; they wanted keys INCLUDED,
  which is a normal fast-forward, not a scrub).
