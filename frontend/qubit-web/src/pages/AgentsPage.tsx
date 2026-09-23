/**
 * A41 — Agents home. Read-only roster of the canonical Qubit worker lane and
 * runtime adapters from `GET /api/qubit/agents` (the same modules the CLI and
 * voice use). No second registry and no fabricated agents.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import { useI18n } from "@/i18n";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface AgentRow {
  id: string;
  kind?: string;
  route?: string;
  state?: string;
  enabled?: boolean;
  model?: string;
}

export default function AgentsPage() {
  const { t } = useI18n();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [authority, setAuthority] = useState<string>("");

  const load = useCallback(() => {
    setState("loading");
    setError(null);
    api
      .getAgents()
      .then((res) => {
        setAgents(res.agents ?? []);
        setAuthority(res.authority ?? "");
        setState("ready");
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className={cn("flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3 pt-1 sm:pt-2")}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-[0.08em]">
            {t.app.nav?.chat ? "Agents" : "Agents"}
          </h2>
          <p className="text-xs text-text-tertiary">
            Worker lane and runtime adapters for this Qubit runtime.
          </p>
        </div>
        <Button
          ghost
          size="sm"
          onClick={load}
          disabled={state === "loading"}
          aria-label="Refresh agents"
        >
          {state === "loading" ? <Spinner /> : "refresh"}
        </Button>
      </div>

      {state === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-tertiary">
          <Spinner className="shrink-0" /> Loading agents…
        </p>
      ) : state === "error" ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : agents.length === 0 ? (
        <p className="text-sm text-text-tertiary">No agents reported.</p>
      ) : (
        <ul className="flex flex-col gap-1" aria-label="Agent roster">
          {agents.map((a) => (
            <li
              key={a.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border bg-background-base/60 px-3 py-2 text-xs"
            >
              <span className="font-mono text-text-secondary">{a.id}</span>
              {a.kind ? (
                <span className="text-text-tertiary">{a.kind}</span>
              ) : null}
              {a.route ? (
                <span className="text-text-tertiary">route: {a.route}</span>
              ) : null}
              {a.model ? (
                <span className="text-text-tertiary">model: {a.model}</span>
              ) : null}
              <span
                className="ml-auto font-mono"
                data-enabled={a.enabled !== false}
              >
                {a.state ?? "OK"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {authority ? (
        <p className="text-[0.6875rem] text-text-tertiary">{authority}</p>
      ) : null}

      {/* Agent create / edit / activate / per-agent permissions, tools, and
          skills are a desktop-app surface (apps/desktop side-agents +
          subagents). The dashboard owns no agent-mutation API, so this is
          gated honestly rather than shown as a dead control. */}
      <div
        data-state="gated"
        className="border border-border bg-background-base/40 px-3 py-2 text-xs text-text-tertiary"
      >
        <p>
          Creating, editing, activating, and permissioning agents — plus their
          tools, skills, and ownership — is managed in the Qubit desktop app.
        </p>
        <p className="mt-1">
          This dashboard exposes the read-only roster above.
        </p>
      </div>
    </div>
  );
}
