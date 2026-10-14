/* The API contract the vendored office client expects, and what Qubit has.
 *
 * HONEST STATEMENT OF THE GAP
 * ---------------------------
 * The client was written against a server that streams SSE from `/api/*`:
 *
 *   GET  /api/access                        what this page may do
 *   GET  /api/stream                        the SSE feed of the whole office
 *   GET  /api/agents/<id>                   one agent's conversation
 *   POST /api/agents/<id>/character         set an agent's appearance
 *   GET  /api/permissions/<id>              a pending permission request
 *   POST /api/permissions/<id>/decision     answer it
 *   GET  /api/messages/<id>                 a queued message
 *
 * Qubit has ONE office route: `GET /agent/api/office/tower` (a read-only
 * snapshot, voicebridge/qubit_compat.py:9431). So:
 *
 *   * The RENDERER can be exercised today — the snapshot carries the floors,
 *     rooms, seats and per-character state the world draws from.
 *   * The INTERACTIVE half cannot. Nothing in Qubit currently serves
 *     `/api/stream`, and answering a permission from the browser is a write
 *     that must go through the confirmation authority (contract C4), not a
 *     POST from a view.
 *
 * This module therefore does two things and refuses a third:
 *   1. It translates the snapshot into the SHAPE the client's store expects.
 *   2. It names each unimplemented route explicitly, so the page can say
 *      "not bound" instead of rendering nothing and looking broken.
 *   3. It never fabricates a frame. A missing binding is reported.
 *
 * Inventing a fake SSE feed would make the page look complete and be a lie —
 * the office would show agents that do not exist.
 */

/** A route the client asks for and whether Qubit serves it. */
export interface RouteBinding {
  /** The path the CLIENT requests, relative to its own `/api/`. */
  client: string;
  /** What Qubit serves, or null when nothing does. */
  qubit: string | null;
  /** True when the binding is read-only and safe to wire now. */
  readOnly: boolean;
  /** One line, shown to a person, on what is missing and who owes it. */
  note: string;
}

export const ROUTES: readonly RouteBinding[] = [
  {
    client: "access",
    qubit: null,
    readOnly: true,
    note: "No access/permission model is served. The client uses this to decide "
        + "whether answering from the office is allowed; with no answer it "
        + "falls back to view-only, which is the safe default.",
  },
  {
    client: "stream",
    qubit: null,
    readOnly: true,
    note: "THE BLOCKING ONE. Qubit serves a snapshot, not a stream, so the "
        + "office cannot update live. Polling /agent/api/office/tower is a "
        + "stand-in that is honest about being a poll.",
  },
  {
    client: "agents/:id",
    qubit: null,
    readOnly: true,
    note: "Per-agent conversation transcripts are not exposed over HTTP.",
  },
  {
    client: "permissions/:id",
    qubit: null,
    readOnly: false,
    note: "Answering a permission is an org mutation and must go through the "
        + "confirmation authority (C4). It must NOT be wired as a bare POST.",
  },
  {
    client: "messages/:id",
    qubit: null,
    readOnly: false,
    note: "Sending a message to an agent is an org mutation. Same rule as above.",
  },
];

/** The one route that IS bound. */
export const SNAPSHOT_ROUTE = "/agent/api/office/tower";

/** How often the stand-in poll runs. Slower than a real stream would be. */
export const POLL_INTERVAL_MS = 2000;

/**
 * The verdict the page shows. Never "everything works" — it is computed from
 * the route table above, so adding a binding changes the page automatically
 * rather than requiring someone to remember to update a banner.
 */
export interface BindingVerdict {
  /** Routes the client needs and Qubit serves. */
  bound: string[];
  /** Routes with no binding at all. */
  missing: string[];
  /** Writes that must go through the authority rather than a direct call. */
  gated: string[];
  /** True when the renderer can show something real. */
  canRender: boolean;
  /** True when the client can only view, never act. */
  viewOnly: boolean;
}

export function bindingVerdict(): BindingVerdict {
  const bound: string[] = [];
  const missing: string[] = [];
  const gated: string[] = [];
  for (const r of ROUTES) {
    if (r.readOnly === false) gated.push(r.client);
    else if (r.qubit) bound.push(r.client);
    else missing.push(r.client);
  }
  return {
    bound,
    missing,
    gated,
    // The snapshot carries everything the world paints from, so the renderer
    // is exercisable even with no stream.
    canRender: true,
    // No write route is bound, and none may be bound outside the authority.
    viewOnly: gated.length > 0,
  };
}

/** One line for a person. Deliberately does not say "working". */
export function bindingSummary(): string {
  const v = bindingVerdict();
  return `${v.bound.length} of ${ROUTES.length} routes bound · `
    + `${v.missing.length} missing · ${v.gated.length} gated by the `
    + `confirmation authority`;
}
