# Qubit Hermes-backend cutover — staged migration

Status: **owner-authorized staged migration** (owner directive 2026-09-12).
This document records each stage, the exact behavior change, the affected
contract tests, and the rollback. No frozen requirement is changed.

## Goal

The Qubit-owned vendored Hermes backend (`qubit_runtime/qubit_cli/web_server.py`)
is the single backend authority. The Qubit `WebUI` HTTP server remains the ONLY
listener (`voicebridge/webui.py`); the fork is invoked in-process through
`voicebridge/hermes_asgi.py`. Qubit permissions, confirmations, audit,
redaction, cancellation, rollback, resource limits, and emergency stop remain in
force for every operation.

## Migration stages

| Stage | Change | Default | Affected contract tests | Rollback |
|---|---|---|---|---|
| 1 (this commit) | Add `voicebridge/hermes_asgi.py` (in-process ASGI bridge) and a `webui.hermes_backend` flag. When enabled, **safe GET/HEAD reads** are served by the fork; writes and Qubit-gated surfaces stay on the compat path. 404/405 from the fork fall back to compat. | **off** | none (default off; behavior unchanged) | set `webui.hermes_backend: false`, or revert this commit |
| 2 | Flip `webui.hermes_backend` default to **on**; serve all safe reads from the fork. Qubit-owned `/api/dashboard*`, `/api/qubit*`, `/api/ops*` reads, all sensitive prefixes, and **every route in `hermes_compat.GATED`** are never proxied; writes always take the Qubit gated path. The bridge sends the fork's own session token for authenticated fork routes and proxies only paths declared in the fork's OpenAPI. | **on** | `tests/test_qubit_cutover8u.py` (gate policy, fork-served reads, sensitive/write non-proxy), `tests/test_qubit_backend8u.py`, `tests/test_webui.py`, dashboard suites | set `webui.hermes_backend: false` or revert this commit |
| 2b | **Implemented.** `voicebridge/hermes_egress_guard.py` installs a deny-by-default outbound guard scoped by a `contextvar` set only while the ASGI bridge invokes the fork. Non-loopback connects/resolves inside a fork request raise `EgressBlocked` and are audited (`hermes.egress.denied`); Qubit's own egress is unaffected. `hermes_asgi.gated_path` also derives its no-proxy set from `hermes_compat.GATED`, so a new gate cannot become a bypass. | on | guard test in `tests/test_qubit_cutover8u.py` (external denied + audited, loopback allowed, flag resets) | guard is inert outside fork scope |
| 3 | **Implemented (reversible).** Add `hermes_asgi.write_policy` + `safe_write_to_proxy` and the `webui.hermes_writes` flag (default **off**). When on, a mutating request runs the Qubit policy middleware first — one e-stop authority (engaged e-stop → 423 `ESTOP_ENGAGED`), a typed `hermes_compat` gate is never converted into a fork bypass, and every decision is audited on the shared path. Only routes the operator explicitly adds to `hermes_asgi.WRITE_PROXY_ALLOWLIST` (empty by default, fail-closed) are proxied to the fork under the egress guard; everything else falls through to the Qubit compat path unchanged. No second listener/authority. | **off** | `tests/test_qubit_cutover8u.py` (write policy fail-closed + gate/e-stop non-bypass; WebUI middleware proxies only allowlisted writes) | set `webui.hermes_writes: false`, or revert this commit |
| 3b (remaining) | Populate `WRITE_PROXY_ALLOWLIST` route-by-route as each fork write is proven not to diverge from the Qubit session/policy authority; then flip `webui.hermes_writes` on. Until then the default write path is the Qubit compat operations (already real, confirm-gated, audited). | off | per-route parity tests | empty the allowlist or flag off |
| 4 | Route Qubit CLI and VoiceBridge operations at the same backend endpoints (single operation path). | on | parity suites | flag or revert |
| 5 | Package backend + frontend/runtime deps; clean isolated-install serve proof. | — | packaging gate | rebuild from prior commit |

## Stage 2b note — Qubit compat handlers are authoritative

The Stage 1 proxy-first read branch could shadow Qubit's own compat handlers:
because the vendored fork also declares routes such as `/api/status`,
`/api/sessions`, `/api/health`, `/api/fs/*`, `/api/files/*`, and `/auth/*`, a
safe GET at one of those paths was served by the fork before the Qubit compat
layer ran (audit findings `finding.fork_shadow_78_get`,
`finding.fs_confinement_bypass`, `finding.auth_prefix_bypass`,
`finding.config_proxied`).

The corrected rule is: **Qubit's compat dispatcher is authoritative for every
route Qubit implements or gates.** The no-proxy set is derived from the single
Qubit route authority, not from the fork's OpenAPI:

- `hermes_asgi.SENSITIVE_READ_PREFIXES` (secrets/identity/raw config plus the
  `/api/fs`, `/api/files` confinement surfaces) and
  `hermes_asgi.QUBIT_OWNED_PREFIXES` (`/api/dashboard*`, `/api/qubit*`,
  `/api/ops*`, and the Qubit-owned vendored dashboard plugins);
- `hermes_asgi._gated_patterns()`, one pattern per `hermes_compat.GATED` GET/HEAD
  row, so a new gate can never become a cutover bypass; and
- `hermes_asgi._owned_read_patterns()`, one pattern per *implemented* GET/HEAD
  row in `hermes_compat.ROUTES`, so a newly implemented Qubit handler cannot be
  shadowed by a later fork declaration.

`hermes_asgi.safe_to_proxy` therefore returns true only for a non-mutating read
that the fork declares and Qubit neither implements nor gates — e.g.
`/openapi.json`. Sensitive prefixes and every write never proxy.
`webui._hermes_api` checks `gated_path` explicitly before its fork branch, so
the authority holds even if the allow/route tables change underneath it.

Rollback: none required (pure policy, no data migration, no second listener);
the previous behavior is restored by reverting the ingress-confinement change
set.

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
- Every mutating method always takes the Qubit `hermes_compat` gated path,
  unless (Stage 3, `webui.hermes_writes` on) it is explicitly allowlisted:
  then the Qubit `write_policy` middleware runs first and a typed gate or an
  engaged emergency stop still refuses it before the fork is ever invoked.
- `hermes_asgi.WRITE_PROXY_ALLOWLIST` is empty by default, so enabling the flag
  alone never exposes a fork write; the rollout is per-route and reversible.
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

`tests/test_qubit_cutover8u.py`: bridge availability + real fork response,
single-listener guarantee, sensitive/write policy, and live `WebUI` behavior
with the flag both off (compat unchanged) and on (Qubit compat handlers answer
every Qubit-implemented route; only fork-only, non-sensitive reads such as
`/openapi.json` proxy). `test_shadowed_routes_are_qubit_authoritative_not_proxied`
pins the once-shadowed route set as Qubit-owned. Stage 3 adds
`test_write_policy_is_fail_closed_and_never_bypasses_a_gate` and
`test_webui_write_middleware_proxies_only_allowlisted_writes`.

`tests/test_ingress_confinement8u.py` pins the confinement findings and live
`/agent/api/*` ingress; `tests/test_qubit_routes_agent2b_wiring.py` pins the
Qubit front door, real dispatch payloads, and the proxy policy for fork-declared
reads.
