# Qubit Office — plan audit and corrections v10

**Status:** architecture/implementation audit of the previous complete plan + Perplexity reviews.
**Conclusion:** the plan was not executable as written. Corrections are now locked into v11 of
`QUBIT-OFFICE-COMPLETE-PLAN.md`.

## Confirmed problems

| Bug | Severity | Correction |
|---|---|---|
| Phase 0 is a bottleneck: contracts, licences, scale fixture, renderer benchmark and threat modeling all before runtime | high | Split contracts vs infrastructure; the runtime can start only after a small executable foundation gate, with optional nonblocking discovery. |
| No reference vertical slice proving event log, gateway, runs, chat, memory, approvals, artifacts and recovery together | critical | Add P1-10 reference execution lane. It is mandatory architecture validation, not an MVP scope cut. |
| Phase 1 and Phase 2 duplicate ToolGateway, run/task/lease and execution ownership | high | Phase 1 owns minimal invocation/run contracts. Phase 2 adds adapters behind those same contracts; no second execution engine. |
| Event log underspecified: aggregate boundaries, ordering, idempotency, retention, schema evolution, projections | critical | `EventEnvelope` schema: event id, aggregate id, sequence, type, correlation, causation, idempotency, tenant, actor, timestamps, payload hash, policy/model versions, retention/tombstone metadata. |
| SQLite durability conflicts with multi-agent/multi-worker claims | high | Initial topology = one host/process, bounded concurrency, SQLite WAL, real lock tests. Multi-worker/HA triggers a Temporal/Postgres migration with compatibility tests. |
| Unknown handling is not operational | critical | State machine `prepared/sent/acked/unknown/compensated/failed`; provider keys, reconciliation owner, unknown/dead-letter evidence; no success until reconciliation. |
| Approval can be bypassed by composite or changed tools | critical | Bind normalized executable intent, full parameters, target and credential; reauthorize at execution; prohibit hidden side effects; verify postconditions. |
| MCP requirements are incomplete | critical | OAuth 2.1+PKCE S256, exact issuer/audience, secure storage, narrow scopes, no token passthrough, short-lived rotation, schema hashes, per-server scopes, output is untrusted. |
| Memory treated as an adapter, not a security-sensitive subsystem | critical | Memory admission policy, confidence/sensitivity/expiry/provenance, quarantine, poisoned-write and cross-scope retrieval tests. Working context != durable memory. |
| Chat and memory ordering diverge | high | Per-thread sequence/read-your-writes; context manifest with memory versions; stale manifest blocks consequential actions. |
| Principal/delegation semantics incomplete | critical | Record actor/principal/beneficiary/delegator, capability subset, expiry and resource limits for every call; handoffs cannot widen authority. |
| One-writer rule is not enforceable by itself | high | Resource leases + commit API + immutable artifact versions + conflict detection + merge authority + filesystem mapping; no direct writes to authoritative records. |
| Business validation appears too late | critical | Move one provider sandbox workflow into the reference lane; other businesses follow that workflow contract. |
| Trading lacks full safety boundary | critical | Only read-only analytics + deterministic backtest + paper/testnet first. No live until separate readiness checks, owner sign-off, all risk tests. Out-of-band kill switch independent of agents. |
| Full-campus data risks false progress | medium | Templates/synthetic fixtures separate from tenant business data. Generate campus deterministically from versioned templates; never pretend those agents operate. |
| Accessibility left to release | high | Every panel/canvas ships with keyboard semantics, focus, labels, reduced motion and semantic DOM equivalent from first implementation. |
| No executable artifact registry / DAG | critical | Every task has input paths, output paths, owner, reviewer, predecessors, acceptance command, evidence path, negative tests and merge gate. |
| No owner/approver matrix | critical | Add RACI: owner/integration lead, platform, security, privacy, licensing, accessibility, business, operations, QA, release. |
| No fake-completion oracle | high | Outcome-state algebra: `pending/sent/unknown/compensated/failed/completed`. Success requires authoritative artifact/receipt; emitted UI state can never produce completion. |
| Missing operator ownership | high | Name who approves, resolves unknown, responds to incidents, stops trading and performs restore. Escalations and timeouts are required. |
| Model/provider governance is missing | high | Model/prompt/policy/grant/tool/schema/connector versions pinned per run; material change invalidates approval. Canary/rollback evals. |
| Data classification/retention is missing | critical | Classes for secrets, personal data, finance, prompts, memory, traces, artifacts and backups; retention/deletion/export/legal-hold semantics. |
| Notification/search/delivery incomplete | high | Thread sequence, delivery/ack, offline replay, dedupe, read/unread, escalation and ACL-aware search/indexing. |
| Cost/fairness/admission control is missing | high | Per-run reservations and tenant quotas for model/tool/concurrency/storage/network; provider/provider-diversity fallback and circuit breakers. |
| Trading data/time authority missing | high | Trusted clock, skew handling, immutable market snapshots, deterministic replay, provider data provenance; stale feed stop. |

## Additional required corrections

- **Reuse is a merge gate, not prose.** Each dependency has URL, SHA, license SPDX and file hash,
  notices, permitted use, reviewer and approval date. CI refuses unapproved dependencies/assets.
- **No disabled approval safety.** Exact normalized intent, credential, parameters and expiry are
  checked again at execution; composite tools cannot hide sub-actions.
- **Mandatory negative tests.** Every gate needs a test ID, failure assertion, runbook and owner.
  “Pass” is a command + assertions + artifact manifest, not prose.
- **Continuous gates.** OWASP/MCP/WCAG/OTel/security tests run during implementation; Phase 8 is
  scale/independent verification, not the first time security is discussed.
- **Status labels.** A current seed/record/projection is not an operational agent, business, room or
  floor. Only receipt-backed execution creates operational state.
- **No disabled reuse of unlicensed code.** agents-office PolyForm is concepts only; external repos
  without LICENSE are concepts only. URL/commit/licence verified before reuse.
- **Trading is a separate readiness lane.** Full department data/rooms exist, but live trading can
  never start from a plan-only document. Research/backtest/paper adapters first.

## Authority for v11

- The detailed audit above is a historical problem list.
- `docs/QUBIT-OFFICE-COMPLETE-PLAN.md` v11 is now the only phase/merge authority.
- `docs/QUBIT-OFFICE-MANDATORY-CHECKLIST.md` remains the mandatory security/accessibility/ops test list.
- `docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md` remains the domain record authority.
- `docs/QUBIT-OFFICE-RESEARCH-GAPS.md` remains the reference for secondary business ideas.
