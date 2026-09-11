"""Voice Dashboard — Qubit Agent web UI for Qubit VoiceBridge.

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
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from .logging_setup import log_event, get_logger
from .redaction import redact_obj
from . import resources

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

    def recent(self, limit: int = 100, since: float = 0.0) -> List[Dict[str, Any]]:
        """Newest-first items, optionally only those newer than `since`
        (delta polling — STEAL_LIST item 5: fewer bytes per poll)."""
        with self._lock:
            if since > 0:
                return [it for it in self._items
                        if float(it.get("ts") or 0) > since][:limit]
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
    if evt == "worker.dispatch":
        return {"kind": "status", "text": "dispatched to the worker",
                "route": "worker",
                "detail": str(ev.get("task", ""))[:300]}
    if evt == "worker.session.started":
        return {"kind": "status", "text": "worker session started",
                "route": "worker",
                "detail": str(ev.get("session_key", ""))[:120]}
    if evt == "worker.result":
        return {"kind": "status",
                "text": f"task {ev.get('status', 'done')}",
                "route": "worker",
                "detail": f"elapsed {ev.get('elapsed_s', '')}s"}
    if evt == "worker.summary":
        return {"kind": "summary", "text": "Worker task",
                "summary": ev.get("summary", ""), "route": "worker"}
    if evt == "worker.error" or evt == "worker.denied":
        return {"kind": "fail", "text": "Worker task",
                "route": "worker",
                "detail": str(ev.get("error", ev.get("detail", "")))[:300]}
    if evt == "worker.status" or evt == "worker.progress":
        return {"kind": "status", "text": "Worker running",
                "route": "worker",
                "detail": f"elapsed {ev.get('elapsed_s', '')}s"}
    if evt == "worker.abort" or evt == "worker.continue":
        return {"kind": "status", "text": evt.replace(".", " "),
                "route": "worker",
                "detail": str(ev.get("session_key", ""))[:120]}
    if evt == "agent.task":
        return {"kind": "status", "text": "Agent task dispatched",
                "route": "agent", "detail": str(ev.get("task", ""))[:300]}
    if evt == "agent.summary":
        return {"kind": "summary", "text": "Agent task",
                "summary": ev.get("summary", ""), "route": "agent"}
    if evt == "agent.error" or evt == "agent.unavailable" or evt == "agent.denied":
        return {"kind": "fail", "text": "Agent task",
                "route": "agent", "detail": str(ev.get("error", ev.get("detail", "")))[:300]}
    if evt == "agent.result.discarded":
        return {"kind": "status", "text": "Agent task interrupted",
                "route": "agent", "detail": ev.get("why", "")}
    if evt == "agent.session" or evt == "agent.turn":
        return {"kind": "status", "text": "Agent turn",
                "route": "agent",
                "detail": f"elapsed {ev.get('elapsed_s', '')}s"}
    if evt == "openclaw.dispatch":
        return {"kind": "status", "text": "dispatched to the agent runtime",
                "route": "worker",
                "detail": str(ev.get("task", ""))[:300]}
    if evt == "openclaw.result":
        return {"kind": "status",
                "text": f"task {ev.get('status', 'done')}",
                "route": "worker",
                "detail": f"elapsed {ev.get('elapsed_s', '')}s · {ev.get('reply_len', '')} chars"}
    if evt == "openclaw.summary":
        return {"kind": "summary", "text": "Agent task",
                "summary": ev.get("summary", ""), "route": "worker"}
    if evt == "openclaw.request.failed" or evt == "openclaw.request.exception":
        return {"kind": "fail", "text": "Agent request",
                "route": "worker",
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
        return {"kind": "summary", "text": "Voice task",
                "summary": ev.get("summary",""), "route": "agent"}
    if evt == "hermes.task":
        return {"kind": "status", "text": "Voice task dispatched",
                "route": "agent", "detail": str(ev.get("task",""))[:300]}
    if evt == "system.command":
        cmd = ev.get("command","") or "system"
        return {"kind": "status", "text": f"system:{cmd}",
                "route": "system", "detail": cmd}
    if evt == "hermes.result.discarded":
        return {"kind": "status", "text": "Voice task interrupted",
                "route": "agent", "detail": ev.get("why","")}
    if evt == "openclaw.error":
        return {"kind": "fail", "text": "Agent error",
                "route": "worker", "detail": str(ev.get("error",""))[:300]}
    if evt == "openclaw.status":
        return {"kind": "status", "text": "Agent runtime running",
                "route": "worker", "detail": f"elapsed {ev.get('elapsed_s','')}s"}
    if evt == "openclaw.progress":
        return {"kind": "status", "text": "Agent progress",
                "route": "worker", "detail": f"elapsed {ev.get('elapsed_s','')}s"}
    if evt == "hermes.error":
        return {"kind": "fail", "text": "Voice error",
                "route": "agent", "detail": str(ev.get("error",""))[:300]}
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


def _power_policy(cfg):
    """Return the shared 8O PowerPolicy for the bridge config (one per process)."""
    from . import power as PW
    return PW.power_policy(cfg)


def parse_audio_rate(raw: Any) -> int:
    """Parse the browser's ACTUAL AudioContext sample-rate header.

    Chrome does NOT always honor ``AudioContext({sampleRate:16000})`` — it
    commonly delivers 48 kHz. The backend must never assume 16 kHz. Valid rates
    are bounded to [8000, 48000]; anything else degrades to ``PCM_RATE``.
    """
    try:
        rate = int(raw)
    except (TypeError, ValueError):
        return PCM_RATE
    if not (8000 <= rate <= 48000):
        return PCM_RATE
    return rate


def resample_pcm_int16(pcm: bytes, src_rate: int, dst_rate: int = PCM_RATE) -> bytes:
    """Resample raw Int16 mono PCM from ``src_rate`` to ``dst_rate``.

    Interpreting 48 kHz samples as 16 kHz corrupts duration/pitch and lets
    near-silence be transcribed as invented speech. Linear interpolation is fine
    for 48k->16k speech (same as the capture path). Pure resampling never
    amplifies or creates signal.
    """
    import numpy as np
    if src_rate == dst_rate or not pcm:
        return pcm
    from .audio.vad import resample_linear
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    out = resample_linear(samples, src_rate, dst_rate)
    return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def pcm_rms(pcm: bytes) -> float:
    """RMS of raw Int16 mono PCM in float [-1,1] scale (bounded)."""
    import numpy as np
    if not pcm or len(pcm) % 2:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt((samples ** 2).mean()))


def normalize_int16_bounded(pcm: bytes, *, target: float = 0.35,
                            max_gain: float = 8.0) -> bytes:
    """Peak-normalize REAL speech with a bounded gain; never amplifies silence.

    Callers gate on energy first (near-silence is rejected before this runs),
    so this only ever boosts genuine speech. The gain cap prevents hiss from
    being amplified into a false transcript.
    """
    import numpy as np
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0.001 or peak >= 0.98:
        return pcm
    scale = min(target / peak, max_gain)
    return (np.clip(samples * scale, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


# --------------------------------------------------------------------------
# The dashboard pages (static, self-contained)
# --------------------------------------------------------------------------

CHAT_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Qubit Agent — Talk (offline fallback)</title>
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
let sampleRate = 16000; // the REAL AudioContext rate, sent to the backend

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

function micError(err) {
  const n = err && err.name;
  if (n === 'NotAllowedError' || n === 'SecurityError')
    return 'Microphone permission denied — allow mic access in the browser and retry.';
  if (n === 'NotFoundError' || n === 'OverconstrainedError')
    return 'No microphone found or it is unavailable — check the mic and retry.';
  if (n === 'NotReadableError')
    return 'Microphone is busy or blocked — close other apps using it and retry.';
  return 'Mic error: ' + (err && err.message ? err.message : err);
}

async function startRec() {
  if (recording) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true }});
    // Ask for 16k, but Chrome does NOT reliably honor it — record the REAL
    // rate and send it so the backend never misreads 48 kHz as 16 kHz.
    ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    sampleRate = ctx.sampleRate || 16000;
    if (ctx.state === 'suspended') {
      try { await ctx.resume(); } catch (_) {}
    }
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
    addMsg('err', micError(err));
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
  const minSamples = Math.round((sampleRate || 16000) * 0.1); // 100ms
  if (total < minSamples) {  // <0.1s of audio = accidental tap
    setStatus('too short — try again');
    setTimeout(() => setStatus('ready'), 1500);
    return;
  }
  await sendAudio(out.buffer, sampleRate);
}

async function sendAudio(pcm, rate) {
  busy = true;
  ptt.disabled = true;
  ptt.textContent = 'listening to result…';
  let rid;
  try {
    // 1. submit -> instant request_id (the bridge acks immediately)
    const res = await fetch('/api/talk', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'X-Audio-Rate': String(rate || 16000),
      },
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
  addMsg('user', data.text || (data.reason === 'NO_SPEECH'
    ? '(no speech detected)' : '(no speech recognized)'));
  if (data.summary) addMsg('assistant', data.summary);
  else if (data.error) addMsg('err', data.error);
  if (data.audio_b64) {
    const audio = new Audio('data:audio/wav;base64,' + data.audio_b64);
    setStatus('speaking…', true);
    await audio.play();
    await new Promise(r => audio.onended = r);
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
<title>Qubit Agent — Activity (offline fallback)</title>
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
const BASE = (localStorage.getItem('vdb_api') || '').replace(/\/$/, '');
// Packet 8L contract: an attacker-controlled ?api= query string is NEVER
// honored (it could redirect the stored token); the base is changed only
// through the explicit Settings/Pairing action, which validates the origin
// and tests the token before saving.
// Packet 1B: tokens come from localStorage only; ?token= URLs are rejected server-side.
const TOKEN = localStorage.getItem('vdb_token') || '';
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
# Hermes-first route aliases (owner directive 2026-09-11)
# --------------------------------------------------------------------------
# Every normal public dashboard route renders the vendored Hermes React
# shell. Qubit route names are compatibility aliases onto Hermes
# workspaces; the legacy static Qubit pages are reachable only under
# /classic/. This table is the single source of truth for that mapping.
HERMES_ROUTE_ALIASES = {
    # Hermes-native workspaces (Qubit compatibility names)
    "/chat": "/hermes/chat",
    "/sessions": "/hermes/sessions",
    "/files": "/hermes/files",
    "/models": "/hermes/models",
    "/analytics": "/hermes/analytics",
    "/logs": "/hermes/logs",
    "/cron": "/hermes/cron",
    "/skills": "/hermes/skills",
    "/plugins": "/hermes/plugins",
    "/mcp": "/hermes/mcp",
    "/channels": "/hermes/channels",
    "/webhooks": "/hermes/webhooks",
    "/pairing": "/hermes/pairing",
    "/profiles": "/hermes/profiles",
    "/config": "/hermes/config",
    "/env": "/hermes/env",
    "/system": "/hermes/system",
    "/docs": "/hermes/docs",
    # Qubit page families -> Hermes shell workspaces / overlay pages
    "/activity": "/hermes/logs",
    "/reports": "/hermes/reports",
    "/operations": "/hermes/operations",
    "/ops": "/hermes/operations",
    "/approvals": "/hermes/qubit-approvals",
    "/settings": "/hermes/config",
    "/setup": "/hermes/system",
    "/projects": "/hermes/files",
    "/tasks": "/hermes/cron",
    "/devices": "/hermes/devices",
    "/messages": "/hermes/channels",
    "/memory": "/hermes/memory",
    "/security": "/hermes/qubit-approvals",
}


def hermes_alias_target(path: str) -> Optional[str]:
    """Return the Hermes target for a normal dashboard path, or None.

    Accepts an optional trailing slash and/or ``.html`` suffix so the old
    static URLs (/activity.html, /ops/) resolve to the shell instead of a
    static file. ``/api/*`` paths are never aliased.
    """
    if path.startswith("/api/") or path.startswith("/hermes"):
        return None
    clean = path
    if clean.endswith(".html") and clean != "/index.html":
        clean = clean[: -len(".html")]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    if not clean:
        clean = "/"
    return HERMES_ROUTE_ALIASES.get(clean)


# --------------------------------------------------------------------------
# Hermes WebSocket refusal (audit 2026-09-11 §1D)
# --------------------------------------------------------------------------
# Qubit keeps a single event path (SSE + ops); the vendored Hermes
# dashboard's WebSocket endpoints must never hang or raise a bare
# connection error. An ``Upgrade: websocket`` request for one of these
# paths gets an HTTP 426 + typed JSON envelope carrying the existing GATED
# codes from ``voicebridge.hermes_compat`` (never a new dialect, never a
# WS server). Keys are the API path with any ``/hermes`` prefix stripped,
# so ``/hermes/api/*`` and any plain ``/api/*`` alias resolve alike.
HERMES_WS_GATE_CODES: Dict[str, str] = {
    "/api/pty": "PTY_NO_SHELL",
    "/api/console": "PTY_NO_SHELL",
    "/api/ws": "WS_SINGLE_PATH",
    "/api/events": "WS_SINGLE_PATH",
    "/api/pub": "WS_SINGLE_PATH",
    "/api/audio/speak-stream": "WS_SINGLE_PATH",
}

HERMES_WS_GATE_DETAIL: Dict[str, str] = {
    "PTY_NO_SHELL": (
        "Qubit forbids raw shell/PTY spawn; the xterm mirror cannot attach. "
        "Use Qubit Chat (streaming agent turns) or the TUI live board."),
    "WS_SINGLE_PATH": (
        "The Hermes WebSocket dialect is not served; Qubit keeps one event "
        "path (SSE feed + ops). Chat uses Qubit Chat."),
}

HERMES_WS_GATE_COUNTERPART: Dict[str, str] = {
    "PTY_NO_SHELL": "overlay Qubit Chat tab (POST /api/qubit/chat) + CLI `qubit tui`",
    "WS_SINGLE_PATH": "GET /api/events/stream (SSE) + POST /api/qubit/chat",
}

HERMES_WS_OWNER = "qubit-policy"
HERMES_WS_ROLLBACK = "nothing started; nothing to roll back"


def hermes_ws_gate_code(path: str) -> Optional[str]:
    """Return the GATED code for a WebSocket-upgrade API path, or None.

    Pure path lookup (query string ignored); used by the request handler and
    directly testable. Only the six upstream WS endpoints are listed.
    """
    clean = path.split("?")[0]
    if clean.startswith("/hermes"):
        clean = clean[len("/hermes"):]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    return HERMES_WS_GATE_CODES.get(clean)


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
            # CORS (Packet 1B): only exact configured origins; no wildcard.
            origin = self.headers.get("Origin", "")
            for k, v in cors_headers_for(self.app.allowed_origins, origin):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected (refresh/cancel/navigate) — not an error

    def _api_token_ok(self) -> bool:
        """Packet 1B contract: query-string tokens are ALWAYS rejected; a
        configured token must arrive via the X-Api-Token header. With no
        configured token, non-loopback peers are refused (fail closed)."""
        cfg = self.app.api_token
        qp = "token=" in self.path.split("?", 1)[-1]
        ok, _reason = token_ok(cfg, self.headers.get("X-Api-Token", ""), qp)
        if not ok:
            return False
        if not cfg:
            peer = self.client_address[0] if self.client_address else ""
            if peer not in ("127.0.0.1", "::1", "localhost"):
                return False
        return True

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _html(self, page: str, code: int = 200) -> None:
        self._send(code, page.encode(), "text/html; charset=utf-8")

    def _file(self, path: Path, ctype: str) -> None:
        try:
            self._send(200, path.read_bytes(), ctype)
        except OSError:
            self._send(404, b"not found")

    def _redirect(self, location: str) -> None:
        try:
            body = ("Redirecting to %s\n" % location).encode()
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _frontend_file(self, name: str) -> Optional[Path]:
        """Resolve a frontend file from the ACTIVE frontend dir, falling back
        to the repository frontend when absent (Packet 8E serving fix)."""
        p = self.app._frontend_dir / name
        if p.is_file():
            return p
        repo = REPO_FRONTEND / name
        return repo if repo.is_file() else None

    def do_OPTIONS(self) -> None:  # noqa: N802  (CORS preflight)
        self.send_response(204)
        origin = self.headers.get("Origin", "")
        hdrs_ = cors_headers_for(self.app.allowed_origins, origin)
        for k, v in hdrs_:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _events_stream(self) -> None:  # noqa: N802 (GET /api/events/stream)
        """SSE loop with journal-backed replay (Packet 2B).

        Frame contract: ``id: <journal seq>`` + ``data: <AG-UI wire json>``
        (R1 framing); heartbeat as ``: ping`` comments; overflow asks the
        client to reconnect — replay then comes from the journal, so no event
        is lost or duplicated across reconnects.
        """
        hub = self.app._events_hub
        if hub is None:
            self._send(503, b"event stream unavailable")
            return
        last_id = self.headers.get("Last-Event-ID", "")
        try:
            q = self.path.split("?", 1)[-1]
            if not last_id and "last_seq=" in q:
                last_id = q.split("last_seq=")[1].split("&")[0]
        except Exception:
            last_id = ""
        run_filter = None
        if "run_id=" in self.path:
            run_filter = self.path.split("run_id=")[1].split("&")[0] or None
        cursor = int(last_id) if str(last_id).isdigit() else None

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        for k, v in cors_headers_for(self.app.allowed_origins,
                                     self.headers.get("Origin", "")):
            self.send_header(k, v)
        self.end_headers()
        sub = hub.subscribe(last_event_id=cursor, run_id=run_filter)
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while not sub.closed and self.app._server is not None:
                items = sub.drain(timeout=15.0)
                out = []
                if sub.overflow:
                    sub.overflow = False
                    out.append(": resync\nevent: resync\ndata: {}\n\n")
                for rec in items:
                    out.append(hub.wire_frame(rec))
                if not out:
                    out.append(": ping\n\n")
                self.wfile.write("".join(out).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected — normal lifecycle, not an error
        finally:
            hub.unsubscribe(sub)


    def do_HEAD(self) -> None:  # noqa: N802  (some browsers preload with HEAD)
        # Reuse GET logic but don't send body
        path = self.path.split("?")[0]
        if path.startswith("/static/"):
            name = path.split("/", 2)[-1]
            if name == "theme.css":
                try:
                    from . import themes as _TH
                    body = _TH.css(self.app.orch.cfg).encode("utf-8")
                except Exception:
                    body = (b"/* Qubit theme: fallback to app.css as shipped */\n")
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            fpath = self._frontend_file(name)
            if fpath is not None:
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
                self.end_headers()
                return
        # fallback: try GET path handling but just send headers
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # silence noise
        pass

    # -- Hermes-dashboard compat (owner amendment 2026-09-11) -----------
    # Serves the byte-identical vendored Hermes dashboard build
    # (frontend/hermes-dist/, built from frontend/hermes-web/) at /hermes/
    # with the loopback session token + base path injected server-side
    # (vendored index.html untouched). /hermes/api/* is served by
    # voicebridge/hermes_compat.py on the same backend modules as CLI/voice.
    def _hermes_token_ok(self) -> bool:
        hermes_tok = self.headers.get("X-Hermes-Session-Token", "")
        if hermes_tok:
            qp = "token=" in self.path.split("?", 1)[-1]
            ok, _reason = token_ok(self.app.api_token, hermes_tok, qp)
            if not ok:
                return False
            if not self.app.api_token:
                peer = self.client_address[0] if self.client_address else ""
                if peer not in ("127.0.0.1", "::1", "localhost"):
                    return False
            return True
        return self._api_token_ok()

    def _hermes_dist(self) -> "Path":
        # Resolve the built Hermes dashboard from package data first (a clean
        # wheel install ships frontend/hermes-dist under voicebridge/_data/),
        # then from the checkout. This is the single resolution point so an
        # isolated install serves the real dashboard.
        from . import resources as _resources
        return _resources.resource_path("frontend/hermes-dist")

    def _serve_hermes_index(self) -> None:
        dist = self._hermes_dist()
        fp = dist / "index.html"
        if not fp.is_file():
            self._send(404, b"hermes dashboard not built (see "
                            b"frontend/hermes-qubit/README.md)")
            return
        try:
            html = fp.read_text(encoding="utf-8")
        except OSError:
            self._send(404, b"hermes dashboard unreadable")
            return
        token = self.app.api_token or ""
        safe = json.dumps(token)
        inject = ('<script>window.__HERMES_SESSION_TOKEN__=%s;'
                  'window.__HERMES_BASE_PATH__="/hermes";</script>' % safe)
        if "</head>" in html:
            html = html.replace("</head>", inject + "</head>", 1)
        else:
            html = inject + html
        # Qubit rebrand (server-side template; vendored index.html untouched):
        # product title carries the Qubit name. Upstream names remain in
        # legal notices, attribution, provenance, and compat records.
        import re as _re
        html = _re.sub(r"<title>.*?</title>",
                       "<title>Qubit Agent - Command Center</title>", html,
                       count=1, flags=_re.S)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_hermes_static(self, sub: str) -> None:
        dist = self._hermes_dist()
        rel = sub.lstrip("/").split("?")[0]
        if not rel or ".." in rel.split("/"):
            self._serve_hermes_index()
            return
        fp = dist / rel
        if fp.is_dir():
            fp = fp / "index.html"
        if not fp.is_file():
            # SPA deep link (/hermes/sessions/...) -> shell with injection.
            self._serve_hermes_index()
            return
        low = fp.name.lower()
        if low.endswith(".js"): ctype = "application/javascript"
        elif low.endswith(".css"): ctype = "text/css"
        elif low.endswith(".html"): ctype = "text/html; charset=utf-8"
        elif low.endswith(".json"): ctype = "application/json"
        elif low.endswith(".svg"): ctype = "image/svg+xml"
        elif low.endswith(".png"): ctype = "image/png"
        elif low.endswith(".ico"): ctype = "image/x-icon"
        elif low.endswith(".woff2"): ctype = "font/woff2"
        elif low.endswith(".woff"): ctype = "font/woff"
        elif low.endswith(".map"): ctype = "application/json"
        else: ctype = "application/octet-stream"
        self._file(fp, ctype)

    def _serve_hermes_plugin(self, sub: str) -> None:
        from . import resources as _resources
        plugdir = _resources.resource_path("frontend/hermes-qubit/plugins")
        rel = sub.lstrip("/").split("?")[0]
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or ".." in parts or len(parts) > 3:
            self._send(404, b"not found")
            return
        fp = plugdir.joinpath(*parts)
        try:
            resolved = fp.resolve()
            if plugdir.resolve() not in resolved.parents:
                self._send(404, b"not found")
                return
        except OSError:
            self._send(404, b"not found")
            return
        if not resolved.is_file() or resolved.suffix not in (".js", ".json", ".css"):
            self._send(404, b"not found")
            return
        ctype = {"js": "application/javascript", "json": "application/json",
                 "css": "text/css"}[resolved.suffix.lstrip(".")]
        self._file(resolved, ctype)

    def _hermes_api(self, method: str) -> None:
        if not self._hermes_token_ok():
            self._send(401, b"unauthorized")
            return
        raw = self.path
        sub = raw[len("/hermes"):]
        api_path = sub.split("?")[0]
        query: Dict[str, str] = {}
        if "?" in raw:
            import urllib.parse as _up
            for k, v in _up.parse_qsl(raw.split("?", 1)[1], keep_blank_values=True):
                query[k] = v
        body: Dict[str, Any] = {}
        if method in ("POST", "PUT", "PATCH"):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length:
                try:
                    raw_body = self.rfile.read(length).decode("utf-8", "replace")
                    body = json.loads(raw_body) if raw_body.strip() else {}
                except ValueError:
                    self._send(400, b"bad json body")
                    return
                if not isinstance(body, dict):
                    body = {"_list": body}
        # Cutover stage 1 (docs/QUBIT_HERMES_CUTOVER_MIGRATION.md): when the
        # owner enables webui.hermes_backend, safe reads are served by the
        # vendored Hermes backend in-process (single listener, no second
        # server); Qubit-gated surfaces and all writes stay on the Qubit
        # permissions/confirmation/audit path. 404/405 fall back to compat.
        if method in ("GET", "HEAD") and self._hermes_backend_enabled():
            try:
                from . import hermes_asgi as HA
                if HA.safe_to_proxy(method, api_path):
                    code, hdrs, rbody = HA.call(
                        method, api_path, query=query,
                        headers={"accept": "application/json"})
                    if code not in (404, 405):
                        ctype = hdrs.get("content-type", "application/json")
                        self._send(code, rbody, ctype)
                        return
            except Exception as exc:
                log_event("webui.hermes_backend.fallback",
                          error=str(exc)[:160])
        try:
            from . import hermes_compat as HC
            code, out = HC.dispatch(self.app.orch.cfg, method, api_path,
                                    query, body)
        except Exception as exc:
            self._json({"ok": False, "code": "EXECUTOR_ERROR",
                        "detail": redact_obj(str(exc))[:200]}, 500)
            return
        self._json(out, code)

    def _hermes_backend_enabled(self) -> bool:
        """Cutover flag: serve safe dashboard reads from the vendored Hermes
        backend. Stage 1 default is off so compatibility behavior is unchanged
        until the documented migration flips it (Stage 2)."""
        try:
            return bool(self.app.orch.cfg.get("webui", "hermes_backend",
                                              default=False))
        except Exception:
            return False

    # -- public read-only session share ----------------------------------
    def _public_share(self, path: str) -> None:
        """GET /share/<share_id>?token=... — public, token-scoped read-only
        session transcript share.

        Authentication is the share token in the query string, NEVER the
        dashboard token. Unknown/expired/revoked/bad tokens fail closed with
        a typed code; one share can never read another session.
        """
        raw = path
        target = raw.split("?", 1)[0]
        query: Dict[str, str] = {}
        if "?" in raw:
            import urllib.parse as _up
            query = dict(_up.parse_qsl(raw.split("?", 1)[1],
                                       keep_blank_values=True))
        try:
            from . import hermes_compat as HC
            code, out = HC.dispatch(self.app.orch.cfg, "GET", target,
                                    query, {})
        except Exception as exc:
            self._json({"ok": False, "code": "EXECUTOR_ERROR",
                        "detail": redact_obj(str(exc))[:200]}, 500)
            return
        self._json(out, code)

    # -- signed inbound webhook ingress ----------------------------------
    def _webhook_ingress(self, path: str) -> None:
        """POST /hooks/<sub_id> (alias /hermes/hooks/<sub_id>).

        Authenticated by HMAC signature over the subscription's configured
        ``env:``/``file:`` secret — NOT by the dashboard session token. An
        unsigned or unknown request fails closed with a typed code. The body
        is read bounded; secret values never leave the vault or appear in
        receipts.
        """
        from . import webhooks as WH

        prefix = ("/hermes/hooks/" if path.startswith("/hermes/hooks/")
                  else "/hooks/")
        sub_id = path[len(prefix):].strip("/")
        if not sub_id or "/" in sub_id:
            self._json({"state": "failed", "code": WH.BAD_INPUT_CODE,
                        "detail": "subscription id required"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length < 0:
            length = 0
        if length > WH.MAX_BODY_BYTES:
            self._json({"state": "failed", "code": WH.TOO_LARGE_CODE,
                        "detail": f"body exceeds {WH.MAX_BODY_BYTES} byte cap"},
                       413)
            return
        body = self.rfile.read(length) if length else b""
        try:
            out = WH.ingest(self.app.orch.cfg, sub_id, body, self.headers)
            self._json(out, 200)
        except WH.WebhookError as exc:
            status = {
                WH.NOT_FOUND_CODE: 404,
                WH.BAD_SIGNATURE_CODE: 401,
                WH.AUTH_CODE: 401,
                WH.STALE_CODE: 401,
                WH.REPLAY_CODE: 409,
                WH.TOO_LARGE_CODE: 413,
                WH.BAD_INPUT_CODE: 400,
                WH.CORRUPT_CODE: 500,
            }.get(exc.code, 400)
            self._json({"state": "failed", "code": exc.code,
                        "detail": redact_obj(exc.detail)[:200]}, status)

    def _first_header(self, names):
        for name in names:
            try:
                value = self.headers.get(name)
            except Exception:
                value = None
            if value:
                return value
        return ""

    def _channel_ingress(self, path: str) -> None:
        """POST /channels/<channel_id> (alias /hermes/channels/<channel_id>).

        Authenticated by the HMAC signature over the channel's configured
        ``env:``/``file:`` secret — NOT by the dashboard session token. The
        raw body is read bounded and handed to
        ``channels_inbound.receive``; a fully verified delivery still
        resolves to the typed ``CHANNEL_UNAVAILABLE`` gate (no listener
        transport exists). Secret values never appear in responses.
        """
        from . import channels_inbound as CI

        prefix = ("/hermes/channels/" if path.startswith("/hermes/channels/")
                  else "/channels/")
        channel_id = path[len(prefix):].strip("/")
        if not channel_id or "/" in channel_id:
            self._json({"state": "failed", "code": CI.BAD_INPUT_CODE,
                        "detail": "channel id required"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length < 0:
            length = 0
        if length > CI.MAX_BODY_BYTES:
            self._json({"state": "failed", "code": CI.TOO_LARGE_CODE,
                        "detail": f"body exceeds {CI.MAX_BODY_BYTES} byte cap"},
                       413)
            return
        body = self.rfile.read(length) if length else b""
        signature = self._first_header(CI.SIGNATURE_HEADERS)
        delivery_id = self._first_header(CI.DELIVERY_HEADERS)
        timestamp = self._first_header(CI.TIMESTAMP_HEADERS)
        try:
            out = CI.receive(
                self.app.orch.cfg, channel=channel_id, payload=body,
                signature=signature, timestamp=timestamp or None,
                delivery_id=delivery_id,
                sender=self.headers.get("X-Qubit-Sender", "") or "",
                event=self.headers.get("X-Qubit-Event", "") or "message")
            self._json(out, 200)
        except CI.ChannelError as exc:
            status = {
                CI.UNKNOWN_CODE: 404,
                CI.NOT_FOUND_CODE: 404,
                CI.BAD_SIGNATURE_CODE: 401,
                CI.STALE_CODE: 401,
                CI.NO_CREDENTIAL_CODE: 401,
                CI.REPLAY_CODE: 409,
                CI.ESTOP_CODE: 409,
                CI.UNAVAILABLE_CODE: 409,
                CI.TOO_LARGE_CODE: 413,
                CI.BAD_INPUT_CODE: 400,
                CI.EVENT_REFUSED_CODE: 400,
                CI.CORRUPT_CODE: 500,
            }.get(exc.code, 400)
            self._json({"state": "failed", "code": exc.code,
                        "detail": redact_obj(exc.detail)[:200]}, status)

    def _refuse_hermes_ws(self) -> bool:
        """Typed HTTP 426 refusal for a Hermes WebSocket upgrade (audit §1D).

        Returns True when the request was an ``Upgrade: websocket`` for a
        gated Hermes path and the typed envelope was written. No WS server is
        started; the connection is closed cleanly. Auth is intentionally not
        consulted: browser WebSocket clients cannot attach the session-token
        header, and a policy refusal is not secret — requiring auth would
        replace the typed answer with a bare 401.
        """
        if self.headers.get("Upgrade", "").strip().lower() != "websocket":
            return False
        api_path = self.path.split("?")[0]
        if api_path.startswith("/hermes"):
            api_path = api_path[len("/hermes"):]
        if len(api_path) > 1:
            api_path = api_path.rstrip("/")
        code = HERMES_WS_GATE_CODES.get(api_path)
        if not code:
            return False
        detail = HERMES_WS_GATE_DETAIL.get(code, "")
        counterpart = HERMES_WS_GATE_COUNTERPART.get(code, "")
        # One policy source: reuse the GATED row's detail/counterpart when the
        # same GET path already carries this code (pty/console/ws).
        try:
            from . import hermes_compat as HC
            route, _ = HC.match("GET", api_path)
            if route and route.get("disposition") == "gated":
                gate = route.get("gate") or {}
                if gate.get("code") == code:
                    detail = gate.get("detail") or detail
                    counterpart = gate.get("counterpart") or counterpart
        except Exception:
            pass
        self.close_connection = True
        self._json({"ok": False, "state": "UNAVAILABLE", "code": code,
                    "detail": detail, "owner": HERMES_WS_OWNER,
                    "rollback": HERMES_WS_ROLLBACK, "ws": "single-sse",
                    "qubit_counterpart": counterpart}, 426)
        # Route through the handler's log hook (no-op here by design) and the
        # audit journal. Only the path + code — never headers or query tokens.
        self.log_message("hermes websocket refused: %s code=%s", api_path, code)
        log_event("webui.hermes.ws_refused", path=api_path, code=code,
                  state="UNAVAILABLE")
        return True

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if self._refuse_hermes_ws():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            # Hermes-first (owner directive): the Hermes React dashboard at
            # /hermes/ is the active UI. The legacy static Qubit page is
            # preserved under /classic/ only — root must never serve the
            # old horizontal nav / Activity/Talk cards as the first screen.
            self._redirect("/hermes/")
            return
        # Every normal public dashboard route renders the Hermes React
        # shell; Qubit route names are compatibility aliases. Static pages
        # are reachable only under the explicitly labelled /classic/.
        _alias = hermes_alias_target(path)
        if _alias is not None:
            self._redirect(_alias)
            return
        elif path == "/classic/" or path == "/classic/index.html":
            fp = self._frontend_file("index.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            elif self.app._use_frontend_dir:
                self._send(404, b"index not found")
            else:
                self._html(CHAT_PAGE)
        elif path.startswith("/classic/"):
            # Explicit legacy fallback: /classic/<page> serves the old
            # static page of that name. Nothing links here.
            _name = path[len("/classic/"):].strip("/")
            if not _name.endswith(".html"):
                _name += ".html"
            fp = self._frontend_file(_name)
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"classic page not found")
        elif path == "/token-setup" or path == "/token-setup.html":
            # No-auth BY DESIGN: storing a token grants nothing by itself; the
            # API still rejects every request lacking the correct header.
            fp = self._frontend_file("token-setup.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"token-setup page not found")
        elif path == "/approvals" or path == "/approvals.html":
            fp = self._frontend_file("approvals.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"approvals page not found")
        elif path == "/reports" or path == "/reports.html":
            fp = self._frontend_file("reports.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"reports page not found")
        elif path == "/settings" or path == "/settings.html":
            # Packet 7D/8E: beginner/advanced control center (every control
            # calls a real /api/* endpoint; no frontend-only state).
            fp = self._frontend_file("settings.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"settings page not found")
        elif path == "/setup" or path == "/setup.html":
            # Packet 8T: one-click setup + health page (every control calls
            # a real /api/setup/* endpoint; no frontend-only state).
            fp = self._frontend_file("setup.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"setup page not found")
        elif path == "/activity":
            fp = self._frontend_file("activity.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._html(ACTIVITY_PAGE)
        elif path == "/docs" or path == "/docs.html":
            fp = self._frontend_file("docs.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"docs page not found")
        elif path == "/api/docs" or path.startswith("/api/docs/"):
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            doc_id = path[len("/api/docs"):].strip("/")
            if not doc_id:
                self._json(user_docs_index())
                return
            try:
                self._json(user_doc(doc_id))
            except KeyError:
                self._send(404, b"unknown doc")
            except FileNotFoundError:
                self._json({"state": "UNAVAILABLE", "code": "DOCS_NOT_SHIPPED",
                            "detail": "user guide is not in this install; "
                                      "see the repository docs/"},
                           503)
        elif path == "/api/status":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            self._json(self.app.status())
        elif path == "/api/runtime":
            # Backend-authoritative runtime inventory (product, agent
            # runtime, voice, broker latch, approvals, adapters,
            # providers). Read-only; typed states.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from .runtime_inventory import runtime_inventory
            self._json(runtime_inventory(self.app.orch.cfg))
        elif path == "/api/activity":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            limit = int(self.path.split("limit=")[1].split("&")[0]) if "limit=" in self.path else 100
            # ?since=<unix ts> -> only items newer than the client's watermark
            # (delta polling); ETag/If-None-Match short-circuits unchanged polls.
            since = 0.0
            if "since=" in self.path:
                try:
                    since = float(self.path.split("since=")[1].split("&")[0])
                except Exception:
                    since = 0.0
            items = self.app.feed.recent(min(limit, 300), since=since)
            etag = 'W/"%d-%d"' % (len(items),
                                  int(float(items[0].get("ts") or 0) * 1000)
                                  if items else 0)
            inm = self.headers.get("If-None-Match", "")
            try:
                if inm == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    for k, v in cors_headers_for(self.app.allowed_origins,
                        self.headers.get("Origin", "")):
                        self.send_header(k, v)
                    self.end_headers()
                    return
                body = json.dumps({"items": items}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", etag)
                for k, v in cors_headers_for(self.app.allowed_origins,
                    self.headers.get("Origin", "")):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        elif path == "/api/events/stream":
            # Packet 2B: typed event stream over the EXISTING server. Replay
            # from the durable journal via Last-Event-ID header or ?last_seq=
            # (id is not a secret; query-string TOKENS stay forbidden).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            self._events_stream()

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
        elif path == "/ops" or path == "/ops.html":
            # Packet 8T-F: operations console — every registry capability
            # plus explicit extras, each calling the same backend as the
            # CLI and voice surfaces. No frontend-only state.
            fp = self._frontend_file("ops.html")
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"ops page not found")
        elif path in ("/sessions", "/projects", "/models", "/skills",
                      "/skills.html", "/tasks", "/tasks.html", "/security",
                      "/security.html", "/devices", "/devices.html",
                      "/messages", "/messages.html", "/memory",
                      "/memory.html", "/system", "/system.html",
                      "/sessions.html", "/projects.html", "/models.html"):
            # Qubit family workspaces: catalog-driven controls over the
            # same /api/ops backend as CLI and voice. No frontend-only
            # state; every control needs a backend receipt to succeed.
            name = path.strip("/").replace(".html", "") + ".html"
            fp = self._frontend_file(name)
            if fp is not None:
                self._file(fp, "text/html; charset=utf-8")
            else:
                self._send(404, b"family page not found")
        elif path == "/api/ops":
            # Packet 8T-F: machine-readable operations catalog (Tier 1
            # voice-registry ops + Tier 2 extras with honest interfaces).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import dashboard_ops as OPS
                self._json({"state": "OK", "ops": OPS.catalog()})
            except Exception as exc:
                self._json({"state": "degraded",
                            "detail": redact_obj(str(exc))[:200]}, 500)
        elif path == "/api/approvals":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            if self.app.approvals is None:
                self._json({"items": []})
                return
            items = [{"id": a.id, "capability": a.capability, "summary": a.summary,
                      "state": a.state, "created_at": a.created_at,
                      "expires_at": a.expires_at}
                     for a in self.app.approvals.pending()]
            self._json({"items": items})
        elif path == "/api/lessons":
            # Packet 6A: learned preferences with stored provenance, plus the
            # review/staged items. Explanations come from the ledger, never
            # invented by the model.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            led = getattr(getattr(self.app, "orch", None), "learning", None)
            if led is None:
                self._json({"lessons": [], "reviews": []})
                return
            self._json({
                "lessons": led.lessons(),
                "reviews": led.reviews(),
            })
        elif path == "/api/reports" or path.startswith("/api/reports/"):
            # Packet 8C: canonical report bundle index / detail / asset / export.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from .communications.reports import ReportStore, ReportStoreError
                rstore = ReportStore(self.app.orch.cfg.state_dir() / "reports")
                sub = path[len("/api/reports"):].strip("/")
                if not sub:
                    qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                    params = {}
                    for kv in qs.split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            params[k] = __import__("urllib.parse").parse.unquote_plus(v)
                    out = rstore.index(q=params.get("q", ""),
                                       platform=params.get("platform", ""),
                                       limit=int(params.get("limit", "100") or 100))
                    self._json({"state": "OK", **out})
                    return
                parts = sub.split("/")
                if parts[0] == "export" and len(parts) == 2:
                    blob = rstore.export(parts[1])
                    if blob is None:
                        self._json({"state": "MISSING", "detail": "no such report"}, 404)
                        return
                    self._send(200, blob, "application/zip")
                    return
                if len(parts) == 2 and parts[1] == "html":
                    # self-contained bundle HTML for the print / read view
                    hp = rstore.bundle_dir(parts[0]) / "index.html"
                    if not hp.exists():
                        self._json({"state": "MISSING", "detail": "no such report"}, 404)
                        return
                    self._send(200, hp.read_bytes(), "text/html")
                    return
                if len(parts) == 3 and parts[1] == "assets":
                    got = rstore.asset(parts[0], parts[2])
                    if got is None:
                        self._json({"state": "MISSING", "detail": "asset unavailable"},
                                   404)
                        return
                    data, mime = got
                    self._send(200, data, mime)
                    return
                if len(parts) == 1:
                    det = rstore.detail(parts[0])
                    if det is None:
                        self._json({"state": "MISSING", "detail": "no such report"},
                                   404)
                        return
                    self._json({"state": "OK", "report": det})
                    return
                self._json({"state": "failed", "detail": "unknown reports path"}, 400)
            except ReportStoreError as exc:
                self._json({"state": "failed", "detail": str(exc)[:160]}, 400)
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:200]})
        elif path == "/api/notifications":
            # Packet 8D: recent deduplicated monitor notifications (redacted).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from .communications.adapters.registry import communications_store
                store = communications_store(self.app.orch.cfg)
                self._json({"state": "OK",
                            "pending": store.pending_notifications(),
                            "notifications": store.recent_notifications(limit=50)})
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:160]})
        elif path == "/api/profiles":
            # Packet 7D: provider/profile registry (read-only list/show/check).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            kind = None
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            for kv in qs.split("&"):
                if kv.startswith("kind="):
                    kind = kv.split("=", 1)[1]
            if kind and kind in ("brain", "stt", "tts", "voice", "audio"):
                self._json(P.profiles_show(self.app.orch.cfg, kind))
                return
            self._json(P.profiles_list(self.app.orch.cfg))
        elif path == "/api/profiles/check":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            kind = None
            pid = None
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            for kv in qs.split("&"):
                if kv.startswith("kind="):
                    kind = kv.split("=", 1)[1]
                if kv.startswith("profile="):
                    pid = kv.split("=", 1)[1]
            if kind not in ("brain", "stt", "tts", "voice", "audio"):
                self._json({"state": "MISSING", "detail": "kind required"}, 400)
                return
            self._json(P.profile_check(self.app.orch.cfg, kind, pid))
        elif path == "/api/audio/status" or path == "/api/audio/list":
            # Packet 7D: audio settings contract exposed to the dashboard.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from .audio import settings as AS
            try:
                if path.endswith("/status"):
                    self._json(AS.status(self.app.orch.cfg))
                else:
                    devs = AS.list_devices()
                    self._json({"inputs": [{"stable_id": d.stable_id,
                                            "name": d.name, "backend": d.backend,
                                            "is_default": d.is_default}
                                           for d in devs["inputs"]],
                                "outputs": [{"stable_id": d.stable_id,
                                             "name": d.name, "backend": d.backend,
                                             "is_default": d.is_default}
                                            for d in devs["outputs"]]})
            except AS.AudioSettingsError as exc:
                self._json({"state": "MISSING", "detail": str(exc)[:200]}, 503)
        elif path == "/api/permissions" and self.command == "GET":
            # Packet 7E: permission profiles + scoped grants + skills. Read-only
            # here; mutating actions go through POST with explicit confirmation.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import permissions as P
                from .broker import build_host_broker
                store = P.permissions_store(self.app.orch.cfg)
                mode, scopes, expires = store.mode()
                grants = [g.to_dict() for g in store.grants()]
                policy = P.PermissionPolicy(store,
                                            registry=build_host_broker().registry)
                effective = {
                    g["capability"]: policy.effective(
                        g["capability"], tuple(g["scopes"])) for g in grants}
                self._json({
                    "state": "OK",
                    "mode": mode, "mode_scopes": scopes,
                    "mode_expires_at": expires,
                    "grants": grants,
                    "skills": [s.to_dict() for s in store.skills()],
                    "audit": store.audit(30),
                    "effective": effective,
                    # 7D-compatible review list (capability/tier view)
                    "items": [{"capability": g["capability"],
                               "tier": "granted", "scopes": g["scopes"]}
                              for g in grants],
                    "source": "permissions store",
                })
            except Exception as exc:
                self._json({"state": "degraded",
                            "detail": str(exc)[:200], "items": []})
        elif path == "/api/websites":
            # Packet 8E: website-integration onboarding/observability (read-only
            # here; mutations go through POST). Never leaks page text/secrets.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import permissions as P
                from . import websites as W
                store = W.website_integrations_store(self.app.orch.cfg)
                perms = P.permissions_store(self.app.orch.cfg)
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = {}
                for kv in qs.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = __import__("urllib.parse").parse.unquote_plus(v)
                if params.get("logs"):
                    rows = store.logs(params["logs"],
                                      tail=max(1, min(int(params.get("tail", "50") or 50), 500)))
                    self._json({"state": "OK", "logs": rows})
                    return
                if params.get("id"):
                    rec = store.lookup(params["id"])
                    if rec is None:
                        self._json({"state": "MISSING",
                                    "detail": "no such integration"}, 404)
                        return
                    self._json({"state": "OK", "integration": rec.to_dict(),
                                "effective": W.effective_permissions(
                                    perms, store, rec)})
                    return
                self._json({"state": "OK", "mode": perms.mode()[0],
                            "integrations": [r.to_dict()
                                             for r in store.integrations()],
                            "skills": [{"skill_id": s.skill_id,
                                        "status": s.status,
                                        "version": s.manifest.version,
                                        "license": s.manifest.license,
                                        "compatibility":
                                            s.manifest.compatibility}
                                       for s in perms.skills()]})
            except W.WebsiteIntegrationError as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:200]})
        elif path == "/api/actions":
            # Packet 8F: typed action layer (read-only here; mutations go
            # through POST). Same backend as the CLI/voice. Never leaks
            # secrets or private content.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import actions as A
                runner = A.action_runner(self.app.orch.cfg,
                                         power=_power_policy(
                                             self.app.orch.cfg))
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = {}
                for kv in qs.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = __import__("urllib.parse").parse.unquote_plus(v)
                if params.get("id"):
                    self._json({"state": "OK",
                                "action": runner.show(params["id"])})
                    return
                if params.get("check"):
                    self._json(runner.check(params["check"],
                                            params.get("scope", "")))
                    return
                if params.get("receipt"):
                    try:
                        self._json({"state": "OK",
                                    "receipt": runner.receipt(params["receipt"])})
                    except A.ActionError as exc:
                        self._json({"state": "MISSING", "detail": str(exc)}, 404)
                    return
                limit = max(1, min(int(params.get("receipts", "0") or "0"),
                                   200))
                out = {"state": "OK",
                       "mode": runner.perms.mode()[0],
                       "state_path": str(runner.store.path),
                       "actions": runner.actions()}
                if limit:
                    out["receipts"] = runner.receipts(limit)["receipts"]
                self._json(out)
            except A.ActionError as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:200]})
        elif path == "/api/ghost":
            # Packet 8N: read-only Ghost adapter status/parity (GET). Mutations
            # are never exposed here; the CLI/authenticated dashboard use the
            # same backend and typed states. No live launch ever happens.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import ghost as G
                runner = G.ghost_runner(self.app.orch.cfg,
                                        power=_power_policy(
                                            self.app.orch.cfg))
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = {}
                for kv in qs.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = __import__("urllib.parse").parse.unquote_plus(v)
                if params.get("id"):
                    spec = G.ghost_spec(params["id"])
                    if spec is None:
                        self._json({"state": "MISSING",
                                    "detail": "unknown Ghost capability"}, 404)
                        return
                    self._json({"state": "OK", "action": spec.to_dict()})
                    return
                if params.get("check"):
                    self._json(runner.check(params["check"],
                                            params.get("scope", "")))
                    return
                out = runner.ghost_status()
                out["capabilities"] = runner.ghost_actions()
                self._json(out)
            except G.ActionError as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:200]})
        elif path == "/api/opencode":
            # Packet 8R: read-only OpenCode adapter status/parity (GET).
            # Mutations (grant/prepare/execute/enable) are CLI-only behind the
            # same runner/broker; nothing here ever spawns a subprocess.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import opencode as OC
                runner = OC.opencode_runner(
                    self.app.orch.cfg,
                    power=_power_policy(self.app.orch.cfg))
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = {}
                for kv in qs.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = __import__("urllib.parse").parse.unquote_plus(v)
                if params.get("id"):
                    spec = OC.opencode_spec(params["id"])
                    if spec is None:
                        self._json({"state": "MISSING",
                                    "detail": "unknown OpenCode capability"},
                                   404)
                        return
                    self._json({"state": "OK", "action": spec.to_dict()})
                    return
                if params.get("check"):
                    self._json(runner.check(params["check"],
                                            params.get("scope", "code") or
                                            "code"))
                    return
                out = runner.status()
                out["capabilities"] = runner.opencode_actions()
                self._json(out)
            except OC.OpenCodeError as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:200]})
        elif path == "/api/power":
            # Packet 8O: one authenticated policy path. Read-only inventory +
            # control/grant status here; mutations go through POST with explicit
            # confirmation. Never executes or launches anything.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import power as PW
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = {}
                for kv in qs.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = __import__(
                            "urllib.parse").parse.unquote_plus(v)
                if params.get("capability"):
                    policy = PW.power_policy(self.app.orch.cfg)
                    out = policy.check(
                        params["capability"],
                        scopes=tuple(params.get("scope", "").split(",")) if
                        params.get("scope") else (),
                        target=params.get("target", "") or "",
                        role=params.get("role", "") or "",
                        account_scope=params.get("account", "") or "")
                    out["state"] = "OK" if out["allowed"] else "denied"
                    self._json(out)
                    return
                store = PW.power_grant_store(self.app.orch.cfg)
                self._json({
                    "state": "OK",
                    "profile": PW.power_profile(self.app.orch.cfg),
                    "grant": store.grant().to_dict() if store.grant() else None,
                    "grant_health": store.health(),
                    "control": PW.power_control(self.app.orch.cfg).status(),
                    "mcp": PW.mcp_status(self.app.orch.cfg),
                })
            except PW.PowerError as exc:
                self._json({"state": "failed",
                            "code": getattr(exc, "code", "POWER_ERROR"),
                            "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "degraded", "detail": str(exc)[:200]})
        elif path == "/api/device/status":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import device as Dev
                self._json(Dev.profile(self.app.orch.cfg))
            except Exception as exc:
                self._json({"enrolled": False, "detail": str(exc)[:120]})
        elif path == "/api/branding":
            # Packet 7D: Qubit branding plan/verify (read-only; rollback via POST).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            self._json(P.branding_verify(self.app.orch.cfg))
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
        elif path == "/api/setup/health":
            # Packet 8T: backend-authoritative installation inventory
            # (redacted; same backend as `voicebridge setup health`).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import lifecycle as LC
            offline = "offline=1" in (self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
            self._json(LC.health_inventory(self.app.orch.cfg,
                                           offline=offline))
        elif path == "/api/setup/checklist":
            # Packet 8T: resumable staged checklist (read-only).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import lifecycle as LC
            offline = "offline=1" in (self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
            self._json(LC.checklist(self.app.orch.cfg, offline=offline))
        elif path == "/api/setup/diagnostics":
            # Packet 8T: redacted diagnostic bundle (never includes secrets).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import lifecycle as LC
            self._json(LC.diagnostic_bundle(self.app.orch.cfg))

        elif path.startswith("/share/"):
            # Public read-only session share: auth is the share token in the
            # query, never the dashboard token (owner directive: public share
            # URL). Typed gates; no cross-session access.
            self._public_share(path)
        elif path == "/hermes" or path.startswith("/hermes/"):
            # Vendored Hermes dashboard (amendment 2026-09-11): same-origin
            # /hermes/api/* compat backend + overlay plugins; the classic
            # Qubit pages above are untouched.
            sub = path[len("/hermes"):]
            if sub.startswith("/api/"):
                if self.command == "GET":
                    self._hermes_api("GET")
                else:
                    self._send(405, b"method not allowed")
                return
            if sub.startswith("/dashboard-plugins/"):
                self._serve_hermes_plugin(
                    sub[len("/dashboard-plugins/"):])
                return
            self._serve_hermes_static(sub)
            return
        elif path.startswith("/static/"):
            name = path.split("/", 2)[-1]
            if name == "theme.css":
                # Dynamic Qubit theme override (state-driven, after app.css).
                # Best-effort: any failure falls back to app.css as shipped.
                try:
                    from . import themes as _TH
                    body = _TH.css(self.app.orch.cfg).encode("utf-8")
                except Exception:
                    body = (b"/* Qubit theme: fallback to app.css as shipped */\n")
                self._send(200, body, "text/css")
                return
            fp = self._frontend_file(name)
            if fp is None:
                self._send(404, b"not found")
                return
            # correct MIME so browsers cache and render images properly
            low = name.lower()
            if low.endswith(".gif"): ctype = "image/gif"
            elif low.endswith(".png"): ctype = "image/png"
            elif low.endswith(".jpg") or low.endswith(".jpeg"): ctype = "image/jpeg"
            elif low.endswith(".svg"): ctype = "image/svg+xml"
            elif low.endswith(".css"): ctype = "text/css"
            elif low.endswith(".js"): ctype = "application/javascript"
            else: ctype = "application/octet-stream"
            self._file(fp, ctype)
        else:
            self._send(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/permissions":
            # Packet 7E: mutating permission/skill actions (explicit confirm).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import permissions as P
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode() or "{}")
                store = P.permissions_store(self.app.orch.cfg)
                action = str(body.get("action", ""))
                confirmed = bool(body.get("confirmed", False))
                actor = "dashboard"
                if action == "set-mode":
                    out = store.set_mode(
                        str(body.get("mode")), requested_by=actor,
                        confirmed=confirmed,
                        scopes=tuple(body.get("scopes") or ()),
                        ttl_s=body.get("ttl"))
                elif action == "grant":
                    g = store.grant(
                        str(body.get("capability")), requested_by=actor,
                        confirmed=confirmed,
                        scopes=tuple(body.get("scopes") or ()),
                        ttl_s=body.get("ttl"),
                        note=str(body.get("note", ""))[:200])
                    out = {"state": "OK", "grant": g.to_dict()}
                elif action == "revoke":
                    g = store.revoke(str(body.get("grant_id")),
                                     requested_by=actor, confirmed=confirmed)
                    out = {"state": "OK", "revoked": g.to_dict()}
                elif action == "reset":
                    out = store.reset(requested_by=actor, confirmed=confirmed)
                elif action == "skill-install":
                    man = P.SkillManifest.from_dict(body.get("manifest") or {})
                    rec = store.install_skill(man, requested_by=actor,
                                              confirmed=confirmed)
                    out = {"state": "OK", "staged": rec.status,
                           "skill": rec.to_dict()}
                elif action == "skill-enable":
                    rec = store.enable_skill(str(body.get("skill_id")),
                                             requested_by=actor,
                                             confirmed=confirmed)
                    out = {"state": "OK", "status": rec.status,
                           "skill_id": rec.skill_id}
                elif action == "skill-disable":
                    rec = store.disable_skill(str(body.get("skill_id")),
                                              requested_by=actor,
                                              confirmed=confirmed)
                    out = {"state": "OK", "status": rec.status,
                           "skill_id": rec.skill_id}
                elif action == "skill-remove":
                    rec = store.remove_skill(str(body.get("skill_id")),
                                             requested_by=actor,
                                             confirmed=confirmed)
                    out = {"state": "OK", "removed": rec.skill_id}
                elif action == "skill-rollback":
                    rec = store.rollback_skill(str(body.get("skill_id")),
                                               requested_by=actor,
                                               confirmed=confirmed)
                    out = {"state": "OK", "status": rec.status,
                           "skill_id": rec.skill_id}
                else:
                    self._json({"state": "failed",
                                "detail": f"unknown action {action}"}, 400)
                    return
                self._json(out)
            except P.ConfirmationRequiredError as exc:
                self._json({"state": "denied", "code": "CONFIRMATION_REQUIRED",
                            "detail": str(exc)})
            except (P.PermissionError, P.SkillValidationError) as exc:
                self._json({"state": "failed", "detail": str(exc)})
            except Exception as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            return
        if path == "/api/websites":
            # Packet 8E: mutating website-integration actions (explicit confirm).
            # "add" persists a PENDING/disabled proposal and grants nothing.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import permissions as P
                from . import websites as W
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode() or "{}")
                store = W.website_integrations_store(self.app.orch.cfg)
                perms = P.permissions_store(self.app.orch.cfg)
                action = str(body.get("action", ""))
                confirmed = bool(body.get("confirmed", False))
                actor = "dashboard"
                ident = str(body.get("id_or_url") or body.get("id") or "")
                if action == "add":
                    if bool(body.get("dry_run")):
                        out = W.add_preview(str(body.get("url", "")),
                                            name=str(body.get("name", "")))
                    else:
                        rec = store.add(str(body.get("url", "")),
                                        requested_by=actor,
                                        display_name=str(body.get("name", "")))
                        out = {"state": "OK", "status": rec.status,
                               "granted": False,
                               "integration": rec.to_dict()}
                elif action == "inspect":
                    rec = store.lookup(ident)
                    if rec is None:
                        self._json({"state": "MISSING",
                                    "detail": "no such integration"}, 404)
                        return
                    rec2 = store.run_discovery(rec.integration_id,
                                               requested_by=actor)
                    out = {"state": "OK", "status": rec2.status,
                           "reason": rec2.health.get("reason_code", ""),
                           "integration": rec2.to_dict()}
                elif action == "check":
                    out = store.check(ident, requested_by=actor, perms=perms)
                elif action == "enable":
                    rec = store.enable(ident, requested_by=actor,
                                       confirmed=confirmed, perms=perms)
                    out = {"state": "OK", "status": rec.status,
                           "enabled": rec.enabled, "integration": rec.to_dict()}
                elif action == "manifest-link":
                    rec = store.link_manifest(
                        ident, skill_id=str(body.get("skill_id", "")),
                        requested_by=actor, confirmed=confirmed, perms=perms)
                    out = {"state": "OK", "manifest_link": rec.manifest_link,
                           "license": rec.license,
                           "content_hash": rec.content_hash,
                           "integration": rec.to_dict()}
                elif action == "disable":
                    rec = store.disable(ident, requested_by=actor,
                                        confirmed=confirmed)
                    out = {"state": "OK", "status": rec.status,
                           "enabled": rec.enabled, "integration": rec.to_dict()}
                elif action == "grant":
                    out = store.grant(ident, requested_by=actor,
                                      confirmed=confirmed,
                                      capabilities=tuple(
                                          body.get("capabilities") or ()),
                                      ttl_s=body.get("ttl"), perms=perms)
                elif action == "revoke":
                    out = store.revoke(ident, requested_by=actor,
                                       confirmed=confirmed, perms=perms)
                elif action == "remove":
                    out = store.remove(ident, requested_by=actor,
                                       confirmed=confirmed, perms=perms)
                elif action == "rollback":
                    out = store.rollback(ident, requested_by=actor,
                                         confirmed=confirmed)
                else:
                    self._json({"state": "failed",
                                "detail": f"unknown action {action}"}, 400)
                    return
                self._json(out)
            except W.ConfirmationRequiredError as exc:
                self._json({"state": "denied", "code": "CONFIRMATION_REQUIRED",
                            "detail": str(exc)})
            except (W.WebsiteIntegrationError, W.WebsiteValidationError) as exc:
                self._json({"state": "failed", "detail": str(exc)})
            except (P.PermissionError, P.SkillValidationError) as exc:
                self._json({"state": "failed", "detail": str(exc)})
            except Exception as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            return
        if path == "/api/actions":
            # Packet 8F: mutating action operations (explicit confirm).
            # Sending/publishing/purchasing/deleting/account/system actions
            # require confirmed=True (fresh human confirmation) or a
            # confirmation id from `prepare`; the backend refuses anything else.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import actions as A
                from pathlib import Path as _Path
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode() or "{}")
                action = str(body.get("action", ""))
                confirmed = bool(body.get("confirmed", False))
                confirmation = body.get("confirmation") or None
                actor = "dashboard"
                executors: Dict[str, Any] = {}
                fixture = body.get("fixture") or None
                if fixture:
                    fdir = (_Path(__file__).resolve().parents[1]
                            / "tests" / "fixtures" / "actions")
                    fpath = _Path(str(fixture)).expanduser()
                    try:
                        fpath.resolve().relative_to(fdir.resolve())
                    except Exception:
                        self._json({"state": "denied",
                                    "code": "FIXTURE_OUT_OF_BOUNDS",
                                    "detail": "dashboard fixtures must live "
                                              "under tests/fixtures/actions"},
                                   403)
                        return
                    if not fpath.is_file():
                        self._json({"state": "failed",
                                    "detail": f"fixture not found: "
                                              f"{fpath.name}"}, 404)
                        return
                    executors = A.make_fixture_executors(
                        A.load_fixture_outcomes(fpath))
                runner = A.action_runner(self.app.orch.cfg,
                                     executors=executors,
                                     power=_power_policy(
                                         self.app.orch.cfg))
                if action == "preview":
                    out = runner.preview(
                        str(body.get("action_id", "")),
                        body.get("args") or {},
                        target=str(body.get("target", "")),
                        account_scope=str(body.get("account_scope", "")),
                        scope=str(body.get("scope", "")),
                        idempotency_key=str(body.get("idempotency_key", "")))
                elif action == "prepare":
                    out = runner.prepare(
                        str(body.get("action_id", "")),
                        body.get("envelope") or {}, issued_by=actor)
                elif action == "execute":
                    out = runner.execute(
                        str(body.get("action_id", "")),
                        args=body.get("args"),
                        envelope=body.get("envelope"),
                        target=str(body.get("target", "")),
                        account_scope=str(body.get("account_scope", "")),
                        scope=str(body.get("scope", "")),
                        idempotency_key=str(body.get("idempotency_key", "")),
                        source="user", confirmation_id=confirmation,
                        confirmed=confirmed,
                        dry_run=bool(body.get("dry_run", False)),
                        expected_hash=str(body.get("expected_hash", "")))
                elif action == "cancel":
                    out = runner.cancel(str(body.get("receipt_id", "")))
                elif action == "retry":
                    out = runner.retry(
                        str(body.get("receipt_id", "")),
                        args=body.get("args") or {},
                        target=str(body.get("target", "")),
                        scope=str(body.get("scope", "")),
                        account_scope=str(body.get("account_scope", "")),
                        source="user", confirmation_id=confirmation,
                        confirmed=confirmed)
                elif action == "recover":
                    out = runner.recover(str(body.get("receipt_id", "")),
                                         confirmation_id=confirmation,
                                         confirmed=confirmed)
                else:
                    self._json({"state": "failed",
                                "detail": f"unknown action {action}"}, 400)
                    return
                self._json(out)
            except (A.ActionError, A.ActionValidationError,
                    A.ActionDeniedError) as exc:
                code = getattr(exc, "code", None) or "ACTION_ERROR"
                self._json({"state": "failed", "code": code,
                            "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            return
        if path in ("/api/ops/run", "/api/ops/confirm", "/api/ops/cancel",
                     "/api/approvals/respond"):
            # Packet 8T-F: operations console execution. Same backend fns,
            # same confirmation journal, same emergency stop, same audit and
            # redaction as the CLI (`voice say`) and microphone paths.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import dashboard_ops as OPS
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode() or "{}")
                cfg = self.app.orch.cfg
                if path == "/api/ops/run":
                    args = body.get("args")
                    out = OPS.run_op(cfg, str(body.get("id", "")),
                                     dict(args) if isinstance(args, dict)
                                     else {},
                                     actor="dashboard:user",
                                     session="dashboard")
                elif path == "/api/ops/confirm":
                    sensitive = body.get("sensitive")
                    out = OPS.confirm_op(
                        cfg, str(body.get("confirmation_id", "")),
                        actor="dashboard:user", session="dashboard",
                        sensitive=dict(sensitive)
                        if isinstance(sensitive, dict) else None)
                elif path == "/api/approvals/respond":
                    # Approvals page: approve executes the staged backend
                    # exactly once (same path as ops confirm + CLI
                    # approvals respond); deny settles without executing.
                    # The record's own session is honored so items staged
                    # by voice, CLI, or dashboard all settle identically.
                    cid = str(body.get("id") or
                              body.get("confirmation_id") or "")
                    decision = str(body.get("decision") or "")
                    session = OPS.record_session(cfg, cid)
                    if decision == "deny":
                        out = OPS.cancel_op(cfg, cid or None,
                                            actor="dashboard:user",
                                            session=session)
                    else:
                        sensitive = body.get("sensitive")
                        out = OPS.confirm_op(
                            cfg, cid, actor="dashboard:user",
                            session=session,
                            sensitive=dict(sensitive)
                            if isinstance(sensitive, dict) else None)
                else:
                    cid = body.get("confirmation_id") or None
                    out = OPS.cancel_op(cfg, str(cid) if cid else None,
                                        actor="dashboard:user",
                                        session="dashboard")
                self._json(out)
            except Exception as exc:
                self._json({"state": "failed",
                            "detail": redact_obj(str(exc))[:200]}, 500)
            return
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
                # result (audio) is fetched via /api/result/<id>. The browser
                # sends its ACTUAL AudioContext sample rate — Chrome does not
                # reliably honor the requested 16 kHz.
                rate = parse_audio_rate(self.headers.get("X-Audio-Rate"))
                rid = self.app.submit_talk(pcm, rate=rate)
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
        elif path.startswith("/api/runs/") and path.endswith("/cancel"):
            # Packet 2B: cancellation emits exactly ONE terminal event per run
            # (idempotent across restarts via the journal) and best-effort
            # aborts the active orchestrator task when the ids match.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            run_id = path[len("/api/runs/"):-len("/cancel")]
            if not run_id or self.app.runs is None:
                self._json({"error": "run coordinator unavailable"}, 503)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode() or "{}") \
                    if length else {}
            except Exception:
                body = {}
            emitted = self.app.runs.cancel(
                run_id, reason=str(body.get("reason") or "user"))
            self._json({"ok": True, "run_id": run_id,
                        "terminal_emitted": bool(emitted)})
        elif path == "/api/profiles/apply":
            # Packet 7D: explicit, confirmed profile activation (dry-run or
            # apply via body). Never trusts a provider without gates.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            kind = body.get("kind")
            profile = body.get("profile")
            if kind not in ("brain", "stt", "tts", "voice", "audio") or not profile:
                self._json({"state": "MISSING",
                            "detail": "kind + profile required"}, 400)
                return
            dry = bool(body.get("dry_run", True))
            confirm = bool(body.get("confirm", False))
            out = P.apply_profile(self.app.orch.cfg, kind, profile,
                                  dry_run=dry, require_confirmation=not confirm)
            code = 200 if out["state"] in ("OK", "DENIED", "PARTIAL") else \
                (400 if out["state"] == "MISSING" else 409)
            self._json(out, code)
        elif path == "/api/profiles/reset":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            kind = body.get("kind")
            if kind not in ("brain", "stt", "tts", "voice", "audio"):
                self._json({"state": "MISSING", "detail": "kind required"}, 400)
                return
            out = P.reset_profile(self.app.orch.cfg, kind,
                                  dry_run=not bool(body.get("apply", False)))
            self._json(out, 200 if out["state"] == "OK" else 409)
        elif path == "/api/profiles/upgrade":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            kind = body.get("kind")
            profile = body.get("profile")
            if kind not in ("brain", "stt", "tts", "voice", "audio"):
                self._json({"state": "MISSING", "detail": "kind required"}, 400)
                return
            out = P.upgrade_profile(self.app.orch.cfg, kind, profile or "",
                                    dry_run=not bool(body.get("apply", False)))
            self._json(out, 200 if out["state"] == "OK" else 409)
        elif path == "/api/profiles/rollback":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            kind = body.get("kind")
            if kind not in ("brain", "stt", "tts", "voice", "audio"):
                self._json({"state": "MISSING", "detail": "kind required"}, 400)
                return
            out = P.rollback_profile(self.app.orch.cfg, kind,
                                     dry_run=not bool(body.get("apply", False)),
                                     backup=None)
            self._json(out, 200 if out["state"] == "OK" else 409)
        elif path == "/api/profiles/remove":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import profiles as P
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            kind = body.get("kind")
            profile = body.get("profile")
            if kind not in ("brain", "stt", "tts", "voice", "audio") or not profile:
                self._json({"state": "MISSING",
                            "detail": "kind + profile required"}, 400)
                return
            out = P.remove_profile(self.app.orch.cfg, kind, profile,
                                   dry_run=not bool(body.get("apply", False)))
            self._json(out, 200 if out["state"] == "OK" else 409)
        elif path == "/api/audio/reset":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from .audio import settings as AS
            try:
                bak = AS.reset_to_defaults(self.app.orch.cfg)
                self._json({"state": "OK", "detail": "reset to OS defaults",
                            "backup": str(bak)})
            except Exception as exc:
                self._json({"state": "FAILED", "detail": str(exc)[:160]}, 409)
        elif path == "/api/power":
            # Packet 8O: mutating power-control/grant actions (explicit confirm).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import power as PW
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode() or "{}")
                action = str(body.get("action", ""))
                confirmed = bool(body.get("confirmed", False))
                store = PW.power_grant_store(self.app.orch.cfg)
                control = PW.power_control(self.app.orch.cfg)
                if action == "grant":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "grant requires explicit "
                                              "confirmation"})
                        return
                    g = store.issue(
                        browsers=tuple(body.get("browsers") or ()),
                        apps=tuple(body.get("apps") or ()),
                        accounts=tuple(body.get("accounts") or ()),
                        origins=tuple(body.get("origins") or ()),
                        directories=tuple(body.get("directories") or ()),
                        scopes=tuple(body.get("scopes") or ()),
                        capabilities=tuple(body.get("capabilities") or ()),
                        ttl_s=body.get("ttl"),
                        note=str(body.get("note", ""))[:2000],
                        issued_by="dashboard")
                    self._json({"state": "OK", "grant": g.to_dict()})
                elif action == "revoke":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "revoke requires explicit "
                                              "confirmation"})
                        return
                    g = store.revoke(str(body.get("grant_id", "")),
                                     issued_by="dashboard")
                    self._json({"state": "OK", "revoked": g.to_dict()})
                elif action == "reset-grant":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "reset-grant requires explicit "
                                              "confirmation"})
                        return
                    store.reset(issued_by="dashboard")
                    self._json({"state": "OK", "grant": None})
                elif action == "emergency-stop":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "emergency stop requires explicit "
                                              "confirmation"})
                        return
                    self._json(control.emergency_stop(issued_by="dashboard"))
                elif action == "reset":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "reset requires explicit human "
                                              "confirmation"})
                        return
                    self._json(control.reset(issued_by="dashboard"))
                elif action == "disable":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "disable requires explicit "
                                              "confirmation"})
                        return
                    self._json(control.global_disable(issued_by="dashboard"))
                elif action == "enable":
                    if not confirmed:
                        self._json({"state": "denied",
                                    "code": "CONFIRMATION_REQUIRED",
                                    "detail": "enable requires explicit "
                                              "confirmation"})
                        return
                    self._json(control.global_enable(issued_by="dashboard"))
                else:
                    self._json({"state": "failed",
                                "detail": f"unknown action {action}"}, 400)
            except PW.PowerDenied as exc:
                self._json({"state": "denied", "code": exc.code,
                            "detail": exc.detail})
            except PW.PowerError as exc:
                self._json({"state": "failed",
                            "code": getattr(exc, "code", "POWER_ERROR"),
                            "detail": str(exc)[:200]})
            except Exception as exc:
                self._json({"state": "failed", "detail": str(exc)[:200]})
            return
        elif path == "/api/device/enroll":
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import device as Dev
                rec = Dev.enroll(self.app.orch.cfg, require_new_authorization=False)
                self._json({"enrolled": True, "detail": "device enrolled",
                            "machine_id": rec.get("machine_id")})
            except Exception as exc:
                self._json({"enrolled": False, "detail": str(exc)[:160]}, 403)
        elif path == "/api/setup/run":
            # Packet 8T: staged setup run. Dry-run preview by default;
            # mutations need explicit {"apply": true, "confirm": true}.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import lifecycle as LC
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            raw = body.get("steps") or []
            # Pass the raw list through: unknown steps must fail closed in
            # the backend, never be silently dropped here.
            wanted = [str(s) for s in raw] or None
            out = LC.setup_run(
                steps=wanted,
                dry_run=not bool(body.get("apply", False)),
                confirm=bool(body.get("confirm", False)))
            code = 200 if out["state"] in ("SUCCESS", "PENDING") else 409
            self._json(out, code)
        elif path == "/api/setup/export":
            # Packet 8T: redacted config-profile export behind the same
            # backend contract as the CLI (preview unless apply+confirm).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import lifecycle as LC
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            raw_out = body.get("out")
            try:
                state_dir = self.app.orch.cfg.state_dir()
            except Exception:
                state_dir = None
            if raw_out:
                try:
                    from pathlib import Path as _P
                    cand = _P(str(raw_out))
                    if not cand.is_absolute() and state_dir is not None:
                        cand = _P(str(state_dir)) / cand
                    ok = (state_dir is not None
                          and _P(str(state_dir)).resolve() in
                          cand.resolve().parents)
                    if not ok:
                        self._json({"state": "DENIED",
                                    "detail": "export path must stay inside "
                                              "the state directory"}, 403)
                        return
                    dest = cand
                except Exception:
                    self._json({"state": "FAILED",
                                "detail": "unusable export path"}, 400)
                    return
            else:
                from pathlib import Path as _P
                dest = _P(str(state_dir)) / "config-profile.json" \
                    if state_dir else _P("config-profile.json")
            out = LC.config_export(self.app.orch.cfg, dest,
                                   dry_run=not bool(body.get("apply", False)),
                                   confirm=bool(body.get("confirm", False)))
            code = 200 if out["state"] in ("SUCCESS", "PENDING") else \
                (403 if out["state"] == "DENIED" else 409)
            self._json(out, code)
        elif path == "/api/export-state":
            # Packet 7D: dashboard export calls the real encrypted exporter.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                from . import portable as P
                from . import device as Dev
                try:
                    Dev.require_enrolled(self.app.orch.cfg)
                except Dev.DeviceNotEnrolledError as exc:
                    self._json({"state": "DENIED",
                                "detail": str(exc)[:160]}, 403)
                    return
                out = self.app.orch.cfg.state_dir() / \
                    f"portable-{int(__import__('time').time())}.vbp"
                # explicit passphrase: read from the request, never stored
                try:
                    body = json.loads(
                        self.rfile.read(int(self.headers.get("Content-Length", 0))
                                        if self.headers.get("Content-Length") else 0)
                        or b"{}")
                except Exception:
                    body = {}
                pw = str(body.get("passphrase") or "")
                if not pw:
                    self._json({"state": "DENIED",
                                "detail": "passphrase required"}, 403)
                    return
                bundle = P.export_bundle(self.app.orch.cfg, pw)
                out.write_bytes(bundle)
                self._json({"state": "OK", "detail": str(out)})
            except Exception as exc:
                self._json({"state": "FAILED", "detail": str(exc)[:160]}, 409)
        elif path == "/api/import-state":
            # Packet 7D: dashboard import — restore a portable-state bundle
            # (b64 in the body) with an explicit passphrase; dry-run by
            # default, --apply/apply:true performs a real (backed-up, atomic,
            # rollback-safe) restore after the enrollment + integrity gates.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            from . import portable as P
            from . import device as Dev
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))
                                    if self.headers.get("Content-Length") else 0)
                    or b"{}")
            except Exception:
                self._json({"state": "FAILED", "detail": "bad JSON body"}, 400)
                return
            b64 = body.get("bundle_b64")
            pw = str(body.get("passphrase") or "")
            if not b64 or not pw:
                self._json({"state": "DENIED",
                            "detail": "bundle + passphrase required"}, 403)
                return
            try:
                import base64 as _b64
                bundle = _b64.b64decode(b64)
            except Exception:
                self._json({"state": "FAILED", "detail": "bundle not base64"},
                           400)
                return
            try:
                try:
                    Dev.require_enrolled(self.app.orch.cfg)
                except Dev.DeviceNotEnrolledError as exc:
                    self._json({"state": "DENIED", "detail": str(exc)[:160]},
                               403)
                    return
                payload = P.read_bundle(bundle, pw)
                apply = bool(body.get("apply", False))
                if not apply:
                    plan = P.plan_restore(self.app.orch.cfg, payload,
                                          dry_run=True)
                    self._json({"state": "OK", "dry_run": True,
                                "detail": "preview", "actions": plan.actions})
                    return
                result = P.restore_state(self.app.orch.cfg, payload,
                                         dry_run=False)
                self._json({"state": "OK", "detail": "restored",
                            "backup": result.get("backup")})
            except P.WrongPassphraseError:
                self._json({"state": "FAILED",
                            "detail": "wrong passphrase"}, 403)
            except P.CorruptedBundleError as exc:
                self._json({"state": "FAILED", "detail": str(exc)[:160]}, 400)
            except P.UnsupportedSchemaError as exc:
                self._json({"state": "FAILED", "detail": str(exc)[:160]}, 400)
            except P.LowDiskError as exc:
                self._json({"state": "FAILED", "detail": str(exc)[:160]}, 409)
            except Exception as exc:
                self._json({"state": "FAILED", "detail": str(exc)[:160]}, 409)
        elif path == "/api/prune":
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
        elif path == "/api/reports/prune":
            # Packet 8C: report-bundle retention (dry-run by default; `apply`
            # actually removes EXPIRED bundles only). Never touches cursors,
            # memory, evidence, legacy .md files, or user data.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)) if self.headers.get("Content-Length") else 0) or b"{}"
                data = __import__("json").loads(body or b"{}")
                days = int(data.get("days", 90))
                apply_ = bool(data.get("apply", False))
            except Exception:
                days, apply_ = 90, False
            from .communications.reports import ReportStore
            rstore = ReportStore(self.app.orch.cfg.state_dir() / "reports")
            out = rstore.retention(dry_run=not apply_, retention_days=days)
            # propagate the REAL retention state (OK/BLOCKED/FAILED/DEGRADED) —
            # never a top-level OK when the operation failed or was blocked.
            self._json(out)
        elif path.startswith("/api/reports/") and path.endswith("/ack"):
            # Packet 8C: acknowledge a report (atomic manifest flag).
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            report_id = path[len("/api/reports/"):-len("/ack")]
            from .communications.reports import ReportStore, ReportStoreError
            rstore = ReportStore(self.app.orch.cfg.state_dir() / "reports")
            try:
                ok = rstore.acknowledge(report_id)
            except ReportStoreError as exc:
                self._json({"state": "failed", "detail": str(exc)[:160]}, 400)
                return
            self._json({"state": "OK" if ok else "MISSING",
                        "acknowledged": ok})
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
        elif path in ("/api/lessons/remove", "/api/lessons/promote"):
            # Packet 6A: explicit lesson removal / promotion. Only review or
            # staged model-inference items can be promoted; everything is
            # tombstoned with stored provenance, never deleted wholesale.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length).decode() or "{}")
            except Exception:
                data = {}
            lid = str(data.get("lesson_id") or "").strip()
            led = getattr(getattr(self.app, "orch", None), "learning", None)
            if led is None or not lid:
                self._json({"error": "unknown lesson id"}, 404)
                return
            if path.endswith("/remove"):
                if not led.remove(lid):
                    self._json({"error": "unknown lesson id"}, 404)
                    return
                log_event("lessons.remove", lesson_id=lid[:16])
                self._json({"ok": True, "lesson_id": lid})
            else:
                promoted = led.promote(lid)
                if promoted is None:
                    self._json({"error": "unknown lesson id"}, 404)
                    return
                log_event("lessons.promote", lesson_id=lid[:16])
                self._json({"ok": True, "lesson_id": lid,
                            "lesson": promoted})
        elif path == "/api/feedback":
            # CLHF: user rates the last answer (good/bad) or offers a correction.
            if not self._api_token_ok():
                self._send(401, b"unauthorized")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            try:
                data = json.loads(body or b"{}")
            except Exception:
                data = {}
            fb = str(data.get("feedback", "")).lower()
            if fb not in ("good", "bad", "correction"):
                self._json({"error": "feedback must be good|bad|correction"}, 400)
                return
            try:
                _orch = getattr(self.app, "orch", None)
                if _orch is not None and hasattr(_orch, "learning"):
                    _orch.learning.record(
                        user_text=str(data.get("user", ""))[:300],
                        answer=str(data.get("answer", ""))[:500],
                        feedback=fb,
                        corrected=str(data.get("corrected", ""))[:500])
                # Packet 6B: every correction/rating is also a structured
                # task outcome (approval/retry/correction/completion), the raw
                # material for the private held-out evaluation set.
                _oc = getattr(_orch, "task_outcomes", None)
                if _oc is not None:
                    try:
                        _oc.record(
                            request_id=str(data.get("request_id", ""))[:64],
                            intent_kind="conversation", route="conversation",
                            tool_status="",
                            approval="",
                            correction=(fb == "correction"),
                            completed=(fb == "good"),
                            outcome=fb,
                            user_text=str(data.get("user", ""))[:300],
                        )
                    except Exception:
                        pass
                # rich log line so the dashboard Logs view shows WHAT was rated
                log_event("feedback.recorded", feedback=fb,
                          user=str(data.get("user", ""))[:80],
                          answer=str(data.get("answer", ""))[:120],
                          corrected=bool(str(data.get("corrected", "")).strip()))
                self._json({"ok": True, "feedback": fb})
            except Exception as exc:
                log_event("feedback.error", error=str(exc)[:160])
                self._json({"error": str(exc)[:200]}, 500)
        elif path.startswith("/hooks/") or path.startswith("/hermes/hooks/"):
            # Signed inbound webhook delivery. Authentication is the HMAC
            # signature, never the dashboard token; unsigned/unknown requests
            # fail closed with a typed code.
            self._webhook_ingress(path)
        elif path.startswith("/channels/") \
                or path.startswith("/hermes/channels/"):
            # Signed inbound channel delivery (same kernel as webhooks).
            # Authentication is the per-channel HMAC signature, never the
            # dashboard token; unsigned/unknown requests fail closed typed.
            self._channel_ingress(path)
        elif path.startswith("/hermes/api/"):
            # Vendored Hermes dashboard POST backend (compat, same modules
            # as CLI/voice). Auth: X-Hermes-Session-Token or X-Api-Token.
            self._hermes_api("POST")
        else:
            self._send(404, b"not found")

    def do_PUT(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/hermes/api/"):
            self._hermes_api("PUT")
        else:
            self._send(404, b"not found")

    def do_PATCH(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/hermes/api/"):
            self._hermes_api("PATCH")
        else:
            self._send(404, b"not found")

    def do_DELETE(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/hermes/api/"):
            self._hermes_api("DELETE")
        else:
            self._send(404, b"not found")


REPO_FRONTEND = resources.resource_path("frontend")


# Curated user guide for the dashboard /docs page (Hermes DocsPage
# equivalent). Allowlisted files only: user-facing, secret-scanned, no
# machine-specific paths. Served from the installed package data when
# present, else the repository docs/ checkout; absent in minimal
# installs -> typed UNAVAILABLE (503), never a fabricated page.
USER_DOCS = (
    ("howto", "How to use Qubit", "HowTo.md"),
    ("audio", "Audio setup", "AUDIO_SETUP.md"),
    ("system", "VoiceBridge system", "VOICE_BRIDGE_SYSTEM.md"),
    ("migration", "Migration guide", "QUBIT_MIGRATION.md"),
)

MAX_DOC_BYTES = 65536


def _docs_dir() -> Optional[Path]:
    installed = resources.package_data_root() / "docs"
    try:
        if (installed / "HowTo.md").is_file():
            return installed
    except OSError:
        pass
    repo = Path(__file__).resolve().parents[1] / "docs"
    try:
        if (repo / "HowTo.md").is_file():
            return repo
    except OSError:
        pass
    return None


def user_docs_index() -> Dict[str, Any]:
    docs = [{"id": doc_id, "title": title} for doc_id, title, _ in USER_DOCS]
    here = _docs_dir()
    return {"docs": docs,
            "state": "OK" if here is not None else "UNAVAILABLE"}


def user_doc(doc_id: str) -> Dict[str, Any]:
    for want_id, title, filename in USER_DOCS:
        if want_id == doc_id:
            break
    else:
        raise KeyError(doc_id)
    here = _docs_dir()
    if here is None:
        raise FileNotFoundError(doc_id)
    text = (here / filename).read_text(encoding="utf-8",
                                       errors="replace")[:MAX_DOC_BYTES + 1]
    truncated = len(text) > MAX_DOC_BYTES
    return {"id": want_id, "title": title,
            "markdown": text[:MAX_DOC_BYTES], "truncated": truncated}


def _frontend_version(frontend_dir: Path) -> str:
    """Read a frontend checkout's VERSION marker (empty when absent)."""
    try:
        return (Path(frontend_dir) / "VERSION").read_text().strip()
    except OSError:
        return ""


def resolve_frontend_dir(explicit: Optional[str]):
    """Resolve the ACTIVE dashboard frontend directory (Packet 8E serving fix).

    Returns ``(active_path, serves_index)`` — the active path after the
    stale/version check (a stale or unversioned external checkout falls back to
    the repository frontend). Use ``frontend_status`` for the full state.
    """
    p, serves, _stale, _ver, _active = frontend_status(explicit)
    return p, serves


def frontend_status(explicit: Optional[str]):
    """Full frontend resolution state (Packet 8E serving fix).

    Order:
      - the repository ``frontend/`` is the canonical current UI and carries a
        ``VERSION`` marker;
      - an explicit ``webui.frontend_dir`` is honored ONLY when it serves
        ``index.html`` AND carries a ``VERSION`` marker equal to the repository
        version (i.e. it is a current checkout). A stale, unversioned, or
        version-mismatched external checkout is NOT served: we safely fall back
        to the repository frontend and report ``stale=True`` so the operator can
        update the external checkout or remove the override. The external
        checkout itself is never modified or overwritten.

    Returns ``(path, serves_index, stale, version, active)`` where ``active`` is
    ``"repo"`` or ``"external"``.
    """
    repo_version = _frontend_version(REPO_FRONTEND)
    if explicit:
        p = Path(explicit).expanduser()
        try:
            if (p / "index.html").is_file():
                ext_version = _frontend_version(p)
                if ext_version and repo_version and ext_version == repo_version:
                    return p, True, False, repo_version, "external"
                # stale / unversioned / mismatched external checkout -> safe
                # repository fallback (never serve an outdated UI).
                return REPO_FRONTEND, True, True, repo_version, "repo"
        except OSError:
            pass
    return REPO_FRONTEND, (REPO_FRONTEND / "index.html").is_file(), \
        False, repo_version, "repo"


def token_ok(cfg_token: str, header_token: str,
             query_token_present: bool) -> tuple[bool, str]:
    """Packet 1B auth contract. Query-string tokens are ALWAYS rejected.
    With no configured token only loopback callers may pass (caller checks
    peer address); with a configured token the X-Api-Token header must match."""
    if query_token_present:
        return False, "query-token-rejected"
    if not cfg_token:
        return True, "loopback-open"
    return (header_token == cfg_token), ("ok" if header_token == cfg_token
                                         else "bad-or-missing-header")


def cors_headers_for(allowed_origins, request_origin: str) -> list:
    """Configured-origin CORS: no header without an exact configured match."""
    origins = [o for o in (allowed_origins or []) if o and o != "*"]
    if request_origin and request_origin in origins:
        return [("Access-Control-Allow-Origin", request_origin),
                ("Access-Control-Allow-Headers", "Content-Type, X-Api-Token"),
                ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                ("Vary", "Origin")]
    return []


class WebUI:
    """Thin HTTP layer around the orchestrator's processing pipeline."""

    def __init__(self, orchestrator: Any, host: Optional[str] = None, port: int = 8890,
                 asr_factory: Any = None, tts: Any = None,
                 state_dir: Optional[Path] = None) -> None:
        self.orch = orchestrator
        cfg_host = orchestrator.cfg.get("webui", "host") if orchestrator is not None else None
        self.host = host or cfg_host or "127.0.0.1"   # loopback by default (Packet 1B)
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
        # Dashboard frontend: optional webui.frontend_dir config key is honored
        # ONLY when it is a current (VERSION-matched) checkout; a stale or
        # unversioned external frontend safely falls back to the repository
        # frontend so the current Packet 8E Websites/Permissions UI is served.
        self._frontend_dir, self._use_frontend_dir, self._frontend_stale, \
            self._frontend_version, self._frontend_active = frontend_status(
                orchestrator.cfg.get("webui", "frontend_dir")
                if orchestrator is not None else None)
        if getattr(self, "_frontend_stale", False):
            log_event("webui.frontend.stale",
                      configured=bool(orchestrator is not None and
                                      orchestrator.cfg.get("webui", "frontend_dir")),
                      version=self._frontend_version, active="repo",
                      detail="configured webui.frontend_dir is stale, unversioned, "
                             "or version-mismatched; serving the repository "
                             "frontend (Packet 8E safe fallback)")
        # optional shared secret for tunneled/external access (config: webui.token)
        self.api_token: str = orchestrator.cfg.get("webui", "token", default="") if orchestrator is not None else ""
        self.allowed_origins = orchestrator.cfg.get("webui", "allowed_origins", default=[]) if orchestrator is not None else []
        _sd = state_dir or Path("/tmp")
        try:
            from .approvals import ApprovalJournal
            self.approvals = ApprovalJournal(_sd / "approvals.jsonl")
        except Exception:
            self.approvals = None
        # Packet 2B: typed event stream (journal + hub + run coordination).
        # Additive: the legacy events.jsonl activity journal is untouched.
        self._events_hub: Optional[Any] = None
        try:
            from .events import EventJournal, EventHub, make_event
            from .events.runs import RunCoordinator, StateTracker
            typed = EventJournal(_sd / "typed-events.jsonl")
            self._events_hub = EventHub(typed)
            self.runs = RunCoordinator(
                self._events_hub,
                abort_hook=self._abort_hook_for_cancel())
            self.state_tracker = StateTracker(self._events_hub)
            if self.approvals is not None:
                hub = self._events_hub

                def _approval_sink(name: str, item: Any) -> None:
                    hub.publish(make_event(
                        "CUSTOM", name=name,
                        value={"approval_id": item.id,
                               "capability": item.capability,
                               "state": item.state,
                               "decision": item.decision}))

                self.approvals.event_sink = _approval_sink
        except Exception:
            log.exception("typed event stream init failed; SSE disabled")
            self._events_hub = None
            self.runs = None
            self.state_tracker = None

    def _abort_hook_for_cancel(self):
        """Best-effort hook: dashboard cancel also aborts the active task."""
        orch = self.orch

        def _hook(run_id: str) -> None:
            task = getattr(orch, "task", None)
            if task is not None and getattr(task, "run_id", None) == run_id \
                    and getattr(task, "active", False):
                abort = getattr(orch, "_abort_task", None)
                if abort is not None:
                    abort("dashboard-cancel")
        return _hook

    # -- Packet 2B repair: orchestrator -> typed stream integration hooks --

    def typed_task_started(self, session_key: str, run_id: str,
                           description: str = "", **_extra: Any) -> None:
        """RUN_STARTED + baseline snapshot for a real dispatched task."""
        if self.runs is None or not run_id:
            return
        self.runs.start(run_id, thread_id=session_key or "task")
        self.state_tracker.snapshot(
            run_id, {"stage": "running", "description": description[:200]})

    def typed_task_finished(self, session_key: str, run_id: str,
                            description: str = "", status: str = "ok",
                            detail: str = "", **_extra: Any) -> None:
        """Terminal event + final state delta for a real task result."""
        if self.runs is None or not run_id:
            return
        ok = status == "ok"
        self.state_tracker.update(run_id, {
            "stage": "done" if ok else "failed", "status": status})
        if ok:
            self.runs.finish(run_id, result={"status": status,
                                             "reply_len": len(detail)})
        else:
            self.runs.fail(run_id, message=detail[:300] or f"task {status}",
                           code=status)

    def typed_task_cancelled(self, session_key: str, run_id: str,
                             reason: str = "user", **_extra: Any) -> None:
        """Idempotent RUN_CANCELLED (single terminal, restart-safe)."""
        if self.runs is None:
            return
        self.runs.cancel(run_id, reason=reason)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        loopback = self.host in ("127.0.0.1", "localhost", "::1")
        if not loopback and not self.api_token:
            raise RuntimeError(
                "webui refuses to start: non-loopback host "
                f"'{self.host}' requires webui.token (Packet 1B fail-closed)")
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
        # Packet 8K closure: wake and close every event-stream subscriber FIRST
        # so in-flight SSE handler threads exit their drain loop instead of
        # blocking server_close() (ThreadingHTTPServer joins handler threads
        # when block_on_close is set; an open stream would otherwise stall the
        # accept loop and the test suite in selectors.py).
        hub = getattr(self, "_events_hub", None)
        if hub is not None:
            try:
                hub.close_all()
            except Exception:
                log.exception("webui.stop: event hub close_all failed")
        if self._server is not None:
            self._server.shutdown()
            # server_close() is required to actually release the listening
            # socket; shutdown() alone leaves it open (ResourceWarning + a real
            # fd leak across start/stop cycles in a long-lived process).
            self._server.server_close()
        th = getattr(self, "_thread", None)
        if th is not None and th is not threading.current_thread():
            th.join(timeout=2.0)

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
            "awaiting_input": bool(self.orch._needs_answer()) if hasattr(self.orch, "_needs_answer") else False,
            # Mic signal self-check from startup (typed HEALTHY/SILENT/DEGRADED/
            # UNAVAILABLE) so the dashboard shows a silent mic at a glance.
            "mic": getattr(self.orch, "_mic_selfcheck", None),
            "frontend": {
                "version": getattr(self, "_frontend_version", ""),
                "active": getattr(self, "_frontend_active", "repo"),
                "stale": bool(getattr(self, "_frontend_stale", False)),
                "configured": bool(getattr(getattr(self.orch, "cfg", None), "get", None) and
                                  self.orch.cfg.get("webui", "frontend_dir")),
            },
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
                # If the bridge speaks on the PC speakers (speak_web_locally), do NOT
                # also hand the browser an audio_b64 to play -> that's the "responds
                # twice" bug. The dashboard shows the text only.
                speak_locally = self._speak_locally()
                if summary and not speak_locally:
                    try:
                        wav = self._synthesize(summary)
                        result["audio_b64"] = base64.b64encode(wav).decode()
                    except Exception as exc:
                        log_event("webui.tts.failed", error=str(exc)[:160])
                else:
                    result["audio_b64"] = None
                result["awaiting_input"] = bool(self.orch._needs_answer()) if hasattr(self.orch, "_needs_answer") else False
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
            self._prune_results()

        threading.Thread(target=_run, daemon=True, name=f"webui-type-{rid}").start()
        return rid

    def submit_talk(self, pcm: bytes, rate: int = PCM_RATE) -> str:
        """Start a voice roundtrip in the background; returns a request id.

        The browser gets the id instantly (fast ack), then polls
        /api/result/<id>. The heavy work (ASR -> route -> worker
        -> local summary -> TTS) runs on a worker thread so the phone UI
        never blocks on a 30s Perplexity search. ``rate`` is the browser's
        ACTUAL AudioContext sample rate (defaults to the 16 kHz contract).
        """
        rid = uuid.uuid4().hex[:12]
        with self._pending_lock:
            self._pending[rid] = {"created": time.time()}

        def _run() -> None:
            try:
                result = self.handle_talk(pcm, rate=rate)
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
            self._prune_results()

        threading.Thread(target=_run, daemon=True, name=f"webui-talk-{rid}").start()
        return rid

    def _prune_results(self, keep: int = 50) -> None:
        """Bound the in-memory result map so the web server never grows unbounded."""
        with self._result_lock:
            if len(self._results) > keep:
                for k in list(self._results)[:-keep]:
                    self._results.pop(k, None)

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
            if since and float(ev.get("ts") or 0) <= since:
                continue   # exclusive watermark: never re-send the boundary item
            if ql and ql not in json.dumps(ev).lower():
                continue
            out.append(ev)
        return out[-limit:]

    def handle_talk(self, pcm: bytes, rate: int = PCM_RATE) -> Dict[str, Any]:
        """Full voice roundtrip: PCM -> text -> route -> summary -> TTS.

        ``rate`` is the browser's ACTUAL AudioContext sample rate. The audio is
        resampled to the 16 kHz ASR contract when needed, gated for energy
        (near-silence is rejected before ASR so it cannot hallucinate text), and
        only genuine speech is bounded-normalized.
        """
        t0 = time.time()
        if rate != PCM_RATE:
            try:
                pcm = resample_pcm_int16(pcm, rate)
            except Exception as exc:
                log_event("webui.talk.resample.error", error=str(exc)[:120])
                rate = PCM_RATE
        n_samples = len(pcm) // 2
        min_samples = max(800, int(PCM_RATE * 0.1))  # 100ms @16k
        if n_samples < min_samples:
            return self._typed_talk(
                "I didn't catch that. The recording was too short — please try again.",
                "SHORT", t0, push=True)
        rms = pcm_rms(pcm)
        if rms < self._web_min_rms():
            # Bounded energy/silence gate BEFORE ASR: pure or near-silent audio
            # must never reach the recognizer (it invents "thank you" on noise).
            log_event("webui.talk.no_speech", rms=round(rms, 5),
                      samples=n_samples)
            return self._typed_talk(
                "No speech detected. Please speak clearly into the microphone "
                "and try again.", "NO_SPEECH", t0, push=True, rms=rms)
        # Only real speech is normalized, with a bounded gain (never amplify
        # silence into speech — silence was already rejected above).
        pcm = normalize_int16_bounded(pcm)
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
            return self._typed_talk("I couldn't hear you. Try again.",
                                    "NO_TRANSCRIPT", t0, push=False)
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
        # If the bridge speaks on the PC speakers (speak_web_locally), don't also
        # hand the browser audio_b64 -> that's the "responds twice" bug.
        speak_locally = self._speak_locally()
        if summary and not speak_locally:
            events.append({"kind": "meta", "text": result.get("route", "route")})
            try:
                wav = self._synthesize(summary)
                audio_b64 = base64.b64encode(wav).decode()
            except Exception as exc:
                log_event("webui.tts.failed", error=str(exc)[:160])
        self.feed.push({"kind": "done", "text": text, "summary": summary,
                        "route": result.get("route", ""),
                        "latency_s": round(time.time() - t0, 2)})
        awaiting = bool(self.orch._needs_answer()) if hasattr(self.orch, "_needs_answer") else False
        return {
            "text": text,
            "route": result.get("route", ""),
            "confidence": result.get("confidence", ""),
            "summary": summary,
            "events": events,
            "audio_b64": audio_b64,
            "latency_s": round(time.time() - t0, 2),
            "awaiting_input": awaiting,
        }

    # -- internals ---------------------------------------------------------
    def _web_min_rms(self) -> float:
        """Energy floor for the dashboard PCM gate (float [-1,1] RMS scale)."""
        try:
            return float(self.orch.cfg.get("asr", "web_min_rms", default=0.0008))
        except Exception:
            return 0.0008

    def _typed_talk(self, summary: str, reason: str, t0: float, *,
                    push: bool, rms: Optional[float] = None) -> Dict[str, Any]:
        """Typed no-speech / too-short result with a clear recovery message.

        ``push`` records a redacted feed entry so the user sees a reason, and
        ``reason`` gives the dashboard a machine-readable typed outcome.
        """
        if push:
            self.feed.push({"kind": "user", "text": "(no speech)",
                            "route": "input", "reason": reason})
        out: Dict[str, Any] = {
            "text": "", "route": "none", "summary": summary, "reason": reason,
            "events": [], "audio_b64": None,
            "latency_s": round(time.time() - t0, 2),
        }
        if rms is not None:
            out["rms"] = round(rms, 5)
        return out

    def _speak_locally(self) -> bool:
        """True when the bridge plays TTS on the PC speakers (webui.speak_web_locally).
        When true, the dashboard must NOT also autoplay audio_b64 (responds twice)."""
        try:
            return bool(self.orch.cfg.get("webui", "speak_web_locally", default=True))
        except Exception:
            return True

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
        hotwords=cfg.get("asr", "hotwords", default=""),
        hotwords_score=float(cfg.get("asr", "hotwords_score", default=1.5)),
    )
