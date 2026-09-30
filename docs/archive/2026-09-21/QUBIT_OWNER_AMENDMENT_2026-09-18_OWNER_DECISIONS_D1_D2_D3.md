# Owner decisions 2026-09-18 — D1/D2/D3 and OpenClaw revision scope

Status: **OWNER-APPROVED architecture decisions.** This is an additive amendment.
It does not edit, weaken, reorder, or reinterpret any frozen acceptance
contract, checklist, execution directive, or prior owner amendment. All product,
parity, safety, licensing, and completion requirements remain in force.

Provided by the owner as the resolution of the previously recorded D1/D2/D3
blockers (`docs/QUBIT_OPEN_GAP_LEDGER_2026-09-17.md`) and the OpenClaw
undisposed-addition gate.

## D1 — Source-level rebrand and parity (approved)

- The shipped runtime is a source-level Qubit rebrand/adaptation.
- Pristine Hermes and OpenClaw trees remain read-only upstream oracles.
- Byte identity applies to pristine baselines.
- Qubit files may differ because of renaming, branding, adapters, policy,
  VoiceBridge, Brain4, and OpenClaw integration.
- Behavioral, API, CLI, dashboard, voice, persistence, provider, and recovery
  parity must be proven against upstream.
- Upstream names may remain only in licenses, provenance, comparison manifests,
  migration compatibility, and internal audit evidence.
- Normal product source, UI, routes, logs, CLI, and runtime must use
  Qubit/Qubit Labs branding.

Consequence: the frozen differential gate may treat byte-identical vendored
rows through the shipped-runtime provenance model (a deterministic Qubit patch
manifest plus behavioral/vendor comparison), instead of requiring `patched == []`.
The existing `QUBIT_OWNER_AMENDMENT_2026-09-18_SOURCE_REBRAND_PROVENANCE.md` and
the runner's `vendor_behavior` proof implement this model. The final source-level
rename remains a later phase (the owner's first directive for this phase).

## D2 — OpenClaw RPC/WebSocket architecture (approved)

- Implement OpenClaw-compatible RPC and WebSocket capabilities through the
  existing Qubit listener.
- Do not start a second gateway, dashboard, server, or competing authority.
- Reuse Qubit authentication, policy, broker, audit, permissions, confirmation,
  resource limits, cancellation, and e-stop.
- Add adapters under Qubit-owned routes and namespaces.
- Preserve compatible OpenClaw wire behavior while enforcing Qubit safety policy.

Consequence: the existing single-listener WS infrastructure
(`voicebridge/pty_ws.py`) and the in-process OpenClaw-dialect registry
(`voicebridge/gateway/rpc.py`) are the approved foundation. The prior
`WS_SINGLE_PATH` / `GATEWAY_NO_SERVER` refusals become implementation targets,
not terminal dispositions.

## D3 — Configuration and environment binding (approved)

- Bind every supported Hermes/OpenClaw configuration key and environment
  variable to Qubit’s configuration system.
- Each key must have a Qubit name, type, default, validation, persistence,
  CLI/dashboard path, runtime consumer, error behavior, and redaction rule.
- Secret values must use references and must never appear in logs or generated
  artifacts.
- Unsafe or unsupported settings must receive a typed Qubit-safe disposition,
  never be silently ignored.
- Do not expose configuration that bypasses Qubit policy, e-stop, audit,
  sandbox, or authorization.

## OpenClaw revision scope (approved: BOTH)

- Preserve and support the contractual pinned OpenClaw revision
  `3928bad9badfcb6c7d140530435e806fb8092190` (v2026.9.2).
- Audit the latest OpenClaw release and latest default branch separately.
- Implement all compatible newer capabilities in Qubit.
- Record each newer capability as IMPLEMENTED, SAFE_EQUIVALENT, or genuine
  OPERATOR_ONLY.
- Do not silently replace the contractual pin.
- Keep a deterministic upgrade/diff manifest from the contractual pin to the
  latest supported revision.

Observed at the time of this amendment:

| role | tag | commit |
|---|---|---|
| contractual pin | v2026.9.2 | `3928bad9badfcb6c7d140530435e806fb8092190` |
| observed pin | v2026.9.4 | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` |
| latest release | v2026.9.5 | `c396abe66d9d3635113ae33de31a21bd0943d959` |
| latest default branch | main | `135f934db69da6d08e2e77443161ea65e9528af2` |

The deterministic manifest is generated read-only by
`scripts/openclaw_upgrade_manifest.py` into
`docs/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`. It embeds no timestamps and stamps
a delta method IMPLEMENTED only on an exact Qubit RPC-registry match; every
other delta stays OPEN until a real proof is recorded. The contractual pin is
never replaced.

## Scope of this amendment

- No frozen acceptance criterion, safety policy, terminal status, or product
  requirement is changed.
- Pristine-baseline byte identity remains mandatory.
- The rebranded shipped tree is never claimed byte-identical to upstream.
- The final Hermes/Nous → Qubit/Qubit Labs source rename is deferred to the
  post-implementation phase directed by the owner.
