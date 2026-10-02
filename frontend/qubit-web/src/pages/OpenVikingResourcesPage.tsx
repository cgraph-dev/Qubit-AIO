/**
 * Open Viking resources (`/open-viking/resources`).
 *
 * Qubit-owned resource index for the local context engine:
 * documentation, about, SDK & API, and agent integrations. Static
 * orientation content written for this console (no upstream text is
 * reproduced here); live entries arrive through the local bridge rows
 * under `/api/qubit/viking/<action>`.
 */

import { Link } from "react-router";
import { ArrowLeft, Blocks, BookOpen, Code, Info } from "lucide-react";

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
    body: "This dashboard addresses /api/qubit/viking/<action> bridge paths. Every row is LOCAL to the context engine; a row that still 404s renders its control disabled with reason until it lands.",
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
    <div className="flex flex-col gap-4 p-4" data-testid="open-viking-resources-page">
      <header className="flex items-center gap-3">
        <Link to="/open-viking" className="flex items-center gap-1 text-sm hover:underline">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Open Viking
        </Link>
        <h1 className="text-xl font-semibold">Resources</h1>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        {SECTIONS.map((section) => (
          <section
            key={section.key}
            id={section.key}
            className="rounded-md border p-4"
            aria-label={section.label}
            data-testid={`viking-resource-${section.key}`}
          >
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <section.icon className="h-4 w-4" aria-hidden /> {section.label}
            </h2>
            <p className="text-sm text-muted-foreground">{section.body}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
