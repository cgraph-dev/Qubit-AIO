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
        with self._lock:
            self._items.appendleft(item)

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)[:limit]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


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

ACTIVITY_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Dashboard — Activity</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font: 14px/1.5 system-ui, sans-serif;
         background:#0d1117; color:#e6edf3; min-height:100vh; }
  .wrap { max-width:760px; margin:0 auto; padding:16px; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           margin-bottom:12px; }
  h1 { font-size:18px; margin:0; }
  a { color:#58a6ff; text-decoration:none; }
  .item { border:1px solid #21262d; border-radius:10px; padding:10px 12px;
          margin:8px 0; background:#0d1117; }
  .item .t { font-size:11px; color:#8b949e; margin-bottom:4px; }
  .item .kind { color:#58a6ff; }
  .item .route { color:#d29922; }
  .item .ok { color:#3fb950; }
  .item .fail { color:#f85149; }
  .item .text { white-space:pre-wrap; margin-top:4px; }
  .item .summary { color:#c9d1d9; background:#161b22; border-radius:6px;
                   padding:6px 8px; margin-top:6px; }
  #empty { color:#8b949e; text-align:center; margin-top:40px; }
</style></head><body>
<div class="wrap">
  <header>
    <h1>📋 Voice Dashboard — Agent Activity</h1>
    <a href="/">← talk</a>
  </header>
  <div id="feed"><div id="empty">waiting for activity…</div></div>
</div>
<script>
const feed = document.getElementById('feed');
const KIND = { user:'', routing:'', status:'', summary:'', done:'', fail:'' };
function esc(s){ return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function fmt(ts){ const d = new Date(ts*1000); return d.toLocaleTimeString(); }
function render(items){
  feed.innerHTML = items.map(it => {
    const cls = it.kind==='fail'?'fail':it.kind==='ok'?'ok':'';
    const route = it.route ? ` · <span class="route">${esc(it.route)}</span>` : '';
    const summary = it.summary ? `<div class="summary">${esc(it.summary)}</div>` : '';
    return `<div class="item">
      <div class="t">${fmt(it.ts)} · <span class="kind">${esc(it.kind)}</span>${route}</div>
      ${it.text ? `<div class="text">${esc(it.text)}</div>` : ''}${summary}</div>`;
  }).join('');
  if (!items.length) feed.innerHTML = '<div id="empty">waiting for activity…</div>';
}
async function refresh(){
  try {
    const r = await fetch('/api/activity?limit=80');
    const d = await r.json();
    render(d.items || []);
  } catch (_) {}
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
        elif self.app._use_frontend_dir and path.startswith("/static/"):
            name = path.split("/", 2)[-1]
            self._file(self.app._frontend_dir / name, "application/octet-stream")
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
        self._frontend_dir = Path.home() / "Projects" / "Repos" / "voice-dashboard" / "frontend"
        if not (self._frontend_dir / "index.html").exists():
            self._frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
        self._use_frontend_dir = (self._frontend_dir / "index.html").exists()
        # optional shared secret for tunneled/external access (config: webui.token)
        self.api_token: str = orchestrator.cfg.get("webui", "token", default="") if orchestrator is not None else ""

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        handler = type("W", (WebUIHandler,), {})  # subclass per instance
        handler.app = self  # type: ignore[attr-defined]
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
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
        return {
            "bridge": "up",
            "route": getattr(self.orch, "_route", "unknown"),
            "task_active": bool(getattr(task, "active", False)),
            "port": self.port,
            "uptime_s": round(time.time() - getattr(self.orch, "_t0", time.time()), 1),
        }

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
                spoken = getattr(self.orch, "_web_spoken", None) or []
                spoken_text = " ".join(x for x in spoken if x).strip()
                if not result.get("summary") and spoken_text:
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
        spoken = getattr(self.orch, "_web_spoken", None) or []
        spoken_text = " ".join(x for x in spoken if x).strip()
        summary = result.get("summary") or result.get("reply") or spoken_text or text
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
            self._tts.synthesize(text, out)
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
