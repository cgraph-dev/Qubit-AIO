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
  officeSliceName,
  officeUiSliceAttrs,
  portraitCellStyle,
  preloadThemeAtlases,
  themeAtlasUrls,
} from "./office-theme";
import { createPixelText } from "./PixelText";
import { createThemeIcon } from "./ThemeIcon";

export interface AgentsPanelProps {
  /** An explicit base path (a test); otherwise the host's injected global. */
  base?: string;
  /**
   * Open the Business OS control drawer for a seat. Optional: without it the
   * panel keeps its original select-and-preview behaviour exactly.
   */
  onOpenAgent?: (seat: AgentSeat) => void;
  /**
   * Open the canonical TALK panel for a seat. Optional: without it the panel
   * keeps its original select-and-preview behaviour exactly.
   */
  onTalk?: (seat: AgentSeat) => void;
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

type PanelThemeComponents = {
  PixelText: ReturnType<typeof createPixelText>;
  ThemeIcon: ReturnType<typeof createThemeIcon>;
};
const THEME_COMPONENTS = new WeakMap<object, PanelThemeComponents>();

/** Stable component identities per host React, so a re-render reconciles. */
function themeComponents(
  R: OfficeGameShellProps["React"],
): PanelThemeComponents {
  const key = R as unknown as object;
  let entry = THEME_COMPONENTS.get(key);
  if (!entry) {
    entry = { PixelText: createPixelText(R), ThemeIcon: createThemeIcon(R) };
    THEME_COMPONENTS.set(key, entry);
  }
  return entry;
}

/** The bit of a React keyboard event the card's navigation needs. */
interface SeatKeyEvent {
  key: string;
  preventDefault: () => void;
}

/**
 * Row wiring. The CARD is the single keyboard owner: rows are not tab stops,
 * they only select on hover/click. `rowId` gives each row a stable DOM anchor
 * (and a stable test hook) independent of its division's open state.
 */
interface SeatInteraction {
  activeSlug: string | null;
  onActivate: (seat: AgentSeat) => void;
  onOpenAgent?: (seat: AgentSeat) => void;
  onTalk?: (seat: AgentSeat) => void;
  rowId: (slug: string) => string;
}

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
  interaction: SeatInteraction,
) {
  const meta = seatMeta(seat);
  const active = interaction.activeSlug === seat.slug;
  return R.createElement("li", {
    key: `${seat.slug}:${index}`,
    id: interaction.rowId(seat.slug),
    className: "agents-panel__seat",
    "data-testid": "agents-panel-seat",
    "data-seat-slug": seat.slug,
    "data-seat-type": seat.seat_type,
    // Rows are NOT tab stops: the card owns the keyboard so a row inside a
    // collapsed division can never trap the roster. Hover/click still selects.
    // Native list semantics stay intact; ``aria-current`` marks the row feeding
    // the card without inventing a listbox selection model for many lists.
    "aria-current": active ? "true" : undefined,
    onMouseEnter: () => interaction.onActivate(seat),
    onClick: () => {
      interaction.onActivate(seat);
      interaction.onOpenAgent?.(seat);
    },
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
    interaction.onTalk
      ? R.createElement("button", {
          key: "talk", type: "button",
          className: "agents-panel__talk office-game__btn",
          "data-testid": "agents-panel-talk-seat",
          "data-seat-slug": seat.slug,
          "aria-label": `Talk with ${seat.name}`,
          // The CARD is the single keyboard owner of the roster (283 rows must
          // not become 283 tab stops). The row button is a one-click mouse
          // shortcut; keyboard/AT users reach the same action from the card.
          tabIndex: -1,
          onClick: (event: { stopPropagation?: () => void }) => {
            event.stopPropagation?.();
            interaction.onTalk?.(seat);
          },
        }, [
          R.createElement(themeComponents(R).ThemeIcon as never, {
            key: "i", theme, name: "talk", className: "office-theme-btn-icon",
          }),
          R.createElement("span", { key: "t" }, "Talk"),
        ])
      : null,
  ]);
}

/**
 * The dialogue-style agent card the pack author demonstrates: a framed portrait,
 * the agent name on the tab, and the 32x64 body sprite beside it. It is the
 * roster's single keyboard owner: hover/click select a row, and Arrow/Home/End
 * on the card walk every seat (default: the first), so the card is a preview of
 * the roster, never a fabricated extra agent.
 *
 * Fixed 72px tall (64px body + padding) and a fixed three-column grid, so
 * moving the pointer down 283 rows cannot reflow the list under it.
 */
function agentCard(
  R: OfficeGameShellProps["React"],
  seat: AgentSeat,
  theme: OfficeTheme | null | undefined,
  ready: boolean,
  onKeyDown: (event: SeatKeyEvent) => void,
  onTalk?: (seat: AgentSeat) => void,
) {
  const urls = themeAtlasUrls(theme);
  // Optional chaining here is deliberate: the payload type requires `bodies`,
  // but a malformed/older theme object must degrade to "no body" rather than
  // throw inside the panel's render.
  const frame = theme?.bodies?.frame ?? null;
  const bodyStyle = seat.body ? bodyCellStyle(seat.body, frame) : undefined;
  const showBody = Boolean(ready && urls.body && bodyStyle);
  return R.createElement("div", {
    key: "card",
    className: "agents-panel__card office-theme-card",
    id: "agents-panel-card",
    "data-testid": "agents-panel-card",
    "data-seat-slug": seat.slug,
    // Opt in to the pack's card 9-slice only when the theme declares it.
    "data-office-slice": officeSliceName(theme, "card"),
    ...officeUiSliceAttrs(theme, "card"),
    role: "group",
    "aria-label": "Active agent",
    // The live region is the announcement path. `aria-activedescendant` was
    // deliberately NOT used: the active row lives outside the card, so pointing
    // at it would require `aria-owns` and would reparent the row out of its
    // division list in the accessibility tree. The card's own name/meta/portrait
    // update inside this live region instead.
    "aria-live": "polite",
    // Atomic: announce the whole active agent, not just the diffed text node.
    "aria-atomic": "true",
    tabIndex: 0,
    onKeyDown,
  }, [
    R.createElement("div", {
      key: "p", className: "agents-panel__cardPortrait",
    }, [seatAvatar(R, seat, theme, ready, "p")]),
    R.createElement("div", {
      key: "t", className: "agents-panel__cardTab",
      "data-office-slice": officeSliceName(theme, "tab"),
      ...officeUiSliceAttrs(theme, "tab"),
    }, [
      R.createElement("span", {
        key: "n", className: "agents-panel__cardName",
      }, R.createElement(themeComponents(R).PixelText as never, {
        key: "p", theme, text: seat.name, size: 12,
      })),
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
    onTalk
      ? R.createElement("button", {
          key: "talk", type: "button",
          className: "agents-panel__talk office-game__btn",
          "data-testid": "agents-panel-talk",
          "data-seat-slug": seat.slug,
          "aria-label": `Talk with ${seat.name}`,
          onClick: () => onTalk(seat),
          ...officeUiSliceAttrs(theme, "button", true),
        }, [
          R.createElement(themeComponents(R).ThemeIcon as never, {
            key: "i", theme, name: "talk", className: "office-theme-btn-icon",
          }),
          R.createElement("span", { key: "t" }, "Talk"),
        ])
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
  interaction: SeatInteraction,
  onToggle: (open: boolean) => void,
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
    onToggle: (event: { currentTarget: { open: boolean } }) =>
      onToggle(event.currentTarget.open),
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
      seatRow(R, seat, i, theme, ready, interaction))),
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
    //
    // `readyAtlasKey` records WHICH pack decoded. Repainting readiness on a
    // theme/atlas change is derived (`readyAtlasKey === atlasKey`), so a new
    // atlas can never be painted with the previous pack's ready state.
    const atlasKey = `${props.theme?.atlas ?? ""}\u0000`
      + `${props.theme?.bodies?.atlas ?? ""}`;
    const hasAtlas = Boolean(props.theme?.atlas || props.theme?.bodies?.atlas);
    const [readyAtlasKey, setReadyAtlasKey] = useState<string | null>(null);
    const portraitsReady = hasAtlas && readyAtlasKey === atlasKey;
    // Which seat the dialogue card previews. null means "the first seat", so
    // the initial render is deterministic before any pointer event.
    const [activeSlug, setActiveSlug] = useState<string | null>(null);
    // Which divisions the reader opened. Managed here so card navigation can
    // open the target's division before revealing/scroll-ing its row.
    const [openDivisions, setOpenDivisions] = useState<Record<string, boolean>>({});
    // A row to reveal after the render that opens its division.
    const [pendingScroll, setPendingScroll] = useState<string | null>(null);
    // The theme the host resolved. Cells must come from the SAME pack, so the
    // id is part of the panel request (and of its effect deps: the theme may
    // finish loading after the panel opened).
    const themeId = props.theme?.active && props.theme.id
      ? props.theme.id : undefined;

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      fetchAgentPanel({
        base: props.base,
        fetchImpl: props.fetchImpl,
        theme: themeId,
        signal: ctl?.signal,
      }).then((next) => {
        if (live) setState(next);
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [props.base, themeId]);

    useEffect(() => {
      const pending = preloadThemeAtlases(props.theme);
      if (!pending) return;
      let live = true;
      pending.then((ok) => {
        if (live && ok) setReadyAtlasKey(atlasKey);
      });
      return () => {
        live = false;
      };
      // The atlas URLs are the identity of a pack; a new theme object with the
      // same URLs must not re-download them.
    }, [atlasKey]);

    // Reveal the card's new active row AFTER the render that opened its
    // division. Focus deliberately STAYS on the card: the card owns the
    // keyboard, so opening a division can never move focus into a possibly
    // collapsing tree.
    useEffect(() => {
      if (!pendingScroll) return;
      if (typeof document !== "undefined") {
        const node = document.getElementById(`agents-panel-seat-${pendingScroll}`);
        node?.scrollIntoView?.({ block: "nearest" });
      }
      setPendingScroll(null);
    }, [pendingScroll]);

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
    // The visual order: the boardroom is rendered first, then the divisions in
    // the payload's order. Arrow navigation follows what the reader sees rather
    // than the plan's flat seat list.
    const orderedSeats = [
      ...(boardroom ? boardroom.seats : []),
      ...divisions.flatMap((division) => division.seats),
    ];
    const activeSeat = (activeSlug
      ? orderedSeats.find((seat) => seat.slug === activeSlug)
      : undefined) ?? orderedSeats[0] ?? null;
    const divisionBySeat = new Map<string, string>();
    for (const group of groups) {
      for (const seat of group.seats) divisionBySeat.set(seat.slug, group.slug);
    }
    const rowId = (slug: string) => `agents-panel-seat-${slug}`;

    const toggleDivision = (slug: string, open: boolean) => {
      setOpenDivisions((previous) =>
        previous[slug] === open ? previous : { ...previous, [slug]: open });
    };
    const navigate = (slug: string, key: string): boolean => {
      const current = orderedSeats.findIndex((seat) => seat.slug === slug);
      if (current < 0) return false;
      let next = current;
      // Clamp at the ends (no wrap): Home/End are the explicit jumps.
      if (key === "ArrowDown") next = Math.min(orderedSeats.length - 1, current + 1);
      else if (key === "ArrowUp") next = Math.max(0, current - 1);
      else if (key === "Home") next = 0;
      else if (key === "End") next = orderedSeats.length - 1;
      else return false;
      const target = orderedSeats[next];
      setActiveSlug(target.slug);
      const division = divisionBySeat.get(target.slug);
      if (division !== undefined) {
        setOpenDivisions((previous) =>
          previous[division] ? previous : { ...previous, [division]: true });
      }
      setPendingScroll(target.slug);
      return true;
    };
    const onCardKeyDown = (event: SeatKeyEvent) => {
      if (!activeSeat) return;
      if (navigate(activeSeat.slug, event.key)) event.preventDefault();
    };
    const interaction: SeatInteraction = {
      activeSlug: activeSeat ? activeSeat.slug : null,
      onActivate: (seat: AgentSeat) => setActiveSlug(seat.slug),
      onOpenAgent: props.onOpenAgent,
      onTalk: props.onTalk,
      rowId,
    };

    return R.createElement("div", {
      className: "agents-panel" + (portraitsReady ? " office-portraits-ready" : ""),
      "data-testid": "agents-panel",
      "data-state": "ready",
      "data-seats": String(counts?.headcount ?? 0),
      "data-divisions": String(counts?.divisions ?? divisions.length),
      "data-portraits": portraitsReady ? "ready" : "fallback",
    }, [
      activeSeat
        ? agentCard(R, activeSeat, props.theme, portraitsReady,
            onCardKeyDown, props.onTalk)
        : null,
      R.createElement("div", {
        key: "summary",
        className: "agents-panel__summary",
        "data-testid": "agents-panel-summary",
      }, summaryLine(counts)),
      // The boardroom tier is not a division: it holds the seats the plan
      // places above every division. It is rendered first and named as such,
      // so the per-division list below is exactly the roster's divisions.
      boardroom
        ? divisionGroup(R, boardroom, Boolean(openDivisions[boardroom.slug]),
            props.theme, portraitsReady, interaction,
            (open) => toggleDivision(boardroom.slug, open))
        : null,
      ...divisions.map((d) =>
        divisionGroup(R, d, Boolean(openDivisions[d.slug]),
          props.theme, portraitsReady, interaction,
          (open) => toggleDivision(d.slug, open))),
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
