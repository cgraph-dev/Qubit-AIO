import type { Translations } from "@/i18n/types";

const BUILTIN: Record<string, keyof Translations["app"]["nav"]> = {
  "/chat": "chat",
  "/sessions": "sessions",
  "/analytics": "analytics",
  "/models": "models",
  "/logs": "logs",
  "/cron": "cron",
  "/skills": "skills",
  "/plugins": "plugins",
  "/profiles": "profiles",
  "/config": "config",
  "/env": "keys",
  "/docs": "documentation",
};

// Built-in routes without an i18n nav key. Keep these in sync with the
// sidebar labels in App.tsx — the naive capitalize fallback below mangles
// initialisms ("/mcp" → "Mcp") and can't match multi-word labels.
const BUILTIN_LITERAL: Record<string, string> = {
  "/files": "Files",
  "/mcp": "MCP",
  "/channels": "Channels",
  "/webhooks": "Webhooks",
  "/pairing": "Pairing",
  "/system": "System",
  // Nested builtin route — without this the naive path fallback renders the
  // segment "new" against the parent and the header showed "Profiles/new".
  "/profiles/new": "New Profile",
};

// Explicit identity for Qubit overlay routes that would otherwise collide with
// a vendored tab or fall through to the naive capitalize fallback:
//   * hidden routes (/qubit-approvals, /qubit-brand) have no plugin tab and
//     produced mangled titles ("Qubit-approvals" / "Qubit-brand") (D13);
//   * the duplicate surfaces (Qubit Boards vs /kanban, Runtime Inventory vs
//     /devices) need distinct page titles, not the generic manifest label
//     (D8/D9/D10).
// Applied before the plugin-tab lookup, and — for the visible routes — reused
// as the sidebar label in App.tsx so the two never drift.
export const ROUTE_TITLES: Record<string, string> = {
  "/qubit-chat": "Qubit Chat",
  "/qubit-approvals": "Qubit Approvals",
  "/qubit-brand": "Qubit Brand",
  "/qubit-boards": "Qubit Boards",
  "/qubit-runtime": "Runtime Inventory",
  "/voice-edit": "Voice Editing",
  // Family pages without an i18n nav key or plugin tab: the naive path
  // fallback mangles them ("/agent-workspace" → "Agent-workspace",
  // "/admin/skills-review" → "Admin/skills-review"). Titles match each
  // page's own heading, Qubit-branded for the header.
  "/agent-workspace": "Qubit Agent Workspace",
  "/nodes": "Qubit Nodes",
  "/devices": "Qubit Devices",
  "/session-ops": "Qubit Session Ops",
  "/worktrees": "Qubit Worktrees",
  "/checkpoints": "Qubit Checkpoints",
  "/qubit-board-admin": "Qubit Board Admin",
  "/qubit-task-inbox": "Qubit Task Inbox",
  "/qubit-talk": "Qubit Talk & Voice Routing",
  "/qubit-scheduler": "Qubit Scheduler & Media",
  "/admin/skills-review": "Skills Review Queue",
  "/admin/mcp-calls": "Gateway MCP Calls",
  "/admin/users": "Operator & Credentials",
};

export function resolvePageTitle(
  pathname: string,
  t: Translations,
  pluginTabs: { path: string; label: string }[],
): string {
  const normalized = pathname.replace(/\/$/, "") || "/";
  if (normalized === "/") {
    return t.app.nav.sessions;
  }
  const explicit = ROUTE_TITLES[normalized];
  if (explicit) {
    return explicit;
  }
  const plugin = pluginTabs.find((p) => p.path === normalized);
  if (plugin) {
    return plugin.label;
  }
  const key = BUILTIN[normalized];
  if (key) {
    return t.app.nav[key];
  }
  const literal = BUILTIN_LITERAL[normalized];
  if (literal) {
    return literal;
  }
  // Derive title from pathname: "/profiles" → "Profiles"
  const segment = normalized.slice(1);
  if (segment) {
    return segment.charAt(0).toUpperCase() + segment.slice(1);
  }
  return t.app.webUi;
}
