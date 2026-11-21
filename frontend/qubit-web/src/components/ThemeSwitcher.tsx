import { FxButton } from "@/sakuga/FxButton";
import { useId, useRef, useState } from "react";
import { UtilityPickerPanel } from "@/manga-skin/UtilityPickerPanel";
import { PickerRow } from "@/manga-skin/PickerPanel";
import { useMangaLayerActive } from "@/manga-skin/useMangaLayerActive";
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
        <UtilityPickerPanel
          id={pickerId}
          title={pickerTitle}
          closeLabel={t.common.close}
          anchor={wrapperRef}
          onClose={close}
          filterLabel={`Filter ${pickerTitle}`}
        >
          {(filter) => (
            <>
              <div role="group" aria-label={pickerTitle}>
                <ThemeSwitcherOptions
                  availableThemes={availableThemes}
                  close={close}
                  setTheme={setTheme}
                  themeName={themeName}
                  filter={filter}
                />
              </div>
              <FontSection fontChoices={fontChoices} fontId={fontId} setFont={setFont} filter={filter} />
            </>
          )}
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
  filter,
}: ThemeSwitcherOptionsProps) {
  const manga = useMangaLayerActive();
  const needle = filter.trim().toLocaleLowerCase();
  const shown = needle
    ? availableThemes.filter((th) =>
        [th.label, th.description ?? "", th.name]
          .some((value) => value.toLocaleLowerCase().includes(needle)),
      )
    : availableThemes;
  if (shown.length === 0) {
    return <div className="qm-select-empty" role="status">No matching themes</div>;
  }
  return (
    <>
      {shown.map((th) => {
        const isActive = th.name === themeName;
        const paletteTheme = Object.hasOwn(BUILTIN_THEMES, th.name) ? BUILTIN_THEMES[th.name] : th.definition;
        const mangaRow = isMangaThemeName(th.name);

        return (
          <PickerRow
            selected={isActive}
            key={th.name}
            onSelect={() => {
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
            ) : !manga ? (
              <Check
                className={cn(
                  "h-3 w-3 shrink-0 text-midground",
                  isActive ? "opacity-100" : "opacity-0",
                )}
              />
            ) : null}
          </PickerRow>
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
function FontSection({ fontChoices, fontId, setFont, filter }: FontSectionProps) {
  const { t } = useI18n();
  const manga = useMangaLayerActive();
  const needle = filter.trim().toLocaleLowerCase();
  const order: FontChoice["category"][] = ["sans", "serif", "mono"];
  const defaultLabel = t.theme?.fontDefault ?? "Theme default";
  const defaultHint = t.theme?.fontDefaultHint ?? "Use the active theme's font";
  const showDefault = !needle || `${defaultLabel} ${defaultHint}`.toLocaleLowerCase().includes(needle);
  const matches = (label: string) => !needle || label.toLocaleLowerCase().includes(needle);
  const groups = order
    .map((cat) => ({ cat, fonts: fontChoices.filter((f) => f.category === cat && matches(f.label)) }))
    .filter((group) => group.fonts.length > 0);
  if (!showDefault && groups.length === 0) return null;
  return (
    <>
      <div role="presentation" className="mt-1 border-t border-current/20 px-3 pb-1 pt-2">
        <span className="inline-flex items-center gap-1.5">
          <Type className="h-3 w-3 text-text-tertiary" />
          <Typography
            className="text-display text-xs tracking-[0.12em] text-text-tertiary"
          >
            {t.theme?.fontTitle ?? "Font"}
          </Typography>
        </span>
      </div>

      {showDefault && (
        <PickerRow
          selected={fontId === THEME_DEFAULT_FONT_ID}
          onSelect={() => setFont(THEME_DEFAULT_FONT_ID)}
        >
          <span aria-hidden className="h-4 w-9 shrink-0" />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <Typography className="break-words text-xs tracking-normal">
              {defaultLabel}
            </Typography>
            <Typography className="break-words text-xs leading-normal tracking-normal text-text-tertiary">
              {defaultHint}
            </Typography>
          </div>
          {!manga && (
            <Check
              className={cn(
                "h-3 w-3 shrink-0 text-midground",
                fontId === THEME_DEFAULT_FONT_ID ? "opacity-100" : "opacity-0",
              )}
            />
          )}
        </PickerRow>
      )}

      {groups.map(({ cat, fonts }) => {
        const catLabel = t.theme?.[FONT_CATEGORY_LABEL_KEY[cat]] ?? cat;
        return (
          <div key={cat} role="presentation">
            <div role="presentation" className="px-3 pb-0.5 pt-1.5">
              <Typography className="text-[0.65rem] uppercase tracking-[0.1em] text-text-tertiary">
                {catLabel}
              </Typography>
            </div>
            {fonts.map((f) => {
              const isActive = f.id === fontId;
              return (
                <PickerRow
                  selected={isActive}
                  key={f.id}
                  onSelect={() => setFont(f.id)}
                >
                  <span aria-hidden className="h-4 w-9 shrink-0" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span
                      className="break-words text-sm"
                    >
                      {f.label}
                    </span>
                  </div>
                  {!manga && (
                    <Check
                      className={cn(
                        "h-3 w-3 shrink-0 text-midground",
                        isActive ? "opacity-100" : "opacity-0",
                      )}
                    />
                  )}
                </PickerRow>
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
  filter: string;
}

interface FontSectionProps {
  fontChoices: FontChoice[];
  fontId: string;
  setFont: (id: string) => void;
  filter: string;
}

interface ThemeSwitcherProps {
  collapsed?: boolean;
}
