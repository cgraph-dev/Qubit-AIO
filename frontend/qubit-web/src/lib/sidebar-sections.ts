/**
 * Sidebar information architecture — the ONE declaration of how nav entries
 * are grouped for the rail, and what each entry says about itself in a
 * tooltip.
 *
 * Kept as a pure data module (no React, no lucide imports) so the grouping is
 * unit-testable: a nav entry that is not listed here still renders (in the
 * catch-all section), which makes an unlisted entry a visual omission rather
 * than a vanished route.
 *
 * Ordering contract: sections render in the order declared below, and entries
 * inside a section render in the order the nav list supplies. Routes are
 * untouched by grouping — this file decides presentation only.
 */

export interface SidebarSectionSpec {
  /** Stable DOM id, used for `aria-labelledby` on the section group. */
  id: string;
  /** Heading text. */
  label: string;
  /** Two-digit index shown in the section rail ("01"). */
  index: string;
  /** Paths belonging to this section, in display order. */
  paths: readonly string[];
}

/**
 * The rail's sections. `chat` is deliberately first and unlabelled in the
 * component (the primary surface needs no heading); `system` is rendered in
 * the fixed bottom block, not in the scrolling area.
 */
export const SIDEBAR_SECTIONS: readonly SidebarSectionSpec[] = [
  {
    id: "primary",
    label: "Primary",
    index: "01",
    paths: ["/chat", "/qubit-chat", "/sessions"],
  },
  {
    id: "work",
    label: "Work",
    index: "02",
    // Workbench leads: it is the three-panel workspace (chat + files + terminal)
    // and the widest surface in the dashboard, so burying it under System with
    // eight other routes made the primary working view undiscoverable.
    paths: ["/qubit-workbench", "/files", "/analytics", "/models", "/logs", "/qubit-scheduler", "/agent-workspace"],
  },
  {
    id: "voice",
    label: "Voice & Media",
    index: "03",
    paths: [
      "/qubit-talk",
      "/qubit-tv",
      "/qubit-office",
      "/omniroute",
    ],
  },
  {
    id: "agents",
    label: "Agents",
    index: "04",
    paths: [
      "/skills",
      "/plugins",
      "/mcp",
      "/cron",
      "/qubit-bot",
      "/qubit-harness-router",
      "/qubit-memory",
      "/agent-cards",
      "/canvas",
      "/kitchen",
      "/qubit-task-inbox",
      "/qubit-board-admin",
      "/qubit-tasks",
      "/qubit-taskflows",
    ],
  },
  {
    id: "connect",
    label: "Connect",
    index: "05",
    paths: ["/channels", "/webhooks", "/pairing"],
  },
  {
    id: "system",
    label: "System",
    index: "06",
    paths: [
      "/system",
      "/qubit-hud",
      "/config",
      "/env",
      "/profiles",
      "/agents",
      "/computer",
      "/guide",
      "/admin/skills-review",
    ],
  },
];

/**
 * A friendlier navigation hierarchy used only by Q6/Q6-L.
 * Presentation-only: sectionizeNav retains all routes, including unknown
 * plugin destinations, and leaves the default theme hierarchy untouched.
 */
export const MANGA_SIDEBAR_SECTIONS: readonly SidebarSectionSpec[] = [
  {
    id: "primary",
    label: "Chat",
    index: "01",
    paths: ["/chat", "/qubit-chat", "/sessions", "/webchat"],
  },
  {
    id: "workspace",
    label: "Workspace",
    index: "02",
    paths: [
      "/qubit-workbench", "/files", "/agent-workspace", "/analytics",
      "/models", "/logs", "/canvas", "/open-viking",
    ],
  },
  {
    id: "automations",
    label: "Automations",
    index: "03",
    paths: [
      "/cron", "/qubit-scheduler", "/qubit-tasks", "/qubit-taskflows",
      "/qubit-task-inbox", "/qubit-board-admin",
    ],
  },
  {
    id: "ai-tools",
    label: "AI Tools",
    index: "04",
    paths: [
      "/skills", "/plugins", "/mcp", "/qubit-bot", "/qubit-memory",
      "/qubit-harness-router", "/agent-cards", "/kitchen",
    ],
  },
  {
    id: "connections",
    label: "Connections",
    index: "05",
    paths: [
      "/channels", "/webhooks", "/pairing", "/devices", "/omniroute",
    ],
  },
  {
    id: "studio",
    label: "Voice & Media",
    index: "06",
    paths: ["/qubit-talk", "/qubit-tv", "/qubit-office", "/voice-edit"],
  },
  {
    id: "system",
    label: "System",
    index: "07",
    paths: [
      "/system", "/qubit-hud", "/config", "/env", "/profiles",
      "/agents", "/computer", "/guide", "/admin/skills-review",
      "/admin/mcp-calls", "/admin/users", "/docs", "/nodes",
      "/session-ops", "/worktrees", "/checkpoints",
    ],
  },
];

/** Anything the nav supplies that no section claims lands here, last. */
export const SIDEBAR_FALLBACK_SECTION: SidebarSectionSpec = {
  id: "more",
  label: "More",
  index: "07",
  paths: [],
};

/**
 * One-line explanations shown in the hover/focus tooltip. Only entries where
 * the label alone is ambiguous need a description — the tooltip always shows
 * the label, and adds this line when present.
 */
export const NAV_DESCRIPTIONS: Readonly<Record<string, string>> = {
  "/chat": "Talk to Qubit in the terminal-style chat",
  "/qubit-chat": "Ask Qubit questions and work with your AI assistants",
  "/sessions": "Find, reopen, and manage previous conversations",
  "/webchat": "Open conversations from connected chat services",
  "/files": "Browse and organize your files and results",
  "/analytics": "See how much you're using Qubit and what it costs",
  "/models": "Choose and manage the AI models Qubit can use",
  "/logs": "See what happened when something goes wrong",
  "/qubit-scheduler": "Set up tasks Qubit runs for you",
  "/cron": "Create tasks that run automatically at chosen times",
  "/qubit-tasks": "See the work that needs to be done",
  "/qubit-taskflows": "Connect multiple tasks into a workflow",
  "/qubit-task-inbox": "Review incoming tasks and requests",
  "/qubit-board-admin": "Manage your task boards and settings",
  "/agent-workspace": "Manage agents, their files, and work history",
  "/qubit-talk": "Talk to Qubit using your voice",
  "/qubit-tv": "Watch or manage media in Qubit",
  "/canvas": "Organize ideas on a visual canvas",
  "/qubit-office": "View your agents in the Office workspace",
  "/omniroute": "Choose how Qubit connects to AI services",
  "/skills": "Explore abilities you can add to Qubit",
  "/plugins": "Install and manage extra features",
  "/mcp": "Connect Qubit to tools and external services",
  "/qubit-memory": "Manage what Qubit remembers and retrieves",
  "/channels": "Choose where Qubit can send and receive messages",
  "/webhooks": "Connect events from your other services",
  "/pairing": "Connect and approve devices",
  "/config": "Choose how Qubit behaves",
  "/env": "Manage connection keys and private settings",
  "/profiles": "Switch between different Qubit setups",
  "/agents": "Manage the assistants doing work for you",
  "/system": "Check whether Qubit is running correctly",
  "/qubit-hud": "See a live overview of Qubit's health",
  "/docs": "Explore the technical reference",
  "/guide": "Learn how to use Qubit",
  "/computer": "Manage connected computer workspaces",
  "/nodes": "See computers and services connected to Qubit",
  "/devices": "Check and manage trusted devices",
  "/worktrees": "View separate working copies of your projects",
  "/checkpoints": "Restore or inspect saved work checkpoints",
  "/session-ops": "Manage active conversations and their tasks",
  "/agent-cards": "Browse and manage your agent profiles",
  "/kitchen": "Bring in features from your other tools",
  "/qubit-bot": "Manage the Qubit bot",
  "/qubit-harness-router": "Choose how assistant tasks are handled",
  "/admin/skills-review": "Review available skills before enabling them",
  "/admin/mcp-calls": "See which connected tools have been used",
  "/admin/users": "Manage people who can use Qubit",
};

export interface SectionedEntry<T> {
  section: SidebarSectionSpec;
  entries: T[];
}

/** Minimal shape assignment needs; the nav item satisfies it. */
interface PathLike {
  path: string;
}

/**
 * Distribute nav entries across the declared sections.
 *
 * - Entry order inside a section follows the input nav order (plugin
 *   `position` hints keep working), not the order typed in `paths`.
 * - Unknown paths collect into the fallback section instead of disappearing.
 * - Cases where the nav supplies child entries for a group parent are the
 *   caller's concern; the parent's own path is what matters here.
 */
export function sectionizeNav<T extends PathLike>(
  items: readonly T[],
  sections: readonly SidebarSectionSpec[] = SIDEBAR_SECTIONS,
  fallback: SidebarSectionSpec = SIDEBAR_FALLBACK_SECTION,
): Array<SectionedEntry<T>> {
  const sectionByPath = new Map<string, SidebarSectionSpec>();
  for (const section of sections) {
    for (const path of section.paths) sectionByPath.set(path, section);
  }

  const buckets = new Map<string, T[]>();
  const unclaimed: T[] = [];
  for (const item of items) {
    const section = sectionByPath.get(item.path);
    if (!section) {
      unclaimed.push(item);
      continue;
    }
    const bucket = buckets.get(section.id);
    if (bucket) bucket.push(item);
    else buckets.set(section.id, [item]);
  }

  const result: Array<SectionedEntry<T>> = [];
  for (const section of sections) {
    const entries = buckets.get(section.id);
    if (entries && entries.length > 0) result.push({ section, entries });
  }
  if (unclaimed.length > 0) {
    result.push({ section: fallback, entries: unclaimed });
  }
  return result;
}

/** Tooltip description for a path; `undefined` when the label stands alone. */
export function navDescription(path: string): string | undefined {
  return NAV_DESCRIPTIONS[path];
}
