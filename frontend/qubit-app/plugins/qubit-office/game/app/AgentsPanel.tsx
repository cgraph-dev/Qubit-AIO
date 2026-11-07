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

export interface AgentsPanelProps {
  /** An explicit base path (a test); otherwise the host's injected global. */
  base?: string;
  /** Injected for tests; defaults to the page's own fetch. */
  fetchImpl?: typeof fetch;
}

/** One seat row: emoji, persona name, type, room, supervisor. */
function seatRow(
  R: OfficeGameShellProps["React"],
  seat: AgentSeat,
  index: number,
) {
  const meta: string[] = [seatTypeLabel(seat.seat_type)];
  if (seat.room) meta.push(seat.room === "boardroom" ? "Boardroom" : seat.room);
  if (seat.supervisor) meta.push(`reports to ${seat.supervisor}`);
  return R.createElement("li", {
    key: `${seat.slug}:${index}`,
    className: "agents-panel__seat",
    "data-testid": "agents-panel-seat",
    "data-seat-slug": seat.slug,
    "data-seat-type": seat.seat_type,
  }, [
    R.createElement("span", {
      key: "e", className: "agents-panel__emoji", "aria-hidden": "true",
    }, seat.emoji || "\u2022"),
    R.createElement("span", {
      key: "n", className: "agents-panel__name",
    }, seat.name),
    R.createElement("span", {
      key: "m", className: "agents-panel__meta",
    }, meta.join(" \u00b7 ")),
  ]);
}

/** One division: a heading with its REAL headcount, then its seats. */
function divisionGroup(
  R: OfficeGameShellProps["React"],
  division: AgentDivision,
  open: boolean,
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
    }, division.seats.map((seat, i) => seatRow(R, seat, i))),
  ]);
}

export function createAgentsPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function AgentsPanel(props: AgentsPanelProps) {
    const [state, setState] = useState<AgentPanelState>({ kind: "loading" });

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

    return R.createElement("div", {
      className: "agents-panel",
      "data-testid": "agents-panel",
      "data-state": "ready",
      "data-seats": String(counts?.headcount ?? 0),
      "data-divisions": String(counts?.divisions ?? divisions.length),
    }, [
      R.createElement("div", {
        key: "summary",
        className: "agents-panel__summary",
        "data-testid": "agents-panel-summary",
      }, summaryLine(counts)),
      // The boardroom tier is not a division: it holds the seats the plan
      // places above every division. It is rendered first and named as such,
      // so the per-division list below is exactly the roster's divisions.
      boardroom ? divisionGroup(R, boardroom, false) : null,
      ...divisions.map((d) => divisionGroup(R, d, false)),
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
