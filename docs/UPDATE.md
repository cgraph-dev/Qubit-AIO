# Qubit UPDATE — source, channel, and ref policy

This is the documented Qubit update policy. The **canonical source/channel
resolver is `voicebridge/update_channel.py`**; the **git mechanism (the real
verbs) is `voicebridge/update_manager.py`**; the CLI surface is
`qubit update …`.

Everything here is Qubit. No legacy assistant name appears in this document;
legal/provenance attribution lives only in `LICENSE` and
`THIRD_PARTY_NOTICES.md`.

**Canonical repository:** `https://github.com/cgraph-dev/Qubit-AIO`.
The default remote is `origin`, the default branch is `main`, and the default
channel is `stable`.

## 1. Defaults

| Setting | Default | Config key | Notes |
|---|---|---|---|
| Repository identity | `https://github.com/cgraph-dev/Qubit-AIO` | — | The canonical public Qubit repo (`cgraph-dev/Qubit-AIO`). A **default destination only**, never a hard requirement. |
| Remote name | `origin` | `update.remote` | A git remote *name*. The clone's own `origin` is always allowed — a public user's `origin` **is** the Qubit repo. |
| Branch | `main` | `update.branch` | Tracked when a channel follows the branch head. |
| Tag | *(none)* | `update.tag` | Optional explicit version tag (e.g. `v1.2.3`). |
| Channel | `stable` | `update.channel` (fallback) | `stable` \| `beta` \| `canary`. Persisted under `QUBIT_HOME` (see §6). |
| Full URL override | *(none)* | `update.remote_url` | Optional; replaces `update.remote`. A URL with legacy branding is **refused** (§4). |
| Checkout to inspect | the repo root | `update.repo_root` | Absolute path; defaults to the running checkout. |
| Backup root | `<state>/backups/update/` | `update.backup_dir` | Timestamped snapshots (§7). |

Resolution order for the **source** (`resolve_source`): explicit override
(`--source`) → `update.remote_url` → `update.remote` (default `origin`) →
`update.branch` (default `main`). An `update.remote` value that is itself a URL
is treated as a URL (validated and guarded). Passing `update.remote` as a bare
name is never resolved to a URL by Qubit — git resolves it, so the clone may
legitimately point `origin` at its own mirror.

## 2. Channel → ref policy

`release_tag_support(channel)` decides whether a channel follows a version
**tag** or the **branch head**:

| Channel | Follows | Tag shape | Policy |
|---|---|---|---|
| `stable` | version **tag** | `^vMAJOR.MINOR.PATCH$` (strict SemVer, no suffix) | Untagged branch commits are never delivered. |
| `beta` | version **tag** | SemVer incl. `-rc.N` / `-beta.N` / `+build` | Pre-releases are eligible. |
| `canary` | **branch head** (`update.branch`) | — | Every commit is eligible; a configured `update.tag` is ignored by policy. |

`resolve_source` reports `follow`, `ref`, `ref_kind`, `pinned`, and
`resolve_latest_tag`. When `follow == "tag"` but no `update.tag` is pinned,
`resolve_latest_tag` is true: the consumer must resolve the newest tag matching
the channel pattern at fetch time. Until a release tag exists in the repository,
the concrete `ref` falls back to the branch head so updates remain possible.

## 3. Anonymous vs authenticated updates

`anonymous_update_supported(remote_url)` is the documented heuristic:

- **Public forge over `http(s)`, no embedded credentials** (`github.com`,
  `gitlab.com`, `codeberg.org`, `bitbucket.org`, …) → **anonymous** pull works.
- **Local path / `file://`** source → no remote auth needed (anonymous).
- **`ssh`, `git`, or scp-style (`user@host:path`)** → **requires the user's own
  auth** (an SSH key or credential helper).
- **A URL carrying userinfo** (`https://user:token@…`) or a private/self-hosted
  host (`localhost`, RFC-1918 ranges, `*.local`/`*.internal`, single-label
  hosts) → **requires explicit user auth**.
- Private sources use the user's git credential helper or a token supplied via
  the **environment**. Qubit **never embeds a token in a URL** and **never
  prints one**: `redact_url()` strips any `user:token@` userinfo (and
  `?token=`/`?password=` query values) from every URL this policy returns.

## 4. Legacy-source guard

`validate_remote_url(url)` does strict parsing. An explicitly configured update
**source URL** whose text contains legacy assistant branding is refused with a
typed result. The guard's exact matched-token list (`hermes`, `nousresearch`,
`nous`) is an **internal safety identifier** (`voicebridge/update_channel.py`),
not public product copy:

- code: `UPDATE_LEGACY_SOURCE_URL`
- fields: `policy` (the exact rule, with the matched token), `fallback_remote`
  (`origin`).

The guard applies **only to a URL**. Using the clone's **own `origin` remote
name** is always allowed. A malformed URL returns
`UPDATE_SOURCE_URL_INVALID`. An invalid channel raises `ValueError`.

## 5. CLI verbs

The sanctioned Qubit update surface (Owner Decision #11):

```
qubit update                 # read-only status (same as --check)
qubit update --check         # read-only: product version, install method,
                             # revision, configured source, runtime image
qubit update --apply         # timestamped backup, then fast-forward the checkout
qubit update --rollback      # restore the recorded pre-apply revision
qubit update --json          # structured receipt for any verb
qubit update --source <ref>  # override the source for this call (remote name or path)
qubit update --backup-dir <d># explicit snapshot directory
```

The three verbs are mutually exclusive (choosing one is the explicit operator
intent). At the API layer `update_manager.apply()` requires `confirm=True`; the
CLI supplies it when the explicit `--apply` verb is chosen. A bare `--dry-run`
previews a mutating verb without writing a backup or running git.

Order for a mutating verb: **confirm → e-stop → test guard → checkout → clean
worktree → act**. It refuses (never executes) on any earlier failure, and
`apply` never runs a destructive verb — it is `git pull --ff-only <remote>
<branch>` only. `rollback` is the sole destructive path (`git reset --hard
<recorded-revision>`), and it writes a **pre-rollback snapshot first** so the
rollback is itself reversible.

## 6. Channel persistence and locations

The channel record is written **only under `QUBIT_HOME`** (never in the source
tree, never a secret):

- Canonical file: `<QUBIT_HOME>/qubit_update_channel.json` (0600, atomic
  write). Read-old fallback: `<QUBIT_HOME>/update_channel.json`. A configured
  `update.channel` key is read only when no valid record file exists.
- `QUBIT_HOME` resolves to the canonical runtime home
  (`$XDG_STATE_HOME/qubit-voicebridge/runtime`, i.e.
  `~/.local/state/qubit-voicebridge/runtime` by default), honoring an explicit
  non-external `QUBIT_HOME`. The external reference home is never written.

Related locations:

- Config: `~/.config/qubit-voicebridge/voice.yaml`.
- Runtime/state: `$XDG_STATE_HOME/qubit-voicebridge/` (runtime home
  `…/runtime`, one `state.db`).
- Update backups: `<state>/backups/update/<UTC-timestamp>/` — each holds
  `manifest.json` and (for a git checkout) `code.bundle` produced by
  `git bundle create HEAD`, so an update and a rollback are both reversible.

## 7. Package / container / immutable behavior

- **Git checkout (source install):** `--check`/`--apply`/`--rollback` work as
  described.
- **Package install (no `.git`):** the updater does **not** self-update in
  place. `update_manager` returns a typed operator message (`UPDATE_OPERATOR_OWNED`)
  naming the reinstall command — `scripts/install.sh` (and, for the messaging
  gateway, `scripts/install_gateway_unit.sh`). A package install still gets a
  backup manifest (no bundle) so the operator has a record.
- **Container / immutable / externally-stewarded install:** updates are owned
  by the image/steward, not by in-place git. Bump the image tag or re-run the
  operator provisioning; the channel policy here still names the intended
  source but does not mutate the container.
- **Lifecycle guards:** `apply` refuses a dirty worktree (Qubit never discards
  an operator's uncommitted work), refuses a non-git install with the exact
  operator command, and requires the single e-stop authority to be clear.
  `rollback` refuses when no backup exists (`UPDATE_NO_BACKUP`).

## 8. Verification commands

```bash
.venv/bin/python -m py_compile voicebridge/update_channel.py
PYTHONPATH=. .venv/bin/python -c \
  "from voicebridge import update_channel as C; \
   print(C.resolve_source({}, None)); print(C.resolve_channel({})); \
   print(C.validate_remote_url('https://github.com/cgraph-dev/Qubit-AIO.git'))"
```

## Provenance (legal note — do not remove from the license/notices)

Qubit is an independently branded product built on an upstream foundation whose
copyright and license attribution is preserved in `LICENSE` and
`THIRD_PARTY_NOTICES.md`. That attribution is the only permitted legacy name in
shipped material; it is not part of the runtime update source, channel, or ref
policy described above.
