/* The Qubit Office page: the vendored office client, mounted in the plugin.
 *
 * React comes from the HOST. This module has no `import from "react"` and uses
 * `R.createElement` rather than JSX — the dashboard injects exactly one React,
 * and a second copy would silently split the hook runtime.
 */

import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OFFICE_APP_PATH, officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import {
  POLL_INTERVAL_MS, SNAPSHOT_ROUTE, STREAM_ROUTE,
  bindingSummary, bindingVerdict,
} from "./office-api";

export interface OfficeAppProps {
  /** The dashboard's base path (empty at the root). */
  base?: string;
  /** Serve the client's own demo data. Off by default — see office-app.ts. */
  mock?: boolean;
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
    const frameRef = useRef<HTMLIFrameElement | null>(null);

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

    return R.createElement("div", {
      "data-testid": "office-app",
      className: "office-game",
    }, children);
  };
}
