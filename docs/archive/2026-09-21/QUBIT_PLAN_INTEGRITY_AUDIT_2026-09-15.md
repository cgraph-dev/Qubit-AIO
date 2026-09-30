# Qubit Plan Integrity Audit — 2026-09-15

Status: **OPEN CORRECTIONS APPLIED TO ACTIVE PLANS; IMPLEMENTATION NOT IMPLIED**

## Why earlier agents repeatedly produced incomplete work

The recurring failures came from gaps between the plans and the evidence gates,
not from one isolated code change:

| Defect | How it produced misleading progress | Required correction |
|---|---|---|
| Source/hash presence counted as product parity | A vendored Hermes file or route map was reported as a working page | Test caller → backend → persistence → visible result for every control |
| Route-level tests only | Pages rendered or returned 200 while chat, achievements, boards, and forms failed | Control-level production-listener, DOM, and user-flow inventory |
| “Byte-identical” mixed with rebrand | Agents could not both preserve upstream bytes and remove upstream names | Preserve a byte-verified vendor baseline; verify Qubit overlays separately |
| Compatibility boundary treated as final runtime | `hermes_compat`/aliases could satisfy narrow tests without activating the fork | Fork must be the real local authority; adapters only delegate to it |
| One voice path read as one fixed vendor/model | Brain4/Ollama and local speech became embedded assumptions | One policy/session path with many explicit adapter profiles |
| Terminal ledger statuses read as full completion | Code-ready labels concealed broken UI and unfinished manual/live flows | Ledger is one gate; functional, visual, packaging, and verifier gates remain separate |
| Uncoordinated parallel edits | Duplicate stores, stale build artifacts, and conflicting configuration survived | Exclusive ownership table, integration owner, per-workstream production proof |
| Full tests run too early and too often | Time/disk was spent on repeated suites before coherent integration | Batch implementation, focused checks, one final integrated full-gate wave |
| No normal-surface branding/path gate | Upstream text and `/hermes/` paths lingered in real usage | Test normal Qubit UI, CLI, service, artifact, route, and log surfaces |
| Stale Brain Upgrade guidance | Obsolete 3B/unsafe instructions conflicted with current requirements | Replace with a versioned optional-Brain4 lifecycle and explicit research policy |

## Governing interpretation

“1:1” means behaviorally complete coverage of the supported **local** upstream
source, delivered through Qubit. It does not mean Qubit product files remain
byte-identical after rebranding and integration. The correct comparison is:

1. immutable, license-preserving upstream vendor baseline with file hashes;
2. an explicit Qubit overlay/patch manifest; and
3. differential, production-caller behavior/control tests.

An upstream online portal or account service absent from pinned local source is
not fabricated as a local feature. Its local client/integration still receives
a truthful workflow and typed unavailable/account-required behavior.

## Required plan updates

The active 2026-09-15 master, provider/voice, and owner amendment documents
are the correction layer. The frozen contract, execution directive, full-parity
contract, native plan/checklist, dashboard plan, unified plans, and VoiceBridge
guides cross-reference them. Historical reports and frozen checklist evidence
remain historical records; no previous success claim is upgraded by this audit.
