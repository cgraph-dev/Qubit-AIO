# OmniRoute opt-in integration — design

Date: 2026-10-11. Owner decisions: full integration, opt-in (never
default-on), provider connections stay user-owned, all platforms
(Linux + macOS + Windows), Approach A (setup-step integration).

## Outcome

`setup run --steps omniroute` provisions a local OmniRoute gateway and
wires it as Qubit's automatic rate-limit fallback. Unprovisioned
installs keep working exactly as today; `/omniroute` degrades to a
setup hint.

## Non-goals

- No default-on behavior. The step runs only when requested.
- No provider connections inside OmniRoute (owner ruling). Setup
  verifies the gateway serves an authed catalog; connecting accounts is
  the operator's job in the OmniRoute dashboard.
- No changes to the inference path or wire behavior.
- No silent auto-upgrade of the gateway package.

## Architecture & components

One new owner module, `voicebridge/omniroute_setup.py`, exposing
table-driven step functions consumed by the existing setup runners
(`SETUP_STEPS` in `voicebridge/lifecycle.py`, install probes in
`voicebridge/product.py`). No new runner, no new dispatch pattern.

- `probe()` — node >= 22, npm reachable, installed `omniroute
  --version` against the pinned floor (3.8.51), port 20128
  free-or-ours, per-platform service state.
- `provision()` — idempotent ordered sub-steps, each safe to re-run:
  install package (pinned) → write `~/.omniroute/.env` → install +
  enable platform service → wait healthy → create gateway API key →
  store `OMNIROUTE_API_KEY` in the runtime `.env` via the existing
  secret path → seed 4 voice profiles via canonical upsert →
  set `fallback_to` on external profiles → append the agent
  `fallback_providers` entry via `load_config` / `save_config` →
  verify (health + authed `/v1/models` non-empty).
- `repair()` — re-runs `provision()`.
- `remove()` — stops + disables the service, drops Qubit-side wiring.
  `~/.omniroute` data is kept unless `--purge-data` is passed; provider
  keys are never deleted silently.
- Platform service backends (same shape, selected by platform):
  systemd user unit (Linux), launchd plist (macOS), scheduled task
  (Windows). Package upgrades via explicit command only.

## Data flow, config & secrets

- Generated admin password + gateway API key are created at provision
  time and written only to `~/.omniroute/.env` (0600) and the Qubit
  runtime `.env` (existing secret-routing path). The key value is shown
  once, then referenced as `env:OMNIROUTE_API_KEY` forever after.
- No secret value lands in `config.yaml`, `voice.yaml`, logs, tests,
  snapshots, or the dashboard page (redaction is a tested invariant).
- Config writes use canonical seams only: `upsert_model_profile`
  (`confirm=True`) for voice profiles; `load_config` / `save_config`
  (atomic, additive) for `providers.*` and `fallback_providers`.
- Failover runtime (seeded, not reimplemented): agent layer
  429/5xx → `fallback_providers[omniroute/auto]` with existing cooldown
  + auto-restore; voice layer `fallback_to` chain in picker +
  `rollback` path; OmniRoute internal 4-tier fallback below that.
- Profiles declare `api_mode: chat_completions`. Provision refuses to
  seed against a gateway older than the pinned floor.
- Fresh-install UX: `/omniroute` probes health; unreachable shows the
  setup command; reachable-but-no-providers shows the connect card.

## Errors, testing, licensing, rollout

- Every sub-step returns a typed result with remediation; failures
  leave prior good state intact and never half-write config (config
  writes happen only after the gateway verifies healthy).
- Tests: provision idempotency against temp homes; secret-redaction
  invariant (no key pattern in snapshots); chain-dedupe (re-provision
  never duplicates). One live Linux E2E (install → serve →
  authed-models); macOS/Windows backends on their lanes. No full suite
  until implementation is frozen.
- Provenance before implementation: record upstream repo + pinned
  commit + MIT license + local changes; THIRD_PARTY_NOTICES entry;
  `BLOCKED — LICENSE REVIEW REQUIRED` on any uncertainty. `LICENSE`
  covers original Qubit material only.
- Rollout: spec → implementation plan → build behind the setup step →
  operator docs (install, key rotation, dashboard password,
  per-platform service commands) → single verification pass.
