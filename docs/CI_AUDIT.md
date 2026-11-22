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
- [ ] After PR #23 lands, add a **required** dashboard log-search regression lane; do not skip absent tests and report green.
- [ ] Add a dashboard acceptance suite against an installed server: authenticated chat, sessions, logs, env, model provider selection, WebSocket reconnect, and error cases.
- [ ] Add Qubit voice acceptance with fake audio devices/providers and separate optional hardware qualification.
- [ ] Add adapter contracts for Hermes plugin manifests, skill discovery, MCP registration and permission boundaries. Test current adapters, not hypothetical universal compatibility.
- [ ] Add reproducible frontend shared-package install; do not switch to `npm ci` until its lockfile is generated and checked in.
- [ ] Enable macOS core tests for PRs once hosted runners are provisioned; add Windows installer smoke as a separate explicit capability tier.
- [ ] Add fresh-install, upgrade, rollback, persisted-state and dashboard-enabled release qualification.
- [ ] Document required status checks in GitHub branch protection. A passing optional workflow is not an enforced merge gate.
- [ ] Audit upstream-derived tests for stale Hermes paths, environment variables, names, mock-only assertions and false-positive skips.

## CI operating rules

No CI job should silently pass when a required test file is missing. A check must distinguish **not run**, **failed**, and **passed**. Avoid exposing live API keys to untrusted pull requests. Pin GitHub Actions to immutable commit SHAs and minimize permissions. A workflow definition is not evidence of execution; verify actual run conclusions before merging a runtime fix.
