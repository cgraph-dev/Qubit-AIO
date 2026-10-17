import {
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
import { createPortal } from "react-dom";
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
  Upload,
  Users,
  Webhook,
  Workflow,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { SelectionSwitcher } from "@nous-research/ui/ui/components/selection-switcher";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { cn } from "@/lib/utils";
import { QubitMark } from "@/components/QubitMark";
import { QubitBrandSparks } from "@/components/QubitBrandSparks";
import { SidebarFooter } from "@/components/SidebarFooter";
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
import "@/styles/sidebar-studio.css";
import "@/styles/qubit-mark.css";

// Route pages are lazy-loaded so the initial dashboard shell does not pay for
// every admin surface (and heavy deps like xterm) up front.
const ConfigPage = lazy(() => import("@/pages/ConfigPage"));
const DocsPage = lazy(() => import("@/pages/DocsPage"));
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
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { useI18n } from "@/i18n";
import type { Translations } from "@/i18n/types";
import { PluginPage, PluginSlot, usePlugins } from "@/plugins";
import type { PluginManifest } from "@/plugins";
import { useTheme } from "@/themes";
import { isDashboardEmbeddedChatEnabled } from "@/lib/dashboard-flags";
import { latchChatActivation } from "@/lib/chat-activation";
import { isChatPath, isFullHeightPath, isNativeChatPath } from "@/lib/chat-routes";
import { ROUTE_TITLES } from "@/lib/resolve-page-title";
import {
  parsePolicyRefusal,
  policyRefusalMessage,
  type PolicyRefusal,
} from "@/lib/policy-refusal";
import { api } from "@/lib/api";
import type { StatusResponse, UpdateCheckResponse } from "@/lib/api";
import { LatencySampler, measureLatency } from "@/lib/latency-sampler";

function RouteFallback({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      className="flex min-h-[12rem] flex-1 items-center justify-center"
      aria-busy="true"
      aria-live="polite"
    >
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        <span>{label}</span>
      </div>
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
  label: "Qubit Chat",
  icon: Terminal,
  exactEnd: true,
  children: [
    { path: "/webchat", label: "WebChat", icon: MessageSquare },
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
  "/qubit-office": () => <PluginPage name="qubit-office" />,
  "/qubit-office2": () => <PluginPage name="qubit-office2" />,
  "/qubit-office3": () => <PluginPage name="qubit-office3" />,
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
  { path: "/files", label: "Files", icon: FolderOpen },
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
  { path: "/mcp", label: "MCP", icon: Plug },
  { path: "/channels", label: "Channels", icon: Radio },
  { path: "/webhooks", label: "Webhooks", icon: Webhook },
  { path: "/pairing", label: "Pairing", icon: ShieldCheck },
  { path: "/profiles", labelKey: "profiles", label: "Profiles", icon: Users },
  { path: "/config", labelKey: "config", label: "Config", icon: Settings },
  { path: "/env", labelKey: "keys", label: "Keys", icon: KeyRound },
  { path: "/system", label: "System", icon: Wrench },
  { path: "/agents", label: "Agents", icon: Users },
  {
    path: "/docs",
    labelKey: "documentation",
    label: "Documentation",
    icon: BookOpen,
  },
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
    label: "Voice & Speech",
    icon: Mic,
    exactEnd: true,
    children: [
      { path: "/voice-edit", label: "Voice Editing", icon: Sparkles },
    ],
  },
  {
    path: "/qubit-scheduler",
    label: "Tasks & Scheduling",
    icon: Clock,
    exactEnd: true,
    children: [
      { path: "/qubit-taskflows", label: "Taskflows", icon: Zap },
      { path: "/qubit-tasks", label: "Tasks", icon: FileText },
      { path: "/qubit-task-inbox", label: "Inbox", icon: Heart },
      { path: "/qubit-board-admin", label: "Board Admin", icon: Star },
    ],
  },
  // Memory family: the external provider picker and Qubit's OWN local context
  // engine are DIFFERENT backends (provider registry vs context_store.py
  // `connection: "local"`), so they stay distinct rows under one parent.
  {
    path: "/qubit-memory",
    label: "Memory",
    icon: Database,
    exactEnd: true,
    children: [
      {
        path: "/open-viking",
        label: "Open Viking Studio",
        icon: Ship,
        exactEnd: true,
      },
      {
        path: "/open-viking/context-store",
        label: "Context Store",
        icon: Database,
      },
    ],
  },
  // Workspace family: agent/desktop administration surfaces.
  {
    path: "/agent-workspace",
    label: "Workspace",
    icon: Code,
    exactEnd: true,
    children: [
      { path: "/nodes", label: "Nodes", icon: Globe },
      { path: "/devices", label: "Devices", icon: Shield },
      { path: "/session-ops", label: "Session Ops", icon: Activity },
      { path: "/worktrees", label: "Worktrees", icon: FolderOpen },
      { path: "/checkpoints", label: "Checkpoints", icon: Database },
      { path: "/agent-cards", label: "Agent Cards", icon: Bot },
    ],
  },
  // Admin family: operator-only surfaces.
  {
    path: "/admin/skills-review",
    label: "Admin",
    icon: Package,
    exactEnd: true,
    children: [
      { path: "/admin/mcp-calls", label: "MCP Calls", icon: Plug },
      { path: "/admin/users", label: "Users", icon: Users },
    ],
  },
  // Ungrouped Qubit surfaces, each one of a kind.
  // NB: /computer is a CopilotKit/openmuse port whose backend is a typed
  // COMPUTER_USE_NO_DRIVER refusal (no AT-SPI actuator) — visible on purpose
  // so the capability gap stays stated rather than hidden.
  { path: "/computer", label: "Computer", icon: Monitor },
  { path: "/kitchen", label: "Kitchen", icon: ChefHat },
  { path: "/omniroute", label: "OmniRoute", icon: Network },
  { path: "/canvas", label: "Qubit Canvas", icon: Eye },
  { path: "/qubit-bot", label: "Qubit Bot", icon: Bot },
  { path: "/qubit-harness-router", label: "Harness Router", icon: Workflow },
  { path: "/qubit-office", label: "Qubit Office", icon: Users, exactEnd: true },
  { path: "/qubit-office2", label: "Agent Quest", icon: Star, exactEnd: true },
  { path: "/qubit-office3", label: "Voice Chat", icon: Mic, exactEnd: true },
];

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

function buildNavItems(
  builtIn: NavItem[],
  manifests: PluginManifest[],
): NavItem[] {
  const items = [...builtIn];

  for (const manifest of manifests) {
    if (manifest.tab.override) continue;
    if (manifest.tab.hidden) continue;

    // A plugin tab whose path is already a built-in route is a duplicate: the
    // built-in nav item already renders that entry and owns the route. Pushing
    // it again puts two items with the same `path` into the sidebar list, which
    // React keys collide on (`key={item.path}`) and the menu shows the entry
    // twice. Skip it — the built-in entry wins.
    if (items.some((i) => i.path === manifest.tab.path)) continue;

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
}> {
  const byOverride = new Map<string, PluginManifest>();
  const addons: PluginManifest[] = [];

  for (const m of manifests) {
    if (m.tab.override) {
      byOverride.set(m.tab.override, m);
    } else {
      addons.push(m);
    }
  }

  const routes: Array<{
    key: string;
    path: string;
    element: ReactNode;
  }> = [];

  for (const [path, Component] of Object.entries(builtinRoutes)) {
    const om = byOverride.get(path);
    if (om) {
      routes.push({
        key: `override:${om.name}`,
        path,
        element: <PluginPage name={om.name} />,
      });
    } else {
      routes.push({ key: `builtin:${path}`, path, element: <Component /> });
    }
  }

  for (const m of addons) {
    if (m.tab.hidden) continue;
    if (m.tab.path === "/plugins") continue;
    if (builtinRoutes[m.tab.path]) continue;
    routes.push({
      key: `plugin:${m.name}`,
      path: m.tab.path,
      element: <PluginPage name={m.name} />,
    });
  }

  for (const m of manifests) {
    if (!m.tab.hidden) continue;
    if (m.tab.path === "/plugins") continue;
    if (builtinRoutes[m.tab.path] || m.tab.override) continue;
    routes.push({
      key: `plugin:hidden:${m.name}`,
      path: m.tab.path,
      element: <PluginPage name={m.name} />,
    });
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

  // A plugin can replace the built-in /chat page via `tab.override: "/chat"`
  // in its manifest.  When one does, `buildRoutes` already swaps the route
  // element for <PluginPage /> — but we also have to suppress the
  // persistent ChatPage host below, or the plugin's page and the built-in
  // terminal would paint on top of each other.  The override is niche
  // (nothing ships overriding /chat today) but it's an advertised
  // extension point, so preserve the pre-persistence contract: when a
  // plugin owns /chat, the built-in chat UI is entirely absent.
  //
  // Waiting on `pluginsLoading` is load-bearing: manifests arrive
  // asynchronously from /api/dashboard/plugins, so on initial render
  // `chatOverriddenByPlugin` is always false.  Without the loading
  // gate, the persistent host would mount, spawn a PTY, and THEN get
  // yanked out from under the user when the plugin's manifest resolves
  // — killing the session mid-paint.  Delaying host mount by the
  // plugin-load window (typically <50ms, worst case 2s safety timeout)
  // is the cheaper trade-off.
  const chatOverriddenByPlugin = useMemo(
    () => manifests.some((m) => m.tab.override === "/chat"),
    [manifests],
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
        ? [CHAT_NAV_ITEM, QUBIT_CHAT_NAV_ITEM, ...BUILTIN_NAV_REST]
        : BUILTIN_NAV_REST,
    [embeddedChat],
  );

  const sidebarNav = useMemo(
    () => partitionSidebarNav(builtinNav, manifests),
    [builtinNav, manifests],
  );
  const routes = useMemo(
    () => buildRoutes(builtinRoutes, manifests),
    [builtinRoutes, manifests],
  );
  const pluginTabMeta = useMemo(
    () =>
      manifests.map((m) => {
        // Hidden tabs (/qubit-approvals, /qubit-brand) still claim a URL, so
        // they need a real page title too; ROUTE_TITLES keeps the Qubit overlay
        // labels distinct from the vendored duplicate routes.
        const path = m.tab.override ?? m.tab.path;
        return { path, label: ROUTE_TITLES[path] ?? m.label };
      }),
    [manifests],
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
      <SelectionSwitcher />

      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
      >
        <PluginSlot name="backdrop" />
      </div>

      <header
        className={cn(
          "lg:hidden fixed top-0 left-0 right-0 z-40 min-h-14",
          "flex items-center gap-2 px-4 py-2",
          "border-b border-current/20",
          "bg-background-base",
        )}
        style={{
          background:
            "var(--component-header-background, var(--background-base))",
          borderImage: "var(--component-header-border-image)",
          clipPath: "var(--component-header-clip-path)",
        }}
      >
        <Button
          ghost
          size="icon"
          onClick={() => setMobileOpen(true)}
          aria-label={t.app.openNavigation}
          aria-expanded={mobileOpen}
          aria-controls="app-sidebar"
          className="text-text-secondary hover:text-midground"
        >
          <Menu />
        </Button>

        <QubitMark className="h-10 w-10 shrink-0 text-midground" />
        <Typography className="font-bold text-[1.3rem] tracking-[0.03em] text-midground">
          {t.app.brand}
        </Typography>
      </header>

      {mobileOpen && (
        <Button
          ghost
          aria-label={t.app.closeNavigation}
          onClick={closeMobile}
          className={cn(
            "lg:hidden fixed inset-0 z-40 p-0 block",
            "bg-black/70",
          )}
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
            data-collapsed={isDesktopCollapsed}
            aria-label={t.app.navigation}
            className={cn(
              "qagent-sidebar",
              "fixed top-0 left-0 z-50 flex h-dvh max-h-dvh w-64 min-h-0 flex-col font-sans",
              "border-r border-current/20",
              "bg-background-base",
              "transition-[transform] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)]",
              mobileOpen ? "translate-x-0" : "-translate-x-full",
              "lg:sticky lg:top-0 lg:translate-x-0 lg:shrink-0 lg:overflow-hidden",
              "lg:transition-[width] lg:duration-300 lg:ease-[cubic-bezier(0.23,1,0.32,1)]",
              collapsed && "lg:w-14",
            )}
            style={{
              background:
                "var(--component-sidebar-background, var(--background-base))",
              clipPath: "var(--component-sidebar-clip-path)",
              borderImage: "var(--component-sidebar-border-image)",
            }}
          >
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
                  "flex min-w-0 items-center gap-2.5",
                  collapsed && "lg:hidden",
                )}
              >
                <PluginSlot name="header-left" />
                <QubitMark className="h-11 w-11 shrink-0 text-midground" />
                <span
                  className="qagent-sidebar__brand-name relative font-bold text-[1.35rem] tracking-[0.03em] text-midground"
                  data-brand={t.app.brand}
                >
                  <QubitBrandSparks />
                  <span className="qbrand-label">{t.app.brand}</span>
                </span>
              </div>

              <Button
                ghost
                size="icon"
                onClick={closeMobile}
                aria-label={t.app.closeNavigation}
                className="lg:hidden text-text-secondary hover:text-midground"
              >
                <X />
              </Button>

              <Button
                ghost
                size="icon"
                onClick={toggleCollapsed}
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
              className="qagent-sidebar__nav min-h-0 w-full flex-1 overflow-y-auto overflow-x-hidden border-t border-current/10 py-2"
              aria-label={t.app.navigation}
            >
              <ul className="flex flex-col">
                {sidebarNav.coreItems.map((item) =>
                  item.children && item.children.length > 0 ? (
                    <SidebarNavGroup
                      closeMobile={closeMobile}
                      collapsed={isDesktopCollapsed}
                      item={item}
                      key={item.path}
                      t={t}
                      tooltipWarmRef={tooltipWarmRef}
                    />
                  ) : (
                    <SidebarNavLink
                      closeMobile={closeMobile}
                      collapsed={isDesktopCollapsed}
                      item={item}
                      key={item.path}
                      t={t}
                      tooltipWarmRef={tooltipWarmRef}
                    />
                  ),
                )}
              </ul>

              {sidebarNav.pluginItems.length > 0 && (
                <div
                  aria-labelledby="qubit-sidebar-plugin-nav-heading"
                  className="flex flex-col border-t border-current/10 pb-2"
                  role="group"
                >
                  <span
                    className={cn(
                      "px-5 pt-2.5 pb-1",
                      "font-sans text-display text-xs tracking-[0.12em] text-text-tertiary",
                      isDesktopCollapsed && "lg:hidden",
                    )}
                    id="qubit-sidebar-plugin-nav-heading"
                  >
                    {t.app.pluginNavSection}
                  </span>

                  <ul className="flex flex-col">
                    {sidebarNav.pluginItems.map((item) => (
                      <SidebarNavLink
                        closeMobile={closeMobile}
                        collapsed={isDesktopCollapsed}
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
              onNavigate={closeMobile}
              status={sidebarStatus}
              tooltipWarmRef={tooltipWarmRef}
            />

            <div
              className={cn(
                "flex shrink-0 items-center gap-2",
                "px-3 py-2",
                "border-t border-current/20",
                isDesktopCollapsed
                  ? "lg:flex-col lg:items-start lg:gap-3 lg:py-3"
                  : "justify-between",
              )}
            >
              <div
                className={cn(
                  "flex min-w-0 items-center gap-2",
                  isDesktopCollapsed && "lg:flex-col lg:items-start",
                )}
              >
                <PluginSlot name="header-right" />

                <SidebarIconWithTooltip
                  collapsed={isDesktopCollapsed}
                  label={t.theme?.switchTheme ?? "Switch theme"}
                  tooltipWarmRef={tooltipWarmRef}
                >
                  <ThemeSwitcher collapsed={isDesktopCollapsed} dropUp />
                </SidebarIconWithTooltip>

                <SidebarIconWithTooltip
                  collapsed={isDesktopCollapsed}
                  label={t.language.switchTo}
                  tooltipWarmRef={tooltipWarmRef}
                >
                  <LanguageSwitcher collapsed={isDesktopCollapsed} dropUp />
                </SidebarIconWithTooltip>
              </div>
            </div>

            <div
              className={cn(
                "flex shrink-0 flex-col",
                isDesktopCollapsed && "lg:hidden",
              )}
            >
              <AuthWidget />
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
                  !chatOverriddenByPlugin &&
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
                      <Suspense
                        fallback={
                          isNativeChatRoute ? (
                            <RouteFallback label="Loading chat…" />
                          ) : null
                        }
                      >
                        <ChatPage isActive={isNativeChatRoute} />
                      </Suspense>
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
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);

  const navLabel = labelKey
    ? ((t.app.nav as Record<string, string>)[labelKey] ?? label)
    : label;
  const showTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <li
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      <NavLink
        to={path}
        end={item.exactEnd ?? path === "/sessions"}
        onClick={() => {
          closeMobile();
          onNavigate?.();
        }}
        title={navLabel}
        aria-label={collapsed ? navLabel : undefined}
        onFocus={collapsed ? showTooltip : undefined}
        onBlur={collapsed ? hideTooltip : undefined}
        className={({ isActive }) =>
          cn(
            "qagent-sidebar__link",
            "group/nav relative flex items-center gap-3",
            "px-5 py-2.5",
            // Submenu children sit one indent in (tailwind-merge keeps the
            // later pl over px-5's left padding).
            nested && "pl-10",
            nested && "qagent-sidebar__link--nested",
            "font-sans text-display uppercase text-sm tracking-[0.12em]",
            "whitespace-nowrap transition-colors cursor-pointer",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
            isActive
              ? "text-midground"
              : "text-text-secondary hover:text-midground",
          )
        }
        style={{
          clipPath: "var(--component-tab-clip-path)",
        }}
      >
        {({ isActive }) => (
          <>
            <Icon className="h-3.5 w-3.5 shrink-0" />

            <span
              className={cn(
                "qagent-sidebar__link-label truncate transition-opacity duration-300",
                collapsed ? "lg:hidden" : "lg:opacity-100",
              )}
            >
              {navLabel}
            </span>

            <span
              aria-hidden
              className="qagent-sidebar__hover-wash absolute inset-y-0.5 left-1.5 right-1.5 bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/nav:opacity-5"
            />

            {isActive && (
              <span
                aria-hidden
                className="qagent-sidebar__active-rail absolute left-0 top-0 bottom-0 w-px bg-midground"
              />
            )}

            {/* Disclosure chevron on a group parent. Hidden when the rail is
                collapsed to icons (no room), where the tooltip carries it. */}
            {expandable && !collapsed && (
              <span
                aria-hidden
                className="ml-auto flex shrink-0 items-center text-text-secondary transition-transform duration-200"
                style={{ transform: expanded ? "rotate(90deg)" : "none" }}
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </span>
            )}
          </>
        )}
      </NavLink>

      {collapsed && hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={navLabel} warmRef={tooltipWarmRef} />
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
    <div
      className={cn(
        "shrink-0 flex flex-col",
        "border-t border-current/10",
        "py-1",
      )}
    >
      <span
        className={cn(
          "px-5 pt-0.5 pb-0.5",
          "font-sans text-display text-xs tracking-[0.12em] text-text-tertiary",
          collapsed && "lg:hidden",
        )}
      >
        {t.app.system}
      </span>

      <div className={cn(collapsed && "lg:hidden")}>
        <SidebarStatusStrip status={status} latencyMs={latencyMs} />
      </div>

      <GatewayDot collapsed={collapsed} status={status} tooltipWarmRef={tooltipWarmRef} />

      <ul className="flex flex-col">
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
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const busy = isPending || isActionRunning;
  const displayLabel = isActionRunning ? runningLabel : label;
  const showTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <li
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      <button
        onClick={onClick}
        disabled={disabled}
        aria-busy={busy}
        title={item.disabledReason || undefined}
        aria-label={collapsed ? displayLabel : undefined}
        onFocus={collapsed ? showTooltip : undefined}
        onBlur={collapsed ? hideTooltip : undefined}
        type="button"
        className={cn(
          "group/action relative flex w-full items-center gap-3",
          "px-5 py-2.5",
          "font-sans text-display text-xs tracking-[0.1em]",
          "whitespace-nowrap transition-colors cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
          busy
            ? "text-midground"
            : "text-text-secondary hover:text-midground",
          "disabled:text-text-disabled disabled:cursor-not-allowed",
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

        <span className={cn(
          "truncate transition-opacity duration-300",
          collapsed ? "lg:opacity-0" : "lg:opacity-100",
        )}>
          {displayLabel}
        </span>

        <span
          aria-hidden
          className="absolute inset-y-0.5 left-1.5 right-1.5 bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/action:opacity-5"
        />

        {busy && (
          <span
            aria-hidden
            className="absolute left-0 top-0 bottom-0 w-px bg-midground"
          />
        )}
      </button>

      {collapsed && hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={displayLabel} warmRef={tooltipWarmRef} />
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
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const showTooltip = (event: MouseEvent<HTMLDivElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <div
      className={cn(
        "relative w-fit",
        collapsed && "group/icon",
      )}
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      {children}

      {collapsed && (
        <span
          aria-hidden
          className="absolute inset-y-0 inset-x-[-0.375rem] bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/icon:opacity-5 hidden lg:block"
        />
      )}

      {collapsed && hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={label} warmRef={tooltipWarmRef} />
      )}
    </div>
  );
}

function GatewayDot({ collapsed, status, tooltipWarmRef }: GatewayDotProps) {
  const { t } = useI18n();
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);

  const toneToColor: Record<string, string> = {
    "text-success": "bg-success",
    "text-warning": "bg-warning",
    "text-destructive": "bg-destructive",
    "text-muted-foreground": "bg-muted-foreground",
  };

  let color: string;
  let label: string;

  if (!status) {
    color = "bg-midground/20";
    label = t.status.gateway;
  } else {
    const gw = gatewayLine(status, t);
    color = toneToColor[gw.tone] ?? "bg-muted-foreground";
    label = `${t.status.gateway} ${gw.label}`;
  }
  const showTooltip = (event: MouseEvent<HTMLDivElement> | FocusEvent<HTMLDivElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <div
      className={cn(
        "hidden lg:flex py-3 pl-[1.625rem] transition-opacity duration-300",
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
        className={cn("h-1.5 w-1.5 rounded-full", color)}
      />

      {hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={label} warmRef={tooltipWarmRef} />
      )}
    </div>
  );
}

function SidebarTooltip({ anchor, label, warmRef }: SidebarTooltipProps) {
  const rect = anchor.getBoundingClientRect();
  const sidebar = document.getElementById("app-sidebar");
  const sidebarRight = sidebar?.getBoundingClientRect().right ?? rect.right;
  const [isWarm, setIsWarm] = useState(false);

  useEffect(() => {
    if (!warmRef) {
      setIsWarm(false);
      return;
    }
    const now = Date.now();
    setIsWarm(now - warmRef.current < 300);
    warmRef.current = now;
    return () => {
      if (warmRef) warmRef.current = Date.now();
    };
  }, [warmRef]);

  return createPortal(
    <span
      className={cn(
        "fixed z-[100] pointer-events-none",
        "px-2 py-1",
        "bg-background-base border border-current/20 shadow-lg",
        "font-sans text-display text-xs tracking-[0.1em] text-midground uppercase",
      )}
      style={{
        top: rect.top + rect.height / 2,
        left: sidebarRight + 8,
        transform: "translateY(-50%)",
        opacity: isWarm ? 1 : undefined,
        animation: isWarm ? "none" : "sidebar-tooltip-in 120ms ease-out",
      }}
    >
      {label}
    </span>,
    document.body,
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
  onNavigate: () => void;
  status: StatusResponse | null;
  tooltipWarmRef: TooltipWarmRef;
}

interface SidebarTooltipProps {
  anchor: HTMLElement;
  label: string;
  warmRef?: TooltipWarmRef;
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
