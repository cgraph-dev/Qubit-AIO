/**
 * Read-only browser regression audit for the dashboard shell. Run against a
 * dev server or a local dashboard. The default routes come from App's route
 * table, so new built-in pages join the audit without a second list.
 *
 * BASE_URL=http://localhost:5173 node scripts/audit-manga-ui.mjs
 * Optional: ROUTES, WIDTHS (comma-separated), MANGA_THEME=q6-l,
 * PLAYWRIGHT_STORAGE_STATE, OUTPUT_DIR, AUDIT_SCREENSHOTS=1.
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import ts from "typescript";

async function builtinRoutes() {
  const source = ts.createSourceFile("App.tsx",
    await readFile(new URL("../src/App.tsx", import.meta.url), "utf8"),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  for (const statement of source.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (declaration.name.getText(source) !== "BUILTIN_ROUTES_CORE") continue;
      const object = declaration.initializer;
      if (!object || !ts.isObjectLiteralExpression(object)) break;
      return [...object.properties.flatMap(({ name }) => name && ts.isStringLiteral(name) ? [name.text] : [])
        .filter((path) => path !== "/" && path !== "/qubit-workbench"), "/chat", "/qubit-chat"];
    }
  }
  throw new Error("App route table was not found");
}

const base = (process.env.BASE_URL ?? "http://localhost:5173").replace(/\/$/, "");
const output = resolve(process.env.OUTPUT_DIR ?? "manga-visual-audit");
const theme = process.env.MANGA_THEME ?? "q6";
if (theme !== "q6" && theme !== "q6-l") throw new Error("MANGA_THEME must be q6 or q6-l");
const widths = (process.env.WIDTHS ?? "390,768,1024,1440").split(",").map(Number);
if (widths.some((width) => !Number.isSafeInteger(width) || width <= 0)) {
  throw new Error("WIDTHS must contain positive integers");
}
const routes = [...new Set((process.env.ROUTES?.split(",") ?? await builtinRoutes()).map((r) => r.trim()))];
if (routes.some((route) => !/^\/(?!\/)[\w\/-]*$/.test(route))) {
  throw new Error("ROUTES must contain dashboard paths without query strings");
}
const mangaExcluded = ["/qubit-workbench", "/workbench", "/qubit-hud", "/qubit-office", "/qubit-office2", "/qubit-office3"];
// Use screenshots only against a fixture dashboard. These routes never save
// captures, even when requested. Reports contain no DOM text or request URLs.
const sensitive = ["/env", "/config", "/admin", "/profiles", "/sessions", "/chat", "/qubit-chat", "/webchat", "/qubit-bot"];
const onPath = (paths, route) => paths.some((p) => route === p || route.startsWith(p + "/"));

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error("Install Playwright in the local QA environment before running the audit.");
  process.exit(2);
}

const checks = [];
const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
});
await mkdir(output, { recursive: true });
try {
  for (const width of widths) {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      reducedMotion: "reduce",
      storageState: process.env.PLAYWRIGHT_STORAGE_STATE || undefined,
    });
    await context.addInitScript((name) => {
      if (window === window.top) localStorage.setItem("qubit-dashboard-theme", name);
    }, theme);
    const page = await context.newPage();
    let errors = 0;
    let checkedRail = false;
    page.on("pageerror", () => { errors++; });
    for (const route of routes) {
      errors = 0;
      const record = { width, route, problems: [] };
      try {
        const response = await page.goto(base + route, { waitUntil: "domcontentloaded", timeout: 15000 });
        if (!response?.ok()) record.problems.push("http-error");
        const main = page.locator("#qubit-main-content");
        await main.waitFor({ state: "attached", timeout: 6000 });
        const loading = page.locator("#qubit-main-content .qsk-route-loading");
        await loading.waitFor({ state: "hidden", timeout: 6000 });
        if (new URL(page.url()).pathname.replace(/\/$/, "") !== route.replace(/\/$/, "")) {
          record.problems.push("unexpected-route");
        }
        const metrics = await page.evaluate(() => {
          const main = document.getElementById("qubit-main-content");
          const header = document.querySelector("[data-dashboard-page-header]");
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && !el.closest("[inert], [aria-hidden='true']") &&
              getComputedStyle(el).visibility === "visible";
          };
          const hasHorizontalScrollParent = (el) => {
            for (let parent = el.parentElement; parent && parent !== main; parent = parent.parentElement) {
              if (["auto", "scroll"].includes(getComputedStyle(parent).overflowX)) return true;
            }
            return false;
          };
          const controls = [...main.querySelectorAll("input,select,textarea,button,sakuga-button,[role='tab']")]
            .filter((el) => visible(el) && !el.closest(".xterm"));
          const clip = controls.filter((el) => {
            const { left, right } = el.getBoundingClientRect();
            return (left < -2 || right > innerWidth + 2) && !hasHorizontalScrollParent(el);
          });
          const headerBounds = header?.getBoundingClientRect();
          const headerClipped = headerBounds && [...header.querySelectorAll("h1,button,sakuga-button,[role='tab']")]
            .filter(visible).some((el) => {
              const r = el.getBoundingClientRect();
              return r.top < headerBounds.top - 2 || r.bottom > headerBounds.bottom + 2;
            });
          const copy = [...main.querySelectorAll(".settings-row__desc,.settings-section__desc,.settings-page__desc")]
            .some((el) => visible(el) && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 2);
          const idCounts = new Set();
          const duplicateId = [...main.querySelectorAll("[id]")].filter(visible).some((el) => {
            if (idCounts.has(el.id)) return true;
            idCounts.add(el.id);
            return false;
          });
          const input = main.querySelector("#bot-composer");
          const composer = input?.closest(".qb-composer");
          const inputBox = input?.getBoundingClientRect();
          const dockBox = composer?.getBoundingClientRect();
          const composerSpills = inputBox && dockBox &&
            (inputBox.left < dockBox.left || inputBox.right > dockBox.right ||
             inputBox.top < dockBox.top || inputBox.bottom > dockBox.bottom ||
             parseFloat(getComputedStyle(input).borderTopWidth) > 0);
          const contentFrameArt = [...main.querySelectorAll('.settings-section__header,[data-qm-zip-asset="model-card"]')]
            .some((el) => getComputedStyle(el).backgroundImage.includes("url(") ||
              (getComputedStyle(el, "::before").content !== "none" &&
               getComputedStyle(el, "::before").backgroundImage.includes("url(")));
          return {
            manga: document.documentElement.dataset.qubitManga === "on",
            problems: [
              document.documentElement.scrollWidth > innerWidth + 2 && "document-overflow",
              clip.length > 0 && "offscreen-control",
              headerClipped && "header-clipped",
              copy && "settings-copy-overflow",
              duplicateId && "duplicate-visible-id",
              composerSpills && "composer-outside-dock-or-framed",
              contentFrameArt && "duplicated-content-frame",
              main.querySelector("[data-testid='page-error-boundary']") && "page-error-boundary",
            ].filter(Boolean).map(String),
          };
        });
        if (metrics.manga === onPath(mangaExcluded, route)) record.problems.push("wrong-theme-gate");
        if (errors) record.problems.push("browser-error");
        record.problems.push(...metrics.problems);
        if (width >= 1024 && metrics.manga && !checkedRail) {
          const rail = page.locator("#app-sidebar");
          const toggle = rail.locator("[data-sidebar-toggle]");
          if (await toggle.isVisible() && (await rail.getAttribute("data-collapsed")) !== "true") {
            checkedRail = true;
            await toggle.click({ timeout: 3000 });
            await page.locator('#app-sidebar[data-collapsed="true"]').waitFor({ timeout: 3000 });
            const overlap = await rail.evaluate((sidebar) => {
              const blocks = [...sidebar.querySelectorAll(".qagent-sidebar__system,.qagent-sidebar__utility,.qagent-sidebar__colophon")]
                .map((el) => el.getBoundingClientRect()).filter((r) => r.width && r.height);
              return blocks.some((a, i) => blocks.slice(i + 1).some((b) =>
                a.top < b.bottom - 2 && b.top < a.bottom - 2));
            });
            if (overlap) record.problems.push("collapsed-footer-overlap");
            await toggle.click({ timeout: 3000 });
          }
        }
        if (process.env.AUDIT_SCREENSHOTS === "1" && !onPath(sensitive, route)) {
          const file = `${width}_${route.replace(/[^\w-]/g, "_")}.png`;
          await page.screenshot({ path: resolve(output, file), animations: "disabled" });
          record.screenshot = file;
        }
      } catch (error) {
        record.problems.push(error.name === "TimeoutError" ? "page-did-not-settle" : "audit-error");
      }
      checks.push(record);
      console.log(width, route, record.problems.join(", ") || "ok");
    }
    await context.close();
  }
} finally {
  await browser.close();
}
const problemCount = checks.reduce((sum, check) => sum + check.problems.length, 0);
await writeFile(resolve(output, "audit.json"), JSON.stringify({ theme, checks, problemCount }, null, 2));
console.log(`${checks.length} checks, ${problemCount} findings`);
if (problemCount) process.exitCode = 1;
