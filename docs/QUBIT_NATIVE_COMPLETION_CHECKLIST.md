# Qubit Native Completion Checklist

Check an item only with inspectable evidence. A report or matrix row alone is
not evidence.

## Scope and preservation

- [ ] Read `AGENTS.md`, the native completion plan, parity contract, and legal records.
- [ ] Hash all unrelated dirty/untracked files before and after the work.
- [ ] No reset, clean, broad checkout, mass formatting, or destructive deletion was used.
- [ ] Exact upstream revisions, licenses, notices, dependencies, and source paths are recorded.

## Qubit identity and runtime

- [ ] `qubit` is the canonical launcher, package, service, shortcut, and completion identity.
- [ ] Qubit owns the effective runtime entry point and installation path.
- [ ] Normal output, commands, services, paths, logs, dashboard, voice, and operational source contain no Hermes/OpenClaw product branding.
- [ ] Upstream names remain only where legally required or explicitly marked as provenance, comparison, or migration data.
- [ ] No external upstream binary, upstream home, `/tmp` runtime, or operator install is used by normal Qubit execution.
- [ ] Identity receipt records adapted source, revision, license, digest, entry point, and effective path.
- [ ] Wrong revision, altered digest, missing runtime, or external substitution fails closed.

## CLI, dashboard, and voice parity

- [ ] Complete pinned Hermes command/interface inventory exists.
- [ ] Complete pinned OpenClaw capability inventory exists.
- [ ] Every supported operation has a real Qubit backend.
- [ ] CLI arguments, help, results, errors, exit codes, persistence, cancellation, and recovery are differentially tested.
- [ ] Every applicable operation has a real authenticated dashboard workflow, not a tile or status-only endpoint.
- [ ] Every implemented operation has a natural voice command with bounded args, confirmation, cancellation, expiry, replay protection, session isolation, and redacted receipt.
- [ ] CLI, dashboard, and voice use the same backend and policy decision.
- [ ] No code-side row is blocked, rejected, deferred, status-only, CLI-only, dashboard-only, or voice-missing.

## Dashboard UX

- [ ] One dashboard combines preserved upstream workflows and integrated OpenClaw workflows.
- [ ] Qubit owns navigation, typography, colors, icons, spacing, responsive layout, accessibility, motion, onboarding, loading, empty, error, degraded, approval, and recovery states.
- [ ] Every visible control calls a real backend and cannot fabricate success.
- [ ] Desktop, narrow/mobile, keyboard, reduced-motion, and critical screen-reader paths are tested.
- [ ] No copied static page, dead button, second dashboard, listener, router, or authority exists.

## Security and lifecycle

- [ ] Broker, permissions, grants, confirmation, audit, redaction, resource limits, cancellation, rollback, and e-stop are authoritative everywhere.
- [ ] No credentials, cookies, browser profiles, raw audio, private prompts, or secrets enter artifacts, logs, URLs, reports, or child environments.
- [ ] Traversal, symlink, hostile instructions, prompt injection, malformed input, overflow, timeout, crash, kill/reap, and stale replay tests pass.
- [ ] Fresh install, upgrade, rollback, uninstall, interrupted migration, read-only, disk-full, and legacy alias tests pass.
- [ ] User config, state, memory, reports, models, audio, and projects survive with exactly one effective Qubit tree.

## Verification gate

- [ ] Focused and affected tests pass with `-W error`.
- [ ] Full hermetic suite passes with zero unexpected failures.
- [ ] Live/dashboard/UI/monitor suites are run separately and reported honestly.
- [ ] `compileall`, dependency check, diff check, secret scan, import audit, package build, clean install, service verification, and Graft build pass.
- [ ] Actual warnings, stderr, receipts, state files, and failure output were inspected.
- [ ] Final diff is scoped and rollback is rehearsed.
- [ ] Independent verifier reproduces the evidence.
- [ ] Remaining manual-only tests have exact operator commands.
- [ ] Only after every code-side box passes: approve the next packet.

## Required handoff

```text
VERDICT: VERIFIED | VERIFIED WITH EXPLICIT LIMITATION | REJECTED
COMMITS: exact hashes
NATIVE QUBIT RUNTIME: evidence and effective path
HERMES PARITY: implemented count and remaining rows
OPENCLAW PARITY: implemented count and remaining rows
CLI/DASHBOARD/VOICE: exact tests and shared-backend proof
FULL TEST: passed/failed/skipped/deselected counts
FAILURES: root cause and repair status
MANUAL ONLY: exact remaining operator paths
PRESERVATION: unrelated-file hash result
ROLLBACK: exact safe command
NEXT ACTION: only if no code-side blocker remains
```
