# Owner Amendment 2026-09-11 — Qubit-owned Hermes runtime (supersedes native-runtime rule)

Status: **APPROVED by owner 2026-09-11. Append-only; the frozen `Identity
promise` sentence "The normal Qubit runtime must be Qubit-owned. A supervised
external upstream assistant is not completion." is amended by the text below.
All other frozen requirements (backend + CLI + dashboard + voice for every
capability, safety gating, licensing/provenance, no-100%-from-a-tile) remain in
force.**

Owner directive (verbatim):

> Replace the Qubit-native runtime with a Qubit-owned fork/vendor of the exact
> pinned Hermes source.
>
> The Hermes backend, agent loop, CLI, dashboard, sessions, tools, skills,
> models, memory, tasks, reports, and runtime must execute from the Qubit
> source tree. Do not supervise or depend on an external Hermes installation.
>
> Integrate OpenClaw capabilities into this Qubit-owned Hermes foundation.
> Integrate Qubit VoiceBridge so every Hermes, OpenClaw, and Qubit operation is
> available through the shared CLI, dashboard, and voice backend.
>
> The result must be:
>
> Qubit-owned Hermes source
> + complete Hermes functionality
> + OpenClaw capabilities
> + Qubit VoiceBridge controlling everything
> + final Qubit rebrand.
>
> Preserve Qubit permissions, confirmation, audit, redaction, cancellation,
> resource limits, rollback, and emergency stop. Preserve all licenses,
> copyright, attribution, and provenance records.
>
> Do not claim completion until source, CLI, dashboard, voice, OpenClaw,
> packaging, clean install, and full tests prove parity.

## Interpretation (recorded, not a requirement change)

1. The pinned Hermes source (`2237be355906fbe6065ce1815711eee52b2d646e`, tag
   `v2026.9.7`, MIT, Copyright (c) 2025 Nous Research) is vendored into the
   Qubit source tree as a Qubit-owned fork. No external Hermes install is
   required or supervised at runtime.
2. "Qubit-owned fork" means the source lives in this repository, is imported
   and executed in-process by the Qubit launcher, and carries a per-file
   provenance manifest (upstream revision + upstream blob hash + local sha256 +
   patch reason) as required by the 2026-09-11 frontend-vendoring amendment.
3. OpenClaw capabilities (per `docs/AUDIT_OPENCLAW_2026-09-11.md`) are added on
   top of the vendored foundation; OpenClaw source is not copied (behavioral
   adaptation only, per `voicebridge/upstream/ATTRIBUTION.md`).
4. Qubit VoiceBridge is the shared command surface over the union of Hermes,
   OpenClaw, and Qubit operations, with Qubit permissions/confirmation/audit/
   redaction/cancellation/resource-limit/rollback/emergency-stop gating.
5. The final rebrand renames the product surface to Qubit; upstream module
   names, legal notices, and provenance records are preserved.

## Provenance

- Upstream: https://github.com/NousResearch/hermes-agent
- Pin: `2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`)
- License: MIT License, Copyright (c) 2025 Nous Research (text retained in
  `THIRD_PARTY_NOTICES.md` and in the vendored tree's own `LICENSE`).
- Local fork root: `qubit_hermes/` (see `docs/QUBIT_HERMES_VENDOR_MANIFEST.json`).
