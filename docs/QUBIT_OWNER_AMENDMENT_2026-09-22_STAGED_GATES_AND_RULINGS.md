# Owner Amendment — 2026-09-22

## Staged gates, the `OPERATOR_ONLY` split, and resolution of the three pending rulings

**Status:** PROPOSED — not in force until signed (§8). Until then, the
2026-09-21 contracts and the pending rulings stand unchanged.
**Authority:** Owner amendment. Changing a requirement requires owner sign-off;
this document *is* that sign-off. It amends the 2026-09-21 contracts by content
and governs where it conflicts. It does not delete or edit any frozen record.
**Basis:** `docs/archive/2026-09-22-pre-reset/QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`,
`docs/archive/2026-09-22-pre-reset/QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`,
`docs/archive/2026-09-22-pre-reset/QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md`,
[QUBIT_VENDOR_DELTA_2026-09-22.md](QUBIT_VENDOR_DELTA_2026-09-22.md).

---

## §0 Rationale

The 2026-09-21 gate set is correct in principle — parity is **behavior, not a
200** — and that principle is preserved here. Three properties of the current set
are harming progress:

1. There is no intermediate verdict, so the only reachable "done" state requires
   gates that depend on operator-only inputs (`QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md:20-57,272-278`). The
   daily target is therefore unreachable, and work drifts to proxy metrics.
2. Frozen records contradict each other and the shipped policy/tests, so the
   tree cannot be green without an owner ruling (Ruling 1; Ruling 3).
3. `OPERATOR_ONLY` is formally terminal, so a capability can be "disposed"
   without behaving (Ruling 2). This closed the OpenClaw surface at "0 OPEN"
   while the runtime did not boot.

This amendment fixes those three without lowering the release bar. `VERIFIED`
still requires the full Gate 1–18 matrix and an independent verifier.

---

## §1 Staged gates — add milestone states (amends release-gate §1 and §7)

The three release verdicts (`NOT_READY`, `CODE_READY_OPERATOR_ACTIONS_REMAIN`,
`VERIFIED`) are retained and unchanged. Added alongside them are **milestone
states**, which are implementation-progress markers, not release approvals:

| State | Meaning | Evidence required |
|---|---|---|
| **M1 `BOOT_BASELINE_PASSED`** | The 13 boot-baseline items run end-to-end through the real Qubit runtime. | Each item: exact command + observed output. No mock, no 200, no rendered-only claim. |
| **M2 `CORE_PARITY_PASSED`** | `/chat` and `/qubit-chat` behave on the one Qubit backend: real provider, real tools/skills/plugins/MCP, persisted sessions, gateway status/start/restart, core dashboard controls against the real backend. | Command + observed output per item; live provider session for a real turn. |
| **M3 `FULL_PARITY_PASSED`** | The full Hermes + OpenClaw + voice matrix and dashboard E2E are dispositioned with behavior evidence (deferred from v0.1; see §1.1). | The gate evidence for the full scope. |
| **M4 `RELEASE`** | Gate 1–18 executed, no code-side OPEN, no fabricated artifact, independent verifier reproduced the evidence → verdict `VERIFIED`. | Sections 1–5 of the release-gate contract. |

### §1.1 Scoped first release — "Qubit v0.1, Unified Agent Core"

A `CODE_READY_OPERATOR_ACTIONS_REMAIN` verdict is permitted at **M2** for the
scoped v0.1 deliverable, provided every deferred item is enumerated with the
milestone that owns it and the operator action it needs. v0.1 is:

**In scope (must pass M1 + M2):** Qubit service + gateway start and stay alive;
`/chat` real terminal runtime; `/chat` real provider/model; real tools, skills,
plugins, MCP, sessions; a real prompt→response turn; Hermes-compatible commands;
skills/plugins/MCP execute; sessions/history persist; gateway status/start/
restart; core dashboard controls against the real backend; `/qubit-chat` local
Brain4 + voice on the same backend.

**Deferred to M3+ (explicitly not v0.1 gates):** Hermes latest-upstream delta
closure; OpenClaw `OPERATOR_ONLY_ARCHITECTURE` rows; the full voice
STT/TTS/realtime switching matrix; complete dashboard/control Playwright sweep;
the 0.21.1→0.21.3 vendor sync; Electron desktop build; packaging/clean-install;
byte-parity manifest; independent verifier.

### §1.2 What does not change

Gate 1–18, the evidence rules (raw log + exact command, no fabrication, no
partial-as-full), and the `VERIFIED` forbiddens remain in force for M4. M1/M2 do
not imply `VERIFIED` and must never be reported as one.

---

## §2 Split `OPERATOR_ONLY` (amends capability-parity contract §3 and the manifest policy)

`OPERATOR_ONLY` is replaced by two distinct dispositions:

- **`OPERATOR_ONLY_EXTERNAL`** — the capability needs a genuinely external
  service, credential, device, or process (e.g. GitHub/OAuth device flow, an
  OpenAI Realtime or equivalent transport, paired node devices, systemd/launchd
  process ownership, a hosted portal, a live native-harness relay, a remote
  verdict registry, cloud-worker transports). **Terminal** once the prerequisite
  is named; remains a qualification state, not exercised behavior.
- **`OPERATOR_ONLY_ARCHITECTURE`** — refused only because Qubit intentionally
  runs no corresponding upstream server registry/transport/store (per-job cron
  scratch; model-proposed task-suggestion registry; human-mention inbox; chunked
  skill-upload staging; wake-trigger store). **NOT terminal** until the owner
  signs off on each row. Until signed, these rows count as **OPEN**.

The OpenClaw pinned-surface disposition may not be reported as "0 OPEN" unless
every `OPERATOR_ONLY_ARCHITECTURE` row has an owner signature in §4. The
"434/434 disposed" figure must be restated with the two classes separated.

---

## §3 Ruling 1 — branding: frozen allowlist vs the 2026-09-21 policy

**Conflict:** the frozen allowlist rows 72–75 permit the upstream docs URL and the
`@NousResearch` handle on shipped surfaces; the 2026-09-21 policy §1.2 forbids
upstream names as URLs/labels. Two shipped tests assert opposites
(`test_branding_bundle_scan9u.py` vs `test_branding_leaks_fixes.py`). Evidence:
`QUBIT_OWNER_RULING_REQUEST_2026-09-21.md` §Ruling 1.

**Options:** A — policy wins (de-brand the rendered surface; keep provenance only
in legal/notice and comparison manifests exempted by policy §3.3). B — frozen
allowlist wins (exempt as attribution). C — narrow exemption (URL only as
non-rendered provenance; rendered label neutral). D — defer (mark scans
`KNOWN_CONFLICT`).

**PROPOSED: Option A.** The deep-rebrand goal is the product decision; the MIT
license and upstream attribution remain preserved in
`THIRD_PARTY_NOTICES.md` and provenance manifests, which are out of scope for the
public-surface scan. Rendered DocsPage/kanban labels point to Qubit docs; the
`@NousResearch` share handle is removed.

**Authorized code/test changes under A:**
- `frontend/qubit-web/src/pages/DocsPage.tsx` — replace the upstream
  `UPSTREAM_DOCS_URL` target with the neutral Qubit docs target.
- `frontend/qubit-web/public/plugins/kanban/dist/index.js` and
  `.../hermes-achievements/dist/index.js` — neutral label; drop the handle.
- Regenerate `frontend/qubit-dist`.
- Update `tests/test_branding_leaks_fixes.py` (currently asserts the strings
  present) under this authorization.

**Owner decision:** [ ] A   [ ] B   [ ] C   [ ] D   — Signed: ____________________

---

## §4 Ruling 2 — confirm / reclassify the OpenClaw `OPERATOR_ONLY` rows

**Request:** confirm the 95 `OPERATOR_ONLY` rows as terminal. The two classes are
`OPERATOR_ONLY_EXTERNAL` (terminal) and `OPERATOR_ONLY_ARCHITECTURE` (not
terminal without signature; §2). Evidence:
`QUBIT_OWNER_RULING_REQUEST_2026-09-21.md` §Ruling 2.

**PROPOSED: Option B.** Confirm the external-prerequisite rows as
`OPERATOR_ONLY_EXTERNAL` (terminal). Re-dispose the architecture-gated rows
(named in the brief §11: `cron.scratch.*`, `taskSuggestions.*`, `mentions.*`,
`skills.upload.*`, `voicewake.*`, and the mixed `portal.*`/`talk.*`/`node.*`/
`environments.*`/`agents.*`/`users.*` single-operator rows) as
`OPERATOR_ONLY_ARCHITECTURE` (OPEN until signed). The manifest's `terminal` policy
is amended so `OPEN` is the default for that class.

**Owner decision:** [ ] A (confirm all 95 terminal)   [ ] B (split; proposed)
[ ] C (re-open architecture subset for a code adapter)   — Signed: ____________________

---

## §5 Ruling 3 — the `/` redirect target

**Conflict:** the frozen allowlist and policy §5 say `/` redirects to `/chat`;
the live server (`voicebridge/webui.py`) and `tests/test_qbpv2_rebrand_routes.py`
redirect to `/sessions`. Evidence: `QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`
§Ruling 3.

**Options:** A — change live behavior to `/chat` and reconcile the client
`RootRedirect` and the route test. B — amend policy/allowlist to `/sessions`.

**PROPOSED: Option A.** `/chat` is the product's primary surface (brief §4), and
the frozen record already names it; aligning code to the record avoids editing
frozen text.

**Owner decision:** [ ] A (redirect `/` → `/chat`; proposed)   [ ] B (amend to
`/sessions`)   — Signed: ____________________

---

## §6 Effect on the active brief and other docs (next, not part of signature)

On signature, the following become required follow-ups (not performed by this
amendment itself): update `QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md` to cite
this amendment for the staged states, the `OPERATOR_ONLY` split, and the rulings;
record the resolution in `QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`; regenerate
the OpenClaw manifest with the split; and restate any "0 OPEN"/"disposed" claim
with the two classes separated.

## §7 Non-effects

Nothing here is `VERIFIED`, complete, 100%, or 1:1. This amendment changes gate
structure and dispositions; it is not evidence that any capability works. No
frozen record is edited. No code-side OPEN row is closed by this document.

## §8 Signature

Effective on owner signature and date.

- Owner: ____________________  Date: __________
- Repo / revision at signing: `/home/trick/Projects/Repos/qubit-voicebridge`
  at ____________________
- Rulings 1 / 2 / 3 as signed: ______ / ______ / ______
