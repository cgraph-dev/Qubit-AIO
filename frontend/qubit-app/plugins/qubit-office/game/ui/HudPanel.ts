/* The HUD, opened from inside the Office — by FRAMING the HUD, not copying it.
 *
 * THE DESIGN FAILURE THIS REPLACES
 * --------------------------------
 * An earlier version watched this document's `window.__QUBIT_PLUGINS__` for the
 * HUD's registration and mounted the registered component in the dock. The
 * HUD is a DASHBOARD plugin: its bundle (`/dashboard-plugins/qubit-hud/
 * qubit-hud.js`) is injected by the dashboard's own loader into the DASHBOARD
 * document. The office page is a full-bleed iframe of the vendored app
 * (`OfficeAppShell.tsx`), a DIFFERENT document that never loads that script —
 * so the registry there is undefined, the tap stayed empty, and the dock painted
 * "The HUD is not available on this page" every time it was opened. Measured on
 * the owner's screen. A registration cannot cross a document boundary; the
 * mechanism was wrong, not the wiring.
 *
 * WHY AN IFRAME OF /qubit-hud
 * ---------------------------
 * `/qubit-hud` is the HUD's own route in the dashboard SPA: its bundle, its
 * stylesheet, its React, its authenticated client, its seven GET tabs. Framing
 * that document means the panels are the REAL HUD — not a copy that drifts, not
 * a second React root with a second fetch seam, and no shared global that has
 * to be smuggled into a realm we do not own.
 *
 * THE OFFICE STAYS READ-ONLY (C4)
 * ------------------------------
 * This module issues NO request and performs NO mutation. It renders a frame
 * whose `src` is a route string. Every read the HUD makes happens inside the
 * HUD's own document, through the HUD's own client, exactly as it does on
 * `/qubit-hud` — the office page is not a party to any of it, and the staged-op
 * journal is never in this path. That is the same boundary the vendored app
 * already crosses deliberately (it, too, is a same-origin iframe that reads and
 * writes through its own bridge); here the framed document is Qubit's own,
 * first-party observability page, which is a strictly narrower grant.
 *
 * WHY THE SERVER HAD TO GRANT FRAMING
 * -----------------------------------
 * `webui.py::security_headers` emits `X-Frame-Options: DENY` and
 * `frame-ancestors 'none'` on every document except the vendored office app,
 * so framing `/qubit-hud` was refused by the browser (measured live: DENY).
 * The grant is now made for the HUD ROUTE alone (`_serve_qubit_index` sets
 * `_allow_same_origin_frame` when the request path is `/qubit-hud`), which
 * yields `SAMEORIGIN` + `frame-ancestors 'self'` — same origin only, one path,
 * every other response unchanged. See `HUD_PATH` below and the docstring on
 * `_serve_qubit_index`.
 *
 * THE LINK, NOT ONLY THE FRAME
 * ----------------------------
 * The frame can be refused (a stale server, an embedding browser policy). When
 * it is, the dock NAMES that and still offers the link to `/qubit-hud`, so the
 * HUD is one click away instead of nowhere. The link is rendered unconditionally
 * in the dock head for the same reason: the frame is a convenience, the route is
 * the authority.
 */

/** The HUD's own dashboard route. Framed by the dock; also the fallback link. */
export const HUD_PATH = "/qubit-hud";

/** The HUD plugin's served bundle (its manifest's `entry`, for the record). */
export const HUD_ASSET_ROOT = "/dashboard-plugins/qubit-hud";
export const HUD_SCRIPT = `${HUD_ASSET_ROOT}/qubit-hud.js`;

/** A same-origin frame's height inside a side sheet. */
export const HUD_FRAME_HEIGHT = "100%";

/** The frame's title: what a screen reader announces for the region. */
export const HUD_FRAME_TITLE = "Qubit HUD";

/**
 * The dashboard's base path, read from the host's injected global — the same
 * seam `office-app.ts::hostBasePath` uses. A deployment behind a subpath must
 * frame `<base>/qubit-hud`, not `/qubit-hud`.
 */
export function hostBasePath(): string {
  if (typeof window === "undefined") return "";
  const raw = (window as { __QUBIT_BASE_PATH__?: string }).__QUBIT_BASE_PATH__;
  if (!raw) return "";
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

/**
 * The URL the dock frames: `HUD_PATH`, rebased if the dashboard is.
 *
 * An OMITTED base falls back to the host's injected global — `undefined or ""`
 * must resolve the same way `office-app.ts::officeAppSrc` does, or a subpath
 * deployment frames `/qubit-hud` at the domain root and gets the wrong page.
 * An explicit empty string means "served at the root" and is honoured.
 */
export function hudFrameSrc(base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  return `${b}${HUD_PATH}`;
}

/** React and hooks come from the HOST — same rule as the rest of the plugin. */
export interface CreateHudDockArgs {
  React: {
    createElement: (...args: unknown[]) => unknown;
  };
  hooks: {
    useState: <T>(v: T) => [T, (next: T | ((prev: T) => T)) => void];
    useEffect: (fn: () => void | (() => void), deps?: unknown[]) => void;
  };
  /** An explicit base path (a test); otherwise the host's injected global. */
  base?: string;
}

/** The dock's body: the framed HUD, with its refusals named. */
export function createHudDock(args: CreateHudDockArgs) {
  const { React: R, hooks, base } = args;
  const src = hudFrameSrc(base ?? hostBasePath());

  return function HudDock() {
    // The frame's own load state. A frame that never loads is not a HUD that
    // loaded and rendered nothing: the difference is exactly what a reader
    // needs, so it is tracked rather than assumed.
    const [loaded, setLoaded] = hooks.useState(false);

    const note = (id: string, text: string) =>
      R.createElement("div", {
        key: id,
        className: "office-game__panel office-game__hudNote",
        "data-testid": id,
        role: "status",
        "aria-live": "polite",
      }, text);

    return R.createElement("div", {
      className: "office-game__frameWrap",
      "data-testid": "office-game-hud-frame-wrap",
      "data-state": loaded ? "ready" : "loading",
    }, [
      // No `sandbox` attribute: the framed document is Qubit's own dashboard on
      // the same origin and needs the same privileges it has on its own route
      // (storage, its own script, its own fetch). A sandbox here would not add
      // a boundary — it is the same origin either way — it would only strip
      // the HUD's storage and break the page. The boundary that matters is the
      // server's: `frame-ancestors 'self'`, granted to this route alone.
      R.createElement("iframe", {
        key: "frame",
        "data-testid": "office-game-hud-frame",
        className: "office-game__hudFrame",
        title: HUD_FRAME_TITLE,
        src,
        height: HUD_FRAME_HEIGHT,
        // No credential is passed in the URL: the HUD document is served by the
        // same server and its client authenticates itself exactly as it does
        // when the route is opened directly.
        referrerPolicy: "same-origin",
        onLoad: () => setLoaded(true),
      }),
      // The route itself, always reachable, even when the frame is not.
      R.createElement("p", {
        key: "direct",
        className: "office-game__hudDirect",
      }, [
        R.createElement("a", {
          key: "a",
          className: "office-game__hudLink",
          "data-testid": "office-game-hud-open",
          href: src,
          target: "_blank",
          rel: "noopener",
        }, "Open HUD in its own tab"),
      ]),
      !loaded
        ? note("office-game-hud-loading",
               `Loading the HUD from ${src}\u2026 If it does not appear, open it `
               + "in its own tab instead.")
        : null,
    ]);
  };
}
