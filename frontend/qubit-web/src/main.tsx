import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import "./index.css";
import App from "./App";
import { SystemActionsProvider } from "./contexts/SystemActions";
import { I18nProvider } from "./i18n";
import { exposePluginSDK } from "./plugins";
import { ThemeProvider } from "./themes";
import { QUBIT_BASE_PATH } from "./lib/api";
import { installChunkReloadRecovery } from "./lib/preload-recovery";

// A rebuild wipes replaced chunks: an already-open tab whose index references
// one gets "Failed to fetch dynamically imported module" on the next lazy
// route. Recover with a single reload to the current index instead of a dead
// page (loop-guarded inside).
installChunkReloadRecovery();

// Expose the plugin SDK before rendering so plugins loaded via <script>
// can access React, components, etc. immediately.
exposePluginSDK();

createRoot(document.getElementById("root")!).render(
  <BrowserRouter basename={QUBIT_BASE_PATH || undefined}>
    <I18nProvider>
      <ThemeProvider>
        <SystemActionsProvider>
          <App />
        </SystemActionsProvider>
      </ThemeProvider>
    </I18nProvider>
  </BrowserRouter>,
);
