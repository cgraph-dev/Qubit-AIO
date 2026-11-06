/**
 * ControlCenter — the workbench's "Preferences, conversation tools, and system
 * controls" drawer (the vocabulary hermes-webui's Control Center used; this is
 * the grouping only, none of its code or state model).
 *
 * Scope is deliberately small: it reports what this page knows and lets you
 * drive this page's own panels. It does NOT re-home the dashboard's settings
 * surfaces — `/config`, `/env` and friends remain the settings homes, and the
 * links here just point at them.
 *
 * It also carries the honest route table: every endpoint the workbench reads is
 * listed with its live state, so a half-landed backend is legible at a glance
 * instead of showing up as four unrelated empty panels.
 */

import { Link } from "react-router";

import { Badge } from "@nous-research/ui/ui/components/badge";

import type { ApiResult, RouteProbe } from "@/lib/workbench-api";

function probeTone(result: ApiResult<unknown> | null): {
  tone: "success" | "warning" | "destructive" | "outline";
  text: string;
} {
  if (result === null) return { tone: "outline", text: "not read" };
  if (result.state === "ok") return { tone: "success", text: "live" };
  if (result.state === "not_available") return { tone: "warning", text: "not mounted" };
  return { tone: "destructive", text: "error" };
}

export function ControlCenter({
  probes,
  workspaceOpen,
  onToggleWorkspace,
  onRefresh,
}: {
  probes: readonly RouteProbe[];
  workspaceOpen: boolean;
  onToggleWorkspace: () => void;
  onRefresh: () => void;
}) {
  return (
    <aside
      aria-label="Control center"
      data-testid="workbench-control-center"
      className="flex h-full min-h-0 flex-col overflow-y-auto border-l border-border"
    >
      <div className="border-b border-border px-3 py-2">
        <h2 className="font-mondwest normal-case text-xs font-semibold tracking-wide text-text-secondary">
          Control center
        </h2>
        <p className="mt-0.5 text-[11px] text-text-tertiary">
          Preferences, conversation tools, and system controls.
        </p>
      </div>

      <section className="border-b border-border px-3 py-2" aria-label="Conversation tools">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-text-tertiary">
          Conversation tools
        </h3>
        <div className="mt-1.5 flex flex-col gap-1 text-xs">
          <label className="flex items-center justify-between gap-2">
            <span className="text-text-secondary">Workspace panel</span>
            <input
              type="checkbox"
              checked={workspaceOpen}
              onChange={onToggleWorkspace}
              aria-label="Show workspace panel"
            />
          </label>
          <button
            type="button"
            onClick={onRefresh}
            className="w-fit text-left text-text-secondary underline-offset-2 hover:text-foreground hover:underline"
          >
            Refresh all panels
          </button>
        </div>
      </section>

      <section className="border-b border-border px-3 py-2" aria-label="Backend routes">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-text-tertiary">
          Backend routes
        </h3>
        <ul className="mt-1.5 flex flex-col gap-1.5" data-testid="workbench-route-probes">
          {probes.map((probe) => {
            const { tone, text } = probeTone(probe.result);
            return (
              <li key={probe.path} className="flex flex-col gap-0.5 text-xs">
                <span className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-text-secondary">{probe.label}</span>
                  <Badge tone={tone} className="shrink-0 text-[10px]">
                    {text}
                  </Badge>
                </span>
                <span className="truncate font-mono-ui text-[10px] text-text-tertiary" title={probe.path}>
                  {probe.path}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="px-3 py-2" aria-label="System controls">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-text-tertiary">
          System controls
        </h3>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs">
          <li>
            <Link className="text-text-secondary hover:text-foreground hover:underline" to="/config">
              Agent configuration
            </Link>
          </li>
          <li>
            <Link className="text-text-secondary hover:text-foreground hover:underline" to="/sessions">
              Full session history
            </Link>
          </li>
          <li>
            <Link className="text-text-secondary hover:text-foreground hover:underline" to="/files">
              Workspace files page
            </Link>
          </li>
          <li>
            <Link className="text-text-secondary hover:text-foreground hover:underline" to="/qubit-chat">
              Voice chat surface
            </Link>
          </li>
        </ul>
        <p className="mt-2 text-[11px] text-text-tertiary">
          Voice (push-to-talk, wake word, read-aloud) lives on the voice chat
          surface. This workbench does not carry it.
        </p>
      </section>
    </aside>
  );
}

export default ControlCenter;
