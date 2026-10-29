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
    paths: ["/files", "/analytics", "/models", "/logs", "/qubit-scheduler", "/agent-workspace"],
  },
  {
    id: "voice",
    label: "Voice & Media",
    index: "03",
    paths: [
      "/qubit-talk",
      "/qubit-tv",
      "/canvas",
      "/qubit-office",
      "/qubit-office2",
      "/qubit-office3",
      "/omniroute",
      "/kitchen",
      "/computer",
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
    paths: ["/system", "/config", "/env", "/profiles", "/agents", "/docs", "/admin/skills-review"],
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
  "/chat": "Terminal chat — the embedded TUI",
  "/qubit-chat": "Voice and reports chat surface",
  "/sessions": "Session history and resume",
  "/files": "Workspace files and artifacts",
  "/analytics": "Token usage and cost series",
  "/models": "Model catalog and routing",
  "/logs": "Agent, gateway and error logs",
  "/qubit-scheduler": "Recurring and one-off tasks",
  "/agent-workspace": "Nodes, worktrees and session ops",
  "/qubit-talk": "Voice, wake word and speech",
  "/qubit-tv": "Media playback surface",
  "/canvas": "Qubit Canvas board",
  "/qubit-office": "Office — agent roster view",
  "/qubit-office2": "Agent Quest",
  "/qubit-office3": "Voice conversation view",
  "/omniroute": "Router and provider mesh",
  "/skills": "Installed skills",
  "/plugins": "Plugin surface and installs",
  "/mcp": "MCP servers and tools",
  "/cron": "Scheduled jobs",
  "/qubit-memory": "Memory providers and context store",
  "/channels": "Messaging channels",
  "/webhooks": "Inbound webhooks",
  "/pairing": "Device pairing and trust",
  "/config": "Agent configuration",
  "/env": "API keys and secrets",
  "/profiles": "Profile isolation",
  "/system": "Machine and runtime status",
  "/docs": "Documentation",
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
