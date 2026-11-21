import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type FocusEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { stageChrome } from "@/lib/stageReveal";
import {
  Routes,
  Route,
  NavLink,
  Navigate,
  useLocation,
  useNavigate,
} from "react-router";
import {
  Activity,
  BarChart3,
  BookOpen,
  Bot,
  ChefHat,
  ChevronRight,
  Clock,
  Code,
  Cpu,
  Database,
  Download,
  Eye,
  FolderOpen,
  FileText,
  Globe,
  Heart,
  KeyRound,
  Menu,
  MessageSquare,
  Mic,
  Monitor,
  Network,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  PanelsTopLeft,
  Plug,
  Puzzle,
  Radio,
  RotateCw,
  Search,
  Settings,
  Shield,
  ShieldCheck,
  Ship,
  Sparkles,
  Star,
  Terminal,
  Tv,
  Upload,
  Users,
  Webhook,
  Workflow,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { Button, SakugaLoading, SakugaToastHost, emitFxAt } from "@/sakuga/ui";
import { SelectionSwitcher } from "@nous-research/ui/ui/components/selection-switcher";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { cn } from "@/lib/utils";
import { useBackdropPhase } from "@/lib/useBackdropPhase";
import { QubitMark } from "@/components/QubitMark";
import { QubitBrandSparks } from "@/components/QubitBrandSparks";
import { QubitSidebarTitle } from "@/components/QubitSidebarTitle";
import { SidebarFooter } from "@/components/SidebarFooter";
import { SidebarFrame } from "@/components/SidebarFrame";
import { ImportantTooltip } from "@/components/ImportantTooltip";
import { QmSystemActionButton } from "@/components/qm-system-action/QmSystemActionButton";
import { Q5TGatewayFrame } from "@/components/Q5TGatewayFrame";
import { Q5TSystemTitle } from "@/components/Q5TSystemTitle";
import { SidebarStatusStrip, gatewayLine } from "@/components/SidebarStatusStrip";
import { useBelowBreakpoint } from "@nous-research/ui/hooks/use-below-breakpoint";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import { AuthWidget } from "@/components/AuthWidget";
import { PageHeaderProvider } from "@/contexts/PageHeaderProvider";
import { ProfileProvider } from "@/contexts/ProfileProvider";
import { useProfileScope } from "@/contexts/useProfileScope";
import { ProfileSwitcher } from "@/components/ProfileSwitcher";
import { ProfileScopeBanner } from "@/components/ProfileScopeBanner";
import { MemoryPressureBanner } from "@/components/MemoryPressureBanner";
import { PageErrorBoundary } from "@/components/PageErrorBoundary";
import { useSystemActions } from "@/contexts/useSystemActions";
import type { SystemAction } from "@/contexts/system-actions-context";
import { SidebarTooltip } from "@/components/SidebarTooltip";
import { navDescription, sectionizeNav, MANGA_SIDEBAR_SECTIONS } from "@/lib/sidebar-sections";
import { isMangaThemeName } from "@/themes/presets";
import { mangaRouteEligible } from "@/manga-skin/manga-store";
import { MangaStudioBrandAtom, MangaStudioTitleOrnaments } from "@/manga-skin/MangaStudioArt";
import "@/styles/qplate.css";
import "@/styles/sidebar-studio.css";
import "@/styles/qubit-mark.css";

// The ambient backdrop is a looping video, so it no longer drags three.js
// (~1 MB) into the shell chunk. Lazy stays: a missing asset or a failed chunk
// must never block or break the shell mount.
const QubitAmbient = lazy(() =>
  import("@/components/QubitAmbient").then((m) => ({
    default: m.QubitAmbientMemo,
  })),
);

class AmbientFailSafe extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

// Route pages are lazy-loaded so the initial dashboard shell does not pay for
// every admin surface (and heavy deps like xterm) up front.
const ConfigPage = lazy(() => import("@/pages/ConfigPage"));
const DocsPage = lazy(() => import("@/pages/DocsPage"));
const GuidePage = lazy(() => import("@/pages/GuidePage"));
const EnvPage = lazy(() => import("@/pages/EnvPage"));
const FilesPage = lazy(() => import("@/pages/FilesPage"));
const SessionsPage = lazy(() => import("@/pages/SessionsPage"));
const LogsPage = lazy(() => import("@/pages/LogsPage"));
const AnalyticsPage = lazy(() => import("@/pages/AnalyticsPage"));
const ModelsPage = lazy(() => import("@/pages/ModelsPage"));
const CronPage = lazy(() => import("@/pages/CronPage"));
const ProfilesPage = lazy(() => import("@/pages/ProfilesPage"));
const ProfileBuilderPage = lazy(() => import("@/pages/ProfileBuilderPage"));
const SkillsPage = lazy(() => import("@/pages/SkillsPage"));
const PluginsPage = lazy(() => import("@/pages/PluginsPage"));
const McpPage = lazy(() => import("@/pages/McpPage"));
const PairingPage = lazy(() => import("@/pages/PairingPage"));
const ChannelsPage = lazy(() => import("@/pages/ChannelsPage"));
const WebhooksPage = lazy(() => import("@/pages/WebhooksPage"));
const SystemPage = lazy(() => import("@/pages/SystemPage"));
const AgentsPage = lazy(() => import("@/pages/AgentsPage"));
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const TerminalChatPage = lazy(() => import("@/pages/TerminalChatPage"));
const VoiceEditPage = lazy(() => import("@/pages/VoiceEditPage"));
// Qubit family pages (integrator lane): separate Qubit surfaces wired to the
// gateway backends through GET/POST/PUT /api/qubit/<family>/<action>.
const AgentWorkspacePage = lazy(() => import("@/pages/AgentWorkspacePage"));
const NodesPage = lazy(() => import("@/pages/NodesPage"));
const DevicesPage = lazy(() => import("@/pages/DevicesPage"));
const SessionOpsPage = lazy(() => import("@/pages/SessionOpsPage"));
const WorktreesPage = lazy(() => import("@/pages/WorktreesPage"));
const CheckpointsPage = lazy(() => import("@/pages/CheckpointsPage"));
const BoardAdminPage = lazy(() => import("@/pages/BoardAdminPage"));
const TaskInboxPage = lazy(() => import("@/pages/TaskInboxPage"));
const TalkPage = lazy(() => import("@/pages/TalkPage"));
const SchedulerMediaPage = lazy(() => import("@/pages/SchedulerMediaPage"));
const SkillsReviewPage = lazy(() => import("@/pages/SkillsReviewPage"));
const McpCallsPage = lazy(() => import("@/pages/McpCallsPage"));
const UsersAdminPage = lazy(() => import("@/pages/UsersAdminPage"));
// Qubit family pages, second wave (integrator lane): separate Qubit surfaces
// wired to the gateway backends through GET/POST /api/qubit/<family>/<action>
// (webchat reuses the existing chat endpoints, no bridge rows).
const CanvasPage = lazy(() => import("@/pages/CanvasPage"));
const TaskflowsPage = lazy(() => import("@/pages/TaskflowsPage"));
const TasksPage = lazy(() => import("@/pages/TasksPage"));
const MemoryBackendsPage = lazy(() => import("@/pages/MemoryBackendsPage"));
const WebChatPage = lazy(() => import("@/pages/WebChatPage"));
// Qubit Bot + Harness Router + Open Viking console (integrator lane 3):
// built pages that were never registered — routes/nav land here, the single
// wiring owner for the sidebar route table.
const QubitBotPage = lazy(() => import("@/pages/QubitBotPage"));
const HarnessRouterPage = lazy(() => import("@/pages/HarnessRouterPage"));
// Open Viking (integrator lane 3): the EMBEDDED upstream studio — the real
// Volcengine SPA, iframed from 127.0.0.1:1933/studio. It supersedes the old
// group at the `/open-viking` route.
const OpenVikingStudioPage = lazy(
  () => import("@/pages/OpenVikingStudioPage"),
);
// The former "Open Viking" pages. They render Qubit's OWN local context engine
// (voicebridge/context_store.py, `connection: "local"`) — a DIFFERENT backend
// from the :1933 server the studio talks to. Unlisted from the sidebar (see
// BUILTIN_NAV_REST) but kept routed so nothing breaks and their tests still
// pass; reachable directly at these URLs.
const OpenVikingPage = lazy(() => import("@/pages/OpenVikingPage"));
const OpenVikingRetrievalPage = lazy(
  () => import("@/pages/OpenVikingRetrievalPage"),
);
const OpenVikingUploadsPage = lazy(
  () => import("@/pages/OpenVikingUploadsPage"),
);
const OpenVikingAgentHubPage = lazy(
  () => import("@/pages/OpenVikingAgentHubPage"),
);
const OpenVikingResourcesPage = lazy(
  () => import("@/pages/OpenVikingResourcesPage"),
);
// Agent console surfaces (integrator lane): ported computer workspace +
// typed agent card/artifact renderers.
const ComputerWorkspacePage = lazy(
  () => import("@/pages/ComputerWorkspacePage"),
);
const AgentCardsPage = lazy(() => import("@/pages/AgentCardsPage"));
// Kitchen: the intake surface for absorbing other agents/apps into Qubit.
const KitchenPage = lazy(() => import("@/pages/KitchenPage"));
// OmniRoute: local AI-gateway console (status + wiring, dashboard opens out).
const OmniRoutePage = lazy(() => import("@/pages/OmniRoutePage"));
// Qubit TV: streaming launcher + YouTube embed (web variant of the MIT
// HermesTV desktop plugin; DRM services open in the browser).
const QubitTvPage = lazy(() => import("@/pages/QubitTvPage"));
// Hidden component gallery for the vendored Sakuga VFX kit. No sidebar row:
// it exists so every control can be exercised in the built dashboard.
const SakugaGalleryPage = lazy(() => import("@/pages/SakugaGalleryPage"));
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { useI18n } from "@/i18n";
import type { Translations } from "@/i18n/types";
import { PluginPage, PluginSlot, usePlugins } from "@/plugins";
import type { PluginManifest } from "@/plugins";
import { useTheme } from "@/themes";
import { isDashboardEmbeddedChatEnabled } from "@/lib/dashboard-flags";
import { prefetchRoute } from "@/lib/route-prefetch";
import { latchChatActivation } from "@/lib/chat-activation";
import { isChatPath, isFullHeightPath, isNativeChatPath, NATIVE_CHAT_PATH, normalizeRoutePath } from "@/lib/chat-routes";
import { ROUTE_TITLES } from "@/lib/resolve-page-title";
import {
  parsePolicyRefusal,
  policyRefusalMessage,
  type PolicyRefusal,
} from "@/lib/policy-refusal";
import { api } from "@/lib/api";
import type { StatusResponse, UpdateCheckResponse } from "@/lib/api";
import { LatencySampler, measureLatency, formatLatency } from "@/lib/latency-sampler";

function RouteFallback({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      className="flex min-h-[60vh] w-full flex-1 items-center justify-center"
      aria-busy="true"
      aria-live="polite"
    >
      <SakugaLoading label={label} className="qsk-route-loading" />
    </div>
  );
}

function RootRedirect() {
  // Ruling 3 → Option A: the root lands on the Qubit chat workspace.
  return <Navigate to="/chat" replace />;
}

function UnknownRouteFallback({ pluginsLoading }: { pluginsLoading: boolean }) {
  if (pluginsLoading) {
    // Render nothing during the plugin-load window — a spinner here would just flash.
    return null;
  }
  return <Navigate to="/sessions" replace />;
}

/**
 * Hands the browser off to a page the PYTHON server owns, at the same path.
 *
 * `/qubit-workbench` is not a React page: `webui.py:_serve_workbench_ui` serves
 * a whole HTML document there (the ported upstream workbench — its own
 * HTML/CSS/JS, 223 KB) and redirects to `/qubit-workbench/session/<id>`. React
 * Router claims the path on the first client-side click, finds no matching
 * Route, and the `*` catch-all bounces the user to `/sessions` — measured live:
 * clicking the sidebar row from `/chat` (and from the workbench itself) landed
 * on `/sessions`, so the surface was unreachable by clicking anywhere.
 *
 * `window.location.replace` re-requests the path from the server, which is the
 * only way to reach a non-SPA document. Through the router it would be a
 * pushState that renders this component again in an endless loop, so a real
 * document navigation is the correct tool here, not a workaround.
 */
function ServerDocumentHandoff({ path }: { path: string }) {
  useEffect(() => {
    window.location.replace(path);
  }, [path]);
  return null;
}

const CHAT_NAV_ITEM: NavItem = {
  path: "/chat",
  labelKey: "chat",
  label: "Chat",
  icon: Terminal,
};

// `/qubit-chat` is the same chat surface under a Qubit-named route. It is a
// built-in route (not a plugin) so both chat paths render the single shared
// ChatPage host and can never diverge into a second implementation.
//
// WebChat hangs off it as a child: /qubit-chat is the voice + reports +
// work-panel surface (voice_edit/heartbeat.py owns its in-page Right-Ctrl
// push-to-talk), and the `/webchat` slash command + "list/show/resume web
// chat" voice commands drive the sibling minimal chat. Grouping them keeps the
// three chat surfaces adjacent without hiding either.
const QUBIT_CHAT_NAV_ITEM: NavItem = {
  path: "/qubit-chat",
  labelKey: "qubitChat",
  label: "Qubit Chat",
  icon: Terminal,
  exactEnd: true,
  children: [
    { path: "/webchat", labelKey: "webchat", label: "WebChat", icon: MessageSquare },
  ],
};

/**
 * Built-in routes except /chat.  Chat is rendered persistently (outside
 * <Routes>) when embedded — see the persistent chat host block rendered
 * inline near the bottom of this file — so the PTY child, WebSocket,
 * and xterm instance survive when the user visits another tab and comes
 * back.  A `display:none` toggle hides the terminal without unmounting.
 * The host itself is still deferred until the first /chat visit so the
 * xterm chunk is not downloaded on unrelated pages.  Routing still owns
 * the URL so /chat deep-links, browser back/forward, and nav highlight
 * keep working.
 */
const BUILTIN_ROUTES_CORE: Record<string, ComponentType> = {
  "/": RootRedirect,
  "/sessions": SessionsPage,
  "/files": FilesPage,
  "/analytics": AnalyticsPage,
  "/models": ModelsPage,
  "/logs": LogsPage,
  "/cron": CronPage,
  "/skills": SkillsPage,
  "/plugins": PluginsPage,
  "/mcp": McpPage,
  "/pairing": PairingPage,
  "/channels": ChannelsPage,
  "/webhooks": WebhooksPage,
  "/system": SystemPage,
  "/agents": AgentsPage,
  "/profiles": ProfilesPage,
  "/profiles/new": ProfileBuilderPage,
  "/config": ConfigPage,
  "/env": EnvPage,
  "/docs": DocsPage,
  "/guide": GuidePage,
  "/voice-edit": VoiceEditPage,
  "/agent-workspace": AgentWorkspacePage,
  "/nodes": NodesPage,
  "/devices": DevicesPage,
  "/session-ops": SessionOpsPage,
  "/worktrees": WorktreesPage,
  "/checkpoints": CheckpointsPage,
  "/qubit-board-admin": BoardAdminPage,
  "/qubit-task-inbox": TaskInboxPage,
  "/qubit-talk": TalkPage,
  "/qubit-scheduler": SchedulerMediaPage,
  "/admin/skills-review": SkillsReviewPage,
  "/admin/mcp-calls": McpCallsPage,
  "/admin/users": UsersAdminPage,
  "/canvas": CanvasPage,
  "/qubit-taskflows": TaskflowsPage,
  "/qubit-tasks": TasksPage,
  "/qubit-memory": MemoryBackendsPage,
  "/webchat": WebChatPage,
  // Integrator lane 3: page-documented paths (the pages link to these;
  // short aliases like /bot or /viking serve no in-page link and stay out).
  "/qubit-bot": QubitBotPage,
  "/qubit-harness-router": HarnessRouterPage,
  // `/open-viking` is the EMBEDDED upstream studio (the real thing).
  // The former Qubit-local context-engine pages keep their URLs below so
  // existing links and their tests stay valid, but they are no longer listed
  // in the sidebar (see BUILTIN_NAV_REST).
  "/open-viking": OpenVikingStudioPage,
  "/open-viking/retrieval": OpenVikingRetrievalPage,
  "/open-viking/uploads": OpenVikingUploadsPage,
  "/open-viking/agent-hub": OpenVikingAgentHubPage,
  "/open-viking/resources": OpenVikingResourcesPage,
  // The Context Store sidebar entry lands on the local-engine console.
  "/open-viking/context-store": OpenVikingPage,
  // Agent console surfaces (integrator lane): the ported computer workspace
  // and the typed agent card/artifact renderers.
  "/computer": ComputerWorkspacePage,
  "/agent-cards": AgentCardsPage,
  "/kitchen": KitchenPage,
  "/omniroute": OmniRoutePage,
  "/qubit-tv": QubitTvPage,
  // Hidden developer/owner surface: the live Sakuga VFX component gallery.
  "/sakuga-gallery": SakugaGalleryPage,
  "/qubit-office": () => <PluginPage name="qubit-office" />,
  "/qubit-office2": () => <PluginPage name="qubit-office2" />,
  "/qubit-office3": () => <PluginPage name="qubit-office3" />,
  // The workbench is a SERVER-served document (webui.py:_serve_workbench_ui),
  // not a plugin bundle or a React page — see ServerDocumentHandoff.
  "/qubit-workbench": () => <ServerDocumentHandoff path="/qubit-workbench" />,
};

// Route placeholder for /chat.  The persistent ChatPage host (rendered
// outside <Routes> when embedded chat is on) paints on top; this empty
// element just claims the path so the `*` catch-all redirect doesn't
// fire when the user navigates to /chat.
function ChatRouteSink() {
  return null;
}

const BUILTIN_NAV_REST: NavItem[] = [
  {
    path: "/sessions",
    labelKey: "sessions",
    label: "Sessions",
    icon: MessageSquare,
  },
  { path: "/files", labelKey: "files", label: "Files", icon: FolderOpen },
  {
    path: "/analytics",
    labelKey: "analytics",
    label: "Analytics",
    icon: BarChart3,
  },
  {
    path: "/models",
    labelKey: "models",
    label: "Models",
    icon: Cpu,
  },
  { path: "/logs", labelKey: "logs", label: "Logs", icon: FileText },
  { path: "/cron", labelKey: "cron", label: "Cron", icon: Clock },
  { path: "/skills", labelKey: "skills", label: "Skills", icon: Package },
  { path: "/plugins", labelKey: "plugins", label: "Plugins", icon: Puzzle },
  { path: "/mcp", labelKey: "mcp", label: "MCP", icon: Plug },
  { path: "/channels", labelKey: "channels", label: "Channels", icon: Radio },
  { path: "/webhooks", labelKey: "webhooks", label: "Webhooks", icon: Webhook },
  { path: "/pairing", labelKey: "pairing", label: "Pairing", icon: ShieldCheck },
  { path: "/profiles", labelKey: "profiles", label: "Profiles", icon: Users },
  { path: "/config", labelKey: "config", label: "Config", icon: Settings },
  { path: "/env", labelKey: "keys", label: "Keys", icon: KeyRound },
  { path: "/system", labelKey: "system", label: "System", icon: Wrench },
  { path: "/agents", labelKey: "agents", label: "Agents", icon: Users },
  // `/docs` is deliberately NOT here: it rendered the served OpenAPI document
  // (450 paths / 520 operations) as a browsable endpoint dump — accurate, but
  // useless to the person using Qubit. The route stays mounted as the API
  // reference for developers; the sidebar entry is `/guide` (see below), a
  // usage page that answers "how do I work this thing".
  // ---- Qubit groups -------------------------------------------------------
  // These below replace what used to be ~20 flat look-alike rows. Every route
  // is UNCHANGED and still reachable; grouping only changes what the sidebar
  // shows by default. A group parent is a real page link (exactEnd stops a
  // child route from lighting up both rows).
  //
  // Voice family: Talk (mic/TTS/wake) + Voice Editing. Qubit Chat is NOT here —
  // it is a top-level entry beside /chat (see QUBIT_CHAT_NAV_ITEM), because it
  // is a primary chat surface, not a voice sub-page.
  {
    path: "/qubit-talk",
    labelKey: "voice",
    label: "Voice & Speech",
    icon: Mic,
    exactEnd: true,
    children: [
      { path: "/voice-edit", labelKey: "voiceEditing", label: "Voice Editing", icon: Sparkles },
    ],
  },
  {
    path: "/qubit-scheduler",
    labelKey: "tasksScheduling",
    label: "Tasks & Scheduling",
    icon: Clock,
    exactEnd: true,
    children: [
      { path: "/qubit-taskflows", labelKey: "taskflows", label: "Taskflows", icon: Zap },
      { path: "/qubit-tasks", labelKey: "tasks", label: "Tasks", icon: FileText },
      { path: "/qubit-task-inbox", labelKey: "inbox", label: "Inbox", icon: Heart },
      { path: "/qubit-board-admin", labelKey: "boardAdmin", label: "Board Admin", icon: Star },
    ],
  },
  // Memory family: the external provider picker and Qubit's OWN local context
  // engine are DIFFERENT backends (provider registry vs context_store.py
  // `connection: "local"`), so they stay distinct rows under one parent.
  {
    path: "/qubit-memory",
    labelKey: "memory",
    label: "Memory",
    icon: Database,
    exactEnd: true,
    children: [
      {
        path: "/open-viking",
        labelKey: "openViking",
        label: "Open Viking Studio",
        icon: Ship,
        exactEnd: true,
      },
      {
        path: "/open-viking/context-store",
        labelKey: "contextStore",
        label: "Context Store",
        icon: Database,
      },
    ],
  },
  // Workspace family: agent/desktop administration surfaces.
  {
    path: "/agent-workspace",
    labelKey: "workspace",
    label: "Workspace",
    icon: Code,
    exactEnd: true,
    children: [
      { path: "/nodes", labelKey: "nodes", label: "Nodes", icon: Globe },
      { path: "/devices", labelKey: "devices", label: "Devices", icon: Shield },
      { path: "/session-ops", labelKey: "sessionOps", label: "Session Ops", icon: Activity },
      { path: "/worktrees", labelKey: "worktrees", label: "Worktrees", icon: FolderOpen },
      { path: "/checkpoints", labelKey: "checkpoints", label: "Checkpoints", icon: Database },
      { path: "/agent-cards", labelKey: "agentCards", label: "Agent Cards", icon: Bot },
    ],
  },
  // Admin family: operator-only surfaces.
  {
    path: "/admin/skills-review",
    labelKey: "admin",
    label: "Admin",
    icon: Package,
    exactEnd: true,
    children: [
      { path: "/admin/mcp-calls", labelKey: "mcpCalls", label: "MCP Calls", icon: Plug },
      { path: "/admin/users", labelKey: "users", label: "Users", icon: Users },
    ],
  },
  // Ungrouped Qubit surfaces, each one of a kind.
  // NB: /computer is a CopilotKit/openmuse port whose backend is a typed
  // COMPUTER_USE_NO_DRIVER refusal (no AT-SPI actuator) — visible on purpose
  // so the capability gap stays stated rather than hidden. It is grouped in
  // the System section (sidebar-sections.ts), not top-level: a stated gap is
  // system information, not a workflow surface.
  { path: "/computer", labelKey: "computer", label: "Computer", icon: Monitor },
  // Canvas / Bot / Harness Router / Kitchen are agent tooling and are grouped
  // into the Agents section (sidebar-sections.ts).
  { path: "/kitchen", labelKey: "kitchen", label: "Kitchen", icon: ChefHat },
  { path: "/omniroute", labelKey: "omniroute", label: "OmniRoute", icon: Network },
  { path: "/qubit-tv", labelKey: "qubitTv", label: "Qubit TV", icon: Tv },
  { path: "/canvas", labelKey: "canvas", label: "Qubit Canvas", icon: Eye },
  { path: "/qubit-bot", labelKey: "qubitBot", label: "Qubit Bot", icon: Bot },
  { path: "/qubit-harness-router", labelKey: "harnessRouter", label: "Harness Router", icon: Workflow },
  // Qubit HUD is a hidden-tab plugin: its manifest keeps `"hidden": true` so the
  // plugin loader does not auto-generate a second, duplicate tab, and the route
  // is still mounted by buildRoutes()'s hidden-tab pass. That left it with no
  // sidebar row at all, so it is listed here as a builtin entry pointing at the
  // plugin's path — the same pattern /qubit-office uses. The label comes from
  // ROUTE_TITLES, and sidebar-sections.ts groups it under System.
  { path: "/qubit-hud", label: "Qubit HUD", icon: Activity },
  // Qubit Workbench had the same gap as the HUD: the route is mounted and serves
  // the three-panel workspace, but it was never listed here, so the sidebar
  // showed no way in and the surface was reachable only by typing the URL.
  // Grouped first under Work (sidebar-sections.ts).
  { path: "/qubit-workbench", label: "Qubit Workbench", icon: PanelsTopLeft },
  { path: "/qubit-office", labelKey: "office", label: "Qubit Office", icon: Users, exactEnd: true },
  // `/qubit-office2` (Agent Quest) and `/qubit-office3` (Voice Chat) are no
  // longer listed: office2 duplicates the Office roster under a colliding
  // "office" name, and office3 is a second voice-conversation surface beside
  // Voice & Speech. Both routes stay mounted and reachable by URL.
];

// The usage guide that replaces the raw OpenAPI dump in the sidebar. `/docs`
// stays mounted as the developer API reference; `/guide` is the page a person
// actually opens.
const GUIDE_NAV_ITEM: NavItem = {
  path: "/guide",
  labelKey: "guide",
  label: "Guide",
  icon: BookOpen,
};

const ICON_MAP: Record<string, ComponentType<{ className?: string }>> = {
  Activity,
  BarChart3,
  Bot,
  Clock,
  Cpu,
  FileText,
  FolderOpen,
  KeyRound,
  MessageSquare,
  Network,
  Package,
  Search,
  Settings,
  Ship,
  Puzzle,
  Sparkles,
  Terminal,
  Globe,
  Database,
  Shield,
  Upload,
  Users,
  Workflow,
  Wrench,
  Zap,
  Heart,
  Star,
  Code,
  Eye,
  ChefHat,
};

function resolveIcon(name: string): ComponentType<{ className?: string }> {
  return ICON_MAP[name] ?? Puzzle;
}

// React Router matches these paths case-insensitively and ignores a trailing slash.
function routeKey(path: string): string {
  return normalizeRoutePath(path).toLowerCase();
}

/** Every path a nav tree owns, including group children. */
function collectNavPaths(items: readonly NavItem[]): Set<string> {
  const paths = new Set<string>();
  const add = (item: NavItem) => {
    paths.add(routeKey(item.path));
    for (const child of item.children ?? []) add(child);
  };
  for (const item of items) add(item);
  return paths;
}

function buildNavItems(
  builtIn: NavItem[],
  manifests: PluginManifest[],
): NavItem[] {
  const items = [...builtIn];
  // Include nested group children: /devices (a Workspace child) also ships a
  // plugin manifest, so a top-level-only check added a second Devices row.
  const ownedPaths = collectNavPaths(items);

  for (const manifest of manifests) {
    if (manifest.tab.override) continue;
    if (manifest.tab.hidden) continue;

    // A plugin tab whose path is already a built-in route is a duplicate: the
    // built-in nav item already renders that entry and owns the route. Pushing
    // it again puts two items with the same `path` into the sidebar list, which
    // React keys collide on (`key={item.path}`) and the menu shows the entry
    // twice. Skip it — the built-in entry wins.
    if (ownedPaths.has(routeKey(manifest.tab.path))) continue;

    const pluginItem: NavItem = {
      path: manifest.tab.path,
      label: ROUTE_TITLES[manifest.tab.path] ?? manifest.label,
      icon: resolveIcon(manifest.icon),
    };

    const pos = manifest.tab.position ?? "end";
    if (pos === "end") {
      items.push(pluginItem);
    } else if (pos.startsWith("after:")) {
      const target = "/" + pos.slice(6);
      const idx = items.findIndex((i) => i.path === target);
      items.splice(idx >= 0 ? idx + 1 : items.length, 0, pluginItem);
    } else if (pos.startsWith("before:")) {
      const target = "/" + pos.slice(7);
      const idx = items.findIndex((i) => i.path === target);
      items.splice(idx >= 0 ? idx : items.length, 0, pluginItem);
    } else {
      items.push(pluginItem);
    }
    ownedPaths.add(routeKey(pluginItem.path));
  }

  return items;
}

/** Split merged nav into built-in sidebar entries vs plugin tabs, preserving plugin order hints. */
function partitionSidebarNav(
  builtIn: NavItem[],
  manifests: PluginManifest[],
): { coreItems: NavItem[]; pluginItems: NavItem[] } {
  const merged = buildNavItems(builtIn, manifests);
  const builtinPaths = new Set(builtIn.map((i) => i.path));
  const coreItems: NavItem[] = [];
  const pluginItems: NavItem[] = [];
  for (const item of merged) {
    if (builtinPaths.has(item.path)) coreItems.push(item);
    else pluginItems.push(item);
  }
  return { coreItems, pluginItems };
}

function buildRoutes(
  builtinRoutes: Record<string, ComponentType>,
  manifests: PluginManifest[],
): Array<{
  key: string;
  path: string;
  element: ReactNode;
  pluginLabel?: string;
}> {
  const byOverride = new Map<string, PluginManifest>();
  const addons: PluginManifest[] = [];

  for (const m of manifests) {
    if (m.tab.override) {
      const key = routeKey(m.tab.override);
      if (!byOverride.has(key)) byOverride.set(key, m);
    } else {
      addons.push(m);
    }
  }

  const routes: Array<{
    key: string;
    path: string;
    element: ReactNode;
    pluginLabel?: string;
  }> = [];

  for (const [path, Component] of Object.entries(builtinRoutes)) {
    const om = byOverride.get(routeKey(path));
    if (om) {
      routes.push({
        key: `override:${om.name}`,
        path,
        element: <PluginPage name={om.name} />,
        pluginLabel: om.label,
      });
    } else {
      routes.push({ key: `builtin:${path}`, path, element: <Component /> });
    }
  }

  const ownedPaths = new Set(Object.keys(builtinRoutes).map(routeKey));
  // Visible tabs own collisions before hidden links. Within each group,
  // keep the same first-manifest rule the sidebar uses.
  for (const hidden of [false, true]) {
    for (const m of addons) {
      if (Boolean(m.tab.hidden) !== hidden) continue;
      const path = routeKey(m.tab.path);
      if (ownedPaths.has(path)) continue;
      ownedPaths.add(path);
      routes.push({
        key: `plugin:${path}`,
        path: m.tab.path,
        element: <PluginPage name={m.name} />,
        pluginLabel: m.label,
      });
    }
  }

  return routes;
}

const SIDEBAR_COLLAPSED_KEY = "qubit-sidebar-collapsed";
/** Pre-rebrand key, migrated one-time on first read so existing users keep
 *  their saved sidebar state. */
const LEGACY_SIDEBAR_COLLAPSED_KEY = "hermes-sidebar-collapsed";

export default function App() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const { manifests, loading: pluginsLoading } = usePlugins();
  const { theme } = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);
  // The right-hand panel plays this same file; two elements on one duration
  // fall into lockstep and the two rails pulse together. Starting here at 0
  // and letting the panel pick its own offset + rate keeps them out of phase.
  const railBackdropRef = useBackdropPhase(0, { rate: 0.97 });
  const closeMobile = useCallback(() => setMobileOpen(false), []);

  const [collapsed, setCollapsed] = useState(() => {
    try {
      const stored = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
      if (stored !== null) return stored === "true";
      const legacy = localStorage.getItem(LEGACY_SIDEBAR_COLLAPSED_KEY);
      if (legacy !== null) {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, legacy);
        localStorage.removeItem(LEGACY_SIDEBAR_COLLAPSED_KEY);
        return legacy === "true";
      }
      return false;
    } catch {
      return false;
    }
  });
  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch { /* localStorage may be unavailable in private browsing */ }
      return next;
    });
  }, []);
  const isMobile = useBelowBreakpoint(1024);
  const isDesktopCollapsed = collapsed && !isMobile;
  const tooltipWarmRef = useRef(0);
  const sidebarStatus = useSidebarStatus();
  const isDocsRoute = pathname === "/docs" || pathname === "/docs/";
  // Both chat routes render the one persistent ChatPage host.
  const isChatRoute = isChatPath(pathname);
  const chromeRef = useRef<HTMLElement>(null);

  // GSAP: the nav stack settles in once on first paint.
  useEffect(() => {
    stageChrome(chromeRef.current);
  }, []);
  // Full-bleed console pages (WebChat, Qubit Bot) own the viewport and scroll
  // internally; without this their `height: 100%` resolves against an
  // auto-height block ancestor and the page grows to its content.
  const isFullHeightRoute = isFullHeightPath(pathname);
  // The Qubit-native ChatPage is the persistent host for `/qubit-chat` only;
  // `/chat` is the real Qubit terminal (xterm) rendered as a normal route.
  const isNativeChatRoute = isNativeChatPath(pathname);
  const embeddedChat = isDashboardEmbeddedChatEnabled();
  // Defer mounting the persistent native-chat host (and its chunk) until the
  // user has actually opened /qubit-chat at least once. Sticky after that.
  const [chatHostMounted, setChatHostMounted] = useState(isNativeChatRoute);
  useEffect(() => {
    setChatHostMounted((prev) =>
      latchChatActivation(prev, isNativeChatRoute),
    );
  }, [isNativeChatRoute]);

  // Only an override of the native route owns the persistent native host.
  // The terminal route has its own route element and override.
  const nativeChatOverridden = manifests.some(
    (manifest) => manifest.tab.override && routeKey(manifest.tab.override) === NATIVE_CHAT_PATH,
  );

  const builtinRoutes = useMemo(
    () => ({
      ...BUILTIN_ROUTES_CORE,
      ...(embeddedChat
        ? { "/chat": TerminalChatPage, "/qubit-chat": ChatRouteSink }
        : {}),
    }),
    [embeddedChat],
  );

  // `/analytics` stays in the builtin nav: the route is mounted and reachable
  // regardless of `dashboard.show_token_analytics` (the page itself renders an
  // explanation when the flag is off), so the nav entry must always be present
  // rather than hiding a live route from the sidebar.
  const builtinNav = useMemo(
    () =>
      embeddedChat
        ? [CHAT_NAV_ITEM, QUBIT_CHAT_NAV_ITEM, ...BUILTIN_NAV_REST, GUIDE_NAV_ITEM]
        : [...BUILTIN_NAV_REST, GUIDE_NAV_ITEM],
    [embeddedChat],
  );

  const sidebarNav = useMemo(
    () => partitionSidebarNav(builtinNav, manifests),
    [builtinNav, manifests],
  );
  // Presentation grouping only — every route stays mounted and reachable; an
  // entry this module does not know about lands in the trailing "More"
  // section instead of disappearing.
  const useMangaNavigation = isMangaThemeName(theme.name) && mangaRouteEligible(pathname);
  const sidebarSections = useMemo(
    () => sectionizeNav(sidebarNav.coreItems, useMangaNavigation ? MANGA_SIDEBAR_SECTIONS : undefined),
    [sidebarNav, useMangaNavigation],
  );
  // Rail brand-row status chip: the one always-visible health readout.
  const brandTone = useMemo(() => {
    if (!sidebarStatus) return "muted";
    const tone = gatewayLine(sidebarStatus, t).tone;
    if (tone === "text-success") return "success";
    if (tone === "text-warning") return "warning";
    if (tone === "text-destructive") return "destructive";
    return "muted";
  }, [sidebarStatus, t]);
  const brandLabel = useMemo(
    () => (sidebarStatus ? `${t.status.gateway} ${gatewayLine(sidebarStatus, t).label}` : t.status.gateway),
    [sidebarStatus, t],
  );
  const routes = useMemo(
    () => buildRoutes(builtinRoutes, manifests),
    [builtinRoutes, manifests],
  );
  const pluginTabMeta = useMemo(
    () => routes.flatMap(({ path, pluginLabel }) =>
      pluginLabel === undefined ? [] : [{ path: routeKey(path), label: pluginLabel }],
    ),
    [routes],
  );

  const layoutVariant = theme.layoutVariant ?? "standard";

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [mobileOpen]);

  useEffect(() => {
    const mql = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setMobileOpen(false);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return (
    <ProfileProvider>
    <div
      data-layout-variant={layoutVariant}
      className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden bg-background-base text-text-primary antialiased"
    >
      <SakugaToastHost />
      <a
        href="#qubit-main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[300] focus:border focus:border-midground focus:bg-background-base focus:px-3 focus:py-2 focus:text-sm focus:text-foreground"
      >
        {t.common.skipToContent ?? "Skip to content"}
      </a>
      <SelectionSwitcher />

      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        /* The backdrop is a full-viewport layer, not an inset panel: it runs
           edge to edge so the fixed header's translucent surface shows it
           through instead of sealing it off. */
      >
        <Suspense fallback={null}>
          <AmbientFailSafe>
            <QubitAmbient />
          </AmbientFailSafe>
        </Suspense>
        <PluginSlot name="backdrop" />
      </div>

      <header
        className={cn(
          "lg:hidden fixed top-0 left-0 right-0 z-40 min-h-14",
          "flex items-center gap-2 px-4 py-2",
          "border-b border-current/20",
        )}
        style={{
          /* Opaque navy plate. The previous translucent + blur pass let the
             ambient backdrop ghost through and read as a stray overlay bar. */
          background: "#0a1730",
          borderBottom: "1px solid rgb(179 199 224 / 22%)",
          color: "#e8eef7",
        }}
      >
        <Button
          variant="secondary"
          onClick={() => setMobileOpen(true)}
          aria-label={t.app.openNavigation}
          {...{ "aria-expanded": String(mobileOpen), "aria-controls": "app-sidebar" }}
          className="text-text-secondary hover:text-midground"
        >
          <Menu />
        </Button>

        <QubitMark className="h-8 w-8 shrink-0 text-midground" />
        <Typography className="font-bold text-[1.1rem] tracking-[0.01em] text-midground">
          {t.app.brand}
        </Typography>
      </header>

      {mobileOpen && (
        <button
          type="button"
          aria-label={t.app.closeNavigation}
          onClick={closeMobile}
          className="lg:hidden fixed inset-0 z-40 block bg-black/70 p-0"
        />
      )}

      {/* Single mobile header clearance for the banner stack + content. The
          fixed lg:hidden header is h-14/z-40; previously each banner carried
          its own mt-14 AND the content kept pt-14, so two visible banners
          stacked three offsets (NS-656 review P3). One spacer, applied once. */}
      <div aria-hidden className="h-14 shrink-0 lg:hidden" />
      <PluginSlot name="header-banner" />
      <ProfileScopeBanner />
      <MemoryPressureBanner status={sidebarStatus} />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex min-h-0 min-w-0 flex-1">
          <aside
            id="app-sidebar"
            ref={chromeRef}
            data-collapsed={isDesktopCollapsed}
            aria-label={t.app.navigation}
            aria-hidden={isMobile && !mobileOpen ? true : undefined}
            inert={isMobile && !mobileOpen ? true : undefined}
            className={cn(
              "qagent-sidebar",
              "qplate",
              "fixed top-0 left-0 z-50 flex h-dvh max-h-dvh w-64 min-h-0 flex-col font-sans",
              "border-r border-current/20",
              "bg-background-base",
              "transition-[transform] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)]",
              mobileOpen ? "translate-x-0" : "-translate-x-full",
              "lg:sticky lg:top-0 lg:translate-x-0 lg:shrink-0 lg:overflow-hidden",
              "lg:transition-[width] lg:duration-300 lg:ease-[cubic-bezier(0.23,1,0.32,1)]",
              collapsed && "lg:w-14",
            )}
          >
            {/* A single reusable nine-slice chrome frame around the entire Manga rail.
                Pointer-events are disabled; navigation and status remain live.
                The legacy CSS perimeter frame is only for non-Manga themes: for
                Manga it must not exist in the DOM at all, so it cannot layer
                under/over the owner frame. */}
            {useMangaNavigation && <SidebarFrame />}
            {!useMangaNavigation && <div className="qm-manga-outer-frame" aria-hidden="true" />}
            {/* Rail backdrop clip — this rail only. Sits at z-index:-2 behind the
                engineering ruling (::before, z-index:-1) and every nav row.
                Muted at the source by the stylesheet, then scrimmed, so 13px
                labels keep their contrast on any theme. */}
            <div className="qagent-sidebar__backdrop" aria-hidden="true">
              <video
                ref={railBackdropRef}
                autoPlay
                disablePictureInPicture
                loop
                muted
                playsInline
                preload="metadata"
                poster="/media/sidebar-backdrop-poster.jpg"
                src="/media/sidebar-backdrop.mp4"
              />
              <div className="qagent-sidebar__backdrop-scrim" />
            </div>
            <div
              className={cn(
                "qagent-sidebar__brand-row",
                "flex h-14 shrink-0 items-center gap-2",
                "border-b border-current/20",
                collapsed ? "lg:justify-center lg:px-0" : "px-4 justify-between",
              )}
            >
              <div
                className={cn(
                  "flex min-w-0 flex-1 items-center gap-2.5",
                  collapsed && "lg:hidden",
                )}
              >
                <PluginSlot name="header-left" />
                <span className="qm-brand-emblem" aria-hidden="true"><QubitMark className="h-14 w-14 shrink-0 text-midground" /><MangaStudioBrandAtom /></span>
                <span
                  className="qagent-sidebar__brand-name"
                  data-brand={t.app.brand}
                >
                  <QubitBrandSparks />
                  <span className="qbrand-label truncate">{t.app.brand}</span>
                </span>
                <span
                  aria-hidden
                  className="qagent-sidebar__pulse ml-auto"
                  data-tone={brandTone}
                  role="status"
                  aria-label={brandLabel}
                />
              </div>

              <Button
                variant="secondary"
                onClick={closeMobile}
                aria-label={t.app.closeNavigation}
                className="lg:hidden text-text-secondary hover:text-midground"
              >
                <X />
              </Button>

              <Button
                variant="secondary"
                onClick={toggleCollapsed}
                data-sidebar-toggle=""
                aria-expanded={!isDesktopCollapsed}
                aria-label={
                  collapsed ? t.common.expand : t.common.collapse
                }
                className="hidden lg:flex text-text-secondary hover:text-midground"
              >
                {collapsed ? (
                  <PanelLeftOpen className="h-4 w-4" />
                ) : (
                  <PanelLeftClose className="h-4 w-4" />
                )}
              </Button>
            </div>

            <ProfileSwitcher collapsed={isDesktopCollapsed} />

            <nav
              className="qagent-sidebar__nav min-h-0 w-full flex-1 overflow-y-auto overflow-x-hidden py-2"
              aria-label={t.app.navigation}
            >
              {sidebarSections.map(({ section, entries }, sectionIndex) => (
                <div
                  aria-labelledby={`qubit-sidebar-section-${section.id}`}
                  className="qagent-sidebar__section"
                  key={section.id}
                  role="group"
                >
                  {/* The primary heading is retained for Manga; other themes hide it via CSS. */}
                  {(sectionIndex > 0 || useMangaNavigation) && (
                    useMangaNavigation && !collapsed ? (
                      <span
                        className="qagent-sidebar__section-head qm-section-titleplate"
                        id={`qubit-sidebar-section-${section.id}`}
                      >
                        <QubitSidebarTitle index={section.index} label={section.label.toUpperCase()} />
                      </span>
                    ) : (
                      <span
                        className={cn(
                          "qagent-sidebar__section-head",
                          collapsed && "lg:justify-center",
                        )}
                        id={`qubit-sidebar-section-${section.id}`}
                      >
                        <span className="qagent-sidebar__section-index">{section.index}</span>
                        <span className={cn(collapsed && "lg:hidden")}>
                          {t.app.sections?.[section.id] ?? section.label}
                        </span>
                        {!collapsed && (
                          <>
                            <span aria-hidden className="qagent-sidebar__section-rule" />
                            <span className="qm-manga-charge-cells" aria-hidden="true"><i /><i /><i /></span>
                          </>
                        )}
                      </span>
                    )
                  )}

                  <ul className="flex flex-col">
                    {entries.map((item) =>
                      item.children && item.children.length > 0 ? (
                        <SidebarNavGroup
                          closeMobile={closeMobile}
                          collapsed={collapsed}
                          item={item}
                          key={item.path}
                          t={t}
                          tooltipWarmRef={tooltipWarmRef}
                        />
                      ) : (
                        <SidebarNavLink
                          closeMobile={closeMobile}
                          collapsed={collapsed}
                          item={item}
                          key={item.path}
                          t={t}
                          tooltipWarmRef={tooltipWarmRef}
                        />
                      ),
                    )}
                  </ul>
                </div>
              ))}

              {sidebarNav.pluginItems.length > 0 && (
                <div
                  aria-labelledby="qubit-sidebar-plugin-nav-heading"
                  className="qagent-sidebar__section"
                  role="group"
                >
                  <span
                    className={cn(
                      "qagent-sidebar__section-head",
                      collapsed && "lg:justify-center",
                    )}
                    id="qubit-sidebar-plugin-nav-heading"
                  >
                    <span className="qagent-sidebar__section-index">++</span>
                    <span className={cn(collapsed && "lg:hidden")}>
                      {t.app.pluginNavSection}
                    </span>
                    {!collapsed && (
                      <>
                        <span aria-hidden className="qagent-sidebar__section-rule" />
                        {useMangaNavigation ? (
                            <MangaStudioTitleOrnaments />
                          ) : (
                            <span className="qm-manga-charge-cells" aria-hidden="true"><i /><i /><i /></span>
                          )}
                      </>
                    )}
                  </span>

                  <ul className="flex flex-col">
                    {sidebarNav.pluginItems.map((item) => (
                      <SidebarNavLink
                        closeMobile={closeMobile}
                        collapsed={collapsed}
                        item={item}
                        key={item.path}
                        t={t}
                        tooltipWarmRef={tooltipWarmRef}
                      />
                    ))}
                  </ul>
                </div>
              )}
            </nav>

            <SidebarSystemActions
              collapsed={isDesktopCollapsed}
              manga={useMangaNavigation}
              onNavigate={closeMobile}
              status={sidebarStatus}
              tooltipWarmRef={tooltipWarmRef}
            />

            <div className="qagent-sidebar__utility">
              <PluginSlot name="header-right" />

              <SidebarIconWithTooltip
                collapsed={isDesktopCollapsed}
                label={t.theme?.switchTheme ?? "Switch theme"}
                tooltipWarmRef={tooltipWarmRef}
              >
                <ThemeSwitcher collapsed={isDesktopCollapsed} />
              </SidebarIconWithTooltip>

              <SidebarIconWithTooltip
                collapsed={isDesktopCollapsed}
                label={t.language.switchTo}
                tooltipWarmRef={tooltipWarmRef}
              >
                <LanguageSwitcher collapsed={isDesktopCollapsed} />
              </SidebarIconWithTooltip>
            </div>

            <div
              className={cn(
                "flex shrink-0 flex-col",
                isDesktopCollapsed && "lg:hidden",
              )}
            >
              <AuthWidget />
            </div>

            <div className="qagent-sidebar__colophon">
              <SidebarFooter status={sidebarStatus} />
            </div>
          </aside>

          <PageHeaderProvider pluginTabs={pluginTabMeta}>
            <div
              className={cn(
                "relative z-2 flex min-w-0 min-h-0 flex-1 flex-col",
                "px-3 sm:px-6",
                isChatRoute
                  ? "pb-0 pt-1 sm:pt-2 lg:pt-4"
                  : "pt-2 sm:pt-4 lg:pt-6",
                isDocsRoute && "min-h-0 flex-1",
              )}
            >
              <PluginSlot name="pre-main" />
              <div
                data-qm-route={pathname}
                className={cn(
                  "w-full min-w-0",
                  !isChatRoute &&
                    !isFullHeightRoute &&
                    "pb-[calc(2rem+env(safe-area-inset-bottom,0px))] lg:pb-8",
                  (isDocsRoute || isChatRoute || isFullHeightRoute) &&
                    "min-h-0 flex flex-1 flex-col",
                )}
              >
                <ProfileKeyedRoutes>
                  <Suspense fallback={<RouteFallback />}>
                    <Routes>
                      {routes.map(({ key, path, element }) => (
                        <Route key={key} path={path} element={
                          <PageErrorBoundary key={key} label={path}>{element}</PageErrorBoundary>
                        } />
                      ))}
                      <Route
                        path="*"
                        element={
                          <UnknownRouteFallback pluginsLoading={pluginsLoading} />
                        }
                      />
                    </Routes>
                  </Suspense>
                </ProfileKeyedRoutes>

                {embeddedChat &&
                  !nativeChatOverridden &&
                  (pluginsLoading ? (
                    isNativeChatRoute ? (
                      <RouteFallback label="Loading chat…" />
                    ) : null
                  ) : chatHostMounted ? (
                    <div
                      data-chat-active={isNativeChatRoute ? "true" : "false"}
                      className={cn(
                        "min-h-0 min-w-0",
                        isNativeChatRoute ? "flex flex-1 flex-col" : "hidden",
                      )}
                      aria-hidden={!isNativeChatRoute}
                    >
                      <PageErrorBoundary label="Chat">
                        <Suspense
                          fallback={
                            isNativeChatRoute ? (
                              <RouteFallback label="Loading chat…" />
                            ) : null
                          }
                        >
                          <ChatPage isActive={isNativeChatRoute} />
                        </Suspense>
                      </PageErrorBoundary>
                    </div>
                  ) : isNativeChatRoute ? (
                    <RouteFallback label="Loading chat…" />
                  ) : null)}
              </div>
              <PluginSlot name="post-main" />
            </div>
          </PageHeaderProvider>
        </div>
      </div>

      <PluginSlot name="overlay" />
    </div>
    </ProfileProvider>
  );
}

/**
 * Remounts the entire routed page tree when the global management profile
 * changes. Pages load their data on mount; without this, a page opened
 * under profile A would keep showing A's state while writes (via the
 * fetchJSON ?profile= injection) silently targeted the newly selected
 * profile B — the exact stale-target footgun the switcher exists to kill.
 * Keying by profile resets every page's local state so it refetches under
 * the new scope. The persistent ChatPage host below handles its own
 * remount (channel keyed on scopedProfile).
 */
function ProfileKeyedRoutes({ children }: { children: ReactNode }) {
  const { profile } = useProfileScope();
  return <div key={profile || "__own__"} className="contents">{children}</div>;
}


/**
 * Lazy route importers, keyed by route path. The sidebar calls these on
 * hover/focus so the chunk for the page you are about to open is already in
 * flight. Prefetch never renders anything: the same `React.lazy` call still
 * owns the render, so a prefetch can only make the eventual load faster.
 */
const ROUTE_IMPORTERS: Record<string, () => Promise<unknown>> = {
  "/sessions": () => import("@/pages/SessionsPage"),
  "/files": () => import("@/pages/FilesPage"),
  "/analytics": () => import("@/pages/AnalyticsPage"),
  "/models": () => import("@/pages/ModelsPage"),
  "/logs": () => import("@/pages/LogsPage"),
  "/cron": () => import("@/pages/CronPage"),
  "/skills": () => import("@/pages/SkillsPage"),
  "/plugins": () => import("@/pages/PluginsPage"),
  "/mcp": () => import("@/pages/McpPage"),
  "/pairing": () => import("@/pages/PairingPage"),
  "/channels": () => import("@/pages/ChannelsPage"),
  "/webhooks": () => import("@/pages/WebhooksPage"),
  "/system": () => import("@/pages/SystemPage"),
  "/agents": () => import("@/pages/AgentsPage"),
  "/profiles": () => import("@/pages/ProfilesPage"),
  "/profiles/new": () => import("@/pages/ProfileBuilderPage"),
  "/config": () => import("@/pages/ConfigPage"),
  "/env": () => import("@/pages/EnvPage"),
  "/docs": () => import("@/pages/DocsPage"),
  "/guide": () => import("@/pages/GuidePage"),
  "/voice-edit": () => import("@/pages/VoiceEditPage"),
  "/agent-workspace": () => import("@/pages/AgentWorkspacePage"),
  "/nodes": () => import("@/pages/NodesPage"),
  "/devices": () => import("@/pages/DevicesPage"),
  "/session-ops": () => import("@/pages/SessionOpsPage"),
  "/worktrees": () => import("@/pages/WorktreesPage"),
  "/checkpoints": () => import("@/pages/CheckpointsPage"),
  "/qubit-board-admin": () => import("@/pages/BoardAdminPage"),
  "/qubit-task-inbox": () => import("@/pages/TaskInboxPage"),
  "/qubit-talk": () => import("@/pages/TalkPage"),
  "/qubit-scheduler": () => import("@/pages/SchedulerMediaPage"),
  "/admin/skills-review": () => import("@/pages/SkillsReviewPage"),
  "/admin/mcp-calls": () => import("@/pages/McpCallsPage"),
  "/admin/users": () => import("@/pages/UsersAdminPage"),
  "/canvas": () => import("@/pages/CanvasPage"),
  "/qubit-taskflows": () => import("@/pages/TaskflowsPage"),
  "/qubit-tasks": () => import("@/pages/TasksPage"),
  "/qubit-memory": () => import("@/pages/MemoryBackendsPage"),
  "/webchat": () => import("@/pages/WebChatPage"),
  "/qubit-bot": () => import("@/pages/QubitBotPage"),
  "/qubit-harness-router": () => import("@/pages/HarnessRouterPage"),
  "/open-viking": () => import("@/pages/OpenVikingStudioPage"),
  "/open-viking/retrieval": () => import("@/pages/OpenVikingRetrievalPage"),
  "/open-viking/uploads": () => import("@/pages/OpenVikingUploadsPage"),
  "/open-viking/agent-hub": () => import("@/pages/OpenVikingAgentHubPage"),
  "/open-viking/resources": () => import("@/pages/OpenVikingResourcesPage"),
  "/open-viking/context-store": () => import("@/pages/OpenVikingPage"),
  "/computer": () => import("@/pages/ComputerWorkspacePage"),
  "/agent-cards": () => import("@/pages/AgentCardsPage"),
  "/kitchen": () => import("@/pages/KitchenPage"),
  "/omniroute": () => import("@/pages/OmniRoutePage"),
  "/qubit-tv": () => import("@/pages/QubitTvPage"),
  "/qubit-chat": () => import("@/pages/ChatPage"),
  "/chat": () => import("@/pages/TerminalChatPage"),
};

function SidebarNavLink({
  closeMobile,
  collapsed,
  item,
  tooltipWarmRef,
  t,
  nested = false,
  onNavigate,
  expandable = false,
  expanded = false,
}: SidebarNavLinkProps) {
  const { path, label, labelKey, icon: Icon } = item;
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);

  const navLabel = labelKey
    ? ((t.app.nav as Record<string, string>)[labelKey] ?? label)
    : label;
  const description = navDescription(path);
  const canShowTooltip = collapsed || Boolean(description);
  const showTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setTooltipAnchor(null);
  };

  return (
    <li
      onMouseEnter={(event) => {
        // Warm the chunk for the page this row opens. Purely a network
        // prefetch — the lazy route still owns rendering and errors.
        prefetchRoute(path, ROUTE_IMPORTERS[path]);
        if (canShowTooltip && window.matchMedia?.("(min-width: 1024px) and (hover: hover)").matches) showTooltip(event);
      }}
      onMouseLeave={canShowTooltip ? hideTooltip : undefined}
    >
      <NavLink
        to={path}
        end={item.exactEnd ?? path === "/sessions"}
        data-stage-row="nav"
        onClick={(event) => {
          if (document.documentElement.dataset.qubitManga === "on") {
            const hasPointer = event.clientX !== 0 || event.clientY !== 0;
            void emitFxAt(
              "impact",
              event.currentTarget,
              hasPointer
                ? {
                    x: event.clientX + window.scrollX,
                    y: event.clientY + window.scrollY,
                    scale: 0.45,
                  }
                : { scale: 0.45 },
            );
          }
          closeMobile();
          onNavigate?.();
        }}
        aria-label={navLabel}
        onFocus={(event) => {
          prefetchRoute(path, ROUTE_IMPORTERS[path]);
          if (event.currentTarget.matches(":focus-visible") && window.matchMedia?.("(min-width: 1024px)").matches && canShowTooltip) showTooltip(event);
        }}
        onBlur={canShowTooltip ? hideTooltip : undefined}
        className={({ isActive }) =>
          cn(
            "qagent-sidebar__link",
            "group/nav relative flex items-center gap-3",
            nested && "qagent-sidebar__link--nested",
            "whitespace-nowrap cursor-pointer",
            "focus-visible:outline-none",
            isActive ? "text-midground" : "text-text-secondary hover:text-midground",
          )
        }
      >
        {({ isActive }) => (
          <>
            <Icon className="h-3.5 w-3.5 shrink-0" />

            <span
              className={cn(
                "qagent-sidebar__link-label truncate transition-opacity duration-300",
                collapsed && "lg:hidden",
              )}
            >
              {navLabel}
            </span>

            {/* Disclosure chevron on a group parent. Hidden when the rail is
                collapsed to icons (no room), where the tooltip carries it. */}
            {expandable && !collapsed && (
              <span
                aria-hidden
                className="qagent-sidebar__chevron"
                style={{ transform: expanded ? "rotate(90deg)" : "none" }}
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </span>
            )}

            {/* Screen readers / mobile keep the state explicit. */}
            <span className="sr-only">{isActive ? "(current page)" : ""}</span>
          </>
        )}
      </NavLink>

      {canShowTooltip && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={navLabel} detail={description} warmRef={tooltipWarmRef} />
      )}
    </li>
  );
}

/**
 * Sidebar submenu group (integrator lane 3: the Open Viking group). The
 * parent stays a real page link rendered exactly like a top-level entry;
 * children render indented beneath it via the shared SidebarNavLink (nested
 * indent, prefix matching). No expand/collapse state — the group is static,
 * so deep-links and sidebar highlighting can never disagree.
 */
function SidebarNavGroup({
  closeMobile,
  collapsed,
  item,
  tooltipWarmRef,
  t,
}: SidebarNavLinkProps) {
  const children = item.children ?? [];
  const location = useLocation();
  // A child route keeps the group open, so the page you are ON is never
  // hidden behind a collapsed parent. Opening the parent row itself toggles.
  const hasActiveChild = children.some(
    (c) => location.pathname === c.path || location.pathname.startsWith(`${c.path}/`),
  );
  const [open, setOpen] = useState(false);
  const expanded = open || hasActiveChild;
  return (
    <>
      <SidebarNavLink
        closeMobile={closeMobile}
        collapsed={collapsed}
        item={item}
        t={t}
        tooltipWarmRef={tooltipWarmRef}
        onNavigate={() => setOpen((v) => !v)}
        expandable={children.length > 0}
        expanded={expanded}
      />
      {expanded && (
        <li aria-label={`${item.label} subpages`}>
          <ul className="flex flex-col">
            {children.map((child) => (
              <SidebarNavLink
                closeMobile={closeMobile}
                collapsed={collapsed}
                item={child}
                key={child.path}
                nested
                t={t}
                tooltipWarmRef={tooltipWarmRef}
              />
            ))}
          </ul>
        </li>
      )}
    </>
  );
}

function SidebarSystemActions({
  collapsed,
  manga,
  onNavigate,
  status,
  tooltipWarmRef,
}: SidebarSystemActionsProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { activeAction, isBusy, isRunning, pendingAction, runAction } =
    useSystemActions();
  const canUpdateQubit = status?.can_update_qubit === true;
  const [restartConfirmOpen, setRestartConfirmOpen] = useState(false);
  const [updateConfirmOpen, setUpdateConfirmOpen] = useState(false);
  const [updateConfirmInfo, setUpdateConfirmInfo] =
    useState<UpdateCheckResponse | null>(null);
  const [updateConfirmChecking, setUpdateConfirmChecking] = useState(false);
  // The status endpoint can advertise `can_update_qubit: true` while the
  // Qubit backend refuses in-place dashboard self-updates with a typed
  // UPDATE_OPERATOR_OWNED policy refusal. Probe once up front so the action
  // renders honestly disabled with that reason instead of offering a control
  // that can only fail (the expected refusal must never surface as a raw
  // `ACTION FAILED: 403` toast).
  const [updateRefusal, setUpdateRefusal] = useState<PolicyRefusal | null>(
    null,
  );
  // Qubit refuses in-dashboard process supervision (gateway start/stop/restart)
  // with a typed GATEWAY_NO_SUPERVISION refusal. GET /api/gateway carries the
  // supervision policy, so the restart action can be honestly disabled with
  // that reason instead of failing on click.
  const [gatewayRefusal, setGatewayRefusal] = useState<string | null>(null);
  // Connection-latency sampler: rounds the `GET /api/gateway` probe (below)
  // and surfaces the smoothed reading in the sidebar status strip.
  const [latencySampler] = useState(() => new LatencySampler());
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const probe = () => {
      measureLatency(() => api.getGatewayStatus())
        .then(({ value: res, ms }) => {
          if (cancelled) return;
          const supervision = res?.supervision ?? "";
          setGatewayRefusal(
            // Any non-empty supervision disables the action. Accept both the
            // legacy GATEWAY_NO_SUPERVISION token and the real supervisor's
            // GATEWAY_SUPERVISION_UNAVAILABLE.
            supervision &&
              (supervision.includes("GATEWAY_NO_SUPERVISION") ||
                supervision.includes("GATEWAY_SUPERVISION_UNAVAILABLE"))
              ? supervision
              : null,
          );
          latencySampler.record(ms);
          setLatencyMs(latencySampler.stats.latestMs);
        })
        .catch(() => {
          // Best-effort probe: leave the action enabled and classify any typed
          // refusal when the operator actually invokes it. A failed probe has
          // no meaningful round-trip to report.
          if (!cancelled) setLatencyMs(null);
        });
    };
    probe();
    const id = setInterval(probe, 15_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [latencySampler]);

  useEffect(() => {
    if (!canUpdateQubit) return;
    let cancelled = false;
    api
      .checkQubitUpdate(false)
      .then((info) => {
        if (cancelled) return;
        setUpdateConfirmInfo(info);
        setUpdateRefusal(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setUpdateConfirmInfo(null);
        setUpdateRefusal(parsePolicyRefusal(err));
      });
    return () => {
      cancelled = true;
    };
  }, [canUpdateQubit]);

  useEffect(() => {
    if (!updateConfirmOpen || updateRefusal) {
      return;
    }
    let cancelled = false;
    setUpdateConfirmChecking(true);
    api
      .checkQubitUpdate(false)
      .then((info) => {
        if (!cancelled) setUpdateConfirmInfo(info);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setUpdateConfirmInfo(null);
        setUpdateRefusal(parsePolicyRefusal(err));
      })
      .finally(() => {
        if (!cancelled) setUpdateConfirmChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [updateConfirmOpen, updateRefusal]);

  const updateConfirmDescription = useMemo(() => {
    if (updateConfirmInfo?.behind && updateConfirmInfo.behind > 0) {
      const cmd = updateConfirmInfo.update_command;
      const n = updateConfirmInfo.behind;
      return `This will run the Qubit update (${cmd}) and pull ${n} new commit${n === 1 ? "" : "s"}. The gateway restarts when the update finishes; the current session keeps its prompt cache until then.`;
    }
    const cmd = updateConfirmInfo?.update_command ?? "qubit update";
    return (
      t.status.updateQubitConfirmMessage ??
      `This will run the Qubit update (${cmd}) and restart the gateway when it finishes.`
    );
  }, [t.status.updateQubitConfirmMessage, updateConfirmInfo]);

  // The canonical GET /api/qubit/update/check answers a truthful report. Qubit
  // never self-updates in place (can_apply=false), so the update action must
  // render honestly disabled with the backend's operator guidance instead of
  // offering a control that can only fail on POST /api/qubit/update.
  const updateBlockedReason = useMemo(() => {
    if (updateRefusal) return policyRefusalMessage(updateRefusal);
    if (updateConfirmInfo && updateConfirmInfo.can_apply === false) {
      return (
        updateConfirmInfo.message ??
        "Qubit updates are operator-owned and staged; run the CLI upgrade path."
      );
    }
    return undefined;
  }, [updateRefusal, updateConfirmInfo]);

  const items: SystemActionItem[] = [
    {
      action: "restart",
      icon: RotateCw,
      label: t.status.restartGateway,
      runningLabel: t.status.restartingGateway,
      spin: true,
      disabledReason: gatewayRefusal ?? undefined,
    },
  ];
  if (canUpdateQubit) {
    items.push({
      action: "update",
      icon: Download,
      label: t.status.updateQubit,
      runningLabel: t.status.updatingQubit,
      spin: false,
      disabledReason: updateBlockedReason,
    });
  }

  const q5t = manga && !collapsed;
  const gatewaySummary = status ? gatewayLine(status, t) : null;
  const stripLabel = (value: string) => value.replace(/[:：]\s*$/, "");

  const renderQmAction = (item: SystemActionItem) => {
    const pending = pendingAction === item.action;
    const running = activeAction === item.action && isRunning && !pending;
    const disabled =
      !!item.disabledReason ||
      (isBusy && !(pending || (activeAction === item.action && isRunning)));
    return (
      <QmSystemActionButton
        key={item.action}
        action={item.action}
        collapsed={collapsed}
        disabled={disabled}
        isPending={pending}
        isRunning={running}
        label={item.label}
        runningLabel={item.runningLabel}
        spin={item.spin}
        disabledReason={item.disabledReason}
        onClick={() => handleClick(item.action)}
        tooltipWarmRef={tooltipWarmRef}
      />
    );
  };

  const handleClick = (action: SystemAction) => {
    if (isBusy) return;
    if (action === "update" && updateBlockedReason) return;
    if (action === "restart" && gatewayRefusal) return;
    if (action === "restart") {
      setRestartConfirmOpen(true);
      return;
    }
    if (action === "update") {
      setUpdateConfirmOpen(true);
      return;
    }
    void runAction(action);
    navigate("/sessions");
    onNavigate();
  };

  const confirmRestart = () => {
    setRestartConfirmOpen(false);
    void runAction("restart");
    navigate("/sessions");
    onNavigate();
  };

  const confirmUpdate = () => {
    setUpdateConfirmOpen(false);
    void runAction("update");
    navigate("/sessions");
    onNavigate();
  };

  return (
    <>
    <div className={cn("qagent-sidebar__system", q5t ? "q5t-system-shell" : "qm-manga-gateway-shell")}>
      {q5t ? (
        <>
          <Q5TSystemTitle>{t.app.system}</Q5TSystemTitle>
          <Q5TGatewayFrame>
            <NavLink to="/sessions" title={t.app.statusOverview} className="block text-left">
              <dl>
                <div>
                  <dt>{stripLabel(t.app.gatewayStatusLabel)}</dt>
                  <dd
                    className={gatewaySummary?.tone}
                    data-state={gatewaySummary?.tone === "text-success" ? "up" : "down"}
                  >
                    {gatewaySummary?.label ?? "…"}
                  </dd>
                </div>
                <div>
                  <dt>{stripLabel(t.app.activeSessionsLabel)}</dt>
                  <dd>{status?.active_sessions ?? "…"}</dd>
                </div>
                <div>
                  <dt>Latency</dt>
                  <dd>{latencyMs === null || latencyMs === undefined ? "—" : formatLatency(latencyMs)}</dd>
                </div>
              </dl>
            </NavLink>
          </Q5TGatewayFrame>
        </>
      ) : !collapsed ? (
        <div className="qagent-sidebar__telemetry qm-manga-gateway-plate" role="group" aria-label={t.app.system}>
          <span className="qagent-sidebar__section-head" style={{ margin: "0 0 .2rem", borderTop: 0 }}>
            <span>{t.app.system}</span>
            <span aria-hidden className="qagent-sidebar__section-rule" />
          </span>
          <SidebarStatusStrip status={status} latencyMs={latencyMs} />
        </div>
      ) : null}

      {!q5t && <GatewayDot collapsed={collapsed} status={status} tooltipWarmRef={tooltipWarmRef} />}

      {manga ? (
        <ul
          className={cn(
            "qm-manga-system-actions",
            collapsed
              ? "qm-manga-system-actions--collapsed flex flex-col gap-2"
              : "flex flex-row items-stretch gap-2",
          )}
        >
          {items.map(renderQmAction)}
        </ul>
      ) : (
        <ul className="flex flex-row gap-2">
          {items.map((item) => (
            <SystemActionButton
              key={item.action}
              collapsed={collapsed}
              disabled={
                !!item.disabledReason ||
                (isBusy && !(pendingAction === item.action || (activeAction === item.action && isRunning)))
              }
              tooltipWarmRef={tooltipWarmRef}
              isPending={pendingAction === item.action}
              isRunning={activeAction === item.action && isRunning && pendingAction !== item.action}
              item={item}
              onClick={() => handleClick(item.action)}
            />
          ))}
        </ul>
      )}
    </div>

    <ConfirmDialog
      cancelLabel={t.common.cancel}
      confirmLabel={t.status.restartGateway}
      description={
        t.status.restartGatewayConfirmMessage ??
        "This restarts the Qubit gateway process. Connected channels and active sessions will reconnect afterward."
      }
      loading={pendingAction === "restart"}
      onCancel={() => setRestartConfirmOpen(false)}
      onConfirm={confirmRestart}
      open={restartConfirmOpen}
      title={
        t.status.restartGatewayConfirmTitle ?? `${t.status.restartGateway}?`
      }
    />

    <ConfirmDialog
      cancelLabel={t.common.cancel}
      confirmLabel={t.status.updateQubitConfirmNow ?? "Update now"}
      description={
        updateConfirmChecking ? t.common.loading : updateConfirmDescription
      }
      loading={pendingAction === "update" || updateConfirmChecking}
      onCancel={() => setUpdateConfirmOpen(false)}
      onConfirm={confirmUpdate}
      open={updateConfirmOpen && !updateRefusal}
      title={t.status.updateQubitConfirmTitle ?? `${t.status.updateQubit}?`}
    />
    </>
  );
}

function SystemActionButton({
  collapsed,
  disabled,
  isPending,
  isRunning: isActionRunning,
  item,
  onClick,
  tooltipWarmRef,
}: SystemActionButtonProps) {
  const { icon: Icon, label, runningLabel, spin } = item;
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const busy = isPending || isActionRunning;
  const displayLabel = isActionRunning ? runningLabel : label;
  const showTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setTooltipAnchor(null);
  };

  return (
    <li
      className="min-w-0 flex-1"
      data-system-action={item.action}
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      <ImportantTooltip
        active={Boolean(item.disabledReason)}
        title={displayLabel}
        detail={item.disabledReason}
      >
        <Button
          variant="secondary"
          onClick={onClick}
          disabled={disabled}
          {...{ "aria-busy": busy }}
          aria-label={displayLabel}
          onFocus={collapsed ? showTooltip : undefined}
          onBlur={collapsed ? hideTooltip : undefined}
          type="button"
          className={cn(
            "qagent-sidebar__action group/action",
            busy ? "text-midground" : "text-text-secondary",
          )}
        >
          {isPending ? (
            <Spinner className="shrink-0 text-[0.875rem]" />
          ) : isActionRunning && spin ? (
            <Spinner className="shrink-0 text-[0.875rem]" />
          ) : (
            <Icon
              className={cn(
                "h-3.5 w-3.5 shrink-0",
                isActionRunning && !spin && "animate-pulse",
              )}
            />
          )}

          <span className={cn("truncate", collapsed && "lg:hidden")}>
            {displayLabel}
          </span>
        </Button>
      </ImportantTooltip>

      {collapsed && tooltipAnchor && !item.disabledReason && (
        <SidebarTooltip
          anchor={tooltipAnchor}
          label={displayLabel}
          warmRef={tooltipWarmRef}
        />
      )}
    </li>
  );
}

function SidebarIconWithTooltip({
  children,
  collapsed,
  label,
  tooltipWarmRef,
}: SidebarIconWithTooltipProps) {
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const showTooltip = (event: MouseEvent<HTMLDivElement> | FocusEvent<HTMLDivElement>) => {
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setTooltipAnchor(null);
  };

  return (
    <div
      className="relative w-fit"
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
      onFocus={collapsed ? (event) => { if (event.target.matches(":focus-visible")) showTooltip(event); } : undefined}
      onBlur={collapsed ? hideTooltip : undefined}
    >
      {children}

      {collapsed && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={label} warmRef={tooltipWarmRef} />
      )}
    </div>
  );
}

function GatewayDot({ collapsed, status, tooltipWarmRef }: GatewayDotProps) {
  const { t } = useI18n();
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);

  const toneToTone: Record<string, string> = {
    "text-success": "success",
    "text-warning": "warning",
    "text-destructive": "destructive",
    "text-muted-foreground": "muted",
  };

  let tone: string;
  let label: string;

  if (!status) {
    tone = "muted";
    label = t.status.gateway;
  } else {
    const gw = gatewayLine(status, t);
    tone = toneToTone[gw.tone] ?? "muted";
    label = `${t.status.gateway} ${gw.label}`;
  }
  const showTooltip = (event: MouseEvent<HTMLDivElement> | FocusEvent<HTMLDivElement>) => {
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setTooltipAnchor(null);
  };

  return (
    <div
      className={cn(
        "hidden lg:flex py-2 pl-3",
        collapsed ? "lg:opacity-100" : "lg:opacity-0 lg:h-0 lg:py-0 lg:overflow-hidden",
      )}
      role="status"
      aria-label={label}
      tabIndex={collapsed ? 0 : -1}
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
      onFocus={collapsed ? showTooltip : undefined}
      onBlur={collapsed ? hideTooltip : undefined}
    >
      <span
        aria-hidden
        className="qagent-sidebar__pulse"
        data-tone={tone}
      />

      {tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={label} warmRef={tooltipWarmRef} />
      )}
    </div>
  );
}

type TooltipWarmRef = React.RefObject<number>;

interface GatewayDotProps {
  collapsed: boolean;
  status: StatusResponse | null;
  tooltipWarmRef: TooltipWarmRef;
}

interface NavItem {
  icon: ComponentType<{ className?: string }>;
  label: string;
  labelKey?: string;
  path: string;
  // Optional submenu children (integrator lane 3: the Open Viking group).
  // The parent stays a real page link; children render indented beneath it.
  children?: NavItem[];
  // Force exact-path matching for a group parent so a child route does not
  // light up both rows (the shared link defaults to prefix matching).
  exactEnd?: boolean;
}

interface SidebarIconWithTooltipProps {
  children: ReactNode;
  collapsed: boolean;
  label: string;
  tooltipWarmRef: TooltipWarmRef;
}

interface SidebarNavLinkProps {
  closeMobile: () => void;
  collapsed: boolean;
  item: NavItem;
  t: Translations;
  tooltipWarmRef: TooltipWarmRef;
  nested?: boolean;
  /** Called in addition to navigation — a group parent uses it to toggle. */
  onNavigate?: () => void;
  /** Renders a disclosure chevron on a group parent row. */
  expandable?: boolean;
  /** Whether that chevron points open (down) or closed (right). */
  expanded?: boolean;
}

interface SidebarSystemActionsProps {
  collapsed: boolean;
  manga: boolean;
  onNavigate: () => void;
  status: StatusResponse | null;
  tooltipWarmRef: TooltipWarmRef;
}

interface SystemActionButtonProps {
  collapsed: boolean;
  disabled: boolean;
  isPending: boolean;
  isRunning: boolean;
  item: SystemActionItem;
  onClick: () => void;
  tooltipWarmRef: TooltipWarmRef;
}

interface SystemActionItem {
  action: SystemAction;
  icon: ComponentType<{ className?: string }>;
  label: string;
  runningLabel: string;
  spin: boolean;
  /** When set, the action is honestly disabled with this operator-facing reason. */
  disabledReason?: string;
}
