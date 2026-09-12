# Operations — Packet 8T-F: Qubit Agent foundation migration

Status: **IN PROGRESS — cutover and full parity not verified**

The authoritative architecture/status document is
[8TF_SOURCE_OF_TRUTH.md](8TF_SOURCE_OF_TRUTH.md). This runbook supplies the
operational migration and rollback requirements; it does not permit a
supervisor-only implementation to pass.

The owner-directed runtime and test cadence are defined in
[QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md](QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md).
The current status gate is the gap ledger, not the historical close-out
numbers in this runbook.

The phase-by-phase execution plan is
[8TF_COMPLETE_MIGRATION_PLAN.md](8TF_COMPLETE_MIGRATION_PLAN.md).

Packet 8T-F is the migration gate before Packet 8U. It creates a clean
Hermes-derived Qubit Agent foundation from a fresh pinned Hermes checkout,
integrates Qubit VoiceBridge as the only voice/audio path, and integrates the
OpenClaw capabilities marked REUSE or ADAPT without creating competing
authorities.

This packet is not complete if the implementation only launches a Qubit-owned
worker that imitates Hermes, or if it runs Hermes only from a temporary
external installation while the resulting Qubit tree remains unchanged. The
resulting Qubit worktree must prove the actual pinned Hermes implementation is
the runtime being executed.

The user-facing result must be a complete Qubit rebrand. Hermes/OpenClaw names,
colors, fonts, route structure, service labels, CLI presentation, dashboard
presentation, and installer language must not remain as apparent product
identity. Required license, copyright, attribution, provenance, and migration
references are retained and clearly separated from product UI. Blind search and
replace is not acceptable; route/component organization and visual identity
must be coherent and tested as Qubit.

## 1. Canonical identity

- Public product: **Qubit Agent**
- Voice subsystem: **Qubit VoiceBridge**
- Current canonical repository/install identity: `qubit-voicebridge` until a
  separately verified migration changes it
- Canonical CLI launcher: `qubit` (`voicebridge` remains as a documented
  compatibility alias: same parser, same backend; `python -m voicebridge`
  when neither entry point is on `PATH`)
- Shell completions: `qubit.{bash,zsh,fish}` canonical,
  `voicebridge.{bash,zsh,fish}` compat alias (regenerate via
  `scripts/gen_completions.py`; parity test fails on drift)
- Internal Python import compatibility: `voicebridge`
- `openclaw-voice-bridge`: legacy compatibility identity only

The active Qubit checkout is never destructively replaced during preparation.
The migration is rehearsed from a clean worktree with a validated backup,
journal, and reversible rollback.

## 2. Upstream acquisition

Hermes and OpenClaw are acquired fresh from their official repositories into
isolated directories outside the active Qubit checkout and outside the
operator’s existing installations. The agent records:

- official URL and selected release/branch;
- exact commit hash and acquisition timestamp;
- license and copyright files;
- third-party notices and dependency lockfiles;
- models, plugins, browser drivers, and their licenses;
- selected source files/tests and local modifications.

Fresh upstream clones are read-only inputs during preparation. The selected,
licensed Hermes source is then integrated into the new Qubit foundation using
a clean worktree, provenance records, and a reversible migration journal. The
operator’s existing Hermes/OpenClaw installations are never overwritten.

## 3. Authority and runtime contract

Qubit remains authoritative for:

- capability broker, permissions, scopes, confirmations, and grants;
- audit, redaction, resource limits, emergency stop, and rollback;
- voice session, microphone, ASR, Piper/TTS, Right Ctrl, and dashboard voice;
- configuration, state, reports, memory, packaging, and release identity.

The resulting runtime must have one effective agent/session authority, one
voice listener, one TTS path, one event path, one policy path, and one audit
path. Hermes or OpenClaw code may not execute around these boundaries. The
runtime identity must be proven from the actual executable/module/source path,
not from a display label or protocol compatibility test.

## 4. Voice integration acceptance

The foundation must prove, hermetically and then with authorized live checks:

- microphone input reaches the Qubit ASR contract;
- responses use the selected Qubit TTS/Piper path;
- Right Ctrl and dashboard voice use the same session;
- cancellation and barge-in stop the same active work;
- temporary audio is bounded and cleaned;
- duplicate listeners and competing TTS providers are impossible or typed;
- unavailable hardware/model/provider states are truthful and recoverable.

“Hey Qubit” and whole-computer browser context remain later packet features;
8T-F integrates the foundation without implementing Packet 8U.

Voice/CLI/dashboard parity (2026-09-09): the voice registry spans every
CLI command family (`voicebridge.voice_commands.CLI_COVERAGE`, 64/64
families; `qubit voice say` / dashboard `/api/ops` / microphone share one
backend, journal, audit, redaction, and emergency stop). Families without
a voice entry carry a tested reason in `VOICE_IMPOSSIBLE`: passphrase
state bundles (dashboard form/CLI), nested-JSON envelopes
(dashboard form/CLI), opencode coding runs (voice dispatches via the
Hermes runtime instead — intentional, see
`opencode.runner.request_coding_run`), standing permission/power grants
(CLI terminal — voice authorizes single executions, never widens
policy), structured curator proposals and profile manifests (CLI),
desktop-session launches (browser/shortcut), and physical audio hardware
(operator-only).

## 5. Migration and rollback

Migration must preserve user configuration, state, memory, reports, models,
audio selections, browser settings, and service intent. It must reject unsafe
symlinks, traversal, corrupt/newer state, read-only and disk-full failures,
partial/interrupted migration, and conflicting trees. It must never copy
cookies, browser profiles, API keys, tokens, credentials, raw audio, or raw
private page/file text.

Rollback must restore the previous Qubit checkout and runtime without
deleting user data. No `git reset --hard`, `git clean`, broad checkout, or
destructive cleanup is permitted.

Rollback safety note (2026-09-09 operations console): the `/ops` console is
additive — one new module, three new API routes, one new page, new CLI
`lessons` subcommand, and new voice-registry entries. No state schema
changed: `dashboard.*` confirmation records share `approvals.jsonl` and are
ignored by older code (which only settles `voice.*` records); passphrases
are never persisted, so rollback leaves no secret residue. Restoring the
previous checkout disables the console without migrating or deleting user
data.

Rollback safety note (2026-09-10 final parity): this change is additive —
one new module (`voicebridge/runtime_images.py`), canvas tables added with
`CREATE TABLE IF NOT EXISTS` to the existing kanban store, new CLI
subcommands/groups (`runtime`, kanban canvas ops, ghost retry/recover,
opencode retry/recover, computer-use session), new voice entries, and new
dashboard Tier-1/Tier-2 ops. No existing state schema migrated: kanban
canvas tables are created lazily and ignored by older code; runtime receipts
live in a new `state/runtime_images.json` (0o600); ghost/opencode runners
gain no new stores. Restoring the previous checkout disables the new
surfaces without migrating or deleting user data. Rollback leaves no secret
residue (passphrases never persisted; digests only).

## 6. Readiness gate

Packet 8U may begin only after an independent 8T-F result of `VERIFIED` or an
explicitly approved `VERIFIED WITH EXPLICIT LIMITATION`. The final report must
list exact upstream commits, reused/adapted/missing capabilities, files,
tests/counts, warnings, failures, live tests not run, privacy findings,
rollback, and the next safe action.

No 8T-F report may claim a live microphone, speaker, browser, account, model,
timer, clean-machine upgrade, or release-signing result without real evidence.

An 8T-F report must also identify every OpenClaw matrix row as implemented,
external-only, missing, deferred, or rejected. “External-only” is not an
integrated result. If the pinned Hermes implementation is not present in or
directly used by the resulting Qubit foundation, the verdict is REJECTED,
regardless of green tests for a protocol-shaped substitute.
