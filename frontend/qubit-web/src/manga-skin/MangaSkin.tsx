import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router";
import "./manga.css";

export const MANGA_SKIN_KEY = "qubit-manga-skin-enabled";
export const MANGA_EXCLUDED_PATHS = [
  "/qubit-workbench", "/qubit-hud",
  "/qubit-office", "/qubit-office2", "/qubit-office3",
] as const;

export function mangaRouteEligible(pathname: string): boolean {
  return !MANGA_EXCLUDED_PATHS.some((path) => pathname === path || pathname.startsWith(path + "/"));
}

function readPreference(): boolean {
  try { return localStorage.getItem(MANGA_SKIN_KEY) === "true"; }
  catch { return false; }
}

/**
 * Web-only skin: no navigation replacement, no backend changes.
 * The sidebar control mounts into the existing utility strip via a portal,
 * preserving the current App.tsx route tree and persistent chat host.
 */
export function MangaSkinGate() {
  const { pathname } = useLocation();
  const [enabled, setEnabled] = useState(readPreference);
  const [utilityHost, setUtilityHost] = useState<HTMLElement | null>(null);
  const eligible = mangaRouteEligible(pathname);

  useEffect(() => {
    const sync = () => setEnabled(readPreference());
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  useEffect(() => {
    const findHost = () => {
      const host = document.querySelector<HTMLElement>("#app-sidebar .qagent-sidebar__utility");
      setUtilityHost((previous) => previous === host ? previous : host);
    };
    findHost();
    const observer = new MutationObserver(findHost);
    observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (enabled && eligible) document.documentElement.dataset.qubitManga = "on";
    else delete document.documentElement.dataset.qubitManga;
    return () => { delete document.documentElement.dataset.qubitManga; };
  }, [enabled, eligible]);

  const toggle = () => {
    const next = !enabled;
    try { localStorage.setItem(MANGA_SKIN_KEY, String(next)); } catch { /* private mode */ }
    setEnabled(next);
  };

  if (!eligible || !utilityHost) return null;
  return createPortal(
    <button
      type="button"
      className="qm-skin-switch"
      aria-label={enabled ? "Disable manga skin" : "Enable manga skin"}
      title={enabled ? "Disable manga skin" : "Enable manga skin"}
      aria-pressed={enabled}
      onClick={toggle}
    >
      <span aria-hidden="true" className="qm-skin-switch__spark">✦</span>
      <span className="qm-skin-switch__label">Manga</span>
      <span aria-hidden="true" className="qm-skin-switch__lamp" data-active={enabled} />
    </button>,
    utilityHost,
  );
}
