# Dashboard interrupt-on-tap (barge-in from the browser)

## Goal
Let the user tap the PTT/orb on the Voice Dashboard (port 8890) to interrupt
mid-sentence — the same "right Ctrl = barge-in" behavior the user gets on the
desktop, but from the phone/touch browser.

## Behavior matrix
| State | Single tap | Double tap |
|-------|-----------|-----------|
| Assistant speaking | **Interrupt**: stop browser audio + abort bridge task, say "Paused. Go ahead." | **Stop** (silent): same but no verbal ack, like Esc |
| Assistant idle | Normal: start recording (click mode) / start+hold | — |
| Recording | Normal: stop recording (click mode) / send (hold mode) | — |

Double-tap window: 220ms (easy to execute, well under human error threshold).

### Right-click (desktop mouse) to interrupt
The orb (PTT button area) also responds to **right-click** as an interrupt,
mirroring the desktop Ctrl-key barge-in for mouse users:

```js
ptt.addEventListener('contextmenu', (e) => {
  e.preventDefault();       // suppress native browser context menu
  if (recording) return;     // don't interrupt mid-capture
  interrupt();
});
```

Behavior matrix (expanded):

| State | Single tap | Double tap | Right-click |
|-------|-----------|-----------|-------------|
| Assistant speaking | Interrupt + "Paused" | Stop (silent) | Interrupt + "Paused" |
| Assistant idle | Start recording | — | Interrupt (no-op) |
| Recording | Stop recording | — | No-op (suppressed) |

## Backend changes (`voicebridge/webui.py`, `voicebridge/orchestrator.py`)

### 1. `POST /api/interrupt` endpoint
Added to `WebUIHandler.do_POST` in `webui.py`. Reads a JSON body for `mode`
(`"interrupt"` or `"stop"`), calls `self.app.interrupt(mode=mode)`.

```python
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
        self._json({"error": str(exc)[:200]}, 500)
```

### 2. `WebUI.interrupt()` method
```python
def interrupt(self, mode: str = "interrupt") -> None:
    self.orch._interrupt_task(announce=(mode == "interrupt"))
```

### 3. `_interrupt_task(announce: bool = True)`
Added `announce` parameter to `Orchestrator._interrupt_task`. The existing
Ctrl-key hotkey caller (line ~2041) calls with no args → defaults to
`announce=True` → "Paused. Go ahead." announcement preserved.
When `announce=False` (double-tap / stop), the verbal ack is skipped.

## Frontend changes (`voice-dashboard/frontend/index.html`)

### Globals
```js
let currentAudio = null;   // track the playing Audio object
let tapTimer = null;       // double-tap detection
```

### `playResult()` updates
Store the Audio in `currentAudio` instead of a local `const audio`. After
playback ends or on interrupt, set `currentAudio = null`.

### New functions
```js
function assistantActive() {
  return currentAudio !== null;
}

async function interrupt() {
  if (currentAudio) { try { currentAudio.pause(); currentAudio = null; } catch (_) {} }
  await fetch(BASE + '/api/interrupt', {
    method: 'POST',
    headers: hdrs({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ mode: 'interrupt' }),
  });
  setStatus('interrupted');
  setTimeout(() => setStatus('ready'), 1200);
}

async function stopAssistant() {
  if (currentAudio) { try { currentAudio.pause(); currentAudio = null; } catch (_) {} }
  await fetch(BASE + '/api/interrupt', {
    method: 'POST',
    headers: hdrs({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ mode: 'stop' }),
  });
  setStatus('stopped');
  setTimeout(() => setStatus('ready'), 1200);
}
```

### Pointer handlers
- `pointerdown` on `#ptt`: if `assistantActive()` → call `interrupt()`, return
  (don't start recording).
- `click` on `#ptt` (touch path): if `assistantActive()`:
  - If `tapTimer` is set (second tap within 220ms) → `clearTimeout`, call
    `stopAssistant()` (double-tap = stop).
  - Else set `tapTimer` for 220ms → if no second tap, fire `interrupt()`
    (single-tap = interrupt).
  - If not speaking → normal toggle start/stop.

## Pitfalls

- **Context menu steals right-click**: `addEventListener('contextmenu', ...)` MUST call
  `e.preventDefault()` or the browser's native context menu appears and the interrupt
  fires but the menu overlay blocks the orb visually.
- **Don't interrupt mid-recording**: guard the contextmenu handler with
  `if (recording) return` — otherwise a right-click during a deliberate capture
  session kills the recording instead of giving input to the assistant.
- **Mobile has no right-click**: the double-tap (220ms window) is the mobile
  equivalent of right-click-interrupt. There is no `contextmenu` event on touch.
- **`currentAudio.pause()` races with `onended`**: always set `currentAudio = null`
  after pausing so `assistantActive()` returns false immediately and the pointer
  handlers resume normal toggle behavior.
```
systemctl --user restart openclaw-voice-bridge.service
```
Frontend changes to `voice-dashboard/frontend/index.html` apply on refresh
(no bridge restart needed, but a hard-refresh Ctrl+Shift+R is recommended
to bust the browser's old JS cache).
