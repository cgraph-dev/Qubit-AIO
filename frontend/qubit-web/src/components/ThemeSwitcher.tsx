import { FxButton } from "@/sakuga/FxButton";
import { useId, useRef, useState } from "react";
import { UtilityPickerPanel } from "@/manga-skin/UtilityPickerPanel";
import { Palette, Check, Type } from "lucide-react";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { BUILTIN_THEMES, THEME_DEFAULT_FONT_ID, useTheme } from "@/themes";
import { isMangaThemeName } from "@/themes/presets";
import type { DashboardTheme, FontChoice, ThemeListEntry } from "@/themes";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/** Palette and font choices in the same panel as the language picker. */
export function ThemeSwitcher({ collapsed = false }: ThemeSwitcherProps) {
  const { themeName, availableThemes, setTheme, fontId, fontChoices, setFont } = useTheme();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const pickerId = useId();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const close = () => setOpen(false);
  const current = availableThemes.find((th) => th.name === themeName);
  const label = current?.label ?? themeName;
  const mangaActive = isMangaThemeName(themeName);
  const pickerTitle = t.theme?.title ?? "Theme";

  return (
    <div ref={wrapperRef} className="relative">
      <FxButton
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "qm-theme-trigger qm-utility-trigger",
          collapsed
            ? "text-text-secondary hover:text-foreground hover:bg-transparent"
            : "px-2 py-1 normal-case tracking-normal font-normal text-xs text-text-secondary hover:text-foreground",
        )}
        data-manga-theme={mangaActive ? "true" : undefined}
        title={`${t.theme?.switchTheme ?? "Switch theme"}: ${label}`}
        aria-label={`${t.theme?.switchTheme ?? "Switch theme"}: ${label}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? pickerId : undefined}
      >
        <span className="inline-flex items-center gap-1.5">
          {mangaActive ? (
            <span aria-hidden className="qm-theme-trigger__spark">
              ✦
            </span>
          ) : (
            <Palette aria-hidden className="h-3.5 w-3.5" />
          )}

          {!collapsed && (
            <Typography
              className="qm-theme-trigger__label text-display tracking-wide text-xs"
            >
              {label}
            </Typography>
          )}

          {mangaActive && (
            <span aria-hidden className="qm-theme-trigger__lamp" />
          )}
        </span>
      </FxButton>

      {open && (
        <UtilityPickerPanel id={pickerId} title={pickerTitle} closeLabel={t.common.close} anchor={wrapperRef} onClose={close}>
          <div role="group" aria-label={pickerTitle}>
            <ThemeSwitcherOptions availableThemes={availableThemes} close={close} setTheme={setTheme} themeName={themeName} />
          </div>
          <FontSection fontChoices={fontChoices} fontId={fontId} setFont={setFont} />
        </UtilityPickerPanel>
      )}
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
        const paletteTheme = Object.hasOwn(BUILTIN_THEMES, th.name) ? BUILTIN_THEMES[th.name] : th.definition;
        const mangaRow = isMangaThemeName(th.name);

        return (
          <FxButton type="button"
            aria-pressed={isActive}
            data-qm-picker-choice=""
            className="qm-picker-choice"
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
                className="break-words text-sm font-semibold tracking-normal"
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
          </FxButton>
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
      <FxButton type="button"
        aria-pressed={fontId === THEME_DEFAULT_FONT_ID}
        className="qm-picker-choice"
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
      </FxButton>

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
                <FxButton type="button"
                  aria-pressed={isActive}
                  className="qm-picker-choice"
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
                </FxButton>
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
}
