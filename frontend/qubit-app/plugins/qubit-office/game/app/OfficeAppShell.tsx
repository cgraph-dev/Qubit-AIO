/* The Qubit Office page: the vendored office client, mounted in the plugin.
 *
 * React comes from the HOST. This module has no `import from "react"` and uses
 * `R.createElement` rather than JSX — the dashboard injects exactly one React,
 * and a second copy would silently split the hook runtime.
 */

import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OFFICE_APP_PATH, officeAppSrc } from "./office-app";
import { POLL_INTERVAL_MS, SNAPSHOT_ROUTE, bindingSummary } from "./office-api";

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
      // The iframe filling the whole page area is the point: the client was
      // written to BE the page, so it gets one.
      frame.src = officeAppSrc({ base: props.base, mock: props.mock });
      const onLoad = () => {
        setLoaded(true);
        setFailed(null);
        props.onReady?.();
      };
      const onError = () => setFailed("The office app failed to load.");
      frame.addEventListener("load", onLoad);
      frame.addEventListener("error", onError);
      return () => {
        frame.removeEventListener("load", onLoad);
        frame.removeEventListener("error", onError);
      };
    }, [props.base, props.mock]);

    const children: unknown[] = [
      R.createElement("iframe", {
        key: "frame",
        ref: frameRef,
        title: "Qubit Office",
        "data-testid": "office-app-frame",
        className: "office-game__frame",
        // The app is same-origin and needs to fetch with the session cookie,
        // but it is NOT sandboxed into a null origin, which would break that
        // fetch. It also needs to run scripts and own its own storage.
        allow: "clipboard-read; clipboard-write",
        style: { border: "0", width: "100%", height: "100%", display: "block" },
      }),
    ];

    if (!loaded && !failed) {
      children.push(R.createElement("div", {
        key: "loading",
        className: "office-game__panel",
        "data-testid": "office-app-loading",
        style: { position: "absolute", inset: "50% 0 auto 0", textAlign: "center" },
      }, "Loading the office\u2026"));
    }

    if (failed) {
      children.push(R.createElement("div", {
        key: "failed",
        className: "office-game__refusal",
        "data-testid": "office-app-failed",
        role: "alert",
        style: { position: "absolute", left: 12, top: 12 },
      }, [
        R.createElement("div", { key: "m" }, failed),
        // The one thing a reader needs when it fails: WHERE it was looking.
        R.createElement("div", {
          key: "p", style: { fontSize: "var(--office-fs-xs)" },
        }, OFFICE_APP_PATH),
      ]));
    }

    // A status bar that states the truth about the binding. It is not a
    // decoration: the interactive half is genuinely not wired, and a page that
    // does not say so reads as either broken or lying.
    children.push(R.createElement("div", {
      key: "status",
      className: "office-game__bar",
      "data-testid": "office-app-status",
      role: "status",
      style: { position: "absolute", left: 0, right: 0, bottom: 0 },
    }, [
      R.createElement("span", { key: "s" }, bindingSummary()),
      R.createElement("span", {
        key: "r", style: { opacity: 0.7 },
      }, `poll ${POLL_INTERVAL_MS / 1000}s \u2192 ${SNAPSHOT_ROUTE}`),
    ]));

    return R.createElement("div", {
      "data-testid": "office-app",
      className: "office-game",
      style: { position: "relative", width: "100%", height: "100%" },
    }, children);
  };
}
