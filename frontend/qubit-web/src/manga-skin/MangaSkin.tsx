import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactElement,
} from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router";
import { emitFx } from "@/sakuga/ui";
import { useTheme } from "@/themes";
import {
  MANGA_THEME_LARGE_NAME,
  MANGA_THEME_NAME,
  isMangaThemeName,
} from "@/themes/presets";
import {
  applyMangaAttribute,
  currentMangaEnabled,
  mangaReturnTheme,
  mangaRouteEligible,
  setMangaEnabled,
  subscribeManga,
} from "./manga-store";
import "./manga.css";

/** The skin only exists when the gate is mounted under the router. */
function useMangaPreference(): boolean {
  return useSyncExternalStore(
    subscribeManga,
    currentMangaEnabled,
    () => false,
  );
}

/**
 * Mount exactly once under the router. Paints nothing visible itself: it keeps
 * the <html> marker in sync with (preference × eligible route) and portals the
 * opt-in switch into the existing sidebar utility strip.
 */
export function MangaSkinGate(): ReactElement | null {
  const { pathname } = useLocation();
  const enabled = useMangaPreference();

  // Layout effect: the marker must be gone before the excluded surface paints.
  useLayoutEffect(() => {
    applyMangaAttribute(enabled && mangaRouteEligible(pathname));
  }, [enabled, pathname]);

  useEffect(() => () => applyMangaAttribute(false), []);

  return <MangaSkinToggle />;
}

/** Find the sidebar utility strip without observing the whole document. */
function useUtilityHost(active: boolean): HTMLElement | null {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const observer = useRef<MutationObserver | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    const find = () => {
      if (cancelled) return true;
      const next = document.querySelector<HTMLElement>(
        "#app-sidebar .qagent-sidebar__utility",
      );
      if (next) {
        setHost(next);
        observer.current?.disconnect();
        observer.current = null;
        return true;
      }
      return false;
    };

    if (!find() && typeof MutationObserver !== "undefined") {
      observer.current = new MutationObserver(() => {
        find();
      });
      observer.current.observe(document.body, { childList: true, subtree: true });
    }

    return () => {
      cancelled = true;
      observer.current?.disconnect();
      observer.current = null;
    };
  }, [active]);

  return active ? host : null;
}

/**
 * The opt-in switch, portalled into the existing sidebar utility strip so it
 * sits beside the theme/language controls with no App.tsx changes.
 */
export function MangaSkinToggle(): ReactElement | null {
  const { pathname } = useLocation();
  const { themeName, setTheme } = useTheme();
  const enabled = useMangaPreference();
  const eligible = mangaRouteEligible(pathname);
  const host = useUtilityHost(eligible);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const onClick = useCallback(() => {
    if (enabled) {
      // Q6/Q6-L is a real theme: return to the previous non-manga theme so
      // the palette and the skin switch off together.
      if (isMangaThemeName(themeName)) setTheme(mangaReturnTheme());
      setMangaEnabled(false);
    } else {
      const next = themeName.endsWith("-l")
        ? MANGA_THEME_LARGE_NAME
        : MANGA_THEME_NAME;
      setTheme(next);
      setMangaEnabled(true);
    }
    // Presentation only; the engine swallows a missing WebGL context.
    void emitFx(enabled ? "pop" : "celebrate", {
      target: buttonRef.current,
      scale: 0.8,
      silent: true,
    }).catch(() => {});
  }, [enabled, setTheme, themeName]);

  if (!eligible || !host) return null;

  return createPortal(
    <button
      ref={buttonRef}
      type="button"
      className="qm-skin-switch"
      data-state={enabled ? "on" : "off"}
      aria-pressed={enabled}
      aria-label={enabled ? "Disable manga skin" : "Enable manga skin"}
      title={
        enabled
          ? "Manga skin is ON — click to restore the default theme"
          : "Enable the optional manga skin"
      }
      onClick={onClick}
    >
      <span aria-hidden="true" className="qm-skin-switch__spark">
        ✦
      </span>
      <span className="qm-skin-switch__label">Manga</span>
      <span aria-hidden="true" className="qm-skin-switch__lamp" />
    </button>,
    host,
  );
}
