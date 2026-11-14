/* Pure helpers for the agent control drawer.
 *
 * The drawer must never invent an agent. A seat from the office roster is
 * linked to a Business OS agent only by an explicit durable key
 * (``metadata.office_seat_slug`` / ``metadata.seat_slug``), an exact desk slug,
 * or an exact display-name match. Everything else is reported as "not linked"
 * and the operator can pick a managed agent explicitly.
 */
import type { AgentPermissions, AgentRecord } from "./business-api";
import type { AgentSeat } from "./agents-panel";

/* The known control vocabulary. Mirrored from the backend contract
 * (`voicebridge/business/agents.py`): the panel must be able to show what the
 * store will accept, and must never invent a category, scope or wildcard the
 * service would refuse. The backend remains the authority; these constants
 * only keep the form honest and the validation local. */
export const PERMISSION_CATEGORIES = [
  "tools", "toolsets", "files", "browser", "network", "terminal",
  "email", "social", "etsy", "payments", "data",
] as const;
export type PermissionCategory = (typeof PERMISSION_CATEGORIES)[number];

/** Categories whose scopes are a closed action list (not free-form names). */
export const ACTION_SCOPES: Record<string, readonly string[]> = {
  browser: ["read", "navigate", "click", "type", "download", "upload", "screenshot"],
  network: ["read", "write", "http", "websocket"],
  terminal: ["read", "execute"],
  email: ["read", "draft", "send"],
  social: ["read", "draft", "publish", "reply"],
  etsy: ["read", "draft", "publish", "message", "refund"],
  payments: ["read", "charge", "refund", "payout"],
  data: ["read", "write", "export", "delete"],
};

export const WILDCARD_SCOPES = [
  "*", "all", "any", "unrestricted", "everything", "full", "none",
] as const;

export const AUTONOMY_LEVELS = ["L0", "L1", "L2", "L3", "L4", "L5"] as const;
export const REASONING_EFFORTS = [
  "none", "minimal", "low", "medium", "high",
] as const;
export const APPROVAL_ACTIONS = [
  "spend", "legal", "publish", "contact", "irreversible",
] as const;

const SCOPE_TOKEN_RE = /^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}$/;
const ROOT_PATHS = new Set(["/", "~", "~/", "//", "\\"]);

export function matchAgentToSeat(
  agents: AgentRecord[], seat: AgentSeat | null | undefined,
): AgentRecord | null {
  if (!seat) return null;
  const slug = (seat.slug || "").trim();
  const name = (seat.name || "").trim().toLowerCase();
  for (const agent of agents) {
    const meta = agent.metadata || {};
    const linked = [meta.office_seat_slug, meta.seat_slug, meta.office_slug]
      .map((value) => String(value ?? "").trim())
      .filter(Boolean);
    if (slug && linked.includes(slug)) return agent;
    if (slug && (agent.desk || "").trim() === slug) return agent;
  }
  for (const agent of agents) {
    if (name && (agent.display_name || "").trim().toLowerCase() === name) {
      return agent;
    }
  }
  return null;
}

export function formatMinor(value: number | null | undefined): string {
  const minor = Number(value);
  if (!Number.isFinite(minor)) return "unset";
  const major = minor / 100;
  return `$${major.toFixed(2)}`;
}

export function budgetSummary(agent: AgentRecord | null): string {
  if (!agent) return "";
  const safety = agent.safety;
  return `${formatMinor(safety.daily_spend_minor)}/day · `
    + `${formatMinor(safety.monthly_spend_minor)}/month · `
    + `${formatMinor(safety.per_action_limit_minor)}/action`;
}

export function rateSummary(agent: AgentRecord | null): string {
  if (!agent) return "";
  const safety = agent.safety;
  return `${safety.rate_limit_per_minute}/min · `
    + `${safety.max_turns_per_day} turns/day · `
    + `${safety.max_tokens_per_turn} tokens/turn`;
}

export function autonomyLabel(level: string | null | undefined): string {
  switch (String(level || "").toUpperCase()) {
    case "L0": return "L0 — observe only";
    case "L1": return "L1 — draft, no action";
    case "L2": return "L2 — act within budgets";
    case "L3": return "L3 — act, approval for risky";
    case "L4": return "L4 — act, approval for spend/legal";
    case "L5": return "L5 — full, owner-audited";
    default: return level ? String(level) : "unset";
  }
}

export function permissionRows(agent: AgentRecord | null): Array<[string, string[]]> {
  if (!agent) return [];
  return Object.entries(agent.permissions || {})
    .map(([category, scopes]): [string, string[]] =>
      [category, Array.isArray(scopes) ? scopes : []])
    .sort(([a], [b]) => a.localeCompare(b));
}

export function permissionSummary(agent: AgentRecord | null): string {
  const rows = permissionRows(agent).filter(([, scopes]) => scopes.length > 0);
  if (!rows.length) return "deny by default (no scopes granted)";
  return rows.map(([category, scopes]) =>
    `${category}: ${scopes.length}`).join(" · ");
}

export function scheduleSummary(agent: AgentRecord | null): string {
  if (!agent) return "";
  return `${agent.schedule || "manual"} (${agent.timezone || "UTC"})`;
}

// ---------------------------------------------------------------------------
// Edit model + validation (mirrors the backend refusals)
// ---------------------------------------------------------------------------
export function emptyPermissions(): AgentPermissions {
  return Object.fromEntries(
    PERMISSION_CATEGORIES.map((category) => [category, []]),
  ) as unknown as AgentPermissions;
}

export function normalizePermissions(
  source: AgentPermissions | null | undefined,
): AgentPermissions {
  const out = emptyPermissions();
  for (const category of PERMISSION_CATEGORIES) {
    const raw = source?.[category];
    if (!Array.isArray(raw)) continue;
    const values = [...new Set(
      raw.map((value) => String(value ?? "").trim()).filter(Boolean),
    )].sort((a, b) => a.localeCompare(b));
    out[category] = values;
  }
  return out;
}

export type ScopeCheck =
  | { ok: true; value: string }
  | { ok: false; error: string };

/** Validate one scope exactly as the backend does before it is added. */
export function validatePermissionScope(
  category: string, raw: string,
): ScopeCheck {
  const token = String(raw ?? "").trim();
  if (!token) return { ok: false, error: "Name an explicit scope." };
  if (token.includes("*")
      || WILDCARD_SCOPES.includes(token.toLowerCase() as never)) {
    return {
      ok: false,
      error: `"${token}" is a wildcard; name an explicit scope.`,
    };
  }
  if (category === "files") {
    const normalised = token.replace(/\\/g, "/");
    if (ROOT_PATHS.has(normalised)) {
      return {
        ok: false,
        error: `"${token}" is unrestricted; name an explicit path.`,
      };
    }
    if (!(normalised.startsWith("/") || normalised.startsWith("~/"))) {
      return {
        ok: false, error: "File scopes must be absolute or start with '~/'.",
      };
    }
    if (normalised.split("/").some((part) => part === "..")) {
      return { ok: false, error: "File scopes may not traverse '..'." };
    }
    return { ok: true, value: normalised };
  }
  const allowed = ACTION_SCOPES[category];
  if (allowed) {
    if (!allowed.includes(token)) {
      return {
        ok: false,
        error: `Unknown ${category} scope; expected one of `
          + `${allowed.join(", ")}.`,
      };
    }
    return { ok: true, value: token };
  }
  if (!SCOPE_TOKEN_RE.test(token)) {
    return { ok: false, error: `"${token}" is not a valid ${category} scope.` };
  }
  return { ok: true, value: token };
}

export function addPermissionScope(
  permissions: AgentPermissions, category: string, raw: string,
): { permissions: AgentPermissions; check: ScopeCheck } {
  const check = validatePermissionScope(category, raw);
  if (!check.ok) return { permissions, check };
  const next = normalizePermissions(permissions);
  next[category] = [...new Set([...(next[category] ?? []), check.value])]
    .sort((a, b) => a.localeCompare(b));
  return { permissions: next, check };
}

export function removePermissionScope(
  permissions: AgentPermissions, category: string, scope: string,
): AgentPermissions {
  const next = normalizePermissions(permissions);
  next[category] = (next[category] ?? []).filter((value) => value !== scope);
  return next;
}

export interface AgentDraft {
  provider: string;
  model: string;
  reasoning_effort: string;
  fallback_provider: string;
  fallback_model: string;
  max_tokens: string;
  autonomy: string;
  daily_spend: string;
  monthly_spend: string;
  per_action_limit: string;
  max_turns_per_day: string;
  max_tokens_per_turn: string;
  rate_limit_per_minute: string;
  schedule: string;
  timezone: string;
  permissions: AgentPermissions;
}

export function minorToMoney(minor: number | null | undefined): string {
  const value = Number(minor);
  return Number.isFinite(value) ? (value / 100).toFixed(2) : "0.00";
}

/** Parse a dollar string into integer minor units, or null when malformed. */
export function parseMoneyToMinor(raw: string): number | null {
  const clean = String(raw ?? "").trim();
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  return Math.round(Number(clean) * 100);
}

export function draftFromAgent(agent: AgentRecord): AgentDraft {
  return {
    provider: agent.provider ?? "",
    model: agent.model ?? "",
    reasoning_effort: agent.reasoning_effort ?? "medium",
    fallback_provider: agent.fallback_provider ?? "",
    fallback_model: agent.fallback_model ?? "",
    max_tokens: String(agent.max_tokens),
    autonomy: agent.safety.autonomy,
    daily_spend: minorToMoney(agent.safety.daily_spend_minor),
    monthly_spend: minorToMoney(agent.safety.monthly_spend_minor),
    per_action_limit: minorToMoney(agent.safety.per_action_limit_minor),
    max_turns_per_day: String(agent.safety.max_turns_per_day),
    max_tokens_per_turn: String(agent.safety.max_tokens_per_turn),
    rate_limit_per_minute: String(agent.safety.rate_limit_per_minute),
    schedule: agent.schedule ?? "",
    timezone: agent.timezone ?? "",
    permissions: normalizePermissions(agent.permissions),
  };
}

function intField(
  value: string, key: string, minimum: number, errors: Record<string, string>,
) {
  const clean = String(value ?? "").trim();
  if (!/^\d+$/.test(clean)) {
    errors[key] = minimum === 1
      ? "Enter a whole number of at least 1."
      : "Enter a whole number of at least 0.";
    return null;
  }
  const parsed = Number(clean);
  if (!Number.isSafeInteger(parsed) || parsed < minimum) {
    errors[key] = minimum === 1
      ? "Enter a whole number of at least 1."
      : "Enter a whole number of at least 0.";
    return null;
  }
  return parsed;
}

function moneyField(
  value: string, key: string, errors: Record<string, string>,
) {
  const minor = parseMoneyToMinor(value);
  if (minor === null) {
    errors[key] = "Enter a dollar amount (0.00) with at most two decimals.";
    return null;
  }
  return minor;
}

function timezoneValid(value: string): boolean {
  if (!value.trim()) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

/** Return field-keyed validation errors; an empty object means save-able. */
export function validateDraft(draft: AgentDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!draft.provider.trim()) errors.provider = "Provider is required.";
  if (!draft.model.trim()) errors.model = "Model is required.";
  if (!REASONING_EFFORTS.includes(
    draft.reasoning_effort as never)) {
    errors.reasoning_effort = "Choose a known reasoning effort.";
  }
  if (!AUTONOMY_LEVELS.includes(draft.autonomy as never)) {
    errors.autonomy = "Choose an autonomy level from L0 to L5.";
  }
  intField(draft.max_tokens, "max_tokens", 1, errors);
  moneyField(draft.daily_spend, "daily_spend", errors);
  moneyField(draft.monthly_spend, "monthly_spend", errors);
  moneyField(draft.per_action_limit, "per_action_limit", errors);
  intField(draft.max_turns_per_day, "max_turns_per_day", 0, errors);
  intField(draft.max_tokens_per_turn, "max_tokens_per_turn", 1, errors);
  intField(draft.rate_limit_per_minute, "rate_limit_per_minute", 0, errors);
  if (!draft.schedule.trim()) {
    errors.schedule = "Schedule is required (use 'manual' for on demand).";
  } else if (draft.schedule.trim().length > 200) {
    errors.schedule = "Schedule must be 200 characters or fewer.";
  }
  if (!draft.timezone.trim()) {
    errors.timezone = "Timezone is required (for example Europe/Bucharest).";
  } else if (!timezoneValid(draft.timezone.trim())) {
    errors.timezone = "That timezone is not recognised.";
  }
  for (const category of PERMISSION_CATEGORIES) {
    for (const scope of draft.permissions[category] ?? []) {
      const check = validatePermissionScope(category, scope);
      if (!check.ok) {
        errors[`perm:${category}`] = check.error;
        break;
      }
    }
  }
  return errors;
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((value, index) => value === b[index]);
}

function permissionsChanged(
  draft: AgentPermissions, agent: AgentPermissions,
): boolean {
  const left = normalizePermissions(draft);
  const right = normalizePermissions(agent);
  return PERMISSION_CATEGORIES.some(
    (category) => !sameList(left[category] ?? [], right[category] ?? []),
  );
}

/** The update payload for exactly the fields the operator changed. */
export function buildAgentChanges(
  draft: AgentDraft, agent: AgentRecord,
): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  if (draft.provider.trim() !== agent.provider) {
    changes.provider = draft.provider.trim();
  }
  if (draft.model.trim() !== agent.model) {
    changes.model = draft.model.trim();
  }
  if (draft.reasoning_effort !== agent.reasoning_effort) {
    changes.reasoning_effort = draft.reasoning_effort;
  }
  const fallbackProvider = draft.fallback_provider.trim() || null;
  const fallbackModel = draft.fallback_model.trim() || null;
  if (fallbackProvider !== (agent.fallback_provider ?? null)) {
    changes.fallback_provider = fallbackProvider;
  }
  if (fallbackModel !== (agent.fallback_model ?? null)) {
    changes.fallback_model = fallbackModel;
  }
  if (Number(draft.max_tokens) !== agent.max_tokens) {
    changes.max_tokens = Number(draft.max_tokens);
  }
  if (draft.schedule.trim() !== agent.schedule) {
    changes.schedule = draft.schedule.trim();
  }
  if (draft.timezone.trim() !== agent.timezone) {
    changes.timezone = draft.timezone.trim();
  }

  const safety: Record<string, unknown> = {};
  if (draft.autonomy !== agent.safety.autonomy) {
    safety.autonomy = draft.autonomy;
  }
  const daily = parseMoneyToMinor(draft.daily_spend);
  const monthly = parseMoneyToMinor(draft.monthly_spend);
  const perAction = parseMoneyToMinor(draft.per_action_limit);
  if (daily !== agent.safety.daily_spend_minor) {
    safety.daily_spend_minor = daily;
  }
  if (monthly !== agent.safety.monthly_spend_minor) {
    safety.monthly_spend_minor = monthly;
  }
  if (perAction !== agent.safety.per_action_limit_minor) {
    safety.per_action_limit_minor = perAction;
  }
  if (Number(draft.max_turns_per_day) !== agent.safety.max_turns_per_day) {
    safety.max_turns_per_day = Number(draft.max_turns_per_day);
  }
  if (Number(draft.max_tokens_per_turn) !== agent.safety.max_tokens_per_turn) {
    safety.max_tokens_per_turn = Number(draft.max_tokens_per_turn);
  }
  if (Number(draft.rate_limit_per_minute)
      !== agent.safety.rate_limit_per_minute) {
    safety.rate_limit_per_minute = Number(draft.rate_limit_per_minute);
  }
  if (Object.keys(safety).length > 0) changes.safety = safety;

  if (permissionsChanged(draft.permissions, agent.permissions)) {
    changes.permissions = normalizePermissions(draft.permissions);
  }
  return changes;
}

export function isDraftDirty(draft: AgentDraft | null,
                             agent: AgentRecord | null): boolean {
  if (!draft || !agent) return false;
  return Object.keys(buildAgentChanges(draft, agent)).length > 0;
}

export function canPause(agent: AgentRecord | null): boolean {
  return Boolean(agent && agent.status === "active");
}

export function canResume(agent: AgentRecord | null): boolean {
  return Boolean(agent && agent.status === "paused");
}

export function canRevoke(agent: AgentRecord | null): boolean {
  return Boolean(agent && agent.status !== "archived");
}

export function canArchive(agent: AgentRecord | null): boolean {
  return Boolean(agent && agent.status !== "archived");
}
