import { Link } from "react-router";
import type { StatusResponse } from "@/lib/api";
import { formatLatency } from "@/lib/latency-sampler";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

/**
 * Gateway + session summary for the rail's System block.
 *
 * Renders as a hairline rows grid (`key ......... value`) inside the
 * telemetry card — the label column is muted uppercase mono so a glance at
 * the rail reads as a status readout rather than a second menu.
 */
export function SidebarStatusStrip({ status, latencyMs }: SidebarStatusStripProps) {
  const { t } = useI18n();

  if (status === null) {
    return (
      <div className="flex flex-col gap-1.5" aria-hidden>
        <div className="h-2 w-[80%] max-w-full animate-pulse rounded-sm bg-midground/10" />
        <div className="h-2 w-[55%] max-w-full animate-pulse rounded-sm bg-midground/10" />
        <div className="h-2 w-[42%] max-w-full animate-pulse rounded-sm bg-midground/10" />
      </div>
    );
  }

  const gw = gatewayLine(status, t);
  const { activeSessionsLabel, gatewayStatusLabel } = t.app;
  // The i18n labels ship with a trailing colon ("Gateway Status:"); the
  // key/value grid supplies its own separator.
  const strip = (value: string) => value.replace(/[:：]\s*$/, "");

  return (
    <Link
      to="/sessions"
      title={t.app.statusOverview}
      className={cn(
        "block text-left",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
      )}
    >
      <span className="qagent-sidebar__telemetry-row">
        <span className="qagent-sidebar__telemetry-key">{strip(gatewayStatusLabel)}</span>
        <span className={cn("qagent-sidebar__telemetry-value", gw.tone)}>{gw.label}</span>
      </span>

      <span className="qagent-sidebar__telemetry-row">
        <span className="qagent-sidebar__telemetry-key">{strip(activeSessionsLabel)}</span>
        <span className="qagent-sidebar__telemetry-value">{status.active_sessions}</span>
      </span>

      {latencyMs !== undefined && (
        <span className="qagent-sidebar__telemetry-row">
          <span className="qagent-sidebar__telemetry-key">Latency</span>
          <span className="qagent-sidebar__telemetry-value">
            {latencyMs === null ? "—" : formatLatency(latencyMs)}
          </span>
        </span>
      )}
    </Link>
  );
}

export function gatewayLine(
  status: StatusResponse,
  t: ReturnType<typeof useI18n>["t"],
): { label: string; tone: string } {
  const g = t.app.gatewayStrip;
  // `status.gateway_state` is the real Qubit supervisor's state, not a
  // vendored label. `voicebridge/gateway_supervisor.status()` owns it:
  //   * while the service is active the wire value is its `state` —
  //     "OK" / "DEGRADED" / "UNAVAILABLE" / "NOT_INSTALLED";
  //   * while it is not active the wire value is the systemd `ActiveState` —
  //     "active" / "activating" / "reloading" / "deactivating" / "inactive" /
  //     "failed" / "unknown".
  // Map that real vocabulary so the strip reflects the process instead of
  // silently collapsing every non-running state to "Off". The canonical
  // labels are retained for the legacy vendored gateway states.
  const byState: Record<string, { label: string; tone: string }> = {
    // Canonical label states (legacy/vendored gateway vocabulary).
    running: { label: g.running, tone: "text-success" },
    starting: { label: g.starting, tone: "text-warning" },
    startup_failed: { label: g.failed, tone: "text-destructive" },
    stopped: { label: g.stopped, tone: "text-muted-foreground" },
    // Qubit supervisor `state` (gateway_supervisor.status()["state"]).
    OK: { label: g.running, tone: "text-success" },
    DEGRADED: { label: g.off, tone: "text-muted-foreground" },
    UNAVAILABLE: { label: g.off, tone: "text-muted-foreground" },
    NOT_INSTALLED: { label: g.off, tone: "text-muted-foreground" },
    // systemd `ActiveState` (surfaced when the service is not active).
    active: { label: g.running, tone: "text-success" },
    activating: { label: g.starting, tone: "text-warning" },
    reloading: { label: g.starting, tone: "text-warning" },
    deactivating: { label: g.stopped, tone: "text-muted-foreground" },
    inactive: { label: g.stopped, tone: "text-muted-foreground" },
    failed: { label: g.failed, tone: "text-destructive" },
  };
  if (status.gateway_state && byState[status.gateway_state]) {
    return byState[status.gateway_state];
  }
  return status.gateway_running
    ? { label: g.running, tone: "text-success" }
    : { label: g.off, tone: "text-muted-foreground" };
}

interface SidebarStatusStripProps {
  status: StatusResponse | null;
  /** Latest sampled round-trip to the gateway, in ms (null = not sampled yet).
   *  `undefined` omits the latency line entirely. */
  latencyMs?: number | null;
}
