# Qubit Hermes-backend cutover — staged migration

Status: **owner-authorized staged migration** (owner directive 2026-09-12).
This document records each stage, the exact behavior change, the affected
contract tests, and the rollback. No frozen requirement is changed.

## Goal

The Qubit-owned vendored Hermes backend (`qubit_hermes/hermes_cli/web_server.py`)
is the single backend authority. The Qubit `WebUI` HTTP server remains the ONLY
listener (`voicebridge/webui.py`); the fork is invoked in-process through
`voicebridge/hermes_asgi.py`. Qubit permissions, confirmations, audit,
redaction, cancellation, rollback, resource limits, and emergency stop remain in
force for every operation.

## Migration stages

| Stage | Change | Default | Affected contract tests | Rollback |
|---|---|---|---|---|
| 1 (this commit) | Add `voicebridge/hermes_asgi.py` (in-process ASGI bridge) and a `webui.hermes_backend` flag. When enabled, **safe GET/HEAD reads** are served by the fork; writes and Qubit-gated surfaces stay on the compat path. 404/405 from the fork fall back to compat. | **off** | none (default off; behavior unchanged) | set `webui.hermes_backend: false`, or revert this commit |
| 2 | Flip `webui.hermes_backend` default to **on**; serve all safe reads from the fork. Qubit-owned `/api/dashboard*`, `/api/qubit*`, `/api/ops*` reads, all sensitive prefixes, and **every route in `hermes_compat.GATED`** are never proxied; writes always take the Qubit gated path. The bridge sends the fork's own session token for authenticated fork routes and proxies only paths declared in the fork's OpenAPI. | **on** | `tests/test_hermes_cutover8u.py` (gate policy, fork-served reads, sensitive/write non-proxy), `tests/test_hermes_backend8u.py`, `tests/test_webui.py`, dashboard suites | set `webui.hermes_backend: false` or revert this commit |
| 2b | **Implemented.** `voicebridge/hermes_egress_guard.py` installs a deny-by-default outbound guard scoped by a `contextvar` set only while the ASGI bridge invokes the fork. Non-loopback connects/resolves inside a fork request raise `EgressBlocked` and are audited (`hermes.egress.denied`); Qubit's own egress is unaffected. `hermes_asgi.gated_path` also derives its no-proxy set from `hermes_compat.GATED`, so a new gate cannot become a bypass. | on | guard test in `tests/test_hermes_cutover8u.py` (external denied + audited, loopback allowed, flag resets) | guard is inert outside fork scope |
| 3 | Route fork **writes/mutations** through Qubit permission/confirmation/audit middleware before execution. | on | new middleware tests; compat write tests updated where the fork now performs the write | flag or revert |
| 4 | Route Qubit CLI and VoiceBridge operations at the same backend endpoints (single operation path). | on | parity suites | flag or revert |
| 5 | Package backend + frontend/runtime deps; clean isolated-install serve proof. | — | packaging gate | rebuild from prior commit |

## Stage 2 blocker (resolved)

Enabling the fork backend by default previously made the dashboard perform
**outbound provider calls** during reads (observed: `Auxiliary: marking
openrouter/nous unhealthy`, Copilot token exchange, unclosed `ssl.SSLSocket`
`ResourceWarning` under `-W error`). Resolved by the Stage 2b fail-closed
egress guard: fork-context outbound connections are refused before the syscall
and audited, so no provider token is exchanged and no socket is left open.
Qubit's own provider egress (voice/CLI/agent) runs outside fork scope and is
untouched.

Also applied: the vendored frontend was rebranded for the two product-facing
strings the cutover rendering exposed (`Hermes Flow` -> `Qubit Flow` in
`SessionsPage.tsx`; `Update Hermes`/`Updating Hermes` -> `Qubit` in the English
locale), recorded in `docs/HERMES_SOURCE_COMPARISON.json`.

## Safety policy (all stages)

- Only `GET`/`HEAD` reads are proxied in Stage 1 (`hermes_asgi.safe_to_proxy`).
- Sensitive read prefixes never proxy: `/api/config/raw`, `/api/env`,
  `/api/credentials`, `/api/secrets`, `/api/keys`, `/api/auth`, `/api/oauth`,
  `/api/mcp/oauth`, `/api/portal`, `/api/backup`, `/api/import`, `/api/export`.
  They keep their Qubit typed refusals.
- Every mutating method always takes the Qubit `hermes_compat` gated path.
- The fork app is invoked **without lifespan**, so it cannot spawn gateway/cron
  services or a second listener.
- Dashboard token auth (`_hermes_token_ok`) is enforced before the bridge.

## Rollback (Stage 1)

1. Config only: set `webui.hermes_backend: false` (or remove the key) and
   restart `qubit-voicebridge.service`.
2. Code: `git revert <stage-1-commit>` (removes `voicebridge/hermes_asgi.py`,
   the flag, the migration doc, and the cutover test). No data migration.
3. Runtime deps are unchanged in Stage 1 (httpx and fastapi/uvicorn were
   already declared).

## Proof

`tests/test_hermes_cutover8u.py`: bridge availability + real fork response,
single-listener guarantee, sensitive/write policy, and live `WebUI` behavior
with the flag both off (compat unchanged) and on (fork serves safe reads).
