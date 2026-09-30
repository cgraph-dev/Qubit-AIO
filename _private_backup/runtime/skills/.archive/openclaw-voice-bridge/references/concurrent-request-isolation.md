# Concurrent Request Isolation — Right Ctrl vs Dashboard (2026-08-23 night, final sweep)

## Problem
Dashboard (`POST /api/type`, `/api/talk` worker threads) and Right Ctrl (hotkey main-thread `_process_utterance` → `_route_intent`) share one `VoiceOrchestrator` instance. Both call the same handlers with shared globals: `_last_web_text/summary/route`, `_web_spoken`, `_web_mode`, `conversation` (`ConversationState` max_turns=4), `executor_choice`, `confirmations`.

Symptoms: *"I can start them simultaneously and they conflict — delay or wrong answers"* and *English `How are you?` answered in Romanian when a Romanian request ran concurrently*.

Failed fix: wrapping `process_web_utterance` in a single `RLock` (`_web_lock`) serialized the whole request — including the 5–60s `Qubit` wait. Dashboard web-search (60s) then blocked Right Ctrl `How are you?` (0.3s) for a full minute → lag the user explicitly rejected: *"wouldn't this cause lag and delays?"*.

## Your Local Stack (no network on happy path)
```
mic Scarlett 2i2 (4ch, gain 8.0, 48→16k) → Silero VAD v6 (sherpa-onnx CPU)
  → Sherpa Parakeet-TDT v3 int8 (CPU, 0 VRAM, ~0.07s) → heuristic router (~0ms)
  ├─ local chat  → huihui_ai/llama3.2-abliterate:3b via Ollama (GPU 2.6 GB, 0.3–0.5s, keep_alive=-1)
  ├─ local action → pactl/code/playerctl (instant, instant time_date)
  ├─ Qubit task → gateway WS 127.0.0.1:18789 (only tasks, 1–12s)
  └─ fallback → Perplexity/DeepSeek (only when local down)
  → summarizer (local 3B, 5s timeout + extractive fallback) → Piper amy-medium (CPU) → speakers OR phone WAV
```
Dashboard just POSTs raw PCM; ASR still runs on your PC via Sherpa — no extra cloud hop. Ollama queues internally, but ~0.3s/turn is fine. The real races were on `_last_web_summary` (polled by `_web_wait_for_summary`), `_web_spoken` (global list read by `webui.py` submit_text/handle_talk), `conversation.as_prompt()` (Romanian history bleeding), and the conversation return dict (read global `_last_web_summary` instead of `ctx.summary`).

## Fix — thread-local per-request isolation (non-blocking)

**Module-level (orchestrator.py top):**
```python
@dataclass
class _WebCtx:
    text: str = ""
    summary: str = ""
    route: str = ""
    spoken: List[str] = field(default_factory=list)
    web_mode: bool = True
    done: threading.Event = field(default_factory=threading.Event)
    conv_snapshot: str = ""  # snapshot of conversation.as_prompt() at arrival

_thread_ctx: threading.local = threading.local()
_conv_lock = threading.RLock()  # guards ConversationState.add / as_prompt
```

**Class-level helpers (VoiceOrchestrator):**
```python
def _cur_ctx(self) -> Optional[_WebCtx]: return getattr(_thread_ctx, "ctx", None)
def _new_ctx(self, text="") -> _WebCtx:
    with _conv_lock: snap = self.conversation.as_prompt()
    ctx = _WebCtx(text=text, conv_snapshot=snap)
    _thread_ctx.ctx = ctx; return ctx
def _clear_ctx(self): ...
_web_lock = threading.RLock()  # keeps status.busy + legacy global handoff brief
_web_active: int = 0
```

**process_web_utterance:** creates `_new_ctx(text)`, increments `_web_active` under lock, then runs `_process_web_utterance_inner` *outside* the lock except for final mirrors. Shares `ctx.spoken` via `self._web_spoken = ctx.spoken` so `speak()` appends to `ctx.spoken` (isolated), and restores globals after.
```python
ctx = self._new_ctx(text)
with self._web_lock: self._web_active += 1
prev_mode, prev_spoken = self._web_mode, self._web_spoken
self._web_mode = True; self._web_spoken = ctx.spoken
try:
    result = self._process_web_utterance_inner(text)
    self._last_web_text = ctx.text
    if ctx.summary:
        with self._web_lock: self._last_web_summary = ctx.summary
        self._last_web_route = ctx.route
    if not result.get("summary") and ctx.summary: result["summary"]=ctx.summary
    return result
finally:
    self._web_mode = prev_mode; self._web_spoken = prev_spoken
    with self._web_lock: self._web_active -= 1
    self._clear_ctx()
```

**_handle_conversation:** uses snapshot, guards history mutation, mirrors to legacy global:
```python
_ctx_for_llm = self._cur_ctx()
_conv_prompt = _ctx_for_llm.conv_snapshot if _ctx_for_llm else self.conversation.as_prompt()
reply = chat_local(text, _conv_prompt, ...)
trimmed = trim_spoken(reply, 300)
if ctx2 := self._cur_ctx(): ctx2.summary=trimmed; ctx2.route="local"; ctx2.done.set()
with _conv_lock: self.conversation.add("user", text); self.conversation.add("assistant", reply)
with self._web_lock: self._last_web_summary = trimmed; self._last_web_route = "local"
self.feed_push({"kind":"summary","text":text[:300],"summary":trimmed,"route":"local","detail":"conversation"})
```
Same pattern for fallback (`trimmed2` → `ctx3`) and `_dispatch_local_script` (`ctx.summary/route/done`). `_chat_deepseek_stream/_chat_perplexity/_chat_ollama` helpers use:
```python
_c = self._cur_ctx()
ctx = _c.conv_snapshot if _c and _c.conv_snapshot else self.conversation.as_prompt()
```

**_handle_local:** mirrors instant local skills to ctx so `/api/result` returns summary:
```python
c_local = self._cur_ctx()
if c_local is not None: c_local.summary = msg[:300]; c_local.route="local"; c_local.done.set()
# then feed_push + speak(msg)
# in _process_web_utterance_inner local branch, also mirror ctx→global under lock
```

**_web_wait_for_summary:** polls both ctx and legacy global (waiter thread has no ctx):
```python
ctx = self._cur_ctx()
while time.time()<deadline and not self._stop.is_set():
    if ctx and ctx.summary: return
    if self._last_web_summary:
        if ctx: ctx.summary=self._last_web_summary; ctx.route=self._last_web_route
        return
    time.sleep(0.2)
```

**Conversation return (the last swap):** `return {"summary": self._last_web_summary...}` swapped under load. Fixed to prefer `ctx.summary`:
```python
_cr = self._cur_ctx()
_cr_summ = _cr.summary if _cr else ""
return {"route": _cr.route or intent.kind, "summary": _cr_summ or self._last_web_summary or "", ...}
```

**webui.py isolation:** `submit_text` and `handle_talk` previously read global `orch._web_spoken`. Now prefer per-request ctx:
```python
_ctx = self.orch._cur_ctx() if hasattr(self.orch,"_cur_ctx") else None
spoken = list(_ctx.spoken) if _ctx and _ctx.spoken else (getattr(self.orch,"_web_spoken",None) or [])
# and summary = (_ctx.summary if _ctx else "") or result.get("summary") or spoken_text ...
```

**Removed:** the 800ms `while self._web_active: sleep` block in `_process_utterance` (mic path) — isolation removes the race.

## Verification
- Single: `curl -X POST /api/type {"text":"How are you?"}` → `I'm functioning well` (EN, `route=local`); `what time is it` → `Today is Sunday, August 23. 00:47 EEST.` (local instant); `system stats` → `CPU …` (local).
- Concurrent `POST /api/type` EN+RO: before snapshot `Hello EN` leaked `Sunt bine` (50% swap, 4 rounds). After full isolation (conversation return + webui spoken + ctx mirrors): **4 rounds ×2 = 8/8 PASS, 0 swaps** (each ~1.1–1.4s, Ollama queue serialized but no cross-talk).
- `GET /api/activity` and `/api/result` now agree (both from ctx); activity shows `You said:` (blue) + `Bot:` (purple) with transcript preserved.
- `GET /api/status` returns `busy`/`web_active` so dashboard can gray out when mic has a task.
- Second burst before restart at `00:35:49` still leaked; after `conv_snapshot` + conversation return fix it stays clean. Final restart `00:47` verified 4×2 no-swap.

## Pitfalls
- Do NOT re-serialize the entire web request on a single lock — a 60s `local-script` (Perplexity) would block gaming `How are you?` for a minute. Isolate per-request state; only final summary handoff needs the lock.
- Never read `conversation.as_prompt()` live from concurrent workers — snapshot at arrival under `_conv_lock`.
- `speak()` in web mode must append to `ctx.spoken` (shared list), not a global; and `webui.py` must read from `ctx.spoken` first.
- Conversation return dict must prefer `ctx.summary` over global `_last_web_summary` — the global is still written for the background waiter but races under load.
- `inline-background.mjs` must stay external (`/static/Logo/...`) by default — re-inlining 40MB base64 regresses `index.html` 290KB→40MB. Use `INLINE_GIF=1` only for Vercel `buildCommand`.

See also: `right-ctrl-dashboard-sync.md`, `conversation-language-lock.md`, `dashboard-stability-aug23.md`, `routing-template-hygiene-aug23.md` (template shadowing, `what-day`→`time_date`, summarizer drift).
