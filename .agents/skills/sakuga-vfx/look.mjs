#!/usr/bin/env node
// look.mjs: see a reaction the way a reviewer should, as frames you can read one at a time.
//
//   node look.mjs "http://localhost:8000/checkbox/" --select "sakuga-checkbox" --act "0:document.querySelector('sakuga-checkbox').shadowRoot.querySelector('input').click()" --times 0,0.1,0.2,0.35,0.6,0.9
//
// Opens the page in headless Chrome, stops the Sakuga clock (SakugaFX.paused), steps it to each time with SakugaFX.advance, and photographs
// the element with room around it (the effects fly outside it). Saves one sheet with every frame side by side. Then open the sheet and look.
// Node 22 or later and Chrome. No installs. Options:
//   --times 0,0.1,0.3   moments to capture, in seconds from the start (after --do)
//   --select css        what to frame. "host >>> inner" reaches into a shadow root. Default: the first sakuga-* element
//   --pad 140           pixels of room around it
//   --do "js"           run once after the page loads, before the first frame
//   --act "0.2:js"      run js when the clock reaches 0.2 s, so one sheet can show a press and its release. Repeat it for more.
//   --zoom, --cols, --width, --height, and --out for the sheet's file name. Set CHROME if Chrome is somewhere unusual.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2), url = args.find((a) => !a.startsWith('--') && !args[args.indexOf(a) - 1]?.startsWith('--'));
const opt = (name, fallback) => { const i = args.indexOf('--' + name); return i >= 0 ? args[i + 1] : fallback; };
if (!url) { console.error('usage: node look.mjs URL --times 0,0.1,0.3 [--select css] [--act "0:js"] [--out sheet.png]'); process.exit(2); }
const acts = args.flatMap((a, i) => (a === '--act' ? [args[i + 1]] : [])).map((v) => [parseFloat(v), v.slice(v.indexOf(':') + 1)]).sort((a, b) => a[0] - b[0]);
const times = opt('times', '0,0.1,0.2,0.4').split(',').map(Number), out = opt('out', 'sheet.png'), pad = +opt('pad', 140), zoom = +opt('zoom', 1);
const cols = +opt('cols', Math.min(4, times.length)), width = +opt('width', 900), height = +opt('height', 800), after = opt('do', ''), select = opt('select', '');
const CHROME = process.env.CHROME || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(existsSync);
if (!CHROME) { console.error('Chrome not found. Set CHROME to its path.'); process.exit(2); }

const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'look-'))}`, '--no-first-run', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let targets; for (let i = 0; i < 60 && !targets?.some?.((t) => t.type === 'page'); i++) { try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch {} await sleep(200); }
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl); await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map(), errors = [];
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); });
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
const run = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.value;

await send('Runtime.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url }); await sleep(1500);
if (!(await run('!!window.SakugaFX'))) { console.error('No SakugaFX on that page.'); process.exit(1); }
const pick = select ? JSON.stringify(select) : 'null';
const box = JSON.parse(await run(`(() => { const s = ${pick}; const el = s ? s.split(' >>> ').reduce((r, q, i) => (i ? r.shadowRoot : r).querySelector(q), document) : [...document.querySelectorAll('*')].find((e) => e.localName.startsWith('sakuga-'));
  if (!el) throw new Error('nothing to frame'); el.scrollIntoView({ block: 'center' }); const b = el.getBoundingClientRect(); return JSON.stringify({ x: b.left, y: b.top, w: b.width, h: b.height }); })()`));
const clip = { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad), width: box.w + pad * 2, height: box.h + pad * 2, scale: 1 };
await run('SakugaFX.paused = true');
if (after) await run(after);
let clock = 0; const shots = [];
const go = async (t) => { await run(`(() => { let n = Math.round(${(t - clock) * 60}); while (n-- > 0) SakugaFX.advance(1 / 60); })()`); clock = t; };
for (const t of times) {
  while (acts.length && acts[0][0] <= t) { const [at, js] = acts.shift(); await go(at); await run(js); }
  await go(t); await sleep(60);
  shots.push([t, (await send('Page.captureScreenshot', { format: 'png', clip })).data]);
}
// the frames go onto one sheet inside the page, so nothing but Chrome is needed to put them side by side
const data = await run(`(async () => { const s = ${JSON.stringify(shots)}; const imgs = await Promise.all(s.map(([, d]) => new Promise((r) => { const i = new Image(); i.onload = () => r(i); i.src = 'data:image/png;base64,' + d; })));
  const w = imgs[0].width * ${zoom}, h = imgs[0].height * ${zoom}, cols = ${cols}, rows = Math.ceil(imgs.length / cols), o = document.createElement('canvas'); o.width = (w + 8) * cols + 8; o.height = (h + 26) * rows + 8;
  const x = o.getContext('2d'); x.fillStyle = '#2b2b30'; x.fillRect(0, 0, o.width, o.height); x.font = '13px monospace';
  imgs.forEach((im, i) => { const px = 8 + (i % cols) * (w + 8), py = 8 + Math.floor(i / cols) * (h + 26); x.drawImage(im, px, py + 18, w, h); x.fillStyle = '#d0d0d8'; x.fillText(s[i][0].toFixed(2) + ' s', px, py + 12); });
  return o.toDataURL('image/png'); })()`);
writeFileSync(out, Buffer.from(data.split(',')[1], 'base64'));
console.log(`${times.length} frames, saved ${out}`);
console.log(errors.length ? 'script errors:\n  ' + errors.join('\n  ') : 'no script errors');
ws.close(); chrome.kill(); process.exit(errors.length ? 1 : 0);
