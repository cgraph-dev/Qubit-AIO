# Qubit engineering plan

Status: in progress. Owner: solo developer, assisted by AI. Updated: 2026-11-23.

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
- [x] P1: Make release archive secret scanning fail closed when content cannot be fully inspected; add large-file and scan-budget tests (merged PR #36; dedicated archive security, CI and E2E passed 2026-11-23).
- [ ] P1: Verify dashboard behavior at port 8890: /chat, /sessions, /logs, /env, keys, and model-provider views. Enumerate actual routes and test every supported action against real backend APIs; no hardcoded success fixtures as release proof.
- [ ] P1: Add full dashboard-enabled clean install and model provisioning tests in addition to the existing minimal install gate.
- [ ] P1: Exercise upgrade and rollback with real persisted user state.
- [ ] P1: Verify gateway authentication, streaming reconnect, concurrent sessions, and errors from unavailable model providers.
- [ ] P2: Pin reproducible shared frontend dependencies in CI; validate macOS and Windows separately.
- [ ] P2: Replace operator-specific release path checks with portable detection and regression tests.
- [ ] P2: Audit existing documentation references, then publish one original Qubit documentation set in both repositories. Cover architecture, features and limits, installation, security, privacy, model providers, voice, web UI, troubleshooting, developer workflow, and release process.
- [ ] P2: Compare public Hermes and OpenClaw documentation and interaction patterns for coverage only; do not copy their wording, artwork, or identity.

## P0 acceptance implementation map (2026-11-23)

The Linux artifact-install probe in `scripts/clean_install_rehearsal.py`
currently exercises a built wheel with isolated state and a real HTTP listener.
PRs #40-#44 merged protected-route token rejection, authenticated plugin
catalog and session reads, listener restart, and canonical transcript
persistence checks. PR #47 merged the fresh-install health verifier repair.
The [November 24 results](e2e/2026-11-24-packaged-acceptance.json) record the
local candidate's authenticated create, loopback-provider streaming, and
active cancellation coverage. The deployed provider and full release checks
remain open.

Implement the remaining P0 release acceptance as a coherent milestone rather
than separate PRs for each assertion:

1. **Real session lifecycle:** use the installed server's
   `/agent/api/qubit/chat/sessions` create/show routes and the canonical
   `voicebridge.chat_transport` store. Exercise a unique session ID in
   isolated `QUBIT_HOME`; assert the returned ID, message transcript and
   canonical `/agent/api/sessions` inventory agree. Avoid mock-only rows.
2. **Turn execution:** drive a deterministic, explicitly configured fake
   provider through the *real* chat transport and HTTP streaming route;
   validate event ordering, final response, errors and cancellation. Never
   require production credentials in untrusted PR CI.
3. **Persistence and recovery:** stop the server, start a fresh process using
   the same isolated state, and verify session metadata **and message contents**
   by ID. Exercise an interrupted turn and its documented recovery contract.
   The existing listener-only restart probe is insufficient.
4. **Privilege and adversarial cases:** test unauthorized session mutations,
   forged token variants, prompt-injection attempts to cross tool permissions,
   secret redaction, path traversal and emergency-stop behavior. Assert typed
   refusals and no host-home writes.
5. **Release gate:** run the above against the *packaged wheel*, publish
   structured PASS/FAIL/NOT_RUN evidence, and make required acceptance checks
   explicit in branch protection. Keep provider/hardware-dependent tests in a
   separate opt-in qualification tier.

Other open work: dashboard routes for logs, env, keys and model providers;
model provisioning; real upgrade/rollback; gateway reconnect/concurrency;
macOS and Windows qualification; frontend dependency reproducibility; original
Qubit documentation. None of these should be marked complete based solely on
the current HTTP smoke tests.

## Recent validated integrations (2026-11-23)

- PR #32: Qubit-owned regression CI and audit documentation (merged).
- PR #33: Dashboard concurrent-session test stabilization (merged).
- PR #34: Full-history dashboard log search and pagination regressions (merged).
- PR #35: Windows workflow security hardening (merged).
- PR #36: Fail-closed release archive scanning, unsafe tar-entry checks, and dedicated regressions (merged).

These merges establish tested components, not end-to-end production acceptance or verification of a live dashboard on port 8890. The P0 acceptance and adversarial security work remain open.

## Work stages

1. Inventory code and existing tests; record exact routes, handlers, and integration contracts.
2. Implement and verify focused fixes in the local Qubit-AIO checkout. Keep concurrent changes separate from the commit being validated.
3. Fetch `origin/main`, verify the diff and acceptance evidence, then integrate and push the checked commits directly to Qubit-AIO `main`. Fast-forward the local `main` to match GitHub.
4. Reconcile docs with verified behavior and update documentation in Qubit-AIO and Qubit/production; do not modify Qubit runtime code.
5. Validate a local or disposable port-8890 instance, record known limitations, and publish release notes.

## Definition of done

A task is complete only when its acceptance test passes and evidence is recorded. A route rendering or a button responding visually is not evidence that its backend operation succeeds. A documentation statement must identify whether the capability is verified, supported with limitations, or planned.
