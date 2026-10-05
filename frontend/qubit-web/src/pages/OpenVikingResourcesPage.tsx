/**
 * Open Viking resources (`/open-viking/resources`).
 *
 * Qubit-owned resource index for the local context engine: orientation
 * sections plus a live engine status read from the bridge
 * (`/api/qubit/viking/status`). Every action navigates to a real console
 * view; nothing is faked. Static orientation copy is written for this
 * console — no upstream text is reproduced here.
 *
 * Structure follows the shared settings contract
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`)
 * with the layout owning the page title (see ROUTE_TITLES) — no `h1` here.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Blocks, BookOpen, ChevronRight, Code, FolderUp, Info, Search } from "lucide-react";
import {
  SettingsEmpty,
  SettingsPageIntro,
  SettingsRow,
  SettingsSection,
  SettingsStatus,
} from "@/components/viking-settings";
import {
  getVikingStatus,
  isBridgeRowMissing,
  normalizeVikingStatus,
} from "@/lib/ocFamilyVikingApi";

interface ResourceSection {
  key: string;
  label: string;
  icon: typeof BookOpen;
  body: string;
  to: string;
  cta: string;
}

const SECTIONS: ResourceSection[] = [
  {
    key: "documentation",
    label: "Documentation",
    icon: BookOpen,
    body: "Start with the Playground, then Retrieval: pick a context-tree node, preview it, and use Find related context before adding anything new.",
    to: "/open-viking",
    cta: "Open Playground",
  },
  {
    key: "about",
    label: "About",
    icon: Info,
    body: "Open Viking runs on Qubit's local context engine — sessions, skills, memory, and resources served by this dashboard's own bridge. No external server is involved.",
    to: "/open-viking/retrieval",
    cta: "Search the engine",
  },
  {
    key: "sdk-api",
    label: "SDK & API",
    icon: Code,
    body: "Every action on this console runs against the local context engine — nothing leaves this machine. If a control can't reach the engine it shows as unavailable with the reason.",
    to: "/open-viking/uploads",
    cta: "Ingest a resource",
  },
  {
    key: "agent-integrations",
    label: "Agent Integrations",
    icon: Blocks,
    body: "Bot sessions and skills live under Agent Hub. Writes (resources, memories) are staged and confirm-gated — nothing sends silently.",
    to: "/open-viking/agent-hub",
    cta: "Open Agent Hub",
  },
];

/** Green-path entry rows: the four console views this index links to. */
const CONSOLE_LINKS = [
  { label: "Playground", desc: "Browse the context tree and preview files.", to: "/open-viking", icon: BookOpen },
  { label: "Retrieval", desc: "Keyword search over the context store.", to: "/open-viking/retrieval", icon: Search },
  { label: "Uploads", desc: "Stage a resource or memory (confirm-gated).", to: "/open-viking/uploads", icon: FolderUp },
  { label: "Agent Hub", desc: "Bot sessions and the skills catalog.", to: "/open-viking/agent-hub", icon: Blocks },
];

export default function OpenVikingResourcesPage() {
  const navigate = useNavigate();
  const [connection, setConnection] = useState<"connected" | "unknown" | "checking" | "unavailable">(
    "checking",
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await getVikingStatus();
        if (cancelled) return;
        const status = normalizeVikingStatus(payload);
        setConnection(status.connection === "connected" ? "connected" : "unknown");
      } catch (e) {
        if (cancelled) return;
        setConnection(isBridgeRowMissing(e) ? "unavailable" : "unknown");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="qb-upstream viking-settings settings-page settings-page--wide" data-testid="open-viking-resources-page">
      <SettingsPageIntro currentPath="/open-viking/resources" showSubnav>
        Orientation for the local context engine console — start here before the
        Playground.
      </SettingsPageIntro>

      <SettingsSection
        title="Console map"
        description="The four views this index covers. Pick one to jump straight in."
        count={CONSOLE_LINKS.length}
        ariaLabel="Console map"
      >
        {CONSOLE_LINKS.map((link) => (
          <SettingsRow
            key={link.label}
            as="button"
            title={link.label}
            description={link.desc}
            onClick={() => void navigate(link.to)}
            ariaLabel={`Open ${link.label}`}
            testId={`viking-console-link-${link.label.toLowerCase().replace(/\s+/g, "-")}`}
          />
        ))}
      </SettingsSection>

      {SECTIONS.map((section) => (
        <SettingsSection
          key={section.key}
          title={section.label}
          ariaLabel={section.label}
        >
          <SettingsRow
            title={
              <span className="flex items-center gap-2">
                <section.icon className="h-4 w-4" aria-hidden /> {section.label}
              </span>
            }
            description={section.body}
            control={
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => void navigate(section.to)}
              >
                {section.cta} <ChevronRight className="h-4 w-4" aria-hidden />
              </button>
            }
            testId={`viking-resource-${section.key}`}
          />
        </SettingsSection>
      ))}

      <SettingsSection
        title="Engine"
        description="Live status of the local context engine backing this console."
        ariaLabel="Engine status"
      >
        {connection === "checking" ? (
          <SettingsEmpty>Checking the local engine…</SettingsEmpty>
        ) : (
          <SettingsRow
            title="Local context engine"
            description={
              connection === "connected"
                ? "Connected — reads and confirm-gated writes are served locally."
                : connection === "unavailable"
                  ? "Status bridge row missing — the engine isn't reachable from this dashboard yet."
                  : "Reachable but reported an unknown state."
            }
            control={
              <SettingsStatus
                tone={
                  connection === "connected"
                    ? "ok"
                    : connection === "unavailable"
                      ? "danger"
                      : "warn"
                }
              >
                {connection === "connected"
                  ? "connected"
                  : connection === "unavailable"
                    ? "unavailable"
                    : "unknown"}
              </SettingsStatus>
            }
          />
        )}
      </SettingsSection>
    </div>
  );
}
