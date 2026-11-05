# Qubit UPDATE — installing, checking, applying and rolling back

**Status:** method + command reference. This document describes the update
surface that exists in the tree; it is not a completion or release verdict.

The canonical backend is `voicebridge/update_manager.py`; source, channel and
repository policy live in `voicebridge/update_channel.py`. The CLI surface is
`qubit update` (implemented in `voicebridge/cli.py`). The dashboard update card
and the voice update intents call the same backend, so what works from one
surface works from the others.

A fresh source install (the README's `git clone … && ./scripts/install.sh`) is
a git checkout, even though the package itself is installed non-editable into
the venv. `qubit update` finds the checkout that the running package was
installed from, so the first update does not require an editable install.

## 1. Daily commands

```bash
qubit update                 # read-only status (same as --check)
qubit update --check         # install method, revision, available update
qubit update --apply         # snapshot, fast-forward, reinstall, restart
qubit update --rollback      # restore the previous revision from the snapshot
```

`--json` prints the structured receipt used by the dashboard and voice
surfaces. `--backup-dir DIR` puts the pre-update snapshot somewhere other than
`$QUBIT_HOME/backups/update/`.

`--apply` is non-destructive: it refuses a dirty worktree, takes a git-bundle
snapshot first, and runs only `git pull --ff-only <remote> <ref>`. It never
runs `reset`, `checkout`, `stash`, `clean` or a force flag. `--rollback` is the
one explicit destructive verb, and it snapshots the current tree before
restoring.

## 2. Source and ref

With no extra flags the updater follows the clone's own `origin` and the
configured branch (`update.branch`, default `production`). The public
`cgraph-dev/Qubit` repository (default branch `production`) is the canonical
destination; the retired private `cgraph-dev/Qubit-AIO` spelling is still
recognised as a read-old origin so an existing checkout is not orphaned.

```bash
qubit update --channel stable   # newest strict vX.Y.Z release tag
qubit update --channel beta     # newest SemVer tag including pre-releases
qubit update --channel canary   # branch head (every commit)
qubit update --tag v0.0.5       # one explicit tag
qubit update --source origin    # explicit remote name or local path
```

`--apply --channel NAME` persists the channel under `$QUBIT_HOME`
(`qubit_update_channel.json`), so later checks and applies keep using it. A
channel that follows tags resolves the newest matching tag with
`git ls-remote --tags`; it falls back to the branch head only when no tag
matches. An explicit `--tag` always wins.

`update.remote`, `update.remote_url`, `update.branch`, `update.tag` and
`update.repo_root` in the configuration override the defaults. `repo_root` is
only needed for an unusual layout where the checkout cannot be derived from
the installed package or its venv.

## 3. What an update does

1. Refuses an emergency-stop latch and a dirty worktree.
2. Writes a timestamped snapshot (manifest plus git bundle) under the update
   backup directory.
3. Fast-forwards the checkout to the selected branch, channel tag or explicit
   tag.
4. Reinstalls the package from the updated checkout and runs the configured
   dependency command.
5. Migrates the configuration schema, compiles/imports the critical startup
   modules and checks the dashboard bundle exists.
6. Restarts only the Qubit units this install owns.
7. Writes a receipt with the pre- and post-update revisions and the exact
   rollback command.

A failed post-pull step restores the pre-update revision and records
`rolled_back`; success is never claimed when a step failed.

## 4. Legacy-source guard

The update source must be Qubit-owned code. An explicit source URL that names
the pre-rebrand assistant (`hermes`, `nousresearch`, `nous`, `openclaw`) is
refused with a typed `UPDATE_LEGACY_SOURCE_URL` instead of being fetched.
The clone's own `origin` remote name is always allowed; a legacy-branded remote
URL is replaced by the canonical Qubit URL. The updater also refuses to treat
the operator's external `~/.hermes` reference home as Qubit state, and it never
restarts the foreign `hermes-dashboard.service`.

## 5. Install shapes

* **Git checkout (source install).** Fully updatable in place, including the
  fresh non-editable `scripts/install.sh` layout. This is the supported path
  for a personal or school machine that cloned the repository.
* **Release archive or wheel.** There is no `.git` tree, so `qubit update`
  reports the install as `package` and names the reinstall command. Re-run the
  bootstrap/installer for a new release; an in-place pull is not possible.
* **Bootstrap prefix install.** The verified bootstrap extracts a release
  archive, installs into `$QUBIT_HOME`, and deletes the extraction. It is a
  package install for update purposes: upgrade by running the bootstrap again
  against the newer release assets.

## 6. If an update reports "package"

Check what the updater resolved:

```bash
qubit update --check --json
```

`install_method` should be `git-checkout` for a clone install. If it is
`package`, confirm the running launcher belongs to the checkout's venv
(`<checkout>/.venv/bin/qubit`) or set `update.repo_root` to the checkout path.
A real wheel/site-packages install is expected to report `package`.

## 7. Rebuilding the dashboard

The updater reinstalls the Python package and validates that the dashboard
bundle exists. If the update changed the dashboard source and the bundle needs
a fresh build, run:

```bash
./scripts/build_dashboard.sh
```

from the checkout, then restart the Qubit service. A release archive already
contains a bundle and does not need this step.
