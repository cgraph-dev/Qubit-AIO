import { FxButton } from "@/sakuga/FxButton";
import { useId, useRef, useState } from "react";
import { Check, ChevronDown, Languages } from "lucide-react";
import { useI18n } from "@/i18n/context";
import { LOCALE_META, type Locale } from "@/i18n";
import { UtilityPickerPanel } from "@/manga-skin/UtilityPickerPanel";

const locales = Object.keys(LOCALE_META) as Locale[];

export function LanguageSwitcher({ collapsed = false }: { collapsed?: boolean }) {
  const { locale, setLocale, t } = useI18n();
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
        <UtilityPickerPanel id={id} title={label} closeLabel={t.common.close} anchor={anchor} onClose={close}>
          <div role="group" aria-label={label}>
            {locales.map((code) => (
              <FxButton type="button" key={code} lang={code} dir="auto"
                aria-pressed={code === locale} data-qm-picker-choice="" className="qm-picker-choice"
                onClick={() => { setLocale(code); close(); }}>
                <span>{LOCALE_META[code].name}</span>
                {code === locale && <Check size={16} aria-hidden />}
              </FxButton>
            ))}
          </div>
        </UtilityPickerPanel>
      )}
    </div>
  );
}
