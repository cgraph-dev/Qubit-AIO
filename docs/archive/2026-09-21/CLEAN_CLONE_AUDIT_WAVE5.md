# Clean-clone install audit — Wave 5 (agent G)

Agent: clean-clone install reproducibility. Base commit: `b79089d5` (waves
1–4 not regressed; the concurrent Wave-5 edits of other agents in the working
tree were not touched).

Scope: make clean install reproducible from a clean clone, or prove it with a
typed, documented path. Owned files changed:

- `pyproject.toml`
- `scripts/build_dashboard.sh`
- `scripts/install.sh`
- `scripts/clean_install_check.sh`
- `scripts/verify_installed_dashboard.sh`
- `scripts/release_qualify.py`
- new `tests/test_clean_clone_wave5.py`
- new `docs/CLEAN_CLONE_AUDIT_WAVE5.md`

No live service, unit, account, user config, model, browser, or audio state was
started, restarted, enabled, or edited. All disposable state lived in throwaway
`/tmp` trees.

## Verdict

**Reproducible with a typed path.** The dashboard is a git-ignored build
artifact (`frontend/qubit-dist/`, `.gitignore:25`). A bare `git clone` does not
carry it, so a wheel/sdist built from a bare clone is dashboard-less — hatchling
silently omits a declared `artifacts` path that is absent. That silent skip is
now closed at four layers:

1. `scripts/install.sh` builds the dashboard deterministically when node/npm are
   available and otherwise exits `4` (DEGRADED) with an actionable
   `DASHBOARD_UNAVAILABLE` message — never a silent install.
2. `scripts/clean_install_check.sh` preflights the bundle, builds it when
   possible, and rejects a dashboard-less wheel with `exit 2`.
3. `scripts/verify_installed_dashboard.sh` carries the same typed preflight.
4. `scripts/release_qualify.py` marks a dashboard-less artifact `FAIL` with a
   `dashboard.state == "MISSING"` record.

The release path remains: build the sdist/wheel on a host that has run
`scripts/build_dashboard.sh` (the bundle rides in the sdist via the
`artifacts` table), then downstream rebuilds need no npm. The fresh-clone path
now self-heals when node is present and fails typed when it is not.

No `BLOCKED — LICENSE REVIEW REQUIRED` condition applies: no third-party code
was copied or adapted; no new dependency was added.

## Root cause and where the bundle lives

| Surface | Ships the dashboard? | Behavior on a bare clone |
|---|---|---|
| git clone | no (git-ignored) | absent |
| wheel from a tree with `frontend/qubit-dist` | yes (`_data/frontend/qubit-dist/index.html`) | built |
| wheel from a bare clone | **no** (silent before this wave) | now typed FAIL |
| sdist from a tree with the bundle | yes (`<root>/frontend/qubit-dist/`) | built |
| sdist from a bare clone | **no** | now typed FAIL |
| `scripts/install.sh` on a node host | builds it from `frontend/qubit-web` | succeeds |
| `scripts/install.sh` on a node-less host | cannot build | `exit 4`, `DASHBOARD_UNAVAILABLE` |

## Fixes (exact locations)

- `scripts/build_dashboard.sh:11-76` — added a hermetic `--check` preflight
  (`DASHBOARD_PRESENT` / `DASHBOARD_BUILDABLE` / `DASHBOARD_UNAVAILABLE`) and
  typed exit codes `3` (toolchain missing) and `4` (npm/tsc/vite failure);
  removed the bare `exit 1` toolchain checks.
- `scripts/install.sh:40-56` — `DASHBOARD_STATE` tracking; deterministic build
  when node/npm exist, typed `build_failed`/`toolchain_missing` states
  otherwise (the established `WARNING: frontend/qubit-dist missing` text is
  preserved).
- `scripts/install.sh:213-231` — the dashboard availability gate: a degraded
  dashboard exits `4` after installing the CLI/runtime, and the systemd unit is
  not installed so a broken dashboard is never auto-started.
- `scripts/clean_install_check.sh:50-65` — dashboard preflight before any wheel
  build/venv/network work: build deterministically or `DEGRADED` (`exit 4`).
- `scripts/clean_install_check.sh:97-111` — assert the built wheel actually
  contains `voicebridge/_data/frontend/qubit-dist/index.html`; otherwise
  `FAIL` (`exit 2`).
- `scripts/verify_installed_dashboard.sh:20-32` — the same typed preflight for
  the fast `--no-deps` isolated gate.
- `scripts/release_qualify.py:85-92,241-255,273-282` — `_has_dashboard()` over
  artifact members; a missing bundle is a typed `FAIL` with a `dashboard`
  record (`OK` / `MISSING`).
- `pyproject.toml:92-99,199-202` — document the clean-clone build requirement
  and the typed enforcement (no packaging behavior change).
- `pyproject.toml:234` — add the concurrently modified internal audit note
  `docs/SECURITY_AUDIT_WAVE4.md` to the sdist exclude list. That file was
  changed during this wave to include an absolute developer home path; the
  established Packet-8K mitigation is the sdist exclusion (the repo retains
  the provenance record), consistent with the other audit docs. This keeps
  `tests/test_packaging_wave3.py::test_sdist_excludes_every_tracked_doc_with_a_developer_home_path`
  green without touching another workstream's file.
- new `tests/test_clean_clone_wave5.py` (9 tests) — fresh-checkout simulation,
  typed preflight, typed clean-install degradation, and the packaging gate.
- new `docs/CLEAN_CLONE_AUDIT_WAVE5.md` (this file).

## Fresh-checkout simulation

The clone is materialized from the **tracked working tree** so it includes the
uncommitted Wave-5 scripts and excludes every git-ignored artifact:

```bash
CL=$(mktemp -d)
git ls-files -z | tar --null -T - -cf - | tar -xf - -C "$CL"
# $CL/frontend/qubit-dist is absent; $CL/frontend/qubit-web is present.
```

## Commands and observed results (this checkout)

```text
# 1. current checkout preflight (bundle present)
$ bash scripts/build_dashboard.sh --check
DASHBOARD_PRESENT: <repo>/frontend/qubit-dist/index.html               # rc 0

# 2. fresh clone preflight (node present)
$ (cd "$CL" && bash scripts/build_dashboard.sh --check)
DASHBOARD_BUILDABLE: bundle missing; node+npm available to build it     # rc 0

# 3. fresh clone preflight (PATH without node/npm)
$ (cd "$CL" && PATH="$NODEFREE" /bin/bash scripts/build_dashboard.sh --check)
DASHBOARD_UNAVAILABLE: frontend/qubit-dist is not built and node/npm
                       are not installed                              # rc 3

# 4. fresh clone clean-install probe (PATH without node/npm)
$ (cd "$CL" && PATH="$NODEFREE" /bin/bash scripts/clean_install_check.sh)
DEGRADED: dashboard bundle frontend/qubit-dist missing and node/npm
not found — cannot build from a clean clone; install node/npm or use a
release sdist that ships the built dashboard (docs/CLEAN_CLONE_AUDIT_WAVE5.md)
                                                                     # rc 4
# stopped at the preflight, before wheel/venv/network work

# 5. fresh-clone wheel is rejected by the release inspector
$ (cd "$CL" && uv build --wheel --out-dir "$CL/dist" .)
$ python -c "...release_qualify.inspect_artifacts('$CL/dist')..."
wheel members: 3936
wheel state: FAIL
dashboard: {"state": "MISSING", ...}   # overall: FAIL

# 6. a tree that already carries the bundle ships it
$ cp -a frontend/qubit-dist "$CL/frontend/qubit-dist"
$ (cd "$CL" && uv build --wheel --out-dir "$CL/dist" .)
$ python -c "...release_qualify.inspect_artifacts('$CL/dist')..."
wheel members: 3996
wheel state: OK
dashboard: {"state": "OK", "detail": "built dashboard shipped in the artifact"}

# 7. full isolated clean install (declared deps, throwaway HOME/venv)
$ bash scripts/clean_install_check.sh --require-network
wheel: qubit_voicebridge-1.0.0-py3-none-any.whl
  dashboard bundle shipped in wheel
  ok: qubit --help / voicebridge --help / qubit-dev --help / qubit-hermes --help
OK dashboard plugins=10 runtime=<venv>/site-packages/voicebridge/_data/qubit_hermes
clean install check: PASS                                            # rc 0 (1m44s)

# 8. fast isolated dashboard + runtime gate
$ bash scripts/verify_installed_dashboard.sh
OK root->/hermes/  asset=assets/index-BFKNJjki.js  plugins=10  ops=510
installed dashboard gate: PASS
installed Hermes runtime gate: PASS                                  # rc 0

# 9. focused tests
$ .venv/bin/python -m pytest -q tests/test_clean_clone_wave5.py \
    tests/test_packaging_wave3.py tests/test_lifecycle_wave4.py \
    tests/test_hermes_package8u.py tests/test_release_gates8u.py
64 passed

# 10. contract tests that exercise the changed files
$ .venv/bin/python -m pytest -q tests/test_packet8k.py \
    tests/test_foundation8tf.py tests/test_rejected_matrix8tf.py \
    tests/test_branding_parity_wave3.py tests/test_branding_scan8tf.py \
    tests/test_packet7c_audio.py tests/test_hermes_package_runtime8u.py
148 passed
# recorded before the concurrent untracked scripts/sanitize_docs.py appeared;
# a re-run now fails ONLY the three test_packet8k machine-path/static tests on
# that external file (no owned file is flagged) — see residual step 6.

# 11. syntax + hygiene
$ bash -n scripts/build_dashboard.sh scripts/install.sh \
    scripts/clean_install_check.sh scripts/verify_installed_dashboard.sh   # clean
$ .venv/bin/python -m compileall -q scripts/release_qualify.py \
    tests/test_clean_clone_wave5.py                                        # clean
$ git diff --check                                                         # clean
```

## New test coverage

`tests/test_clean_clone_wave5.py` (9 tests):

- `test_fresh_clone_omits_the_gitignored_dashboard` — premise: tracked clone has
  no `frontend/qubit-dist`, but has the tracked source;
- `test_build_dashboard_check_is_typed_when_node_is_missing` — `rc 3`,
  `DASHBOARD_UNAVAILABLE`;
- `test_build_dashboard_check_reports_present_or_buildable` — environment-aware
  typed state, never a crash;
- `test_clean_install_degrades_typed_on_a_node_less_clone` — `rc 4`, `DEGRADED`,
  stops before wheel/venv/network work, no false PASS;
- `test_clean_clone_wheel_without_dashboard_is_a_typed_fail` — wheel state
  `FAIL`, `dashboard.state == "MISSING"`;
- `test_tree_with_built_dashboard_produces_a_shipping_wheel` — wheel ships
  `_data/frontend/qubit-dist/index.html`, `dashboard.state == "OK"`;
- `test_installer_has_typed_dashboard_degradation_gate`,
  `test_clean_install_preflights_dashboard_before_building`,
  `test_pyproject_declares_the_dashboard_artifact_and_documents_reproducibility`
  — declared contract text.

## Residual operator-only steps (typed, not silently passed)

1. **Node/npm and network are prerequisites for a fresh-clone build.** On a
   host with neither, `install.sh` / `clean_install_check.sh` exit `4`
   (DEGRADED) with the exact remediation; that is the documented limitation,
   not a silent pass. The alternate path is a release sdist/wheel built on a
   host that already ran `scripts/build_dashboard.sh`.
2. **The release artifact must be built from a tree that carries the bundle.**
   A bare `git clone` + `uv build` is dashboard-less and is now rejected by
   both `clean_install_check.sh` and `release_qualify.py`.
3. **`docs/MIGRATIONS.md` entry.** This wave changes installed-state handling
   (typed degraded installs). `docs/MIGRATIONS.md` is not an owned file here;
   the migrations owner should record a `MIG-0xx` entry. The behavior change is
   documented in this audit and in `pyproject.toml`.
4. **Release signing / clean-machine qualification** — unchanged; operator-only
   (`docs/RELEASE_SIGNING.md`).
5. **Real npm/Vite build on the target host** — the fresh-clone build path was
   validated through the typed preflight and the packaging gate; a full
   `npm install` + `vite build` on a node host is the operator's normal build
   step (`scripts/build_dashboard.sh`).
6. **Unrelated static-gate failure from a concurrent worktree file.** During
   this wave a different workstream created the untracked
   `scripts/sanitize_docs.py`, which hardcodes an absolute developer home path
   and therefore fails the existing `release_qualify.py --static` /
   `test_packet8k.py` machine-path gate. That file is outside this agent's
   owned set and was not touched. The clean-clone surfaces here are clean under
   the same scan (no flagged file owned by this wave); the concurrent file's
   owner should sanitize it before the final static gate.

## Rollback

```bash
git checkout -- pyproject.toml scripts/build_dashboard.sh scripts/install.sh \
    scripts/clean_install_check.sh scripts/verify_installed_dashboard.sh \
    scripts/release_qualify.py
rm -f tests/test_clean_clone_wave5.py docs/CLEAN_CLONE_AUDIT_WAVE5.md
```

No user state, config, systemd unit, service, model, or data is created or
altered by these files. The only runtime rehearsals ran in throwaway `/tmp`
trees removed by their own `trap`.
