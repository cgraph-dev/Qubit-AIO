# Install ref rule — what a fresh "Install Qubit locally" gets

Status: **decision recorded, option (b) implemented (code only, nothing published)**.
Owner action still required — see [Open decision](#open-decision-for-the-owner).

## The rule as it stands

A fresh install is **forced onto the latest GitHub release tag**, not the tip of
`main`:

| Where | What it does |
|---|---|
| `qubit_runtime/scripts/install.sh:23` | `REPO_URL` defaults to `https://github.com/cgraph-dev/Qubit.git` |
| `install.sh:197-204` | `is_official_repository` → true for `cgraph-dev/qubit` **and** `cgraph-dev/qubit-aio` |
| `install.sh:208-223` | `official_origin_url` → same two repos |
| `install.sh:727-737` | unconfigured install + official origin ⇒ `channel=stable` |
| `install.sh:738-749` | `latest_stable_tag()`; on failure a fresh install `fail … release_lookup_failed` |
| `install.sh:945-960` | clone then `checkout --force --detach "$release_tag^{commit}"` |
| `install.sh:168-181` | `latest_stable_tag` = `GET /repos/<repo>/releases/latest`, tag must match `^v<major>.<minor>.<patch>$` |

Opt-out is `--channel main` / `QUBIT_INSTALL_CHANNEL=main` (`install.sh:25`,
`:727`). A fork or local-path origin is *not* official and stays on `main`.

## Why a fresh install is stale today (measured)

Live against the two repos, 2026-11-08:

```
$ git ls-remote --heads --tags https://github.com/cgraph-dev/Qubit.git
e90dea36d91da7cdf388ecb965cdc765e773cf95  refs/heads/main
e90dea36d91da7cdf388ecb965cdc765e773cf95  refs/heads/production
1392fe019ee9d8a85c04ebdacac5055656bea68b  refs/tags/v0.0.14
e90dea36d91da7cdf388ecb965cdc765e773cf95  refs/tags/v0.0.14^{}   # newest tag

$ git ls-remote --heads https://github.com/cgraph-dev/Qubit-AIO.git
4e4a25738f29d634dd58e48892c90a571684c832  refs/heads/main            # no tags in the whole listing

$ curl -s -o /dev/null -w '%{http_code}' https://api.github.com/repos/cgraph-dev/Qubit/releases/latest
200     # → tag_name v0.0.14
$ curl -s -o /dev/null -w '%{http_code}' https://api.github.com/repos/cgraph-dev/Qubit-AIO/releases/latest
404
$ curl -s -o /dev/null -w '%{http_code}' https://api.github.com/repos/cgraph-dev/Qubit-AIO/tags
404
```

So the default (`Qubit.git`) resolves `v0.0.14` → commit `e90dea36
"release: Qubit 0.0.14 public seed"`. Everything landed since lives on
`Qubit-AIO@main` (+2 commits from the local checkout's `origin/main`) plus local
unpushed work.

Direct proof from two shallow clones:

```
AIO main            : 4e4a2573 feat(webui): put Qubit Workbench in the sidebar
  grep -c _SESSION_HEADER_ALIASES qubit_runtime/qubit_cli/web_server.py  → 3
Official v0.0.14    : e90dea36 release: Qubit 0.0.14 public seed
  grep -c _SESSION_HEADER_ALIASES qubit_runtime/qubit_cli/web_server.py  → 0
```

And from the installer's own decision functions, sourced live:

```
url=https://github.com/cgraph-dev/Qubit.git
  github_repository=cgraph-dev/Qubit
  is_official=yes   official_origin_url=yes   latest_stable_tag=v0.0.14
url=https://github.com/cgraph-dev/Qubit-AIO.git
  github_repository=cgraph-dev/Qubit-AIO
  is_official=yes   official_origin_url=yes   latest_stable_tag=FAILED(rc=1)
```

That last line is the heart of it: **both** repos are "official", so
`Qubit-AIO` is forced to `channel=stable` too, its release lookup 404s, and a
fresh install on AIO stops at `release_lookup_failed` with no checkout
(`install.sh:747`). The two repos cannot converge: `Qubit.git` publishes tags but
is frozen at `e90dea36`; `Qubit-AIO` has the code but publishes no tags.

The neighbouring rule is **not** the problem and should be left alone:
`official_origin_url` already accepts `qubit-aio` (`install.sh:220`), so the
"is this a real Qubit source" gate is correct. The defect is that *official*
is treated as *has releases*.

## The two options

### (a) Ship from releases — tag `Qubit` (or `Qubit-AIO`) with today's fixes and publish a release

Add a `vX.Y.Z` tag on a commit that contains the current work, push it, and
publish the GitHub release. Optionally repoint `REPO_URL` at `Qubit-AIO` so the
code and the release feed live in one repo.

- **Product meaning:** users get a *tested, immutable* build. `latest_stable_tag`
  keeps working as designed, `qubit update` moves installs release-to-release,
  and a bad `main` cannot reach a fresh install. Every install is reproducible.
- **Cost:** someone must repeat the tag+release step for **every** change that
  should reach users — a manual, privileged release ceremony. Until it is run,
  fresh installs are stale by construction, which is exactly today's state.
- **Risk:** low at install time (the current code path is already exercised), but
  it needs publishing rights and a green verification pass this lane cannot run.

### (b) Ship from the branch — default install to `Qubit-AIO` and fall back to `channel=main` when `releases/latest` 404s

Fix the rule itself, in two independent halves:

1. Default `REPO_URL` → `cgraph-dev/Qubit-AIO.git` (the repo that actually
   receives commits).
2. A release lookup failure on a **fresh** install falls back to `channel=main`
   with a warning instead of `release_lookup_failed`. A repo with no releases
   is a valid branch-tracking repo, not a broken one.

- **Product meaning:** users track `main` — whatever is merged ships. No release
  ceremony, no staleness by construction. It also makes the documented
  "fork/mirror stays on its branch" promise true for a fork of a tag-having repo
  whose own feed is empty.
- **Cost / risk:** installs are not reproducible and `main` must stay green —
  a broken merge can reach users before a release gate would catch it.

## What was implemented

**Option (b), because it is the only one implementable without publishing**, and
it is the less likely of the two to break a fresh install — today's fresh
install already aborts (`release_lookup_failed`) on the repo that has the code,
so the fallback strictly *adds* a working path where none exists. Option (a)
cannot be done in this lane at all: it requires a tag and a release, which are
out of scope, and a fresh install would still abort on `Qubit-AIO` until that
publish happened.

Changes are confined to `qubit_runtime/scripts/install.sh`:

- `REPO_URL` default → `https://github.com/cgraph-dev/Qubit-AIO.git`, with a
  comment stating why (releases-based installs stay available via
  `QUBIT_REPO_URL=https://github.com/cgraph-dev/Qubit.git`).
- `stage_repository`: a fresh install whose release lookup fails no longer
  `fail`s. It logs a warning and continues on `main` (`channel=main`), which the
  existing clone path already handles (`install.sh:870-880`). The *rerun* branch
  is untouched — it keeps its current checkout, as before.

Deliberately **not** changed: `is_official_repository`, `official_origin_url`,
`latest_stable_tag`, `select_stable_release`. A repo that *does* publish
releases (e.g. `Qubit.git` when selected explicitly) keeps its stable-pin
behaviour, so nothing regresses for existing release users.

## Open decision for the owner

The fix makes fresh installs current **today**, but it does not make the two
repos converge. Pick one:

- **Keep (b) as the shipping model** — accept "users track `main`" as the
  product contract, and make the release feed optional.
- **Move to (a)** — tag and publish from `Qubit-AIO`, then either revert the
  `REPO_URL` default back to a release-publishing repo or let (b)'s fallback
  become dead code that only fires for forks.
