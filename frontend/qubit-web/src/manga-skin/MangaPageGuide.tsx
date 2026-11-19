import { useSyncExternalStore } from "react";
import { useLocation } from "react-router";
import { useI18n } from "@/i18n";
import { currentMangaEnabled, mangaRouteEligible, subscribeManga } from "./manga-store";

/** A single sentence answering "what can I do here?" — no backend jargon. */
export const MANGA_PAGE_GUIDES: Readonly<Record<string, string>> = {
  "/qubit-chat": "Ask Qubit a question, continue a conversation, or choose an assistant.",
  "/sessions": "Find an earlier conversation and pick up where you left off.",
  "/webchat": "See conversations from your connected messaging services.",
  "/files": "Browse the files and results Qubit can work with.",
  "/analytics": "See your usage, activity, and estimated AI costs.",
  "/models": "Choose the AI models Qubit uses for different tasks.",
  "/logs": "Check what happened, especially when something did not work.",
  "/cron": "Tell Qubit what to do, when to do it, and where to send the result.",
  "/qubit-scheduler": "Schedule work for Qubit to do automatically.",
  "/qubit-tasks": "See tasks, their progress, and what needs attention.",
  "/qubit-taskflows": "Connect tasks into repeatable, multi-step workflows.",
  "/qubit-task-inbox": "Review tasks and requests waiting for you.",
  "/qubit-board-admin": "Adjust how your task boards are organized.",
  "/skills": "Give Qubit new abilities by enabling helpful skills.",
  "/plugins": "Add new features and manage installed extensions.",
  "/mcp": "Connect tools and services that Qubit can work with.",
  "/channels": "Choose where Qubit receives and sends messages.",
  "/webhooks": "Connect events from your other applications.",
  "/pairing": "Approve and manage trusted device connections.",
  "/profiles": "Switch between separate setups for different kinds of work.",
  "/config": "Change how Qubit behaves; leave unfamiliar settings at their defaults.",
  "/env": "Manage private connection keys and account settings.",
  "/system": "Check whether Qubit and its services are working correctly.",
  "/agents": "Manage assistants and assign work to them.",
  "/agent-workspace": "Review the files, sessions, and work belonging to your agents.",
  "/nodes": "Check which computers and services are connected.",
  "/devices": "View and manage trusted devices.",
  "/session-ops": "Manage active sessions and their background work.",
  "/worktrees": "Work on separate project changes without losing your main work.",
  "/checkpoints": "Review saved work and restore earlier states.",
  "/agent-cards": "Find and manage your available agent profiles.",
  "/qubit-bot": "Manage your Qubit bot and what it can do.",
  "/qubit-harness-router": "Choose how Qubit routes tasks to its assistants.",
  "/qubit-memory": "Choose which information Qubit can remember and retrieve.",
  "/open-viking": "Browse the knowledge and context available to your agents.",
  "/open-viking/context-store": "Find and organize saved context for your agents.",
  "/open-viking/retrieval": "Search the saved information Qubit can retrieve.",
  "/open-viking/uploads": "Add information for Qubit to use later.",
  "/open-viking/agent-hub": "Manage the agents using your saved knowledge.",
  "/open-viking/resources": "See the resources available to your agents.",
  "/qubit-talk": "Speak with Qubit and adjust voice features.",
  "/voice-edit": "Edit voice content and related settings.",
  "/qubit-tv": "Browse your media experience.",
  "/canvas": "Organize ideas and work in a visual space.",
  "/omniroute": "Set up connections and routes to AI providers.",
  "/kitchen": "Bring useful features from other tools into Qubit.",
  "/guide": "Learn what Qubit can do and how to get started.",
  "/docs": "Look up technical details when you need them.",
  "/admin/skills-review": "Review skills before making them available.",
  "/admin/mcp-calls": "Review calls made to your connected tools.",
  "/admin/users": "Manage access for the people using Qubit.",
  "/computer": "Work with connected computer environments.",
};

export function mangaGuideForPath(pathname: string): string | undefined {
  if (MANGA_PAGE_GUIDES[pathname]) return MANGA_PAGE_GUIDES[pathname];
  // Descendant views get the closest parent explanation until they define a
  // more useful, specific one. Never map the terminal or excluded routes.
  const prefixes = Object.keys(MANGA_PAGE_GUIDES).filter((p) => pathname.startsWith(p + "/"));
  prefixes.sort((a, b) => b.length - a.length);
  return prefixes.length ? MANGA_PAGE_GUIDES[prefixes[0]] : undefined;
}

/** Only visible in eligible Manga dashboard routes, never in /chat TUI. */
export function MangaPageGuide() {
  const { pathname } = useLocation();
  const { locale } = useI18n();
  const mangaOn = useSyncExternalStore(subscribeManga, currentMangaEnabled, () => false);
  // These optional tips are currently English copy. Avoid introducing an
  // untranslated strip into Qubit\u0027s other supported languages.
  const message = locale === "en" && mangaOn && mangaRouteEligible(pathname)
    ? mangaGuideForPath(pathname)
    : undefined;
  if (!message) return null;
  return (
    <div className="qm-page-guide" role="note" aria-label="About this page">
      <span className="qm-page-guide__star" aria-hidden="true">✦</span>
      <span>{message}</span>
    </div>
  );
}
