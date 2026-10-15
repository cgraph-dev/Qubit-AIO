/* Verified capability contract for the embedded Habblaud client.
 * Read-only endpoints are bound to Qubit's office/session stores. Writes and
 * absent upstream concepts are deliberately not mapped to unrelated APIs.
 */
export interface RouteBinding {
  client: string;
  qubit: string | null;
  readOnly: boolean;
  gated?: boolean;
  note: string;
}

export const ROUTES: readonly RouteBinding[] = [
  { client: "access", qubit: "/api/access", readOnly: true,
    note: "Qubit local view and permitted capabilities." },
  { client: "stream", qubit: "/api/stream", readOnly: true,
    note: "SSE snapshots and notices translated from the Qubit tower report." },
  { client: "agents/:id", qubit: "/api/agents/:id", readOnly: true,
    note: "Qubit seat and office-event history; not an upstream conversation." },
  { client: "sessions/recent", qubit: "/api/sessions/recent", readOnly: true,
    note: "Actual Qubit recent sessions." },
  { client: "timeline/days", qubit: "/api/timeline/days", readOnly: true,
    note: "Days with real office-ledger records only." },
  { client: "timeline/:day", qubit: "/api/timeline/:day", readOnly: true,
    note: "Real Qubit ledger detail; no visual replay is implied." },
  { client: "stats/days", qubit: "/api/stats/days", readOnly: true,
    note: "Per-day totals derived from the Qubit office ledger." },
  { client: "stats?day=", qubit: "/api/stats", readOnly: true,
    note: "Real Qubit ledger totals for the requested day." },
  { client: "permissions/:id", qubit: null, readOnly: false, gated: true,
    note: "Permission decisions need the Qubit confirmation authority." },
  { client: "messages", qubit: null, readOnly: false, gated: true,
    note: "Sending messages is an org mutation requiring confirmation." },
  { client: "terminal/pty", qubit: null, readOnly: false,
    note: "No raw terminal/PTY capability is exposed by this app." },
  { client: "spawn", qubit: null, readOnly: false,
    note: "Starting an agent is not exposed through this client adapter." },
  { client: "renameRoom", qubit: null, readOnly: false,
    note: "Room rename is not exposed from this embedded app." },
  { client: "deliveries", qubit: null, readOnly: true,
    note: "No Qubit delivery store is bound." },
  { client: "recording/replay", qubit: null, readOnly: true,
    note: "Qubit has no Habblaud screen recording/replay source." },
];

export const SNAPSHOT_ROUTE = "/agent/api/office/tower";
export const STREAM_ROUTE = "/api/stream";
export const POLL_INTERVAL_MS = 2000;

export interface BindingVerdict {
  bound: string[];
  missing: string[];
  gated: string[];
  canRender: boolean;
  viewOnly: boolean;
  live: boolean;
}

export function bindingVerdict(): BindingVerdict {
  const bound: string[] = [];
  const missing: string[] = [];
  const gated: string[] = [];
  for (const route of ROUTES) {
    if (route.gated) gated.push(route.client);
    else if (route.qubit) bound.push(route.client);
    else missing.push(route.client);
  }
  return {
    bound,
    missing,
    gated,
    canRender: true,
    viewOnly: true,
    live: ROUTES.some((route) => route.client === "stream" && !!route.qubit),
  };
}

export function bindingSummary(): string {
  const v = bindingVerdict();
  return `${v.bound.length} bound · ${v.missing.length} unavailable · `
    + `${v.gated.length} confirmation-gated`;
}
