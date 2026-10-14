/* F12 — the knowledge panel: what the org knows, from memory + dossier.
 *
 * SOURCE: `characters[].memory` (`{count, known}`) and `characters[].dossier`
 * (`{known, agent_id, dimensions[], beliefs[], memory_count, note}`). Nothing is
 * invented; a seat the org has no memory of says so.
 *
 * THE GOTCHA types.ts WARNS ABOUT, REPRODUCED HERE SO IT CANNOT BE "FIXED"
 * WRONGLY: `dossier.known === false` while `dossier.beliefs` is NON-EMPTY.
 * Known-ness is a FIELD, never "is the beliefs array empty". The beliefs carry
 * provenance (an `evidence` string) even when the dossier is NOT established —
 * a belief with evidence is a belief, whether or not the dossier is "known".
 * So this panel renders known-ness from `known` and the beliefs from `beliefs`,
 * independently. Collapsing them would either hide real beliefs or claim a
 * certainty the payload denies.
 */

import type { OfficeCharacter, OfficeTowerPayload } from "../types";

export interface KnowledgeSeat {
  agentId: number;
  slug: string;
  seatType: string;
  /** The dossier's OWN known-ness field. Never derived from beliefs.length. */
  dossierKnown: boolean;
  /** The memory's OWN known-ness field. */
  memoryKnown: boolean;
  memoryCount: number;
  dimensions: string[];
  beliefs: Array<{ dimension: string; belief: string; evidence: string }>;
  /** The payload's own note, carried verbatim. */
  note: string;
}

/** One knowledge row per seat, from the character object. No synthesis. */
export function knowledgeSeats(
  payload: OfficeTowerPayload | null,
): KnowledgeSeat[] {
  if (!payload || !Array.isArray(payload.characters)) return [];
  return payload.characters.map((c: OfficeCharacter) => ({
    agentId: c.agent_id,
    slug: c.slug,
    seatType: c.seat_type,
    // The FIELD, not the array length. See the header.
    dossierKnown: c.dossier?.known === true,
    memoryKnown: c.memory?.known === true,
    memoryCount: Number(c.memory?.count ?? 0),
    dimensions: Array.isArray(c.dossier?.dimensions)
      ? c.dossier.dimensions.map((d) => String(d)) : [],
    beliefs: (c.dossier?.beliefs ?? []).map((b) => ({
      dimension: String(b.dimension),
      belief: String(b.belief),
      evidence: String(b.evidence),
    })),
    note: String(c.dossier?.note ?? ""),
  }));
}

/** A roll-up for the header: how much does the org actually know? */
export interface KnowledgeSummary {
  seats: number;
  withDossier: number;
  withMemory: number;
  /** Seats carrying beliefs while their dossier is NOT established. */
  beliefsWithoutDossier: number;
  totalBeliefs: number;
  /** Evidence-backed beliefs / total beliefs, scaled by 100. */
  evidenceCoveragePct: number;
}

export function summariseKnowledge(
  payload: OfficeTowerPayload | null,
): KnowledgeSummary {
  const seats = knowledgeSeats(payload);
  const totalBeliefs = seats.reduce((n, s) => n + s.beliefs.length, 0);
  const backed = seats.reduce(
    (n, s) => n + s.beliefs.filter((b) => b.evidence.length > 0).length, 0);
  return {
    seats: seats.length,
    withDossier: seats.filter((s) => s.dossierKnown).length,
    withMemory: seats.filter((s) => s.memoryKnown).length,
    beliefsWithoutDossier:
      seats.filter((s) => !s.dossierKnown && s.beliefs.length > 0).length,
    totalBeliefs,
    evidenceCoveragePct: totalBeliefs === 0
      ? 0 : Math.round((backed / totalBeliefs) * 100),
  };
}

/**
 * A seat's knowledge state, named honestly. Four states, and the fourth —
 * "beliefs but the dossier is not established" — is the one a naive
 * implementation loses.
 */
export function knowledgeState(seat: KnowledgeSeat):
  "known" | "beliefs-unconfirmed" | "unknown" | "empty" {
  if (seat.dossierKnown && seat.memoryKnown) return "known";
  if (seat.beliefs.length > 0) return "beliefs-unconfirmed";
  if (seat.dimensions.length > 0) return "unknown";
  return "empty";
}

// ---------------------------------------------------------------------------
// Render — uses the HOST's React.
// ---------------------------------------------------------------------------
type ReactLike = { createElement: (...args: unknown[]) => unknown };

export interface KnowledgePanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
}

export function renderKnowledgePanel(props: KnowledgePanelProps): unknown {
  const { R, payload } = props;
  const seats = knowledgeSeats(payload);
  const summary = summariseKnowledge(payload);

  if (seats.length === 0) {
    return R.createElement("section", {
      "data-testid": "office-knowledge-panel", "data-seats": "0",
      role: "region", "aria-label": "Knowledge",
    }, "No seats in this payload.");
  }

  const head = R.createElement("div", {
    key: "head", "data-testid": "knowledge-summary",
    "data-with-dossier": String(summary.withDossier),
    "data-beliefs-without-dossier": String(summary.beliefsWithoutDossier),
    "data-evidence-coverage": String(summary.evidenceCoveragePct),
  }, `${summary.withDossier}/${summary.seats} seats have a dossier · ` +
     `${summary.totalBeliefs} beliefs, ` +
     `${summary.evidenceCoveragePct}% evidence-backed`);

  const rows = seats.map((s) => R.createElement("div", {
    key: s.agentId,
    "data-testid": `knowledge-seat-${s.agentId}`,
    "data-dossier-known": String(s.dossierKnown),
    "data-memory-known": String(s.memoryKnown),
    "data-beliefs": String(s.beliefs.length),
    "data-state": knowledgeState(s),
  }, [
    R.createElement("div", { key: "h" },
      `${s.slug} (${s.seatType}) — ${knowledgeState(s)}`),
    s.beliefs.length > 0
      ? R.createElement("ul", { key: "b",
          "data-testid": `knowledge-beliefs-${s.agentId}` },
        s.beliefs.map((b, i) => R.createElement("li", {
          key: `${b.dimension}:${i}`,
          "data-testid": `knowledge-belief-${s.agentId}-${i}`,
          "data-has-evidence": String(b.evidence.length > 0),
        }, `${b.dimension}: ${b.belief} (evidence: ${b.evidence || "none"})`)))
      : null,
    s.note
      ? R.createElement("div", {
          key: "n", "data-testid": `knowledge-note-${s.agentId}`,
          style: { fontSize: "var(--office-fs-xs)",
                   color: "var(--office-muted)" } }, s.note)
      : null,
  ]));

  return R.createElement("section", {
    "data-testid": "office-knowledge-panel",
    "data-seats": String(seats.length),
    role: "region", "aria-label": "Knowledge",
  }, [head, ...rows]);
}
