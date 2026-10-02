/**
 * Open Viking Agent Hub (`/open-viking/agent-hub`).
 *
 * Qubit-owned view over the local context engine's bot sessions
 * and skills catalog via `@/lib/ocFamilyVikingApi` (`bot_sessions`,
 * `skills_list`). Read-only; each panel degrades independently — a
 * bridge row that still 404s renders honestly disabled with reason.
 *
 * Visual language matches SessionsPage/ChannelsPage: the layout owns the
 * page title (see ROUTE_TITLES), so this page renders no `h1`; panels
 * are `Card`s and page loading is a centered `Spinner`.
 */

import { useEffect, useState } from "react";
import { Bot, Wrench } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import {
  isBridgeRowMissing,
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
  const [sessionsMissing, setSessionsMissing] = useState(false);
  const [skillsMissing, setSkillsMissing] = useState(false);
  const [state, setState] = useState<"loading" | "done" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sessionsPayload, skillsPayload] = await Promise.all([
          listVikingBotSessions().catch((e: unknown) => {
            if (isBridgeRowMissing(e)) return { __missing: "bot_sessions" };
            throw e;
          }),
          listVikingSkills().catch((e: unknown) => {
            if (isBridgeRowMissing(e)) return { __missing: "skills" };
            throw e;
          }),
        ]);
        if (cancelled) return;
        if (
          typeof sessionsPayload === "object" &&
          sessionsPayload !== null &&
          "__missing" in (sessionsPayload as Record<string, unknown>)
        ) {
          setSessionsMissing(true);
        } else {
          setSessions(normalizeBotSessions(sessionsPayload));
        }
        if (
          typeof skillsPayload === "object" &&
          skillsPayload !== null &&
          "__missing" in (skillsPayload as Record<string, unknown>)
        ) {
          setSkillsMissing(true);
        } else {
          setSkills(normalizeVikingSkills(skillsPayload));
        }
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

  if (state === "loading") {
    return (
      <div
        className="flex items-center justify-center py-24"
        data-testid="open-viking-agent-hub-page"
      >
        <Spinner className="text-2xl text-primary" aria-label="Loading agent hub" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6" data-testid="open-viking-agent-hub-page">
      <p className="text-xs text-muted-foreground">
        Bot sessions and skills from the local context engine.
      </p>

      {state === "error" && (
        <p className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {state === "done" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card aria-label="Bot sessions">
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>
                <span className="flex items-center gap-2">
                  <Bot className="h-4 w-4" aria-hidden /> Bot sessions
                </span>
              </CardTitle>
              <Badge tone="secondary" className="text-xs tabular-nums">
                {sessionsMissing ? "—" : sessions.length}
              </Badge>
            </CardHeader>
            <CardContent className="pt-0">
              {sessionsMissing ? (
                <p
                  className="text-sm text-muted-foreground"
                  data-testid="viking-sessions-unavailable"
                >
                  Bot sessions couldn't be loaded — try Refresh.
                </p>
              ) : sessions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No bot sessions.</p>
              ) : (
                <ul className="flex flex-col gap-2" data-testid="viking-bot-sessions">
                  {sessions.map((s) => (
                    <li key={s.id} className="border border-border p-2 text-sm">
                      <p className="font-medium">{s.title || s.id}</p>
                      <p className="font-courier text-xs text-muted-foreground">
                        {s.id}
                      </p>
                      {s.state && (
                        <p className="text-xs text-muted-foreground">
                          state: {s.state}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card aria-label="Skills">
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>
                <span className="flex items-center gap-2">
                  <Wrench className="h-4 w-4" aria-hidden /> Skills
                </span>
              </CardTitle>
              <Badge tone="secondary" className="text-xs tabular-nums">
                {skillsMissing ? "—" : skills.length}
              </Badge>
            </CardHeader>
            <CardContent className="pt-0">
              {skillsMissing ? (
                <p
                  className="text-sm text-muted-foreground"
                  data-testid="viking-skills-unavailable"
                >
                  Skills couldn't be loaded — try Refresh.
                </p>
              ) : skills.length === 0 ? (
                <p className="text-sm text-muted-foreground">No skills.</p>
              ) : (
                <ul className="flex flex-col gap-2" data-testid="viking-skills">
                  {skills.map((s) => (
                    <li key={s.id} className="border border-border p-2 text-sm">
                      <p className="font-medium">{s.title || s.id}</p>
                      {s.description && (
                        <p className="text-xs text-muted-foreground break-words">
                          {s.description}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
