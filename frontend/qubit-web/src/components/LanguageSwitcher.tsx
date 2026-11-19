import type { CSSProperties } from "react";
import { Select } from "@/sakuga/ui";
import { useI18n } from "@/i18n/context";
import { LOCALE_META } from "@/i18n";
import type { Locale } from "@/i18n";

/**
 * Language picker — the Sakuga select composition over every supported locale.
 *
 * The previous version was a bespoke listbox with a separate mobile sheet and
 * a portal for the sidebar's drop-up case. The Sakuga `Select` already owns the
 * trigger, the filterable panel, Escape/outside-click handling and the value
 * semantics, so the language list is now one control rather than three
 * implementations. Endonyms stay the labels: languages are not countries.
 */
export function LanguageSwitcher({ collapsed = false, dropUp: _dropUp }: LanguageSwitcherProps) {
  const { locale, setLocale, t } = useI18n();
  const allLocales = Object.entries(LOCALE_META) as Array<[Locale, (typeof LOCALE_META)[Locale]]>;
  const options = allLocales.map(([code, meta]) => ({ value: code, label: meta.name }));

  return (
    <Select
      label={t.language.switchTo}
      value={locale}
      options={options}
      onChange={(next) => setLocale(next as Locale)}
      className="language-switcher qsk-select--compact"
      style={{ "--sk-input-width": collapsed ? "100%" : "10rem", width: collapsed ? "100%" : undefined, minWidth: 0, maxWidth: "100%" } as CSSProperties}
    />
  );
}

interface LanguageSwitcherProps {
  collapsed?: boolean;
  /** Kept for call-site compatibility; the Sakuga panel positions itself. */
  dropUp?: boolean;
}
