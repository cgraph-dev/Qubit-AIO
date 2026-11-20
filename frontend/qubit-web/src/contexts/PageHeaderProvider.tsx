import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
import { PageHeaderContext } from "./page-header-context";
import { resolvePageTitle } from "@/lib/resolve-page-title";
import { isChatPath } from "@/lib/chat-routes";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

interface HeaderSlots {
  pathname: string;
  titleOverride: string | null;
  afterTitle: ReactNode;
  end: ReactNode;
}

function emptySlots(pathname: string): HeaderSlots {
  return { pathname, titleOverride: null, afterTitle: null, end: null };
}

export function PageHeaderProvider({
  children,
  pluginTabs,
}: {
  children: ReactNode;
  pluginTabs: { path: string; label: string }[];
}) {
  const { pathname } = useLocation();
  const { t } = useI18n();
  // Clear the previous route's slots before descendants register their new
  // controls. A parent layout effect runs after child layout effects and
  // would wipe controls that registered during the same commit.
  const [slots, setSlots] = useState(() => emptySlots(pathname));
  if (slots.pathname !== pathname) setSlots(emptySlots(pathname));
  const { titleOverride, afterTitle, end } = slots;

  const mainRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [pathname]);

  const defaultTitle = useMemo(
    () => resolvePageTitle(pathname, t, pluginTabs),
    [pathname, t, pluginTabs],
  );
  const displayTitle = titleOverride ?? defaultTitle;

  // Keep the browser tab in step with the routed page. Without this the tab
  // stayed on the static index.html title for the whole session, so every page
  // had the same title in history/bookmarks. Per-page usePageHeader overrides
  // (setTitle) flow through `displayTitle` too.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const brand = t.app.brand || "Qubit AiO";
    document.title = displayTitle ? `${displayTitle} · ${brand}` : brand;
  }, [displayTitle, t.app.brand]);

  const isChatRoute = isChatPath(pathname);
  /** Env jump-nav is wide — stack below title on small screens so KEYS stays readable. */
  const isEnvRoute =
    pathname === "/env" || pathname.startsWith("/env/");

  const value = useMemo(() => {
    const update = (patch: Partial<HeaderSlots>) => {
      setSlots((current) => current.pathname === pathname ? { ...current, ...patch } : current);
    };
    return {
      setAfterTitle: (node: ReactNode) => update({ afterTitle: node }),
      setEnd: (node: ReactNode) => update({ end: node }),
      setTitle: (titleOverride: string | null) => update({ titleOverride }),
    };
  }, [pathname]);

  return (
    <PageHeaderContext.Provider value={value}>
      <div className="flex min-h-0 w-full min-w-0 flex-1 flex-col overflow-hidden">
        <header
          role="banner"
          data-dashboard-page-header=""
          className={cn(
            "relative z-1 w-full shrink-0 border-b border-border bg-background-base",
            isChatRoute && "hidden",
          )}
        >
          <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 px-3 py-3 sm:px-6">
            <div className={cn(
              "flex min-w-0 flex-[1_1_20rem] flex-wrap items-center gap-2",
              isEnvRoute && "items-start",
            )}>
              <div className="qubit-module-header">
                <h1 className="min-w-0 max-w-full font-expanded text-sm font-bold tracking-[0.08em] text-midground [overflow-wrap:anywhere]">
                  {displayTitle}
                </h1>
                {afterTitle ? (
                  <div
                    data-page-header-actions=""
                    className={cn(
                      "qubit-module-header__meta min-w-0 max-w-full overflow-x-auto scrollbar-none",
                      isEnvRoute && "w-full",
                    )}
                  >
                    {afterTitle}
                  </div>
                ) : null}
              </div>
            </div>
            {end ? (
              <div data-page-header-actions="" className="flex min-w-0 max-w-full flex-wrap justify-start sm:justify-end">
                {end}
              </div>
            ) : null}
          </div>
        </header>

        <main
          ref={mainRef}
          id="qubit-main-content"
          tabIndex={-1}
          className={cn(
            "min-h-0 w-full min-w-0 flex-1 flex flex-col",
            // Bottom inset for scrolled pages lives on the route outlet wrapper in
            // `App.tsx` (`w-full min-w-0`) so it pads scrollable content, not flex chrome.
            isChatRoute
              ? "overflow-hidden"
              : "overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable]",
          )}
        >
          {children}
        </main>
      </div>
    </PageHeaderContext.Provider>
  );
}
