/**
 * WorkbenchRail — the far-left, icon-only navigation column.
 *
 * Eight destinations, top to bottom, with the settings gear pinned to the
 * bottom by a spacer — the same arrangement the absorbed console used
 * (`static/index.html:152-169` rail). Two rules make this honest:
 *
 * 1. **A route that exists in this app is a link.** Every enabled entry names a
 *    path that is registered in `App.tsx`; clicking it navigates with the
 *    router, so the rail is real navigation rather than decoration.
 * 2. **A route that does not exist is disabled, with the reason in its title.**
 *    It is never a button that does nothing and never points at a second
 *    product's dashboard. `disabledWhy` is rendered as the tooltip text, so
 *    "why can't I click this" is answered on hover.
 *
 * The Chat entry is the current page (`/qubit-workbench`), which is what the
 * active accent bar marks.
 */

import {
  BarChart3,
  Calendar,
  Clock,
  FileText,
  LayoutGrid,
  MessageSquare,
  Settings,
  Sparkles,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Link } from "react-router";

import {
  RAIL_BUTTON,
  RAIL_BUTTON_ACTIVE,
  RAIL_BUTTON_DISABLED,
  RAIL_BUTTON_IDLE,
  REGION,
} from "./workbench-theme";

interface RailEntry {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Router path, when this app has one. */
  to?: string;
  active?: boolean;
  /** Shown as the native title when the entry cannot be opened. */
  disabledWhy?: string;
}

/**
 * The eight entries. `to` values are all present in `App.tsx`'s route table;
 * an entry without one is drawn disabled and says why.
 */
export const WORKBENCH_RAIL: readonly RailEntry[] = [
  { id: "chat", label: "Chat", icon: MessageSquare, to: "/qubit-workbench", active: true },
  { id: "workspaces", label: "Workspaces", icon: LayoutGrid, to: "/agent-workspace" },
  { id: "agents", label: "Agents", icon: Sparkles, to: "/skills" },
  { id: "sessions", label: "Session history", icon: Clock, to: "/sessions" },
  { id: "jobs", label: "Scheduled jobs", icon: Calendar, to: "/cron" },
  { id: "analytics", label: "Analytics", icon: BarChart3, to: "/analytics" },
  { id: "files", label: "Workspace files", icon: FileText, to: "/files" },
];

/**
 * The bottom block: settings. `/config` is the dashboard's settings home.
 * Kept separate so the spacer above it can push it to the floor.
 */
const RAIL_FOOTER: readonly RailEntry[] = [
  { id: "settings", label: "Settings", icon: Settings, to: "/config" },
];

function RailButton({ entry }: { entry: RailEntry }) {
  const Icon = entry.icon;
  if (!entry.to) {
    return (
      <button
        type="button"
        disabled
        aria-disabled="true"
        aria-label={entry.label}
        title={entry.disabledWhy ?? `${entry.label} is not available`}
        data-testid={`workbench-rail-${entry.id}`}
        className={`${RAIL_BUTTON} ${RAIL_BUTTON_DISABLED}`}
      >
        <Icon aria-hidden="true" />
      </button>
    );
  }
  return (
    <Link
      to={entry.to}
      aria-label={entry.label}
      title={entry.label}
      aria-current={entry.active ? "page" : undefined}
      data-testid={`workbench-rail-${entry.id}`}
      className={`${RAIL_BUTTON} ${entry.active ? RAIL_BUTTON_ACTIVE : RAIL_BUTTON_IDLE}`}
    >
      <Icon aria-hidden="true" />
    </Link>
  );
}

export function WorkbenchRail() {
  return (
    <nav
      aria-label="Primary navigation"
      data-testid="workbench-rail"
      className={`${REGION.rail} border-r border-border`}
    >
      {WORKBENCH_RAIL.map((entry) => (
        <RailButton key={entry.id} entry={entry} />
      ))}
      <div className="min-h-2 flex-1" aria-hidden="true" />
      {RAIL_FOOTER.map((entry) => (
        <RailButton key={entry.id} entry={entry} />
      ))}
    </nav>
  );
}

export default WorkbenchRail;
