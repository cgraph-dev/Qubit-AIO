# Qubit CI ownership and coverage audit

Updated: 2026-11-22. This is an inspection of workflow definitions, **not** a claim that their tests passed.

## Provenance and boundaries

Qubit carries an upstream-derived Hermes agent runtime and E2E strategy, while maintaining Qubit-specific release tooling, voice, dashboard, adapter and packaging changes. The repository's existing `.github/workflows/ci.yml` explicitly calls out upstream-derived workflows. Similarities are architectural evidence of lineage, not a byte-for-byte provenance determination; exact file ancestry requires a Git history comparison.

Do not delete inherited tests merely because they came from Hermes. Keep them when they exercise Qubit's current behavior, and add Qubit-specific gates when the product differs.

## Verified from current workflow source

| Workflow / lane | What it currently checks | Gap |
| --- | --- | --- |
| `ci.yml` runtime-smoke | Compile runtime/voice, parse tests, check package metadata | Compilation does not prove the agent starts or completes a turn |
| `ci.yml` frontend | Dashboard typecheck, unit tests and build | Does not exercise a live installed dashboard on port 8890 |
| `ci.yml` packaging | Build, install, reproduce release artifacts | Does not by itself qualify all real dashboard workflows |
| `ci.yml` macos-core | macOS install/launchd contract | Manual dispatch only |
| `ci.yml` lockfiles | Validate existing workspace lockfiles | Shared frontend dependency install still uses `--no-package-lock` |
| `e2e.yml` | Eight Linux shards running E2E files | Test coverage must be mapped to Qubit-specific requirements |
| `qubit-regressions.yml` | Qubit public release verifier hermetic tests | New explicit, independent check; require it in branch protection after first green run |

## Ordered remediation

- [x] Add a lightweight Qubit-owned release-verifier test lane.
- [x] PR #23 adds a dashboard log-search regression workflow that fails if its tests are missing. Require its check in branch protection after it passes.
- [ ] Add a dashboard acceptance suite against an installed server: authenticated chat, sessions, logs, env, model provider selection, WebSocket reconnect, and error cases.
- [ ] Add Qubit voice acceptance with fake audio devices/providers and separate optional hardware qualification.
- [ ] Add adapter contracts for Hermes plugin manifests, skill discovery, MCP registration and permission boundaries. Test current adapters, not hypothetical universal compatibility.
- [ ] Add reproducible frontend shared-package install; do not switch to `npm ci` until its lockfile is generated and checked in.
- [ ] Enable macOS core tests for PRs once hosted runners are provisioned; add Windows installer smoke as a separate explicit capability tier.
- [ ] Add fresh-install, upgrade, rollback, persisted-state and dashboard-enabled release qualification.
- [ ] Document required status checks in GitHub branch protection. A passing optional workflow is not an enforced merge gate.
- [ ] Audit upstream-derived tests for stale Hermes paths, environment variables, names, mock-only assertions and false-positive skips.

## Workflow security follow-up

The repository's `.github/scripts/check_workflow_security.py` enforces immutable action references, least-privilege permissions, no PR-target triggers, no self-hosted PR runners and restricted secrets for managed workflows. Its explicit `LEGACY_WORKFLOWS` exemption currently includes `windows-ci.yml` and `windows-install-update-e2e.yml`. These workflows are **not** covered by the managed security policy and require manual review and eventual migration; exemption must not be mistaken for a clean security audit.

## CI evidence and triage (2026-11-22)

The Qubit regression gate passed on PR #24 at commit `31bbd7e` after the workflow installed pytest, PyYAML and uv. This verifies the hermetic release-verifier and fake fresh-install contracts, **not** a real end-user installation. The dedicated dashboard log regression and install-E2E workflows passed on PR #23 at commit `783cb7e`; its general CI and E2E checks had not completed when inspected. Neither PR is merged solely on that evidence.

The completed PR #22 runs exposed **independent** failures: seven Manga frontend contract assertions; upgrade tests involving a stale Git index lock and dirty checkout; two fresh-process tests failing on root `project.license` metadata; a concurrent dashboard session read timing assertion; and a fresh-install test reporting a dirty checkout. PR #22 itself modifies only the release scanner and verifier tests. Do not mark these unrelated failures as fixed by the scanner change. The root license metadata fix is proposed separately in PR #26 and still needs CI confirmation.

The Windows workflow SHA-pin and least-privilege migration is proposed separately in PR #25. Until that PR merges, the legacy-workflow exemptions remain on `main`; do not describe the Windows security migration as landed.

## CI operating rules

No CI job should silently pass when a required test file is missing. A check must distinguish **not run**, **failed**, and **passed**. Avoid exposing live API keys to untrusted pull requests. Pin GitHub Actions to immutable commit SHAs and minimize permissions. A workflow definition is not evidence of execution; verify actual run conclusions before merging a runtime fix.
