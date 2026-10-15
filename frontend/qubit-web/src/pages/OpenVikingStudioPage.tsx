/**
 * Open Viking Studio page (`/open-viking`) — the REAL Volcengine studio,
 * embedded.
 *
 * WHY THIS EXISTS: the sibling Qubit pages (`/open-viking/retrieval`,
 * `/uploads`, `/agent-hub`, `/resources`) render our LOCAL context engine
 * (`voicebridge/context_store.py`, `engine: "qubit-local-context"`). The
 * OpenViking *server* (systemd unit `openviking.service`, 127.0.0.1:1933)
 * ships its own full SPA — Workspace / Activity / Settings / Resources with
 * 18 routes — which is the actual product UI and is actively developed
 * upstream. Reimplementing that menu here would drift and always lag, so this
 * page embeds the real thing instead.
 *
 * WHY AN IFRAME IS SAFE HERE (verified, not assumed):
 *   - `:1933/studio/*` sends NO `X-Frame-Options` and no `frame-ancestors`, so
 *     embedding is permitted (unlike OmniRoutePage, whose upstream is
 *     `DENY` and therefore has to open a new tab).
 *   - The studio is a Vite SPA: every deep route
 *     (`/studio/retrieval`, `/studio/filesystem`, …) serves the same shell, so
 *     the in-iframe navigation works without us mirroring its menu.
 *   - `auth_mode: "dev"` — no login to bridge, and the app calls `/api/v1/*`
 *     on its OWN origin, so no proxying or CORS work is required.
 *   - Bound to loopback only (`--host 127.0.0.1`).
 *
 * The embed is a progressive enhancement: if the server is down or the browser
 * refuses the frame, the status panel below still renders the wiring honestly
 * and offers an "open in new tab" escape. Nothing is faked.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
  SettingsValue,
} from "@/components/console-ui";
import { fetchJSON } from "@/lib/api";

/** The OpenViking server (systemd user unit `openviking.service`). Loopback. */
const STUDIO_ORIGIN = "http://127.0.0.1:1933";
const STUDIO_HOME = `${STUDIO_ORIGIN}/studio/home`;

/** The studio's own top-level groups, shown as a jump bar (its real routes). */
const STUDIO_GROUPS: Array<{ label: string; items: Array<{ label: string; path: string }> }> = [
  {
    label: "Workspace",
    items: [
      { label: "Home", path: "/studio/home" },
      { label: "Filesystem", path: "/studio/filesystem" },
      { label: "VikingBot", path: "/studio/vikingbot" },
      { label: "Retrieval", path: "/studio/retrieval" },
      { label: "Skills", path: "/studio/skills" },
      { label: "Compile", path: "/studio/compile" },
    ],
  },
  {
    label: "Activity",
    items: [
      { label: "Sessions", path: "/studio/sessions" },
      { label: "Agent Experience", path: "/studio/agent-experience" },
      { label: "Request Logs", path: "/studio/request-logs" },
      { label: "Task Center", path: "/studio/task-center" },
      { label: "Scheduled Sync", path: "/studio/scheduled-sync" },
      { label: "Monitoring", path: "/studio/monitoring" },
    ],
  },
  {
    label: "Settings",
    items: [
      { label: "Connection Settings", path: "/studio/settings/connection" },
      { label: "OAuth verify", path: "/studio/settings/oauth-verify" },
    ],
  },
  {
    label: "Resources",
    items: [
      { label: "Documentation", path: "/studio/resources/documentation" },
      { label: "SDK & API", path: "/studio/resources/sdk-api" },
      { label: "Agent Integrations", path: "/studio/resources/agent-integrations" },
    ],
  },
];

type Health = "checking" | "running" | "unreachable";

export default function OpenVikingStudioPage() {
  const [health, setHealth] = useState<Health>("checking");
  const [detail, setDetail] = useState<string>("");
  const [route, setRoute] = useState<string>(STUDIO_HOME);
  const [nonce, setNonce] = useState<number>(0);
  const [wiring, setWiring] = useState<string>("");

  const probe = useCallback(async () => {
    setHealth("checking");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    try {
      // The served CSP blocks a cross-origin fetch to :1933 from this origin,
      // so probe through the Qubit backend (same-origin) instead. The route
      // returns the health payload at { result: {...} } per the family
      // convention (see ocFamilyVikingApi). A missing route surfaces as a
      // normal error, which we render honestly rather than faking.
      const res = await fetchJSON<{
        result?: {
          ok?: boolean;
          version?: string;
          auth_mode?: string;
          error?: string;
        };
      }>("/api/qubit/viking/studio-health");
      const r = res?.result;
      if (r?.ok) {
        setHealth("running");
        setDetail(
          [
            r.version ? `v${r.version}` : "",
            r.auth_mode ? `auth: ${r.auth_mode}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        );
      } else {
        setHealth("unreachable");
        setDetail(r?.error ?? "health check failed");
      }
    } catch (err) {
      setHealth("unreachable");
      setDetail(err instanceof Error ? err.message : "health check failed");
    } finally {
      clearTimeout(timer);
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  // Report the memory wiring from the dashboard side, so the page shows WHY the
  // studio matters to Qubit (memory.provider = openviking).
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const cfg = await fetchJSON<{
          result?: { memory?: { provider?: string } };
          memory?: { provider?: string };
        }>("/api/qubit/viking/studio-wiring");
        const provider =
          cfg?.result?.memory?.provider ?? cfg?.memory?.provider ?? "";
        if (alive && typeof provider === "string" && provider) setWiring(provider);
      } catch {
        /* non-fatal: the row simply shows nothing */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const statusBadge = useMemo(() => {
    if (health === "checking") return <Badge tone="secondary">checking…</Badge>;
    if (health === "running") return <Badge tone="success">running</Badge>;
    return <Badge tone="destructive">unreachable</Badge>;
  }, [health]);

  return (
    <SettingsPage>
      {/* No h1 here: the layout owns the page title via ROUTE_TITLES
          (see lib/resolve-page-title.ts), matching the sibling
          OpenViking*Page convention. */}
      <SettingsPageHeader
        title="Open Viking Studio"
        description="The OpenViking Studio, embedded. Qubit's context database — one filesystem for knowledge, memory and skills."
      />

      <SettingsSection title="Server">
        <SettingsRow
          title="Status"
          control={
            <div className="flex items-center gap-2">
              {statusBadge}
              {detail ? <span className="text-xs opacity-70">{detail}</span> : null}
            </div>
          }
        />
        <SettingsRow
          title="Endpoint"
          control={<SettingsValue>{STUDIO_ORIGIN}</SettingsValue>}
        />
        <SettingsRow
          title="Qubit memory provider"
          control={<SettingsValue>{wiring || "—"}</SettingsValue>}
        />
        <SettingsRow
          title="Service"
          control={<SettingsValue>openviking.service (user)</SettingsValue>}
        />
        <SettingsRow
          title="Actions"
          control={
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" outlined onClick={() => void probe()}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" /> Recheck
              </Button>
              {/* A plain anchor, not a Button: ButtonProps has no `asChild`
                  (see button.d.ts), and the repo idiom for a link action is a
                  styled <a> (OmniRoutePage.tsx:83-90). */}
              <a
                href={route}
                target="_blank"
                rel="noreferrer"
                className="btn btn--outlined btn--sm inline-flex items-center"
              >
                <ExternalLink className="mr-1 h-3.5 w-3.5" /> Open in new tab
              </a>
            </div>
          }
        />
      </SettingsSection>

      {/* Jump bar: the studio's own groups, as real deep links into the embed. */}
      <SettingsSection title="Studio">
        <div className="flex flex-wrap gap-x-6 gap-y-2 px-1 pb-2">
          {STUDIO_GROUPS.map((group) => (
            <div key={group.label} className="min-w-[10rem]">
              <div className="mb-1 text-[11px] uppercase tracking-wide opacity-60">
                {group.label}
              </div>
              <div className="flex flex-col gap-1">
                {group.items.map((item) => {
                  const active = route.endsWith(item.path);
                  return (
                    <button
                      key={item.path}
                      type="button"
                      onClick={() => setRoute(`${STUDIO_ORIGIN}${item.path}`)}
                      className={
                        "text-left text-xs hover:underline " +
                        (active ? "font-semibold" : "opacity-80")
                      }
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </SettingsSection>

      <SettingsSection title="Embed">
        {health === "unreachable" ? (
          <div className="px-1 py-3 text-sm opacity-80">
            The OpenViking server is not answering on {STUDIO_ORIGIN}. Start it with{" "}
            <code>systemctl --user start openviking.service</code>, then Recheck. The embed
            stays hidden rather than showing a broken frame.
          </div>
        ) : (
          <div className="overflow-hidden rounded-md border border-[var(--border)]">
            <iframe
              key={nonce}
              title="OpenViking Studio"
              src={route}
              className="h-[75vh] w-full bg-white"
              // The studio is our own loopback service; it needs no elevated
              // privileges. No allow-same-origin coupling to THIS origin.
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
              referrerPolicy="no-referrer"
            />
          </div>
        )}
        {health !== "unreachable" ? (
          <div className="flex items-center justify-between px-1 pt-2">
            <span className="text-xs opacity-60">
              Showing {route.replace(STUDIO_ORIGIN, "")}
            </span>
            <Button size="sm" ghost onClick={() => setNonce((n) => n + 1)}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" /> Reload frame
            </Button>
          </div>
        ) : null}
      </SettingsSection>
    </SettingsPage>
  );
}
