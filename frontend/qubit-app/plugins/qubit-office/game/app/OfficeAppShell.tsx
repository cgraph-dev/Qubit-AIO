/* The Qubit Office page: the vendored office client, mounted in the plugin.
 *
 * React comes from the HOST. This module has no `import from "react"` and uses
 * `R.createElement` rather than JSX — the dashboard injects exactly one React,
 * and a second copy would silently split the hook runtime.
 */

import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OFFICE_APP_PATH, officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import { createSeatModelPanel } from "./SeatModelPanel";
import {
  POLL_INTERVAL_MS, SNAPSHOT_ROUTE, STREAM_ROUTE,
  bindingSummary, bindingVerdict,
} from "./office-api";

export interface OfficeAppProps {
  /** The dashboard's base path (empty at the root). */
  base?: string;
  /** Serve the client's own demo data. Off by default — see office-app.ts. */
  mock?: boolean;
  /**
   * Which seat the model panel configures. Omitted = no panel, so the pure
   * game view (and every existing caller) is unchanged.
   */
  seatAgentId?: string | number;
  /** Heading text for the panel. */
  seatLabel?: string;
  /**
   * Selectable seats, from the tower payload's own characters. When given, the
   * shell offers a picker instead of relying on a fixed `seatAgentId` — the
   * office is the same Qubit backend, so the seats it draws are the seats it
   * can configure.
   */
  seats?: Array<{ agent_id: number; slug: string }>;
  /** Called when the operator picks a different seat. */
  onSeatChange?: (agentId: number) => void;
  /** Set once the iframe has loaded, for tests and for a load-failure banner. */
  onReady?: () => void;
}

/**
 * Build the component. React and hooks arrive as arguments, exactly as the rest
 * of the plugin does it.
 */
export function createOfficeApp(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;

  return function OfficeApp(props: OfficeAppProps = {}) {
    const [loaded, setLoaded] = useState(false);
    const [failed, setFailed] = useState<string | null>(null);
    const [panelOpen, setPanelOpen] = useState(false);
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    // Built unconditionally (hooks must not be conditional) but only rendered
    // when a seat is chosen, so the default view is still just the game.
    const SeatModelPanel = createSeatModelPanel(R, hooks);

    useEffect(() => {
      const frame = frameRef.current;
      if (!frame) return;
      // Install before navigation. Registering onLoad first is not enough:
      // the upstream ES module can issue `/api/access` while parsing, before
      // the iframe's load event fires. Setting `src` only after replacing
      // fetch closes that startup race without touching the host's fetch.
      const win = frame.contentWindow as Window | null;
      if (!win) {
        setFailed("The office API could not be connected from this page.");
        return;
      }
      let restoreBridge: (() => void) | undefined;
      try {
        restoreBridge = installOfficeApiBridge(win);
      } catch {
        setFailed("The office API could not be connected from this page.");
        return;
      }
      const onLoad = () => {
        setLoaded(true);
        setFailed(null);
        props.onReady?.();
      };
      const onError = () => setFailed("The office app failed to load.");
      frame.addEventListener("load", onLoad);
      frame.addEventListener("error", onError);
      setFailed(null);
      setLoaded(false);
      frame.src = officeAppSrc({ base: props.base, mock: props.mock });
      return () => {
        frame.removeEventListener("load", onLoad);
        frame.removeEventListener("error", onError);
        restoreBridge?.();
      };
    }, [props.base, props.mock]);

    const children: unknown[] = [
      R.createElement("iframe", {
        key: "frame",
        ref: frameRef,
        title: "Habblaud virtual office",
        "data-testid": "office-app-frame",
        className: "office-game__frame",
        "aria-label": "Habblaud office game",
        "aria-describedby": "office-app-description",
        "aria-busy": !loaded && !failed,
        "data-state": failed ? "error" : loaded ? "ready" : "loading",
        // Keep the upstream app in a distinct origin and storage namespace.
        // allow-scripts + allow-same-origin is still required for its module
        // worker, canvas and storage APIs; the dashboard host stays separate.
        // The server injects the session token into the app document itself.
        sandbox: "allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals",
        allow: "clipboard-read; clipboard-write",
        style: { border: "0", width: "100%", height: "100%", display: "block" },
      }),
      R.createElement("p", {
        key: "description",
        id: "office-app-description",
        className: "office-game__sr-only",
      }, "Interactive Habblaud virtual office. Use the controls within the game; unsupported write actions are disabled."),
    ];

    if (!loaded && !failed) {
      children.push(R.createElement("div", {
        key: "loading",
        className: "office-game__panel office-game__loading",
        "data-testid": "office-app-loading",
        role: "status",
        "aria-live": "polite",
      }, "Loading the office\u2026"));
    }

    if (failed) {
      children.push(R.createElement("div", {
        key: "failed",
        className: "office-game__refusal office-game__error",
        "data-testid": "office-app-failed",
        role: "alert",
      }, [
        R.createElement("div", { key: "m" }, `${failed} Check Qubit's office connection; do not start a separate upstream server.`),
        // The one thing a reader needs when it fails: WHERE it was looking.
        R.createElement("div", {
          key: "p", style: { fontSize: "var(--office-fs-xs)" },
        }, OFFICE_APP_PATH),
      ]));
    }

    // Keep binding diagnostics available to assistive technology, but do not
    // place an invisible overlay over the game or intercept its keyboard/mouse.
    const verdict = bindingVerdict();
    children.push(R.createElement("div", {
      key: "status",
      className: "office-game__sr-only",
      "data-testid": "office-app-status",
      role: "status",
      "aria-label": `${bindingSummary()}; ${verdict.live ? `live stream ${STREAM_ROUTE}` : `poll ${POLL_INTERVAL_MS / 1000}s → ${SNAPSHOT_ROUTE}`}`,
    }, bindingSummary()));

    // Per-seat model routing. Rendered only when the host names a seat, so the
    // default office view is the game and nothing else. It sits OUTSIDE the
    // iframe on purpose: the api-bridge answers every non-GET with 501, so a
    // write control inside the vendored client could not save anything.
    if (props.seatAgentId !== undefined && props.seatAgentId !== null) {
      const seats = props.seats ?? [];
      children.push(R.createElement("button", {
        key: "seat-model-toggle",
        type: "button",
        className: "seat-model__toggle",
        "data-testid": "seat-model-toggle",
        "aria-expanded": panelOpen,
        onClick: () => setPanelOpen(!panelOpen),
      }, panelOpen ? "Hide seat model" : "Seat model"));
      if (panelOpen) {
        // The picker comes from the tower payload's own characters: the seats
        // drawn in the office are the seats that can be configured.
        if (seats.length > 0) {
          children.push(R.createElement("label", {
            key: "seat-picker", className: "seat-model__label",
          }, "Seat",
            R.createElement("select", {
              key: "seat",
              "data-testid": "seat-model-seat",
              value: String(props.seatAgentId),
              onChange: (e: { target: { value: string } }) => {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) props.onSeatChange?.(next);
              },
            }, seats.map((s) =>
              R.createElement("option", {
                key: s.agent_id, value: String(s.agent_id),
              }, s.slug || `seat ${s.agent_id}`))),
          ));
        }
        children.push(R.createElement(SeatModelPanel, {
          key: "seat-model-panel",
          agentId: props.seatAgentId,
          seatLabel: props.seatLabel,
        }));
      }
    }

    return R.createElement("div", {
      "data-testid": "office-app",
      className: "office-game",
    }, children);
  };
}
