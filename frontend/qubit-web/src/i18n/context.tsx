import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Locale, Translations } from "./types";
import { en } from "./en";

// Only the default dictionary is in the initial bundle. Every other locale is
// a dynamic import, so a first load downloads ~one dictionary instead of all
// seventeen (the previous eager map was the second-largest chunk in the
// dashboard shell). The active locale is loaded before first paint in
// `main.tsx`, and a switch loads the chosen dictionary before swapping it in,
// so a user never sees a partially translated frame.
const LOCALE_LOADERS: Record<Locale, () => Promise<Translations>> = {
  en: async () => en,
  zh: () => import("./zh").then((m) => m.zh),
  "zh-hant": () => import("./zh-hant").then((m) => m.zhHant),
  ja: () => import("./ja").then((m) => m.ja),
  de: () => import("./de").then((m) => m.de),
  es: () => import("./es").then((m) => m.es),
  fr: () => import("./fr").then((m) => m.fr),
  tr: () => import("./tr").then((m) => m.tr),
  uk: () => import("./uk").then((m) => m.uk),
  af: () => import("./af").then((m) => m.af),
  ko: () => import("./ko").then((m) => m.ko),
  it: () => import("./it").then((m) => m.it),
  ga: () => import("./ga").then((m) => m.ga),
  pt: () => import("./pt").then((m) => m.pt),
  ru: () => import("./ru").then((m) => m.ru),
  hu: () => import("./hu").then((m) => m.hu),
  ar: () => import("./ar").then((m) => m.ar),
};

export function loadTranslations(locale: Locale): Promise<Translations> {
  return (LOCALE_LOADERS[locale] ?? LOCALE_LOADERS.en)();
}

// Locales whose script flows right-to-left. Consumed by the provider to set the
// document direction so Tailwind's logical utilities (ms-/me-, ps-/pe-) flip.
const RTL_LOCALES = new Set<Locale>(["ar"]);

// Display metadata for the language picker — endonym (native name) so users
// recognize their language even if they don't speak the current UI language.
// Exposed as a constant so the LanguageSwitcher and any future settings page
// can share the same list.
//
// We intentionally do NOT pair locales with country flags. Languages are not
// countries (English ≠ GB, Portuguese ≠ PT, Spanish ≠ ES, Chinese variants ≠
// any single jurisdiction). Endonyms are unambiguous and avoid the political
// mismapping that flag pairings inevitably create.
export const LOCALE_META: Record<Locale, { name: string }> = {
  en: { name: "English" },
  zh: { name: "简体中文" },
  "zh-hant": { name: "繁體中文" },
  ja: { name: "日本語" },
  de: { name: "Deutsch" },
  es: { name: "Español" },
  fr: { name: "Français" },
  tr: { name: "Türkçe" },
  uk: { name: "Українська" },
  af: { name: "Afrikaans" },
  ko: { name: "한국어" },
  it: { name: "Italiano" },
  ga: { name: "Gaeilge" },
  pt: { name: "Português" },
  ru: { name: "Русский" },
  hu: { name: "Magyar" },
  ar: { name: "العربية" },
};

const SUPPORTED_LOCALES = Object.keys(LOCALE_META) as Locale[];
const STORAGE_KEY = "qubit-locale";
/** Pre-rebrand key, migrated one-time on first read so existing users keep
 *  their saved locale. */
const LEGACY_STORAGE_KEY = "hermes-locale";

function isLocale(value: string): value is Locale {
  return (SUPPORTED_LOCALES as string[]).includes(value);
}

export function getInitialLocale(): Locale {
  try {
    let stored = localStorage.getItem(STORAGE_KEY);
    if (stored === null) {
      const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
      if (legacy !== null) {
        localStorage.setItem(STORAGE_KEY, legacy);
        localStorage.removeItem(LEGACY_STORAGE_KEY);
        stored = legacy;
      }
    }
    if (stored && isLocale(stored)) return stored;
  } catch {
    // SSR or privacy mode
  }
  return "en";
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Translations;
}

const I18nContext = createContext<I18nContextValue>({
  locale: "en",
  setLocale: () => {},
  t: en,
});

interface I18nProviderProps {
  children: ReactNode;
  /** Preloaded locale, used by `main.tsx` so the first paint is already in the
   *  saved language instead of flashing English. */
  initialLocale?: Locale;
  initialTranslations?: Translations;
}

export function I18nProvider({
  children,
  initialLocale,
  initialTranslations,
}: I18nProviderProps) {
  const [state, setState] = useState<{ locale: Locale; t: Translations }>(
    () => ({
      locale: initialLocale ?? getInitialLocale(),
      t: initialTranslations ?? en,
    }),
  );
  const stateRef = useRef(state);
  stateRef.current = state;
  const requestId = useRef(0);

  // Tests and embedders may render the provider without preloading; hydrate a
  // saved non-English locale once on mount. In the app this is a no-op because
  // main.tsx already supplied the matching dictionary.
  useEffect(() => {
    if (initialTranslations) return;
    const locale = stateRef.current.locale;
    if (locale === "en") return;
    const id = ++requestId.current;
    void loadTranslations(locale)
      .then((t) => {
        if (requestId.current === id) setState({ locale, t });
      })
      .catch(() => {
        // A failed locale chunk falls back to English rather than a blank UI.
        if (requestId.current === id) setState({ locale: "en", t: en });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setLocale = useCallback((l: Locale) => {
    if (l === stateRef.current.locale) return;
    if (l === "en") {
      requestId.current += 1;
      setState({ locale: "en", t: en });
      try {
        localStorage.setItem(STORAGE_KEY, l);
      } catch {
        // ignore
      }
      return;
    }
    const id = ++requestId.current;
    void loadTranslations(l)
      .then((t) => {
        if (requestId.current !== id) return;
        setState({ locale: l, t });
        try {
          localStorage.setItem(STORAGE_KEY, l);
        } catch {
          // ignore
        }
      })
      .catch(() => {
        // Keep the current language; do not persist a choice that cannot load.
      });
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.lang = state.locale;
    document.documentElement.dir = RTL_LOCALES.has(state.locale) ? "rtl" : "ltr";
  }, [state.locale]);

  const value: I18nContextValue = {
    locale: state.locale,
    setLocale,
    t: state.t,
  };

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
