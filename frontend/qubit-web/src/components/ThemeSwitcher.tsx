import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { Palette, Check, Type } from "lucide-react";
import { ListItem, Button } from "@/sakuga/ui";
import { BottomSheet } from "@nous-research/ui/ui/components/bottom-sheet";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { useBelowBreakpoint } from "@nous-research/ui/hooks/use-below-breakpoint";
import { BUILTIN_THEMES, THEME_DEFAULT_FONT_ID, useTheme } from "@/themes";
import { isMangaThemeName } from "@/themes/presets";
import type { DashboardTheme, FontChoice, ThemeListEntry } from "@/themes";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Compact theme picker mounted next to the language switcher in the header.
 * Each dropdown row shows a 3-stop swatch (background / midground / warm
 * glow) so users can preview the palette before committing. User-defined
 * themes from the runtime home's `dashboard-themes/*.yaml` use their API-provided
 * definitions so they show real palette swatches just like built-ins.
 *
 * When placed at the bottom of a container (e.g. the sidebar rail), pass
 * `dropUp` so the menu opens above the trigger instead of clipping below
 * the viewport. On viewports below the `sm` breakpoint, `dropUp` uses a
 * bottom sheet portaled to `document.body` so the picker is not clipped by
 * the sidebar (same idea as a responsive Drawer).
 */
export function ThemeSwitcher({ collapsed = false, dropUp = false }: ThemeSwitcherProps) {
  const { themeName, availableThemes, setTheme, fontId, fontChoices, setFont } = useTheme();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [flyoutStyle, setFlyoutStyle] = useState<CSSProperties>();
  const pickerId = useId();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const narrowViewport = useBelowBreakpoint(640);
  const useMobileSheet = Boolean(dropUp && narrowViewport);

  const close = useCallback(() => setOpen(false), []);
  const closeAndFocus = useCallback(() => {
    close();
    requestAnimationFrame(() => wrapperRef.current?.querySelector<HTMLElement>(".qm-theme-trigger")?.focus());
  }, [close]);

  useEffect(() => {
    if (!open || useMobileSheet) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.key !== "Escape") return;
      event.preventDefault();
      closeAndFocus();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.composedPath().includes(wrapperRef.current!) ||
          event.composedPath().includes(dropdownRef.current!)) return;
      close();
    };
    const onFocus = (event: FocusEvent) => {
      const target = event.target as Node | null;
      if (wrapperRef.current?.contains(target) || dropdownRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("focusin", onFocus);
    };
  }, [open, close, closeAndFocus, useMobileSheet]);

  useLayoutEffect(() => {
    if (!open || useMobileSheet) return;
    const panel = dropdownRef.current;
    const refresh = (event?: Event) => {
      if (!dropUp || (event?.target instanceof Node && panel?.contains(event.target))) return;
      const anchor = wrapperRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const viewport = window.visualViewport;
      const width = viewport?.width ?? window.innerWidth;
      const height = viewport?.height ?? window.innerHeight;
      const x = viewport?.offsetLeft ?? 0;
      const y = viewport?.offsetTop ?? 0;
      const panelWidth = Math.min(366, Math.max(1, width - 24));
      const panelHeight = Math.min(570, Math.max(1, height - 24));
      const sidebarRight = document.getElementById("app-sidebar")?.getBoundingClientRect().right;
      const desiredLeft = Math.max(sidebarRight ?? 0, anchor.right) + 8;
      setFlyoutStyle({
        left: Math.max(x + 12, Math.min(desiredLeft, x + width - panelWidth - 12)),
        top: Math.max(y + 12, Math.min(anchor.bottom - panelHeight, y + height - panelHeight - 12)),
        width: panelWidth,
        maxHeight: panelHeight,
      });
    };
    refresh();
    panel?.focus();
    window.addEventListener("resize", refresh);
    window.addEventListener("scroll", refresh, true);
    window.visualViewport?.addEventListener("resize", refresh);
    window.visualViewport?.addEventListener("scroll", refresh);
    return () => {
      window.removeEventListener("resize", refresh);
      window.removeEventListener("scroll", refresh, true);
      window.visualViewport?.removeEventListener("resize", refresh);
      window.visualViewport?.removeEventListener("scroll", refresh);
    };
  }, [open, dropUp, useMobileSheet]);

  const current = availableThemes.find((th) => th.name === themeName);
  const label = current?.label ?? themeName;
  const mangaActive = isMangaThemeName(themeName);
  const sheetTitle = t.theme?.title ?? "Theme";

  return (
    <div ref={wrapperRef} className="relative">
      <Button
        variant="secondary"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "qm-theme-trigger",
          collapsed
            ? "text-text-secondary hover:text-foreground hover:bg-transparent"
            : "px-2 py-1 normal-case tracking-normal font-normal text-xs text-text-secondary hover:text-foreground",
        )}
        data-manga-theme={mangaActive ? "true" : undefined}
        title={`${t.theme?.switchTheme ?? "Switch theme"}: ${label}`}
        aria-label={t.theme?.switchTheme ?? "Switch theme"}
        {...{ "aria-expanded": String(open) }}
        aria-haspopup="dialog"
        aria-controls={open && !useMobileSheet ? pickerId : undefined}
      >
        <span className="inline-flex items-center gap-1.5">
          {mangaActive ? (
            <span aria-hidden className="qm-theme-trigger__spark">
              ✦
            </span>
          ) : (
            <Palette className="h-3.5 w-3.5" />
          )}

          {!collapsed && (
            <Typography
              className="qm-theme-trigger__label hidden sm:inline text-display tracking-wide text-xs"
            >
              {label}
            </Typography>
          )}

          {mangaActive && (
            <span aria-hidden className="qm-theme-trigger__lamp" />
          )}
        </span>
      </Button>

      {useMobileSheet && (
        <BottomSheet
          backdropDismissLabel={t.common.close}
          onClose={closeAndFocus}
          open={open}
          title={sheetTitle}
        >
          <div data-appearance-picker="">
            <ThemeSwitcherOptions
              availableThemes={availableThemes}
              close={closeAndFocus}
              setTheme={setTheme}
              themeName={themeName}
            />
            <FontSection
              fontChoices={fontChoices}
              fontId={fontId}
              setFont={setFont}
            />
          </div>
        </BottomSheet>
      )}

      {open && !useMobileSheet && (() => {
        const dropdown = (
          <div
            ref={dropdownRef}
            aria-label={sheetTitle}
            className={cn(
              "qrail-flyout",
              dropUp ? "fixed z-[100]" : "absolute z-50 right-0 top-full mt-1",
            )}
            id={pickerId}
            data-appearance-picker=""
            tabIndex={-1}
            role="dialog"
            style={dropUp ? flyoutStyle : undefined}
          >
            <div className="qrail-flyout__head">
              <Palette className="h-3 w-3" />
              <Typography className="qrail-flyout__title">{sheetTitle}</Typography>
            </div>

            <div className="qrail-flyout__body">
              <ThemeSwitcherOptions
                availableThemes={availableThemes}
                close={closeAndFocus}
                setTheme={setTheme}
                themeName={themeName}
              />
              <FontSection
                fontChoices={fontChoices}
                fontId={fontId}
                setFont={setFont}
              />
            </div>
          </div>
        );
        return dropUp ? createPortal(dropdown, document.body) : dropdown;
      })()}
    </div>
  );
}

function ThemeSwitcherOptions({
  availableThemes,
  close,
  setTheme,
  themeName,
}: ThemeSwitcherOptionsProps) {
  return (
    <>
      {availableThemes.map((th) => {
        const isActive = th.name === themeName;
        const paletteTheme = BUILTIN_THEMES[th.name] ?? th.definition;
        const mangaRow = isMangaThemeName(th.name);

        return (
          <ListItem
            active={isActive}
            aria-pressed={isActive}
            className={cn("gap-3", mangaRow && "qm-theme-row qm-theme-row--manga")}
            data-manga-theme={mangaRow ? "true" : undefined}
            key={th.name}
            onClick={() => {
              setTheme(th.name);
              close();
            }}
          >
            {mangaRow ? (
              <span aria-hidden className="qm-theme-row__spark">
                ✦
              </span>
            ) : paletteTheme ? (
              <ThemeSwatch theme={paletteTheme} />
            ) : (
              <PlaceholderSwatch />
            )}

            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <Typography
                className="break-words text-display text-xs tracking-wide"
              >
                {th.label}
              </Typography>
              {th.description && (
                <Typography className="break-words text-xs leading-normal tracking-normal text-text-tertiary">
                  {th.description}
                </Typography>
              )}
            </div>

            {mangaRow ? (
              <span
                aria-hidden
                className={cn(
                  "qm-theme-row__lamp",
                  isActive && "qm-theme-row__lamp--on",
                )}
              />
            ) : (
              <Check
                className={cn(
                  "h-3 w-3 shrink-0 text-midground",
                  isActive ? "opacity-100" : "opacity-0",
                )}
              />
            )}
          </ListItem>
        );
      })}
    </>
  );
}

const FONT_CATEGORY_LABEL_KEY: Record<FontChoice["category"], "fontSans" | "fontSerif" | "fontMono"> = {
  sans: "fontSans",
  serif: "fontSerif",
  mono: "fontMono",
};

/** Font-override section rendered below the theme list. Lets the user pick
 *  any catalog font independently of the active theme, or "Theme default"
 *  to clear the override. Rows show the catalog names without fetching every webfont. */
function FontSection({ fontChoices, fontId, setFont }: FontSectionProps) {
  const { t } = useI18n();
  const order: FontChoice["category"][] = ["sans", "serif", "mono"];
  return (
    <>
      <div className="mt-1 border-t border-current/20 px-3 pb-1 pt-2">
        <span className="inline-flex items-center gap-1.5">
          <Type className="h-3 w-3 text-text-tertiary" />
          <Typography
            className="text-display text-xs tracking-[0.12em] text-text-tertiary"
          >
            {t.theme?.fontTitle ?? "Font"}
          </Typography>
        </span>
      </div>

      {/* Theme-default (clears the override). */}
      <ListItem
        active={fontId === THEME_DEFAULT_FONT_ID}
        aria-pressed={fontId === THEME_DEFAULT_FONT_ID}
        className="gap-3"
        onClick={() => setFont(THEME_DEFAULT_FONT_ID)}
      >
        <span aria-hidden className="h-4 w-9 shrink-0" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Typography className="break-words text-xs tracking-normal">
            {t.theme?.fontDefault ?? "Theme default"}
          </Typography>
          <Typography className="break-words text-xs leading-normal tracking-normal text-text-tertiary">
            {t.theme?.fontDefaultHint ?? "Use the active theme's font"}
          </Typography>
        </div>
        <Check
          className={cn(
            "h-3 w-3 shrink-0 text-midground",
            fontId === THEME_DEFAULT_FONT_ID ? "opacity-100" : "opacity-0",
          )}
        />
      </ListItem>

      {order.map((cat) => {
        const fonts = fontChoices.filter((f) => f.category === cat);
        if (fonts.length === 0) return null;
        const catLabel = t.theme?.[FONT_CATEGORY_LABEL_KEY[cat]] ?? cat;
        return (
          <div key={cat}>
            <div className="px-3 pb-0.5 pt-1.5">
              <Typography className="text-[0.65rem] uppercase tracking-[0.1em] text-text-tertiary">
                {catLabel}
              </Typography>
            </div>
            {fonts.map((f) => {
              const isActive = f.id === fontId;
              return (
                <ListItem
                  active={isActive}
                  aria-pressed={isActive}
                  className="gap-3"
                  key={f.id}
                  onClick={() => setFont(f.id)}
                      >
                  <span aria-hidden className="h-4 w-9 shrink-0" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span
                      className="break-words text-sm"
                    >
                      {f.label}
                    </span>
                  </div>
                  <Check
                    className={cn(
                      "h-3 w-3 shrink-0 text-midground",
                      isActive ? "opacity-100" : "opacity-0",
                    )}
                  />
                </ListItem>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

function ThemeSwatch({ theme }: { theme: DashboardTheme }) {
  const [c1, c2, c3] = theme.swatchColors ?? [
    theme.palette.background.hex,
    theme.palette.midground.hex,
    theme.palette.warmGlow,
  ];
  return (
    <span aria-hidden className="qrail-swatch" style={{ borderColor: c2 }}>
      <span style={{ background: c1 }} />
      <span style={{ background: c2 }} />
      <span style={{ background: c3 }} />
    </span>
  );
}

function PlaceholderSwatch() {
  return (
    <span aria-hidden className="qrail-swatch qrail-swatch--empty" />
  );
}

interface ThemeSwitcherOptionsProps {
  availableThemes: ThemeListEntry[];
  close: () => void;
  setTheme: (name: string) => void;
  themeName: string;
}

interface FontSectionProps {
  fontChoices: FontChoice[];
  fontId: string;
  setFont: (id: string) => void;
}

interface ThemeSwitcherProps {
  collapsed?: boolean;
  dropUp?: boolean;
}
