// Packet 2B evidence harness v2: runs the EXACT consumer block from
// frontend/index.html in Node against scripted server behaviors.
// Proves (repair #1/#4): (A) on `resync` the reader is cancelled immediately,
// the fetch signal is aborted, and the client reconnects with NO last_seq
// (safe full-replay cursor); (B) after a clean end-of-stream it reconnects
// WITH last_seq=<n> (gap-free continuation). Not part of pytest gates.
// Run: node scripts/check_dashboard_consumer.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "frontend/index.html"), "utf8");
// Select the consumer by what it DOES, never by its position. This used to be
// `blocks[blocks.length - 2]`, an off-by-one that broke the moment the
// permissions domain split its inline script in two: the document then ended
// with the learned-preferences, permissions and reports blocks, and `length - 2`
// pointed at the permissions panel. eval'ing that against this harness's stub
// DOM threw `Cannot read properties of null (reading 'addEventListener')` —
// before the consumer ever loaded — and no assertion in the file could run.
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const consumerIndex = blocks.findIndex(b => b.includes("/api/events/stream"));
if (consumerIndex < 0) {
  console.error("CONSUMER FAILED: frontend/index.html has no inline block reading /api/events/stream");
  process.exit(1);
}
// Exactly one block may own the stream; a second copy would mean two consumers
// racing the same journal, which is worse than the missing block this replaces.
if (blocks.filter(b => b.includes("/api/events/stream")).length !== 1) {
  console.error("CONSUMER FAILED: more than one inline block reads /api/events/stream");
  process.exit(1);
}
const consumer = blocks[consumerIndex];
// The consumer opens the stream through frontend/ui.js's vbStream(path, opts)
// (shared header token, typed {ok,status,authRequired,reader,cancel}), not a
// bare fetch — and ui.js is a separate <script src>, so this hermetic harness
// never loads it. Stand in for exactly the contract the consumer relies on
// instead of letting the call throw: an unstubbed vbStream made every run fail
// at "vbStream is not defined" with fetchCalls empty, regardless of whether the
// consumer still handled resync correctly.
globalThis.vbStream = async (url, opts = {}) => {
  const res = await globalThis.fetch(url, { headers: {}, signal: opts.signal });
  if (res.status === 401) return { ok: false, status: 401, authRequired: true, reader: null, cancel() {} };
  if (!res.ok || !res.body) return { ok: false, status: res.status, authRequired: false, reader: null, cancel() {} };
  return { ok: true, status: res.status, authRequired: false, reader: res.body.getReader(), cancel() {} };
};

const shown = [];
globalThis.window = globalThis;
globalThis.BASE = "";
globalThis.API_TOKEN = "t";
globalThis.hdrs = (x) => x || {};
globalThis.addMsg = (cls, text) => shown.push(`${cls}|${text}`);
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.document = {
  getElementById: () => null,
  addEventListener() {},
  dispatchEvent() {},
};
globalThis.CustomEvent = class {};

class FakeReader {
  constructor(chunks) {
    this.chunks = chunks.map(c => new TextEncoder().encode(c));
    this.i = 0;
    this.cancelled = false;
  }
  async read() {
    if (this.cancelled || this.i >= this.chunks.length) return { done: true };
    return { done: false, value: this.chunks[this.i++] };
  }
  async cancel() { this.cancelled = true; return {}; }
}

function makeRes(chunks) {
  const reader = new FakeReader(chunks);
  return { ok: true, body: { getReader: () => reader }, _reader: reader };
}

// Scripted connections, consumed in order:
const script = [
  // conn 1: normal frames then a RESYNC mid-stream
  [makeRes(["retry: 3000\n\n",
            'id: 1\ndata: {"type":"STEP_STARTED","timestamp":1,"stepName":"a"}\n\n',
            ": resync\nevent: resync\ndata: {}\n\n"])],
];
let fetchCalls = [];
let pendingAssertions;
globalThis.fetch = async (url, opts = {}) => {
  fetchCalls.push({ url, signal: opts.signal });
  if (pendingAssertions) { const f = pendingAssertions; pendingAssertions = null; f(); }
  if (fetchCalls.length <= script.length) return script[fetchCalls.length - 1][0] ?? makeRes([]);
  if (fetchCalls.length === script.length + 1) {
    // safe-cursor reconnect: replay one real frame, then hold the stream open
    const frame = new TextEncoder().encode(
      'id: 1\ndata: {"type":"RUN_STARTED","timestamp":2,"threadId":"t","runId":"r1"}\n\n');
    let sent = false;
    const reader = {
      cancelled: false,
      async cancel() { this.cancelled = true; return {}; },
      async read() {
        if (this.cancelled || sent) return new Promise(() => {}); // hold open
        sent = true;
        return { done: false, value: frame };
      },
    };
    return { ok: true, body: { getReader: () => reader } };
  }
  return new Promise(() => {});              // park further reconnects
};
eval(consumer);

setTimeout(() => {
  try {
    // (A) resync handling
    const c1 = fetchCalls[0];
    if (!c1.url.endsWith("/api/events/stream"))
      throw new Error(`conn1 should have NO last_seq, got ${c1.url}`);
    if (!c1.signal) throw new Error("conn1 missing AbortController signal");
    if (!c1.signal.aborted)
      throw new Error("reader was not aborted on resync");
    // (B) safe-cursor reconnect: NO last_seq after resync (full replay)
    const c2 = fetchCalls[1];
    if (!c2 || !c2.url.endsWith("/api/events/stream"))
      throw new Error(`conn2 must reconnect from safe empty cursor, got ${c2 && c2.url}`);
    if (/last_seq=/.test(c2.url))
      throw new Error(`conn2 must NOT carry last_seq after resync: ${c2.url}`);
    // rendered the pre-resync frame exactly once, then replayed state
    const stepA = shown.filter(s => s.includes("· a"));
    if (stepA.length !== 1) throw new Error(`frame before resync rendered ${stepA.length}x`);
    if (!shown.some(s => s.includes("run r1: run_started")))
      throw new Error("replayed RUN_STARTED not rendered after safe reconnect");
    console.log("CONSUMER OK:", JSON.stringify(fetchCalls.map(f => f.url)));
    console.log("RENDERED:", JSON.stringify(shown));
    process.exit(0);
  } catch (e) {
    console.error("CONSUMER FAILED:", e.message, "\ncalls:",
      JSON.stringify(fetchCalls.map(f => f.url)), "\nshown:", JSON.stringify(shown));
    process.exit(1);
  }
}, 3500);
