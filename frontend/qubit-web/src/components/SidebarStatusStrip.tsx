import { Link } from "react-router";
import type { StatusResponse } from "@/lib/api";
import { formatLatency } from "@/lib/latency-sampler";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

/** Gateway + session summary for the System sidebar block (no separate strip chrome). */
export function SidebarStatusStrip({ status, latencyMs }: SidebarStatusStripProps) {
  const { t } = useI18n();

  if (status === null) {
    return (
      <div className="px-5 py-1.5" aria-hidden>
        <div className="h-2 w-[80%] max-w-full animate-pulse rounded-sm bg-midground/10" />
      </div>
    );
  }

  const gw = gatewayLine(status, t);
  const { activeSessionsLabel, gatewayStatusLabel } = t.app;

  return (
    <Link
      to="/sessions"
      title={t.app.statusOverview}
      className={cn(
        "block text-left",
        "px-5 pb-2 pt-0.5",
        "text-text-secondary",
        "transition-colors hover:text-midground",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
        "focus-visible:ring-inset",
      )}
    >
      <div className="flex flex-col gap-1 font-sans text-xs leading-snug tracking-[0.08em]">
        <p className="break-words">
          <span className="text-text-tertiary">{gatewayStatusLabel}</span>{" "}
          <span className={cn("font-medium", gw.tone)}>{gw.label}</span>
        </p>

        <p className="break-words">
          <span className="text-text-tertiary">{activeSessionsLabel}</span>{" "}
          <span className="tabular-nums text-text-secondary">
            {status.active_sessions}
          </span>
        </p>

        {latencyMs !== undefined && (
          <p className="break-words">
            <span className="text-text-tertiary">Latency</span>{" "}
            <span className="tabular-nums text-text-secondary">
              {formatLatency(latencyMs)}
            </span>
          </p>
        )}
      </div>
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
