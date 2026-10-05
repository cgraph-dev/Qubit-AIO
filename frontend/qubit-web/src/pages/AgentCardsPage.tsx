/**
 * /agent-cards — the typed agent card / artifact surface.
 *
 * Ported from the upstream mobile `agent-ui.tsx` (the 1882-line card renderer;
 * MIT, structural port — React + Tailwind, Qubit dark theme, upstream card
 * geometry). The heavy lifting lives in `src/components/om-agent-ui.tsx`.
 *
 * Backend (probed LIVE, real status codes):
 *   GET  /api/qubit/bot/tasks-list          200  read-only task ledger
 *   GET  /api/qubit/bot/ideas-list          200  suggestion ledger
 *   GET  /api/qubit/bot/goals-list          200  goals + schedules sidecar
 *   GET  /api/qubit/bot/alerts-list         200  run receipts
 *   GET  /api/qubit/bot/cards-get?card=..   200/404 rich card by id
 *   GET  /api/qubit/harness/harnesses       200  agent roster
 *
 * Task mutation, delegation, monitor polling and notification inbox have no
 * Qubit backend and render as honest, stated gaps (complete cards, truthful
 * empty states — never a dead button, never a fabricated success).
 */
import { useState } from "react";

import {
  AgentActivityScreen,
  AppsScreen,
  DelegateSheet,
  GoalsScreen,
  IdeasScreen,
  NotificationsSheet,
  TaskDetail,
  useAgentSurface,
} from "@/components/om-agent-ui";
import type { AgentTaskRow } from "@/lib/agentSurfaceApi";
import { cn } from "@/lib/utils";

type CardTab = "activity" | "ideas" | "goals" | "notifications" | "apps";

const TABS: ReadonlyArray<{ id: CardTab; label: string }> = [
  { id: "activity", label: "Activity" },
  { id: "ideas", label: "Ideas" },
  { id: "goals", label: "Goals" },
  { id: "notifications", label: "Notifications" },
  { id: "apps", label: "Apps" },
];

export default function AgentCardsPage() {
  const [tab, setTab] = useState<CardTab>("activity");
  const [selected, setSelected] = useState<AgentTaskRow | null>(null);
  const surface = useAgentSurface();

  return (
    <main
      className="qb-upstream settings-page settings-page--wide"
      aria-label="Agent cards"
    >
      <section aria-label="Agent cards toolbar" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Agent work &amp; artifacts</h2>
            <p className="settings-section__desc">
              Typed cards for the agent's tasks, ideas, goals and receipts —
              rendered from the live Qubit backends below.
            </p>
          </div>
        </div>
        <div
          className="settings-section__actions"
          role="tablist"
          aria-label="Agent card panels"
        >
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              aria-label={`Show ${item.label} panel`}
              onClick={() => setTab(item.id)}
              className={cn("btn btn--ghost btn--sm", tab === item.id && "active")}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>

      <section aria-label="Agent card panel" className="settings-section">
        {tab === "activity" ? (
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
            <div className="min-w-0 flex-1">
              <AgentActivityScreen
                tasks={surface.tasks}
                loading={surface.loading}
                error={surface.error}
                onReload={surface.reload}
                context={surface.context}
              />
            </div>
            <aside className="w-full shrink-0 lg:w-[360px]">
              <div className="rounded-[23px] border border-border bg-card p-5">
                {selected ? (
                  <TaskDetail task={selected} onClose={() => setSelected(null)} />
                ) : (
                  <div className="flex flex-col gap-3">
                    <p className="text-[15px] font-semibold">Task detail</p>
                    <p className="text-[13px] text-muted-foreground">
                      Select a task from the ledger to inspect its state and any
                      stored rich card.
                    </p>
                    <ul className="flex flex-col gap-1">
                      {surface.tasks.slice(0, 6).map((task) => (
                        <li key={task.id}>
                          <button
                            type="button"
                            aria-label={`Inspect task: ${task.title || task.id}`}
                            onClick={() => setSelected(task)}
                            className="w-full truncate rounded-[10px] px-2 py-2 text-left text-[13px] hover:bg-[color:var(--background-base)]"
                          >
                            {task.title || task.id}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </aside>
          </div>
        ) : tab === "ideas" ? (
          <IdeasScreen />
        ) : tab === "goals" ? (
          <GoalsScreen />
        ) : tab === "notifications" ? (
          <NotificationsSheet />
        ) : (
          <AppsScreen context={surface.context} />
        )}
      </section>

      <section aria-label="Delegation" className="settings-section">
        <div className="settings-section__copy settings-section__header">
          <h2 className="settings-section__heading">Hand over an outcome</h2>
          <p className="settings-section__desc">
            Delegate a task the agent will plan and run on the server.
          </p>
        </div>
        <DelegateSheet />
      </section>
    </main>
  );
}
