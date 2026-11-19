import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import "./index.css";
import "./sakuga/sakuga.css";
import App from "./App";
import { MangaSkinGate } from "./manga-skin/MangaSkin";
import { bootstrapMangaSkin } from "./manga-skin/manga-store";
import { SystemActionsProvider } from "./contexts/SystemActions";
import {
  I18nProvider,
  getInitialLocale,
  loadTranslations,
} from "./i18n";
import { en } from "./i18n/en";
import { exposePluginSDK } from "./plugins";
import { installFxBridge, loadSakugaCore } from "@qubit/shared/sakuga";
import { ThemeProvider } from "./themes";
import { QUBIT_BASE_PATH } from "./lib/api";
import { installChunkReloadRecovery } from "./lib/preload-recovery";
import { installBuildWatch, showBuildNotice } from "./lib/build-watch";

// A rebuild wipes replaced chunks: an already-open tab whose index references
// one gets "Failed to fetch dynamically imported module" on the next lazy
// route. Recover with a single reload to the current index instead of a dead
// page (loop-guarded inside).
installChunkReloadRecovery();

// Builds keep old chunks on disk, so an open tab keeps running old code until
// it reloads — which is how a shipped fix can look like it "disappeared". Tell
// the operator when the served entry chunk differs from the loaded one.
installBuildWatch({ onNewBuild: (bundle) => showBuildNotice(bundle) });

// Expose the plugin SDK before rendering so plugins loaded via <script>
// can access React, components, etc. immediately.
exposePluginSDK();

// Sakuga VFX: install the window-level effect bridge and warm the shared
// canvas engine after first paint. Components load lazily, so this is ~40 KB
// and never the 506 KB toggle. Presentation failures are swallowed by the bus.
installFxBridge();
const warmSakuga = () => { void loadSakugaCore().catch(() => {}); };
if (typeof window !== "undefined" && "requestIdleCallback" in window) {
  (window as Window & { requestIdleCallback: (cb: () => void, opts?: { timeout: number }) => void })
    .requestIdleCallback(warmSakuga, { timeout: 2500 });
} else {
  setTimeout(warmSakuga, 1200);
}

// Apply the persisted manga preference before first paint so the skin never
// flashes for users who left it on. No-op when the preference is off or the
// first route is an excluded surface.
bootstrapMangaSkin();

async function mount(): Promise<void> {
  // Load the saved dictionary before the first paint. English (the default)
  // is already in this chunk, so the common path resolves without a network
  // request; a non-English locale fetches only its own dictionary chunk.
  let initialLocale = getInitialLocale();
  let initialTranslations = en;
  try {
    initialTranslations = await loadTranslations(initialLocale);
  } catch {
    // A missing/broken locale chunk must not block the dashboard: start in
    // English with the document language matching what is rendered.
    initialLocale = "en";
    initialTranslations = en;
  }
  createRoot(document.getElementById("root")!).render(
    <BrowserRouter basename={QUBIT_BASE_PATH || undefined}>
      <I18nProvider
        initialLocale={initialLocale}
        initialTranslations={initialTranslations}
      >
        <ThemeProvider>
          <SystemActionsProvider>
            <MangaSkinGate />
            <App />
          </SystemActionsProvider>
        </ThemeProvider>
      </I18nProvider>
    </BrowserRouter>,
  );
}

void mount();
