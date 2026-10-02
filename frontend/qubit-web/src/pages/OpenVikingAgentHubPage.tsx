/**
 * Open Viking Agent Hub (`/open-viking/agent-hub`).
 *
 * Qubit-owned view over the external console server's bot sessions
 * and skills catalog via `@/lib/ocFamilyVikingApi` (`bot_sessions`,
 * `skills_list`). Read-only; unconfigured servers render the typed
 * SERVER_UNCONFIGURED state.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, Bot, Wrench } from "lucide-react";
import {
  isServerUnconfiguredPayload,
  listVikingBotSessions,
  listVikingSkills,
  normalizeBotSessions,
  normalizeVikingSkills,
  type VikingBotSession,
  type VikingSkillRow,
} from "@/lib/ocFamilyVikingApi";

export default function OpenVikingAgentHubPage() {
  const [sessions, setSessions] = useState<VikingBotSession[]>([]);
  const [skills, setSkills] = useState<VikingSkillRow[]>([]);
  const [state, setState] = useState<"loading" | "done" | "unconfigured" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sessionsPayload, skillsPayload] = await Promise.all([
          listVikingBotSessions(),
          listVikingSkills(),
        ]);
        if (cancelled) return;
        if (isServerUnconfiguredPayload(sessionsPayload) || isServerUnconfiguredPayload(skillsPayload)) {
          setState("unconfigured");
          return;
        }
        setSessions(normalizeBotSessions(sessionsPayload));
        setSkills(normalizeVikingSkills(skillsPayload));
        setState("done");
      } catch (e) {
        if (!cancelled) {
          setState("error");
          setError(e instanceof Error ? e.message : "agent hub unavailable");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="open-viking-agent-hub-page">
      <header className="flex items-center gap-3">
        <Link to="/open-viking" className="flex items-center gap-1 text-sm hover:underline">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Open Viking
        </Link>
        <h1 className="text-xl font-semibold">Agent Hub</h1>
      </header>

      {state === "loading" && <p className="text-sm text-muted-foreground">Loading…</p>}
      {state === "unconfigured" && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm" role="note">
          <strong>SERVER_UNCONFIGURED</strong> — no Open Viking server is configured.
        </div>
      )}
      {state === "error" && <p className="text-sm text-destructive">{error}</p>}

      {state === "done" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-md border p-3" aria-label="Bot sessions">
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <Bot className="h-4 w-4" aria-hidden /> VikingBot sessions ({sessions.length})
            </h2>
            {sessions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No bot sessions.</p>
            ) : (
              <ul className="flex flex-col gap-2" data-testid="viking-bot-sessions">
                {sessions.map((s) => (
                  <li key={s.id} className="rounded border p-2 text-sm">
                    <p className="font-medium">{s.title || s.id}</p>
                    <p className="font-mono text-xs text-muted-foreground">{s.id}</p>
                    {s.state && <p className="text-xs text-muted-foreground">state: {s.state}</p>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-md border p-3" aria-label="Skills">
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <Wrench className="h-4 w-4" aria-hidden /> Skills ({skills.length})
            </h2>
            {skills.length === 0 ? (
              <p className="text-sm text-muted-foreground">No skills.</p>
            ) : (
              <ul className="flex flex-col gap-2" data-testid="viking-skills">
                {skills.map((s) => (
                  <li key={s.id} className="rounded border p-2 text-sm">
                    <p className="font-medium">{s.title || s.id}</p>
                    {s.description && (
                      <p className="text-xs text-muted-foreground">{s.description}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
