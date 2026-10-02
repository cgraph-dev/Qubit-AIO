/**
 * Open Viking resources (`/open-viking/resources`).
 *
 * Qubit-owned resource index for the local context engine:
 * documentation, about, SDK & API, and agent integrations. Static
 * orientation content written for this console (no upstream text is
 * reproduced here); live entries arrive through the local bridge rows
 * under `/api/qubit/viking/<action>`.
 *
 * Visual language matches SessionsPage/ChannelsPage: the layout owns the
 * page title (see ROUTE_TITLES), so this page renders no `h1`; sections
 * are `Card`s.
 */

import { Blocks, BookOpen, Code, Info } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";

const SECTIONS = [
  {
    key: "documentation",
    label: "Documentation",
    icon: BookOpen,
    body: "Start with the Playground, then Retrieval: pick a context-tree node, preview it, and use Find related context before adding anything new.",
  },
  {
    key: "about",
    label: "About",
    icon: Info,
    body: "Open Viking runs on Qubit's local context engine — sessions, skills, memory, and resources served by this dashboard's own bridge. No external server is involved.",
  },
  {
    key: "sdk-api",
    label: "SDK & API",
    icon: Code,
    body: "Every action on this console runs against the local context engine — nothing leaves this machine. If a control can't reach the engine it shows as unavailable with the reason.",
  },
  {
    key: "agent-integrations",
    label: "Agent Integrations",
    icon: Blocks,
    body: "Bot sessions and skills live under Agent Hub. Writes (resources, memories) are staged and confirm-gated — nothing sends silently.",
  },
];

export default function OpenVikingResourcesPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="open-viking-resources-page">
      <p className="text-xs text-muted-foreground">
        Orientation for the local context engine console — start here before
        the Playground.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        {SECTIONS.map((section) => (
          <Card
            key={section.key}
            id={section.key}
            aria-label={section.label}
            data-testid={`viking-resource-${section.key}`}
          >
            <CardHeader>
              <CardTitle>
                <span className="flex items-center gap-2">
                  <section.icon className="h-4 w-4" aria-hidden /> {section.label}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <p className="text-sm text-muted-foreground">{section.body}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
