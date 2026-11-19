import { useSyncExternalStore } from "react";

/**
 * True only while the route gate has `html[data-qubit-manga="on"]`.
 *
 * Components rendered outside the route tree (portals, shared selects) must
 * follow this attribute instead of a one-time `window.location` snapshot:
 * client navigation to an excluded route (/chat, Office, Workbench, HUD)
 * removes it while the manga theme stays selected.
 */
function subscribe(onChange: () => void): () => void {
  if (typeof document === "undefined") return () => {};
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-qubit-manga"],
  });
  return () => observer.disconnect();
}

function active(): boolean {
  return (
    typeof document !== "undefined" &&
    document.documentElement.dataset.qubitManga === "on"
  );
}

export function useMangaLayerActive(): boolean {
  return useSyncExternalStore(subscribe, active, () => false);
}
