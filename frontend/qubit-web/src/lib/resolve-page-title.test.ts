import { describe, expect, it } from "vitest";
import { resolvePageTitle } from "./resolve-page-title";
import type { Translations } from "@/i18n/types";

// Minimal translations stub — only the fields resolvePageTitle touches.
const t = {
  app: {
    webUi: "Web UI",
    nav: {
      analytics: "Analytics",
      chat: "Chat",
      config: "Config",
      cron: "Cron",
      documentation: "Documentation",
      keys: "Keys",
      logs: "Logs",
      models: "Models",
      profiles: "Profiles",
      plugins: "Plugins",
      sessions: "Sessions",
      skills: "Skills",
    },
  },
} as unknown as Translations;

describe("resolvePageTitle", () => {
  it("uses i18n nav keys for translated routes", () => {
    expect(resolvePageTitle("/sessions", t, [])).toBe("Sessions");
    expect(resolvePageTitle("/env", t, [])).toBe("Keys");
  });

  it("renders initialisms and literal labels correctly", () => {
    // Regression: the naive capitalize fallback produced "Mcp".
    expect(resolvePageTitle("/mcp", t, [])).toBe("MCP");
    expect(resolvePageTitle("/system", t, [])).toBe("System");
    expect(resolvePageTitle("/channels", t, [])).toBe("Channels");
    expect(resolvePageTitle("/webhooks", t, [])).toBe("Webhooks");
    expect(resolvePageTitle("/pairing", t, [])).toBe("Pairing");
    expect(resolvePageTitle("/files", t, [])).toBe("Files");
  });

  it("resolves the nested profile-builder route to a real label", () => {
    // Regression: the naive fallback produced "Profiles/new".
    expect(resolvePageTitle("/profiles/new", t, [])).toBe("New Profile");
    expect(resolvePageTitle("/profiles/new/", t, [])).toBe("New Profile");
  });

  it("prefers plugin tab labels", () => {
    expect(
      resolvePageTitle("/kanban", t, [{ path: "/kanban", label: "Kanban" }]),
    ).toBe("Kanban");
  });

  it("falls back to capitalized path segment for unknown routes", () => {
    expect(resolvePageTitle("/whatever", t, [])).toBe("Whatever");
  });

  it("treats root as sessions and trailing slashes as equivalent", () => {
    expect(resolvePageTitle("/", t, [])).toBe("Sessions");
    expect(resolvePageTitle("/mcp/", t, [])).toBe("MCP");
  });

  it("resolves the family pages to proper Qubit product titles", () => {
    // Regression: the naive fallback mangled these ("Agent-workspace",
    // "Admin/skills-review"). Each title matches its page's own heading.
    expect(resolvePageTitle("/agent-workspace", t, [])).toBe("Qubit Agent Workspace");
    expect(resolvePageTitle("/nodes", t, [])).toBe("Qubit Nodes");
    expect(resolvePageTitle("/devices", t, [])).toBe("Qubit Devices");
    expect(resolvePageTitle("/session-ops", t, [])).toBe("Qubit Session Ops");
    expect(resolvePageTitle("/worktrees", t, [])).toBe("Qubit Worktrees");
    expect(resolvePageTitle("/checkpoints", t, [])).toBe("Qubit Checkpoints");
    expect(resolvePageTitle("/qubit-board-admin", t, [])).toBe("Qubit Board Admin");
    expect(resolvePageTitle("/qubit-task-inbox", t, [])).toBe("Qubit Task Inbox");
    expect(resolvePageTitle("/qubit-talk", t, [])).toBe("Qubit Talk & Voice Routing");
    expect(resolvePageTitle("/qubit-scheduler", t, [])).toBe("Qubit Scheduler & Media");
    expect(resolvePageTitle("/admin/skills-review", t, [])).toBe("Skills Review Queue");
    expect(resolvePageTitle("/admin/mcp-calls", t, [])).toBe("Gateway MCP Calls");
    expect(resolvePageTitle("/admin/users", t, [])).toBe("Operator & Credentials");
  });

  it("resolves the second-wave family pages to proper Qubit product titles", () => {
    expect(resolvePageTitle("/canvas", t, [])).toBe("Qubit Canvas");
    expect(resolvePageTitle("/qubit-taskflows", t, [])).toBe("Qubit Taskflows");
    expect(resolvePageTitle("/qubit-tasks", t, [])).toBe("Qubit Tasks");
    expect(resolvePageTitle("/qubit-memory", t, [])).toBe("Qubit Memory Backends");
    expect(resolvePageTitle("/webchat", t, [])).toBe("Qubit WebChat");
  });

  it("resolves the Bot / Harness Router / Open Viking console titles", () => {
    // Integrator lane 3: page-documented paths, titles match each page's
    // own heading (the Viking children use their short h1s).
    expect(resolvePageTitle("/qubit-bot", t, [])).toBe("Qubit Bot");
    expect(resolvePageTitle("/harness-router", t, [])).toBe("Harness Router");
    expect(resolvePageTitle("/open-viking", t, [])).toBe("Open Viking");
    expect(resolvePageTitle("/open-viking/retrieval", t, [])).toBe("Retrieval");
    expect(resolvePageTitle("/open-viking/uploads", t, [])).toBe("Uploads");
    expect(resolvePageTitle("/open-viking/agent-hub", t, [])).toBe("Agent Hub");
    expect(resolvePageTitle("/open-viking/resources", t, [])).toBe("Resources");
    expect(resolvePageTitle("/open-viking/uploads/", t, [])).toBe("Uploads");
  });
});
