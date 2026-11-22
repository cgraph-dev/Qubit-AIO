# Qubit engineering plan

Status: in progress. Owner: solo developer, assisted by AI. Updated: 2026-11-22.

## Repository boundaries

- **Qubit-AIO** (main): development, implementation, tests, release tooling, dashboard, and documentation.
- **Qubit** (production): documentation updates only. No code, dependency, workflow, or configuration changes without a new explicit instruction.
- The repositories describe one product. Documentation may be synchronized; code is not automatically promoted.

## Release policy

A merged change is not equivalent to a verified deployment. Code changes must be reviewed against their tests and CI results; live behavior on the developer's port 8890 requires access to a running instance or an equivalent reproducible environment. Never print secrets or real environment variable values in test logs.

## Prioritized backlog

- [x] Clarify repository roles in both READMEs (merged 2026-11-22).
- [ ] P0: Establish an automated Linux release acceptance lane using a clean packaged installation, authenticated dashboard/API, agent session, streaming and cancellation, persistence, and service restart.
- [ ] P0: Audit privileged tool boundaries, secret handling, and prompt-injection paths; add adversarial regression tests.
- [ ] P1: Make release archive secret scanning fail closed when content cannot be fully inspected; add large-file and scan-budget tests.
- [ ] P1: Verify dashboard behavior at port 8890: /chat, /sessions, /logs, /env, keys, and model-provider views. Enumerate actual routes and test every supported action against real backend APIs; no hardcoded success fixtures as release proof.
- [ ] P1: Add full dashboard-enabled clean install and model provisioning tests in addition to the existing minimal install gate.
- [ ] P1: Exercise upgrade and rollback with real persisted user state.
- [ ] P1: Verify gateway authentication, streaming reconnect, concurrent sessions, and errors from unavailable model providers.
- [ ] P2: Pin reproducible shared frontend dependencies in CI; validate macOS and Windows separately.
- [ ] P2: Replace operator-specific release path checks with portable detection and regression tests.
- [ ] P2: Audit existing documentation references, then publish one original Qubit documentation set in both repositories. Cover architecture, features and limits, installation, security, privacy, model providers, voice, web UI, troubleshooting, developer workflow, and release process.
- [ ] P2: Compare public Hermes and OpenClaw documentation and interaction patterns for coverage only; do not copy their wording, artwork, or identity.

## Work stages

1. Inventory code and existing tests; record exact routes, handlers, and integration contracts.
2. Implement focused fixes in Qubit-AIO feature branches with regression tests.
3. Run CI and acceptance gates, review diffs, and merge only validated changes into Qubit-AIO/main.
4. Reconcile docs with verified behavior and update documentation in Qubit-AIO and Qubit/production; do not modify Qubit runtime code.
5. Validate a local or disposable port-8890 instance, record known limitations, and publish release notes.

## Definition of done

A task is complete only when its acceptance test passes and evidence is recorded. A route rendering or a button responding visually is not evidence that its backend operation succeeds. A documentation statement must identify whether the capability is verified, supported with limitations, or planned.
