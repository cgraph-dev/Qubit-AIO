/**
 * Qubit family admin API client (`oc` = the contractual-pin family surface).
 *
 * Separate admin pages for the three families that the main product pages
 * intentionally do not cover:
 *
 * - `skills-review` — the verified-skill proposals queue. Reads go through
 *   the content half (`content.py`: list / inspect / history-status /
 *   create / events journal); owner-gated writes go through the mutations
 *   half (`skills_extra.py`: apply / reject / quarantine / revise /
 *   update / request-revision / curator pin-unpin-restore), each settled
 *   with the shared staged-confirmation round-trip.
 * - `mcp-calls` — direct tool calls plus resource list/read against the
 *   configured MCP layer (`mcp_app.py`).
 * - `users` — the Qubit single-operator adapter. Qubit has no multi-user
 *   module: reads resolve to the one loopback operator, the persisted
 *   dashboard font/theme store, and the credential/model registries
 *   (`users.py`).
 *
 * Every path lives under `/api/qubit/<family>/<action>` (see `ROUTES`).
 * Operations the backend refuses by policy are NOT called: they are listed
 * in the `DISABLED_*` tables with the exact `operator_prerequisite` string
 * the backend emits, so pages render them disabled-with-reason.
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";

/** Human-confirm callback: the button press IS the confirmation. */
export type ConfirmFn = (detail: string) => boolean | Promise<boolean>;

/** One canonical route in the family admin namespace. */
export interface FamilyRoute {
  family: "skills-review" | "mcp-calls" | "users";
  action: string;
  method: "GET" | "POST" | "PUT" | "DELETE";
  path: string;
}

/**
 * Manifest of every path+method this module calls, under
 * `/api/qubit/<family>/<action>`. No plugins page, no portal page: those
 * families are served by their own product surfaces.
 */
export const ROUTES: FamilyRoute[] = [
  // ── skills-review (reads: content.py half) ──
  { family: "skills-review", action: "proposals.list", method: "GET", path: "/api/qubit/skills-review/proposals" },
  { family: "skills-review", action: "proposals.inspect", method: "GET", path: "/api/qubit/skills-review/proposals/{id}" },
  { family: "skills-review", action: "proposals.historyStatus", method: "GET", path: "/api/qubit/skills-review/history-status" },
  { family: "skills-review", action: "proposals.create", method: "POST", path: "/api/qubit/skills-review/proposals" },
  // ── skills-review (writes: skills_extra.py mutations half, confirm-gated) ──
  { family: "skills-review", action: "proposals.apply", method: "POST", path: "/api/qubit/skills-review/proposals/{id}/apply" },
  { family: "skills-review", action: "proposals.reject", method: "POST", path: "/api/qubit/skills-review/proposals/{id}/reject" },
  { family: "skills-review", action: "proposals.quarantine", method: "POST", path: "/api/qubit/skills-review/proposals/{id}/quarantine" },
  { family: "skills-review", action: "proposals.revise", method: "POST", path: "/api/qubit/skills-review/proposals/{id}/revise" },
  { family: "skills-review", action: "proposals.update", method: "POST", path: "/api/qubit/skills-review/proposals/{id}/update" },
  { family: "skills-review", action: "proposals.requestRevision", method: "POST", path: "/api/qubit/skills-review/proposals/{id}/request-revision" },
  { family: "skills-review", action: "proposals.events.list", method: "GET", path: "/api/qubit/skills-review/events" },
  { family: "skills-review", action: "curator.pin", method: "POST", path: "/api/qubit/skills-review/curator/pin" },
  { family: "skills-review", action: "curator.unpin", method: "POST", path: "/api/qubit/skills-review/curator/unpin" },
  { family: "skills-review", action: "curator.restore", method: "POST", path: "/api/qubit/skills-review/curator/restore" },
  // ── mcp-calls (mcp_app.py) ──
  { family: "mcp-calls", action: "callTool", method: "POST", path: "/api/qubit/mcp-calls/call-tool" },
  { family: "mcp-calls", action: "listResources", method: "GET", path: "/api/qubit/mcp-calls/resources" },
  { family: "mcp-calls", action: "readResource", method: "GET", path: "/api/qubit/mcp-calls/resources/read" },
  // ── users (users.py single-operator adapter) ──
  { family: "users", action: "list", method: "GET", path: "/api/qubit/users/list" },
  { family: "users", action: "self", method: "GET", path: "/api/qubit/users/self" },
  { family: "users", action: "mentionable", method: "GET", path: "/api/qubit/users/mentionable" },
  { family: "users", action: "prefs.get", method: "GET", path: "/api/qubit/users/prefs" },
  { family: "users", action: "prefs.set", method: "PUT", path: "/api/qubit/users/prefs" },
  { family: "users", action: "listAuthLinks", method: "GET", path: "/api/qubit/users/auth-links" },
  { family: "users", action: "listModelAccounts", method: "GET", path: "/api/qubit/users/model-accounts" },
  { family: "users", action: "selectModelAccount", method: "POST", path: "/api/qubit/users/select-model-account" },
  { family: "users", action: "linkAuthProfile", method: "POST", path: "/api/qubit/users/link-auth-profile" },
  { family: "users", action: "unlinkAuthProfile", method: "POST", path: "/api/qubit/users/unlink-auth-profile" },
  { family: "users", action: "authConnect.catalog", method: "GET", path: "/api/qubit/users/auth-connect-catalog" },
];

/** A policy-refused operation: rendered disabled with its exact prerequisite. */
export interface DisabledAction {
  action: string;
  reason: string;
  counterpart: string;
}

/* ------------------------------------------------------------------ */
/*  skills-review                                                       */
/* ------------------------------------------------------------------ */

export interface SkillProposal {
  id?: string;
  skill_id?: string;
  slug?: string;
  name?: string;
  status?: string;
  [key: string]: unknown;
}

export interface ProposalsListResponse {
  state?: string;
  proposals: SkillProposal[];
  count: number;
}

export interface ProposalInspectResponse {
  state?: string;
  proposal: SkillProposal;
}

export interface ProposalEvent {
  sequence: number;
  action: string;
  payload: Record<string, unknown>;
}

export interface ProposalEventsResponse {
  state?: string;
  events: ProposalEvent[];
  count: number;
  journal?: unknown;
}

export function proposalIdOf(p: SkillProposal): string {
  for (const k of ["id", "skill_id", "slug", "name"]) {
    const v = p[k];
    if (typeof v === "string" && v.trim()) return v;
  }
  return "";
}

/** content.py half: list the proposals queue, optional status filter. */
export function listProposals(status?: string): Promise<ProposalsListResponse> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return fetchJSON<ProposalsListResponse>(`/api/qubit/skills-review/proposals${q}`);
}

/** content.py half: inspect one proposal row in full. */
export function inspectProposal(id: string): Promise<ProposalInspectResponse> {
  return fetchJSON<ProposalInspectResponse>(
    `/api/qubit/skills-review/proposals/${encodeURIComponent(id)}`,
  );
}

/** content.py half: hash-chain journal verification status. */
export function proposalsHistoryStatus(): Promise<{ state?: string; journal?: unknown }> {
  return fetchJSON<{ state?: string; journal?: unknown }>(
    "/api/qubit/skills-review/history-status",
  );
}

/** content.py half: stage a reviewed skill draft (shared confirmation journal). */
export function createProposal(
  payload: Record<string, unknown>,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>("/api/qubit/skills-review/proposals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    confirm,
  );
}

function proposalMutation(
  id: string,
  verb: "apply" | "reject" | "quarantine",
  body: Record<string, unknown>,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>(
        `/api/qubit/skills-review/proposals/${encodeURIComponent(id)}/${verb}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true, ...body }),
        },
      ),
    confirm,
  );
}

/** skills_extra.py half: owner-gated verified verdict. */
export function applyProposal(id: string, reason?: string, confirm?: ConfirmFn): Promise<unknown> {
  return proposalMutation(id, "apply", reason ? { reason } : {}, confirm);
}

/** skills_extra.py half: owner-gated refutation. */
export function rejectProposal(id: string, reason?: string, confirm?: ConfirmFn): Promise<unknown> {
  return proposalMutation(id, "reject", reason ? { reason } : {}, confirm);
}

/** skills_extra.py half: canonical quarantine transition. */
export function quarantineProposal(id: string, confirm?: ConfirmFn): Promise<unknown> {
  return proposalMutation(id, "quarantine", {}, confirm);
}

/** skills_extra.py half: stage a reviewed skill revision on the shared journal. */
export function reviseProposal(
  id: string,
  payload: Record<string, unknown>,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>(
        `/api/qubit/skills-review/proposals/${encodeURIComponent(id)}/revise`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true, payload }),
        },
      ),
    confirm,
  );
}

/** skills_extra.py half: stage an updated reviewed manifest on the shared journal. */
export function updateStagedProposal(
  id: string,
  payload: Record<string, unknown>,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>(
        `/api/qubit/skills-review/proposals/${encodeURIComponent(id)}/update`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true, payload }),
        },
      ),
    confirm,
  );
}

/** skills_extra.py half: append free-text revision instructions to the session. */
export function requestProposalRevision(
  id: string,
  session: string,
  instructions: string,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>(
        `/api/qubit/skills-review/proposals/${encodeURIComponent(id)}/request-revision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true, session, instructions, proposalId: id }),
        },
      ),
    confirm,
  );
}

/** skills_extra.py half: the append-only verified-skill journal for a proposal. */
export function listProposalEvents(opts?: {
  proposalId?: string;
  after?: number;
  limit?: number;
}): Promise<ProposalEventsResponse> {
  const q = new URLSearchParams();
  if (opts?.proposalId) q.set("proposalId", opts.proposalId);
  if (opts?.after != null) q.set("after", String(opts.after));
  if (opts?.limit != null) q.set("limit", String(opts.limit));
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<ProposalEventsResponse>(`/api/qubit/skills-review/events${suffix}`);
}

function curatorMutation(
  verb: "pin" | "unpin" | "restore",
  skillId: string,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>(`/api/qubit/skills-review/curator/${verb}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, skill_id: skillId }),
      }),
    confirm,
  );
}

/** skills_extra.py half: owner-gated verified-skill pin. */
export function curatorPin(skillId: string, confirm?: ConfirmFn): Promise<unknown> {
  return curatorMutation("pin", skillId, confirm);
}

/** skills_extra.py half: owner-gated verified-skill unpin. */
export function curatorUnpin(skillId: string, confirm?: ConfirmFn): Promise<unknown> {
  return curatorMutation("unpin", skillId, confirm);
}

/** skills_extra.py half: workshop collection restore/rollback. */
export function curatorRestore(skillId: string, confirm?: ConfirmFn): Promise<unknown> {
  return curatorMutation("restore", skillId, confirm);
}

/**
 * Policy-refused skills operations. Reasons are the exact
 * `operator_prerequisite` strings the backend emits — rendered
 * disabled-with-reason, never called.
 */
export const DISABLED_SKILLS_ACTIONS: DisabledAction[] = [
  {
    action: "skills.upload.begin",
    reason: "a staged chunked upload store/transport for skill archives",
    counterpart:
      "voicebridge.verified_skills.mutations (single-shot reviewed manifest on the shared confirmation journal)",
  },
  {
    action: "skills.upload.chunk",
    reason: "a staged chunked upload store/transport for skill archives",
    counterpart:
      "voicebridge.verified_skills.mutations (single-shot reviewed manifest on the shared confirmation journal)",
  },
  {
    action: "skills.upload.commit",
    reason: "a staged chunked upload store/transport for skill archives",
    counterpart:
      "voicebridge.verified_skills.mutations (single-shot reviewed manifest on the shared confirmation journal)",
  },
  {
    action: "skills.proposals.evaluate",
    reason:
      "an isolated proposal-evaluation model runtime (Qubit runs no self-evaluation path)",
    counterpart:
      "voicebridge.verified_skills.VerifiedSkillStore.apply_verdict (human owner verdict)",
  },
  {
    action: "skills.proposals.historyScan",
    reason:
      "a session-history scan runtime (an isolated model over the transcript) that emits pending proposals",
    counterpart:
      "voicebridge.verified_skills.workshop.SkillWorkshop.author (authored/quarantined proposals)",
  },
  {
    action: "skills.securityVerdicts",
    reason:
      "a reachable skill security-verdict registry/endpoint and network access",
    counterpart:
      "local bundled/verified skill validation (voicebridge.verified_skills._bundled + VerifiedSkillStore)",
  },
];

/* ------------------------------------------------------------------ */
/*  mcp-calls                                                           */
/* ------------------------------------------------------------------ */

export interface McpCallResponse {
  state?: string;
  toolName?: string;
  server?: string;
  code?: string;
  result?: unknown;
  text?: string;
  [key: string]: unknown;
}

export interface McpResourcesResponse {
  state?: string;
  code?: string;
  server?: string;
  resources: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface McpResourceReadResponse {
  state?: string;
  code?: string;
  server?: string;
  uri?: string;
  contents: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

/** mcp_app.py `callTool`: invoke a bound tool (confirm-gated). */
export function callMcpTool(
  opts: { server: string; tool: string; args?: Record<string, unknown> },
  confirm?: ConfirmFn,
): Promise<McpCallResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<McpCallResponse>("/api/qubit/mcp-calls/call-tool", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirm: true,
          server: opts.server,
          toolName: opts.tool,
          args: opts.args ?? {},
        }),
      }),
    confirm,
  );
}

/** mcp_app.py `listResources`: live `resources/list` for a configured server. */
export function listMcpResources(server: string): Promise<McpResourcesResponse> {
  return fetchJSON<McpResourcesResponse>(
    `/api/qubit/mcp-calls/resources?server=${encodeURIComponent(server)}`,
  );
}

/** mcp_app.py `readResource`: live `resources/read` for one URI. */
export function readMcpResource(server: string, uri: string): Promise<McpResourceReadResponse> {
  const q = new URLSearchParams({ server, uri });
  return fetchJSON<McpResourceReadResponse>(
    `/api/qubit/mcp-calls/resources/read?${q.toString()}`,
  );
}

/**
 * Policy-refused MCP operations. Reasons are the exact
 * `operator_prerequisite` strings the backend emits.
 */
export const DISABLED_MCP_ACTIONS: DisabledAction[] = [
  {
    action: "mcp.app.listResourceTemplates",
    reason: "an MCP server/runtime that advertises resource templates",
    counterpart:
      "the MCP tool layer's `list_resources` (qubit_runtime/tools/mcp_tool.py)",
  },
  {
    action: "mcp.app.updateModelContext",
    reason: "an MCP App sandbox runtime/active app view",
    counterpart:
      "voicebridge.plugin_runtime.mcp_doctor (in-process MCP host diagnostics)",
  },
];

/* ------------------------------------------------------------------ */
/*  users (Qubit single-operator adapter)                               */
/* ------------------------------------------------------------------ */

export interface OperatorProfile {
  id: string;
  userId?: string;
  displayName?: string;
  email?: string;
  role?: string;
  orgId?: string;
  provider?: string;
  mode?: string;
  authenticated?: boolean;
  [key: string]: unknown;
}

export interface OperatorPrefs {
  font?: string;
  theme?: string;
  [key: string]: unknown;
}

export interface AuthLinkRow {
  provider?: string;
  [key: string]: unknown;
}

export interface ModelAccountRow {
  id?: string;
  profile_id?: string;
  provider?: string;
  model?: string;
  [key: string]: unknown;
}

export interface AuthConnectCatalogRow {
  provider: string;
  keyRef?: string;
  portalUrl?: string;
  [key: string]: unknown;
}

/** users.py `users.self`: the one canonical loopback operator identity. */
export function getOperatorSelf(): Promise<{ state?: string; profile: OperatorProfile }> {
  return fetchJSON<{ state?: string; profile: OperatorProfile }>("/api/qubit/users/self");
}

/** users.py `users.list`: the single-operator roster (always one row). */
export function listOperators(): Promise<{ state?: string; profiles: OperatorProfile[]; count: number }> {
  return fetchJSON<{ state?: string; profiles: OperatorProfile[]; count: number }>(
    "/api/qubit/users/list",
  );
}

/** users.py `users.mentionable`: the one mentionable operator. */
export function listMentionable(): Promise<{
  state?: string;
  mentionable: OperatorProfile[];
  count: number;
}> {
  return fetchJSON<{ state?: string; mentionable: OperatorProfile[]; count: number }>(
    "/api/qubit/users/mentionable",
  );
}

/** users.py `users.prefs.get`: persisted dashboard font/theme (subset-able). */
export function getOperatorPrefs(keys?: string[]): Promise<{ state?: string; entries: OperatorPrefs }> {
  const q = keys && keys.length ? `?keys=${encodeURIComponent(keys.join(","))}` : "";
  return fetchJSON<{ state?: string; entries: OperatorPrefs }>(`/api/qubit/users/prefs${q}`);
}

/** users.py `users.prefs.set`: font/theme only; other keys come back unsupported. */
export function setOperatorPrefs(entries: { font?: string; theme?: string }): Promise<{
  state?: string;
  applied: OperatorPrefs;
  unsupported?: string[];
}> {
  return fetchJSON<{ state?: string; applied: OperatorPrefs; unsupported?: string[] }>(
    "/api/qubit/users/prefs",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries }),
    },
  );
}

/** users.py `users.listAuthLinks`: credential-reference inventory (names only). */
export function listAuthLinks(): Promise<{
  state?: string;
  links: AuthLinkRow[];
  count: number;
  envReferences?: unknown[];
}> {
  return fetchJSON<{ state?: string; links: AuthLinkRow[]; count: number; envReferences?: unknown[] }>(
    "/api/qubit/users/auth-links",
  );
}

/** users.py `users.listModelAccounts`: the canonical model-profile registry. */
export function listModelAccounts(): Promise<{
  state?: string;
  accounts: ModelAccountRow[];
  count: number;
  active?: string;
}> {
  return fetchJSON<{ state?: string; accounts: ModelAccountRow[]; count: number; active?: string }>(
    "/api/qubit/users/model-accounts",
  );
}

/** users.py `users.selectModelAccount`: session -> profile selection (confirm-gated). */
export function selectModelAccount(
  session: string,
  authProfileId: string,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>("/api/qubit/users/select-model-account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, session, authProfileId }),
      }),
    confirm,
  );
}

/** users.py `users.linkAuthProfile`: credential-pool add (references only, confirm-gated). */
export function linkAuthProfile(
  provider: string,
  keyRef: string,
  label?: string,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>("/api/qubit/users/link-auth-profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, provider, keyRef, label: label ?? "" }),
      }),
    confirm,
  );
}

/** users.py `users.unlinkAuthProfile`: credential-pool remove (confirm-gated). */
export function unlinkAuthProfile(
  provider: string,
  index: number,
  confirm?: ConfirmFn,
): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>("/api/qubit/users/unlink-auth-profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, provider, index }),
      }),
    confirm,
  );
}

/** users.py `users.authConnect.catalog`: configured providers for the auth handoff. */
export function authConnectCatalog(): Promise<{
  state?: string;
  providers: AuthConnectCatalogRow[];
  count: number;
}> {
  return fetchJSON<{ state?: string; providers: AuthConnectCatalogRow[]; count: number }>(
    "/api/qubit/users/auth-connect-catalog",
  );
}

/**
 * Manual credential handoff: Qubit never performs the provider dance itself.
 * Shown next to the auth-connect catalog.
 */
export const AUTH_LOGIN_HANDOFF = "qubit auth login --provider <name>";

/**
 * Policy-refused users operations. Reasons are the exact
 * `operator_prerequisite` strings the backend emits.
 */
export const DISABLED_USERS_ACTIONS: DisabledAction[] = [
  {
    action: "users.setDisplayName",
    reason: "a multi-user profile store (Qubit is single-operator)",
    counterpart:
      "the fixed loopback operator display name (voicebridge.qubit_compat.h_auth_me)",
  },
  {
    action: "users.setRole",
    reason: "a multi-user profile/role store (Qubit is single-operator)",
    counterpart: "the fixed operator scopes advertised by the gateway `connect` handshake",
  },
  {
    action: "users.setAvatar",
    reason: "an operator-managed avatar/media store and hosting",
    counterpart: "the local device identity (voicebridge.device.profile); no avatar field exists",
  },
  {
    action: "users.linkEmail",
    reason: "an email/identity verification service",
    counterpart:
      "the loopback operator identity (voicebridge.qubit_compat.h_auth_me; email is empty)",
  },
  {
    action: "users.github.status",
    reason:
      "a GitHub credential reference (env:/file:) with the required scopes and network access",
    counterpart: "CLI `qubit auth status` + an operator-managed credential reference",
  },
  {
    action: "users.github.authorize.start",
    reason: "a live GitHub OAuth app/device-flow service and network access",
    counterpart:
      "CLI `qubit auth login --provider github` (manual handoff in the operator's browser)",
  },
  {
    action: "users.authConnect.start",
    reason: "a live external provider OAuth/connect service and network access",
    counterpart:
      "CLI `qubit auth login --provider <name>` (manual handoff; Qubit never performs the OAuth dance)",
  },
];

export const ocFamilyAdminApi = {
  ROUTES,
  // skills-review
  listProposals,
  inspectProposal,
  proposalsHistoryStatus,
  createProposal,
  applyProposal,
  rejectProposal,
  quarantineProposal,
  reviseProposal,
  updateStagedProposal,
  requestProposalRevision,
  listProposalEvents,
  curatorPin,
  curatorUnpin,
  curatorRestore,
  DISABLED_SKILLS_ACTIONS,
  // mcp-calls
  callMcpTool,
  listMcpResources,
  readMcpResource,
  DISABLED_MCP_ACTIONS,
  // users
  getOperatorSelf,
  listOperators,
  listMentionable,
  getOperatorPrefs,
  setOperatorPrefs,
  listAuthLinks,
  listModelAccounts,
  selectModelAccount,
  linkAuthProfile,
  unlinkAuthProfile,
  authConnectCatalog,
  AUTH_LOGIN_HANDOFF,
  DISABLED_USERS_ACTIONS,
};
