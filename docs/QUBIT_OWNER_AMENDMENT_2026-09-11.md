# Owner Amendment 2026-09-11 — Hermes frontend vendoring permitted

Status: **APPROVED by owner 2026-09-11. Append-only; all frozen requirements remain in force.**

The owner approves changing the frozen dashboard contract as follows:

1. The pinned Hermes frontend source may be vendored/adapted because its license permits this (MIT, Copyright (c) 2025 Nous Research; license text in `THIRD_PARTY_NOTICES.md`).
2. React/Vite/Node frontend dependencies are permitted for the dashboard.
3. The Hermes frontend remains the functional and structural baseline.
4. Qubit may apply documented branding and backend-integration patches after parity.
5. Every upstream file must have a recorded source revision, hash, and patch reason.
6. Hermes legal notices and attribution must remain intact.
7. OpenClaw pages and capabilities must be added without removing Hermes functionality.
8. CLI, dashboard, and voice must use shared Qubit backend operations.
9. Unsafe upstream actions must remain behind Qubit permissions, confirmation, audit, and emergency-stop controls.
10. The agent may update the frozen plan/checklist only to record this approved amendment, never to hide missing work.

Required result: Hermes frontend source and workflows preserved 1:1 as the baseline,
OpenClaw capabilities added, then the complete product rebranded as Qubit.

Implementation record:
- Pinned Hermes revision: `2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`).
- Pinned OpenClaw revision: `3928bad9badfcb6c7d140530435e806fb8092190` (tag `v2026.9.2`).
- Vendored baseline: `frontend/hermes/` ← upstream `web/` (byte-identical; overlay integration in `frontend/hermes-qubit/`).
- Source comparison: `docs/HERMES_SOURCE_COMPARISON.json` (upstream hash, Qubit hash, patch, reason per file).
- Licensing: `docs/UPSTREAM_REUSE.md`, `THIRD_PARTY_NOTICES.md` (MIT attribution retained).
