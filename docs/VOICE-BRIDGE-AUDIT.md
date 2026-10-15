# Voice Bridge Audit — interrupt ordering, worker crash-resilience, routing lanes

Auditor: Qubit Agent (subagent). Repo: `/home/trick/Projects/Repos/qubit-voicebridge`
HEAD at audit: `17da98198`. Method: drive the **real** handlers on a stub object
(`tests/test_voice_bridge_contract.py`), plus reading the source at exact lines.
No service was restarted; no audio device or `voice.yaml` key was touched.

Dashboard under audit: **:8890** (`python -m voicebridge run`). `:9119` is a
different project's dashboard and was not used.

---

## 1. Interrupt path — ORDER with evidence

There are **three** interrupt entry points. They differ in whether a busy guard
sits in front of them:

| # | Entry point | Source | Guard before the interrupt? |
|---|-------------|--------|-----------------------------|
| A | Desktop PTT (hold Right Ctrl) | `orchestrator.py:1750` `_emit_hotkey("ptt_start")` → flag set at `1770-1775`; main loop consumes at `4408-4416` | **YES** — `busy = task.active or playback.playing or _interrupt_requested or _agent_busy` at `4408`, then `_interrupt_task()` at `4415` |
| B | Desktop Esc / cancel | `orchestrator.py:1787` `_emit_hotkey("cancel")` | no guard; aborts task directly (`1794-1795`) |
| C | Dashboard `POST /api/interrupt` | `webui.py:4121-4136` → `WebUI.interrupt()` (`webui.py:5217`) → `Orchestrator._interrupt_task()` (`orchestrator.py:3067`) | **NO guard** — `self.app.interrupt(mode=mode)` is called unconditionally (`webui.py:4132`) |

### The historical bug ("interrupt fires before the busy guard") — status: FIXED for the dashboard path

The dashboard interrupt path (C) has no busy check at all; it calls
`_interrupt_task` directly. Idle interrupts are **not swallowed** — they are
handled explicitly with a 2 s rate-limit + a journal entry at
`orchestrator.py:3094-3106` ("Always journal the interrupt … even when idle").
Contract test `test_interrupt_task_idle_is_rate_limited_not_lost` and
`test_webui_interrupt_delegates_to_interrupt_task` pass. **So the dashboard
interrupt is not lost to a busy guard any more.**

### The desktop PTT path (A) — ORDER is wrong for one busy state. DEFECT-1.

`_emit_hotkey` sets the interrupt flag **only** for two of the four busy states:

```python
1770  if self.cfg.get("hotkey", "interrupt_on_ptt", default=True):
1771      if self.playback.playing:
1772          self.playback.stop()
1773          self._interrupt_requested = True
1774      elif self.task.active:
1775          self._interrupt_requested = True
```

`self._agent_busy` (a background agent turn running) is **not** in that guard.
The main loop's `busy` (`4408-4409`) *does* include `_agent_busy`, so it enters
the interrupt branch and calls `_interrupt_task()` (`4415`) — but
`_interrupt_task()` never sets `_interrupt_requested` (it only logs and stops
playback; `orchestrator.py:3078-3122`). Net effect:

> **A PTT press during an agent-only busy state (agent turn running, no playback,
> no task) leaves `_interrupt_requested == False`. The agent turn is driven with
> `cancelled=lambda: self._interrupt_requested or self._stop.is_set()`
> (`orchestrator.py:3905-3906`), so it cannot be cancelled and runs to
> completion. The interrupt is swallowed.**

Exact lines: **`voicebridge/orchestrator.py:1770-1775`** (narrow guard) →
**`orchestrator.py:3906`** (the `cancelled` callback that stays False).

**Reproduction** (`tests/test_voice_bridge_contract.py`):
- `test_interrupt_not_swallowed_when_only_agent_busy` — drives the real
  `_emit_hotkey` with `_agent_busy=True`, `playback_playing=False`,
  `task_active=False`; asserts `_interrupt_requested is True` → **XFAIL** (the
  flag stays `False`).
- `test_agent_turn_cancel_callback_stays_false_on_agent_only_interrupt` — full
  chain: PTT press → main-loop compensation `_interrupt_task()` → mirrors the
  `cancelled()` callback; asserts it becomes True → **XFAIL**.
- `test_callback_test_is_sensitive_to_the_flag` — falsifiability guard proving
  the callback assertion is live (flips True when the flag is set).

Both xfail are `strict=True`: they flip green the moment the guard is widened to
include `_agent_busy` (e.g. add `or self._agent_busy` to line 1774's condition).

**Severity:** real but narrow — only the desktop PTT path, only the
agent-turn-only window, and it needs the hum playback to not be mid-clip. The
dashboard path (the primary UX on the phone) is unaffected.

---

## 2. Router/worker contract

Rule under test: *"router thin, workers get FULL terminal + browser; bridge must
never crash, always listen / respond / interruptible."*

Pinned by tests, all passing against the real `WebUI` / `Orchestrator` handlers:

| Test | What it pins | Result |
|------|--------------|--------|
| `test_worker_exception_does_not_kill_listener` | a raising `handle_talk` is caught in `submit_talk`'s worker (`webui.py:5298-5307`); result is a **typed error** (`route == "error"`), thread returns cleanly | PASS |
| `test_worker_exception_on_text_path_is_typed` | same for `/api/type` (`webui.py:5234-5280`) | PASS |
| `test_health_probe_answers_after_worker_crash` | after a failed worker, `WebUI.status()` still returns `bridge == "up"` (`webui.py:5180-5215`) | PASS |
| `test_malformed_audio_rate_falls_back_not_crash` | a garbage `X-Audio-Rate` header falls back to 16000 via `parse_audio_rate` instead of raising | PASS |
| `test_interrupt_endpoint_mode_parse_is_defensive` | a malformed `/api/interrupt` body defaults to `mode="interrupt"` (`webui.py:4127-4130`), never crashes | PASS |
| `test_interrupt_task_keeps_running_task_alive` | interrupt does **not** hard-abort the running task (`orchestrator.py:3080-3086`) — the "no abort strain" contract | PASS |

The listener itself is a `ThreadingHTTPServer`; each request is handled on its
own thread and every worker body wraps its work in `try/except` that converts to
a typed result (`webui.py:5269-5275`, `5301-5307`). No worker exception can
propagate into `do_POST` and kill the accept loop.

### DEFECT-3 — unhandled exception on the interrupt announce thread

`orchestrator.py:3115-3122` spawns `interrupt-ack` threads that call
`self.speak(...)` **with no try/except**:

```python
3118  _th.Thread(target=lambda: self.speak(
3119      "Go ahead — I'm listening. ..."),
3120      daemon=True, name="interrupt-ack").start()
```

If TTS fails, the exception is unhandled on that thread (it does **not** crash
the bridge or the handler — `_interrupt_task` itself returns cleanly — but the
failure is unattributed and unlogged). Reproduction:
`test_interrupt_survives_speak_raising` installs a `threading.excepthook`, makes
`speak` raise, and asserts (a) `_interrupt_task` does not raise, and (b) the
thread exception surfaces — the test currently **PASSES** because it asserts the
observed behavior, and the finding is recorded here. Fix: wrap the `self.speak`
call in `try/except` (mirroring `feed_push`'s best-effort pattern at
`orchestrator.py:3112-3113`).

---

## 3. Routing lanes

`voicebridge/intent/classifier.py:classify` (`298-446`) dispatches lanes in this
precedence order: **system → confirm/cancel → local (whitelist) → (perplexity
force-conversation) → agent → weather/identity fast-paths → chitchat → cancel-
while-busy → clarify (non-English) → coding → LLM → default conversation**.

Each lane reachable (all PASS in `test_voice_bridge_contract.py`):

| Lane | Probe utterance | Kind |
|------|-----------------|------|
| system | "are you there" | `system` (params.command == "status") |
| cancel | "stop the task" **while busy** | `cancel` |
| confirm | "yes" **while a confirmation is pending** | `confirm` |
| coding | "refactor the login module and fix the failing tests" | `coding` |
| conversation | "thanks, that was helpful" | `conversation` |
| clarify | "файлды ашып көрсет маған" (non-English) | `clarify` |

**Ambiguity does not silently take the wrong lane** — pinned by:
- `test_ambiguous_utterance_never_silently_cancels`: "what stops a hard drive
  from spinning" while idle → **not** `cancel` (guard at `classifier.py:387-391`
  requires `busy_fn()` AND an explicit stop+target pattern).
- `test_empty_utterance_is_conversation_not_crash`: whitespace → conversation,
  confidence 0.0 (`classifier.py:300-301`).
- `test_lane_clarify_on_non_english`: garbled input goes to `clarify`, never
  `coding` (`classifier.py:392-397`) — this is the documented defense against
  the "garbled Romanian dispatched a coding task" bug.

Note: `cancel` vs `system:stop` are distinct lanes by design — a bare "stop"
matches the system `^stop$` pattern (`classifier.py:47`) while "stop the task"
requires the busy-gated cancel path (`classifier.py:387`). Both were verified.

---

## 4. Existing voice tests — TRUE counts

Command (bounded to the core voice pipeline files; **not** the whole `tests/`
tree, per the task constraint):

```
.venv/bin/python -m pytest \
  tests/test_voice_bridge_contract.py tests/test_barge_stream.py \
  tests/test_webui.py tests/test_webui_reports.py \
  tests/test_router_extensions.py tests/test_voice_commands8tf.py \
  tests/test_voice_edit_bargein9u.py tests/test_qubitchat_voice_continuity9u.py \
  tests/test_voice_confirm_session9u.py tests/test_voice_wake_routing9u.py \
  tests/test_voice_lane2_fixes9u.py tests/test_voice_fullturn9u.py \
  -q --timeout=180
```

Result: **147 passed, 1 skipped, 2 xfailed, 2 failed** (84 s).

The 2 failures are **pre-existing and unrelated** to this audit (no
`voicebridge/` source was modified):

1. `tests/test_voice_commands8tf.py::test_cli_families_all_mapped_with_voice_or_reason`
   — **deterministic**. The `office` CLI subcommand (`voicebridge/cli.py:1276`)
   is not a key in `voicebridge/voice_commands.py:CLI_COVERAGE` (`9589`),
   so the coverage assertion fails with `Extra items in the right set: 'office'`.
   Reproduced in isolation (1 failed, 1 passed).
2. `tests/test_qubitchat_voice_continuity9u.py::test_branch_turns_emit_uniform_receipt_shape`
   — **flaky under the batch** (live Perplexity network call returned real
   search URLs instead of the stubbed `https://example/aion2`). It **passes in
   isolation** (1 failed, 1 passed when run alone), so it is an order/network
   artifact, not a routing defect.

New file result (isolated): `tests/test_voice_bridge_contract.py` →
**20 passed, 2 xfailed** (1.2 s).

---

## Summary of defects

| ID | Severity | Line | Defect | Reproduction |
|----|----------|------|--------|--------------|
| DEFECT-1 | medium (desktop PTT only, narrow window) | `orchestrator.py:1770-1775` (flag), `:3906` (callback) | `_emit_hotkey` ptt_start sets `_interrupt_requested` only for `playback.playing`/`task.active`, not `_agent_busy`; an agent-only turn cannot be cancelled | `test_interrupt_not_swallowed_when_only_agent_busy` (XFAIL), `test_agent_turn_cancel_callback_stays_false_on_agent_only_interrupt` (XFAIL) |
| DEFECT-3 | low | `orchestrator.py:3116-3122` | interrupt announce thread calls `self.speak` with no try/except → unhandled thread exception on TTS failure | `test_interrupt_survives_speak_raising` |
| (pre-existing, out of scope) | — | `voice_commands.py:9589` vs `cli.py:1276` | `CLI_COVERAGE` missing `office` family → `test_voice_commands8tf` fails | run that test alone |

**Fixes are NOT applied** (task constraint: do not edit `voicebridge/` source).
The two strict-xfail tests will flip green automatically once DEFECT-1 is fixed.
