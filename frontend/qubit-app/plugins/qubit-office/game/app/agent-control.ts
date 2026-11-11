/* Pure helpers for the agent control drawer.
 *
 * The drawer must never invent an agent. A seat from the office roster is
 * linked to a Business OS agent only by an explicit durable key
 * (``metadata.office_seat_slug`` / ``metadata.seat_slug``), an exact desk slug,
 * or an exact display-name match. Everything else is reported as "not linked"
 * and the operator can pick a managed agent explicitly.
 */
import type { AgentRecord } from "./business-api";
import type { AgentSeat } from "./agents-panel";

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
