/**
 * Open Viking Agent Hub (`/open-viking/agent-hub`).
 *
 * Qubit-owned console over the local context engine's bot sessions and
 * skills catalog via `@/lib/ocFamilyVikingApi` (`bot_sessions`,
 * `skills_list`). Read-only; each panel degrades independently — a bridge
 * row that still 404s renders honestly disabled with reason.
 *
 * Structure follows the shared settings contract
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`)
 * with the layout owning the page title (see ROUTE_TITLES) — no `h1` here.
 * The skills catalog is filterable so a large catalog stays navigable.
 */

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { Bot, Search, Wrench } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import {
  SettingsEmpty,
  SettingsLoadingSkeleton,
  SettingsPageIntro,
  SettingsRow,
  SettingsSection,
} from "@/components/viking-settings";
import { copyTextToClipboard } from "@/lib/clipboard";
import {
  isBridgeRowMissing,
  listVikingBotSessions,
  listVikingSkills,
  normalizeBotSessions,
  normalizeVikingSkills,
  type VikingBotSession,
  type VikingSkillRow,
} from "@/lib/ocFamilyVikingApi";

/** Skills rendered per page before the "Show more" control appears. */
const SKILL_PAGE = 24;

export default function OpenVikingAgentHubPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<VikingBotSession[]>([]);
  const [skills, setSkills] = useState<VikingSkillRow[]>([]);
  const [sessionsMissing, setSessionsMissing] = useState(false);
  const [skillsMissing, setSkillsMissing] = useState(false);
  const [state, setState] = useState<"loading" | "done" | "error">("loading");
  const [error, setError] = useState("");
  const [skillFilter, setSkillFilter] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<VikingSkillRow | null>(null);
  const [skillLimit, setSkillLimit] = useState(SKILL_PAGE);

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

  const filteredSkills = useMemo(() => {
    const needle = skillFilter.trim().toLowerCase();
    if (!needle) return skills;
    return skills.filter(
      (s) =>
        (s.title ?? "").toLowerCase().includes(needle) ||
        s.id.toLowerCase().includes(needle) ||
        (s.description ?? "").toLowerCase().includes(needle),
    );
  }, [skills, skillFilter]);

  const visibleSkills = useMemo(
    () => filteredSkills.slice(0, skillLimit),
    [filteredSkills, skillLimit],
  );

  if (state === "loading") {
    return (
      <div className="qb-upstream viking-settings settings-page settings-page--wide" data-testid="open-viking-agent-hub-page">
        <SettingsPageIntro currentPath="/open-viking/agent-hub" showSubnav>
          Bot sessions and the skills catalog from the local context engine.
        </SettingsPageIntro>
        <SettingsSection title="Agent Hub" description="Loading bot sessions and the skills catalog…">
          <SettingsLoadingSkeleton rows={4} />
        </SettingsSection>
      </div>
    );
  }

  return (
    <div className="qb-upstream viking-settings settings-page settings-page--wide" data-testid="open-viking-agent-hub-page">
      <SettingsPageIntro currentPath="/open-viking/agent-hub" showSubnav>
        Bot sessions and the skills catalog from the local context engine.
      </SettingsPageIntro>

      {state === "error" && (
        <p className="callout danger" role="alert">
          {error}
        </p>
      )}

      {state === "done" && (
        <>
          <SettingsSection
            title="Bot sessions"
            description="Conversation sessions recorded by the local engine."
            count={sessionsMissing ? undefined : sessions.length}
            actions={
              <Button
                size="sm"
                outlined
                className="uppercase"
                onClick={() => void navigate("/open-viking")}
              >
                Open Playground
              </Button>
            }
            ariaLabel="Bot sessions"
          >
            {sessionsMissing ? (
              <SettingsRow
                title="Bot sessions unavailable"
                description="Bot-sessions bridge row missing — the local engine isn't serving this list yet."
                testId="viking-sessions-unavailable"
              />
            ) : sessions.length === 0 ? (
              <SettingsEmpty>No bot sessions recorded yet.</SettingsEmpty>
            ) : (
              <div data-testid="viking-bot-sessions">
                {sessions.map((s) => (
                  <SettingsRow
                    key={s.id}
                    as="button"
                    title={s.title || s.id}
                    description={
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="settings-row__value settings-row__value--mono">{s.id}</span>
                        {s.state && <span>state: {s.state}</span>}
                      </span>
                    }
                    onClick={() => void navigate("/open-viking")}
                    ariaLabel={`Open session ${s.title || s.id} in the Playground`}
                  />
                ))}
              </div>
            )}
          </SettingsSection>

          <SettingsSection
            title="Skills"
            description="Installed skill packages available to the bot."
            count={skillsMissing ? undefined : filteredSkills.length}
            actions={
              skillsMissing ? undefined : (
                <div className="flex items-center gap-2">
                  <Search className="h-4 w-4 text-muted" aria-hidden />
                  <Input
                    className="settings-input"
                    placeholder="Filter skills…"
                    value={skillFilter}
                    onChange={(e) => setSkillFilter(e.target.value)}
                    aria-label="Filter skills"
                  />
                </div>
              )
            }
            ariaLabel="Skills"
          >
            {skillsMissing ? (
              <SettingsRow
                title="Skills unavailable"
                description="Skills bridge row missing — the local catalog isn't reachable from this dashboard yet."
                testId="viking-skills-unavailable"
              />
            ) : skills.length === 0 ? (
              <SettingsEmpty>No skills in the catalog.</SettingsEmpty>
            ) : filteredSkills.length === 0 ? (
              <SettingsEmpty>No skills match “{skillFilter.trim()}”.</SettingsEmpty>
            ) : (
              <details open>
                <summary className="settings-row__desc cursor-pointer px-3 py-2">
                  {filteredSkills.length} skill{filteredSkills.length === 1 ? "" : "s"} — {skillFilter.trim() ? "filtered" : "expand"} to browse
                </summary>
                <div data-testid="viking-skills">
                  {visibleSkills.map((s) => (
                    <SettingsRow
                      key={s.id}
                      as="button"
                      title={
                        <span className="flex items-center gap-2">
                          <Wrench className="h-4 w-4" aria-hidden /> {s.title || s.id}
                        </span>
                      }
                      description={
                        <span className="flex flex-col gap-1">
                          {s.description && <span>{s.description}</span>}
                          <span className="settings-row__value settings-row__value--mono">{s.id}</span>
                        </span>
                      }
                      onClick={() => setSelectedSkill((prev) => (prev?.id === s.id ? null : s))}
                      ariaLabel={`Select skill ${s.title || s.id}`}
                    />
                  ))}
                </div>
                {filteredSkills.length > visibleSkills.length && (
                  <SettingsRow
                    className="settings-row--actions"
                    control={
                      <Button
                        size="sm"
                        outlined
                        className="uppercase"
                        onClick={() => setSkillLimit((n) => n + SKILL_PAGE)}
                      >
                        Show more ({filteredSkills.length - visibleSkills.length} remaining)
                      </Button>
                    }
                  />
                )}
              </details>
            )}
          </SettingsSection>

          {selectedSkill && (
            <SettingsSection
              title="Selected skill"
              description={selectedSkill.title || selectedSkill.id}
              ariaLabel="Selected skill"
            >
              <SettingsRow
                title={
                  <span className="flex items-center gap-2">
                    <Wrench className="h-4 w-4" aria-hidden /> {selectedSkill.title || selectedSkill.id}
                  </span>
                }
                description={selectedSkill.description || "No description provided."}
                control={
                  <Button
                    size="sm"
                    outlined
                    className="uppercase"
                    onClick={() => void copyTextToClipboard(selectedSkill.id)}
                  >
                    Copy id
                  </Button>
                }
              />
              <SettingsRow
                title="Skill id"
                control={
                  <span className="settings-row__value settings-row__value--mono">{selectedSkill.id}</span>
                }
              />
            </SettingsSection>
          )}

          <SettingsSection
            title="Agent surface"
            description="Where these entities are used across the console."
            ariaLabel="Agent surface"
          >
            <SettingsRow
              as="button"
              title={
                <span className="flex items-center gap-2">
                  <Bot className="h-4 w-4" aria-hidden /> Bot sessions
                </span>
              }
              description="Browse recorded sessions in the Playground context tree."
              onClick={() => void navigate("/open-viking")}
            />
            <SettingsRow
              as="button"
              title={
                <span className="flex items-center gap-2">
                  <Search className="h-4 w-4" aria-hidden /> Retrieval
                </span>
              }
              description="Search across memories, sessions, and skills."
              onClick={() => void navigate("/open-viking/retrieval")}
            />
            <SettingsRow
              as="button"
              title={
                <span className="flex items-center gap-2">
                  <Wrench className="h-4 w-4" aria-hidden /> Add a memory
                </span>
              }
              description="Stage a note into the context store (confirm-gated)."
              onClick={() => void navigate("/open-viking/uploads?tab=memory")}
            />
          </SettingsSection>
        </>
      )}
    </div>
  );
}
