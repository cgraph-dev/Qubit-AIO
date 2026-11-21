import { FxButton } from "@/sakuga/FxButton";
import { useId, useRef, useState } from "react";
import { Check, ChevronDown, Languages } from "lucide-react";
import { useI18n } from "@/i18n/context";
import { LOCALE_META, type Locale } from "@/i18n";
import { UtilityPickerPanel } from "@/manga-skin/UtilityPickerPanel";
import { PickerRow } from "@/manga-skin/PickerPanel";
import { useMangaLayerActive } from "@/manga-skin/useMangaLayerActive";

const locales = Object.keys(LOCALE_META) as Locale[];

export function LanguageSwitcher({ collapsed = false }: { collapsed?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  const manga = useMangaLayerActive();
  const [open, setOpen] = useState(false);
  const id = useId();
  const anchor = useRef<HTMLDivElement>(null);
  const close = () => setOpen(false);
  const label = t.language.switchTo;

  return (
    <div ref={anchor} className="language-switcher">
      <FxButton type="button" className="qm-utility-trigger qm-language-trigger"
        aria-label={`${label}: ${LOCALE_META[locale].name}`} title={label} aria-expanded={open} aria-haspopup="dialog"
        aria-controls={open ? id : undefined} onClick={() => setOpen(true)}>
        <Languages size={15} aria-hidden />
        {!collapsed && <><span lang={locale} dir="auto">{LOCALE_META[locale].name}</span><ChevronDown size={13} aria-hidden /></>}
      </FxButton>
      {open && (
        <UtilityPickerPanel
          id={id}
          title={label}
          closeLabel={t.common.close}
          anchor={anchor}
          onClose={close}
          filterLabel={`Filter ${label}`}
        >
          {(filter) => {
            const needle = filter.trim().toLocaleLowerCase();
            const shown = needle
              ? locales.filter((code) => `${LOCALE_META[code].name} ${code}`.toLocaleLowerCase().includes(needle))
              : locales;
            if (shown.length === 0) {
              return <div className="qm-select-empty" role="status">No matching languages</div>;
            }
            return (
              <div role="group" aria-label={label}>
                {shown.map((code) => (
                  <PickerRow
                    key={code}
                    selected={code === locale}
                    onSelect={() => { setLocale(code); close(); }}
                    lang={code}
                    dir="auto"
                  >
                    <span>{LOCALE_META[code].name}</span>
                    {!manga && code === locale && <Check size={16} aria-hidden />}
                  </PickerRow>
                ))}
              </div>
            );
          }}
        </UtilityPickerPanel>
      )}
    </div>
  );
}
