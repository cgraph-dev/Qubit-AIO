/**
 * "A new build is available" notice for long-lived dashboard tabs.
 *
 * Builds are no longer wiped on rebuild (old hashed chunks stay on disk), so an
 * open tab keeps working — and keeps running OLD code. That is how a shipped
 * fix appears to vanish: the tab never reloads, so it keeps the previous
 * bundle. This watch compares the entry chunk the document loaded with the one
 * the server serves now, and offers a one-click reload when they differ.
 *
 * Client-only and dependency-free: plain DOM, so it also works while the SPA
 * shell is degraded. Never auto-reloads (a turn may be streaming) and never
 * spams: one banner per detected build, dismissible until the NEXT build.
 */
const DEFAULT_INTERVAL_MS = 5 * 60 * 1000;
const DISMISS_KEY = "qubit.build-notice-dismissed";

export function loadedBundleName(doc: Document = document): string {
  const el = doc.querySelector('script[type="module"][src*="/assets/index-"]');
  const src = el?.getAttribute("src") ?? "";
  const base = src.split("/").pop() ?? "";
  return base.endsWith(".js") ? base : "";
}

export function servedBundleName(html: string): string {
  const match = html.match(
    /<script[^>]+type="module"[^>]+src="([^"]*\/assets\/index-[^"]+\.js)"/,
  );
  if (!match) return "";
  const base = match[1].split("/").pop() ?? "";
  return base.endsWith(".js") ? base : "";
}

export interface BuildWatchOptions {
  fetchFn?: typeof fetch;
  intervalMs?: number;
  doc?: Document;
  onNewBuild?: (bundle: string) => void;
  /** URL that serves the SPA shell (same document type this page loaded). */
  shellUrl?: string;
  now?: () => number;
}

export function installBuildWatch({
  fetchFn = fetch,
  intervalMs = DEFAULT_INTERVAL_MS,
  doc = document,
  onNewBuild,
  shellUrl = "/chat",
  now = Date.now,
}: BuildWatchOptions = {}): () => void {
  let stopped = false;
  let lastCheck = 0;
  let noticed = "";

  const check = async () => {
    if (stopped || doc.visibilityState === "hidden") return;
    // Collapse focus + interval + visibility into one probe per interval.
    if (now() - lastCheck < intervalMs / 2) return;
    lastCheck = now();
    const mine = loadedBundleName(doc);
    if (!mine) return; // dev server / unknown document: nothing to compare
    try {
      const res = await fetchFn(shellUrl, { cache: "no-store" });
      if (!res.ok) return;
      const remote = servedBundleName(await res.text());
      if (!remote || remote === mine || remote === noticed) return;
      noticed = remote;
      onNewBuild?.(remote);
    } catch {
      // offline / server restarting: try again next tick
    }
  };

  const onVisible = () => {
    if (doc.visibilityState === "visible") void check();
  };
  const timer = setInterval(() => void check(), intervalMs);
  doc.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", onVisible);
  void check();

  return () => {
    stopped = true;
    clearInterval(timer);
    doc.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("focus", onVisible);
  };
}

/** The visible notice: a fixed banner with Reload + dismiss. */
export function showBuildNotice(
  bundle: string,
  doc: Document = document,
  reload: () => void = () => window.location.reload(),
): void {
  if (doc.getElementById("qubit-build-notice")) return;
  try {
    if (doc.defaultView?.sessionStorage.getItem(DISMISS_KEY) === bundle) return;
  } catch {
    /* storage blocked: show it anyway, one banner only */
  }
  const wrap = doc.createElement("div");
  wrap.id = "qubit-build-notice";
  wrap.setAttribute("role", "status");
  wrap.setAttribute("aria-live", "polite");

  // Painted from the ACTIVE theme's tokens, never from fixed hexes. The banner is
  // injected into <body> outside the React tree, so it gets no className — the
  // only way it can follow a skin is to read the same custom properties the app
  // renders from. An earlier build hardcoded a teal (#06201f / #7dd3c8), which
  // is why a Midnight user saw the cyberpunk-looking green banner.
  const looks = [
    "position:fixed",
    "right:16px",
    "bottom:16px",
    "z-index:2147483000",
    "display:flex",
    "align-items:center",
    "gap:10px",
    "max-width:min(92vw,420px)",
    "padding:10px 12px",
    "border:1px solid color-mix(in srgb, var(--midground) 35%, transparent)",
    "border-radius:var(--theme-radius, 10px)",
    "background:color-mix(in srgb, var(--midground-base) 6%, var(--background-base))",
    "color:var(--midground)",
    "font:13px/1.4 system-ui,sans-serif",
    "box-shadow:0 10px 34px rgb(0 0 0 / 45%)",
  ];
  wrap.setAttribute("style", looks.join(";"));

  const text = doc.createElement("span");
  text.textContent = "A new Qubit build is available.";
  text.setAttribute("style", "flex:1 1 auto");

  const reloadBtn = doc.createElement("button");
  reloadBtn.type = "button";
  reloadBtn.textContent = "Reload";
  const accentStyle =
    "padding:4px 10px;border:0;border-radius:calc(var(--theme-radius, 10px) - 4px);cursor:pointer;" +
    "background:var(--midground);color:var(--background-base);font:inherit;font-weight:600";
  reloadBtn.setAttribute("style", accentStyle);
  reloadBtn.addEventListener("click", reload);

  const closeBtn = doc.createElement("button");
  closeBtn.type = "button";
  closeBtn.textContent = "×";
  closeBtn.setAttribute("aria-label", "Dismiss update notice");
  closeBtn.setAttribute(
    "style",
    "border:0;background:transparent;color:var(--midground);opacity:.7;font-size:16px;line-height:1;cursor:pointer",
  );
  closeBtn.addEventListener("click", () => {
    try {
      doc.defaultView?.sessionStorage.setItem(DISMISS_KEY, bundle);
    } catch {
      /* ignore */
    }
    wrap.remove();
  });

  wrap.append(text, reloadBtn, closeBtn);
  doc.body.appendChild(wrap);
}
