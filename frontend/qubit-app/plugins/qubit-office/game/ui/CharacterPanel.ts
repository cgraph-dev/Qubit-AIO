/* F09 — the character panel: the roster, the profile, and `Go to target`.
 *
 * THE THREE CLAIMS THIS PANEL HAS TO MAKE HONESTLY
 * ------------------------------------------------
 * 1. **Identity matches the world.** The card is keyed by the SAME
 *    `portraitKey` (`agent:<id>`) and points at the SAME `Place` the scene
 *    addresses, so the person in the panel and the figure on the floor are not
 *    two lookalikes — they are one identity with two renderings. The panel does
 *    not draw a face: no portrait art is authored in this build (F10 owns that),
 *    so the token is rendered as an identity chip and the panel says which
 *    artwork it is waiting on rather than showing a stand-in that would read as
 *    a real portrait.
 * 2. **Known/unknown is explicit.** `systems/CharacterPresenter.ts` hands every
 *    field a three-way state; this file renders all three, and a field the
 *    payload did not carry renders as a NAMED UNKNOWN (with the field's own
 *    name in it) rather than as a plausible default. The dossier is the case to
 *    watch: `known: false` does NOT hide the beliefs the backend sent.
 * 3. **`Go to target` is the F06 reducer.** It resolves a route through the
 *    DECLARED portal graph and refuses with the first hop's own reason. It
 *    moves the VIEWPOINT and never an agent (amendment § 3).
 *
 * AND THE ONE THING IT MUST NEVER DO (C4)
 * ---------------------------------------
 * This module contains no network call of any kind. It renders a read-only
 * view; every mutation in this product is a staged op through the existing
 * confirmation authority. `test_office_game_character.py` asserts that by
 * reading the source, because the property IS about what the module can do.
 *
 * No `import from "react"`: elements come from the host's `createElement`
 * (`OfficeGameShell.tsx` header explains why a second React runtime is fatal).
 */

import type { OfficeTowerPayload } from "../types";
import type { NavState, Place, Portal } from "../navigation";
import {
  ROSTER_FILTERS, dossierView, goToTarget, memoryView, presentCharacter,
  roleView, rosterView, supervisorView, worldIdentity,
  type CharacterProfile, type RosterFilter, type RosterFilters, type RosterView,
} from "../systems/CharacterPresenter";

type ReactLike = { createElement: (...args: unknown[]) => unknown };

/** The panel states a not-yet-known value can be in. Named, not prose. */
export type UnknownState = "known" | "unknown" | "absent" | "not-selected";

export interface CharacterPanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
  /** The selected seat, by STABLE id. Never a character object (see A03). */
  selectedAgentId?: number | null;
  /** Called with the new selection when a roster row is picked. */
  onSelect?: (agentId: number) => void;
  /** The portal graph `Go to target` routes through. */
  portals?: Portal[];
  /** The live navigation state, for the "where am I" line and the arrival. */
  nav?: NavState;
  /** Called with the navigation state `Go to target` produced. */
  onNavigate?: (nav: NavState) => void;
  /** Initial filters. */
  filters?: RosterFilters;
  /** True when the world's artwork is not authored yet (F10). Honest banner. */
  assetsPending?: boolean;
}

/** The stable testid for a roster row. */
export function rowTestId(agentId: number): string {
  return `character-row-${Number(agentId)}`;
}

/** The stable testid for a field, so a test can assert its state as data. */
export function fieldTestId(agentId: number, field: string): string {
  return `character-field-${Number(agentId)}-${field}`;
}

/**
 * Render an unknown as a NAMED gap. Every caller passes the field's own name,
 * so the UI can never show a bare "—" that a reader would fill in with a guess.
 */
export function unknownText(field: string, why: string): string {
  return `${field}: ${why}`;
}

/**
 * The panel's own summary of what it could NOT know about a profile. A caller
 * (and the tests) can assert this list rather than trusting that nothing was
 * defaulted.
 */
export function unknownFields(profile: CharacterProfile): string[] {
  return [...profile.unknownFields].sort();
}

// ---------------------------------------------------------------------------
// Go to target
// ---------------------------------------------------------------------------

export interface GoToView {
  enabled: boolean;
  /** Why it is disabled, when it is. A disabled control with no reason is a bug. */
  reason: string;
  target: string;
}

/**
 * Whether `Go to target` can do anything for this profile, BEFORE it is pressed.
 *
 * The distinction that matters: an unseated character is not a broken button,
 * it is a character with no place — and the panel says which.
 */
export function goToView(profile: CharacterProfile | null): GoToView {
  if (!profile) {
    return { enabled: false, reason: "no character is selected", target: "" };
  }
  if (!profile.world.place) {
    return {
      enabled: false,
      reason: profile.world.seated
        ? "this seat carries no floor, so there is no place to travel to"
        : "this character has no seat, so there is nowhere to go",
      target: "",
    };
  }
  return { enabled: true, reason: "", target: profile.world.placeKey };
}

/**
 * The click handler's work, extracted so a test can drive it without a DOM.
 *
 * It returns the next navigation state AND whether the route was refused, so
 * the caller can surface the reducer's own refusal reason instead of a silent
 * nothing (the F06 rule: a refusal is information).
 */
export function goToTargetFromPanel(
  nav: NavState,
  profile: CharacterProfile | null,
  portals: Portal[],
): ReturnType<typeof goToTarget> {
  return goToTarget(nav, profile, portals);
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

/** Field row: label + value + an explicit data-state for the value's knownness. */
function field(
  R: ReactLike, agentId: number, name: string, label: string,
  value: string, state: UnknownState,
): unknown {
  return R.createElement("div", {
    key: name,
    "data-testid": fieldTestId(agentId, name),
    "data-field": name,
    "data-state": state,
    className: "office-game__field",
  }, [
    R.createElement("span", {
      key: "l", className: "office-game__stat-label",
    }, label),
    R.createElement("span", { key: "v" }, value),
  ]);
}

/**
 * The beliefs block. THE POINT OF THE WHOLE PACKET.
 *
 * It renders on TWO axes, and neither may stand in for the other:
 *   * `basis` — the payload's own note saying why the dossier is not
 *     established (`no evidence yet: no seat row and no memory`). Verbatim.
 *   * the beliefs — each with its evidence string. ALWAYS, even when
 *     `known: false`, because the recorded payload sends them.
 *
 * A `known`-only implementation would show the basis and hide the beliefs, or
 * show the beliefs as though they were established. Both are wrong here.
 */
function beliefsBlock(
  R: ReactLike, profile: CharacterProfile,
): unknown {
  const d = profile.dossier;
  const rows = d.beliefs.map((b, i) => R.createElement("li", {
    key: `${b.dimension}-${i}`,
    "data-testid": `character-belief-${profile.agentId}-${i}`,
    "data-dimension": b.dimension,
    "data-evidence": b.evidence,
  }, `${b.belief} — evidence: ${b.evidence || "(none given)"}`));

  return R.createElement("div", {
    key: "dossier",
    "data-testid": fieldTestId(profile.agentId, "dossier"),
    "data-known": String(d.known),
    "data-belief-count": String(d.beliefs.length),
    // The flag and the data are BOTH reported, so a test can assert that a
    // `known: false` dossier still carried beliefs rather than asserting prose.
    "data-unestablished-evidence": String(d.hasUnestablishedEvidence),
  }, [
    R.createElement("div", { key: "h", className: "office-game__stat-label" },
      d.known ? "Dossier" : "Dossier (not established)"),
    d.basis
      ? R.createElement("div", {
          key: "basis",
          "data-testid": fieldTestId(profile.agentId, "dossier-basis"),
          className: "office-game__muted",
          style: { fontSize: "var(--office-fs-xs)" },
        }, d.basis)
      : null,
    rows.length > 0
      ? R.createElement("ul", {
          key: "beliefs",
          "data-testid": fieldTestId(profile.agentId, "dossier-beliefs"),
          style: { margin: 0, paddingLeft: "var(--office-s3)" },
        }, rows)
      : R.createElement("div", {
          key: "none",
          "data-testid": fieldTestId(profile.agentId, "dossier-beliefs-none"),
          className: "office-game__muted",
        }, unknownText("beliefs", "the payload sent none")),
  ]);
}

/** The profile card for one seat. */
export function renderProfile(
  R: ReactLike, profile: CharacterProfile,
): unknown {
  const memory = profile.memory;
  const gv = goToView(profile);
  return R.createElement("section", {
    "data-testid": `character-profile-${profile.agentId}`,
    "data-agent-id": String(profile.agentId),
    "data-slug": profile.slug,
    // The portrait token, identical to the world's identity key.
    "data-portrait": profile.portrait,
    "data-known": String(profile.dossier.known),
    "aria-label": `Profile of ${profile.slug}`,
    role: "region",
  }, [
    R.createElement("h3", { key: "name", "data-testid": "character-name" },
      profile.slug),
    field(R, profile.agentId, "portrait", "Portrait",
      `${profile.portrait} · identity matches the world figure`,
      "known"),
    field(R, profile.agentId, "role", "Role",
      profile.role.recognised
        ? `${profile.role.label} (${profile.role.seatType})`
        : unknownText("role", `"${profile.role.seatType}" is not a role this build knows`),
      profile.role.recognised ? "known" : "unknown"),
    field(R, profile.agentId, "supervisor", "Reports to",
      profile.supervisor.text,
      profile.supervisor.state === "assigned"
        || profile.supervisor.state === "none" ? "known" : "unknown"),
    field(R, profile.agentId, "department", "Department",
      profile.departmentId === null
        ? unknownText("department", "the payload sent null")
        : String(profile.departmentId),
      profile.departmentId === null ? "unknown" : "known"),
    field(R, profile.agentId, "memory", "Memory",
      memory.text, memory.known ? "known" : "unknown"),
    field(R, profile.agentId, "last-event", "Last event",
      profile.lastEvent.text,
      profile.lastEvent.state === "known" ? "known" : "absent"),
    field(R, profile.agentId, "animation", "Now",
      `${profile.animation} · at ${profile.at || "(location kind not reported)"}`,
      profile.at ? "known" : "unknown"),
    field(R, profile.agentId, "position", "Place",
      profile.world.placeKey
        ? `${profile.world.placeKey} (${profile.world.room})`
        : unknownText("place", profile.world.seated
            ? "the seat carries no floor"
            : "this character has no seat"),
      profile.world.placeKey ? "known" : "unknown"),
    field(R, profile.agentId, "stalled", "Stalled",
      profile.stalled ? "yes" : "no", "known"),
    beliefsBlock(R, profile),
    R.createElement("button", {
      key: "goto",
      type: "button",
      className: "office-game__btn",
      "data-testid": `character-goto-${profile.agentId}`,
      disabled: !gv.enabled,
      "data-target": gv.target,
      "aria-label": gv.enabled
        ? `Go to ${profile.slug}` : gv.reason,
    }, gv.enabled ? "Go to target" : `Go to target — ${gv.reason}`),
  ]);
}

/**
 * The roster. Rows are buttons so the list is keyboard-reachable without a
 * mouse — the A18 gap was exactly this (`no accessible roster`).
 */
export function renderRoster(
  R: ReactLike,
  view: RosterView,
  selectedAgentId: number | null,
  onSelect?: (agentId: number) => void,
): unknown {
  const rows = view.entries.map((e) => R.createElement("li", {
    key: e.agentId,
    "data-testid": `character-row-wrap-${e.agentId}`,
  }, R.createElement("button", {
    type: "button",
    className: "office-game__btn",
    "data-testid": rowTestId(e.agentId),
    "data-agent-id": String(e.agentId),
    "data-portrait": e.portrait,
    "data-seated": String(e.seated),
    "data-stalled": String(e.stalled),
    "data-animation": e.animation,
    "aria-pressed": selectedAgentId === e.agentId,
    onClick: () => { if (onSelect) onSelect(e.agentId); },
  }, [
    R.createElement("span", { key: "p", "aria-hidden": "true" }, "◧ "),
    R.createElement("span", { key: "s" }, e.slug),
    R.createElement("span", {
      key: "r", className: "office-game__muted",
    }, ` · ${e.role.label} · ${e.room || "no room"}`),
  ])));

  const head = R.createElement("div", {
    key: "head", "data-testid": "character-roster-count",
    "data-total": String(view.total),
    "data-shown": String(view.shown),
    "data-filter": view.filters.filter,
    className: "office-game__stat-label",
  }, `${view.shown} of ${view.total} seats · ${view.filters.filter}`);

  const empty = view.total > 0 && view.shown === 0
    ? R.createElement("div", {
        key: "filtered-out", "data-testid": "character-roster-filtered-out",
      }, unknownText("roster", "no seat matches this filter"))
    : view.total === 0
      ? R.createElement("div", {
          key: "none", "data-testid": "character-roster-none",
        }, view.empty
          ? `The office is empty. ${view.emptyReason}`
          : unknownText("roster", "no characters in this payload"))
      : null;

  return R.createElement("div", {
    key: "roster",
    "data-testid": "office-character-roster",
    role: "list",
    "aria-label": "Seats",
  }, [head, empty,
    R.createElement("ul", { key: "rows", style: { margin: 0, paddingLeft: 0,
      listStyle: "none" } }, rows)]);
}

/**
 * The whole panel: roster + profile + the honest banners.
 *
 * Pure renderer. It does not fetch, does not stage, and does not move the
 * viewpoint by itself — `Go to target`'s handler returns the reducer's result to
 * the caller through `onNavigate`.
 */
export function renderCharacterPanel(props: CharacterPanelProps): unknown {
  const { R, payload } = props;
  const filters: RosterFilters = props.filters
    ?? { filter: "all" as RosterFilter, query: "" };
  const view = rosterView(payload, filters);
  const selectedId = props.selectedAgentId ?? null;
  const rows = Array.isArray(payload?.characters) ? payload!.characters : [];
  const raw = selectedId === null
    ? null : rows.find((c) => Number(c.agent_id) === Number(selectedId)) ?? null;
  const profile = raw
    ? presentCharacter(raw, rows, Number(payload?.active_window_s ?? 0))
    : null;

  const filterBar = R.createElement("div", {
    key: "filters", "data-testid": "character-filters", role: "group",
    "aria-label": "Roster filters",
  }, ROSTER_FILTERS.map((f) => R.createElement("button", {
    key: f,
    type: "button",
    className: "office-game__btn",
    "data-testid": `character-filter-${f}`,
    "aria-pressed": f === filters.filter,
  }, f)));

  // THE HONEST BANNER. No portrait or sprite art is authored in this build, so
  // the panel says which packet owes it rather than drawing a stand-in that a
  // reader would take for a real portrait.
  const assets = props.assetsPending === false
    ? null
    : R.createElement("div", {
        key: "assets", role: "status", "data-testid": "character-assets-pending",
        className: "office-game__muted",
        style: { fontSize: "var(--office-fs-xs)" },
      }, "Portrait artwork is not authored yet (F10). The figure and this card " +
         "share one identity key, so they cannot disagree.");

  return R.createElement("section", {
    "data-testid": "office-character-panel",
    "aria-label": "Characters",
    role: "region",
  }, [
    assets,
    filterBar,
    renderRoster(R, view, selectedId, props.onSelect),
    profile
      ? renderProfile(R, profile)
      : R.createElement("div", {
          key: "no-selection", "data-testid": "character-profile-none",
          "data-state": selectedId === null ? "not-selected" : "absent",
        }, selectedId === null
          ? "Select a seat to see its profile."
          : unknownText("profile", `seat #${selectedId} is not in this payload`)),
  ]);
}

// ---------------------------------------------------------------------------
// The Go to target wiring, for a caller that has a live nav state
// ---------------------------------------------------------------------------

/**
 * Run `Go to target` for the selected seat and hand the caller the reducer's
 * own outcome. Returns the refusal REASON as well as the state, so the caller
 * surfaces it rather than rendering a dead button.
 */
export function travelToSelected(
  nav: NavState,
  payload: OfficeTowerPayload | null,
  selectedAgentId: number | null,
  portals: Portal[],
): { nav: NavState; arrived: boolean; refused: boolean; reason: string;
     target: string } {
  const rows = Array.isArray(payload?.characters) ? payload!.characters : [];
  const raw = selectedAgentId === null ? null
    : rows.find((c) => Number(c.agent_id) === Number(selectedAgentId)) ?? null;
  if (!raw) {
    return {
      nav, arrived: false, refused: true, target: "",
      reason: "no seat is selected, so there is nothing to travel to",
    };
  }
  const profile = presentCharacter(
    raw, rows, Number(payload?.active_window_s ?? 0));
  const res = goToTarget(nav, profile, portals);
  return {
    nav: res.nav, arrived: res.arrived, refused: res.refused,
    reason: res.reason, target: res.target,
  };
}

/** Re-exported so a caller needs one import for the profile it renders. */
export {
  dossierView, memoryView, presentCharacter, roleView, supervisorView,
  worldIdentity,
};
