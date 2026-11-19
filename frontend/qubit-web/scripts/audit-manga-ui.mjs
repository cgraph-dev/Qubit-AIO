/**
 * Read-only Manga UI browser regression audit.
 * Uses an existing local Playwright install; does not install dependencies or
 * mutate Qubit data. Provide BASE_URL and optionally ROUTES (comma-separated).
 *
 *   BASE_URL=http://localhost:5173 node scripts/audit-manga-ui.mjs
 *
 * Authenticated environments: set PLAYWRIGHT_STORAGE_STATE to your local
 * Playwright storage-state JSON path. Keep secrets out of screenshots/reports.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const base = (process.env.BASE_URL ?? "http://localhost:5173").replace(/\/$/, "");
const output = resolve(process.env.OUTPUT_DIR ?? "manga-visual-audit");
const defaults = [
 "/qubit-chat","/sessions","/models","/cron","/qubit-scheduler",
 "/qubit-tasks","/qubit-taskflows","/files","/analytics","/agents",
 "/profiles","/skills","/plugins","/mcp","/channels","/system",
 "/config","/env","/logs","/qubit-memory","/open-viking",
 "/qubit-talk","/omniroute","/worktrees","/checkpoints","/admin/users",
];
const exclude = ["/chat","/qubit-workbench","/qubit-hud","/qubit-office","/qubit-office2","/qubit-office3"];
const routes = (process.env.ROUTES ? process.env.ROUTES.split(",") : defaults).map((r) => r.trim()).filter((r) => r.startsWith("/"));
if (routes.some((r) => exclude.some((x) => r === x || r.startsWith(x + "/")))) {
 throw Error("Audit routes must exclude /chat TUI, Workbench, HUD, and Office");
}
let playwright;
try {
 playwright = await import("playwright");
} catch {
 console.error("Playwright is required for the browser audit. Install it in the local QA environment; do not modify the Qubit production dependencies.");
 process.exit(2);
}
const checks = [];
const problems = [];
await mkdir(output, { recursive:true });
const browser = await playwright.chromium.launch({ headless:process.env.HEADED !== "1" });
const widths = [390,768,1280,1600];
const storageState = process.env.PLAYWRIGHT_STORAGE_STATE || undefined;
function safeName(r) { return r.replace(/[^a-z0-9]/gi,"_").slice(0,96); }
try {
 for (const width of widths) {
  const context = await browser.newContext({viewport:{width,height:900},deviceScaleFactor:1,storageState});
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const route of routes) {
   const record = {width,route,status:"unknown",screenshot:null,scrollOverflow:false,collapsedFooterOverlap:false,errors:[]};
   try {
    const response = await page.goto(base + route, { waitUntil:"domcontentloaded",timeout:30000 });
    await page.waitForTimeout(400);
    // Enable Manga from the actual Q6 preference to exercise route gating.
    // This affects only the disposable audit browser context.
    await page.evaluate(() => {
      localStorage.setItem("qubit-dashboard-theme","q6");
      localStorage.setItem("qubit-manga-skin-enabled","true");
    });
    await page.reload({waitUntil:"domcontentloaded",timeout:30000});
    await page.waitForTimeout(450);
    record.status = String(response?.status() ?? "unknown");
    const metrics = await page.evaluate(() => {
      const root = document.documentElement;
      const main = document.querySelector("#qubit-main-content");
      const rail = document.querySelector("#app-sidebar");
      const side = document.querySelector(".qchat__side");
      const screenWidth = window.innerWidth;
      function rect(x) {if(!x) return null; const r=x.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};}
      return {
       marked:root.dataset.qubitManga,
       docWidth:root.scrollWidth,
       viewWidth:screenWidth,
       main:rect(main),rail:rect(rail),side:rect(side),
       clippedElements:[...document.querySelectorAll("input,select,textarea,button")].filter((el) => {
         const r=el.getBoundingClientRect();return r.width>0 && r.height>0 && (r.right>screenWidth+3 || r.left<-3);
       }).slice(0,15).map((el) => el.outerHTML.slice(0,140)),
      };
    });
    record.scrollOverflow = metrics.docWidth > metrics.viewWidth + 3;
    record.clippedInputs = metrics.clippedElements;
    record.marked = metrics.marked;
    if(width>=1024) {
     const ctl=page.locator('#app-sidebar button[aria-label*="Collapse"], #app-sidebar button[aria-label*="collapse"]');
     if(await ctl.count()) {await ctl.first().click().catch(()=>{});await page.waitForTimeout(300);}
     const overlap=await page.evaluate(()=>{
       const rail=document.querySelector('#app-sidebar[data-collapsed="true"]');
       if(!rail) return false;
       const blocks=['.qagent-sidebar__system','.qagent-sidebar__utility','.qagent-sidebar__colophon']
        .map(s=>rail.querySelector(s)).filter(Boolean).map(el=>el.getBoundingClientRect());
       if(blocks.some(r=>r.width>60||r.left<0))return true;
       return blocks.some((r,i)=>blocks.slice(i+1).some(s=>r.top<s.bottom-2&&s.top<r.bottom-2));
     });
     record.collapsedFooterOverlap=overlap;
     if(overlap)problems.push({width,route,kind:"collapsed-footer-overlap"});
    }
    if(record.scrollOverflow)problems.push({width,route,kind:"document-horizontal-overflow"});
    if(record.clippedInputs.length)problems.push({width,route,kind:"offscreen-control",count:record.clippedInputs.length});
    if(errors.length)problems.push({width,route,kind:"browser-errors",errors:errors.slice()});
    const file=width+"_"+safeName(route)+".png";
    await page.screenshot({path:resolve(output,file),fullPage:false,animations:"disabled"});
    record.screenshot=file;
    record.errors=errors.splice(0);
   }catch(error){
    record.status="ERROR";
    record.errors.push(String(error));
    problems.push({width,route,kind:"navigation-or-capture-error",error:String(error)});
   }
   checks.push(record);
   console.log(width,route,record.status,record.scrollOverflow?"OVERFLOW":"",record.collapsedFooterOverlap?"FOOTER OVERLAP":"");
  }
  await context.close();
 }
}finally{await browser.close();}
await writeFile(resolve(output,"audit.json"), JSON.stringify({base,checks,problems},null,2));
console.log("Manga audit:",checks.length,"captures;",problems.length,"problems; report:",resolve(output,"audit.json"));
if(problems.length)process.exitCode=1;
