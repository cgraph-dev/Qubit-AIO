/* The semantic outside-world panel for Qubit Office (P6-03).
 *
 * WHAT THIS IS
 * ------------
 * A keyboard-operable, screen-reader-first projection of the campus exterior,
 * the lobby/security strip and the elevator/stairs links to the tower. It shows
 * the weather / day-night / season indicator, the active/dormant zone list, the
 * selected zone's chunks and the floor links.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is not mounted by the shell (the lead mounts it once the shell lane
 * lands), it draws no canvas, and it never invents a zone, an agent or a
 * business fact. Every value comes from `outside-world.ts`, which refuses a
 * payload that claims business truth.
 *
 * React and hooks arrive as arguments, exactly as the rest of the plugin does
 * it, so the host keeps one React runtime.
 */
import { sakugaButton } from "./sakuga";
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  type OutsideChunk, type OutsideEnvironmentEvent, type OutsideFloorLink,
  type OutsideWorld, type OutsideWorldView, type OutsideZone,
  currentEnvironment, floorHref, orderedZones, outsideWorldView, stateLabel,
  zoneChunks,
} from "./outside-world";
import { createOutsideScene } from "./OutsideScene";

export interface OutsideWorldProps {
  /** A pre-built view (tests/host). When absent, it is derived below. */
  view?: OutsideWorldView;
  /** Raw projection payload from `GET /agent/api/office/world/outside`. */
  payload?: unknown;
  loading?: boolean;
  error?: string | null;
  code?: string;
  /** Start on this zone instead of the first active one. */
  initialZoneId?: string | null;
  /** Force reduced motion; otherwise the OS preference is honoured. */
  reducedMotion?: boolean;
  onSelectZone?: (zone: OutsideZone) => void;
  onSelectFloor?: (link: OutsideFloorLink) => void;
  onRetry?: () => void;
}

type ReactArg = OfficeGameShellProps["React"];
type HooksArg = OfficeGameShellProps["hooks"];

const TRUTHY_CREDIT_URL = "https://limezu.itch.io";

function environmentText(
  events: readonly OutsideEnvironmentEvent[], reducedMotion: boolean,
): string {
  if (!events.length) return "Environment status unavailable.";
  return events
    .map((event) => {
      const cue = reducedMotion ? event.still_cue : event.cue;
      return `${event.label}${cue ? ` — ${cue}` : ""}`;
    })
    .join(" · ");
}

export function createOutsideWorld(R: ReactArg, hooks: HooksArg) {
  const { useState, useEffect, useRef } = hooks;
  const OutsideScene = createOutsideScene(R, hooks);

  return function OutsideWorld(props: OutsideWorldProps) {
    const view: OutsideWorldView = props.view ?? outsideWorldView({
      payload: props.payload,
      loading: props.loading,
      error: props.error,
      code: props.code,
    });
    const world: OutsideWorld | null = view.world;
    const zones = world ? orderedZones(world) : [];

    const [selectedId, setSelectedId] = useState<string | null>(
      props.initialZoneId ?? zones[0]?.zone_id ?? null);
    const [reducedMotion, setReducedMotion] = useState<boolean>(
      Boolean(props.reducedMotion));
    const refs = useRef<Record<string, HTMLElement | null>>({});
    const hadZones = useRef(zones.length > 0);

    useEffect(() => {
      const arrived = !hadZones.current && zones.length > 0;
      hadZones.current = zones.length > 0;
      if (!zones.length) return;
      if (!arrived && selectedId === null) return;
      if (selectedId && zones.some((zone) => zone.zone_id === selectedId)) {
        return;
      }
      setSelectedId(props.initialZoneId ?? zones[0].zone_id);
    }, [zones, selectedId, props.initialZoneId]);

    useEffect(() => {
      if (typeof props.reducedMotion === "boolean") {
        setReducedMotion(props.reducedMotion);
        return;
      }
      if (typeof window === "undefined"
          || typeof window.matchMedia !== "function") {
        return;
      }
      const query = window.matchMedia("(prefers-reduced-motion: reduce)");
      const apply = () => setReducedMotion(Boolean(query.matches));
      apply();
      if (typeof query.addEventListener === "function") {
        query.addEventListener("change", apply);
        return () => query.removeEventListener("change", apply);
      }
      return;
    }, [props.reducedMotion]);

    const select = (zone: OutsideZone) => {
      setSelectedId(zone.zone_id);
      props.onSelectZone?.(zone);
    };

    const focusZone = (zoneId: string) => {
      const node = refs.current[zoneId];
      if (node && typeof node.focus === "function") node.focus();
    };

    const onZoneKeyDown = (
      event: { key: string; preventDefault?: () => void },
      zone: OutsideZone,
    ) => {
      const index = zones.findIndex((item) => item.zone_id === zone.zone_id);
      if (index < 0) return;
      let nextIndex = index;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") {
        nextIndex = Math.min(zones.length - 1, index + 1);
      } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
        nextIndex = Math.max(0, index - 1);
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = zones.length - 1;
      } else if (event.key === "Enter" || event.key === " ") {
        nextIndex = index;
      } else {
        return;
      }
      event.preventDefault?.();
      const next = zones[nextIndex];
      select(next);
      if (event.key !== "Enter" && event.key !== " ") {
        focusZone(next.zone_id);
      }
    };

    const credit = () => R.createElement("p", {
      key: "credit",
      className: "outside-world__credit",
      "data-testid": "outside-world-credit",
    }, [
      R.createElement("span", { key: "text" }, "Office art: LimeZu — "),
      R.createElement("a", {
        key: "link",
        href: TRUTHY_CREDIT_URL,
        target: "_blank",
        rel: "noopener noreferrer",
      }, TRUTHY_CREDIT_URL),
    ]);

    const statusLine = () => R.createElement("p", {
      key: "status",
      className: `outside-world__status is-${view.status}`,
      "data-testid": "outside-world-status",
      "data-state": view.status,
      role: view.status === "error" ? "alert" : "status",
      "aria-live": view.status === "error" ? "assertive" : "polite",
    }, view.message);

    const retryButton = () => {
      if (!props.onRetry) return null;
      return sakugaButton(R, {
        key: "retry",
        type: "button",
        className: "outside-world__retry",
        "data-testid": "outside-world-retry",
        onClick: () => props.onRetry?.(),
      }, "Retry");
    };

    const nonReady = () => {
      const label = view.status === "loading" ? "Loading the outside world"
        : view.status === "empty" ? "No outside-world zones"
        : view.status === "unavailable" ? "Outside world unavailable"
        : "Outside world error";
      return R.createElement("div", {
        key: "state",
        className: `outside-world__state is-${view.status}`,
        "data-testid": "outside-world-state",
        "data-state": view.status,
      }, [
        R.createElement("p", {
          key: "label", className: "outside-world__stateLabel",
        }, label),
        statusLine(),
        view.code
          ? R.createElement("p", {
              key: "code", className: "outside-world__code",
              "data-testid": "outside-world-code",
            }, `Code: ${view.code}`)
          : null,
        view.status === "error" || view.status === "unavailable"
          ? retryButton() : null,
        credit(),
      ]);
    };

    const environment = () => {
      if (!world) return null;
      const events = currentEnvironment(world);
      return R.createElement("section", {
        key: "environment",
        className: "outside-world__environment",
        "data-testid": "outside-world-environment",
        "aria-label": "Outside environment",
      }, [
        R.createElement("h3", {
          key: "title", className: "outside-world__subhead",
        }, "Environment"),
        R.createElement("p", {
          key: "text",
          className: "outside-world__envText",
          "data-testid": "outside-world-environment-text",
        }, environmentText(events, reducedMotion)),
        R.createElement("ul", {
          key: "events",
          className: "outside-world__envEvents",
          "aria-label": "Environment cues",
        }, events.map((event: OutsideEnvironmentEvent) =>
          R.createElement("li", {
            key: event.event_id,
            className: "outside-world__envEvent",
            "data-testid": "outside-world-env-event",
            "data-kind": event.kind,
            "data-value": event.value,
            "data-animated": event.animated && !reducedMotion
              ? "true" : "false",
          }, `${event.kind}: ${event.label} (${reducedMotion
            ? event.still_cue : event.cue})`))),
      ]);
    };

    const zoneButton = (zone: OutsideZone) => {
      const isSelected = selectedId === zone.zone_id;
      return sakugaButton(R, {
        key: zone.zone_id,
        type: "button",
        ref: (node: HTMLElement | null) => {
          refs.current[zone.zone_id] = node;
        },
        className: "outside-world__zone"
          + ` is-${zone.state}`
          + (isSelected ? " is-selected" : ""),
        role: "option",
        "aria-selected": isSelected ? "true" : "false",
        "aria-pressed": isSelected ? "true" : "false",
        tabIndex: isSelected || (selectedId === null
          && zone.zone_id === zones[0]?.zone_id) ? 0 : -1,
        "data-testid": "outside-zone",
        "data-zone": zone.zone_id,
        "data-state": zone.state,
        "data-animated": zone.animated && !reducedMotion ? "true" : "false",
        "aria-label": `${zone.label}, ${stateLabel(zone.state)}, `
          + `${zone.counts.chunks} chunks, `
          + `${zone.animated ? "animated" : "still"}`,
        onClick: () => select(zone),
        onKeyDown: (event: { key: string; preventDefault?: () => void }) =>
          onZoneKeyDown(event, zone),
      }, [
        R.createElement("span", {
          key: "label", className: "outside-world__zoneLabel",
        }, zone.label),
        R.createElement("span", {
          key: "state", className: "outside-world__zoneState",
          "data-testid": "outside-zone-state",
        }, stateLabel(zone.state)),
        R.createElement("span", {
          key: "motion", className: "outside-world__zoneMotion",
        }, zone.animated ? "animated" : "still"),
        R.createElement("span", {
          key: "counts", className: "outside-world__zoneCounts",
        }, `${zone.counts.chunks} chunks · ${zone.counts.tiles} tiles`),
      ]);
    };

    const zoneList = () => R.createElement("section", {
      key: "zones",
      className: "outside-world__zoneSection",
      "aria-label": "Outside zones",
    }, [
      R.createElement("h3", {
        key: "title", className: "outside-world__subhead",
      }, `Zones (${world?.summary.active_zones ?? 0} active / `
        + `${world?.summary.zones ?? 0})`),
      R.createElement("div", {
        key: "list",
        className: "outside-world__zones",
        role: "listbox",
        "aria-label": "Outside zones",
        "data-testid": "outside-world-zones",
      }, zones.map(zoneButton)),
    ]);

    const chunkRow = (chunk: OutsideChunk) => R.createElement("li", {
      key: chunk.chunk_id,
      className: `outside-world__chunk is-${chunk.state}`,
      "data-testid": "outside-chunk",
      "data-chunk": chunk.chunk_id,
      "data-state": chunk.state,
      "data-animated": chunk.animated && !reducedMotion ? "true" : "false",
    }, [
      R.createElement("span", {
        key: "kind", className: "outside-world__chunkKind",
      }, chunk.kind),
      R.createElement("span", {
        key: "state", className: "outside-world__chunkState",
      }, stateLabel(chunk.state)),
      R.createElement("span", {
        key: "motion", className: "outside-world__chunkMotion",
      }, chunk.animated ? "animated" : "still"),
    ]);

    const zoneDetail = () => {
      if (!world) return null;
      const zone = zones.find((item) => item.zone_id === selectedId)
        ?? zones[0] ?? null;
      if (!zone) return null;
      const chunks = zoneChunks(world, zone.zone_id);
      return R.createElement("section", {
        key: "detail",
        className: "outside-world__detail",
        "data-testid": "outside-world-zone-detail",
        "data-zone": zone.zone_id,
        "data-state": zone.state,
        "aria-label": `Selected zone ${zone.label}`,
      }, [
        R.createElement("h3", {
          key: "title", className: "outside-world__subhead",
        }, zone.label),
        R.createElement("p", {
          key: "summary", className: "outside-world__zoneSummary",
          "data-testid": "outside-zone-summary",
        }, `${stateLabel(zone.state)} · ${zone.counts.chunks} chunks · `
          + `${zone.counts.active_chunks} active · `
          + `${zone.counts.animated_chunks} animated · `
          + `${zone.counts.tiles} tiles`),
        chunks.length
          ? R.createElement("ul", {
              key: "chunks",
              className: "outside-world__chunks",
              "aria-label": `${zone.label} chunks`,
            }, chunks.map(chunkRow))
          : R.createElement("p", {
              key: "empty", className: "outside-world__empty",
              "data-testid": "outside-world-zone-empty",
            }, "This zone has no chunks."),
      ]);
    };

    const floorLinks = () => {
      if (!world) return null;
      const links = world.floor_links;
      return R.createElement("section", {
        key: "floors",
        className: "outside-world__floors",
        "data-testid": "outside-world-floor-links",
        "aria-label": "Lobby links to tower floors",
      }, [
        R.createElement("h3", {
          key: "title", className: "outside-world__subhead",
        }, "Elevators and stairs"),
        links.length
          ? R.createElement("ul", {
              key: "list",
              className: "outside-world__floorList",
            }, links.map((link: OutsideFloorLink) => R.createElement("li", {
              key: `${link.kind}-${link.floor_id}`,
              className: "outside-world__floorItem",
            }, R.createElement("a", {
              href: floorHref(link.floor_id),
              className: "outside-world__floorLink",
              "data-testid": "outside-floor-link",
              "data-kind": link.kind,
              "data-floor": link.floor_id,
              "aria-label": link.label,
              onClick: (event: { preventDefault?: () => void }) => {
                if (props.onSelectFloor) {
                  event.preventDefault?.();
                  props.onSelectFloor(link);
                }
              },
            }, link.label))))
          : R.createElement("p", {
              key: "empty", className: "outside-world__empty",
              "data-testid": "outside-world-floors-empty",
            }, "No elevator or stair links are configured."),
      ]);
    };

    const scene = () => R.createElement(OutsideScene as never, {
      key: "scene",
      view,
      selectedZoneId: selectedId,
      reducedMotion,
      onSelectZone: select,
      onSelectFloor: props.onSelectFloor,
    });

    const ready = () => R.createElement("div", {
      key: "ready", className: "outside-world__body",
    }, [scene(), environment(), zoneList(), zoneDetail(), floorLinks()]);

    return R.createElement("section", {
      className: "outside-world",
      role: "region",
      "aria-label": "Campus outside world",
      "aria-busy": view.status === "loading" ? "true" : "false",
      "data-testid": "outside-world",
      "data-status": view.status,
      "data-reduced-motion": reducedMotion ? "true" : "false",
    }, [
      R.createElement("header", {
        key: "header", className: "outside-world__header",
      }, [
        R.createElement("h2", {
          key: "title", className: "outside-world__title",
        }, "Outside world"),
        R.createElement("p", {
          key: "sub", className: "outside-world__lede",
        }, "Campus exterior, lobby and elevators. Presentation only — never "
          + "business truth."),
      ]),
      view.status === "ready" ? ready() : nonReady(),
      view.status === "ready" ? credit() : null,
    ]);
  };
}
