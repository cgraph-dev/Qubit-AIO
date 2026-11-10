/* The agents panel: every seat the building plan places, grouped by division.
 *
 * WHAT IT SHOWS, AND WHAT IT WILL NOT
 * -----------------------------------
 * It renders the roster the OFFICE actually plans — the seat list the route
 * serves — with each seat's real persona name, seat type, room and supervisor,
 * under the division the seat records. It never invents a persona, a count or a
 * division: the route refuses an inconsistent roster and this component renders
 * that refusal as a NAMED state, so an empty panel and an unreachable one can
 * never look the same.
 *
 * WHY IT LIVES OUTSIDE THE VENDORED IFRAME
 * ----------------------------------------
 * The embedded Habblaud document has no hook for a Qubit panel, and its
 * api-bridge answers every non-GET with a 501 (see api-bridge.ts) because Qubit
 * routes office writes through the confirmation authority. So the panel is
 * part of the Qubit-owned shell AROUND the frame — beside the game, like the
 * HUD dock and the seat-model panel — and reads over real HTTP with the host
 * session token. That is the same mechanism those two panels use, and it is
 * the one that can cross the document boundary: a `window.__QUBIT_PLUGINS__`
 * lookup from inside the frame could never see the host's registry (the
 * failure that killed the first HUD drawer — see ui/HudPanel.ts).
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  type AgentDivision,
  type AgentPanelState,
  type AgentSeat,
  fetchAgentPanel,
  seatTypeLabel,
  summaryLine,
} from "./agents-panel";
import {
  type OfficeTheme,
  bodyCellStyle,
  initialsFor,
  portraitCellStyle,
  preloadThemeAtlases,
  themeAtlasUrls,
} from "./office-theme";

export interface AgentsPanelProps {
  /** An explicit base path (a test); otherwise the host's injected global. */
  base?: string;
  /** Injected for tests; defaults to the page's own fetch. */
  fetchImpl?: typeof fetch;
  /**
   * The active theme payload. Task 6 owns fetching and applying it, and passes
   * the result here. Omitted/classic means no atlas: every avatar stays on the
   * initials -> emoji fallback and the dialogue card shows that portrait only.
   */
  theme?: OfficeTheme | null;
}

/** The panel portrait box. The payload's portrait tier is 32px (see Task 4). */
const PORTRAIT_PX = 32;

function seatMeta(seat: AgentSeat): string[] {
  const meta: string[] = [seatTypeLabel(seat.seat_type)];
  if (seat.room) meta.push(seat.room === "boardroom" ? "Boardroom" : seat.room);
  if (seat.supervisor) meta.push(`reports to ${seat.supervisor}`);
  return meta;
}

/**
 * One avatar, with the owner-directed fallback chain:
 *   generated atlas portrait -> initials chip -> emoji.
 *
 * The portrait is a CSS SPRITE, not an `<img>`: one atlas request feeds all 283
 * seats (two for portrait+body), so a fast scroll cannot flood the connection
 * pool. It only appears once the sheets have actually decoded (``ready``); until
 * then the initials/emoji carry the row and remain announced. A failed preload
 * therefore degrades to the same truthful fallback instead of an empty box.
 */
function seatAvatar(
  R: OfficeGameShellProps["React"],
  seat: AgentSeat,
  theme: OfficeTheme | null | undefined,
  ready: boolean,
  key: string,
) {
  const urls = themeAtlasUrls(theme);
  const initials = initialsFor(seat.name);
  const style = seat.portrait
    ? portraitCellStyle(seat.portrait, PORTRAIT_PX)
    : undefined;
  const showSprite = Boolean(ready && urls.portrait && style);
  const common = {
    "data-testid": "agents-panel-avatar",
    "data-seat-slug": seat.slug,
  };

  if (showSprite && style && urls.portrait) {
    return R.createElement("span", {
      ...common,
      key,
      className: "agents-panel__portrait",
      role: "img",
      "aria-label": `${seat.name} portrait`,
      "data-sprite": "portrait",
      style: { ...style, backgroundImage: `url("${urls.portrait}")` },
    });
  }
  if (initials) {
    return R.createElement("span", {
      ...common,
      key,
      className: "agents-panel__initials",
      role: "img",
      "aria-label": `${seat.name} initials`,
      "data-sprite": "initials",
    }, initials);
  }
  return R.createElement("span", {
    ...common,
    key,
    className: "agents-panel__emoji",
    role: "img",
    "aria-label": seat.name ? `${seat.name} portrait` : "Agent portrait",
    "data-sprite": "emoji",
  }, (seat.emoji || "").trim() || "\u2022");
}

/** One seat row: portrait, persona name, type, room, supervisor. */
function seatRow(
  R: OfficeGameShellProps["React"],
  seat: AgentSeat,
  index: number,
  theme: OfficeTheme | null | undefined,
  ready: boolean,
  onActivate: (slug: string) => void,
) {
  const meta = seatMeta(seat);
  return R.createElement("li", {
    key: `${seat.slug}:${index}`,
    className: "agents-panel__seat",
    "data-testid": "agents-panel-seat",
    "data-seat-slug": seat.slug,
    "data-seat-type": seat.seat_type,
    onMouseEnter: () => onActivate(seat.slug),
    onFocus: () => onActivate(seat.slug),
  }, [
    R.createElement("span", {
      key: "a", className: "agents-panel__avatar",
    }, [seatAvatar(R, seat, theme, ready, "p")]),
    R.createElement("span", {
      key: "n", className: "agents-panel__name",
    }, seat.name),
    R.createElement("span", {
      key: "m", className: "agents-panel__meta",
    }, meta.join(" \u00b7 ")),
  ]);
}

/**
 * The dialogue-style agent card the pack author demonstrates: a framed portrait,
 * the agent name on the tab, and the 32x64 body sprite beside it. It follows the
 * row the reader hovers or focuses (default: the first seat), so the card is a
 * preview of the roster, never a fabricated extra agent.
 *
 * Fixed 72px tall (64px body + padding) and a fixed three-column grid, so
 * moving the pointer down 283 rows cannot reflow the list under it.
 */
function agentCard(
  R: OfficeGameShellProps["React"],
  seat: AgentSeat,
  theme: OfficeTheme | null | undefined,
  ready: boolean,
) {
  const urls = themeAtlasUrls(theme);
  const frame = theme ? theme.bodies.frame : null;
  const bodyStyle = seat.body ? bodyCellStyle(seat.body, frame) : undefined;
  const showBody = Boolean(ready && urls.body && bodyStyle);
  return R.createElement("div", {
    key: "card",
    className: "agents-panel__card",
    "data-testid": "agents-panel-card",
    "data-seat-slug": seat.slug,
    "aria-live": "polite",
    tabIndex: 0,
  }, [
    R.createElement("div", {
      key: "p", className: "agents-panel__cardPortrait",
    }, [seatAvatar(R, seat, theme, ready, "p")]),
    R.createElement("div", {
      key: "t", className: "agents-panel__cardTab",
    }, [
      R.createElement("span", {
        key: "n", className: "agents-panel__cardName",
      }, seat.name),
      R.createElement("span", {
        key: "m", className: "agents-panel__cardMeta",
      }, seatMeta(seat).join(" \u00b7 ")),
    ]),
    showBody && bodyStyle && urls.body
      ? R.createElement("span", {
          key: "b",
          className: "agents-panel__body",
          role: "img",
          "aria-label": `${seat.name} body`,
          "data-sprite": "body",
          style: { ...bodyStyle, backgroundImage: `url("${urls.body}")` },
        })
      : null,
  ]);
}

/** One division: a heading with its REAL headcount, then its seats. */
function divisionGroup(
  R: OfficeGameShellProps["React"],
  division: AgentDivision,
  open: boolean,
  theme: OfficeTheme | null | undefined,
  ready: boolean,
  onActivate: (slug: string) => void,
) {
  const boardroom = division.slug === "";
  const heading = `${division.label} \u2014 ${division.headcount} `
    + (division.headcount === 1 ? "seat" : "seats");
  return R.createElement("details", {
    key: division.slug || "boardroom",
    className: "agents-panel__division",
    "data-testid": "agents-panel-division",
    // An id as well as the testid: the dock is a SCOPING ANCHOR, and CSS
    // [id=...] is the one attribute selector that reliably compiles in every
    // engine (the testid is unquoted, so `[data-testid=x]` is only safe until
    // the value needs quoting).
    id: `agents-panel-division-${division.slug || "boardroom"}`,
    "data-division": division.slug,
    "data-headcount": String(division.headcount),
    open,
  }, [
    R.createElement("summary", {
      key: "s", className: "agents-panel__divisionHead",
    }, [
      R.createElement("span", {
        key: "l", className: "agents-panel__divisionLabel",
      }, heading),
      division.floor !== null && !boardroom
        ? R.createElement("span", {
            key: "f", className: "agents-panel__divisionMeta",
          }, `Floor ${division.floor}${division.kind ? ` \u00b7 ${division.kind}` : ""}`)
        : R.createElement("span", {
            key: "f", className: "agents-panel__divisionMeta",
          }, boardroom ? "Above every division" : division.kind),
    ]),
    R.createElement("ul", {
      key: "u", className: "agents-panel__seats",
    }, division.seats.map((seat, i) =>
      seatRow(R, seat, i, theme, ready, onActivate))),
  ]);
}

export function createAgentsPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function AgentsPanel(props: AgentsPanelProps) {
    const [state, setState] = useState<AgentPanelState>({ kind: "loading" });
    // The sheets are preloaded ONCE per theme (two Image objects, not 283
    // per-seat requests). Portraits only paint after both decoded; on failure
    // these stay false and the initials/emoji remain the visible, announced
    // fallback.
    const [portraitsReady, setPortraitsReady] = useState(false);
    // Which seat the dialogue card previews. null means "the first seat", so
    // the initial render is deterministic before any pointer event.
    const [activeSlug, setActiveSlug] = useState<string | null>(null);

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      fetchAgentPanel({
        base: props.base,
        fetchImpl: props.fetchImpl,
        signal: ctl?.signal,
      }).then((next) => {
        if (live) setState(next);
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [props.base]);

    useEffect(() => {
      const pending = preloadThemeAtlases(props.theme);
      if (!pending) {
        setPortraitsReady(false);
        return;
      }
      let live = true;
      pending.then((ok) => {
        if (live) setPortraitsReady(ok);
      });
      return () => {
        live = false;
      };
      // The atlas URLs are the identity of a pack; a new theme object with the
      // same URLs must not re-download them.
    }, [props.theme?.atlas, props.theme?.bodies?.atlas]);

    if (state.kind === "loading") {
      return R.createElement("div", {
        className: "agents-panel",
        "data-testid": "agents-panel",
        "data-state": "loading",
        role: "status", "aria-live": "polite",
      }, "Loading the roster\u2026");
    }

    if (state.kind === "error") {
      return R.createElement("div", {
        className: "agents-panel agents-panel__error",
        "data-testid": "agents-panel",
        "data-state": "error",
        role: "alert",
      }, state.message);
    }

    const { data } = state;
    const counts = data.counts;
    const groups = data.divisions ?? [];
    const boardroom = groups.find((g) => g.slug === "");
    const divisions = groups.filter((g) => g.slug !== "");
    const seats = data.seats ?? [];
    const activeSeat = (activeSlug
      ? seats.find((seat) => seat.slug === activeSlug)
      : undefined) ?? seats[0] ?? null;
    const activate = (slug: string) => setActiveSlug(slug);

    return R.createElement("div", {
      className: "agents-panel" + (portraitsReady ? " office-portraits-ready" : ""),
      "data-testid": "agents-panel",
      "data-state": "ready",
      "data-seats": String(counts?.headcount ?? 0),
      "data-divisions": String(counts?.divisions ?? divisions.length),
      "data-portraits": portraitsReady ? "ready" : "fallback",
    }, [
      activeSeat ? agentCard(R, activeSeat, props.theme, portraitsReady) : null,
      R.createElement("div", {
        key: "summary",
        className: "agents-panel__summary",
        "data-testid": "agents-panel-summary",
      }, summaryLine(counts)),
      // The boardroom tier is not a division: it holds the seats the plan
      // places above every division. It is rendered first and named as such,
      // so the per-division list below is exactly the roster's divisions.
      boardroom
        ? divisionGroup(R, boardroom, false, props.theme, portraitsReady, activate)
        : null,
      ...divisions.map((d) =>
        divisionGroup(R, d, false, props.theme, portraitsReady, activate)),
      counts
        ? R.createElement("div", {
            key: "types",
            className: "agents-panel__types",
            "data-testid": "agents-panel-types",
          }, Object.entries(counts.seat_types)
            .map(([type, n]) => `${n} ${seatTypeLabel(type).toLowerCase()}`)
            .join(" \u00b7 "))
        : null,
    ]);
  };
}
