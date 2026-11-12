# Qubit Office — mandatory 100% checklist

**Status:** mandatory release/implementation gate. A row is not PASS until evidence exists.
**Rule:** no release/`VERIFIED` claim while an applicable row is FAIL, UNTESTED, or waived without
owner sign-off. “Obligatory” means it must be designed, implemented, tested and evidenced.

Sources reviewed: OWASP Top 10 for Agentic Applications 2026, OWASP AI Agent Security and MCP
Security Cheat Sheets, MCP Authorization Security Considerations, WCAG 2.2, OpenTelemetry GenAI
semantic conventions (Development), Temporal durable-agent guidance, plus local plan review.

---

## 1. OWASP Agentic Top 10 — mandatory mapping

| ID | Threat | Required control | Gate |
|---|---|---|---|
| ASI01 | Agent Goal Hijack | Locked system prompts/goals; intent capsule binding declared goal/constraints/context; runtime intent validation; deviation detection; approval for goal-changing/high-impact actions | injection/goal-override red team; goal-change audit; prompt/config versioned |
| ASI02 | Tool Misuse & Exploitation | Least Agency + least privilege; per-tool scopes/rate/egress; action-level auth; dry-run/diff preview; semantic tool-name/version validation; budgets; sandbox | unauthorized tool denied even when model insists; tool-chain limit; full schema/args shown |
| ASI03 | Identity & Privilege Abuse | Stable human/agent/service/workflow identities; RBAC+ABAC; short-lived scoped tokens; no wildcard; step-up auth; revocation | token forbidden cross-audience; revoked token fails immediately; privilege escalation test |
| ASI04 | Agentic Supply Chain | Signed/pinned dependencies, MCP servers, models, plugins; SBOM/AIBOM; provenance; vendor risk registry; review on change | tampered package/server/model blocked; SBOM/dependency scan |
| ASI05 | Unexpected Code Execution | Ephemeral sandbox; no host socket; read-only base; args not shell; resource/egress limits; artifact scanning | escape/host-socket/metadata tests; command injection tests |
| ASI06 | Memory & Context Poisoning | Treat retrieved/memory/tool content as untrusted; provenance/trust; write validation; isolation; retention/delete; cryptographic/audit integrity | poisoned memory test; cross-scope retrieval denied; deletion/correction works |
| ASI07 | Insecure Inter-Agent Communication | A2A trust boundaries; authenticated/signed messages; typed schemas; recipient ACL; no privilege escalation; replay protection | spoofed/replayed/overscoped agent message refused |
| ASI08 | Cascading Failures | Circuit breakers; retry/loop/handoff/depth caps; isolation; dead-letter owners; budget/backpressure; dependency degradation | runaway loop/retry storm contained; dead-letter owned; one tenant cannot starve others |
| ASI09 | Human-Agent Trust Exploitation | Exact payload/evidence preview; no fake success; uncertainty/unknown states; approval payload hash/expiry; anti-fatigue/anti-confusion; status derives from durable events | approve A/execute B fails; fabricated completion blocked; stale approval fails |
| ASI10 | Rogue Agents | Behavioral baselines/anomaly detection; kill switch; credential/lease revocation; sandbox termination; quarantine; incident forensics | kill switch stops scheduler/gateway/leases/egress; rogue agent quarantined |

**Obligatory:** every threat must have at least one automated test and one incident/runbook path.

---

## 2. Identity, authorization and secrets

- [ ] Human IdP/SSO/MFA/passkeys; local owner auth for personal mode.
- [ ] Separate principals: owner, tenant, business/entity, agent, run, task, workspace, tool lease,
  MCP server, connector.
- [ ] RBAC + ABAC; deny-by-default; resource/action/data/environment/expiry; deny wins.
- [ ] Short-lived workload/capability leases; audience-bound tokens; no token passthrough.
- [ ] Step-up auth for payments, credentials, destructive/privilege changes.
- [ ] Immediate revocation of sessions, leases, tokens, secret bindings.
- [ ] Secrets manager; no secrets in prompts/logs/memory/workspaces/artifacts/commits.
- [ ] Secret scanning + canary tests.

Gate: cross-tenant/role escalation, stale token, revoked lease and secret-canary tests all fail closed.

---

## 3. MCP — mandatory

- [ ] OAuth 2.1 + PKCE `S256`; verify `code_challenge_methods_supported` before proceeding.
- [ ] RFC 8707 `resource` parameter; MCP server validates token audience; no token passthrough.
- [ ] Exact registered redirect URIs; `state` validation; CSRF protection.
- [ ] HTTPS endpoints; localhost default binding; validate `Origin`/`Host`.
- [ ] Secure OS token storage; no plaintext tokens in config/logs.
- [ ] Allowlist signed/version-pinned servers; per-server scoped credentials.
- [ ] Hash the **complete** tool definition (`inputSchema`, `outputSchema`, annotations), not just
  description; review/human-approve changes.
- [ ] Strict JSON Schema (`additionalProperties:false`, patterns); no unrestricted shell/paths.
- [ ] Treat tool output as untrusted; strip/label/limit; block SSRF/private addresses.
- [ ] Human approves server/tool authorization; no auto-approve; show full args, not summary.
- [ ] Rate/quotas/timeouts per server/tool; isolate sensitive servers.

Gate: tool-poisoning/rug-pull, schema-flip, cross-server shadowing, token-replay, SSRF and
oversized-output tests.

---

## 4. Sandbox and code execution

- [ ] Ephemeral unprivileged sandbox per run/task.
- [ ] Read-only base; isolated workspace; no host socket/privileged access.
- [ ] CPU/memory/process/disk/time limits.
- [ ] Filesystem allowlist/path canonicalization; no arbitrary home/root.
- [ ] Deny network by default; proxy allowlist; block metadata/private IPs.
- [ ] Argument allowlists; no shell interpolation; generated-code review where needed.
- [ ] Artifact malware scan; provenance; quarantine; destruct/temporary retention.
- [ ] Context-loss/worker-crash recovery.

Gate: escape, path traversal, shell injection, metadata/SSRF and malware tests.

---

## 5. Human approval and high-impact actions

- [ ] Approval is a durable workflow state, not a chat message.
- [ ] Exact payload hash + actor/tool/target/params/timestamp/expiry/policy revision.
- [ ] Show evidence, cost, reversibility, side effects, affected records.
- [ ] No approval by mutable draft reference; any change invalidates.
- [ ] Approve/reject/edit/delegate/escalate/pause/stop.
- [ ] Dual control for payments, contracts, credentials, legal/destructive changes.
- [ ] Approval fatigue/queue ownership/expiry/escalation.
- [ ] No auto-approve from model output; UI cannot be bypassed by LLM.

Gate: payload-confusion, stale approval, replay, delegated approval and queue-timeout tests.

---

## 6. Memory and context

- [ ] Event log authoritative; Obsidian projection; OpenViking adapter.
- [ ] Scopes: agent/session/task/department/office/tenant; read/write/append/retention.
- [ ] Provenance, confidence, source, visibility, conflict/supersession.
- [ ] Treat retrieved content as untrusted; label data vs instructions.
- [ ] Write validation/quarantine; no silent overwrites of decisions/policy.
- [ ] Deletion/export/legal hold; stale-fact invalidation; rebuild projections.
- [ ] Cross-scope retrieval denied unless explicitly authorized.

Gate: poisoning, cross-scope leak, stale/deleted memory, rebuild and deletion tests.

---

## 7. Multi-agent coordination

- [ ] A2A authenticated/signed typed messages; no natural-language authority transfer.
- [ ] One owner per task/resource/file; single-writer leases; append-only fragments.
- [ ] Max concurrency/wave, max handoffs, max depth, max retries, no-progress detection.
- [ ] Independent verifier; verifier cannot be the same orchestrator/prompt.
- [ ] Circuit breakers, dead-letter owner, compensation/rollback.
- [ ] Agent cannot edit checks/evals/policy/gates.

Gate: shared-file overwrite, handoff loop, role-overlap, verifier collusion, gate-tampering tests.

---

## 8. Accessibility — WCAG 2.2 AA mandatory

- [ ] All functionality keyboard operable; no keyboard traps; visible focus.
- [ ] Character-key shortcuts can be turned off/remapped/focus-only.
- [ ] Text/meaningful UI contrast; non-color status.
- [ ] Target size and spacing meet WCAG 2.5.8.
- [ ] Reflow/zoom 200%+ without loss.
- [ ] Reduced-motion preference; disable camera shake/flash/particles; preserve critical cues.
- [ ] Screen-reader names/roles/states/landmarks; semantic DOM mirror for canvas entities.
- [ ] Canvas not the only interface; text/list equivalent for floors/rooms/agents.
- [ ] Test with keyboard-only, screen reader (NVDA/VoiceOver/TalkBack), zoom, high contrast,
  reduced motion, muted audio and touch.

Gate: automated axe + manual keyboard/screen-reader review; no color-only state.

---

## 9. Observability and evaluation — mandatory

- [ ] One trace per run: `invoke_agent`/`invoke_workflow`, `chat`, `execute_tool`, retrieval,
  approval, sandbox, external receipt, reconciliation.
- [ ] IDs: tenant, owner, agent, run, task, session, trace, provider request, artifact, approval.
- [ ] Metrics: duration, tokens, cost, tool errors, retries, queue age, unknown outcomes.
- [ ] Content capture OFF by default; external-store references for flagged runs; redact before export.
- [ ] Eval event linked to judged span: `gen_ai.evaluation.result`.
- [ ] Offline + online evals; deterministic checks, LLM judge, human calibration.
- [ ] Sampling: all errors/timeouts/handoffs/failed evals/outliers; small baseline sample.
- [ ] Per-tool schema/eval canary in CI.
- [ ] Dashboards/alerts/runbooks/kill switch.

Gate: one trace explains a real run end-to-end; eval regression blocks release; no secret/PII in telemetry.

---

## 10. Durable execution and recovery

- [ ] Temporal workflows/activities; deterministic workflow code.
- [ ] One external effect per activity; idempotency key; provider receipt.
- [ ] Explicit timeouts/retry policy; no unlimited retries.
- [ ] Unknown outcome state + reconciliation queue.
- [ ] Checkpoint/lease/fencing/heartbeat; restart resumes.
- [ ] Cancel revokes leases/tokens and records unknowns.
- [ ] Save/restore verified; RPO/RTO; restore drills.

Gate: crash-after-effect, duplicate webhook, timeout, lease expiry, revoked run and restore tests.

---

## 11. Data, privacy, legal and licensing

- [ ] Tenant isolation in DB/storage/queue/cache/vector/backups.
- [ ] Encryption in transit/at rest; PII classification/masking/minimization.
- [ ] Consent/opt-out/suppression; retention/deletion/export/legal hold.
- [ ] Jurisdiction-aware rules; tax/marketplace obligations.
- [ ] Contracts/obligations/renewals; human approval for legal commitments.
- [ ] Asset/licence ledger; paid art local-only; credit/attribution.
- [ ] AIBOM/SBOM; vendor/model risk registry.
- [ ] Backup exclusion/leakage scans; safe shutdown/exit.

Gate: data-deletion/export, tenant-boundary, paid-art leakage, licence/provenance tests.

---

## 12. Performance, scale and device gates

- [ ] 100-floor/3,075-desk/1,000-agent scale fixture before tower art.
- [ ] One active floor; chunk streaming; LRU atlases/chunks; active/dormant tiers.
- [ ] P95 frame 16.7 ms desktop / 33.3 ms low tier.
- [ ] Active agents ≤40 default; visible avatars ≤80; pathfinding ≤2 ms avg/8 ms P95.
- [ ] Bundle/texture/heap/event budgets from `QUBIT-OFFICE-IMPROVEMENTS-2026.md`.
- [ ] WebGL context-loss recovery; dirty rendering; device tiers.
- [ ] Reconnect ≤5 s; restore 1,000-agent state ≤60 s staging.
- [ ] Accessibility works at 30 FPS; nonessential FX disableable.

Gate: device matrix + scale fixtures + context-loss/reconnect/restore tests.

---

## 13. Release gate

A release is blocked unless:

1. Every applicable row above is PASS with linked evidence.
2. No open critical/high security finding.
3. Restore drill passes.
4. One end-to-end business run has receipts/reconciliation.
5. Accessibility manual review passes.
6. Full matrix runs once on a frozen SHA.
7. `VERIFIED` is claimed only with owner release-gate evidence.

Waivers require owner sign-off, a named risk, expiry and compensating control; a waiver is not a
PASS.
