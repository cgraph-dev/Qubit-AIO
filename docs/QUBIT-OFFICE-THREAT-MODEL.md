# Qubit Office — threat model

**Status:** Phase 0 baseline. Not a security certification, not `VERIFIED`.
**Assumption:** personal/local-first deployment now; future multi-entity and invite-only World.

## 1. Assets

- Owner identity, sessions, passkeys/tokens, recovery paths.
- Business records, customer/lead data, contracts, financial/ledger data, tax data.
- Agent credentials, OAuth tokens, API keys, MCP credentials, Etsy/social/payment tokens.
- Agent workspaces, code worktrees, artifacts, prompts, checkpoints, memory (Obsidian/OpenViking).
- Model/provider context, traces, logs, backups.
- Paid/licensed art, character/portrait assets, generated atlases.
- Availability of 24/7 workflows, approvals, schedules and external integrations.

## 2. Actors

- Owner/operator (human).
- Agents (main, delegated, subagents, workflows).
- Service accounts/jobs/schedulers.
- External providers: models, MCP servers, Etsy/social/CRM/payment/email.
- Untrusted content: web pages, files, emails, marketplace listings, user uploads, tool output.
- Malicious local process or stolen OS account.
- Supply-chain attacker (package/model/MCP/plugin/container).
- Insider/admin with deployment or backup access.
- Future tenant/user/visitor.

## 3. Trust boundaries

1. Human/browser ↔ Office UI ↔ API.
2. Office/renderer ↔ Business OS/event log.
3. Agent ↔ model provider.
4. Agent ↔ ToolGateway ↔ external tool/API/MCP.
5. Agent ↔ workspace/files/worktree.
6. Agent ↔ memory (Obsidian/OpenViking).
7. Tenant ↔ tenant.
8. Local host ↔ sandbox/container.
9. Local host ↔ backups/export.
10. Paid/local art ↔ public bundle/export.
11. Approval/owner interface ↔ executing agent.
12. Provider/model version ↔ recorded run.

## 4. Threats and controls

| Threat | Control | Required test |
|---|---|---|
| Prompt injection from web/file/email/MCP/memory | Treat all as untrusted data; separate instructions; validate action outside model; provenance labels; approval for sensitive changes | Direct/indirect/multilingual/encoded injection corpus |
| Tool/MCP poisoning or schema drift | Allowlist signed/version-pinned servers; snapshot schemas/descriptions; tool-level allow/deny; health/version diff | Malicious schema/description, changed tool, poisoned result |
| Secret leakage into prompts/logs/memory/workspaces/artifacts | Vault refs only; gateway injects per-tool short-lived tokens; redaction/scanning; rotation/revocation | Secret canary in prompt/file/log/memory/commit; revoke mid-run |
| Sandbox escape / host compromise | Ephemeral unprivileged sandbox; read-only base; no host sockets; resource limits; metadata blocking; restricted mounts | Escape attempts, privileged calls, host socket/metadata access |
| Network exfiltration / SSRF / spam / unauthorized purchase | Default-deny egress; proxy allowlist; private-address blocking; per-run identity; spend caps | SSRF, redirect to metadata, exfil payload, blocked domain |
| Cross-tenant/room/department leakage | Tenant-scoped DB/storage/queue/cache/vector/backups; ACL on every read; separate browser profiles | Cross-tenant retrieval, cache, log, backup and trace tests |
| Approval confusion / TOCTOU | Bind approval to canonical payload hash, recipient, amount, content, destination, policy revision, expiry; invalidate on change | Approve payload A, execute payload B; stale approval; changed price/recipient |
| Replay / duplicate side effect | Idempotency keys; provider receipts; unknown state; reconciliation; nonce/expiry | Timeout after commit, duplicate webhook, retry, replay approval |
| Runaway cost/loop/recursive delegation | Per-run/agent/tenant budgets; step/recursion/concurrency/time/token/tool limits; circuit breakers; kill switch | Budget exhaustion, recursion, provider loop, anomalous spend |
| Agent impersonation / wrong attribution | Agent/security principal separate from display name/desk/avatar; signed audit; session owner | Display-name collision, desk move, delegated child |
| Malicious/compromised provider or model update | Version pinning; canary/evals; provider fallback policy; data residency; rollback | Provider change, unsafe tool-call distribution, wrong region |
| Data poisoning / stale knowledge | Provenance/trust; source freshness; retrieval ACL; conflict display; deletion/correction; re-index | Poisoned note/doc/vector, stale policy, conflicting facts |
| Office UI invents truth | Renderer consumes authoritative records; no completion without receipt; explicit unknown/error | Empty/stale/tampered renderer vs API |
| Paid-art/license leakage | Asset ledger; fail-closed builder; local-only paths; scan bundles/caches/screenshots/backups/exports | Paid asset in public bundle/export/screenshot/cache |
| Backup/restore failure | Encrypted immutable backups; tenant restore; RPO/RTO; restore drills | Restore tenant, indexes, workflows, audit chain |
| Supply-chain compromise | Pin/sign dependencies/images/MCP; SBOM/AIBOM; scan; protected CI; provenance | Tampered package/image/plugin; dependency change |
| Insider/admin abuse | Separate duties; audit; approval for privileged change; kill switch; backup access control | Admin deploy/approve/erase conflict-of-interest test |
| Local PC compromise | Disk encryption; separate OS account; hardware-backed secrets; locked browser profile; endpoint protection; remote revoke | Stolen home dir/token/backup; remote revoke |
| Denial of service / context flooding | Tool output size limits; queue quotas; backpressure; timeouts; rate limits | Oversized output, queue flood, provider quota exhaustion |
| Time/calendar abuse | Timezone-aware schedules; recurring-obligation confirmation; replay protection | DST boundary, duplicate schedule, wrong-time send |
| Asset/IP misuse | Rights/provenance per asset; blocked categories; takedown/appeal; audience restrictions | Prohibited content/asset use, missing credit |
| Unrecoverable bad automation | Dry-run/preview; approval; rollback/compensation; manual SOP; export/cancel | Bad publish/email/price change and recovery |

## 5. Residual risks at Phase 0

- No live identity provider/SSO yet; local owner auth is the current boundary.
- No tenant isolation implementation yet.
- No sandbox/secrets manager yet.
- No durable workflow engine yet.
- No tamper-evident audit chain yet.
- No backup/restore drills yet.
- No real external connectors; dry-run only.
- Paid-art leakage scans are not implemented.

## 6. Security gates before real external actions

1. Threat model approved and threat tests exist.
2. A secret never enters prompt/log/memory/workspace/artifact in tests.
3. Unauthorized capability call is denied at the gateway.
4. Sandbox escape tests fail closed.
5. Cross-tenant/room read tests fail closed.
6. Approval payload-confusion test fails closed.
7. Duplicate side effect is prevented or reconciled.
8. Kill switch stops scheduler, gateway calls, leases and egress.
9. Backup restore drill passes.
10. Audit trail is exportable and tamper-evident.
