/**
 * ChatToolsSummary — compact, READ-ONLY summary of the agent's real tool,
 * skill, plugin, and MCP registries, shown inside the chat side panels
 * (`/qubit-chat` today; the same card is intended for `/chat`).
 *
 * Closes M1 boot item 5: those registries were reachable only via the left-nav
 * pages, never surfaced next to the chat surface. This card reads the SAME
 * authenticated endpoints the dedicated pages already call:
 *
 *   - tools   → `GET /api/tools/toolsets` (each toolset carries its `tools[]`)
 *   - skills  → `GET /api/skills`
 *   - plugins → `GET /api/dashboard/plugins`
 *   - MCP     → `GET /api/mcp/servers`
 *
 * No mutation, no new backend route, no fabricated data: an endpoint that
 * fails (or an empty registry) renders a short honest note and nothing else.
 * Every fetch is independent and best-effort, so a failure here can never
 * disturb the chat surface or the rest of the panel.
 *
 * Optional `onMention`: when the host page provides it (a composer that can
 * accept text), listed names render as buttons that hand the name back —
 * the host inserts plain `@name` text, exactly as if typed. No send-path,
 * protocol, or routing change: the turn backend sees ordinary composer text.
 *
 *
 * MCP rows are inventory, not a live-connection count: a server is
 * `configured` (registered) and, independently, `connected`. The embedded TUI
 * banner headline counts *connected* servers only, so this card reports both
 * ("N configured · M connected") — never the configured count presented as if
 * it were connected.
 */

import { Card } from "@nous-research/ui/ui/components/card";
import { Link } from "react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import type {
  McpServer,
  PluginManifestResponse,
  SkillInfo,
  ToolsetInfo,
} from "@/lib/api";

/** How many names to show before collapsing into a "+N more" tail. */
const SHORT_LIST = 6;

interface RegistryState<T> {
  data: T | null;
  failed: boolean;
}

const EMPTY = { data: null, failed: false } as const;

function useRegistry<T>(key: string, load: () => Promise<T>): RegistryState<T> {
  const [state, setState] = useState<RegistryState<T>>(EMPTY);
  // The loader is a fresh closure per render; route it through a ref so the
  // effect only re-fires when the resolved `key` (profile) actually changes.
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    let cancelled = false;
    // `Promise.resolve().then(...)` also converts a synchronous throw from a
    // missing/misconfigured endpoint into a normal rejection, so this card can
    // never take down the panel it sits in.
    Promise.resolve()
      .then(() => loadRef.current())
      .then((data) => {
        if (!cancelled) setState({ data, failed: false });
      })
      .catch(() => {
        if (!cancelled) setState({ data: null, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [key]);
  return state;
}

function CountBadge({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded-full border border-current/15 px-1.5 py-0.5 text-[0.6875rem] tabular-nums text-text-secondary">
      {children}
    </span>
  );
}

function ShortList({
  items,
  onMention,
}: {
  items: string[];
  onMention?: (name: string) => void;
}) {
  if (items.length === 0) {
    return <span className="text-[0.6875rem] text-text-tertiary">none</span>;
  }
  const shown = items.slice(0, SHORT_LIST);
  const rest = items.length - shown.length;
  if (!onMention) {
    return (
      <span className="min-w-0 wrap-break-word text-[0.6875rem] leading-snug text-text-tertiary">
        {shown.join(", ")}
        {rest > 0 ? ` +${rest} more` : ""}
      </span>
    );
  }
  return (
    <span className="min-w-0 wrap-break-word text-[0.6875rem] leading-snug text-text-tertiary">
      {shown.map((name, i) => (
        <span key={name}>
          {i > 0 ? ", " : ""}
          <button
            type="button"
            onClick={() => onMention(name)}
            title={`Insert @${name} into the composer`}
            className="hover:underline"
          >
            {name}
          </button>
        </span>
      ))}
      {rest > 0 ? ` +${rest} more` : ""}
    </span>
  );
}

function Row({
  label,
  href,
  testId,
  count,
  detail,
  items,
  failed,
  onMention,
}: {
  label: string;
  href: string;
  testId: string;
  count: number | null;
  detail?: string;
  items: string[];
  failed: boolean;
  onMention?: (name: string) => void;
}) {
  return (
    <div className="qchat__tools-row flex min-w-0 flex-col gap-0.5">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <Link
          to={href}
          className="min-w-0 truncate text-xs font-medium text-text-secondary hover:underline"
          title={`Open ${label}`}
        >
          {label}
        </Link>
        {failed ? (
          <span
            data-testid={`${testId}-error`}
            data-state="degraded"
            className="shrink-0 text-[0.6875rem] text-text-tertiary"
          >
            unavailable
          </span>
        ) : (
          <CountBadge>
            <span data-testid={`${testId}-count`}>
              {count === null ? "…" : count}
            </span>
            {detail ? ` ${detail}` : ""}
          </CountBadge>
        )}
      </div>
      {!failed && count !== null ? (
        <ShortList items={items} onMention={onMention} />
      ) : null}
    </div>
  );
}

export function ChatToolsSummary({
  profile,
  onMention,
}: {
  profile?: string;
  onMention?: (name: string) => void;
}) {
  // Each loader is a stable identity for its profile so `useRegistry` fires
  // exactly once per mount / profile switch.
  const toolsets = useRegistry<ToolsetInfo[]>(
    profile ?? "",
    () => api.getToolsets(profile),
  );
  const skills = useRegistry<SkillInfo[]>(profile ?? "", () =>
    api.getSkills(profile),
  );
  const plugins = useRegistry<PluginManifestResponse[]>("plugins", () =>
    api.getPlugins(),
  );
  const mcp = useRegistry<{ servers: McpServer[] }>("mcp", () =>
    api.getMcpServers(),
  );

  // Tools are counted across the real toolsets (the enabled ones are the
  // agent's live tool surface; disabled toolsets stay counted but are not
  // listed). Skills/plugins/MCP are counted directly.
  // Every registry is guarded on `Array.isArray`: a hostile/empty backend can
  // answer `{}` where a list is expected, and `?? []` does not catch that (an
  // object is not nullish) — which crashed the whole chat page on `.map`.
  const allToolsets = Array.isArray(toolsets.data) ? toolsets.data : [];
  const enabledToolsets = allToolsets.filter((t) => t.enabled);
  const toolNames = Array.from(
    new Set(
      enabledToolsets.flatMap((t) => (Array.isArray(t.tools) ? t.tools : [])),
    ),
  );
  const toolsetNames = enabledToolsets.map((t) => t.name);

  const skillRows = Array.isArray(skills.data) ? skills.data : [];
  const pluginRows = Array.isArray(plugins.data) ? plugins.data : [];
  const mcpRows = Array.isArray(mcp.data?.servers) ? mcp.data.servers : [];
  // Configured vs connected are different truths; the TUI banner counts the
  // latter. Surface both so the panel never overstates the live MCP surface.
  const mcpConnected = mcpRows.filter((s) => s.connected).length;

  return (
    <Card
      data-testid="chat-tools-summary"
      className="qchat__tools-card flex shrink-0 flex-col gap-2 px-3 py-2"
    >
      <span className="text-display text-xs tracking-wider text-text-tertiary">
        Agent tools
      </span>
      <Row
        label="Tools"
        href="/skills"
        testId="chat-tools-tools"
        count={toolNames.length}
        detail={`in ${enabledToolsets.length} toolsets`}
        items={toolsetNames}
        failed={toolsets.failed}
      />
      <Row
        label="Skills"
        href="/skills"
        testId="chat-tools-skills"
        count={skillRows.length}
        items={skillRows.map((s) => s.name)}
        failed={skills.failed}
        // Skill names are the composer affordance: the host inserts `@name`
        // as plain text. Tool rows list toolset (not tool) names and plugin /
        // MCP rows list inventory identities, so they stay plain text.
        onMention={onMention}
      />
      <Row
        label="Plugins"
        href="/plugins"
        testId="chat-tools-plugins"
        count={pluginRows.length}
        items={pluginRows.map((p) => p.label || p.name)}
        failed={plugins.failed}
      />
      <Row
        label="MCP servers"
        href="/mcp"
        testId="chat-tools-mcp"
        count={mcpRows.length}
        detail={`configured · ${mcpConnected} connected`}
        items={mcpRows.map((s) => s.name)}
        failed={mcp.failed}
      />
      <span className="text-[0.6875rem] leading-snug text-text-tertiary">
        Read-only summary. Manage on the{" "}
        <Link to="/skills" className="hover:underline">
          tools
        </Link>
        ,{" "}
        <Link to="/plugins" className="hover:underline">
          plugins
        </Link>{" "}
        and{" "}
        <Link to="/mcp" className="hover:underline">
          MCP
        </Link>{" "}
        pages.
      </span>
    </Card>
  );
}
