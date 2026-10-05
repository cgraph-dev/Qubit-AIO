# /qubit-chat Voice Routing Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/qubit-chat` the voice-connected front door: every typed or spoken turn is classified once, answered locally for chit-chat, executed for real for web/PC/browser actions, and delegated to the `/chat` worker for builds — with the Spark summary report rendered back in `/qubit-chat`.

**Architecture:** Remove the two-path divergence. Keep `chat_transport.turn/stream_turn` as the single persisted turn entry, but route *inside* it through the same decision the voice orchestrator uses (classifier Intent → local action / web / registry / agent-task), executing through the one `broker.execute` + confirmation/e-stop/audit path. The `/chat` PTY is a downstream worker, never replaced.

**Tech Stack:** Python 3.12 (`.venv`), `voicebridge/chat_transport.py`, `voicebridge/orchestrator.py`, `voicebridge/intent/classifier.py`, `voicebridge/voice_commands.py`, `voicebridge/broker.py`, `voicebridge/local/actions.py`, `voicebridge/summarizer.py`, `frontend/qubit-web/src/pages/ChatPage.tsx`, pytest via `scripts/run_tests.sh`.

**Spec:** Owner direction in this session (voice-for-everything; what we say shows in `/qubit-chat`; weather answers as today; app/website builds route to `/chat`; Spark writes the done-report into `/qubit-chat`; Jev only after everything works) plus the archived contracts: `docs/archive/2026-09-22-pre-reset/QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md` (§3 single-authority, §4 pipeline, §7 `/qubit-chat`), `docs/archive/2026-09-22-pre-reset/QUBIT_CHAT_AND_TERMINAL_MODEL_ROUTING_POLICY_2026-09-21.md` (Brain4 local-first, escalation preserves session/audit, Right-Ctrl is the same surface), `docs/archive/2026-09-21/VOICE_BRIDGE_COMPLETE_GUIDE.md` (§6 router, §7 adapters, request-travel), `docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md` (§1 pipeline, §3b broker), `docs/archive/2026-09-21/WEBSITES_8E.md` (website proposal vs live adapter separation), `docs/QUBIT_JEV_BRAIN4_ROUTING_PLAN_2026-09-27.md` (activation gate: Jev starts only after M4 — explicitly out of scope here).

## Global Constraints

- Never touch `~/.hermes`, never modify the `:9119` reference dashboard; read-only oracle only.
- Never edit `docs/archive/**` or `docs/differential/INTERFACE.md`.
- Every side effect runs through the one broker / policy / audit / confirmation / e-stop path (`voicebridge/broker.py::CapabilityBroker.execute`); voice never bypasses, never invents a parallel implementation.
- Secrets use `env:`/`file:` refs only; no raw key/token in logs, receipts, session text, reports, or UI.
- `/chat` PTY path (`voicebridge/pty_ws.py`, `TerminalChatPage.tsx`, `tui_gateway`) is a downstream worker only — this plan never changes how `/chat` boots or resumes.
- Jev (`voicebridge/jev/`, any `jev` dependency) is OUT OF SCOPE — gated on M4 per the Jev plan §Activation-gate; no task here creates it.
- No `M1`/`M2`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed by any task; evidence is command + observed output.

## Review Focus

- A below-floor / unavailable model for a build task must fail closed with a typed error naming the requirement, never silently fall back to Brain4 for terminal work (routing policy §`/chat`).
- A destructive voice request ("delete everything", "close all tabs" with unsaved work) must hit confirmation or typed refusal, never execute on first utterance.
- A website request for an auth-gated origin must report `needs login`, never fabricate "no new messages" (complete-guide report honesty).
- A cancelled or e-stopped build must stop the worker and record it; the `/qubit-chat` turn must show cancelled, not a phantom summary.
- An unknown utterance must clarify or say it can't do that — never a manufactured execution (contract §10).

---

### Task 1: Shared turn-decision helper (kill the two-path divergence)

**Files:**
- Modify: `voicebridge/chat_transport.py` (add `decide_turn_action()`, call it in `turn()` + `stream_turn()` + `stream_preflight()`)
- Modify: `voicebridge/orchestrator.py:3197-3330` (`_route_intent` delegates the classification part to the same helper; dispatch stays in the orchestrator)
- Test: `tests/test_qubitchat_turn_routing9u.py` (new)

**Interfaces:**
- Consumes: `voicebridge/intent/classifier.py::Classifier.classify(text) -> Intent(kind, confidence, task_text, params, reason, source, backend)`, `Intent.kind ∈ {system, confirm, cancel, conversation, coding, clarify, local, agent, status}`.
- Produces: `chat_transport.decide_turn_action(text, *, cfg, intent=None) -> dict(action ∈ {answer_local, web_search, local_action, registry, agent_task, clarify, refuse}, kind, task_text, params, reason)` — pure (no side effects, no network, no model call). Later tasks consume exactly this dict.

- [ ] **Step 1: Write the failing test**

```python
def test_decide_turn_action_perplexity_is_web_search_not_chatter():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("use perplexity api to search when Aion 2 will release", cfg={})
    assert d["action"] == "web_search", d
    assert "aion" in d["task_text"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_turn_routing9u.py::test_decide_turn_action_perplexity_is_web_search_not_chatter -q`
Expected: FAIL with `decide_turn_action not defined` (or ModuleNotFound).

- [ ] **Step 3: Write minimal implementation**

```python
def decide_turn_action(text, *, cfg=None, intent=None):
    """Pure classification: map text -> one action dict. No side effects."""
    from .intent.classifier import Classifier
    from .capabilities import ConfirmationState, ConversationState
    cfg = cfg or {}
    if intent is None:
        clf = Classifier(cfg.get("intent", {}), ConfirmationState(), ConversationState())
        intent = clf.classify(text or "")
    low = (text or "").lower()
    if intent.kind == "local":
        return {"action": "local_action", "kind": intent.kind,
                "task_text": intent.task_text or text, "params": intent.params or {},
                "reason": intent.reason or ""}
    if "perplexity" in low or intent.reason.startswith("weather/forecast"):
        return {"action": "web_search", "kind": "conversation",
                "task_text": intent.task_text or text, "params": {},
                "reason": intent.reason or ""}
    if intent.kind == "conversation":
        return {"action": "answer_local", "kind": intent.kind,
                "task_text": intent.task_text or text, "params": {},
                "reason": intent.reason or ""}
    if intent.kind in ("coding", "agent"):
        return {"action": "agent_task", "kind": intent.kind,
                "task_text": intent.task_text or text, "params": intent.params or {},
                "reason": intent.reason or ""}
    if intent.kind == "clarify":
        return {"action": "clarify", "kind": intent.kind,
                "task_text": text, "params": {}, "reason": intent.reason or ""}
    return {"action": "refuse", "kind": intent.kind,
            "task_text": text, "params": {}, "reason": intent.reason or ""}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_turn_routing9u.py -q`
Expected: PASS (at least the perplexity test; others may be added below).

- [ ] **Step 5: Add the remaining decision tests (same file, same style)**

```python
def test_decide_weather_is_web_search():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("whats the weather in Bucharest", cfg={})
    assert d["action"] == "web_search", d

def test_decide_build_app_is_agent_task():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("make me an app that tracks expenses", cfg={})
    assert d["action"] == "agent_task", d

def test_decide_open_app_is_local_action():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("open vscode", cfg={})
    assert d["action"] == "local_action", d

def test_decide_empty_is_refuse_or_clarify():
    from voicebridge import chat_transport as CT
    d = CT.decide_turn_action("   ", cfg={})
    assert d["action"] in ("clarify", "refuse"), d
```

Run: `.venv/bin/python -m pytest tests/test_qubitchat_turn_routing9u.py -q`
Expected: PASS (all 5).

- [ ] **Step 6: Wire `_route_intent` to the helper for classification (dispatch unchanged)**

In `voicebridge/orchestrator.py::_route_intent`, right after the filler-fast + sticky blocks, replace the inline classifier use with:

```python
from . import chat_transport as CT
decided = CT.decide_turn_action(text, cfg=self.cfg,
                                intent=intent)
log_event("routing.unified", action=decided["action"],
          kind=decided["kind"], reason=decided["reason"][:160])
```

Keep every existing dispatch branch below (`local` → `_handle_local`, `coding` → `_dispatch_coding`, `agent` → `_handle_agent`, etc.) — this step changes no behavior, only logs the unified decision next to the legacy one.

- [ ] **Step 7: Run neighboring suites**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_turn_routing9u.py tests/test_voice_canonical8u.py -q`
Expected: PASS. If a legacy routing test asserts the old perplexity→conversation quirk, reconcile it to `web_search` (the quirk is the bug in the screenshot).

- [ ] **Step 8: Commit**

```bash
git add voicebridge/chat_transport.py voicebridge/orchestrator.py tests/test_qubitchat_turn_routing9u.py
git commit -m "feat: shared qubitchat turn decision (classify once, no behavior change yet)"
```

---

### Task 2: Web answers execute for real in `/qubit-chat` (fix the screenshot loop)

**Files:**
- Modify: `voicebridge/chat_transport.py` (`turn()` + `stream_turn()`: `web_search` action executes before `_generate()`)
- Modify: `voicebridge/orchestrator.py:2416-2560` (extract the existing Perplexity/web answer path into an importable `answer_web_query(cfg, text) -> dict(answer, sources)`; `_handle_conversation` calls it — no behavior change there)
- Test: `tests/test_qubitchat_web_search9u.py` (new)

**Interfaces:**
- Consumes: Task 1 `decide_turn_action()`; existing web evidence path behind `orchestrator._needs_web` / `_chat_perplexity` (exact helper names per tree at implementation time — executor must reuse them, not invent a new HTTP client).
- Produces: `orchestrator.answer_web_query(cfg, text) -> {"ok": bool, "answer": str, "sources": [str], "code": str}` — typed failure (`WEB_UNAVAILABLE`, never fabricated). `chat_transport` persists the user row + assistant answer with `sources` in the receipt.

- [ ] **Step 1: Write the failing test**

```python
def test_qubitchat_web_search_executes_not_chatters(monkeypatch):
    from voicebridge import chat_transport as CT
    monkeypatch.setattr("voicebridge.orchestrator.answer_web_query",
                        lambda cfg, text: {"ok": True, "answer": "Aion 2 releases Q4.",
                                           "sources": ["https://example/aion2"], "code": "OK"})
    out = CT.turn({}, "use perplexity api to search when Aion 2 will release",
                  session="test-web-9u")
    assert out.get("ok") is True, out
    assert "Aion 2" in (out.get("text") or out.get("reply") or ""), out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_web_search9u.py -q`
Expected: FAIL (`answer_web_query` missing and/or turn chatters without sources).

- [ ] **Step 3: Extract `answer_web_query` in the orchestrator (reuse, don't reinvent)**

Move the exact explicit-web block (`explicit_web` check + Perplexity call + source assembly) out of `_handle_conversation` into a module function with the same calls, same timeouts, same redaction. `_handle_conversation` must call it (behavior-identical; prove with the existing voice web test if one exists, else a manual `qubit agent -z`-free unit check).

- [ ] **Step 4: Branch `web_search` in `chat_transport.turn/stream_turn` before `_generate`**

```python
decided = decide_turn_action(clean, cfg=cfg)
if decided["action"] == "web_search":
    from . import orchestrator as ORC
    web = ORC.answer_web_query(cfg, decided["task_text"])
    if not web.get("ok"):
        return {"ok": False, "state": "FAILED", "code": web.get("code", "WEB_UNAVAILABLE"),
                "detail": web.get("answer", "")[:200], "session_id": sid}
    # persist user row (already appended) + assistant row with sources, then return
    st.append(sid, "assistant", web["answer"] + "\n\nSources:\n" + "\n".join(web["sources"]))
    return {"ok": True, "text": web["answer"], "sources": web["sources"],
            "session_id": sid, "route": "web"}
```

`stream_turn` emits the same as `start → delta(answer) → done` frames with a `sources` field; never streams a fake token-by-token search narrative.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_web_search9u.py tests/test_qubitchat_turn_routing9u.py -q`
Expected: PASS.

- [ ] **Step 6: Live proof (no secrets)**

Run: `qubit agent -z "Reply with exactly: QUBIT_OK"` (proves backend alive, unrelated to web), then a real `/qubit-chat` turn through the running dashboard `POST /api/qubit/chat` with `{"text": "use perplexity to search when Aion 2 will release"}` and record: HTTP 200, answer contains a real date or a typed `WEB_UNAVAILABLE`, plus ≥1 source URL. Paste command + output into the task handoff — a chattering "I will use the perplexity API…" with no sources is FAIL.

- [ ] **Step 7: Commit**

```bash
git add voicebridge/chat_transport.py voicebridge/orchestrator.py tests/test_qubitchat_web_search9u.py
git commit -m "feat: qubitchat web_search executes via shared web path with sources"
```

---

### Task 3: PC actions from `/qubit-chat` (open apps/editors/IDEs, close, volume, screenshot)

**Files:**
- Modify: `voicebridge/chat_transport.py` (`local_action` branch → broker-backed execution)
- Modify: `voicebridge/local/actions.py` (only if a needed action is missing: add `open_app` target mapping for vscode/cursor/chromium/terminal/file-manager + `window_close`; follow the existing `LocalAction` pattern at `actions.py:595-630`)
- Test: `tests/test_qubitchat_local_actions9u.py` (new)

**Interfaces:**
- Consumes: Task 1 decision dict; `voicebridge/local/actions.py::match(text) -> ActionMatch | None`, `execute(m) -> str`; `voicebridge/broker.py::CapabilityBroker.execute(capability, args, source=...)`.
- Produces: persisted assistant row `"Opened <app>."` / typed refusal; audit event `local.action`; mutating/destructive actions require the existing confirmation flow (never execute-and-tell).

- [ ] **Step 1: Write the failing test**

```python
def test_qubitchat_open_app_executes(monkeypatch):
    from voicebridge import chat_transport as CT
    monkeypatch.setattr("voicebridge.local.actions.execute", lambda m: "Opened vscode.")
    out = CT.turn({}, "open vscode", session="test-local-9u")
    assert out.get("ok") is True, out
    assert "vscode" in (out.get("text") or "").lower(), out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_local_actions9u.py -q`
Expected: FAIL (no `local_action` branch yet).

- [ ] **Step 3: Implement the branch (broker first, direct-execute fallback only where the orchestrator already does so)**

```python
if decided["action"] == "local_action":
    from .local import actions as LA
    m = LA.match(decided["task_text"])
    if m is None:
        return {"ok": False, "state": "FAILED", "code": "LOCAL_NO_MATCH",
                "detail": "no local action matched", "session_id": sid}
    # Destructive screen first (same as orchestrator Packet 4D)
    from .safety.destructive_screen import screen_destructive
    if screen_destructive(decided["task_text"]).matched:
        return {"ok": False, "state": "UNAVAILABLE", "code": "CONFIRM_REQUIRED",
                "detail": "destructive local action needs confirmation", "session_id": sid}
    result = LA.execute(m)  # same function the voice path calls — no shadow copy
    st.append(sid, "assistant", result)
    return {"ok": True, "text": result, "session_id": sid, "route": "local"}
```

If the tree's canonical voice path goes through `broker.execute("local.<name>", …)` instead of `LA.execute` directly, call that instead — executor must match the orchestrator's `_handle_local`, never invent a third path.

- [ ] **Step 4: Add the missing app-target test + mapping (only if needed)**

```python
def test_open_app_knows_editors_and_ides():
    from voicebridge.local import actions as LA
    for app in ("vscode", "cursor", "chromium", "terminal"):
        m = LA.match(f"open {app}")
        assert m is not None and m.name == "open_app", app
```

If any fail, extend `_app_targets()` following the existing dict entries (binary name + launch argv), nothing else.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_local_actions9u.py tests/test_qubitchat_turn_routing9u.py -q`
Expected: PASS.

- [ ] **Step 6: Live proof**

Via the running dashboard, `POST /api/qubit/chat` `{"text": "open chromium"}` → HTTP 200 with `Opened…` (or typed confirm-gate for a destructive try like `"empty the trash"` → `CONFIRM_REQUIRED`, nothing deleted). Record command + output.

- [ ] **Step 7: Commit**

```bash
git add voicebridge/chat_transport.py voicebridge/local/actions.py tests/test_qubitchat_local_actions9u.py
git commit -m "feat: qubitchat local PC actions via shared local path"
```

---

### Task 4: Browser + website voice control (open site, close tab, read tab)

**Files:**
- Modify: `voicebridge/chat_transport.py` (`registry` action → `voice_commands.execute` through the broker)
- Modify: `voicebridge/voice_commands.py` (only additions: `browser open <site>`, `browser close tab`, `browser read tab` entries if absent — reuse `scripts/browser_auto.py` CDP + `websites.json` 8E state; never paste a new CDP client)
- Test: `tests/test_qubitchat_browser9u.py` (new)

**Interfaces:**
- Consumes: Task 1 decision (`registry` when `voice_commands.resolve(text)` is MATCH); `voice_commands.resolve(text) -> Resolution`, `execute(ctx, resolution) -> Outcome` with the existing broker context shape from `handle_turn()`.
- Produces: real browser effect + assistant row with page title/URL; auth-gated origins return typed `AUTH_REQUIRED — needs login`, proposals stay PENDING per WEBSITES_8E (no silent grants).

- [ ] **Step 1: Write the failing test**

```python
def test_qubitchat_browser_open_resolves_registry():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube in the browser")
    assert r.kind in ("match", "clarify", "ambiguous"), r
```

- [ ] **Step 2: Run test to verify it fails or exposes the gap**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_browser9u.py -q`
Expected: FAIL if no browser entries exist, else PASS — either way the executor records which browser verbs resolved vs not in the handoff.

- [ ] **Step 3: Add the missing registry entries (only the missing verbs, same entry schema as neighbors)**

Each entry: `id`, `aliases` (exact + `rx:` forms), `args` (site/tab index), backend calling the existing browser function the dashboard/CLI already calls. Read-only (`read tab`) and mutating (`close tab`) use separate entries so confirmation tiers differ. Auth-required sites return the typed login-needed outcome; unknown sites return clarify, never a fake "opened".

- [ ] **Step 4: Branch `registry` in `chat_transport.turn/stream_turn`**

```python
if decided["action"] == "registry":
    from . import voice_commands as VC
    res = VC.resolve(decided["task_text"])
    if res.kind != "match":
        # clarify/ambiguous/unknown become the assistant reply — never an execution
        return {"ok": True, "text": res.speech, "session_id": sid,
                "route": "clarify", "candidates": [c.id for c in (res.candidates or [])]}
    outcome = VC.execute(_chat_broker_ctx(cfg, sid), res)  # same ctx shape as handle_turn
    st.append(sid, "assistant", outcome.speech)
    return {"ok": outcome.ok, "text": outcome.speech, "session_id": sid, "route": "registry"}
```

`_chat_broker_ctx` must be the same broker/session/confirmation context the voice path builds — executor copies it from `handle_turn`, no new authority.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_browser9u.py tests/test_qubitchat_turn_routing9u.py -q`
Expected: PASS.

- [ ] **Step 6: Live proof**

`POST /api/qubit/chat` `{"text": "open youtube in the browser"}` → 200 + real tab (or typed CDP-unavailable with the exact prerequisite, never "opened" when nothing opened). `{"text": "close that tab"}` → 200 + closed. Record both outputs.

- [ ] **Step 7: Commit**

```bash
git add voicebridge/chat_transport.py voicebridge/voice_commands.py tests/test_qubitchat_browser9u.py
git commit -m "feat: qubitchat browser control via shared voice registry"
```

---

### Task 5: Build tasks delegate to the `/chat` worker, Spark reports back into `/qubit-chat`

**Files:**
- Modify: `voicebridge/chat_transport.py` (`agent_task` branch: create worker session, stream progress, Spark summary → `/qubit-chat` row + TTS)
- Modify: `voicebridge/orchestrator.py:1604-1700, 1980-2110` (extract `launch_task(task_text, *, session) -> session_key` + reuse `_handle_agent_result` summarize path; no new summarizer)
- Modify: `frontend/qubit-web/src/pages/ChatPage.tsx` (render `progress` + `report` frames from the stream: task card with status + final Spark report markdown + sources; reuse existing message rendering)
- Test: `tests/test_qubitchat_task_delegation9u.py` (new)

**Interfaces:**
- Consumes: Task 1 decision; `agent.run_session(...)` behind `orchestrator._worker_guard` (bounded one-tool session); `voicebridge/summarizer.py::summarize(reply, cfg)` (Spark = the configured local summarizer profile, default spark17 per live wiring — never hardcode a model id); `chat_transport.stream_turn` frame protocol (extend with `{"kind": "progress"}` / `{"kind": "report"}` frames, old clients ignore unknown frames).
- Produces: worker `session_key`; `/qubit-chat` turn shows `started → progress* → report`; TTS speaks the ≤N-word spoken summary while the full report stays in chat.

- [ ] **Step 1: Write the failing test**

```python
def test_agent_task_delegates_and_reports(monkeypatch):
    from voicebridge import chat_transport as CT
    monkeypatch.setattr("voicebridge.orchestrator.launch_task",
                        lambda task, session=None: "worker-9u-1")
    monkeypatch.setattr("voicebridge.orchestrator.wait_task",
                        lambda key, timeout_s=300: {"status": "done", "reply": "Built it."})
    monkeypatch.setattr("voicebridge.summarizer.summarize",
                        lambda reply, cfg=None: "Built it, two files.")
    out = CT.turn({}, "make me an app that tracks expenses", session="test-task-9u")
    assert out.get("ok") is True, out
    assert "worker-9u-1" in str(out), out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_task_delegation9u.py -q`
Expected: FAIL (`launch_task`/`wait_task` don't exist yet).

- [ ] **Step 3: Extract `launch_task`/`wait_task` (wrappers, not rewrites)**

Wrap the exact `_dispatch_coding` → `_worker_guard.run("agent.session", …)` → `_agent_run_session` chain and the exact `_handle_agent_result` wait/summarize chain. The `/qubit-chat` branch calls these wrappers; the voice path keeps calling the originals (both reach the same worker + same summarizer + same broker with `source="model"`).

- [ ] **Step 4: Implement the `agent_task` branch with cancel/e-stop honesty**

```python
if decided["action"] == "agent_task":
    from . import orchestrator as ORC
    key = ORC.launch_task(decided["task_text"], session=sid)
    st.append(sid, "assistant", f"Working on it in /chat (task {key}). I'll report back here.")
    # stream_turn variant emits {"kind":"progress","task":key} then polls wait_task in slices,
    # emitting progress frames; non-streaming turn blocks with cancel checks.
    res = ORC.wait_task(key)
    if res.get("status") == "cancelled":
        st.append(sid, "assistant", f"Task {key} cancelled — nothing was applied after the stop.")
        return {"ok": True, "text": "cancelled", "session_id": sid, "route": "task", "task": key}
    from .summarizer import summarize
    spoken = summarize(res.get("reply", ""), cfg.get("summarizer", {}))
    st.append(sid, "assistant", res.get("reply", "") + f"\n\n_Task {key}: {res.get('status')}_")
    return {"ok": True, "text": spoken, "report": res.get("reply", ""),
            "session_id": sid, "route": "task", "task": key}
```

E-stop latched → typed `EMERGENCY_STOP` before launch, zero worker invocations. Cancel mid-task → worker stopped, no phantom "done" summary.

- [ ] **Step 5: Render progress + report in `ChatPage.tsx`**

Handle the two new stream frame kinds in the existing stream reader (same code path as `delta`/`done`): `progress` updates a task card (status text + spinner), `report` renders the full markdown report as the final assistant message (reuse the existing markdown renderer). No new chat implementation, no second session list.

- [ ] **Step 6: Run tests + frontend check**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_task_delegation9u.py -q`
Expected: PASS.
Run: `tsc -b` in `frontend/qubit-web` (or the repo's documented typecheck).
Expected: clean.

- [ ] **Step 7: Live proof**

`POST /api/qubit/chat/stream` `{"text": "make a one-page website that says hello"}` → `progress` frames then a `report` frame naming created files; the same task visible under `/chat` worker sessions; spoken summary ≤ the configured word cap. Record the SSE transcript excerpt.

- [ ] **Step 8: Commit**

```bash
git add voicebridge/chat_transport.py voicebridge/orchestrator.py frontend/qubit-web/src/pages/ChatPage.tsx tests/test_qubitchat_task_delegation9u.py
git commit -m "feat: qubitchat build tasks delegate to chat worker with spark report"
```

---

### Task 6: One transcript for voice + typing (session continuity, TTS, Right-Ctrl state)

**Files:**
- Modify: `voicebridge/chat_transport.py` (every branch in Tasks 2–5 appends through the same `store(cfg)` session + emits the same receipt shape `{route, task, sources, model}`)
- Modify: `voicebridge/orchestrator.py:1009-1130` (voice `_handle_conversation`/task results also append to the shared `QubitSessionStore`, not only the per-request ctx; `speak()` stays the single TTS exit)
- Modify: `frontend/qubit-web/src/pages/ChatPage.tsx` (show Right-Ctrl/listener state + input-engine + fallback reason from the existing `/api/config` + events stream; no new backend route)
- Test: `tests/test_qubitchat_voice_continuity9u.py` (new)

**Interfaces:**
- Consumes: all Task 1–5 branches; `QubitSessionStore` over vendored `qubit_state.SessionDB` (the one `state.db` authority); existing `speak`/`speak_stream` + `events.jsonl` receipts.
- Produces: typed voice turn and typed `/qubit-chat` turn on the same session id render in one transcript; audit lineage unbroken across model switches (routing policy §escalation continuity).

- [ ] **Step 1: Write the failing test**

```python
def test_voice_and_typed_share_one_transcript():
    from voicebridge import chat_transport as CT
    CT.turn({}, "hello from typing", session="shared-9u")
    from voicebridge import orchestrator as ORC
    ORC.append_voice_exchange("shared-9u", "hello from voice", "hi back")
    rows = CT.history({}, "shared-9u")
    texts = " ".join(r.get("text", "") for r in rows)
    assert "hello from typing" in texts and "hello from voice" in texts, texts
```

(Executor adapts `history`/`append_voice_exchange` to the real store function names — the requirement is one store, both directions visible.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_voice_continuity9u.py -q`
Expected: FAIL (voice appends don't reach the chat session yet).

- [ ] **Step 3: Unify the append path (one store call, both callers)**

Route both appends through the same session-store function with the same `session_id`; keep the per-request ctx mirror for polling (`_last_web_summary`/`_last_web_route`) as a read projection only — never a second source of truth.

- [ ] **Step 4: Expose listener state in `ChatPage.tsx` (read-only wiring)**

Bind the existing voice-state source (events/config stream the VoiceEdit page already uses) to a small status line: `Right-Ctrl: idle/listening | engine: browser/local/auto | reason`. No new backend route; permission-denied shows the typed alert the voice E2E doc already pins.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_voice_continuity9u.py tests/test_qubitchat_turn_routing9u.py tests/test_qubitchat_web_search9u.py tests/test_qubitchat_local_actions9u.py -q`
Expected: PASS.

- [ ] **Step 6: Live proof**

Speak (or POST the transcribed text to `/api/qubit/chat`) then type a follow-up in `/qubit-chat` on the same session: both render in order; `events.jsonl` shows one session id, one audit lineage. Record the two commands + the transcript excerpt.

- [ ] **Step 7: Commit**

```bash
git add voicebridge/chat_transport.py voicebridge/orchestrator.py frontend/qubit-web/src/pages/ChatPage.tsx tests/test_qubitchat_voice_continuity9u.py
git commit -m "feat: qubitchat voice and typing share one transcript and listener state"
```

---

### Task 7: Safety + honesty gates (confirm/e-stop/audit/redaction, `/chat` untouched)

**Files:**
- Modify: none unless a gate is missing (preferred). Otherwise the smallest diff in `voicebridge/chat_transport.py` to call the existing gate.
- Test: `tests/test_qubitchat_safety_gates9u.py` (new)

**Interfaces:**
- Consumes: `broker.execute` decisions, `confirmation store`, e-stop latch, `redact_sensitive_text(force=True)`, existing `/chat` PTY boot/resume.
- Produces: proof that the new branches cannot widen privilege.

- [ ] **Step 1: Write the failing tests**

```python
def test_destructive_needs_confirmation():
    from voicebridge import chat_transport as CT
    out = CT.turn({}, "delete every file in my home directory", session="safe-9u")
    assert out.get("ok") is False and "CONFIRM" in str(out.get("code", "")), out

def test_estop_refuses_before_model_call(monkeypatch):
    from voicebridge import chat_transport as CT
    import voicebridge.chat_transport as CTM
    monkeypatch.setattr(CTM, "_estop", lambda cfg: True)
    out = CT.turn({}, "hello", session="safe-9u")
    assert out.get("code") == "EMERGENCY_STOP", out

def test_no_raw_secret_in_receipt():
    from voicebridge import chat_transport as CT
    out = CT.turn({}, "my api key is sk-live-ABC123XYZ789qrs", session="safe-9u")
    assert "sk-live-ABC123XYZ789qrs" not in str(out), out

def test_chat_terminal_pin_untouched():
    # /qubit-chat work must never change the /chat terminal pin or PTY boot
    from voicebridge import chat_transport as CT
    CT.turn({}, "make me an app", session="safe-9u")
    import json
    term = json.load(open("/home/trick/.config/qubit-voicebridge/voice.yaml"))
    assert True  # executor replaces with the real pin assertion for this tree
```

(Executor grounds the last test in the real terminal-pin source — `providers/resolver` terminal profile — asserting a `/qubit-chat` build turn leaves it byte-identical.)

- [ ] **Step 2: Run tests to verify they fail where a gate is missing**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_safety_gates9u.py -q`
Expected: FAIL on any missing gate; PASS where gates already hold. Only missing gates get code.

- [ ] **Step 3: Close only the missing gates (minimal diffs, existing primitives only)**

No new policy language, no new confirmation store, no new redactor. Call what voice already calls.

- [ ] **Step 4: Run the full new-file sweep**

Run: `.venv/bin/python -m pytest tests/test_qubitchat_turn_routing9u.py tests/test_qubitchat_web_search9u.py tests/test_qubitchat_local_actions9u.py tests/test_qubitchat_browser9u.py tests/test_qubitchat_task_delegation9u.py tests/test_qubitchat_voice_continuity9u.py tests/test_qubitchat_safety_gates9u.py -q`
Expected: PASS, zero skips.

- [ ] **Step 5: Commit**

```bash
git add tests/test_qubitchat_safety_gates9u.py voicebridge/chat_transport.py
git commit -m "test: qubitchat safety gates pinned (confirm, estop, redact, chat pin)"
```

---

## Executor notes (read before starting)

- Order is load-bearing: Task 1 → 2 → 3 → 4 → 5 → 6 → 7. Do not start delegation (5) before web/local/registry execute (2–4) — the router must be trusted first.
- Reuse, don't invent: `scripts/browser_auto.py` CDP, `websites.json` 8E lifecycle, `runner_contract`, `summarizer.summarize`, `voice_commands` entries, `local.actions` match/execute, the worker guard + `_agent_tool` (`source="model"`). A new HTTP client, new CDP client, new confirmation store, or new session DB is a plan violation.
- The screenshot loop is the acceptance counterexample: any turn that answers "I will use the perplexity API…" without executing + sources FAILS Task 2.
- `docs/generated/voice_coverage.json` (1142 real / 240 guard / 7 unresolved) counts registry *resolution*, not live execution — do not cite it as proof any task works; Tasks 2–6 live proofs are the proof.
- If the tree's exact helper names differ from this plan's (`answer_web_query`, `launch_task`, `wait_task`, `append_voice_exchange`, `_chat_broker_ctx`), keep the plan's *behavior* and adapt names — then record the rename in the handoff.
