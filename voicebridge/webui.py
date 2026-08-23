"""Voice Dashboard — phone web UI for the OpenClaw voice bridge.

Talk from any browser on the LAN (PC or phone), watch live activity, and
get spoken replies that are ALWAYS the local model's short summary — never
the raw tool output.

Endpoints
---------
GET  /                chat page (PTT button, mic capture, transcript)
GET  /activity        dashboard page (live activity feed)
POST /api/talk        raw Int16 PCM @16k mono body -> JSON:
                          {text, route, summary, events[], audio_b64}
                      audio_b64 = WAV (piper TTS) of the full spoken reply
GET  /api/activity    recent activity feed (JSON)
GET  /api/status      bridge health (JSON)

Browser audio path: MediaRecorder-free — AudioContext(16000) +
ScriptProcessorNode yields raw Int16 PCM, POSTed as binary. No codecs,
no ffmpeg, no new server deps (stdlib http.server only).

The server runs INSIDE the bridge process (a daemon thread started by
Orchestrator.start()) so it shares templates, classifier, TTS and task
state; it uses its OWN ASR instance so the mic loop and the phone never
contend on the recognizer.

Only stdlib + the bridge's own packages.
"""
from __future__ import annotations

import base64
import json
import struct
import threading
import time
import uuid
import wave
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from .logging_setup import log_event, get_logger

log = get_logger()

PCM_RATE = 16000
PCM_WIDTH = 2  # Int16

# --------------------------------------------------------------------------
# Activity feed (ring buffer) + persistence through events.jsonl
# --------------------------------------------------------------------------

class ActivityFeed:
    """Recent activity items shared with the dashboard (in-memory ring)."""

    def __init__(self, maxlen: int = 200) -> None:
        self._items: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, item: Dict[str, Any]) -> None:
        item.setdefault("ts", time.time())
        item.setdefault("id", uuid.uuid4().hex[:12])
        with self._lock:
            self._items.appendleft(item)

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)[:limit]

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for it in self._items:
                if it.get("id") == item_id:
                    return dict(it)
        return None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def load_history(self, events_path: Path, limit: int = 200) -> None:
        """Seed the feed from the persisted event journal (events.jsonl) so
        the dashboard reflects EVERYTHING the bridge did — mic loop, duplex
        taps, typed tasks, web — including activity from before this process
        started (the ring buffer alone only covers this process's lifetime)."""
        try:
            with events_path.open("r", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return
        for line in lines[-4000:]:  # bound the scan; ring keeps newest `limit`
            try:
                ev = json.loads(line)
            except Exception:
                continue
            item = _event_to_feed_item(ev)
            if item is None:
                continue
            item.setdefault("ts", ev.get("ts") or time.time())
            with self._lock:
                self._items.appendleft(item)  # newest stays at index 0


# Event journal -> activity feed item. Return None for noise (personaplex
# lane text, hotkeys, TTS, ASR internals, memory notes...).
def _event_to_feed_item(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    evt = ev.get("event", "")
    if evt == "routing.input":
        return {"kind": "user", "text": ev.get("text", ""),
                "route": ev.get("source", "mic"),
                "conf": ev.get("confidence")}
    if evt == "routing.decision":
        return {"kind": "routing", "text": ev.get("text", ""),
                "route": ev.get("kind", ""),
                "conf": ev.get("confidence"),
                "detail": ev.get("reason", "")}
    if evt == "routing.template":
        return {"kind": "routing", "text": ev.get("text", ""),
                "route": f"template:{ev.get('name', '')}",
                "detail": ev.get("kind", "")}
    if evt == "webui.utterance":
        return {"kind": "user", "text": ev.get("text", ""), "route": "web"}
    if evt == "local_script.summary":
        return {"kind": "summary", "text": ev.get("name", "local script"),
                "summary": ev.get("summary", ""), "route": "local-script",
                "detail": f"{ev.get('raw_len', '')} chars raw"}
    if evt == "openclaw.dispatch":
        return {"kind": "status", "text": "dispatched to OpenClaw",
                "route": "openclaw",
                "detail": str(ev.get("task", ""))[:300]}
    if evt == "openclaw.result":
        return {"kind": "status",
                "text": f"task {ev.get('status', 'done')}",
                "route": "openclaw",
                "detail": f"elapsed {ev.get('elapsed_s', '')}s · {ev.get('reply_len', '')} chars"}
    if evt == "openclaw.summary":
        return {"kind": "summary", "text": "OpenClaw task",
                "summary": ev.get("summary", ""), "route": "openclaw"}
    if evt == "openclaw.request.failed" or evt == "openclaw.request.exception":
        return {"kind": "fail", "text": "OpenClaw request",
                "route": "openclaw",
                "detail": str(ev.get("error", ev.get("reason", "")))[:300]}
    if evt == "safety.verdict":
        return {"kind": "routing", "text": str(ev.get("text", ""))[:160],
                "route": "safety",
                "detail": "allowed" if ev.get("allowed") else "denied"}
    if evt == "webui.talk.error" or evt == "webui.type.error":
        return {"kind": "fail", "text": evt.replace(".", " "),
                "route": "webui", "detail": str(ev.get("error", ""))[:300]}
    if evt == "conversation.local.summary":
        # new: text=what user SAID (transcription), summary=bot reply — lets Tricky
        # verify ASR: the activity card shows "You: How are you?" alongside the reply
        return {"kind": "summary", "text": ev.get("text", ""),
                "summary": ev.get("summary", ""), "route": "local",
                "detail": "conversation"}
    if evt == "conversation.fallback.summary":
        return {"kind": "summary", "text": ev.get("text", ""),
                "summary": ev.get("summary", ""), "route": "fallback",
                "detail": "conversation"}
    if evt == "conversation.local":
        # legacy journal items (before text field was added): show the reply chars
        # these will appear as e.g. "conversation · 21 chars" so old history still renders
        return {"kind": "summary", "text": ev.get("text", "") or "conversation",
                "summary": f"({ev.get('chars','?')} chars reply)", "route": "local",
                "detail": f"{ev.get('model','local')} · {ev.get('elapsed_s','?')}s"}
    if evt == "local.action.summary":
        return {"kind": "summary", "text": f"local:{ev.get('action','')}",
                "summary": ev.get("summary",""), "route": "local",
                "detail": str(ev.get("text",""))[:160]}
    if evt == "hermes.summary":
        return {"kind": "summary", "text": "Hermes task",
                "summary": ev.get("summary",""), "route": "hermes"}
    if evt == "hermes.task":
        return {"kind": "status", "text": "Hermes task dispatched",
                "route": "hermes", "detail": str(ev.get("task",""))[:300]}
    if evt == "system.command":
        cmd = ev.get("command","") or "system"
        return {"kind": "status", "text": f"system:{cmd}",
                "route": "system", "detail": cmd}
    if evt == "hermes.result.discarded":
        return {"kind": "status", "text": "Hermes task interrupted",
                "route": "hermes", "detail": ev.get("why","")}
    if evt == "openclaw.error":
        return {"kind": "fail", "text": "OpenClaw error",
                "route": "openclaw", "detail": str(ev.get("error",""))[:300]}
    if evt == "openclaw.status":
        return {"kind": "status", "text": "OpenClaw running",
                "route": "openclaw", "detail": f"elapsed {ev.get('elapsed_s','')}s"}
    if evt == "openclaw.progress":
        return {"kind": "status", "text": "OpenClaw progress",
                "route": "openclaw", "detail": f"elapsed {ev.get('elapsed_s','')}s"}
    if evt == "hermes.error":
        return {"kind": "fail", "text": "Hermes error",
                "route": "hermes", "detail": str(ev.get("error",""))[:300]}
    if evt == "boss_talk":
        return {"kind": "status", "text": ev.get("phrase","boss talk"),
                "route": ev.get("target",""), "detail": str(ev.get("task",""))[:160]}
    if evt == "humming":
        return {"kind": "status", "text": ev.get("phrase","humming"),
                "route": ev.get("target","humming"), "detail": f"elapsed {ev.get('elapsed_s','')}s"}
    return None


# --------------------------------------------------------------------------
# PCM helpers
# --------------------------------------------------------------------------

def pcm_to_wav(pcm: bytes, rate: int = PCM_RATE) -> bytes:
    """Wrap raw Int16 mono PCM in a WAV container (16-bit PCM)."""
    buf = bytearray()
    def _w(s: bytes) -> None:
        buf.extend(s)
    _w(b"RIFF")
    _w(struct.pack("<I", 36 + len(pcm)))
    _w(b"WAVE")
    _w(b"fmt ")
    _w(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * PCM_WIDTH, PCM_WIDTH, 16))
    _w(b"data")
    _w(struct.pack("<I", len(pcm)))
    _w(pcm)
    return bytes(buf)


# --------------------------------------------------------------------------
# The dashboard pages (static, self-contained)
# --------------------------------------------------------------------------

CHAT_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font: 16px/1.5 system-ui, sans-serif;
         background:#0d1117; color:#e6edf3; min-height:100vh; }
  .wrap { max-width:720px; margin:0 auto; padding:16px; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           gap:8px; margin-bottom:8px; }
  h1 { font-size:20px; margin:0; }
  .status { font-size:13px; color:#8b949e; }
  .status.live { color:#3fb950; }
  #pulse { width:10px; height:10px; border-radius:50%; background:#f85149;
           display:inline-block; vertical-align:middle; margin-right:6px; }
  #pulse.on { background:#3fb950; animation: blink 1s infinite; }
  @keyframes blink { 50% { opacity:.35; } }
  .ptt { width:100%; padding:36px 0; margin:24px 0; font-size:20px;
         border:2px solid #30363d; border-radius:16px; background:#161b22;
         color:#e6edf3; cursor:pointer; user-select:none; -webkit-user-select:none;
         touch-action:none; transition: background .08s; }
  .ptt:active, .ptt.hold { background:#1f6feb; border-color:#1f6feb; color:#fff; }
  .ptt[disabled] { opacity:.4; cursor:wait; }
  .hint { text-align:center; color:#8b949e; font-size:13px; margin-top:-16px; }
  #transcript { margin-top:20px; }
  .msg { padding:10px 12px; border-radius:10px; margin:8px 0; white-space:pre-wrap; }
  .msg.user { background:#1f6feb22; border:1px solid #1f6feb44; }
  .msg.assistant { background:#161b22; border:1px solid #30363d; }
  .msg.meta { background:#0d1117; color:#8b949e; font-size:13px; border:none; }
  .err { color:#f85149; }
  a { color:#58a6ff; text-decoration:none; }
  .row { display:flex; gap:8px; align-items:center; }
</style></head><body>
<div class="wrap">
  <header>
    <h1>🎙 Voice Dashboard</h1>
    <div class="row">
      <span class="status" id="st">connecting…</span>
      <a href="/activity">activity →</a>
    </div>
  </header>

  <button class="ptt" id="ptt">Hold to talk</button>
  <div class="hint">tap = toggle · release = send · esc = stop</div>

  <div id="transcript"></div>
</div>

<script>
const ptt = document.getElementById('ptt');
const st = document.getElementById('st');
const transcript = document.getElementById('transcript');
let ctx=null, source=null, processor=null, chunks=[], recording=false, busy=false;
let pushMode = 'hold';  // 'hold' = press-and-hold, 'tap' = one-tap toggle

function addMsg(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  transcript.prepend(d);
}
function setStatus(t, live=false) {
  st.textContent = t;
  st.className = 'status' + (live ? ' live' : '');
}
function setPulse(on) {
  document.getElementById('pulse').style.display = 'none'; // unused fallback
}

async function startRec() {
  if (recording) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true }});
    // Ask for 16k — Chrome honours AudioContext({sampleRate:16000})
    ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    source = ctx.createMediaStreamSource(stream);
    processor = ctx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (e) => {
      if (!recording) return;
      const ch = e.inputBuffer.getChannelData(0);
      const buf = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) buf[i] = Math.max(-1, Math.min(1, ch[i])) * 32767;
      chunks.push(buf);
    };
    source.connect(processor);
    processor.connect(ctx.destination);
    recording = true;
    setStatus('listening…', true);
  } catch (err) {
    addMsg('err', 'Mic error: ' + err.message);
    setStatus('mic error');
  }
}

async function stopRec() {
  if (!recording) return;
  recording = false;
  const total = chunks.reduce((a, c) => a + c.length, 0);
  const out = new Int16Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  chunks = [];
  try { source.disconnect(); processor.disconnect(); ctx.close(); } catch (_) {}
  source = processor = ctx = null;
  setStatus('thinking…');
  if (total < 1600) {  // <0.1s of audio = accidental tap
    setStatus('too short — try again');
    setTimeout(() => setStatus('ready'), 1500);
    return;
  }
  await sendAudio(out.buffer);
}

async function sendAudio(pcm) {
  busy = true;
  ptt.disabled = true;
  ptt.textContent = 'listening to result…';
  let rid;
  try {
    // 1. submit -> instant request_id (the bridge acks immediately)
    const res = await fetch('/api/talk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: pcm,
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    rid = data.request_id;
    setStatus('working…', true);
    // 2. poll the result until done
    while (true) {
      await new Promise(r => setTimeout(r, 900));
      const r = await fetch('/api/result/' + rid);
      const poll = await r.json();
      if (poll.status === 'done') {
        await playResult(poll);
        break;
      } else if (poll.status === 'unknown') {
        addMsg('err', 'Request lost (bridge restarted?). Try again.');
        break;
      }
    }
  } catch (err) {
    addMsg('err', 'Request failed: ' + err.message);
    setStatus('error');
  } finally {
    busy = false;
    ptt.disabled = false;
    ptt.textContent = 'Hold to talk';
    setStatus('ready');
  }
}

async function playResult(data) {
  addMsg('user', data.text || '(no speech recognized)');
  if (data.events && data.events.length) {
    for (const ev of data.events) {
      if (ev.kind === 'meta') addMsg('meta', '· ' + ev.text);
    }
  }
  if (data.audio_b64) {
    const audio = new Audio('data:audio/wav;base64,' + data.audio_b64);
    addMsg('assistant', data.summary || data.text || '');
    setStatus('speaking…', true);
    await audio.play();
    await new Promise(r => audio.onended = r);
  } else if (data.summary) {
    addMsg('assistant', data.summary);
  } else if (data.error) {
    addMsg('err', data.error);
  }
}

// ---- PTT: hold on desktop / toggle on touch ----
let holdTimer = null;
ptt.addEventListener('pointerdown', (e) => {
  e.preventDefault();
  if (busy) return;
  if (pushMode === 'hold') {
    holdTimer = setTimeout(() => { pushMode = 'toggle'; startRec(); }, 120);
  } else {
    startRec();
  }
});
ptt.addEventListener('pointerup', (e) => {
  e.preventDefault();
  if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
  if (pushMode === 'hold' && !recording) { pushMode = 'toggle'; }
  if (recording) stopRec();
});
window.addEventListener('keydown', (e) => {
  if (e.code === 'Escape') { if (recording) stopRec(); }
});

setStatus('ready');
fetch('/api/status').then(r => r.json()).then(d => {
  if (d.bridge === 'up') setStatus('bridge up · ' + d.route, true);
  else setStatus('bridge: ' + (d.bridge || 'unknown'));
}).catch(() => setStatus('bridge offline'));
</script>
</body></html>
"""

ACTIVITY_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Dashboard — Activity</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font: 14px/1.5 system-ui, sans-serif;
         background:#0d1117; color:#e6edf3; min-height:100vh; }
  .wrap { max-width:820px; margin:0 auto; padding:16px; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           margin-bottom:12px; }
  h1 { font-size:18px; margin:0; }
  a { color:#58a6ff; text-decoration:none; }
  #conn { font-size:12px; color:#8b949e; }
  #conn.live { color:#3fb950; }
  .item { border:1px solid #21262d; border-radius:10px; padding:10px 12px;
          margin:8px 0; background:#0d1117; }
  .item .t { font-size:11px; color:#8b949e; margin-bottom:4px;
             display:flex; flex-wrap:wrap; gap:4px 10px; align-items:center; }
  .badge { display:inline-block; padding:0 7px; border-radius:10px;
           font-size:10px; font-weight:600; text-transform:uppercase;
           letter-spacing:.03em; }
  .b-user    { background:#1f6feb33; color:#58a6ff; border:1px solid #1f6feb66; }
  .b-routing { background:#d2992233; color:#d29922; border:1px solid #d2992266; }
  .b-status  { background:#8b949e22; color:#8b949e; border:1px solid #8b949e55; }
  .b-summary { background:#3fb95022; color:#3fb950; border:1px solid #3fb95066; }
  .b-done    { background:#3fb95022; color:#3fb950; border:1px solid #3fb95066; }
  .b-fail, .b-error { background:#f8514922; color:#f85149; border:1px solid #f8514966; }
  .route { color:#d29922; }
  .conf { color:#8b949e; }
  .lat  { color:#8b949e; }
  .item .text { white-space:pre-wrap; margin-top:4px; }
  .item .summary { color:#c9d1d9; background:#161b22; border-radius:6px;
                   padding:6px 8px; margin-top:6px; }
  .item .err { color:#f85149; background:#f8514911; border-radius:6px;
               padding:6px 8px; margin-top:6px; }
  .item details { margin-top:6px; }
  .item summary { font-size:11px; color:#8b949e; cursor:pointer; }
  .item .raw { color:#8b949e; background:#161b22; border-radius:6px;
               padding:6px 8px; margin-top:4px; white-space:pre-wrap;
               font-size:12px; max-height:220px; overflow:auto; }
  #empty { color:#8b949e; text-align:center; margin-top:40px; }
  #errbox { color:#f85149; text-align:center; margin-top:16px; }
</style></head><body>
<div class="wrap">
  <header>
    <h1>📋 Voice Dashboard — Agent Activity</h1>
    <span id="conn">connecting…</span>
  </header>
  <a href="/">← talk</a>
  <div id="feed"><div id="empty">waiting for activity…</div></div>
</div>
<script>
const BASE = (new URLSearchParams(location.search).get('api') || localStorage.getItem('vdb_api') || '').replace(/\/$/, '');
const TOKEN = new URLSearchParams(location.search).get('token') || localStorage.getItem('vdb_token') || '';
function hdrs(extra) { const h = Object.assign({}, extra || {}); if (TOKEN) h['X-Api-Token'] = TOKEN; return h; }

const feed = document.getElementById('feed');
const conn = document.getElementById('conn');
function esc(s){ return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function fmt(ts){
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function render(items){
  feed.innerHTML = items.map(it => {
    const kind = it.kind || 'status';
    const badge = `<span class="badge b-${esc(kind)}">${esc(kind)}</span>`;
    const route = it.route ? ` · <span class="route">${esc(it.route)}</span>` : '';
    const conf = it.conf ? ` · <span class="conf">conf ${esc(it.conf)}</span>` : '';
    const lat = it.latency_s != null ? ` · <span class="lat">${esc(it.latency_s)}s</span>` : '';
    const detail = it.detail ? ` · <span class="lat">${esc(it.detail)}</span>` : '';
    const text = it.text ? `<div class="text">${esc(it.text)}</div>` : '';
    const summary = it.summary ? `<div class="summary">${esc(it.summary)}</div>` : '';
    const err = it.error ? `<div class="err">${esc(it.error)}</div>` : '';
    const raw = it.raw ? `<details><summary>raw output</summary><div class="raw">${esc(it.raw)}</div></details>` : '';
    return `<div class="item">
      <div class="t"><span>${fmt(it.ts)}</span>${badge}${route}${conf}${lat}${detail}</div>
      ${text}${summary}${err}${raw}</div>`;
  }).join('');
  if (!items.length) feed.innerHTML = '<div id="empty">no activity yet</div>';
}
async function refresh(){
  try {
    const r = await fetch(BASE + '/api/activity?limit=200', { headers: hdrs() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    conn.textContent = 'live · ' + (d.items || []).length + ' items';
    conn.className = 'live';
    render(d.items || []);
  } catch (_) {
    conn.textContent = 'bridge offline';
    conn.className = '';
  }
}
setInterval(refresh, 2000);
refresh();
</script>
</body></html>
"""


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "VoiceDashboard/1.0"

    @property
    def app(self) -> "WebUI":
        return self.server.app  # type: ignore[attr-defined]

    # -- helpers ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # CORS: the Vercel-hosted frontend calls the PC API cross-origin.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected (refresh/cancel/navigate) — not an error

    def _api_token_ok(self) -> bool:
        """Gate API endpoints: no token configured = open (localhost/LAN);
        token configured = must match X-Api-Token header or ?token= param."""
        token = self.app.api_token
        if not token:
            return True
        header = self.headers.get("X-Api-Token", "")
        query = self.path.split("token=")[1].split("&")[0] if "token=" in self.path else ""
        return header == token or query == token

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _html(self, page: str, code: int = 200) -> None:
        self._send(code, page.encode(), "text/html; charset=utf-8")

    def _file(self, path: Path, ctype: str) -> None:
        try:
            self._send(200, path.read_bytes(), ctype)
        except OSError:
            self._send(404, b"not found")

    def do_OPTIONS(self) -> None:  # noqa: N802  (CORS preflight)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802  (some browsers preload with HEAD)
        # Reuse GET logic but don't send body
        path = self.path.split("?")[0]
        if path.startswith("/static/") and self.app._use_frontend_dir:
            name = path.split("/", 2)[-1]
            fpath = self.app._frontend_dir / name
            if fpath.exists():
                low = name.lower()
                if low.endswith(".gif"): ctype = "image/gif"
                elif low.endswith(".png"): ctype = "image/png"
                elif low.endswith(".jpg") or low.endswith(".jpeg"): ctype = "image/jpeg"
                elif low.endswith(".svg"): ctype = "image/svg+xml"
                elif low.endswith(".css"): ctype = "text/css"
                elif low.endswith(".js"): ctype = "application/javascript"
                else: ctype = "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(fpath.stat().st_size))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
        # fallback: try GET path handling but just send headers
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # silence noise
        pass

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            if self.app._use_frontend_dir:
                self._file(self.app._frontend_dir / "index.html", "text/html; charset=utf-8")
            else:
                self._html(CHAT_PAGE)
        elif path == "/reports" or path == "/reports.html":
            if self.app._use_frontend_dir and (self.app._frontend_dir / "reports.html").exists():
                self._file(self.app._frontend_dir / "reports.html", "text/html; charset=utf-8")
            else:
                self._send(404, b"reports page not found")
        elif path == "/activity":
            if self.app._use_frontend_dir and (self.app._frontend_dir / "activity.html").exists():
                self._file(self.app._frontend_dir / "activity.html", "text/html; charset=utf-8")
            else:
                self._html(ACTIVITY_PAGE)
        elif path == "/api/status":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            self._json(self.app.status())
        elif path == "/api/activity":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            limit = int(self.path.split("limit=")[1].split("&")[0]) if "limit=" in self.path else 100
            self._json({"items": self.app.feed.recent(min(limit, 300))})
        elif path.startswith("/api/result/"):
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            rid = path.rsplit("/", 1)[-1]
            self._json(self.app.result(rid))
        elif path.startswith("/api/item/"):
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            iid = path.rsplit("/", 1)[-1].split("?")[0]
            item = self.app.item(iid)
            if item is None:
                self._send(404, b"not found")
                return
            self._json(item)
        elif path == "/api/reports":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params: Dict[str, str] = {}
            for kv in qs.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params[k] = __import__("urllib.parse").parse.unquote_plus(v)
            q = params.get("q", "").strip()[:120]
            cat = params.get("category", "").strip()[:40]
            days = int(params.get("days", "90") or 90)
            limit = min(int(params.get("limit", "200") or 200), 500)
            # merged activity + logs, categorized, with filters
            activity = self.app.feed.recent(limit=300)
            logs = self.app.logs(limit=300, q=q, since=0.0)
            # tag categories
            def _cat(it):
                k = (it.get("kind") or "").lower()
                r = (it.get("route") or it.get("detail") or "").lower()
                txt = " ".join(str(it.get(x, "")) for x in ("text","summary","detail","route","kind")).lower()
                if "weather" in txt: return "weather"
                if any(x in k for x in ("interrupt","hotkey")): return "interrupt"
                if "filler" in txt: return "filler"
                if "hermes" in k or "hermes" in r: return "hermes"
                if "openclaw" in k or "openclaw" in r: return "openclaw"
                if "inbox" in txt or "gmail" in txt or "messenger" in txt or "instagram" in txt or "tiktok" in txt: return "inbox"
                if k in ("summary","conversation","local","fallback"): return k
                return k or "other"
            import time as _time
            cutoff = _time.time() - days * 86400 if days > 0 else 0
            # shared ledger (durable across provider switches + reboots) — also visible in reports
            try:
                _shared = self.app.orch.shared.recent(limit=300) if hasattr(self.app.orch, "shared") else []
                for _r in _shared:
                    _ts = float(_r.get("ts") or 0)
                    if cutoff and _ts and _ts < cutoff:
                        continue
                    _it = {"kind": _r.get("route") or _r.get("role") or "shared", "text": _r.get("text","")[:600], "summary": _r.get("text","")[:600] if _r.get("role")=="agent" else "", "route": _r.get("route","") or _r.get("provider",""), "ts": _ts, "detail": f"shared:{_r.get('role','')}", "shared": True}
                    c = _cat(_it)
                    if cat and c != cat:
                        continue
                    if q and q.lower() not in __import__("json").dumps(_it).lower():
                        continue
                    _it["category"] = c
                    merged_item_exists = any(abs(float(m.get("ts") or 0) - _ts) < 1.0 and m.get("text","")[:80]==_it["text"][:80] for m in activity)
                    if not merged_item_exists:
                        activity.append(_it)
            except Exception:
                pass
            merged = []
            for it in activity:
                if cutoff and float(it.get("ts") or 0) < cutoff:
                    continue
                if cat and _cat(it) != cat:
                    continue
                if q and q.lower() not in __import__("json").dumps(it).lower():
                    continue
                it = dict(it); it["category"] = _cat(it)
                merged.append(it)
            # also include raw log events not yet in feed
            for ev in logs:
                if any(ev.get("ts") == m.get("ts") and ev.get("event") == m.get("kind") for m in merged):
                    continue
                if cutoff and float(ev.get("ts") or 0) < cutoff:
                    continue
                c = _cat({"kind": ev.get("event",""), "text": str(ev)})
                if cat and c != cat:
                    continue
                if q and q.lower() not in __import__("json").dumps(ev).lower():
                    continue
                merged.append({"kind": ev.get("event"), "text": str(ev.get("text","")), "summary": str(ev.get("summary","")), "route": ev.get("route",""), "ts": ev.get("ts"), "category": c, "raw_log": ev})
            merged.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
            self._json({"items": merged[:limit], "pruned_available": True})
        elif path == "/api/logs":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            # ?limit=200&q=foo&since=<unix ts>
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params: Dict[str, str] = {}
            for kv in qs.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params[k] = v
            limit = min(int(params.get("limit", "200")), 500)
            q = params.get("q", "").strip()[:80]
            try:
                since = float(params.get("since", ""))
            except Exception:
                since = 0.0
            self._json({"items": self.app.logs(limit=limit, q=q, since=since)})
        elif path == "/api/shared":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            # ?limit=60 — recent cross-provider history
            try:
                lim = int(self.path.split("limit=")[1].split("&")[0]) if "limit=" in self.path else 60
            except Exception:
                lim = 60
            lim = max(1, min(lim, 200))
            try:
                items = self.app.orch.shared.recent(limit=lim) if hasattr(self.app.orch, "shared") else []
            except Exception:
                items = []
            self._json({"items": items})

        elif self.app._use_frontend_dir and path.startswith("/static/"):
            name = path.split("/", 2)[-1]
            # correct MIME so browsers cache and render images properly
            low = name.lower()
            if low.endswith(".gif"): ctype = "image/gif"
            elif low.endswith(".png"): ctype = "image/png"
            elif low.endswith(".jpg") or low.endswith(".jpeg"): ctype = "image/jpeg"
            elif low.endswith(".svg"): ctype = "image/svg+xml"
            elif low.endswith(".css"): ctype = "text/css"
            elif low.endswith(".js"): ctype = "application/javascript"
            else: ctype = "application/octet-stream"
            self._file(self.app._frontend_dir / name, ctype)
        else:
            self._send(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/talk":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            length = int(self.headers.get("Content-Length", 0))
            pcm = self.rfile.read(length) if length else b""
            if not pcm:
                self._json({"error": "no audio"}, 400)
                return
            try:
                # async: return a request_id immediately (fast ack), the
                # result (audio) is fetched via /api/result/<id>
                rid = self.app.submit_talk(pcm)
                self._json({"request_id": rid})
            except Exception as exc:  # keep the phone page alive on bridge errors
                log.exception("webui /api/talk failed")
                log_event("webui.talk.error", error=str(exc)[:200])
                self._json({"error": str(exc)[:300]}, 500)
        elif path == "/api/interrupt":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                mode = json.loads(body or b"{}").get("mode", "interrupt")
            except Exception:
                mode = "interrupt"
            try:
                self.app.interrupt(mode=mode)
                self._json({"ok": True, "mode": mode})
            except Exception as exc:
                log.exception("webui /api/interrupt failed")
                self._json({"error": str(exc)[:200]}, 500)
        elif path == "/api/prune" or path == "/api/reports/prune":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)) if self.headers.get("Content-Length") else 0) or b"{}"
                data = __import__("json").loads(body or b"{}")
                days = int(data.get("days", 90))
            except Exception:
                days = 90
            removed = self.app._prune_old_events(max_age_days=days)
            self._json({"ok": True, "removed": removed, "days": days})
        elif path == "/api/type":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            try:
                data = json.loads(body or b"{}")
                text = str(data.get("text", "")).strip()
            except Exception:
                text = ""
            if not text:
                self._json({"error": "no text"}, 400)
                return
            try:
                rid = self.app.submit_text(text)
                self._json({"request_id": rid})
            except Exception as exc:
                log_event("webui.type.error", error=str(exc)[:200])
                self._json({"error": str(exc)[:300]}, 500)
        else:
            self._send(404, b"not found")


class WebUI:
    """Thin HTTP layer around the orchestrator's processing pipeline."""

    def __init__(self, orchestrator: Any, host: str = "0.0.0.0", port: int = 8890,
                 asr_factory: Any = None, tts: Any = None,
                 state_dir: Optional[Path] = None) -> None:
        self.orch = orchestrator
        self.host = host
        self.port = port
        self.feed = ActivityFeed()
        self._asr_factory = asr_factory or _default_asr_factory(orchestrator)
        self._tts = tts
        self._state_dir = state_dir or Path("/tmp")
        # pre-seed the live feed from the persisted event journal so the
        # dashboard reflects everything (mic loop, taps, typed, web, history)
        self.feed.load_history(self._state_dir / "events.jsonl", limit=200)
        self._asr = None
        self._asr_lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._results: Dict[str, Dict[str, Any]] = {}
        self._result_lock = threading.Lock()
        # serve the shared frontend/ dir when it exists (Vercel-ready build),
        # otherwise fall back to the embedded pages. Prefer the dedicated
        # voice-dashboard repo (edits there apply live), then the bridge repo.
        # Prefer the bridge's own small frontend (voice pages ~12KB, fast over tunnel).
        # The voice-dashboard React build is 41MB and would make / on mobile brutally slow.
        self._frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
        if not (self._frontend_dir / "index.html").exists():
            self._frontend_dir = Path.home() / "Projects" / "Repos" / "voice-dashboard" / "frontend"
        self._use_frontend_dir = (self._frontend_dir / "index.html").exists()
        # optional shared secret for tunneled/external access (config: webui.token)
        self.api_token: str = orchestrator.cfg.get("webui", "token", default="") if orchestrator is not None else ""

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        handler = type("W", (WebUIHandler,), {})  # subclass per instance
        handler.app = self  # type: ignore[attr-defined]  # keep for direct class access
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        # FIX: 'W' object has no attribute 'feed' crash (2026-08-23 02:23:13)
        # WebUIHandler.app property returns self.server.app, not handler.app.
        # Without this, /api/reports -> self.app.feed fails with AttributeError.
        self._server.app = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="webui-http")
        self._thread.start()
        log_event("webui.started", host=self.host, port=self.port)
        log.info("voice dashboard listening on http://%s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()

    # -- public API -------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        task = self.orch.task
        busy = bool(getattr(task, "active", False)) or bool(getattr(self.orch, "_web_active", 0))
        shared_len = 0
        sticky = ""
        try:
            shared_len = len(self.orch.shared.recent(limit=200)) if hasattr(self.orch, "shared") else 0
        except Exception:
            pass
        try:
            sticky = self.orch.sticky_route.target() if hasattr(self.orch, "sticky_route") else ""
        except Exception:
            pass
        return {
            "bridge": "up",
            "route": getattr(self.orch, "_route", "unknown"),
            "task_active": bool(getattr(task, "active", False)),
            "busy": busy,
            "web_active": int(getattr(self.orch, "_web_active", 0)),
            "port": self.port,
            "uptime_s": round(time.time() - getattr(self.orch, "_t0", time.time()), 1),
            "shared_items": shared_len,
            "sticky_route": sticky,
            "tts": getattr(getattr(self.orch, "_tts", None), "name", "") if hasattr(self.orch, "_tts") else "",
        }

    def interrupt(self, mode: str = "interrupt") -> None:
        """Dashboard interrupt (tap the orb while assistant is speaking or
        a task is running). Mirrors the Ctrl-key barge-in.

        mode='interrupt' — single tap: stops TTS, pauses the task, announces
                           'Paused. Go ahead.' so the user can re-direct.
        mode='stop'     — double tap: full stop without announcement (like Esc).
        """
        self.orch._interrupt_task(announce=(mode == "interrupt"))

    def submit_text(self, text: str) -> str:
        """Type-in path: same pipeline as voice but no ASR step."""
        rid = uuid.uuid4().hex[:12]
        with self._pending_lock:
            self._pending[rid] = {"created": time.time()}
        self.feed.push({"kind": "user", "text": text, "route": "input"})

        def _run() -> None:
            try:
                t0 = time.time()
                result = self.orch.process_web_utterance(text)
                result["latency_s"] = round(time.time() - t0, 2)
                # conversation/local replies are "spoken" through speak() which
                # records into _web_spoken in web mode — surface that text
                # (same fallback as the voice path).
                # Prefer per-request spoken (isolated, no race) over legacy global _web_spoken
                _ctx = self.orch._cur_ctx() if hasattr(self.orch, "_cur_ctx") else None
                spoken = list(_ctx.spoken) if _ctx is not None and getattr(_ctx, "spoken", None) else (getattr(self.orch, "_web_spoken", None) or [])
                spoken_text = " ".join(x for x in spoken if x).strip()
                if not result.get("summary") and spoken_text:
                    # spoken_text IS the reply when _WebCtx already has summary (avoid overwrite)
                    if _ctx is not None and getattr(_ctx, "summary", None):
                        result["summary"] = _ctx.summary
                        result["reply"] = _ctx.summary
                    else:
                        result["summary"] = spoken_text
                        result["reply"] = spoken_text
                # synthesize the reply audio (same as the voice path)
                summary = result.get("summary") or ""
                if summary:
                    try:
                        wav = self._synthesize(summary)
                        result["audio_b64"] = base64.b64encode(wav).decode()
                    except Exception as exc:
                        log_event("webui.tts.failed", error=str(exc)[:160])
            except Exception as exc:
                log.exception("webui type worker failed")
                log_event("webui.type.error", error=str(exc)[:200])
                result = {"text": text, "route": "error",
                          "summary": "Something went wrong.",
                          "reply": "", "events": [],
                          "audio_b64": None, "latency_s": 0.0}
            with self._result_lock:
                self._results[rid] = result
            with self._pending_lock:
                self._pending.pop(rid, None)

        threading.Thread(target=_run, daemon=True, name=f"webui-type-{rid}").start()
        return rid

    def submit_talk(self, pcm: bytes) -> str:
        """Start a voice roundtrip in the background; returns a request id.

        The browser gets the id instantly (fast ack), then polls
        /api/result/<id>. The heavy work (ASR -> route -> script/OpenClaw
        -> local summary -> TTS) runs on a worker thread so the phone UI
        never blocks on a 30s Perplexity search.
        """
        rid = uuid.uuid4().hex[:12]
        with self._pending_lock:
            self._pending[rid] = {"created": time.time()}

        def _run() -> None:
            try:
                result = self.handle_talk(pcm)
            except Exception as exc:
                log.exception("webui talk worker failed")
                log_event("webui.talk.error", error=str(exc)[:200])
                result = {"text": "", "route": "error",
                          "summary": "Something went wrong.",
                          "reply": "", "events": [],
                          "audio_b64": None, "latency_s": 0.0}
            with self._result_lock:
                self._results[rid] = result
            with self._pending_lock:
                self._pending.pop(rid, None)
            # prune old results (keep last 50)
            with self._result_lock:
                if len(self._results) > 50:
                    for k in list(self._results)[:-50]:
                        self._results.pop(k, None)

        threading.Thread(target=_run, daemon=True, name=f"webui-talk-{rid}").start()
        return rid

    def result(self, rid: str) -> Dict[str, Any]:
        """Poll endpoint: pending -> {"status": "pending"} until done."""
        with self._pending_lock:
            pending = rid in self._pending
        if pending:
            return {"status": "pending", "request_id": rid}
        with self._result_lock:
            r = self._results.get(rid)
        if r is None:
            return {"status": "unknown", "request_id": rid}
        return {"status": "done", "request_id": rid, **r}

    def item(self, item_id: str) -> Optional[Dict[str, Any]]:
        return self.feed.get(item_id)

    def _prune_old_events(self, max_age_days: int = 90) -> int:
        """Delete events.jsonl entries older than max_age_days. Returns removed count."""
        import time
        path = self.orch.cfg.state_dir() / "events.jsonl" if self.orch and self.orch.cfg else Path("/tmp") / "events.jsonl"
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return 0
        cutoff = time.time() - max_age_days * 86400
        kept = []
        removed = 0
        for l in lines:
            try:
                ev = __import__("json").loads(l)
                ts = float(ev.get("ts") or 0)
                if ts and ts < cutoff:
                    removed += 1
                    continue
            except Exception:
                pass
            kept.append(l)
        if removed:
            # atomic replace
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), errors="replace")
            tmp.replace(path)
            try:
                self.feed.load_history(path, limit=200)
            except Exception:
                pass
        return removed

    def logs(self, limit: int = 200, q: str = "", since: float = 0.0) -> List[Dict[str, Any]]:
        path = self.orch.cfg.state_dir() / "events.jsonl" if self.orch and self.orch.cfg else Path("/tmp") / "events.jsonl"
        try:
            lines = path.read_text(errors="replace").splitlines()[-max(limit * 6, 6000):]
        except OSError:
            return []
        out: List[Dict[str, Any]] = []
        ql = q.lower() if q else ""
        for l in lines:
            try:
                ev = json.loads(l)
            except Exception:
                continue
            if since and float(ev.get("ts") or 0) < since:
                continue
            if ql and ql not in json.dumps(ev).lower():
                continue
            out.append(ev)
        return out[-limit:]

    def handle_talk(self, pcm: bytes) -> Dict[str, Any]:
        """Full voice roundtrip: PCM -> text -> route -> summary -> TTS."""
        t0 = time.time()
        # reject empty/too-short audio before touching the ASR engine
        if len(pcm) < 1600:  # <0.1s of Int16 @16k
            return {"text": "", "summary": "I couldn't hear you. Try again.",
                    "route": "none", "events": [], "audio_b64": None,
                    "latency_s": round(time.time() - t0, 2)}
        wav = pcm_to_wav(pcm)
        wav_path = self._state_dir / f"webui-{uuid.uuid4().hex[:10]}.wav"
        wav_path.write_bytes(wav)
        try:
            transcript = self._transcribe(wav_path)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass
        text = (transcript.text or "").strip() if transcript else ""
        self.feed.push({"kind": "user", "text": text or "(no speech)", "route": "input"})
        if not text:
            return {"text": "", "summary": "I couldn't hear you. Try again.",
                    "route": "none", "events": [], "audio_b64": None,
                    "latency_s": round(time.time() - t0, 2)}
        result = self.orch.process_web_utterance(text)
        self.feed.push({"kind": "routing", "text": text, "route": result.get("route", ""),
                        "conf": result.get("confidence", "")})
        # summarize + speak the FINAL reply (never the raw tool output).
        # Prefer the text the orchestrator actually "spoke" in web mode
        # (recorded in _web_spoken — acks, summaries, fallbacks); fall back
        # to the routing result.
        # Per-request spoken: isolated so concurrent EN/RO don't swap
        _ctx2 = self.orch._cur_ctx() if hasattr(self.orch, "_cur_ctx") else None
        spoken2 = list(_ctx2.spoken) if _ctx2 is not None and getattr(_ctx2, "spoken", None) else (getattr(self.orch, "_web_spoken", None) or [])
        spoken_text = " ".join(x for x in spoken2 if x).strip()
        summary = (_ctx2.summary if _ctx2 is not None and getattr(_ctx2, "summary", None) else "") or result.get("summary") or result.get("reply") or spoken_text or text
        audio_b64 = None
        events: List[Dict[str, Any]] = []
        if summary:
            events.append({"kind": "meta", "text": result.get("route", "route")})
            try:
                wav = self._synthesize(summary)
                audio_b64 = base64.b64encode(wav).decode()
            except Exception as exc:
                log_event("webui.tts.failed", error=str(exc)[:160])
        self.feed.push({"kind": "done", "text": text, "summary": summary,
                        "route": result.get("route", ""),
                        "latency_s": round(time.time() - t0, 2)})
        return {
            "text": text,
            "route": result.get("route", ""),
            "confidence": result.get("confidence", ""),
            "summary": summary,
            "events": events,
            "audio_b64": audio_b64,
            "latency_s": round(time.time() - t0, 2),
        }

    # -- internals ---------------------------------------------------------
    def _transcribe(self, wav_path: Path):
        with self._asr_lock:
            # Reuse the bridge's OWN ASR instance when it's loaded: the model
            # (parakeet, ~640MB) is already warm and thread-safe (internal
            # lock) — avoids loading a second copy from disk (~10s on the
            # slow Kingston SSD + extra RAM). Fall back to a private instance
            # only when the bridge has no ASR (mic-only edge cases).
            orch_asr = getattr(self.orch, "_asr", None)
            if orch_asr is not None and getattr(orch_asr, "_recognizer", None) is not None:
                return orch_asr.transcribe(wav_path)
            if self._asr is None:
                self._asr = self._asr_factory()
                self._asr.load()
            return self._asr.transcribe(wav_path)

    def _synthesize(self, text: str) -> bytes:
        if self._tts is None:
            self._tts = getattr(self.orch, "_tts", None)
        if self._tts is None:
            raise RuntimeError("no TTS provider")
        out = self._state_dir / f"webui-{uuid.uuid4().hex[:10]}.wav"
        try:
            try:
                self._tts.synthesize(text, out)
            except Exception as exc:
                # Hot-fallback for dashboard audio too (OpenAI -> Piper)
                from .logging_setup import log_event as _le2
                _le2("webui.tts.fallback", provider=getattr(self._tts, "name", "unknown"), error=str(exc)[:160])
                if getattr(self._tts, "name", "") == "openai":
                    from .tts.piper import PiperTTS as _WebFallbackPiper
                    _fb2 = _WebFallbackPiper(model_dir=self.orch.cfg.model_dir(), voice=self.orch.cfg.get("tts", "voice", default="en_US-amy-medium"))
                    _fb2.load()
                    _fb2.synthesize(text, out)
                else:
                    raise
            return out.read_bytes()
        finally:
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass


def _default_asr_factory(orchestrator: Any):
    """Build a standalone SherpaASR mirroring the bridge's ASR config."""
    from .asr.sherpa import SherpaASR
    cfg = orchestrator.cfg
    return lambda: SherpaASR(
        engine=cfg.get("asr", "engine", default="parakeet"),
        model_dir=cfg.model_dir(),
        provider=cfg.get("asr", "provider_ep", default="cpu"),
        num_threads=int(cfg.get("asr", "num_threads", default=4)),
        language=cfg.get("asr", "language", default="en"),
    )